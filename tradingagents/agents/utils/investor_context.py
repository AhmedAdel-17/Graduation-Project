"""
InvestorContext — runtime snapshot of investor profile injected into graph state.

Strips PII (name, notes) from the dashboard_store profile row. Validates all
fields that will enter LLM prompts (enum checks, length caps, clamping).

Phase 1: portfolio_state and feedback_summary are always None.
Phase 2: extend with live portfolio and accumulated feedback.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from typing_extensions import TypedDict

_log = logging.getLogger("tradingagents.investor_context")

# ── Allowed values (prompt-injection defence) ────────────────────────────

VALID_RISK = {"conservative", "moderate", "aggressive"}
VALID_HORIZON = {"short_term", "medium_term", "long_term"}
VALID_STYLE = {"swing", "position", "core", "intraday"}

_MIN_CAPITAL = 10_000
_MAX_CAPITAL = 1_000_000_000
_MIN_POSITION_PCT = 0.01
_MAX_POSITION_PCT = 0.25
_MAX_SECTOR_ENTRIES = 10
_MAX_SECTOR_LEN = 50


class InvestorContext(TypedDict, total=False):
    """Runtime investor context injected into AgentState."""

    # ── Investor profile (stable preferences) ──
    profile_id: str
    risk_tolerance: str          # "conservative" | "moderate" | "aggressive"
    investment_horizon: str      # "short_term" | "medium_term" | "long_term"
    capital_size: float          # portfolio capital in EGP
    max_position_pct: float      # max single-stock allocation (0.0–1.0)
    sector_preferences: List[str]
    sector_exclusions: List[str]
    trading_style: str           # "swing" | "position" | "core" | "intraday"
    benchmark_target: str        # "EGX30" | "EGX70"
    recommendation_only: bool    # always True — StockHive never executes

    # ── Phase 2 extensions (None for now) ──
    portfolio_state: Optional[Dict]   # {value, cash, holdings, sector_exposure}
    feedback_summary: Optional[str]   # compact preference summary


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def _validate_sector_list(raw: Any) -> List[str]:
    """Sanitise a sector list: cap length and entry count."""
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for entry in raw[:_MAX_SECTOR_ENTRIES]:
        if isinstance(entry, str):
            cleaned = entry.strip()[:_MAX_SECTOR_LEN]
            if cleaned:
                out.append(cleaned)
    return out


def build_investor_context(profile: Dict[str, Any]) -> InvestorContext:
    """Build an InvestorContext from a dashboard_store profile row.

    Strips PII fields (name, notes, investor_category, timestamps).
    Validates and clamps all values that will enter LLM prompts.
    """
    risk = profile.get("risk_tolerance", "moderate")
    if risk not in VALID_RISK:
        _log.warning("Invalid risk_tolerance %r, defaulting to 'moderate'", risk)
        risk = "moderate"

    horizon = profile.get("investment_horizon", "medium_term")
    if horizon not in VALID_HORIZON:
        _log.warning("Invalid investment_horizon %r, defaulting to 'medium_term'", horizon)
        horizon = "medium_term"

    style = profile.get("trading_style", "position")
    if style not in VALID_STYLE:
        _log.warning("Invalid trading_style %r, defaulting to 'position'", style)
        style = "position"

    capital = profile.get("capital_size", 1_000_000)
    if not isinstance(capital, (int, float)):
        capital = 1_000_000
    capital = _clamp(float(capital), _MIN_CAPITAL, _MAX_CAPITAL)

    max_pos = profile.get("max_position_pct", 0.10)
    if not isinstance(max_pos, (int, float)):
        max_pos = 0.10
    max_pos = _clamp(float(max_pos), _MIN_POSITION_PCT, _MAX_POSITION_PCT)

    benchmark = profile.get("benchmark_target", "EGX30")
    if not isinstance(benchmark, str) or len(benchmark) > 20:
        benchmark = "EGX30"

    ctx: InvestorContext = {
        "profile_id": str(profile.get("id", "")),
        "risk_tolerance": risk,
        "investment_horizon": horizon,
        "capital_size": capital,
        "max_position_pct": max_pos,
        "sector_preferences": _validate_sector_list(profile.get("sector_preferences")),
        "sector_exclusions": _validate_sector_list(profile.get("sector_exclusions")),
        "trading_style": style,
        "benchmark_target": benchmark,
        "recommendation_only": True,
        # Phase 2 placeholders
        "portfolio_state": None,
        "feedback_summary": None,
    }
    return ctx
