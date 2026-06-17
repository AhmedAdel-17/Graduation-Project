"""Tests for the Policy Compiler (roadmap P1).

Verifies every mapping row (risk → λ/caps, horizon → shrinkage/cash/turnover,
objective → vol bounds), the user-override precedence, the conflict detector, and
version pinning. This is the explainability backbone: if a rule silently changes,
allocations stop tracing to a documented cause. Pure — no LLM, no data.
"""

from __future__ import annotations

import pytest

from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.analytics import compute_analytics
from tradingagents.portfolio.policy_compiler import COMPILER_VERSION, compile_policy


PRICES = {"COMI.CA": 50.0, "TMGH.CA": 10.0}


def _analytics(cash=30000.0, comi_shares=1000, tmgh_shares=2000):
    snap = s.PortfolioSnapshot(cash_egp=cash, holdings=[
        s.PortfolioHolding(ticker="COMI.CA", shares=comi_shares, avg_cost=40),
        s.PortfolioHolding(ticker="TMGH.CA", shares=tmgh_shares, avg_cost=12),
    ])
    return compute_analytics(snap, PRICES)


# ---------------------------------------------------------------------------
# 1. Risk-tolerance mapping
# ---------------------------------------------------------------------------

class TestRiskMapping:
    @pytest.mark.parametrize("rt,exp_lambda,exp_cap,exp_sector", [
        (s.RiskTolerance.VERY_LOW, 10.0, 8.0, 25.0),
        (s.RiskTolerance.LOW, 8.0, 10.0, 30.0),
        (s.RiskTolerance.MEDIUM, 5.0, 15.0, 40.0),
        (s.RiskTolerance.HIGH, 2.0, 20.0, 50.0),
        (s.RiskTolerance.VERY_HIGH, 1.0, 25.0, 60.0),
    ])
    def test_risk_tier_sets_lambda_and_caps(self, rt, exp_lambda, exp_cap, exp_sector):
        """WHY: risk tolerance drives caution + caps. BUG: a wrong λ/cap makes the
        optimizer ignore the user's risk profile. PHASE: P1 (optimizer)."""
        pol = s.InvestmentPolicy(risk_tolerance=rt)
        params, _ = compile_policy(pol, _analytics())
        assert params.risk_aversion == exp_lambda
        assert params.max_position_pct == exp_cap
        assert params.max_sector_pct == exp_sector


# ---------------------------------------------------------------------------
# 2. Horizon mapping
# ---------------------------------------------------------------------------

class TestHorizonMapping:
    @pytest.mark.parametrize("hz,shrink,cash,turn", [
        (s.Horizon.LT_6M, 0.75, 60.0, 0.020),
        (s.Horizon.M6_12, 0.40, 20.0, 0.010),
        (s.Horizon.Y1_3, 0.15, 0.0, 0.005),
        (s.Horizon.GT_3Y, 0.0, 0.0, 0.005),
    ])
    def test_horizon_sets_shrinkage_cash_turnover(self, hz, shrink, cash, turn):
        """WHY: horizon controls how much to trust agent views, the cash floor,
        and churn. BUG: a 6-month wedding fund optimized with full view weight +
        0 cash floor is the headline failure the design guards against. PHASE: P1."""
        pol = s.InvestmentPolicy(horizon=hz)
        params, _ = compile_policy(pol, _analytics())
        assert params.view_shrinkage == shrink
        assert params.min_cash_pct == cash
        assert params.turnover_penalty == turn


# ---------------------------------------------------------------------------
# 3. Objective vol bounds
# ---------------------------------------------------------------------------

class TestObjectiveVolBounds:
    def test_capital_preservation_sets_vol_target(self):
        """WHY: preservation caps vol at 0.6× benchmark. BUG: no target means a
        'safest possible' request ignores volatility. PHASE: P1 (optimizer)."""
        pol = s.InvestmentPolicy(objective=s.Objective.CAPITAL_PRESERVATION)
        params, _ = compile_policy(pol, _analytics(), benchmark_vol=0.30)
        assert params.vol_target == pytest.approx(0.30 * 0.6)
        assert params.vol_ceiling is None

    def test_aggressive_growth_sets_vol_ceiling(self):
        """WHY: aggressive lifts the vol ceiling to 1.5× benchmark. PHASE: P1."""
        pol = s.InvestmentPolicy(objective=s.Objective.AGGRESSIVE_GROWTH)
        params, _ = compile_policy(pol, _analytics(), benchmark_vol=0.30)
        assert params.vol_ceiling == pytest.approx(0.30 * 1.5)
        assert params.vol_target is None

    def test_no_benchmark_vol_means_no_bounds(self):
        """WHY: without benchmark vol, absolute targets can't be set — leave unset
        rather than guess. BUG: a fabricated vol target. PHASE: P1 honesty."""
        pol = s.InvestmentPolicy(objective=s.Objective.CAPITAL_PRESERVATION)
        params, _ = compile_policy(pol, _analytics())
        assert params.vol_target is None and params.vol_ceiling is None


# ---------------------------------------------------------------------------
# 4. User overrides & passthroughs
# ---------------------------------------------------------------------------

class TestOverrides:
    def test_explicit_max_position_wins(self):
        """WHY: a user-stated per-name cap overrides the risk-tier default.
        BUG: ignoring an explicit user constraint. PHASE: P2/P1."""
        pol = s.InvestmentPolicy(risk_tolerance=s.RiskTolerance.HIGH, max_position_pct=12.0)
        params, _ = compile_policy(pol, _analytics())
        assert params.max_position_pct == 12.0  # not the HIGH default of 20

    def test_user_cash_floor_raises_min_cash(self):
        """WHY: an explicit min cash (EGP) becomes a % floor, max'd with horizon.
        BUG: dropping the user's cash request. PHASE: P1."""
        # total = 100,000; ask for 40,000 cash floor -> 40%
        pol = s.InvestmentPolicy(horizon=s.Horizon.Y1_3, min_cash_egp=40000.0)
        params, _ = compile_policy(pol, _analytics())
        assert params.min_cash_pct == pytest.approx(40.0)

    def test_exclusions_and_income_passthrough(self):
        """WHY: exclusions + income flag flow into params verbatim. PHASE: P1."""
        pol = s.InvestmentPolicy(income_preference=True,
                                 excluded_sectors=["banks"], excluded_tickers=["swdy"])
        params, _ = compile_policy(pol, _analytics())
        assert params.income_tilt is True
        assert params.excluded_sectors == ["BANKS"]
        assert params.excluded_tickers == ["SWDY.CA"]


# ---------------------------------------------------------------------------
# 5. Conflict detection
# ---------------------------------------------------------------------------

class TestConflicts:
    def test_concentration_vs_low_risk(self):
        """WHY: low risk + a concentrated book is a real tension to surface.
        BUG: silently optimizing around a contradiction. PHASE: P8 (policy flags)."""
        # COMI 50% + TMGH 20% -> HHI 0.29; push concentration up with a big COMI
        a = _analytics(cash=0, comi_shares=2000, tmgh_shares=200)  # COMI ~96%
        pol = s.InvestmentPolicy(risk_tolerance=s.RiskTolerance.LOW)
        _, flags = compile_policy(pol, a)
        codes = {f.code for f in flags}
        assert "CONCENTRATION_VS_RISK" in codes

    def test_short_horizon_vs_growth(self):
        """WHY: 6-month growth is hard on EGX; flag it. PHASE: P8."""
        pol = s.InvestmentPolicy(horizon=s.Horizon.LT_6M, objective=s.Objective.AGGRESSIVE_GROWTH)
        _, flags = compile_policy(pol, _analytics())
        assert "HORIZON_VS_OBJECTIVE" in {f.code for f in flags}

    def test_cash_floor_requires_selling(self):
        """WHY: a cash floor above current cash implies selling — disclose it.
        BUG: a surprise sell with no explanation. PHASE: P8."""
        # fully invested (cash 0), short horizon forces 60% cash floor
        a = _analytics(cash=0)
        pol = s.InvestmentPolicy(horizon=s.Horizon.LT_6M)
        _, flags = compile_policy(pol, a)
        assert "CASH_FLOOR_REQUIRES_SELLING" in {f.code for f in flags}

    def test_exclusion_hits_holding(self):
        """WHY: excluding a held name/sector implies a sell — disclose it. PHASE: P8."""
        pol = s.InvestmentPolicy(excluded_tickers=["COMI.CA"])
        _, flags = compile_policy(pol, _analytics())
        flag = next(f for f in flags if f.code == "EXCLUSION_HITS_HOLDING")
        assert "COMI.CA" in flag.data["tickers"]

    def test_no_false_conflicts_on_aligned_policy(self):
        """WHY: a consistent policy must not raise spurious flags. BUG: flag noise
        erodes trust. PHASE: P8."""
        # diversified-ish, medium risk, long horizon, no exclusions
        a = _analytics(cash=50000, comi_shares=500, tmgh_shares=2000)
        pol = s.InvestmentPolicy(risk_tolerance=s.RiskTolerance.MEDIUM, horizon=s.Horizon.GT_3Y)
        _, flags = compile_policy(pol, a)
        assert flags == []


# ---------------------------------------------------------------------------
# 6. Versioning & determinism
# ---------------------------------------------------------------------------

class TestVersioningDeterminism:
    def test_params_carry_versions(self):
        """WHY: params record compiler + policy version for audit reproducibility.
        BUG: a proposal you can't trace to the rules that made it. PHASE: P1/P4."""
        pol = s.InvestmentPolicy(version=3)
        params, _ = compile_policy(pol, _analytics())
        assert params.compiler_version == COMPILER_VERSION
        assert params.policy_version == 3

    def test_deterministic(self):
        """WHY: same policy+analytics → identical params+flags. PHASE: P1 gate."""
        pol = s.InvestmentPolicy(risk_tolerance=s.RiskTolerance.LOW, horizon=s.Horizon.LT_6M)
        a = _analytics()
        p1, f1 = compile_policy(pol, a)
        p2, f2 = compile_policy(pol, a)
        assert p1.model_dump() == p2.model_dump()
        assert [f.model_dump() for f in f1] == [f.model_dump() for f in f2]
