"""Tests for the scenario-tree operations (roadmap P1, Enhancement 2).

Verifies that each what-if op transforms the derived state correctly, that the
baseline is never mutated, policy overrides compose, promotion produces a clean
new baseline, and diffs compute the right metric deltas. Pure — no store, no LLM.
"""

from __future__ import annotations

import pytest

from tradingagents.portfolio import schemas as s
from tradingagents.portfolio import scenarios as sc


PRICES = {"COMI.CA": 50.0, "TMGH.CA": 10.0, "FWRY.CA": 12.0}


def _base() -> tuple[s.PortfolioSnapshot, s.InvestmentPolicy]:
    snap = s.PortfolioSnapshot(cash_egp=30000.0, holdings=[
        s.PortfolioHolding(ticker="COMI.CA", shares=1000, avg_cost=40),
        s.PortfolioHolding(ticker="TMGH.CA", shares=2000, avg_cost=12),
    ])
    return snap, s.InvestmentPolicy(version=1)


def _patch(*ops, reference="active", label="") -> s.ScenarioPatch:
    return s.ScenarioPatch(ops=list(ops), reference=reference, label=label)


# ---------------------------------------------------------------------------
# 1. apply_patch — structural ops
# ---------------------------------------------------------------------------

class TestApplyPatchStructural:
    def test_add_and_remove_cash(self):
        """WHY: cash ops adjust the derived cash. BUG: wrong cash invalidates the
        whole what-if. PHASE: P1."""
        snap, pol = _base()
        d, _ = sc.apply_patch(snap, pol, _patch(s.AddCashOp(amount_egp=50000)))
        assert d.cash_egp == 80000.0
        d2, _ = sc.apply_patch(snap, pol, _patch(s.RemoveCashOp(amount_egp=100000)))
        assert d2.cash_egp == 0.0  # floored

    def test_close_position_credits_proceeds(self):
        """WHY: 'sell all FWRY' removes the holding and adds its value to cash.
        BUG: losing the proceeds understates the derived portfolio. PHASE: P1."""
        snap, pol = _base()
        d, _ = sc.apply_patch(snap, pol, _patch(s.ClosePositionOp(ticker="TMGH.CA")), prices=PRICES)
        assert "TMGH.CA" not in {h.ticker for h in d.holdings}
        assert d.cash_egp == 30000.0 + 2000 * 10.0  # proceeds credited

    def test_scale_position(self):
        """WHY: 'increase TE exposure ×1.5' scales the holding. PHASE: P1."""
        snap, pol = _base()
        d, _ = sc.apply_patch(snap, pol, _patch(s.ScalePositionOp(ticker="COMI.CA", factor=1.5)))
        comi = next(h for h in d.holdings if h.ticker == "COMI.CA")
        assert comi.shares == 1500.0

    def test_baseline_is_not_mutated(self):
        """WHY: a what-if must never change the baseline (it's a fork). BUG: an
        in-place mutation corrupts the user's real portfolio. PHASE: P1/P8."""
        snap, pol = _base()
        original_cash = snap.cash_egp
        original_n = len(snap.holdings)
        sc.apply_patch(snap, pol, _patch(s.AddCashOp(amount_egp=1), s.ClosePositionOp(ticker="COMI.CA")), prices=PRICES)
        assert snap.cash_egp == original_cash and len(snap.holdings) == original_n


# ---------------------------------------------------------------------------
# 2. apply_patch — policy ops
# ---------------------------------------------------------------------------

class TestApplyPatchPolicy:
    def test_override_policy_coerces_enum(self):
        """WHY: 'go aggressive' overrides a policy field; the string coerces to the
        enum. BUG: a raw string breaks the compiler. PHASE: P1/P2."""
        snap, pol = _base()
        _, dp = sc.apply_patch(snap, pol, _patch(
            s.OverridePolicyOp(field="risk_tolerance", value="high")))
        assert dp is not None and dp.risk_tolerance == s.RiskTolerance.HIGH
        assert dp.version == pol.version + 1

    def test_exclude_ops_extend_policy(self):
        """WHY: exclusion what-ifs add to the derived policy. PHASE: P1."""
        snap, pol = _base()
        _, dp = sc.apply_patch(snap, pol, _patch(
            s.ExcludeTickerOp(ticker="COMI.CA"), s.ExcludeSectorOp(sector="REAL_ESTATE")))
        assert dp is not None
        assert "COMI.CA" in dp.excluded_tickers and "REAL_ESTATE" in dp.excluded_sectors

    def test_no_policy_op_returns_none_policy(self):
        """WHY: a purely structural patch leaves the policy unchanged (None).
        BUG: spuriously versioning the policy. PHASE: P1."""
        snap, pol = _base()
        _, dp = sc.apply_patch(snap, pol, _patch(s.AddCashOp(amount_egp=1)))
        assert dp is None

    def test_target_risk_delta_directive(self):
        """WHY: 'reduce risk 20%' is non-structural; surfaced as a vol multiplier
        directive (0.8). BUG: silently ignoring the request. PHASE: P1 (optimizer)."""
        patch = _patch(s.TargetRiskDeltaOp(vol_delta_pct=-20))
        assert sc.extract_directives(patch) == {"target_vol_mult": 0.8}


# ---------------------------------------------------------------------------
# 3. build_scenario / promote
# ---------------------------------------------------------------------------

class TestBuildPromote:
    def test_build_auto_labels(self):
        """WHY: an unlabeled patch gets a readable auto-label for the tabs. PHASE: P8."""
        snap, pol = _base()
        scen = sc.build_scenario(_patch(s.ClosePositionOp(ticker="TMGH.CA")), snap, pol, prices=PRICES)
        assert scen.label == "Sell all TMGH.CA"
        assert scen.status == s.ScenarioStatus.ACTIVE
        assert "TMGH.CA" not in {h.ticker for h in scen.derived_snapshot.holdings}

    def test_explicit_label_kept(self):
        snap, pol = _base()
        scen = sc.build_scenario(_patch(s.AddCashOp(amount_egp=1), label="my label"), snap, pol)
        assert scen.label == "my label"

    def test_promote_produces_clean_baseline(self):
        """WHY: adopting a scenario yields a confirmed baseline at the next version
        with provenance. BUG: a promoted baseline carrying a stale id/version.
        PHASE: P8 (adopt flow)."""
        snap, pol = _base()
        scen = sc.build_scenario(_patch(s.AddCashOp(amount_egp=50000)), snap, pol)
        scen.scenario_id = 7
        promoted = sc.promote_to_baseline(scen, version=4)
        assert promoted.version == 4 and promoted.snapshot_id is None
        assert promoted.confirmed_by_user is True and promoted.promoted_from_scenario == 7
        assert promoted.cash_egp == 80000.0


# ---------------------------------------------------------------------------
# 4. diff_proposals
# ---------------------------------------------------------------------------

class TestDiff:
    def _prop(self, vol, hhi, ret, weights):
        return s.OptimizationProposal(
            snapshot_id=1, policy_version=1, solver_status=s.SolverStatus.OPTIMAL,
            engine_version="0.1.0", expected_vol_after=vol, hhi_after=hhi,
            expected_return_view_annual=ret, target_weights=weights)

    def test_metric_deltas(self):
        """WHY: scenario_compare shows scenario−base deltas. BUG: wrong sign/value
        misleads the user about the hypothetical's effect. PHASE: P7/P8."""
        base = self._prop(0.25, 0.40, 0.08, {"COMI.CA": 60.0})       # 40% cash
        scen = self._prop(0.20, 0.30, 0.07, {"COMI.CA": 40.0})       # 60% cash
        cmp = sc.diff_proposals(base, scen, reference="baseline", scenario_label="Sell half COMI")
        assert cmp.metric_deltas["vol"] == pytest.approx(-0.05)
        assert cmp.metric_deltas["hhi"] == pytest.approx(-0.10)
        assert cmp.metric_deltas["expected_return_view"] == pytest.approx(-0.01)
        assert cmp.metric_deltas["cash_pct"] == pytest.approx(20.0)
        assert cmp.reference == "baseline" and cmp.scenario_label == "Sell half COMI"

    def test_diff_is_none_safe(self):
        """WHY: missing metrics must not crash the diff. PHASE: P7."""
        base = self._prop(None, 0.4, None, {"COMI.CA": 50.0})
        scen = self._prop(0.2, 0.3, None, {"COMI.CA": 40.0})
        cmp = sc.diff_proposals(base, scen)
        assert "vol" not in cmp.metric_deltas        # base vol was None
        assert cmp.metric_deltas["hhi"] == pytest.approx(-0.10)
