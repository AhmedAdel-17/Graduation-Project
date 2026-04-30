# TradingAgents/graph/propagation.py

from typing import Dict, Any
from tradingagents.agents.utils.agent_states import (
    AgentState,
    InvestDebateState,
    RiskDebateState,
)
from tradingagents.dataflows.config import get_config, set_config

# =============================================================================
# EGX-Enhanced Propagator
# =============================================================================
# Handles state initialization with EGX-specific fields:
# - Liquidity flags
# - Confidence tracking
# - Structured analysis storage
# - Data quality indicators
# =============================================================================


class Propagator:
    """Handles state initialization and propagation through the graph."""

    def __init__(self, max_recur_limit=100):
        """Initialize with configuration parameters."""
        self.max_recur_limit = max_recur_limit

    def create_initial_state(
        self, company_name: str, trade_date: str
    ) -> Dict[str, Any]:
        """Create the initial state for the agent graph with EGX enhancements."""
        
        # Inject trade_date into global config so tool wrappers can enforce
        # a hard ceiling on any end_date parameters the LLM supplies.
        # This prevents the LLM from accidentally requesting future OHLCV/news data.
        set_config({"trade_date": str(trade_date)})

        # Get config to determine market
        config = get_config()
        target_market = config.get("target_market", "US")
        is_egx = target_market == "EGX"
        
        # Base state
        state = {
            "messages": [("human", company_name)],
            "company_of_interest": company_name,
            "trade_date": str(trade_date),
            "investment_debate_state": InvestDebateState(
                {
                    "bull_history": "",
                    "bear_history": "",
                    "history": "",
                    "current_response": "",
                    "judge_decision": "",
                    "count": 0,
                }
            ),
            "risk_debate_state": RiskDebateState(
                {
                    "risky_history": "",
                    "safe_history": "",
                    "neutral_history": "",
                    "history": "",
                    "latest_speaker": "",
                    "current_risky_response": "",
                    "current_safe_response": "",
                    "current_neutral_response": "",
                    "judge_decision": "",
                    "count": 0,
                }
            ),
            # Analyst reports (text)
            "market_report": "",
            "fundamentals_report": "",
            "sentiment_report": "",
            "news_report": "",
        }
        
        # EGX-specific state extensions
        if is_egx:
            state.update({
                # Market context
                "target_market": "EGX",
                "trading_currency": config.get("trading_currency", "EGP"),
                
                # Liquidity tracking
                "low_liquidity": False,  # Set by data layer
                "avg_daily_volume": 0,
                "volume_missing": False,
                
                # Structured analyses (JSON objects)
                "technical_analysis": {},  # From Chartist
                "fundamental_analysis": {},  # From Accountant
                "sentiment_analysis": {},  # From Journalist
                
                # Confidence tracking - weak data reduces downstream confidence
                "confidence_scores": {
                    "technical": None,
                    "fundamental": None,
                    "sentiment": None,
                    "overall": None,
                },
                
                # Data quality indicators
                "data_quality": {
                    "price_data_complete": True,
                    "fundamentals_complete": True,
                    "news_available": True,
                    "data_completeness_score": 100,
                },
                
                # Thesis summaries (from Bull/Bear researchers)
                "bull_thesis": None,
                "bear_thesis": None,
                
                # Execution plan (from Trader)
                "execution_plan": None,
                
                # Risk assessment (from Risk Manager)
                "risk_assessment": None,
                "risk_veto": False,  # If True, stops execution
                
                # Portfolio context (for position sizing)
                "portfolio_value": config.get("portfolio_value", 10000000),  # 10M EGP default
                "current_price": 0,
            })
        
        return state

    def get_graph_args(self) -> Dict[str, Any]:
        """Get arguments for the graph invocation."""
        return {
            "stream_mode": "values",
            "config": {"recursion_limit": self.max_recur_limit},
        }
    
    @staticmethod
    def propagate_confidence(state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate confidence scores by harvesting values from analyst output dicts.

        Each analyst stores its confidence_score inside its structured analysis dict.
        This function reads those values directly — no analyst needs to write to
        state["confidence_scores"] explicitly.

        Returns:
            dict with keys: technical, fundamental, sentiment (each float or None),
            and overall (float in [0.10, 1.0]).
        """
        import json as _json

        # ── Market / Technical analyst ──────────────────────────────────────
        tech_conf = None
        technical_analysis = state.get("technical_analysis") or {}
        if isinstance(technical_analysis, dict):
            raw = technical_analysis.get("confidence_score")
            if raw is not None:
                try:
                    tech_conf = float(raw)
                    # Normalise: analysts return 0-1; guard against 0-100 scale
                    if tech_conf > 1.0:
                        tech_conf /= 100.0
                except (TypeError, ValueError):
                    pass

        # ── Fundamentals analyst ────────────────────────────────────────────
        fund_conf = None
        fundamental_analysis = state.get("fundamental_analysis") or {}
        if isinstance(fundamental_analysis, dict):
            raw = fundamental_analysis.get("confidence_score") or fundamental_analysis.get("data_completeness")
            if raw is not None:
                try:
                    fund_conf = float(raw)
                    if fund_conf > 1.0:
                        fund_conf /= 100.0
                except (TypeError, ValueError):
                    pass

        # ── News analyst ────────────────────────────────────────────────────
        news_conf = None
        sentiment_analysis = state.get("sentiment_analysis") or {}
        if isinstance(sentiment_analysis, dict):
            combined = sentiment_analysis.get("combined_sentiment") or {}
            raw = combined.get("confidence") if combined else sentiment_analysis.get("confidence_score")
            if raw is not None:
                try:
                    news_conf = float(raw)
                    if news_conf > 1.0:
                        news_conf /= 100.0
                except (TypeError, ValueError):
                    pass

        # ── Social media analyst ────────────────────────────────────────────
        social_conf = None
        social_raw = state.get("social_sentiment_analysis") or "{}"
        try:
            social_data = (
                _json.loads(social_raw) if isinstance(social_raw, str) else social_raw
            ) or {}
            if isinstance(social_data, dict):
                combined = social_data.get("combined_sentiment") or {}
                raw = combined.get("confidence") if combined else None
                if raw is not None:
                    social_conf = float(raw)
                    if social_conf > 1.0:
                        social_conf /= 100.0
        except (TypeError, ValueError, _json.JSONDecodeError):
            pass

        # ── Blend news + social into a single sentiment confidence ──────────
        sent_vals = [v for v in [news_conf, social_conf] if v is not None]
        sent_conf = sum(sent_vals) / len(sent_vals) if sent_vals else None

        # ── Aggregate across analysts (weakest-link principle) ──────────────
        valid_scores = [s for s in [tech_conf, fund_conf, sent_conf] if s is not None]

        if not valid_scores:
            return {
                "technical": None,
                "fundamental": None,
                "sentiment": None,
                "overall": 0.50,
            }

        min_conf = min(valid_scores)
        avg_conf = sum(valid_scores) / len(valid_scores)

        # 30% weight on the weakest signal, 70% on the average.
        # Prevents one weak analyst from crushing a strong consensus.
        overall = 0.30 * min_conf + 0.70 * avg_conf

        # Data-quality penalty: multiply by CSV completeness score (0-1)
        data_quality = state.get("data_quality") or {}
        completeness = data_quality.get("data_completeness_score", 100) / 100.0
        overall *= completeness

        return {
            "technical": round(tech_conf, 3) if tech_conf is not None else None,
            "fundamental": round(fund_conf, 3) if fund_conf is not None else None,
            "sentiment": round(sent_conf, 3) if sent_conf is not None else None,
            "overall": round(max(0.10, min(1.0, overall)), 3),
        }
    
    @staticmethod
    def should_halt_on_risk_veto(state: Dict[str, Any]) -> bool:
        """
        Check if risk veto has been triggered and execution should stop.
        
        Returns:
            bool: True if execution should be halted
        """
        risk_assessment = state.get("risk_assessment", {})
        
        # Check explicit veto flag
        if state.get("risk_veto", False):
            return True
        
        # Check risk assessment approval status
        if isinstance(risk_assessment, dict):
            if risk_assessment.get("approved") is False:
                return True
        
        return False

