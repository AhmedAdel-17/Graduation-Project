"""Golden + property tests for the portfolio analytics engine (roadmap P1).

Every number the user eventually sees originates here, so these tests pin exact
values on frozen prices, prove the two input representations (shares vs
weight+total) converge, and verify the risk math + honest failure modes.

WHY/BUG/PHASE noted per test. Pure arithmetic — no LLM, no network.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingagents.dataflows.social_v2.sectors import ticker_sector
from tradingagents.portfolio.analytics import compute_analytics
from tradingagents.portfolio import schemas as s


# ---------------------------------------------------------------------------
# Fixtures: a frozen golden portfolio
# ---------------------------------------------------------------------------

PRICES = {"COMI.CA": 50.0, "TMGH.CA": 10.0}


def _shares_snapshot() -> s.PortfolioSnapshot:
    # COMI: 1000sh @50 (cost 40) = 50,000 ; TMGH: 2000sh @10 (cost 12) = 20,000
    # cash 30,000 -> invested 70,000, total 100,000
    return s.PortfolioSnapshot(cash_egp=30000.0, holdings=[
        s.PortfolioHolding(ticker="COMI.CA", shares=1000, avg_cost=40),
        s.PortfolioHolding(ticker="TMGH.CA", shares=2000, avg_cost=12),
    ])


# ---------------------------------------------------------------------------
# 1. Absolute (shares-based) golden numbers
# ---------------------------------------------------------------------------

class TestAbsoluteGolden:
    def test_values_weights_pnl(self):
        """WHY: pins the core arithmetic (market value, total, weights, P&L).
        BUG: an off-by-one in valuation propagates to every chart and the
        optimizer. PHASE: P1 (optimizer reads these), P7 (donut/table)."""
        a = compute_analytics(_shares_snapshot(), PRICES)
        assert a.total_value_egp == 100000.0
        assert a.invested_egp == 70000.0
        assert a.cash_egp == 30000.0
        assert a.cash_drag_pct == 30.0
        byt = {h.ticker: h for h in a.holdings}
        assert byt["COMI.CA"].market_value_egp == 50000.0
        assert byt["COMI.CA"].weight_pct == 50.0
        assert byt["COMI.CA"].unrealized_pnl_egp == 10000.0   # (50-40)*1000
        assert byt["COMI.CA"].unrealized_pnl_pct == 25.0
        assert byt["TMGH.CA"].market_value_egp == 20000.0
        assert byt["TMGH.CA"].weight_pct == 20.0
        assert byt["TMGH.CA"].unrealized_pnl_egp == -4000.0   # (10-12)*2000
        assert round(byt["TMGH.CA"].unrealized_pnl_pct, 4) == round(-100/6, 4)

    def test_hhi_excludes_cash_term(self):
        """WHY: HHI = 0.5²+0.2² = 0.29 (cash not a term). BUG: including cash as
        a term, or weighting over invested not total, gives a wrong concentration
        number on the risk panel. PHASE: P7 (RiskPanel), P1 (compiler conflicts)."""
        a = compute_analytics(_shares_snapshot(), PRICES)
        assert round(a.hhi, 6) == round(0.5**2 + 0.2**2, 6) == 0.29

    def test_sector_and_index_exposure(self):
        """WHY: exposures group weights by the shared taxonomy. BUG: wrong
        grouping mis-states sector/index risk. PHASE: P7 (treemap), P1 (sector cap)."""
        a = compute_analytics(_shares_snapshot(), PRICES)
        # sector totals equal the invested % of total (70%)
        assert round(sum(a.sector_exposure.values()), 6) == 70.0
        assert a.sector_exposure[ticker_sector("TMGH.CA")] == 20.0
        assert a.sector_exposure[ticker_sector("COMI.CA")] == 50.0
        # EGX100 ⊇ EGX30 ∪ EGX70, so its exposure dominates each
        ie = a.index_exposure
        if "EGX100" in ie:
            assert ie["EGX100"] >= ie.get("EGX30", 0.0)
            assert ie["EGX100"] >= ie.get("EGX70", 0.0)


# ---------------------------------------------------------------------------
# 2. Representation equivalence
# ---------------------------------------------------------------------------

class TestRepresentationEquivalence:
    def test_weight_plus_total_matches_shares(self):
        """WHY: extraction may yield weights+total OR shares; both must produce
        identical analytics. BUG: a divergence means the same portfolio analyzes
        differently depending on how the user phrased it. PHASE: P2 (extraction)."""
        shares_a = compute_analytics(_shares_snapshot(), PRICES)
        # same portfolio expressed as % of invested (70,000) + the same total
        weight_snap = s.PortfolioSnapshot(cash_egp=30000.0, holdings=[
            s.PortfolioHolding(ticker="COMI.CA", weight_pct=50000/70000*100, avg_cost=40),
            s.PortfolioHolding(ticker="TMGH.CA", weight_pct=20000/70000*100, avg_cost=12),
        ])
        weight_a = compute_analytics(weight_snap, PRICES, total_invested_egp=70000.0)
        assert weight_a.total_value_egp == shares_a.total_value_egp
        assert weight_a.weights == pytest.approx(shares_a.weights)
        assert round(weight_a.hhi, 9) == round(shares_a.hhi, 9)
        by_s = {h.ticker: h for h in shares_a.holdings}
        by_w = {h.ticker: h for h in weight_a.holdings}
        for t in PRICES:
            assert by_w[t].market_value_egp == pytest.approx(by_s[t].market_value_egp)
            assert by_w[t].shares == pytest.approx(by_s[t].shares)


# ---------------------------------------------------------------------------
# 3. Honest failure modes
# ---------------------------------------------------------------------------

class TestFailureModes:
    def test_missing_anchor_raises(self):
        """WHY: a weight-only holding with no total can't be valued in EGP.
        BUG: silently inventing EGP values would mislead. PHASE: P2 (must prompt
        for shares/total before analytics runs)."""
        snap = s.PortfolioSnapshot(cash_egp=1000, holdings=[
            s.PortfolioHolding(ticker="COMI.CA", weight_pct=50)])
        with pytest.raises(ValueError, match="absolute anchor"):
            compute_analytics(snap, PRICES)

    def test_missing_price_raises(self):
        """WHY: can't value a holding with no price. PHASE: P1/P3."""
        snap = s.PortfolioSnapshot(cash_egp=0, holdings=[
            s.PortfolioHolding(ticker="ZZZZ.CA", shares=10)])
        with pytest.raises(ValueError, match="no price"):
            compute_analytics(snap, PRICES)

    def test_nonpositive_price_raises(self):
        """WHY: a zero/negative price is bad data, not a valuation. PHASE: P1."""
        snap = s.PortfolioSnapshot(cash_egp=0, holdings=[
            s.PortfolioHolding(ticker="COMI.CA", shares=10)])
        with pytest.raises(ValueError, match="non-positive price"):
            compute_analytics(snap, {"COMI.CA": 0.0})


# ---------------------------------------------------------------------------
# 4. Properties: determinism, cash dilution, signal attach
# ---------------------------------------------------------------------------

class TestProperties:
    def test_determinism(self):
        """WHY: audit reproducibility — same inputs → identical output. BUG:
        any nondeterminism breaks the audit trail. PHASE: P1 (determinism gate)."""
        a1 = compute_analytics(_shares_snapshot(), PRICES)
        a2 = compute_analytics(_shares_snapshot(), PRICES)
        assert a1.model_dump() == a2.model_dump()

    def test_more_cash_lowers_hhi(self):
        """WHY: cash should dilute concentration (it isn't concentration risk).
        BUG: HHI rising with cash would misrepresent risk. PHASE: P1/P7."""
        base = compute_analytics(_shares_snapshot(), PRICES)
        cashier = _shares_snapshot()
        cashier.cash_egp = 200000.0
        more_cash = compute_analytics(cashier, PRICES)
        assert more_cash.hhi < base.hhi

    def test_signal_attached(self):
        """WHY: per-holding signals ride along for the freshness chip + treemap
        tone. BUG: a dropped signal loses provenance. PHASE: P7."""
        sig = {"COMI.CA": s.SignalView(ticker="COMI.CA", label=s.SignalLabel.BUY, confidence=0.7)}
        a = compute_analytics(_shares_snapshot(), PRICES, signals=sig)
        byt = {h.ticker: h for h in a.holdings}
        assert byt["COMI.CA"].signal is not None and byt["COMI.CA"].signal.label == s.SignalLabel.BUY
        assert byt["TMGH.CA"].signal is None


# ---------------------------------------------------------------------------
# 5. Risk metrics (beta / vol) from returns
# ---------------------------------------------------------------------------

def _synthetic_returns(n: int = 120, seed: int = 0):
    rng = np.random.default_rng(seed)
    bench = pd.Series(rng.normal(0, 0.01, n), index=pd.RangeIndex(n))
    # COMI moves exactly with the benchmark (beta 1); TMGH at half (beta 0.5)
    df = pd.DataFrame({"COMI.CA": bench.to_numpy(), "TMGH.CA": 0.5 * bench.to_numpy()},
                      index=bench.index)
    return df, bench


class TestRiskMetrics:
    def test_portfolio_beta_is_weighted(self):
        """WHY: portfolio beta = Σ total_weight·beta_i (cash beta 0). With COMI
        β=1 (w=0.5), TMGH β=0.5 (w=0.2) → 0.6. BUG: weighting over invested not
        total, or mishandling cash, gives the wrong beta. PHASE: P1 (compiler vol
        rules), P7 (RiskPanel)."""
        df, bench = _synthetic_returns()
        a = compute_analytics(_shares_snapshot(), PRICES, returns=df, benchmark_returns=bench)
        assert a.portfolio_beta == pytest.approx(0.5 * 1.0 + 0.2 * 0.5, abs=1e-6)
        assert a.annual_vol is not None and a.annual_vol > 0
        assert a.min_history_excluded == []

    def test_beta_is_proxy_flag_only_when_beta_present(self):
        """WHY: the proxy-β flag must not be set when no β was computed (finding
        #3 honesty). BUG: a dangling proxy flag misleads the UI. PHASE: P7."""
        # no returns -> no beta -> flag forced False even if caller passed True
        a = compute_analytics(_shares_snapshot(), PRICES, beta_is_proxy=True)
        assert a.portfolio_beta is None and a.beta_is_proxy is False

    def test_min_history_exclusion(self):
        """WHY: tickers with insufficient history drop out of risk math and are
        disclosed. BUG: computing β/vol on 3 data points is noise sold as signal.
        PHASE: P1 (honest risk), P7 (disclosure)."""
        df, bench = _synthetic_returns(n=120)
        a = compute_analytics(_shares_snapshot(), PRICES, returns=df,
                              benchmark_returns=bench, min_history_days=200)
        assert set(a.min_history_excluded) == {"COMI.CA", "TMGH.CA"}
        assert a.portfolio_beta is None and a.annual_vol is None
