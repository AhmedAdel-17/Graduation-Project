"""Goal feasibility (deterministic).

When the user states a concrete numeric goal ("grow my 50,000 EGP to 80,000 in two
years"), this module answers the natural follow-up: *is that realistic?* It does so
honestly, on the efficient-frontier logic the rest of the engine already uses:

1. Compute the **required annual return** that turns the current portfolio value
   (plus any planned monthly contributions) into the target by the horizon — a
   future-value equation solved for the rate (bisection; closed form has none once
   contributions are included).
2. Compare it to the proposal's **model-view expected return** and **volatility**
   (the Black-Litterman posterior outputs, already computed by the optimizer):
   * required ≤ expected                         → **achievable** (central case meets it)
   * expected < required ≤ expected + 1·vol      → **ambitious** (reachable only in a
     good year — within ~1 standard deviation, not the expectation)
   * required > expected + 1·vol                 → **unrealistic** for this risk profile

Everything here is a *model view, not a forecast* — and the messages say so. Pure
and deterministic: same inputs → same verdict.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from tradingagents.portfolio.schemas import FlagSeverity, Horizon, InvestmentPolicy, PolicyFlag

logger = logging.getLogger("tradingagents.portfolio.goal")

GOAL_VERSION = "1.0"

#: Fallback horizon (months) when only the qualitative bucket is known — the
#: midpoint of each enum band, used if the user gave no explicit goal_horizon_months.
_HORIZON_MONTHS = {Horizon.LT_6M: 3, Horizon.M6_12: 9, Horizon.Y1_3: 24, Horizon.GT_3Y: 60}


@dataclass
class GoalFeasibility:
    verdict: str  # achievable | ambitious | unrealistic | underspecified | no_goal
    required_return_annual: Optional[float] = None
    expected_return_annual: Optional[float] = None
    expected_vol_annual: Optional[float] = None
    horizon_years: Optional[float] = None
    target_amount_egp: Optional[float] = None
    current_value_egp: Optional[float] = None
    message_en: str = ""
    message_ar: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def has_goal(self) -> bool:
        return self.verdict not in ("no_goal", "underspecified")


def _future_value(v0: float, monthly: float, monthly_rate: float, months: int) -> float:
    """FV of v0 plus end-of-period monthly contributions at a monthly rate."""
    if abs(monthly_rate) < 1e-12:
        return v0 + monthly * months
    growth = (1.0 + monthly_rate) ** months
    return v0 * growth + monthly * (growth - 1.0) / monthly_rate


def _required_annual_return(v0: float, target: float, months: int, monthly: float) -> Optional[float]:
    """Annual return that grows (v0 + contributions) to target over `months`.
    Bisection on the monthly rate (FV is monotonic increasing in it)."""
    if months <= 0 or v0 <= 0:
        return None
    # If contributions alone already overshoot at 0% growth, required return ≤ 0.
    lo, hi = -0.99, 1.0  # monthly rate bounds (~ -100%/yr .. ~ +400,000%/yr)
    if _future_value(v0, monthly, lo, months) > target:
        return (1.0 + lo) ** 12 - 1.0
    if _future_value(v0, monthly, hi, months) < target:
        return (1.0 + hi) ** 12 - 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if _future_value(v0, monthly, mid, months) < target:
            lo = mid
        else:
            hi = mid
    monthly_rate = (lo + hi) / 2.0
    return (1.0 + monthly_rate) ** 12 - 1.0


def assess_goal_feasibility(
    policy: InvestmentPolicy,
    *,
    current_value_egp: Optional[float],
    expected_return_annual: Optional[float],
    expected_vol_annual: Optional[float],
) -> GoalFeasibility:
    """Assess whether the policy's numeric goal is reachable by the proposed plan."""
    target = policy.goal_target_amount_egp
    if not target:
        return GoalFeasibility(verdict="no_goal")

    months = policy.goal_horizon_months or _HORIZON_MONTHS.get(policy.horizon)
    monthly = float(policy.monthly_contribution_egp or 0.0)
    v0 = float(current_value_egp or 0.0)
    if not months or v0 <= 0:
        return GoalFeasibility(verdict="underspecified", target_amount_egp=target,
                               current_value_egp=v0 or None)

    years = months / 12.0
    required = _required_annual_return(v0, float(target), int(months), monthly)
    if required is None or expected_return_annual is None:
        return GoalFeasibility(
            verdict="underspecified", required_return_annual=required,
            expected_return_annual=expected_return_annual, horizon_years=years,
            target_amount_egp=float(target), current_value_egp=v0)

    vol = float(expected_vol_annual or 0.0)
    exp = float(expected_return_annual)
    if required <= exp + 1e-9:
        verdict = "achievable"
    elif required <= exp + vol + 1e-9:
        verdict = "ambitious"
    else:
        verdict = "unrealistic"

    fb = GoalFeasibility(
        verdict=verdict, required_return_annual=required, expected_return_annual=exp,
        expected_vol_annual=vol, horizon_years=years, target_amount_egp=float(target),
        current_value_egp=v0,
        detail={"months": int(months), "monthly_contribution_egp": monthly,
                "rule": f"goal-{GOAL_VERSION}/{verdict}"},
    )
    _fill_messages(fb)
    return fb


def _fill_messages(fb: GoalFeasibility) -> None:
    rr = (fb.required_return_annual or 0.0) * 100.0
    er = (fb.expected_return_annual or 0.0) * 100.0
    yrs = fb.horizon_years or 0.0
    tgt = fb.target_amount_egp or 0.0
    if fb.verdict == "achievable":
        fb.message_en = (
            f"Your goal of EGP {tgt:,.0f} in ~{yrs:.1f}y needs ~{rr:.1f}%/yr. The proposed "
            f"portfolio's model-view expected return (~{er:.1f}%/yr) covers that — realistic, "
            f"though returns vary year to year (a model view, not a forecast).")
        fb.message_ar = (
            f"هدفك ({tgt:,.0f} ج.م خلال ~{yrs:.1f} سنة) محتاج عائد ~{rr:.1f}% سنويًّا، والعائد "
            f"المتوقّع للمحفظة (~{er:.1f}% — نظرة نموذجية مش تنبؤ) يغطّيه، فهو واقعي.")
    elif fb.verdict == "ambitious":
        fb.message_en = (
            f"Your goal needs ~{rr:.1f}%/yr, above the portfolio's model-view expected "
            f"~{er:.1f}%/yr but within one standard deviation — reachable only in a good run. "
            f"Consider extending the horizon, adding contributions, or accepting more risk.")
        fb.message_ar = (
            f"هدفك محتاج ~{rr:.1f}% سنويًّا، أعلى من العائد المتوقّع (~{er:.1f}%) لكنه ضمن انحراف "
            f"معياري واحد — ممكن في سنة كويسة بس. فكّر تمدّ المدة أو تزوّد المساهمات أو تقبل مخاطرة أكبر.")
    elif fb.verdict == "unrealistic":
        fb.message_en = (
            f"Your goal needs ~{rr:.1f}%/yr, well beyond what this risk profile's portfolio can "
            f"target (model-view ~{er:.1f}%/yr). To make it realistic, extend the horizon, raise "
            f"contributions, or lower the target — not chase return beyond your risk budget.")
        fb.message_ar = (
            f"هدفك محتاج ~{rr:.1f}% سنويًّا، أعلى بكتير من اللي تقدر المحفظة تستهدفه عند مستوى مخاطرتك "
            f"(~{er:.1f}%). عشان يبقى واقعي: مدّ المدة، زوّد المساهمات، أو قلّل الهدف.")


def goal_flag(fb: GoalFeasibility) -> Optional[PolicyFlag]:
    """Render a feasibility verdict as a PolicyFlag (shown in the policy-flags block)."""
    if not fb.has_goal or not fb.message_en:
        return None
    severity = {"achievable": FlagSeverity.INFO, "ambitious": FlagSeverity.WARNING,
                "unrealistic": FlagSeverity.WARNING}.get(fb.verdict, FlagSeverity.INFO)
    return PolicyFlag(
        code="GOAL_FEASIBILITY", severity=severity, detail=fb.message_en,
        data={"verdict": fb.verdict,
              "required_return_annual": fb.required_return_annual,
              "expected_return_annual": fb.expected_return_annual,
              "horizon_years": fb.horizon_years, "target_amount_egp": fb.target_amount_egp,
              **fb.detail},
    )


__all__ = ["assess_goal_feasibility", "GoalFeasibility", "goal_flag", "GOAL_VERSION"]
