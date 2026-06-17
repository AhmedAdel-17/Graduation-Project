"""Tests for the deterministic multi-evidence view engine (roadmap P2).

These pin the *reasons* behind a recommendation: that cheaper/higher-quality/
higher-momentum names earn positive views, that a fresh agent signal is folded
in, that confidence rises with evidence coverage + agreement, and that a name
with no evidence gets no view (the optimizer leaves it at the prior). Pure,
offline, deterministic — injected ratios/returns, no network, no LLM.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.portfolio import schemas as s
from tradingagents.portfolio import views as v


UNIVERSE = ["COMI.CA", "TMGH.CA", "FWRY.CA"]


def _ratios(pe, roe):
    """Build a ratios dict over the universe from per-ticker (pe, roe)."""
    return {t: {"pe_ratio": pe[i], "roe": roe[i]} for i, t in enumerate(UNIVERSE)}


# ---------------------------------------------------------------------------
# 1. Each factor points the right way
# ---------------------------------------------------------------------------

class TestFactorDirection:
    def test_value_cheaper_is_more_positive(self):
        """WHY: value factor — a low P/E (high earnings yield) is the bullish view.
        BUG: a value engine that prefers expensive names. CITE: Basu 1977."""
        views = v.build_views(UNIVERSE, ratios=_ratios(pe=[5.0, 10.0, 30.0], roe=[0.2, 0.2, 0.2]))
        # COMI cheapest (PE 5) should beat FWRY dearest (PE 30) on the value leg.
        assert views["COMI.CA"].score > views["FWRY.CA"].score

    def test_quality_higher_roe_is_more_positive(self):
        """WHY: quality factor — higher ROE earns a positive view. CITE: Novy-Marx 2013."""
        views = v.build_views(UNIVERSE, ratios=_ratios(pe=[10.0, 10.0, 10.0], roe=[0.40, 0.20, 0.05]))
        assert views["COMI.CA"].score > views["FWRY.CA"].score

    def test_momentum_higher_trailing_return_is_more_positive(self):
        """WHY: 12-1 momentum — recent winners earn a positive view. CITE: Jegadeesh-Titman 1993."""
        n = 300
        rng = np.random.default_rng(0)
        base = rng.normal(0, 0.001, n)
        returns = pd.DataFrame({
            "COMI.CA": base + 0.004,   # strong uptrend
            "TMGH.CA": base + 0.000,
            "FWRY.CA": base - 0.004,   # downtrend
        })
        views = v.build_views(UNIVERSE, returns=returns)
        assert views["COMI.CA"].score > views["FWRY.CA"].score

    def test_agent_buy_positive_sell_negative_hold_abstains(self):
        """WHY: a fresh agent BUY/SELL is one evidence source; HOLD/stale/0-conf
        must abstain (no fabricated reason). PHASE: P2 (hybrid evidence)."""
        sigs = {
            "COMI.CA": s.SignalView(ticker="COMI.CA", label=s.SignalLabel.BUY, confidence=0.8),
            "TMGH.CA": s.SignalView(ticker="TMGH.CA", label=s.SignalLabel.SELL, confidence=0.8),
            "FWRY.CA": s.SignalView(ticker="FWRY.CA", label=s.SignalLabel.HOLD, confidence=0.0,
                                    source=s.SignalSource.QUANT_PRIOR, is_stale=True),
        }
        views = v.build_views(UNIVERSE, agent_signals=sigs)
        assert views["COMI.CA"].score > 0
        assert views["TMGH.CA"].score < 0
        assert "FWRY.CA" not in views  # HOLD + stale + 0-conf → no view


# ---------------------------------------------------------------------------
# 2. Composite, confidence, abstention, determinism
# ---------------------------------------------------------------------------

class TestComposite:
    def test_no_evidence_means_no_view(self):
        """WHY: a name with zero firing sources must get NO view, so the optimizer
        leaves it at the equilibrium prior. BUG: fabricating a 0-reason view. PHASE: P2."""
        views = v.build_views(UNIVERSE)  # nothing injected
        assert views == {}

    def test_aligned_evidence_beats_single_source_confidence(self):
        """WHY: confidence must rise with coverage + agreement. BUG: one weak source
        treated as confidently as three aligned ones. PHASE: P2 (feeds Idzorek Ω)."""
        # COMI: cheap AND high-quality (two aligned sources).
        # FWRY: only a value signal of similar strength (one source).
        ratios = {
            "COMI.CA": {"pe_ratio": 5.0, "roe": 0.40},
            "TMGH.CA": {"pe_ratio": 15.0, "roe": 0.20},
            "FWRY.CA": {"pe_ratio": 5.0},   # value only, no roe
        }
        views = v.build_views(UNIVERSE, ratios=ratios)
        assert views["COMI.CA"].confidence > views["FWRY.CA"].confidence
        assert all(0.0 < x.confidence <= 0.95 for x in views.values())

    def test_scores_bounded_and_deterministic(self):
        """WHY: scores ∈ [-1,1] and identical inputs → identical views (audit). PHASE: P2 gate."""
        ratios = _ratios(pe=[5.0, 12.0, 25.0], roe=[0.35, 0.18, 0.06])
        a = v.build_views(UNIVERSE, ratios=ratios)
        b = v.build_views(UNIVERSE, ratios=ratios)
        assert all(-1.0 <= a[t].score <= 1.0 for t in a)
        assert {t: a[t].as_tuple() for t in a} == {t: b[t].as_tuple() for t in b}

    def test_sentiment_sector_tilt_applied(self):
        """WHY: social_v2 sector sentiment must enter as a (low-weight) view source
        on every name in that sector. CITE: design — sentiment is sector-level on
        EGX (per-stock social too sparse). PHASE: c."""
        from tradingagents.dataflows.social_v2.sectors import ticker_sector
        sec = ticker_sector("ETEL.CA")
        views = v.build_views(["ETEL.CA"], sector_sentiment={sec: 0.8})
        assert "ETEL.CA" in views
        ev = {e.source: e for e in views["ETEL.CA"].evidence}
        assert "sentiment" in ev and ev["sentiment"].score > 0

    def test_provenance_lists_firing_sources(self):
        """WHY: the explainability chain needs the per-source evidence trail.
        PHASE: P2 → P3 (narration)."""
        views = v.build_views(UNIVERSE, ratios=_ratios(pe=[5, 12, 25], roe=[0.3, 0.2, 0.1]))
        prov = views["COMI.CA"].provenance()
        sources = {e["source"] for e in prov["evidence"]}
        assert {"value", "quality"} <= sources
        assert "score" in prov and "confidence" in prov
