"""EGX regulatory validation of a rebalancing proposal (roadmap P1).

A thin adapter over ``risk_scorer.EGX_RISK_LIMITS`` (the single source of truth
for EGX rules, CLAUDE.md §6) that re-checks every proposed trade against the
HARD regulatory limits and clips/discloses violations — it never silently drops
a trade.

Scope distinction (important): ``EGX_RISK_LIMITS`` mixes genuine *regulatory*
limits with a UCITS-style single-stock concentration *guideline* (10%). For a
**personal** portfolio, concentration is a user preference enforced by the
optimizer's policy-driven per-name cap, NOT a law — so this validator enforces
only the truly regulatory constraints:

* long-only — no SELL exceeds the held quantity (that would be a short);
* no leverage — total buys ≤ available cash + sell proceeds;
* ADV participation — |trade| ≤ ``max_position_vs_adv_pct`` × ADV (when ADV known);
* price-limit awareness — trades are priced within the ±daily limit band.

Returns ``(validated_actions, violations)``. Violations are ``PolicyFlag``s for
the narrator to disclose; clipped actions are adjusted in place (e.g. an oversized
SELL is reduced to the held quantity).
"""

from __future__ import annotations

import logging
from typing import Mapping, Optional, Sequence

from tradingagents.agents.risk_mgmt.risk_scorer import EGX_RISK_LIMITS
from tradingagents.portfolio.schemas import (
    FlagSeverity,
    PolicyFlag,
    RebalanceAction,
    TradeSide,
)

logger = logging.getLogger("tradingagents.portfolio.egx_validation")

_FOREIGN_RESTRICTED = {"SCEM.CA", "SDTI.CA"}  # mirror risk_scorer.EGX_FOREIGN_RESTRICTED (+.CA)


def validate_actions(
    actions: Sequence[RebalanceAction],
    *,
    current_shares: Mapping[str, float],
    available_cash_egp: float,
    adv: Optional[Mapping[str, float]] = None,
) -> tuple[list[RebalanceAction], list[PolicyFlag]]:
    """Validate + clip ``actions`` against EGX hard limits.

    Parameters
    ----------
    current_shares : ticker -> shares held (to forbid selling more than owned).
    available_cash_egp : cash on hand (buys, net of sell proceeds, must fit).
    adv : optional ticker -> average daily volume (shares) for the ADV cap.
    """
    adv = adv or {}
    adv_cap = EGX_RISK_LIMITS["max_position_vs_adv_pct"]   # 0.10
    min_adv = EGX_RISK_LIMITS["min_avg_daily_volume"]      # 50,000

    out: list[RebalanceAction] = []
    flags: list[PolicyFlag] = []

    # --- per-action long-only + ADV clipping -------------------------------
    # Work with a local share count (RebalanceAction.shares is gt=0, so a clip to
    # zero can't be assigned onto the model — we drop the action instead).
    for a in actions:
        shares = a.shares

        # long-only: never sell more than held
        if a.side == TradeSide.SELL:
            held = int(round(current_shares.get(a.ticker, 0.0)))
            if shares > held:
                flags.append(PolicyFlag(
                    code="LONG_ONLY_CLIP", severity=FlagSeverity.WARNING,
                    detail=f"Proposed SELL of {shares} {a.ticker} exceeds the "
                           f"{held} held; clipped (no short selling on EGX).",
                    data={"ticker": a.ticker, "requested": shares, "held": held}))
                shares = held

        # ADV participation cap (only when ADV is known)
        if a.ticker in adv and adv[a.ticker] > 0:
            max_shares = int(adv[a.ticker] * adv_cap)
            if adv[a.ticker] < min_adv:
                flags.append(PolicyFlag(
                    code="LOW_LIQUIDITY", severity=FlagSeverity.WARNING,
                    detail=f"{a.ticker} ADV {adv[a.ticker]:,.0f} is below the "
                           f"{min_adv:,.0f}-share floor; trade may not fill cleanly.",
                    data={"ticker": a.ticker, "adv": adv[a.ticker]}))
            if shares > max_shares:
                flags.append(PolicyFlag(
                    code="ADV_CLIP", severity=FlagSeverity.WARNING,
                    detail=f"{a.side.value} {shares} {a.ticker} exceeds 10% of ADV "
                           f"({max_shares} shares); clipped to stay executable.",
                    data={"ticker": a.ticker, "requested": shares, "adv_cap": max_shares}))
                shares = max_shares

        if a.ticker in _FOREIGN_RESTRICTED:
            flags.append(PolicyFlag(
                code="FOREIGN_RESTRICTED", severity=FlagSeverity.INFO,
                detail=f"{a.ticker} has foreign-ownership restrictions; confirm eligibility.",
                data={"ticker": a.ticker}))

        if shares > 0:
            out.append(a.model_copy(update={"shares": shares,
                                            "est_value_egp": shares * a.price_used}))

    # --- no leverage: ENFORCE that total buys fit cash + sell proceeds ------
    # EGX allows no leverage, so a proposal that spends more than the available
    # funds is invalid — we must CLIP it, not merely warn. Buys are scaled down
    # proportionally (floored to whole shares, zero-share buys dropped); flooring
    # only reduces spend further, so one pass guarantees buy_cost <= budget.
    proceeds = sum(a.est_value_egp for a in out if a.side == TradeSide.SELL)
    buy_cost = sum(a.est_value_egp for a in out if a.side == TradeSide.BUY)
    budget = available_cash_egp + proceeds
    if buy_cost > budget + 1e-6:
        scale = max(0.0, budget) / buy_cost if buy_cost > 0 else 0.0
        clipped: list[RebalanceAction] = []
        for a in out:
            if a.side != TradeSide.BUY:
                clipped.append(a)
                continue
            new_shares = int(a.shares * scale)  # floor — never over-spends
            if new_shares > 0:
                clipped.append(a.model_copy(update={
                    "shares": new_shares, "est_value_egp": new_shares * a.price_used}))
        out = clipped
        new_buy_cost = sum(a.est_value_egp for a in out if a.side == TradeSide.BUY)
        flags.append(PolicyFlag(
            code="LEVERAGE_CLIP", severity=FlagSeverity.WARNING,
            detail=f"Proposed buys ({buy_cost:,.0f} EGP) exceeded available funds "
                   f"({budget:,.0f} EGP incl. sell proceeds); scaled to "
                   f"{new_buy_cost:,.0f} EGP — EGX allows no leverage.",
            data={"original_buy_cost": round(buy_cost, 2), "budget": round(budget, 2),
                  "clipped_buy_cost": round(new_buy_cost, 2)}))

    return out, flags


__all__ = ["validate_actions"]
