# TradingAgents/graph/trading_graph.py

import os
from pathlib import Path
import json
from datetime import date
from typing import Dict, Any, Tuple, List, Optional

from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI

from langgraph.prebuilt import ToolNode

from tradingagents.agents import *
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.agents.utils.memory import FinancialSituationMemory

from tradingagents.agents.utils.agent_states import (
    AgentState,
    InvestDebateState,
    RiskDebateState,
)
from tradingagents.sentiment.surfacing import extract_sentiment_audit_record
from tradingagents.dataflows.config import set_config

# Import the new abstract tool methods from agent_utils
from tradingagents.agents.utils.agent_utils import (
    get_stock_data,
    get_indicators,
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_news,
    get_insider_sentiment,
    get_insider_transactions,
    get_global_news,
)

# Import EGX-specific fundamental data tools
from tradingagents.agents.utils.fundamental_data_tools import (
    get_egx_fundamentals,
    get_egx_income,
    get_egx_balance,
    get_egx_ratios,
)

# Import EGX-specific news data tools
from tradingagents.agents.utils.news_data_tools import (
    get_egx_company_news,
    get_egx_market_news,
)

# Import social media tools for EGX sentiment analysis
from tradingagents.agents.utils.social_media_tools import (
    get_social_sentiment,
    get_social_media_posts,
)

from .conditional_logic import ConditionalLogic
from .setup import GraphSetup
from .propagation import Propagator
from .reflection import Reflector
from .signal_processing import SignalProcessor


def _resolve_memory_class(config: Dict[str, Any]):
    """Return the configured memory backend class."""
    backend = str(config.get("memory_backend", "chroma")).strip().lower()
    if backend in {"postgres", "pgvector", "persistent"}:
        try:
            from persistent_memory import PersistentAgentMemory
            return PersistentAgentMemory
        except ImportError:
            return FinancialSituationMemory
    return FinancialSituationMemory


class TradingAgentsGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts=["market", "social", "news", "fundamentals"],
        debug=False,
        config: Dict[str, Any] = None,
    ):
        """Initialize the trading agents graph and components.

        Args:
            selected_analysts: List of analyst types to include
            debug: Whether to run in debug mode
            config: Configuration dictionary. If None, uses default config
        """
        self.debug = debug
        self.config = config or DEFAULT_CONFIG

        # Update the interface's config
        set_config(self.config)

        # Create necessary directories
        os.makedirs(
            os.path.join(self.config["project_dir"], "dataflows/data_cache"),
            exist_ok=True,
        )

        # Initialize LLMs
        if self.config["llm_provider"].lower() == "openai" or self.config["llm_provider"] == "ollama" or self.config["llm_provider"] == "openrouter":
            self.deep_thinking_llm = ChatOpenAI(model=self.config["deep_think_llm"], base_url=self.config["backend_url"], temperature=0, seed=42)
            self.quick_thinking_llm = ChatOpenAI(model=self.config["quick_think_llm"], base_url=self.config["backend_url"], temperature=0, seed=42)
        elif self.config["llm_provider"].lower() == "anthropic":
            self.deep_thinking_llm = ChatAnthropic(model=self.config["deep_think_llm"], base_url=self.config["backend_url"], temperature=0)
            self.quick_thinking_llm = ChatAnthropic(model=self.config["quick_think_llm"], base_url=self.config["backend_url"], temperature=0)
        elif self.config["llm_provider"].lower() == "google":
            self.deep_thinking_llm = ChatGoogleGenerativeAI(model=self.config["deep_think_llm"], temperature=0)
            self.quick_thinking_llm = ChatGoogleGenerativeAI(model=self.config["quick_think_llm"], temperature=0)
        else:
            raise ValueError(f"Unsupported LLM provider: {self.config['llm_provider']}")
        
        # Initialize memories. ChromaDB is the default; Postgres/pgvector is
        # available only when explicitly selected by config.
        memory_class = _resolve_memory_class(self.config)
        self.bull_memory         = memory_class("bull_memory",         self.config)
        self.bear_memory         = memory_class("bear_memory",         self.config)
        self.trader_memory       = memory_class("trader_memory",       self.config)
        self.invest_judge_memory = memory_class("invest_judge_memory", self.config)
        self.risk_manager_memory = memory_class("risk_manager_memory", self.config)

        # Create tool nodes
        self.tool_nodes = self._create_tool_nodes()

        # Initialize components
        self.conditional_logic = ConditionalLogic()
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.tool_nodes,
            self.bull_memory,
            self.bear_memory,
            self.trader_memory,
            self.invest_judge_memory,
            self.risk_manager_memory,
            self.conditional_logic,
        )

        self.propagator = Propagator()
        self.reflector = Reflector(self.quick_thinking_llm)
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # State tracking
        self.curr_state = None
        self.ticker = None
        self.log_states_dict = {}  # date to full state dict

        # Set up the graph
        self.graph = self.graph_setup.setup_graph(selected_analysts)

    def _create_tool_nodes(self) -> Dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods."""
        return {
            "market": ToolNode(
                [
                    # Core stock data tools
                    get_stock_data,
                    # Technical indicators
                    get_indicators,
                ]
            ),
            "social": ToolNode(
                [
                    # Social media sentiment tools
                    get_social_sentiment,
                    get_social_media_posts,
                ]
            ),
            "news": ToolNode(
                [
                    # EGX-specific news tools (match what news_analyst.py binds)
                    get_egx_company_news,
                    get_egx_market_news,
                ]
                if self.config.get("target_market") == "EGX"
                else [
                    # Generic news and insider information
                    get_news,
                    get_global_news,
                    get_insider_sentiment,
                    get_insider_transactions,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    # EGX-specific fundamental analysis tools (CSV-backed)
                    get_egx_fundamentals,
                    get_egx_income,
                    get_egx_balance,
                    get_egx_ratios,
                ]
            ),
        }

    def propagate(self, company_name, trade_date, *, user_id: Optional[str] = None):
        """Run the trading agents graph for a company on a specific date."""
        import uuid
        session_id = uuid.uuid4().hex
        self.session_id = session_id

        # Pre-flight data freshness check (zero LLM tokens)
        if (self.config.get("target_market") == "EGX"
                and self.config.get("auto_refresh_fundamentals", True)
                and not self.config.get("backtest_mode", False)):
            from tradingagents.dataflows.egx_data_refresh import is_fundamentals_stale, fetch_and_refresh_egx_data
            max_age = self.config.get("fundamentals_max_age_days", 90)
            if is_fundamentals_stale(trade_date, max_age_days=max_age):
                fetch_and_refresh_egx_data(trade_date)

        self.ticker = company_name

        # Initialize state
        init_agent_state = self.propagator.create_initial_state(
            company_name, trade_date
        )
        args = self.propagator.get_graph_args()

        # Redis publisher — real-time event streaming to the WebSocket dashboard.
        # Falls back to a silent no-op when redis package is not installed.
        try:
            from redis_pubsub import AgentEventPublisher
            _publisher = AgentEventPublisher(company_name)
        except ImportError:
            class _publisher:  # type: ignore
                def prefetch_started(self): pass
                def prefetch_finished(self, *a, **k): pass
                def final_decision(self, *a, **k): pass
            _publisher = _publisher()

        # Phase 2a: Pre-fetch news & social data in parallel before graph starts.
        # This eliminates 1 LLM tool-call round-trip for each of the News and
        # Social analysts (~2 LLM calls, ~6,000 tokens, ~30-60s saved).
        if self.config.get("prefetch_data", True):
            try:
                from tradingagents.graph.prefetch import DataPrefetcher
                prefetcher = DataPrefetcher(self.config)
                _publisher.prefetch_started()
                prefetched = prefetcher.fetch_all(company_name, trade_date)
                _publisher.prefetch_finished(sum(1 for v in prefetched.values() if v))
                init_agent_state.update(prefetched)
            except Exception as e:
                import logging
                logging.getLogger("tradingagents").warning(
                    "DataPrefetcher failed, falling back to tool-call mode: %s", e
                )

        if self.debug:
            # Debug mode with tracing
            trace = []
            for chunk in self.graph.stream(init_agent_state, **args):
                if len(chunk["messages"]) == 0:
                    pass
                else:
                    chunk["messages"][-1].pretty_print()
                    trace.append(chunk)

            final_state = trace[-1]
        else:
            # Standard mode without tracing
            final_state = self.graph.invoke(init_agent_state, **args)

        # Compute overall confidence from analyst structured outputs and inject
        # into final_state so the backtester can use it for position sizing.
        # propagate_confidence() reads directly from technical_analysis,
        # fundamental_analysis, sentiment_analysis, and social_sentiment_analysis
        # dicts — no analyst needs to write to confidence_scores explicitly.
        conf_scores = self.propagator.propagate_confidence(final_state)
        if not isinstance(final_state.get("confidence_scores"), dict):
            final_state["confidence_scores"] = {}
        final_state["confidence_scores"].update(conf_scores)

        # Store current state for reflection
        self.curr_state = final_state

        # Log state
        self._log_state(trade_date, final_state)

        # Audit write-through: persist into Postgres if available (degrades silently)
        try:
            from tradingagents.db import audit_writer
            fingerprint = audit_writer.build_model_fingerprint(self.config)
            audit_writer.write_analysis_session(
                session_id=session_id, ticker=company_name, trade_date=trade_date,
                final_state=final_state, model_fingerprint=fingerprint, user_id=user_id,
            )
            audit_writer.write_agent_events(
                session_id=session_id, final_state=final_state, model_fingerprint=fingerprint,
            )
        except Exception as _e:
            import logging
            logging.getLogger("tradingagents").warning("Audit write-through failed: %s", _e)

        # Publish final decision to Redis so WebSocket clients get the result
        _publisher.final_decision(
            decision=self.process_signal(final_state.get("final_trade_decision", "HOLD")),
            confidence=final_state.get("confidence_scores", {}).get("overall"),
        )

        # Return decision and processed signal
        return final_state, self.process_signal(final_state["final_trade_decision"])

    def _log_state(self, trade_date, final_state):
        """Log the final state to a JSON file with EGX-specific fields."""
        
        # Check if EGX market
        is_egx = final_state.get("target_market") == "EGX"
        
        # Base state logging
        state_log = {
            "company_of_interest": final_state.get("company_of_interest", ""),
            "trade_date": final_state.get("trade_date", ""),
            "market_report": final_state.get("market_report", ""),
            "sentiment_report": final_state.get("sentiment_report", ""),
            "news_report": final_state.get("news_report", ""),
            "fundamentals_report": final_state.get("fundamentals_report", ""),
            "investment_debate_state": {
                "bull_history": final_state.get("investment_debate_state", {}).get("bull_history", ""),
                "bear_history": final_state.get("investment_debate_state", {}).get("bear_history", ""),
                "history": final_state.get("investment_debate_state", {}).get("history", ""),
                "current_response": final_state.get("investment_debate_state", {}).get("current_response", ""),
                "judge_decision": final_state.get("investment_debate_state", {}).get("judge_decision", ""),
            },
            "trader_investment_decision": final_state.get("trader_investment_plan", ""),
            "risk_debate_state": {
                "risky_history": final_state.get("risk_debate_state", {}).get("risky_history", ""),
                "safe_history": final_state.get("risk_debate_state", {}).get("safe_history", ""),
                "neutral_history": final_state.get("risk_debate_state", {}).get("neutral_history", ""),
                "history": final_state.get("risk_debate_state", {}).get("history", ""),
                "judge_decision": final_state.get("risk_debate_state", {}).get("judge_decision", ""),
            },
            "investment_plan": final_state.get("investment_plan", ""),
            "final_trade_decision": final_state.get("final_trade_decision", ""),
        }
        
        # EGX-specific state logging
        if is_egx:
            state_log.update({
                # Market context
                "target_market": "EGX",
                "trading_currency": final_state.get("trading_currency", "EGP"),
                
                # Liquidity info
                "low_liquidity": final_state.get("low_liquidity", False),
                "avg_daily_volume": final_state.get("avg_daily_volume", 0),
                
                # Structured analyses
                "technical_analysis": final_state.get("technical_analysis", {}),
                "fundamental_analysis": final_state.get("fundamental_analysis", {}),
                "sentiment_analysis": final_state.get("sentiment_analysis", {}),
                
                # Theses
                "bull_thesis": final_state.get("investment_debate_state", {}).get("bull_thesis"),
                "bear_thesis": final_state.get("investment_debate_state", {}).get("bear_thesis"),
                
                # Execution plan
                "execution_plan": final_state.get("execution_plan"),
                
                # Risk assessment and veto
                "risk_assessment": final_state.get("risk_assessment", {}),
                "risk_veto": final_state.get("risk_veto", False),
                
                # Confidence tracking
                "confidence_scores": final_state.get("confidence_scores", {}),
                "data_quality": final_state.get("data_quality", {}),
            })

        # Phase 3 (PR 9): always attach sentiment audit record, EGX or not.
        # extract_sentiment_audit_record is pure — never raises.
        try:
            state_log["sentiment_audit"] = extract_sentiment_audit_record(final_state)
        except Exception as _e:  # pragma: no cover
            import logging as _logging
            _logging.getLogger("tradingagents").warning(
                "_log_state: failed to extract sentiment audit record: %s", _e
            )

        self.log_states_dict[str(trade_date)] = state_log

        # Save to file
        directory = Path(f"eval_results/{self.ticker}/TradingAgentsStrategy_logs/")
        directory.mkdir(parents=True, exist_ok=True)

        with open(
            f"eval_results/{self.ticker}/TradingAgentsStrategy_logs/full_states_log_{trade_date}.json",
            "w",
        ) as f:
            json.dump(self.log_states_dict, f, indent=4, default=str)

    def reflect_and_remember(self, returns_losses):
        """Reflect on decisions and update memory based on returns.

        In backtest mode this is a no-op: per-trade reflection would fire 5 LLM
        calls per date (~2-4 min) with no feedback into the current run.
        Call `flush_reflection_queue()` at the end of a full backtest to batch
        all queued (state, return) pairs into a single reflection pass.
        """
        if self.config.get("backtest_mode", False):
            # Queue for end-of-backtest batch reflection instead of running inline
            if not hasattr(self, "_reflection_queue"):
                self._reflection_queue = []
            self._reflection_queue.append((self.curr_state, returns_losses))
            return

        self._run_reflections(returns_losses)

    def _run_reflections(self, returns_losses):
        """Execute all five reflection LLM calls for the current state."""
        self.reflector.reflect_bull_researcher(
            self.curr_state, returns_losses, self.bull_memory
        )
        self.reflector.reflect_bear_researcher(
            self.curr_state, returns_losses, self.bear_memory
        )
        self.reflector.reflect_trader(
            self.curr_state, returns_losses, self.trader_memory
        )
        self.reflector.reflect_invest_judge(
            self.curr_state, returns_losses, self.invest_judge_memory
        )
        self.reflector.reflect_risk_manager(
            self.curr_state, returns_losses, self.risk_manager_memory
        )

    def flush_reflection_queue(self):
        """
        Batch-process all queued reflections accumulated during a backtest run.
        Call once after the full backtest completes to persist memory updates.
        Only the most recent entry per date is processed to avoid redundancy.
        """
        queue = getattr(self, "_reflection_queue", [])
        if not queue:
            return

        original_state = self.curr_state
        for state, returns_losses in queue:
            self.curr_state = state
            self._run_reflections(returns_losses)

        self.curr_state = original_state
        self._reflection_queue = []

    def process_signal(self, full_signal):
        """Process a signal to extract the core decision."""
        return self.signal_processor.process_signal(full_signal)
