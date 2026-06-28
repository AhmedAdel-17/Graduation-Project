"""Tests for the factor → Black-Litterman → portfolio bridge (Track B, Phase 5)."""
import pytest

from tradingagents.factors.core import FactorInputs, FactorScore
from tradingagents.factors.portfolio_bridge import (
    factor_scores_to_views,
    build_factor_portfolio,
)


def _fs(ticker, composite):
    return FactorScore(ticker=ticker, composite=composite, rank=1, percentile=1.0, bucket_scores={})


# ── Pure mapping ─────────────────────────────────────────────────────────────

def test_views_map_score_and_cap_confidence():
    views = factor_scores_to_views([_fs("A.CA", 1.5)], score_scale=1.5, max_confidence=0.6)
    score, conf = views["A.CA"]
    assert score == pytest.approx(1.0)         # composite/scale clipped to 1
    assert conf == pytest.approx(0.6)          # |score|=1 capped at max_confidence


def test_views_clip_extreme_and_sign():
    views = factor_scores_to_views([_fs("HI.CA", 9.0), _fs("LO.CA", -9.0)])
    assert views["HI.CA"][0] == pytest.approx(1.0)
    assert views["LO.CA"][0] == pytest.approx(-1.0)
    assert views["HI.CA"][1] > 0 and views["LO.CA"][1] > 0


def test_views_drop_weak_signal():
    # composite 0.03 / 1.5 = 0.02 < min_abs_score 0.05 ⇒ dropped
    assert factor_scores_to_views([_fs("MEH.CA", 0.03)]) == {}


def test_views_confidence_never_exceeds_cap():
    v = factor_scores_to_views([_fs("A.CA", 0.5)], score_scale=1.5, max_confidence=0.3)
    assert v["A.CA"][1] <= 0.3


# ── End-to-end (synthetic, no network) ───────────────────────────────────────

def _series(start, step, n=300):
    return [start + step * i for i in range(n)]


def test_build_factor_portfolio_overweights_strong_name():
    # UP has strong momentum + cheap/clean fundamentals; DOWN the opposite.
    inputs = [
        FactorInputs("UP.CA", _series(100, 1.0), earnings_yield=0.15, roe=0.20, debt_to_equity=0.3),
        FactorInputs("MID.CA", [200.0] * 300, earnings_yield=0.07, roe=0.10, debt_to_equity=1.0),
        FactorInputs("DOWN.CA", _series(400, -1.0), earnings_yield=0.02, roe=0.03, debt_to_equity=4.0),
    ]
    prices = {fi.ticker: fi.closes[-1] for fi in inputs}
    proposal = build_factor_portfolio(
        ["UP.CA", "MID.CA", "DOWN.CA"], "2024-06-01",
        inputs=inputs, prices=prices, capital_egp=1_000_000.0,
    )
    tw = proposal.target_weights
    # Long-only: no negative weights.
    assert all(w >= -1e-6 for w in tw.values())
    # The strong-factor name gets more weight than the weak one.
    assert tw.get("UP.CA", 0.0) > tw.get("DOWN.CA", 0.0)
    # Respects a sane per-name cap (well under 100%).
    assert max(tw.values()) <= 100.0


def test_build_factor_portfolio_requires_two_names():
    with pytest.raises(ValueError):
        build_factor_portfolio(
            ["ONLY.CA"], "2024-06-01",
            inputs=[FactorInputs("ONLY.CA", _series(100, 1.0))],
            prices={"ONLY.CA": 399.0},
        )


def test_build_factor_portfolio_needs_inputs_or_closes():
    with pytest.raises(ValueError):
        build_factor_portfolio(["A.CA", "B.CA"], "2024-06-01")
