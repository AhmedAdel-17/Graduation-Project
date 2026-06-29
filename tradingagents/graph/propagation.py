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
# - Confidence tracking (Phase 3: quorum-aware + sentiment-blended)
# - Structured analysis storage
# - Data quality indicators
#
# Phase 3 changes (PR 7):
# - propagate_confidence() now enforces the quorum rule (≥2 directional analysts)
#   and applies sentiment blend multipliers read from state["sentiment_blend_result"].
# - Returns "overall_status": "OK" | "INSUFFICIENT_DATA" alongside "overall" confidence.
# - Position-size multiplier is surfaced at "position_size_multiplier" in the result.
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
        set_config({"trade_date": str(trade_date)})

        config = get_config()
        target_market = config.get("target_market", "US")
        is_egx = target_market == "EGX"

        state: Dict[str, Any] = {
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

        if is_egx:
            state.update(
                {
                    # Market context
                    "target_market": "EGX",
                    "trading_currency": config.get("trading_currency", "EGP"),
                    # Liquidity tracking
                    "low_liquidity": False,
                    "avg_daily_volume": 0,
                    "volume_missing": False,
                    # Structured analyses (JSON objects)
                    "technical_analysis": {},
                    "fundamental_analysis": {},
                    "sentiment_analysis": {},
                    # Confidence tracking
                    "confidence_scores": {
                        "technical": None,
                        "fundamental": None,
                        "sentiment": None,
                        "overall": None,
                        "overall_status": "PENDING",
                        "position_size_multiplier": 1.0,
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
                    "risk_veto": False,
                    # Portfolio context (for position sizing)
                    "portfolio_value": config.get("portfolio_value", 10_000_000),
                    "current_price": 0,
                    # Phase 3 (PR 7): sentiment blend result written by social_media_analyst
                    "sentiment_blend_result": None,
                    # Macro environment (populated by DataPrefetcher.fetch_all → "macro_context")
                    "macro_context": None,
                }
            )

        return state

    def get_graph_args(self) -> Dict[str, Any]:
        """Get arguments for the graph invocation."""
        return {
            "stream_mode": "values",
            "config": {"recursion_limit": self.max_recur_limit},
        }

    @staticmethod
    def propagate_confidence(state: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate per-analyst and overall confidence scores.

        Phase 3 behaviour (PR 7):
        - Enforces the quorum rule: ≥2 directional analysts must have a non-None
          confidence.  When quorum fails, ``overall_status`` is ``"INSUFFICIENT_DATA"``
          and ``overall`` is set to 0.10 (minimum floor, not 0, so downstream code
          that gates on ``overall > 0`` still sees a signal to abort gracefully).
        - Reads ``state["sentiment_blend_result"]`` and applies the confidence
          multiplier to ``overall``.  The position-size multiplier is surfaced
          as ``position_size_multiplier`` in the returned dict.

        Returns
        -------
        dict with keys: technical, fundamental, sentiment (each float or None),
        overall (float in [0.10, 1.0]), overall_status ("OK" | "INSUFFICIENT_DATA"),
        position_size_multiplier (float in [0.50, 1.0]).
        """
        import json as _json
        from tradingagents.agents.utils.scoring import (
            QUORUM_MINIMUM,
            blend_from_dict,
        )
        from tradingagents.dataflows.config import get_config
        config = get_config()

        # ── Market / Technical analyst ─────────────────────────────────────
        tech_conf: float | None = None
        technical_analysis = state.get("technical_analysis") or {}
        if isinstance(technical_analysis, dict):
            raw = technical_analysis.get("confidence_score")
            if raw is not None:
                try:
                    tech_conf = float(raw)
                    if tech_conf > 1.0:
                        tech_conf /= 100.0
                except (TypeError, ValueError):
                    pass

        # ── Fundamentals analyst ───────────────────────────────────────────
        fund_conf: float | None = None
        fundamental_analysis = state.get("fundamental_analysis") or {}
        if isinstance(fundamental_analysis, dict):
            raw = fundamental_analysis.get("confidence_score") or fundamental_analysis.get(
                "data_completeness"
            )
            if raw is not None:
                try:
                    fund_conf = float(raw)
                    if fund_conf > 1.0:
                        fund_conf /= 100.0
                except (TypeError, ValueError):
                    pass

        # ── News analyst ───────────────────────────────────────────────────
        news_conf: float | None = None
        sentiment_analysis = state.get("sentiment_analysis") or {}
        if isinstance(sentiment_analysis, dict):
            combined = sentiment_analysis.get("combined_sentiment") or {}
            raw = combined.get("confidence") if combined else sentiment_analysis.get(
                "confidence_score"
            )
            if raw is not None:
                try:
                    news_conf = float(raw)
                    if news_conf > 1.0:
                        news_conf /= 100.0
                except (TypeError, ValueError):
                    pass

        # ── Social media analyst ───────────────────────────────────────────
        social_conf: float | None = None
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

        # Blend news + social into a single sentiment confidence
        sent_vals = [v for v in [news_conf, social_conf] if v is not None]
        sent_conf: float | None = sum(sent_vals) / len(sent_vals) if sent_vals else None

        # ── Quorum check ───────────────────────────────────────────────────
        # Directional analysts: technical, fundamental, news (3 total)
        directional_confs = [tech_conf, fund_conf, news_conf]
        active_directional = [c for c in directional_confs if c is not None]
        quorum_met = len(active_directional) >= QUORUM_MINIMUM

        if not quorum_met:
            return {
                "technical": round(tech_conf, 3) if tech_conf is not None else None,
                "fundamental": round(fund_conf, 3) if fund_conf is not None else None,
                "sentiment": round(sent_conf, 3) if sent_conf is not None else None,
                "overall": 0.10,
                "overall_status": "INSUFFICIENT_DATA",
                "position_size_multiplier": 1.0,
            }

        # ── Aggregate (weakest-link dampening) ────────────────────────────
        all_valid = [s for s in [tech_conf, fund_conf, sent_conf] if s is not None]
        min_conf = min(all_valid)
        avg_conf = sum(all_valid) / len(all_valid)
        # P7: reduced from 30/70 to 15/85 — weakest-link was over-dampening
        # the reported confidence when one analyst (typically news) had low
        # confidence.  This metric is used for audit/logging and injected into
        # the Research Manager prompt (P7 confidence context), not for sizing.
        # B1 (P8): further reduce to 5/95 when enabled.
        wl_weight = 0.05 if config.get("b1_weakest_link_enabled") else 0.15
        overall = wl_weight * min_conf + (1 - wl_weight) * avg_conf

        # Data-quality penalty
        data_quality = state.get("data_quality") or {}
        completeness = data_quality.get("data_completeness_score", 100) / 100.0
        overall *= completeness

        # ── Apply sentiment blend multiplier ───────────────────────────────
        blend_dict = state.get("sentiment_blend_result")
        blend = blend_from_dict(blend_dict) if blend_dict else None

        if blend is not None:
            overall = overall * blend.confidence_multiplier
            pos_size_mult = blend.position_size_multiplier
        else:
            pos_size_mult = 1.0

        # ── Fix C (P8): Market breadth confidence modulation ──────────────
        # Adjusts confidence based on broad market regime. NOT a hard gate.
        # Rally → slight confidence boost (tailwind), downturn → dampen.
        if config.get("market_breadth_enabled"):
            breadth = state.get("market_breadth") or {}
            regime = breadth.get("regime", "sideways")
            dampening = config.get("market_breadth_dampening_factor", 0.75)
            if regime == "rally":
                overall *= 1.0 + (1.0 - dampening)  # e.g. 1.25x in rally
            elif regime == "downturn":
                overall *= dampening  # e.g. 0.75x in downturn
            # sideways: no change

        # B3 (P8): lower hard confidence floor when enabled
        conf_floor = 0.01 if config.get("b3_confidence_floor_enabled") else 0.10

        return {
            "technical": round(tech_conf, 3) if tech_conf is not None else None,
            "fundamental": round(fund_conf, 3) if fund_conf is not None else None,
            "sentiment": round(sent_conf, 3) if sent_conf is not None else None,
            "overall": round(max(conf_floor, min(1.0, overall)), 3),
            "overall_status": "OK",
            "position_size_multiplier": round(max(0.0, min(1.0, pos_size_mult)), 4),
        }

    @staticmethod
    def should_halt_on_risk_veto(state: Dict[str, Any]) -> bool:
        """Check if risk veto has been triggered and execution should stop."""
        risk_assessment = state.get("risk_assessment", {})

        if state.get("risk_veto", False):
            return True

        if isinstance(risk_assessment, dict):
            if risk_assessment.get("approved") is False:
                return True

        return False
