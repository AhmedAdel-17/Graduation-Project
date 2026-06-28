"""
EGX risk limits, the RiskViolation data structure, and low-level coercion helpers.

Extracted verbatim from the former monolithic ``risk_scorer.py`` (R4 split,
2026-06-18). This module is the single source of truth for ``EGX_RISK_LIMITS``;
``risk_checks`` and ``risk_scorer`` both import the *same* dict object from here,
so the in-place backtest-mode tweak in ``create_risk_scorer_node`` remains visible
to the check functions exactly as before.

Literature grounding for the limit values lives in the ``risk_scorer`` module
docstring (unchanged).
"""

from typing import Optional


# =============================================================================
# EGX Risk Limits
# =============================================================================

EGX_RISK_LIMITS = {
    # Position limits
    "max_single_stock_pct": 0.10,          # UCITS Art. 52(2) / ICA §5(b)(1)
    "max_sector_pct": 0.30,
    "max_correlated_exposure": 0.40,       # Analogous to UCITS 5/10/40 aggregate

    # Liquidity thresholds (two-tier per Almgren-Chriss + Bangia et al.)
    "throttle_adv_threshold": 0.05,        # Warn+throttle at 5% ADV participation
    "max_position_vs_adv_pct": 0.10,       # Hard VETO at 10% ADV participation
    "min_avg_daily_volume": 50_000,        # Minimum ADV floor (shares)
    "max_days_to_exit": 10,               # Almgren-Chriss "characteristic time"
    "low_liquidity_reduction": 0.50,       # Halve capacity for low-liq stocks

    # Drawdown limits (Elder 2% rule; Tharp R-multiple)
    "max_single_trade_loss_pct": 0.02,     # CRITICAL — 2% per-trade hard cap
    "max_daily_loss_pct": 0.05,
    "max_weekly_loss_pct": 0.10,
    "stop_loss_required": True,

    # EGX market structure
    "daily_price_limit": 0.10,            # Default ±10% (List B). List A uses ±20%.
    "magnet_zone_pct": 0.015,             # 1.5% inside band => magnet zone (Farag 2013)
    "no_short_selling": True,
    "no_leverage": True,

    # ATR stop parameters (Wilder 1978; Kaminski & Lo 2014)
    "atr_stop_multiplier": 2.0,           # 2xATR(14) primary stop distance
    "atr_stop_max_pct": 0.07,             # Cap at 7% — inside EGX magnet zone
    "fallback_stop_pct": 0.05,            # Fixed 5% only when ATR unavailable
}

# EGX tickers with foreign ownership restrictions
EGX_FOREIGN_RESTRICTED = {"SCEM", "SDTI"}


# =============================================================================
# Data Structures
# =============================================================================

class RiskViolation:
    """Represents a single risk rule violation with severity and remediation."""

    def __init__(
        self,
        rule_name: str,
        severity: str,          # "critical" | "high" | "medium" | "low"
        limit_value: float,
        actual_value: float,
        explanation: str,
        remediation: str,
    ):
        self.rule_name = rule_name
        self.severity = severity
        self.limit_value = limit_value
        self.actual_value = actual_value
        self.explanation = explanation
        self.remediation = remediation

    def to_dict(self) -> dict:
        return {
            "rule": self.rule_name,
            "severity": self.severity,
            "limit": self.limit_value,
            "actual": self.actual_value,
            "explanation": self.explanation,
            "remediation": self.remediation,
        }


# =============================================================================
# Helper Utilities
# =============================================================================

def _coerce_numeric(value, default: float = 0.0) -> float:
    """Coerce LLM-returned values (str/int/float/None) to float."""
    if value is None:
        return default
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return default


def _normalize_execution_plan(plan: dict) -> None:
    """Coerce numeric fields in position_sizing and exit_logic to float in-place."""
    ps = plan.get("position_sizing")
    if isinstance(ps, dict):
        for key in ("target_shares", "max_shares", "min_shares", "max_shares_per_day"):
            if key in ps:
                ps[key] = _coerce_numeric(ps[key])
    el = plan.get("exit_logic")
    if isinstance(el, dict):
        sl = el.get("stop_loss")
        if isinstance(sl, dict) and "price" in sl:
            sl["price"] = _coerce_numeric(sl["price"])
        tp = el.get("take_profit")
        if isinstance(tp, dict) and "price" in tp:
            tp["price"] = _coerce_numeric(tp["price"])
