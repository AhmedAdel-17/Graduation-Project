"""Tests for deterministic goal feasibility (Phase a).

Pins the required-return math (incl. contributions) and the three verdicts against
the proposal's model-view return/vol. Pure, offline, deterministic.
"""

from __future__ import annotations

import pytest

from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.goal import assess_goal_feasibility, goal_flag


def _policy(**over) -> s.InvestmentPolicy:
    return s.InvestmentPolicy(**over)


class TestRequiredReturn:
    def test_simple_cagr_no_contributions(self):
        """WHY: 50k → 80k in 2y is (1.6)^(1/2)-1 ≈ 26.5%/yr. PHASE: a."""
        fb = assess_goal_feasibility(
            _policy(goal_target_amount_egp=80000, goal_horizon_months=24),
            current_value_egp=50000, expected_return_annual=0.30, expected_vol_annual=0.15)
        assert fb.required_return_annual == pytest.approx(0.2649, abs=0.005)

    def test_contributions_lower_required_return(self):
        """WHY: adding monthly contributions reduces the return needed. PHASE: a."""
        base = assess_goal_feasibility(
            _policy(goal_target_amount_egp=80000, goal_horizon_months=24),
            current_value_egp=50000, expected_return_annual=0.30, expected_vol_annual=0.15)
        withc = assess_goal_feasibility(
            _policy(goal_target_amount_egp=80000, goal_horizon_months=24, monthly_contribution_egp=1000),
            current_value_egp=50000, expected_return_annual=0.30, expected_vol_annual=0.15)
        assert withc.required_return_annual < base.required_return_annual


class TestVerdicts:
    def _fb(self, required_target, exp, vol):
        # choose a target that needs ~`required_target` over 1y from 100k, no contributions
        target = 100000 * (1 + required_target)
        return assess_goal_feasibility(
            _policy(goal_target_amount_egp=target, goal_horizon_months=12),
            current_value_egp=100000, expected_return_annual=exp, expected_vol_annual=vol)

    def test_achievable(self):
        """WHY: required ≤ expected ⇒ achievable. PHASE: a."""
        fb = self._fb(0.10, exp=0.15, vol=0.20)
        assert fb.verdict == "achievable" and "realistic" in fb.message_en.lower()

    def test_ambitious_within_one_sigma(self):
        """WHY: expected < required ≤ expected+vol ⇒ ambitious. PHASE: a."""
        fb = self._fb(0.20, exp=0.12, vol=0.15)
        assert fb.verdict == "ambitious"

    def test_unrealistic_beyond_risk_budget(self):
        """WHY: required > expected+vol ⇒ unrealistic. PHASE: a."""
        fb = self._fb(0.60, exp=0.12, vol=0.15)
        assert fb.verdict == "unrealistic"
        flag = goal_flag(fb)
        assert flag is not None and flag.code == "GOAL_FEASIBILITY"
        assert flag.severity == s.FlagSeverity.WARNING


class TestEdges:
    def test_no_goal_returns_no_goal(self):
        """WHY: no numeric target ⇒ feasibility is inert (no flag). PHASE: a."""
        fb = assess_goal_feasibility(_policy(), current_value_egp=100000,
                                     expected_return_annual=0.15, expected_vol_annual=0.2)
        assert fb.verdict == "no_goal" and not fb.has_goal and goal_flag(fb) is None

    def test_horizon_enum_midpoint_used_when_no_explicit_months(self):
        """WHY: fall back to the qualitative horizon bucket midpoint. PHASE: a."""
        fb = assess_goal_feasibility(
            _policy(goal_target_amount_egp=120000, horizon=s.Horizon.Y1_3),  # ~24 months
            current_value_egp=100000, expected_return_annual=0.15, expected_vol_annual=0.2)
        assert fb.horizon_years == pytest.approx(2.0, abs=0.01)

    def test_underspecified_without_expected_return(self):
        """WHY: can't judge feasibility without the proposal's expected return. PHASE: a."""
        fb = assess_goal_feasibility(
            _policy(goal_target_amount_egp=120000, goal_horizon_months=24),
            current_value_egp=100000, expected_return_annual=None, expected_vol_annual=None)
        assert fb.verdict == "underspecified"
