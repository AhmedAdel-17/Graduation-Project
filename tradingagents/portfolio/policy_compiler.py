"""Policy Compiler — qualitative InvestmentPolicy → quantitative OptimizerParams.

The deterministic half of Enhancement 1 (design §3.4). The Strategy Agent (LLM,
P2) infers *what the user wants* as an enum-constrained ``InvestmentPolicy``; this
module turns that into the exact numbers the optimizer obeys, via a versioned,
inspectable mapping table. Keeping the qualitative→quantitative step in pure code
(not an LLM) is what makes allocations auditable: every constraint traces to a
named rule, e.g. "low risk → max 10%/name (compiler v1, rule R-LOW)".

It also runs deterministic **conflict detection** between the stated policy and
the actual portfolio, surfaced as ``PolicyFlag``s. Flags never change the math —
they are explained to the user, who decides.

Returns ``(OptimizerParams, list[PolicyFlag])``.

Deferred to a later compiler version (needs per-ticker universe vol from
market_data, not available here): the design's "auto-exclude names with 90d vol
above universe P75 for low-risk profiles". v1 honors explicit exclusions only.
"""

from __future__ import annotations

import logging
from typing import Optional

from tradingagents.portfolio.schemas import (
    FlagSeverity,
    Horizon,
    InvestmentPolicy,
    Objective,
    OptimizerParams,
    PolicyFlag,
    PortfolioAnalytics,
    RiskTolerance,
)

logger = logging.getLogger("tradingagents.portfolio.policy_compiler")

#: Bump when any mapping constant below changes (persisted on OptimizerParams +
#: in the proposal audit blob, so a proposal is reproducible against the rules
#: that produced it).
COMPILER_VERSION = "1.0"

# Base costs / penalties (EGX ~0.2%/side; turnover base discourages churn).
_TRANSACTION_COST_PCT = 0.002
_TURNOVER_PENALTY_BASE = 0.10

# --- mapping tables (the inspectable rule set) -----------------------------
# risk_tolerance → mean-variance risk aversion λ (higher = more cautious)
_RISK_AVERSION = {
    RiskTolerance.VERY_LOW: 10.0, RiskTolerance.LOW: 8.0, RiskTolerance.MEDIUM: 5.0,
    RiskTolerance.HIGH: 2.0, RiskTolerance.VERY_HIGH: 1.0,
}
# risk_tolerance → per-name weight cap (%)
_MAX_POSITION = {
    RiskTolerance.VERY_LOW: 8.0, RiskTolerance.LOW: 10.0, RiskTolerance.MEDIUM: 15.0,
    RiskTolerance.HIGH: 20.0, RiskTolerance.VERY_HIGH: 25.0,
}
# risk_tolerance → per-sector cap (%)
_MAX_SECTOR = {
    RiskTolerance.VERY_LOW: 25.0, RiskTolerance.LOW: 30.0, RiskTolerance.MEDIUM: 40.0,
    RiskTolerance.HIGH: 50.0, RiskTolerance.VERY_HIGH: 60.0,
}
# horizon → view shrinkage toward prior (0 = full agent views, 1 = ignore views).
# Agent theses are multi-month; distrust them as the horizon shortens.
_VIEW_SHRINKAGE = {
    Horizon.LT_6M: 0.75, Horizon.M6_12: 0.40, Horizon.Y1_3: 0.15, Horizon.GT_3Y: 0.0,
}
# horizon → cash floor (%). Short horizon + EGX T+2 / ±10% limits ⇒ hold cash.
_HORIZON_MIN_CASH = {
    Horizon.LT_6M: 60.0, Horizon.M6_12: 20.0, Horizon.Y1_3: 0.0, Horizon.GT_3Y: 0.0,
}
# horizon → turnover penalty, in annual-return objective units (a coefficient on
# Σ|Δweight|). Calibrated near round-trip transaction cost (~0.4%) so it damps
# churn WITHOUT swamping the view tilts (VIEW_SCALE≈0.10); a shorter horizon
# penalizes turnover more so it doesn't trade for a payoff it won't hold to see.
_TURNOVER_PENALTY = {
    Horizon.LT_6M: 0.020, Horizon.M6_12: 0.010, Horizon.Y1_3: 0.005, Horizon.GT_3Y: 0.005,
}
# objective → volatility target/ceiling as a multiple of benchmark (EGX30) vol
_VOL_TARGET_MULT = {Objective.CAPITAL_PRESERVATION: 0.6, Objective.INCOME: 0.8}
_VOL_CEILING_MULT = {Objective.AGGRESSIVE_GROWTH: 1.5, Objective.GROWTH: 1.2}

# Conflict thresholds
_CONCENTRATION_HHI = 0.35  # "low risk but currently this concentrated" trigger


def compile_policy(
    policy: InvestmentPolicy,
    analytics: PortfolioAnalytics,
    *,
    benchmark_vol: Optional[float] = None,
) -> tuple[OptimizerParams, list[PolicyFlag]]:
    """Compile ``policy`` into optimizer params + surface deterministic conflicts.

    ``benchmark_vol`` is the annualized EGX30 (or proxy) vol used to set absolute
    vol targets for the preservation / growth objectives; when absent, vol
    bounds are left unset (the optimizer applies no vol constraint).
    """
    rt, hz, obj = policy.risk_tolerance, policy.horizon, policy.objective

    # per-name cap: user's explicit override wins over the risk-tier default
    max_position_pct = policy.max_position_pct if policy.max_position_pct is not None \
        else _MAX_POSITION[rt]

    # cash floor: the larger of the horizon floor and the user's explicit cash
    horizon_floor = _HORIZON_MIN_CASH[hz]
    user_floor = 0.0
    if policy.min_cash_egp is not None and analytics.total_value_egp > 0:
        user_floor = min(100.0, policy.min_cash_egp / analytics.total_value_egp * 100.0)
    min_cash_pct = min(100.0, max(horizon_floor, user_floor))

    # objective vol bounds (absolute, derived from benchmark vol when available)
    vol_target = vol_ceiling = None
    if benchmark_vol is not None and benchmark_vol > 0:
        if obj in _VOL_TARGET_MULT:
            vol_target = benchmark_vol * _VOL_TARGET_MULT[obj]
        if obj in _VOL_CEILING_MULT:
            vol_ceiling = benchmark_vol * _VOL_CEILING_MULT[obj]

    params = OptimizerParams(
        risk_aversion=_RISK_AVERSION[rt],
        max_position_pct=max_position_pct,
        max_sector_pct=_MAX_SECTOR[rt],
        min_cash_pct=min_cash_pct,
        view_shrinkage=_VIEW_SHRINKAGE[hz],
        turnover_penalty=_TURNOVER_PENALTY[hz],
        transaction_cost_pct=_TRANSACTION_COST_PCT,
        vol_target=vol_target,
        vol_ceiling=vol_ceiling,
        income_tilt=policy.income_preference,
        excluded_tickers=list(policy.excluded_tickers),
        excluded_sectors=list(policy.excluded_sectors),
        compiler_version=COMPILER_VERSION,
        policy_version=policy.version,
    )

    flags = _detect_conflicts(policy, analytics, params)
    return params, flags


def _detect_conflicts(
    policy: InvestmentPolicy, analytics: PortfolioAnalytics, params: OptimizerParams,
) -> list[PolicyFlag]:
    """Deterministic policy-vs-portfolio and policy-vs-policy consistency checks."""
    flags: list[PolicyFlag] = []
    rt, hz, obj = policy.risk_tolerance, policy.horizon, policy.objective

    # 1. stated low risk, but the current book is concentrated
    if rt in (RiskTolerance.VERY_LOW, RiskTolerance.LOW) and analytics.hhi > _CONCENTRATION_HHI:
        flags.append(PolicyFlag(
            code="CONCENTRATION_VS_RISK", severity=FlagSeverity.WARNING,
            detail="Your current portfolio is more concentrated than your stated "
                   "low risk tolerance implies.",
            data={"hhi": round(analytics.hhi, 4), "risk_tolerance": rt.value,
                  "rule": "compiler-1.0/CONFLICT-CONC"},
        ))

    # 2. short horizon vs a growth objective (honest tension on EGX)
    if hz == Horizon.LT_6M and obj in (Objective.GROWTH, Objective.AGGRESSIVE_GROWTH):
        flags.append(PolicyFlag(
            code="HORIZON_VS_OBJECTIVE", severity=FlagSeverity.WARNING,
            detail="A growth objective over a sub-6-month horizon is hard to act on "
                   "with EGX T+2 settlement and ±10% daily limits; expect a high cash floor.",
            data={"horizon": hz.value, "objective": obj.value, "min_cash_pct": params.min_cash_pct,
                  "rule": "compiler-1.0/CONFLICT-HZN"},
        ))

    # 3. reaching the cash floor requires selling (book is more invested than the floor allows)
    invested_pct = 100.0 - analytics.cash_drag_pct
    if params.min_cash_pct > analytics.cash_drag_pct + 1e-9 and invested_pct > 0:
        flags.append(PolicyFlag(
            code="CASH_FLOOR_REQUIRES_SELLING", severity=FlagSeverity.INFO,
            detail=f"Holding at least {params.min_cash_pct:.0f}% cash will require selling "
                   f"part of the current {invested_pct:.0f}% invested.",
            data={"min_cash_pct": params.min_cash_pct, "current_cash_pct": round(analytics.cash_drag_pct, 2),
                  "rule": "compiler-1.0/CONFLICT-CASH"},
        ))

    # 4. an exclusion lands on a currently-held name / sector → implies a sell
    held_tickers = {h.ticker for h in analytics.holdings}
    hit_tickers = sorted(held_tickers & set(params.excluded_tickers))
    held_sectors = {h.sector for h in analytics.holdings}
    hit_sectors = sorted(held_sectors & set(params.excluded_sectors))
    if hit_tickers or hit_sectors:
        flags.append(PolicyFlag(
            code="EXCLUSION_HITS_HOLDING", severity=FlagSeverity.INFO,
            detail="An exclusion you set applies to something you currently hold; "
                   "the proposal will sell it.",
            data={"tickers": hit_tickers, "sectors": hit_sectors,
                  "rule": "compiler-1.0/CONFLICT-EXCL"},
        ))

    return flags


__all__ = ["compile_policy", "COMPILER_VERSION"]
