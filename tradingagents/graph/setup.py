# TradingAgents/graph/setup.py

from typing import Dict, Any
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph, START
from langgraph.prebuilt import ToolNode

from tradingagents.agents import *
from tradingagents.agents.utils.agent_states import AgentState

from .conditional_logic import ConditionalLogic


class PerAnalystToolNode:
    """
    Wraps a LangGraph ToolNode so it reads tool calls from — and writes
    tool results back to — a per-analyst message channel instead of the
    shared 'messages' field.  This lets analysts run in parallel without
    clobbering each other's in-flight tool messages.
    """

    def __init__(self, tool_node: ToolNode, field: str):
        self.tool_node = tool_node
        self.field = field

    def __call__(self, state: Dict[str, Any], config=None):
        # Expose only this analyst's messages as "messages" so ToolNode
        # can find the most-recent tool_call correctly.
        patched = {**state, "messages": list(state.get(self.field, []))}
        result = self.tool_node.invoke(patched, config)
        # Re-map the result back to the per-analyst channel.
        return {self.field: result.get("messages", [])}


class GraphSetup:
    """Handles the setup and configuration of the agent graph."""

    def __init__(
        self,
        quick_thinking_llm: ChatOpenAI,
        deep_thinking_llm: ChatOpenAI,
        tool_nodes: Dict[str, ToolNode],
        bull_memory,
        bear_memory,
        trader_memory,
        invest_judge_memory,
        risk_manager_memory,
        conditional_logic: ConditionalLogic,
    ):
        """Initialize with required components."""
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.tool_nodes = tool_nodes
        self.bull_memory = bull_memory
        self.bear_memory = bear_memory
        self.trader_memory = trader_memory
        self.invest_judge_memory = invest_judge_memory
        self.risk_manager_memory = risk_manager_memory
        self.conditional_logic = conditional_logic

    def setup_graph(
        self, selected_analysts=["market", "social", "news", "fundamentals"]
    ):
        """Set up and compile the agent workflow graph.

        Args:
            selected_analysts (list): List of analyst types to include. Options are:
                - "market": Market analyst
                - "social": Social media analyst
                - "news": News analyst
                - "fundamentals": Fundamentals analyst
        """
        if len(selected_analysts) == 0:
            raise ValueError("Trading Agents Graph Setup Error: no analysts selected!")

        # Create analyst nodes
        analyst_nodes = {}
        delete_nodes = {}
        tool_nodes = {}

        # Map analyst type → its dedicated per-analyst message field
        _msg_field = {
            "market":       "market_messages",
            "social":       "social_messages",
            "news":         "news_messages",
            "fundamentals": "fundamentals_messages",
        }

        if "market" in selected_analysts:
            from tradingagents.dataflows.config import get_config as _get_cfg
            _cfg = _get_cfg()
            if _cfg.get("target_market") == "EGX":
                # Deterministic path: skip LLM, call data tools directly.
                # The tool node is added as a no-op because should_continue_market
                # will never route there (deterministic node returns empty market_messages).
                analyst_nodes["market"] = create_deterministic_market_analyst()
                delete_nodes["market"] = create_msg_delete(_msg_field["market"])
                tool_nodes["market"] = lambda state: {}  # No-op, never reached
            else:
                analyst_nodes["market"] = create_market_analyst(self.quick_thinking_llm)
                delete_nodes["market"] = create_msg_delete(_msg_field["market"])
                tool_nodes["market"] = PerAnalystToolNode(
                    self.tool_nodes["market"], _msg_field["market"]
                )

        if "social" in selected_analysts:
            analyst_nodes["social"] = create_social_media_analyst(self.quick_thinking_llm)
            delete_nodes["social"] = create_msg_delete(_msg_field["social"])
            tool_nodes["social"] = PerAnalystToolNode(
                self.tool_nodes["social"], _msg_field["social"]
            )

        if "news" in selected_analysts:
            analyst_nodes["news"] = create_news_analyst(self.quick_thinking_llm)
            delete_nodes["news"] = create_msg_delete(_msg_field["news"])
            tool_nodes["news"] = PerAnalystToolNode(
                self.tool_nodes["news"], _msg_field["news"]
            )

        if "fundamentals" in selected_analysts:
            from tradingagents.dataflows.config import get_config as _get_cfg2
            _cfg2 = _get_cfg2()
            if _cfg2.get("target_market") == "EGX":
                if _cfg2.get("use_hybrid_fundamental_analyst", False):
                    # Phase 2A: deterministic foundation + three-stage CoT pipeline
                    analyst_nodes["fundamentals"] = create_hybrid_fundamentals_analyst(
                        self.quick_thinking_llm, self.deep_thinking_llm
                    )
                else:
                    # Default: deterministic-only path (no LLM calls)
                    analyst_nodes["fundamentals"] = create_deterministic_fundamentals_analyst()
                delete_nodes["fundamentals"] = create_msg_delete(_msg_field["fundamentals"])
                tool_nodes["fundamentals"] = lambda state: {}  # No-op, never reached
            else:
                analyst_nodes["fundamentals"] = create_fundamentals_analyst(self.quick_thinking_llm)
                delete_nodes["fundamentals"] = create_msg_delete(_msg_field["fundamentals"])
                tool_nodes["fundamentals"] = PerAnalystToolNode(
                    self.tool_nodes["fundamentals"], _msg_field["fundamentals"]
                )

        # Create researcher and manager nodes
        bull_researcher_node = create_bull_researcher(
            self.quick_thinking_llm, self.bull_memory
        )
        bear_researcher_node = create_bear_researcher(
            self.quick_thinking_llm, self.bear_memory
        )
        research_manager_node = create_research_manager(
            self.deep_thinking_llm, self.invest_judge_memory
        )
        # Trader uses deep_thinking_llm because it generates the most complex
        # structured output (JSON execution plans) that must pass risk checks.
        trader_node = create_trader(self.deep_thinking_llm, self.trader_memory)

        # Phase 3a: Single merged risk debate replaces 3 sequential debators.
        # All 3 perspectives are argued in one LLM call without re-passing
        # the full analyst reports (eliminates ~85-90% token redundancy).
        from tradingagents.agents.risk_mgmt.merged_debator import create_merged_risk_debator
        from tradingagents.agents.risk_mgmt.risk_scorer import (
            create_risk_scorer_node,
            risk_veto_node,
        )
        merged_risk_node = create_merged_risk_debator(self.quick_thinking_llm)
        risk_scorer_node = create_risk_scorer_node()
        risk_manager_node = create_risk_manager(
            self.deep_thinking_llm, self.risk_manager_memory
        )

        # Create workflow
        workflow = StateGraph(AgentState)

        # Add analyst nodes to the graph
        for analyst_type, node in analyst_nodes.items():
            workflow.add_node(f"{analyst_type.capitalize()} Analyst", node)
            workflow.add_node(
                f"Msg Clear {analyst_type.capitalize()}", delete_nodes[analyst_type]
            )
            workflow.add_node(f"tools_{analyst_type}", tool_nodes[analyst_type])

        # Add other nodes
        workflow.add_node("Bull Researcher", bull_researcher_node)
        workflow.add_node("Bear Researcher", bear_researcher_node)
        workflow.add_node("Research Manager", research_manager_node)
        workflow.add_node("Trader", trader_node)
        # Phase 3: Three-layer risk pipeline
        #   Risk Scorer (deterministic, pre-LLM)
        #   -> Risk Veto (hard reject, no LLM) | Merged Risk Debate (LLM debate)
        #   -> Risk Judge (Constitutional LLM manager + final gate)
        workflow.add_node("Risk Scorer", risk_scorer_node)
        workflow.add_node("Risk Veto", risk_veto_node)
        workflow.add_node("Merged Risk Debate", merged_risk_node)
        workflow.add_node("Risk Judge", risk_manager_node)

        # ── Parallel analyst fan-out ──────────────────────────────────────────
        # All analysts start simultaneously from START and run in the same
        # LangGraph super-step.  Each analyst has its own isolated message
        # channel so tool calls don't interfere.  All clear nodes converge
        # at "Analysts Sync" before the debate phase.
        #
        # defer=True makes "Analysts Sync" a TRUE barrier: it executes once,
        # only after every analyst branch has fully settled. Without it the
        # node fires once per super-step in which any analyst's "Msg Clear"
        # edge delivers a trigger — and analysts finish in different
        # super-steps (each runs a variable number of tool-call rounds).
        # Multiple firings re-trigger "Bull Researcher", so a second Bull
        # task runs concurrently with the Bear task; both write
        # investment_debate_state and the _keep_last reducer drops one —
        # which is why bear_history came back empty on every run (MEMORY §AA).
        workflow.add_node("Analysts Sync", lambda state: {}, defer=True)

        for analyst_type in selected_analysts:
            current_analyst = f"{analyst_type.capitalize()} Analyst"
            current_tools   = f"tools_{analyst_type}"
            current_clear   = f"Msg Clear {analyst_type.capitalize()}"

            # Fan-out: START → every analyst in parallel
            workflow.add_edge(START, current_analyst)

            # Tool-call loop for this analyst
            workflow.add_conditional_edges(
                current_analyst,
                getattr(self.conditional_logic, f"should_continue_{analyst_type}"),
                [current_tools, current_clear],
            )
            workflow.add_edge(current_tools, current_analyst)

            # Fan-in: each clear node → shared sync barrier
            workflow.add_edge(current_clear, "Analysts Sync")

        # After all analysts are done, start the debate phase.
        #
        # The debate is a STRICT LINEAR CHAIN: Bull → Bear → Research Manager.
        # It used to be a Bull↔Bear cycle driven by `should_continue_debate`
        # conditional edges, but that cycle re-triggered a second concurrent
        # Bull task. Both Bull and Bear write `investment_debate_state`; the
        # `_keep_last` reducer then dropped one write — and Bear's structured
        # thesis was the one lost, leaving `bear_history` empty on every run
        # (MEMORY §AA — one-sided debates).
        #
        # A linear chain is also functionally equivalent to the prior intent:
        # `ConditionalLogic()` is always constructed with no arguments, so
        # `max_debate_rounds` is permanently 1 regardless of config — the
        # debate was already a single Bull/Bear round in practice. Multi-round
        # debate would need a real round-counter node, not a self-cycle.
        workflow.add_edge("Analysts Sync", "Bull Researcher")
        workflow.add_edge("Bull Researcher", "Bear Researcher")
        workflow.add_edge("Bear Researcher", "Research Manager")
        workflow.add_edge("Research Manager", "Trader")

        # Phase 3: Risk pipeline routing
        # Step 1: Trader -> Risk Scorer (always)
        workflow.add_edge("Trader", "Risk Scorer")

        # Step 2: Risk Scorer -> conditional routing
        #   VETO  -> Risk Veto -> END  (LLM debate skipped entirely)
        #   other -> Merged Risk Debate -> Risk Judge -> END
        workflow.add_conditional_edges(
            "Risk Scorer",
            lambda state: "VETO" if state.get("risk_action") == "VETO" else "CONTINUE",
            {
                "VETO": "Risk Veto",
                "CONTINUE": "Merged Risk Debate",
            },
        )
        workflow.add_edge("Risk Veto", END)
        workflow.add_edge("Merged Risk Debate", "Risk Judge")
        workflow.add_edge("Risk Judge", END)

        # Compile and return
        return workflow.compile()
