"""Tests for the cross-sectional factor engine (remediation Track B)."""
import pytest

from tradingagents.factors.core import (
    FactorInputs,
    momentum_score,
    realized_vol,
    rank_universe,
    rank_to_decisions,
    DEFAULT_FACTOR_WEIGHTS,
)


def _uptrend(n=300, start=100.0, step=1.0):
    return [start + step * i for i in range(n)]


def _downtrend(n=300, start=400.0, step=1.0):
    return [start - step * i for i in range(n)]


def _flat(n=300, val=200.0):
    return [val] * n


# ── Price-derived atomic factors ─────────────────────────────────────────────

def test_momentum_positive_on_uptrend():
    assert momentum_score(_uptrend()) > 0


def test_momentum_negative_on_downtrend():
    assert momentum_score(_downtrend()) < 0


def test_momentum_none_on_short_history():
    assert momentum_score(_uptrend(n=50)) is None


def test_momentum_skips_recent_month():
    # With skip=21, the most recent 21 bars don't affect the score.
    base = _uptrend(n=300)
    spiked = list(base)
    spiked[-1] = 10_000.0  # huge last-day spike inside the skip window
    assert momentum_score(base) == pytest.approx(momentum_score(spiked))


def test_realized_vol_zero_on_flat_and_positive_on_noisy():
    assert realized_vol(_flat()) == 0.0
    noisy = [100.0, 110.0, 95.0, 120.0, 90.0, 130.0, 85.0] * 20
    assert realized_vol(noisy) > 0


def test_realized_vol_none_on_short_history():
    assert realized_vol([100.0, 101.0]) is None


# ── Cross-sectional ranking ──────────────────────────────────────────────────

def test_uptrend_outranks_downtrend():
    inputs = [
        FactorInputs("UP.CA", _uptrend()),
        FactorInputs("FLAT.CA", _flat()),
        FactorInputs("DOWN.CA", _downtrend()),
    ]
    scores = rank_universe(inputs)
    order = [s.ticker for s in scores]
    assert order[0] == "UP.CA"
    assert order[-1] == "DOWN.CA"
    # rank + percentile sanity
    assert scores[0].rank == 1 and scores[0].percentile == pytest.approx(1.0)
    assert scores[-1].rank == 3 and scores[-1].percentile == pytest.approx(0.0)


def test_value_factor_breaks_a_price_tie():
    # Two tickers with identical price paths; the one with higher earnings yield
    # (cheaper) must rank higher purely on the value bucket.
    a = FactorInputs("CHEAP.CA", _flat(), earnings_yield=0.15, roe=0.10)
    b = FactorInputs("RICH.CA", _flat(), earnings_yield=0.03, roe=0.10)
    scores = rank_universe([a, b])
    assert scores[0].ticker == "CHEAP.CA"


def test_quality_penalizes_high_leverage():
    a = FactorInputs("CLEAN.CA", _flat(), debt_to_equity=0.2)
    b = FactorInputs("LEVERED.CA", _flat(), debt_to_equity=5.0)
    scores = rank_universe([a, b])
    assert scores[0].ticker == "CLEAN.CA"


def test_missing_fundamentals_are_neutral_not_disqualifying():
    # Price-only inputs still rank fine (value/quality buckets contribute 0).
    inputs = [FactorInputs("UP.CA", _uptrend()), FactorInputs("DOWN.CA", _downtrend())]
    scores = rank_universe(inputs)
    assert scores[0].ticker == "UP.CA"
    assert set(scores[0].bucket_scores) == {"value", "quality", "momentum", "low_vol"}


def test_rank_to_decisions_top_quantile_buys():
    inputs = [FactorInputs(f"T{i}.CA", _uptrend(start=100.0 + i)) for i in range(10)]
    scores = rank_universe(inputs)
    decisions = rank_to_decisions(scores, top_quantile=0.30)
    buys = [t for t, d in decisions.items() if d == "BUY"]
    assert len(buys) == 3  # top 30% of 10
    # the #1 ranked name must be a BUY
    assert decisions[scores[0].ticker] == "BUY"
    assert decisions[scores[-1].ticker] == "HOLD"


def test_sector_neutral_rescues_well_run_bank():
    # Banks structurally carry high D/E; industrials low. Flat identical prices so
    # only the debt_to_equity (quality) factor drives the ranking.
    inputs = [
        FactorInputs("BANK_A.CA", _flat(), debt_to_equity=8.0),   # high even for a bank
        FactorInputs("BANK_B.CA", _flat(), debt_to_equity=6.0),   # low for a bank
        FactorInputs("IND_A.CA", _flat(), debt_to_equity=0.8),
        FactorInputs("IND_B.CA", _flat(), debt_to_equity=0.4),
    ]
    sector = {"BANK_A.CA": "banks", "BANK_B.CA": "banks",
              "IND_A.CA": "operational", "IND_B.CA": "operational"}

    # RAW (no sector map): both banks get dumped to the bottom purely on raw D/E.
    raw_order = [s.ticker for s in rank_universe(inputs)]
    assert set(raw_order[-2:]) == {"BANK_A.CA", "BANK_B.CA"}

    # SECTOR-NEUTRAL: the well-run bank (low D/E for a bank) is no longer penalized
    # for being a bank — at least one bank climbs into the top half.
    neutral_order = [s.ticker for s in rank_universe(inputs, sector_map=sector)]
    assert "BANK_B.CA" in neutral_order[:2]
    assert neutral_order[-1] == "BANK_A.CA"  # worst-in-sector bank still ranks last


def test_sector_map_none_is_unchanged_behavior():
    inputs = [FactorInputs("UP.CA", _uptrend()), FactorInputs("DOWN.CA", _downtrend())]
    assert [s.ticker for s in rank_universe(inputs)] == \
           [s.ticker for s in rank_universe(inputs, sector_map=None)]


def test_empty_universe():
    assert rank_universe([]) == []
    assert rank_to_decisions([]) == {}


def test_weights_sum_to_one():
    assert sum(DEFAULT_FACTOR_WEIGHTS.values()) == pytest.approx(1.0)
