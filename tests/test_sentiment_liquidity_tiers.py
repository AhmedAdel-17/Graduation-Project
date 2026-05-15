"""PR 1 — liquidity tiers: per-tier threshold lookup and conservative defaults."""
from __future__ import annotations

import pytest

from tradingagents.default_config import EGX_TICKERS
from tradingagents.sentiment.config import STOCK_THRESHOLDS_BY_TIER
from tradingagents.sentiment.liquidity_tiers import LiquidityTier, tier_for


def test_every_egx_ticker_gets_a_concrete_tier() -> None:
    for t in EGX_TICKERS:
        tier = tier_for(t)
        assert tier in (LiquidityTier.MEGA, LiquidityTier.MID, LiquidityTier.SMALL)


def test_unknown_ticker_defaults_to_small_for_conservative_gating() -> None:
    assert tier_for("ZZZZ") == LiquidityTier.SMALL
    assert tier_for("ZZZZ.CA") == LiquidityTier.SMALL


def test_normalization_handles_suffix_and_case() -> None:
    assert tier_for("comi") == LiquidityTier.MEGA
    assert tier_for("COMI.CA") == LiquidityTier.MEGA
    assert tier_for(" comi.ca ") == LiquidityTier.MEGA


@pytest.mark.parametrize(
    "ticker, expected",
    [
        ("COMI", LiquidityTier.MEGA),
        ("TMGH", LiquidityTier.MEGA),
        ("FWRY", LiquidityTier.MEGA),
        ("ETEL", LiquidityTier.MEGA),
        ("HRHO", LiquidityTier.MEGA),
    ],
)
def test_seed_mega_membership(ticker: str, expected: LiquidityTier) -> None:
    assert tier_for(ticker) == expected


def test_threshold_lookup_per_tier() -> None:
    for tier in (LiquidityTier.MEGA, LiquidityTier.MID, LiquidityTier.SMALL):
        thr = STOCK_THRESHOLDS_BY_TIER[tier]
        assert {"n_strong_mentions", "n_distinct_authors", "n_distinct_sources"} <= thr.keys()
        for k, v in thr.items():
            assert isinstance(v, (int, float))
            assert v > 0


def test_thresholds_decrease_monotonically_from_mega_to_small() -> None:
    mega = STOCK_THRESHOLDS_BY_TIER[LiquidityTier.MEGA]
    mid = STOCK_THRESHOLDS_BY_TIER[LiquidityTier.MID]
    small = STOCK_THRESHOLDS_BY_TIER[LiquidityTier.SMALL]
    for k in ("n_strong_mentions", "n_distinct_authors", "n_distinct_sources"):
        assert mega[k] >= mid[k] >= small[k], (
            f"threshold {k} must be non-increasing MEGA→MID→SMALL "
            f"(mega={mega[k]}, mid={mid[k]}, small={small[k]})"
        )


def test_provisional_defaults_match_approved_phase2_values() -> None:
    assert STOCK_THRESHOLDS_BY_TIER[LiquidityTier.MEGA]["n_strong_mentions"] == 8
    assert STOCK_THRESHOLDS_BY_TIER[LiquidityTier.MID]["n_strong_mentions"] == 5
    assert STOCK_THRESHOLDS_BY_TIER[LiquidityTier.SMALL]["n_strong_mentions"] == 3
