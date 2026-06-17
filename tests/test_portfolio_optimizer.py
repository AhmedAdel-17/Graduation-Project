"""Tests for the optimization engine (roadmap P1).

The optimizer is the riskiest deterministic component, so these tests pin the
invariants that make a proposal safe and reproducible: long-only, caps respected,
cash floor honored, exclusions zeroed, signals actually move allocation in the
right direction, determinism, vol targeting, discretization (integer lots, T+2
sell-before-buy ordering), and the heuristic fallback. Offline — injected
covariance, no network, no LLM.
"""

from __future__ import annotations

import numpy as np
import pytest

from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.analytics import compute_analytics
from tradingagents.portfolio.policy_compiler import compile_policy
from tradingagents.portfolio import optimizer as opt


PRICES = {"COMI.CA": 50.0, "TMGH.CA": 10.0, "FWRY.CA": 12.0}


def _snapshot() -> s.PortfolioSnapshot:
    return s.PortfolioSnapshot(cash_egp=30000.0, holdings=[
        s.PortfolioHolding(ticker="COMI.CA", shares=1000, avg_cost=40),  # 50%
        s.PortfolioHolding(ticker="TMGH.CA", shares=2000, avg_cost=12),  # 20%
    ])


def _params(**over) -> s.OptimizerParams:
    pol = s.InvestmentPolicy(**over)
    params, _ = compile_policy(pol, compute_analytics(_snapshot(), PRICES))
    return params


def _optimize(signals=None, candidates=("FWRY.CA",), params=None, **kw):
    return opt.optimize(
        _snapshot(), PRICES, params or _params(), signals=signals or {},
        candidate_tickers=candidates, snapshot_id=1, **kw)


# ---------------------------------------------------------------------------
# 1. Hard invariants (long-only, caps, cash floor, exclusions)
# ---------------------------------------------------------------------------

class TestInvariants:
    def test_long_only(self):
        """WHY: EGX is long-only (no shorts). BUG: a negative target weight is an
        illegal short. PHASE: P1 (regulatory), feeds egx_validation."""
        prop = _optimize()
        assert all(w >= -1e-6 for w in prop.target_weights.values())

    def test_per_name_cap_respected(self):
        """WHY: the compiled per-name cap is the concentration control. BUG: a
        weight above the cap defeats the user's risk preference. PHASE: P1."""
        params = _params(risk_tolerance=s.RiskTolerance.MEDIUM)  # 15% cap
        prop = _optimize(params=params)
        assert all(w <= params.max_position_pct + 1e-6 for w in prop.target_weights.values())

    def test_cash_floor_respected(self):
        """WHY: short horizons force a cash floor; invested must stay under it.
        BUG: violating the floor on a 6-month goal is the headline failure case.
        PHASE: P1."""
        params = _params(horizon=s.Horizon.LT_6M)  # 60% cash floor
        prop = _optimize(params=params)
        invested = sum(prop.target_weights.values()) / 100.0
        assert invested <= (1.0 - params.min_cash_pct / 100.0) + 1e-6

    def test_excluded_ticker_is_zeroed(self):
        """WHY: a hard exclusion must produce zero weight + no buy. BUG: buying an
        excluded name ignores the user. PHASE: P1/P2."""
        params = _params(excluded_tickers=["COMI.CA"])
        prop = _optimize(params=params)
        assert prop.target_weights["COMI.CA"] == pytest.approx(0.0, abs=1e-6)
        assert all(not (a.ticker == "COMI.CA" and a.side == s.TradeSide.BUY) for a in prop.actions)

    def test_excluded_sector_is_zeroed(self):
        """WHY: sector exclusion zeroes every name in it. BUG: residual exposure to
        an excluded sector. PHASE: P1."""
        from tradingagents.dataflows.social_v2.sectors import ticker_sector
        params = _params(excluded_sectors=[ticker_sector("TMGH.CA")])  # REAL_ESTATE
        prop = _optimize(params=params)
        assert prop.target_weights["TMGH.CA"] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 2. Signals move allocation in the right direction
# ---------------------------------------------------------------------------

class TestSignalsDirection:
    def test_buy_signal_increases_weight(self):
        """WHY: a BUY view must raise a name's weight vs no view. BUG: signals not
        flowing into allocation makes the whole agent stack pointless here.
        PHASE: P1 (BL views)."""
        no_sig = _optimize(signals={})
        buy = _optimize(signals={"FWRY.CA": s.SignalView(
            ticker="FWRY.CA", label=s.SignalLabel.BUY, confidence=0.8)})
        assert buy.target_weights["FWRY.CA"] > no_sig.target_weights["FWRY.CA"] + 1e-6

    def test_sell_signal_decreases_weight(self):
        """WHY: a SELL view must lower a held name's weight vs no view. PHASE: P1."""
        no_sig = _optimize(signals={})
        sell = _optimize(signals={"TMGH.CA": s.SignalView(
            ticker="TMGH.CA", label=s.SignalLabel.SELL, confidence=0.9)})
        assert sell.target_weights["TMGH.CA"] < no_sig.target_weights["TMGH.CA"] - 1e-6

    def test_higher_confidence_moves_more(self):
        """WHY: view magnitude scales with confidence. BUG: ignoring confidence
        flattens the signal. PHASE: P1."""
        low = _optimize(signals={"FWRY.CA": s.SignalView(
            ticker="FWRY.CA", label=s.SignalLabel.BUY, confidence=0.3)})
        high = _optimize(signals={"FWRY.CA": s.SignalView(
            ticker="FWRY.CA", label=s.SignalLabel.BUY, confidence=0.95)})
        assert high.target_weights["FWRY.CA"] >= low.target_weights["FWRY.CA"]


# ---------------------------------------------------------------------------
# 2b. Action rationale explains the real driver (not a 0.00-conf HOLD)
# ---------------------------------------------------------------------------

class TestActionRationale:
    def test_neutral_signals_give_risk_profile_rationale(self):
        """WHY: with no actionable signal the trade is construction-driven; the chip
        must say so, not blame a "HOLD signal (conf 0.00)". BUG: a misleading verb
        with no reason erodes trust. PHASE: P2 explainability."""
        prop = _optimize(signals={})  # all neutral → conf 0.00
        assert prop.actions, "expected trades"
        for a in prop.actions:
            assert a.rationale and "HOLD signal" not in a.rationale
            assert "risk profile" in a.rationale
            if a.side == s.TradeSide.SELL:
                assert "concentration" in a.rationale
            else:
                assert "diversify" in a.rationale

    def test_actionable_signal_is_named_in_rationale(self):
        """WHY: a real (high-confidence) BUY/SELL view should be cited as the driver
        with its confidence. PHASE: P2."""
        sig = {"FWRY.CA": s.SignalView(ticker="FWRY.CA", label=s.SignalLabel.BUY, confidence=0.8)}
        prop = _optimize(signals=sig)
        fwry = [a for a in prop.actions if a.ticker == "FWRY.CA"]
        assert fwry and "bullish agent signal" in fwry[0].rationale
        assert "80%" in fwry[0].rationale

    def test_evidence_view_factors_named_in_rationale(self):
        """WHY: a recommendation must cite its EVIDENCE (value/quality/momentum) +
        confidence + the Black-Litterman framing — this is the defense answer to
        "based on what?". PHASE: P3 (explainability chain)."""
        views = {"FWRY.CA": (0.7, 0.8)}
        prov = {"FWRY.CA": {"score": 0.7, "confidence": 0.8, "evidence": [
            {"source": "value", "z": 1.2, "score": 0.6, "weight": 0.35},
            {"source": "momentum", "z": 1.0, "score": 0.5, "weight": 0.25}]}}
        prop = opt.optimize(_snapshot(), PRICES, _params(), views=views, view_provenance=prov,
                            candidate_tickers=("FWRY.CA",), snapshot_id=1)
        fwry = [a for a in prop.actions if a.ticker == "FWRY.CA"]
        assert fwry, "expected a FWRY trade"
        r = fwry[0].rationale
        assert "Black-Litterman" in r and "value" in r and "confidence 80%" in r


# ---------------------------------------------------------------------------
# 3. Determinism & audit
# ---------------------------------------------------------------------------

class TestDeterminismAudit:
    def test_deterministic_excluding_timestamp(self):
        """WHY: same inputs → identical proposal (modulo the created_at stamp).
        BUG: a nondeterministic optimizer breaks the audit trail. PHASE: P1 gate."""
        sigs = {"FWRY.CA": s.SignalView(ticker="FWRY.CA", label=s.SignalLabel.BUY, confidence=0.8)}
        p1 = _optimize(signals=sigs)
        p2 = _optimize(signals=sigs)
        assert p1.model_dump(exclude={"created_at"}) == p2.model_dump(exclude={"created_at"})

    def test_audit_blob_is_complete(self):
        """WHY: a proposal must be reproducible from its audit blob. BUG: a thin
        audit can't be reconstructed for compliance. PHASE: P4 (audit view)."""
        prop = _optimize()
        a = prop.inputs_audit
        assert set(a) >= {"prices", "params", "signals", "covariance_hash", "universe"}
        assert prop.engine_version and prop.policy_version == _params().policy_version

    def test_status_optimal_on_feasible(self):
        """WHY: the normal path solves to optimal (cash is always a feasible
        escape, so it should never silently fall back). PHASE: P1."""
        assert _optimize().solver_status == s.SolverStatus.OPTIMAL


# ---------------------------------------------------------------------------
# 4. Vol targeting
# ---------------------------------------------------------------------------

class TestVolTarget:
    def test_vol_target_binds(self):
        """WHY: capital preservation caps portfolio vol; the result must honor it.
        BUG: a 'safest possible' request that ignores vol. PHASE: P1."""
        pol = s.InvestmentPolicy(objective=s.Objective.CAPITAL_PRESERVATION,
                                 risk_tolerance=s.RiskTolerance.LOW)
        params, _ = compile_policy(pol, compute_analytics(_snapshot(), PRICES), benchmark_vol=0.30)
        assert params.vol_target is not None
        prop = _optimize(params=params)
        assert prop.expected_vol_after <= params.vol_target + 1e-3


# ---------------------------------------------------------------------------
# 5. Discretization (integer lots, T+2 ordering)
# ---------------------------------------------------------------------------

class TestDiscretization:
    def test_integer_shares_and_value(self):
        """WHY: trades are whole shares and value = shares×price. BUG: fractional
        shares can't be placed; wrong value misleads. PHASE: P1/P7."""
        prop = _optimize(signals={"FWRY.CA": s.SignalView(
            ticker="FWRY.CA", label=s.SignalLabel.BUY, confidence=0.8)})
        for a in prop.actions:
            assert isinstance(a.shares, int) and a.shares > 0
            assert a.est_value_egp == pytest.approx(a.shares * a.price_used)

    def test_sells_ordered_before_buys(self):
        """WHY: T+2 settlement — sells must free cash before buys spend it. BUG:
        buy-before-sell assumes cash the user doesn't have yet. PHASE: P1."""
        prop = _optimize(signals={
            "TMGH.CA": s.SignalView(ticker="TMGH.CA", label=s.SignalLabel.SELL, confidence=0.9),
            "FWRY.CA": s.SignalView(ticker="FWRY.CA", label=s.SignalLabel.BUY, confidence=0.8)})
        sides = [a.side for a in prop.actions]
        last_sell = max((i for i, x in enumerate(sides) if x == s.TradeSide.SELL), default=-1)
        first_buy = min((i for i, x in enumerate(sides) if x == s.TradeSide.BUY), default=len(sides))
        assert last_sell < first_buy

    def test_lot_size_rounding(self):
        """WHY: board lots — share counts are multiples of lot_size. BUG: an
        unfillable odd lot. PHASE: P1."""
        prop = opt.optimize(_snapshot(), PRICES, _params(), candidate_tickers=["FWRY.CA"],
                            signals={"FWRY.CA": s.SignalView(ticker="FWRY.CA",
                                     label=s.SignalLabel.BUY, confidence=0.8)},
                            lot_size=100, snapshot_id=1)
        for a in prop.actions:
            assert a.shares % 100 == 0


# ---------------------------------------------------------------------------
# 6. Heuristic fallback
# ---------------------------------------------------------------------------

class TestHeuristicFallback:
    def test_heuristic_is_valid_long_only_plan(self):
        """WHY: when cvxpy is unavailable, the fallback must still be long-only +
        within caps + under the cash floor. BUG: an invalid fallback proposal.
        PHASE: P1 (degradation)."""
        universe = ["COMI.CA", "TMGH.CA", "FWRY.CA"]
        w0 = np.array([0.5, 0.2, 0.0])
        params = _params(risk_tolerance=s.RiskTolerance.LOW, horizon=s.Horizon.M6_12)
        from tradingagents.dataflows.social_v2.sectors import ticker_sector
        sigs = {"TMGH.CA": s.SignalView(ticker="TMGH.CA", label=s.SignalLabel.SELL, confidence=0.8),
                "FWRY.CA": s.SignalView(ticker="FWRY.CA", label=s.SignalLabel.BUY, confidence=0.9)}
        w = opt._heuristic(universe, w0, sigs, params, ticker_sector)
        cap = params.max_position_pct / 100.0
        assert (w >= -1e-9).all()
        assert (w <= cap + 1e-9).all()
        assert w.sum() <= (1.0 - params.min_cash_pct / 100.0) + 1e-9
        assert w[1] == pytest.approx(0.0)  # TMGH SELL -> zeroed

    def test_optimize_uses_fallback_when_solver_unavailable(self, monkeypatch):
        """WHY: optimize() must degrade to the heuristic + flag it, not crash.
        BUG: a hard failure when the solver is missing. PHASE: P1."""
        monkeypatch.setattr(opt, "_solve_mvo", lambda *a, **k: (None, s.SolverStatus.HEURISTIC_FALLBACK))
        prop = _optimize(signals={"FWRY.CA": s.SignalView(
            ticker="FWRY.CA", label=s.SignalLabel.BUY, confidence=0.8)})
        assert prop.solver_status == s.SolverStatus.HEURISTIC_FALLBACK
        assert all(w >= -1e-6 for w in prop.target_weights.values())


# ---------------------------------------------------------------------------
# 6. Phase-1: CAPM-equilibrium prior + candidate universe (the "real reasons")
# ---------------------------------------------------------------------------

class TestEquilibriumPriorAndCandidates:
    def test_market_weights_tilt_prior_toward_market(self):
        """WHY: with NO views, the BL prior π=δΣw_mkt must pull the no-view
        optimum toward the market-cap portfolio (He-Litterman 1999), not just
        sit at the current book. BUG: the equilibrium prior is ignored, so
        'what should I buy' can't surface an under-owned market leader. PHASE: P1."""
        # FWRY is a candidate the user does not hold; give it the dominant
        # market-cap weight. It should attract more weight than with the
        # default (current-weights) prior, which would leave it near zero.
        mw = {"COMI.CA": 0.10, "TMGH.CA": 0.10, "FWRY.CA": 0.80}
        without = _optimize()
        with_mw = _optimize(market_weights=mw)
        assert with_mw.target_weights["FWRY.CA"] > without.target_weights["FWRY.CA"] + 1e-3
        assert with_mw.inputs_audit["prior_source"] == "market_cap_equilibrium"
        assert without.inputs_audit["prior_source"] == "current_weights"

    def test_candidate_can_become_a_new_position(self):
        """WHY: the opt-in candidate universe is what makes 'recommend new EGX
        names' possible. BUG: a strong BUY view on an unheld candidate produces
        no buy → the feature is dead. PHASE: P1 (user-controlled universe)."""
        sigs = {"FWRY.CA": s.SignalView(ticker="FWRY.CA", label=s.SignalLabel.BUY, confidence=0.9)}
        prop = _optimize(signals=sigs)  # FWRY is a candidate, not a holding
        assert prop.target_weights["FWRY.CA"] > 0.0
        assert any(a.ticker == "FWRY.CA" and a.side == s.TradeSide.BUY for a in prop.actions)

    def test_deterministic_with_market_weights(self):
        """WHY: the equilibrium prior must not break reproducibility. PHASE: P1 gate."""
        mw = {"COMI.CA": 0.5, "TMGH.CA": 0.3, "FWRY.CA": 0.2}
        p1 = _optimize(market_weights=mw)
        p2 = _optimize(market_weights=mw)
        assert p1.model_dump(exclude={"created_at"}) == p2.model_dump(exclude={"created_at"})

    def test_audit_records_covariance_and_prior_provenance(self):
        """WHY: a defensible proposal must say which risk model / prior produced
        it. BUG: silent diagonal-fallback masquerading as a real risk model.
        PHASE: P1 (explainability)."""
        prop = _optimize()  # no covariance passed → diagonal fallback
        assert prop.inputs_audit["covariance_source"] == "diagonal_fallback"
        assert prop.inputs_audit["prior_source"] == "current_weights"


# ---------------------------------------------------------------------------
# 7. Phase-2: Black-Litterman views (evidence → posterior → weight)
# ---------------------------------------------------------------------------

class TestBlackLittermanViews:
    def test_positive_view_raises_weight(self):
        """WHY: a positive evidence view on a candidate must raise its posterior
        weight vs no view. BUG: views that don't move allocation are decorative.
        CITE: Black & Litterman 1992. PHASE: P2."""
        base = _optimize()  # no views
        with_view = _optimize(views={"FWRY.CA": (0.9, 0.8)})
        assert with_view.target_weights["FWRY.CA"] > base.target_weights["FWRY.CA"] + 1e-4
        assert with_view.inputs_audit["views_source"] == "evidence_engine"

    def test_idzorek_confidence_monotonic(self):
        """WHY: higher view confidence ⇒ Ω smaller ⇒ the view binds harder ⇒ a
        bigger weight move. BUG: confidence that doesn't scale conviction.
        CITE: Idzorek 2005. PHASE: P2."""
        low = _optimize(views={"FWRY.CA": (0.9, 0.2)})
        high = _optimize(views={"FWRY.CA": (0.9, 0.9)})
        assert high.target_weights["FWRY.CA"] > low.target_weights["FWRY.CA"] + 1e-4

    def test_negative_view_reduces_weight(self):
        """WHY: a bearish evidence view must pull a held name's weight down vs the
        no-view prior. PHASE: P2."""
        base = _optimize()
        bearish = _optimize(views={"COMI.CA": (-0.9, 0.8)})
        assert bearish.target_weights["COMI.CA"] < base.target_weights["COMI.CA"] + 1e-9

    def test_full_shrinkage_ignores_views(self):
        """WHY: the policy compiler's horizon shrinkage (→1 at <6m) must let the
        optimizer distrust views and sit at the equilibrium prior. PHASE: P2."""
        params = _params(horizon=s.Horizon.LT_6M)  # heavy view shrinkage
        shrunk = params.model_copy(update={"view_shrinkage": 1.0, "min_cash_pct": 0.0})
        no_view = opt.optimize(_snapshot(), PRICES, shrunk, candidate_tickers=("FWRY.CA",),
                               snapshot_id=1)
        with_view = opt.optimize(_snapshot(), PRICES, shrunk, views={"FWRY.CA": (0.9, 0.9)},
                                 candidate_tickers=("FWRY.CA",), snapshot_id=1)
        assert with_view.target_weights["FWRY.CA"] == pytest.approx(
            no_view.target_weights["FWRY.CA"], abs=1e-6)

    def test_views_deterministic(self):
        """WHY: the BL posterior must be reproducible (audit). PHASE: P2 gate."""
        p1 = _optimize(views={"FWRY.CA": (0.7, 0.6), "COMI.CA": (-0.3, 0.5)})
        p2 = _optimize(views={"FWRY.CA": (0.7, 0.6), "COMI.CA": (-0.3, 0.5)})
        assert p1.model_dump(exclude={"created_at"}) == p2.model_dump(exclude={"created_at"})
