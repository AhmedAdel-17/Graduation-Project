"""Stress / production-hardening tests for the optimizer (audit).

Pre-production RC checks for a real-money tool: the EXECUTABLE plan must always be
fundable (no leverage), long-only, cash-non-negative, and self-consistent between
the rebalance actions and the reported after-metrics — even when EGX validation
clips trades. Offline, deterministic, no LLM/network.
"""

from __future__ import annotations

import numpy as np

from tradingagents.portfolio import schemas as s
from tradingagents.portfolio import optimizer as opt
from tradingagents.portfolio.analytics import compute_analytics
from tradingagents.portfolio.policy_compiler import compile_policy

PRICES = {"COMI.CA": 50.0, "TMGH.CA": 10.0, "FWRY.CA": 12.0, "ETEL.CA": 30.0, "ABUK.CA": 25.0}


def _snap(cash=30000.0):
    return s.PortfolioSnapshot(cash_egp=cash, holdings=[
        s.PortfolioHolding(ticker="COMI.CA", shares=1000, avg_cost=40),  # 50k
        s.PortfolioHolding(ticker="TMGH.CA", shares=2000, avg_cost=12),  # 20k
    ])


def _params(snap=None, **over):
    pol = s.InvestmentPolicy(**over)
    params, _ = compile_policy(pol, compute_analytics(snap or _snap(), PRICES))
    return params


def _run(*, snap=None, candidates=("FWRY.CA", "ETEL.CA", "ABUK.CA"), views=None, adv=None, params=None, **kw):
    snap = snap or _snap()
    return opt.optimize(snap, PRICES, params or _params(snap), candidate_tickers=candidates,
                        views=views, adv=adv, snapshot_id=1, **kw)


def _buy_cost(prop):
    return sum(a.est_value_egp for a in prop.actions if a.side == s.TradeSide.BUY)


def _sell_proceeds(prop):
    return sum(a.est_value_egp for a in prop.actions if a.side == s.TradeSide.SELL)


# ---------------------------------------------------------------------------
# No-leverage / cash invariants (must hold on EVERY proposal)
# ---------------------------------------------------------------------------

class TestNoLeverageInvariant:
    def test_buys_never_exceed_cash_plus_proceeds(self):
        """WHY: EGX has no leverage — the executable plan must always be fundable.
        Strong bullish views on candidates try to deploy aggressively. PHASE: audit F1."""
        views = {"FWRY.CA": (0.9, 0.9), "ETEL.CA": (0.8, 0.9), "ABUK.CA": (0.85, 0.9)}
        for cash in (0.0, 5000.0, 30000.0, 200000.0):
            prop = _run(snap=_snap(cash=cash), views=views)
            analytics = compute_analytics(_snap(cash=cash), PRICES)
            budget = analytics.cash_egp + _sell_proceeds(prop)
            assert _buy_cost(prop) <= budget + 1e-6, f"leverage at cash={cash}"

    def test_realized_invested_never_exceeds_total_value(self):
        """WHY: realized weights must keep cash >= 0 (sum of weights <= 100%)."""
        prop = _run(views={"FWRY.CA": (0.9, 0.9), "ETEL.CA": (0.9, 0.9)})
        assert sum(prop.target_weights.values()) <= 100.0 + 1e-6

    def test_long_only_no_negative_realized_weight(self):
        prop = _run(views={"COMI.CA": (-0.9, 0.9)})  # bearish on a holding
        assert all(w >= -1e-6 for w in prop.target_weights.values())


# ---------------------------------------------------------------------------
# F2 — reported after-metrics match the executable (clipped) plan
# ---------------------------------------------------------------------------

class TestRealizedMetricsConsistency:
    def test_adv_clip_keeps_realized_above_continuous_target(self):
        """WHY: if EGX clips the SELL that would trim an over-cap name, the name
        stays bigger than the continuous optimum — and the REPORTED after-weight
        must reflect that reality, not the unachievable target. PHASE: audit F2."""
        params = _params(risk_tolerance=s.RiskTolerance.MEDIUM)  # 15% cap → big COMI sell
        # ADV so small that the COMI trim is heavily clipped.
        prop = _run(params=params, adv={"COMI.CA": 1000.0}, candidates=())
        cont = prop.inputs_audit["target_weights_continuous"]["COMI.CA"]
        realized = prop.target_weights["COMI.CA"]
        assert "ADV_CLIP" in {f.code for f in prop.policy_flags}
        assert realized > cont + 1.0  # realized COMI materially exceeds the 15% target
        # and the reported after-HHI is computed from the realized (bigger) book
        assert prop.hhi_after >= (realized / 100.0) ** 2 - 1e-9

    def test_target_weights_reconstruct_from_actions(self):
        """WHY: target_weights must equal current + net traded shares (no drift)."""
        prop = _run(views={"FWRY.CA": (0.7, 0.8)})
        total = compute_analytics(_snap(), PRICES).total_value_egp
        cur_shares = {"COMI.CA": 1000.0, "TMGH.CA": 2000.0}
        realized = {t: cur_shares.get(t, 0.0) for t in prop.target_weights}
        for a in prop.actions:
            realized[a.ticker] += a.shares if a.side == s.TradeSide.BUY else -a.shares
        for t, w in prop.target_weights.items():
            expected = realized[t] * PRICES[t] / total * 100.0
            assert abs(w - expected) < 1e-6, t


# ---------------------------------------------------------------------------
# F4 — heuristic fallback honors the evidence views
# ---------------------------------------------------------------------------

class TestHeuristicUsesViews:
    def test_bearish_view_exits_and_bullish_view_gets_budget(self):
        universe = ["COMI.CA", "FWRY.CA", "TMGH.CA"]
        w0 = np.array([0.30, 0.0, 0.20])  # 30% COMI, 20% TMGH, 50% cash
        params = _params(risk_tolerance=s.RiskTolerance.HIGH)  # 20% cap, 0 cash floor
        views = {"COMI.CA": (-0.6, 0.8), "FWRY.CA": (0.7, 0.8)}  # exit COMI, buy FWRY
        w = opt._heuristic(universe, w0, {}, params, opt.ticker_sector, views=views)
        i = {t: k for k, t in enumerate(universe)}
        assert w[i["COMI.CA"]] == 0.0          # strongly bearish → exited
        assert w[i["FWRY.CA"]] > 0.0           # bullish → funded from freed budget
        assert w.sum() <= (1.0 - params.min_cash_pct / 100.0) + 1e-9


# ---------------------------------------------------------------------------
# Graceful degradation + determinism under stress
# ---------------------------------------------------------------------------

class TestDegradationAndDeterminism:
    def test_infeasible_vol_target_relaxes_and_stays_valid(self):
        """WHY: an unachievable vol target must relax + disclose, never crash or
        return an invalid book. PHASE: audit (graceful degradation)."""
        params = _params(objective=s.Objective.CAPITAL_PRESERVATION,
                         risk_tolerance=s.RiskTolerance.VERY_LOW)
        params = params.model_copy(update={"vol_target": 0.001})  # impossibly tight
        prop = _run(params=params, candidates=())
        assert prop.solver_status in (s.SolverStatus.INFEASIBLE_RELAXED,
                                      s.SolverStatus.OPTIMAL, s.SolverStatus.HEURISTIC_FALLBACK)
        assert all(w >= -1e-6 for w in prop.target_weights.values())
        assert sum(prop.target_weights.values()) <= 100.0 + 1e-6

    def test_determinism_byte_identical(self):
        """WHY: same inputs → identical proposal (audit reproducibility). The only
        non-deterministic field is the wall-clock created_at; everything the user
        and auditor rely on must match exactly. PHASE: audit (determinism)."""
        views = {"FWRY.CA": (0.5, 0.7), "ETEL.CA": (-0.4, 0.6)}
        a = _run(views=views).model_dump(mode="json")
        b = _run(views=views).model_dump(mode="json")
        a.pop("created_at", None)
        b.pop("created_at", None)
        assert a == b
