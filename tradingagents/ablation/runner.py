"""
Ablation experiment runner.

Usage:
    python -m tradingagents.ablation.runner \
        --experiment BASELINE \
        --ticker COMI.CA \
        --dates 2024-01-15,2024-01-16,2024-01-17 \
        --output results/ablation.jsonl

Each experiment ID maps to a configuration dict that specifies which agents
use LLM vs deterministic variants.  The runner patches the graph accordingly,
runs the pipeline, instruments every LLM call, and writes one AblationRecord
per (ticker, date) to a JSONL file.
"""

import argparse
import json
import os
import sys
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

# Add project root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.ablation.schemas import (
    AblationRecord, AgentLog, LLMCallLog, InstrumentedLLM,
)
from tradingagents.ablation.deterministic_agents import (
    deterministic_market_analyst,
    deterministic_fundamentals_analyst,
    deterministic_signal_aggregation,
    deterministic_trader,
    deterministic_risk_manager,
    regex_signal_processor,
)


# =============================================================================
# Experiment Configurations
# =============================================================================
# Each key maps to a dict of agent_name -> variant.
# "llm" = original LLM agent
# "det" = deterministic replacement
# "skip" = remove from graph entirely
# "agg" = deterministic signal aggregation (replaces debate)
#
# Agents not listed default to "llm".
# =============================================================================

EXPERIMENT_CONFIGS: Dict[str, Dict[str, str]] = {
    "CURRENT": {
        # Empty dict = use the graph exactly as currently defined in setup.py
        # (This already has merged debators and deterministic logic based on setup logic)
    },
    "ABLATION_NO_PREFETCH": {
        "prefetch": "skip",  # Handled in runner graph building
    },
    "ABLATION_NO_MERGE": {
        "risk_debators": "legacy",  # To test the old 3-debator setup if needed
    }
}


# =============================================================================
# Patched Graph Builder
# =============================================================================

def build_patched_graph(experiment_id: str, config: dict = None):
    """
    Build a TradingAgentsGraph with agents patched according to the experiment.

    Returns:
        (graph_instance, call_log_list)
        call_log_list is a mutable list that InstrumentedLLM appends to.
    """
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    exp_config = EXPERIMENT_CONFIGS.get(experiment_id, {})
    config = config or deepcopy(DEFAULT_CONFIG)

    # Apply experiment-level config patches BEFORE graph construction so they
    # take effect inside __init__ (e.g. prefetch_data controls DataPrefetcher).
    if exp_config.get("prefetch") == "skip":
        config["prefetch_data"] = False

    # Build graph to get all plumbing (memories, tool_nodes, LLM instances).
    tag = TradingAgentsGraph(
        selected_analysts=["market", "social", "news", "fundamentals"],
        config=config,
    )

    call_log: List[LLMCallLog] = []

    # Always rebuild the graph with InstrumentedLLM wrappers baked into each
    # agent node's closure.  Simply replacing tag.quick_thinking_llm after
    # __init__ has no effect — nodes already captured the original LLM
    # reference when setup_graph() compiled the graph.
    tag.graph = _rebuild_graph(tag, exp_config, call_log)

    # Override signal processor if requested
    if exp_config.get("signal_processor") == "det":
        tag.signal_processor.process_signal = regex_signal_processor

    # Override reflect_and_remember if requested
    if exp_config.get("reflector") == "skip":
        tag.reflect_and_remember = lambda *a, **k: None

    return tag, call_log


def _rebuild_graph(tag, exp_config: dict, call_log: List[LLMCallLog]):
    """Rebuild the LangGraph workflow with deterministic nodes swapped in."""
    from tradingagents.graph.conditional_logic import ConditionalLogic
    from tradingagents.agents.utils.agent_states import AgentState
    from tradingagents.agents import (
        create_market_analyst, create_fundamentals_analyst,
        create_news_analyst, create_social_media_analyst,
        create_bull_researcher, create_bear_researcher,
        create_research_manager, create_trader,
        create_merged_risk_debator,
        create_risk_manager, create_msg_delete,
    )
    from tradingagents.graph.setup import PerAnalystToolNode
    from langgraph.graph import END, StateGraph, START
    from langgraph.prebuilt import ToolNode

    cond = ConditionalLogic()
    workflow = StateGraph(AgentState)

    # --- Analysts ---
    _msg_field = {
        "market": "market_messages",
        "social": "social_messages",
        "news": "news_messages",
        "fundamentals": "fundamentals_messages",
    }

    selected = ["market", "social", "news", "fundamentals"]
    analysts_needing_sync = []

    from tradingagents.dataflows.config import get_config as _get_cfg
    _cfg = _get_cfg()
    is_egx = _cfg.get("target_market") == "EGX"

    for atype in selected:
        field = _msg_field[atype]
        explicit = exp_config.get(f"{atype}_analyst")  # "det", "llm", or None
        if explicit == "det":
            is_det = True
        elif explicit == "llm":
            is_det = False
        elif is_egx and atype in ("market", "fundamentals"):
            # Mirror setup.py: EGX market uses deterministic analysts by default
            is_det = True
        else:
            is_det = False

        if is_det:
            # Deterministic analyst: single node, no tool loop, no msg-clear
            if atype == "market":
                workflow.add_node("Market Analyst", deterministic_market_analyst)
            elif atype == "fundamentals":
                workflow.add_node("Fundamentals Analyst", deterministic_fundamentals_analyst)
            workflow.add_edge(START, f"{atype.capitalize()} Analyst")
            analysts_needing_sync.append(f"{atype.capitalize()} Analyst")
        else:
            # LLM analyst: original tool-calling loop
            quick_llm = InstrumentedLLM(tag.quick_thinking_llm, f"{atype}_analyst", call_log)
            if atype == "market":
                node = create_market_analyst(quick_llm)
            elif atype == "social":
                node = create_social_media_analyst(quick_llm)
            elif atype == "news":
                node = create_news_analyst(quick_llm)
            elif atype == "fundamentals":
                node = create_fundamentals_analyst(quick_llm)

            delete_node = create_msg_delete(field)
            tool_node = PerAnalystToolNode(tag.tool_nodes[atype], field)

            workflow.add_node(f"{atype.capitalize()} Analyst", node)
            workflow.add_node(f"Msg Clear {atype.capitalize()}", delete_node)
            workflow.add_node(f"tools_{atype}", tool_node)

            workflow.add_edge(START, f"{atype.capitalize()} Analyst")

            should_continue = getattr(cond, f"should_continue_{atype}")
            workflow.add_conditional_edges(
                f"{atype.capitalize()} Analyst",
                should_continue,
                [f"tools_{atype}", f"Msg Clear {atype.capitalize()}"],
            )
            workflow.add_edge(f"tools_{atype}", f"{atype.capitalize()} Analyst")

            analysts_needing_sync.append(f"Msg Clear {atype.capitalize()}")

    # Sync barrier
    workflow.add_node("Analysts Sync", lambda state: {})
    for node_name in analysts_needing_sync:
        workflow.add_edge(node_name, "Analysts Sync")

    # --- Debate or Aggregation ---
    if exp_config.get("debate") == "agg":
        workflow.add_node("Signal Aggregation", deterministic_signal_aggregation)
        workflow.add_edge("Analysts Sync", "Signal Aggregation")
        post_debate_node = "Signal Aggregation"
    else:
        # LLM debate
        deep_llm = InstrumentedLLM(tag.deep_thinking_llm, "deep_llm", call_log)
        quick_llm = InstrumentedLLM(tag.quick_thinking_llm, "quick_llm", call_log)

        workflow.add_node("Bull Researcher",
                          create_bull_researcher(quick_llm, tag.bull_memory))
        workflow.add_node("Bear Researcher",
                          create_bear_researcher(quick_llm, tag.bear_memory))
        workflow.add_node("Research Manager",
                          create_research_manager(deep_llm, tag.invest_judge_memory))

        workflow.add_edge("Analysts Sync", "Bull Researcher")
        workflow.add_conditional_edges(
            "Bull Researcher", cond.should_continue_debate,
            {"Bear Researcher": "Bear Researcher", "Research Manager": "Research Manager"},
        )
        workflow.add_conditional_edges(
            "Bear Researcher", cond.should_continue_debate,
            {"Bull Researcher": "Bull Researcher", "Research Manager": "Research Manager"},
        )
        post_debate_node = "Research Manager"

    # --- Trader ---
    if exp_config.get("trader") == "det":
        workflow.add_node("Trader", deterministic_trader)
    else:
        deep_llm = InstrumentedLLM(tag.deep_thinking_llm, "trader", call_log)
        workflow.add_node("Trader", create_trader(deep_llm, tag.trader_memory))
    workflow.add_edge(post_debate_node, "Trader")

    # --- Risk Debators ---
    if exp_config.get("risk_debators") == "skip":
        # Skip risk debate, go straight to risk judge
        post_trader_node = "Trader"
    else:
        quick_llm = InstrumentedLLM(tag.quick_thinking_llm, "risk_debator", call_log)
        workflow.add_node("Merged Risk Debate", create_merged_risk_debator(quick_llm))
        workflow.add_edge("Trader", "Merged Risk Debate")
        post_trader_node = "Merged Risk Debate"

    # --- Risk Manager ---
    if exp_config.get("risk_manager") == "det":
        workflow.add_node("Risk Judge", deterministic_risk_manager)
    else:
        deep_llm = InstrumentedLLM(tag.deep_thinking_llm, "risk_manager", call_log)
        workflow.add_node("Risk Judge",
                          create_risk_manager(deep_llm, tag.risk_manager_memory))

    if post_trader_node is not None:
        workflow.add_edge(post_trader_node, "Risk Judge")

    workflow.add_edge("Risk Judge", END)

    return workflow.compile()


# =============================================================================
# Single-run executor
# =============================================================================

def run_single(
    tag,
    call_log: List[LLMCallLog],
    experiment_id: str,
    ticker: str,
    trade_date: str,
) -> AblationRecord:
    """Run one (ticker, date) through the patched graph and return a record."""

    call_log.clear()
    t0 = time.perf_counter()

    final_state, signal = tag.propagate(ticker, trade_date)

    total_ms = (time.perf_counter() - t0) * 1000

    # Use regex processor if we have it, else use whatever signal came back
    decision = signal.strip().upper() if signal else "HOLD"
    for d in ("BUY", "SELL", "HOLD"):
        if d in decision:
            decision = d
            break
    else:
        decision = "HOLD"

    # Build agent-level summaries from call_log
    agent_calls: Dict[str, List[LLMCallLog]] = {}
    for c in call_log:
        agent_calls.setdefault(c.agent_name, []).append(c)

    agent_logs = []
    for agent_name, calls in agent_calls.items():
        agent_logs.append(AgentLog(
            agent_name=agent_name,
            variant="llm",
            wall_time_ms=sum(c.latency_ms for c in calls),
            llm_calls=len(calls),
            total_input_tokens=sum(c.input_tokens for c in calls),
            total_output_tokens=sum(c.output_tokens for c in calls),
            output_decision=None,
            output_confidence=None,
            structured_output_parsed=True,
        ))

    total_input = sum(c.input_tokens for c in call_log)
    total_output = sum(c.output_tokens for c in call_log)

    # DeepSeek pricing (as of 2024): ~$0.14/M input, ~$0.28/M output
    cost = (total_input * 0.14 + total_output * 0.28) / 1_000_000

    overall_conf = (final_state.get("confidence_scores") or {}).get("overall", 0.5)
    risk_vetoed = final_state.get("risk_veto", False)

    return AblationRecord(
        experiment_id=experiment_id,
        ticker=ticker,
        trade_date=trade_date,
        run_timestamp=datetime.utcnow().isoformat(),
        final_decision=decision,
        overall_confidence=overall_conf,
        risk_vetoed=risk_vetoed,
        total_wall_time_ms=total_ms,
        agent_logs=agent_logs,
        total_llm_calls=len(call_log),
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        estimated_api_cost_usd=round(cost, 4),
        llm_call_details=list(call_log),
    )


# =============================================================================
# Main entry point
# =============================================================================

def run_ablation_experiment(
    experiment_id: str,
    ticker: str,
    dates: List[str],
    output_path: str = "results/ablation.jsonl",
    config: dict = None,
):
    """
    Run a full ablation experiment for one (experiment, ticker) across dates.
    Appends results to the output JSONL file.
    """
    print(f"=== Experiment {experiment_id} | {ticker} | {len(dates)} dates ===")

    tag, call_log = build_patched_graph(experiment_id, config)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    for i, date_str in enumerate(dates):
        print(f"  [{i+1}/{len(dates)}] {date_str} ...", end=" ", flush=True)
        try:
            record = run_single(tag, call_log, experiment_id, ticker, date_str)
            print(
                f"{record.final_decision} | "
                f"{record.total_wall_time_ms/1000:.1f}s | "
                f"{record.total_llm_calls} LLM calls | "
                f"${record.estimated_api_cost_usd:.4f}"
            )
        except Exception as e:
            import traceback
            print(f"ERROR: {e}")
            traceback.print_exc()
            record = AblationRecord(
                experiment_id=experiment_id,
                ticker=ticker,
                trade_date=date_str,
                run_timestamp=datetime.utcnow().isoformat(),
                final_decision="ERROR",
                overall_confidence=0.0,
                risk_vetoed=False,
                total_wall_time_ms=0,
            )

        with open(output_path, "a") as f:
            f.write(record.to_jsonl() + "\n")

    print(f"=== Done. Results appended to {output_path} ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run ablation experiments")
    parser.add_argument("--experiment", required=True, choices=list(EXPERIMENT_CONFIGS.keys()))
    parser.add_argument("--ticker", required=True, help="EGX ticker (e.g., COMI.CA)")
    parser.add_argument("--dates", required=True, help="Comma-separated dates (YYYY-MM-DD)")
    parser.add_argument("--output", default="results/ablation.jsonl")
    args = parser.parse_args()

    dates = [d.strip() for d in args.dates.split(",")]
    run_ablation_experiment(args.experiment, args.ticker, dates, args.output)
