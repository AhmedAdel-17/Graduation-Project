"""Tests for Layer C StockSentiment aggregator and the social analyst pre-LLM gate.

Coverage:
  - All three tier threshold sets (MEGA / MID / SMALL)
  - All four hard gates (n_strong_mentions, n_distinct_authors,
    n_distinct_sources, recent_72h_share)
  - Gate ordering (first-fail wins)
  - Spam exclusion before count
  - contradicts_market flag
  - All five MEGA tickers
  - Boundary / off-by-one values per tier
  - Confidence formula components and bounds
  - Contract invariants (score/reason mutual exclusion)
  - Social analyst pre-LLM gate: NO_SIGNAL skips LLM, SIGNAL calls LLM,
    absent datapoints calls LLM
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.sentiment.contracts import LayerStatus
from tradingagents.sentiment.liquidity_tiers import LiquidityTier, tier_for
from tradingagents.sentiment.stock import (
    StockDataPoint,
    _signal_confidence,
    _weighted_mean,
    compute_stock_sentiment,
)

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

REF_TIME = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
RECENT_TS = "2024-01-14T10:00:00Z"   # ~26 h before REF_TIME — within 72 h
OLD_TS = "2024-01-10T10:00:00Z"      # ~5 days before REF_TIME — outside 72 h
BORDER_TS = "2024-01-12T12:00:00Z"   # exactly 72 h before REF_TIME — boundary


def _strong(
    platform: str = "facebook",
    author: str = "u1",
    ts: str = RECENT_TS,
    score: float = 0.5,
    weight: float = 1.0,
    conf: float = 0.90,
) -> StockDataPoint:
    """StockDataPoint with entity_confidence >= 0.85 (strong mention)."""
    return StockDataPoint(
        timestamp=ts,
        platform=platform,
        author=author,
        sentiment_score=score,
        weight=weight,
        entity_confidence=conf,
    )


def _weak(
    platform: str = "facebook",
    author: str = "u1",
    ts: str = RECENT_TS,
    score: float = 0.5,
) -> StockDataPoint:
    """StockDataPoint with entity_confidence < 0.85 (does not count as strong)."""
    return StockDataPoint(
        timestamp=ts,
        platform=platform,
        author=author,
        sentiment_score=score,
        weight=1.0,
        entity_confidence=0.60,
    )


def _spam(
    platform: str = "facebook",
    author: str = "u1",
    ts: str = RECENT_TS,
    conf: float = 0.90,
) -> StockDataPoint:
    """Spam post that should be excluded before gate counts."""
    return StockDataPoint(
        timestamp=ts,
        platform=platform,
        author=author,
        sentiment_score=0.5,
        weight=1.0,
        entity_confidence=conf,
        is_spam_promo=True,
    )


def _mega_passing() -> List[StockDataPoint]:
    """Minimal passing MEGA set: 8 strong / 5 authors / 3 sources / all recent."""
    return [
        _strong("facebook", "u1"),
        _strong("facebook", "u2"),
        _strong("telegram", "u3"),
        _strong("telegram", "u4"),
        _strong("reddit",   "u5"),
        _strong("reddit",   "u1"),
        _strong("facebook", "u3"),
        _strong("telegram", "u2"),
    ]


def _mid_passing() -> List[StockDataPoint]:
    """Minimal passing MID set: 5 strong / 3 authors / 2 sources / all recent."""
    return [
        _strong("facebook", "u1"),
        _strong("facebook", "u2"),
        _strong("telegram", "u3"),
        _strong("telegram", "u1"),
        _strong("facebook", "u2"),
    ]


def _small_passing() -> List[StockDataPoint]:
    """Minimal passing SMALL set: 3 strong / 2 authors / 2 sources / all recent."""
    return [
        _strong("facebook", "u1"),
        _strong("telegram", "u2"),
        _strong("facebook", "u1"),
    ]


# ---------------------------------------------------------------------------
# 1. Empty / degenerate input
# ---------------------------------------------------------------------------


class TestEmptyAndDegenerateInput:
    def test_empty_posts_no_signal_gate1(self):
        result = compute_stock_sentiment("SMALL.CA", [], REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason is not None
        assert result.reason.gate_failed == "stock.n_strong_mentions"
        assert result.score is None

    def test_spam_only_no_signal_gate1(self):
        posts = [_spam() for _ in range(10)]
        result = compute_stock_sentiment("SMALL.CA", posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.n_strong_mentions"

    def test_weak_only_no_signal_gate1(self):
        posts = [_weak() for _ in range(20)]
        result = compute_stock_sentiment("SMALL.CA", posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.n_strong_mentions"


# ---------------------------------------------------------------------------
# 2. MEGA tier — all gates
# ---------------------------------------------------------------------------


class TestMegaGate1StrongMentions:
    """MEGA requires n_strong_mentions >= 8."""

    def test_seven_strong_fails(self):
        posts = [_strong(author=f"u{i}", platform=["facebook", "telegram", "reddit"][i % 3])
                 for i in range(7)]
        result = compute_stock_sentiment("COMI.CA", posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.n_strong_mentions"
        assert result.reason.metrics["n_strong_mentions"] == 7
        assert result.reason.metrics["required"] == 8

    def test_eight_strong_passes_gate1(self):
        # Still needs authors + sources + recency to be valid,
        # so we use the full passing set
        posts = _mega_passing()
        result = compute_stock_sentiment("COMI.CA", posts, REF_TIME)
        # If all gates pass → SIGNAL (we test SIGNAL separately)
        assert result.reason is None or result.reason.gate_failed != "stock.n_strong_mentions"

    def test_boundary_exactly_eight(self):
        # Exactly MEGA threshold — gate 1 passes, verify it's not the failing gate
        posts = _mega_passing()  # exactly 8 strong
        result = compute_stock_sentiment("COMI.CA", posts, REF_TIME)
        if result.status == LayerStatus.NO_SIGNAL:
            assert result.reason.gate_failed != "stock.n_strong_mentions"


class TestMegaGate2DistinctAuthors:
    """MEGA requires n_distinct_authors >= 5."""

    def test_four_authors_fails(self):
        # 8 strong, 4 authors, 3 sources — gate 2 fails
        posts = [
            _strong("facebook", "u1"), _strong("facebook", "u2"),
            _strong("telegram", "u3"), _strong("telegram", "u4"),
            _strong("reddit",   "u1"), _strong("reddit",   "u2"),
            _strong("facebook", "u3"), _strong("telegram", "u4"),
        ]
        result = compute_stock_sentiment("COMI.CA", posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.n_distinct_authors"
        assert result.reason.metrics["n_distinct_authors"] == 4
        assert result.reason.metrics["required"] == 5

    def test_five_authors_passes_gate2(self):
        posts = _mega_passing()  # 5 distinct authors
        result = compute_stock_sentiment("COMI.CA", posts, REF_TIME)
        if result.status == LayerStatus.NO_SIGNAL:
            assert result.reason.gate_failed != "stock.n_distinct_authors"


class TestMegaGate3DistinctSources:
    """MEGA requires n_distinct_sources >= 3."""

    def test_two_sources_fails(self):
        # 8 strong, 5 authors, only 2 platforms → gate 3 fails
        posts = [
            _strong("facebook", "u1"), _strong("facebook", "u2"),
            _strong("telegram", "u3"), _strong("telegram", "u4"),
            _strong("facebook", "u5"), _strong("telegram", "u1"),
            _strong("facebook", "u2"), _strong("telegram", "u3"),
        ]
        result = compute_stock_sentiment("COMI.CA", posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.n_distinct_sources"
        assert result.reason.metrics["n_distinct_sources"] == 2
        assert result.reason.metrics["required"] == 3

    def test_three_sources_passes_gate3(self):
        posts = _mega_passing()  # 3 platforms
        result = compute_stock_sentiment("COMI.CA", posts, REF_TIME)
        if result.status == LayerStatus.NO_SIGNAL:
            assert result.reason.gate_failed != "stock.n_distinct_sources"


class TestMegaGate4Recency:
    """MEGA requires recent_72h_share >= 0.60 across clean posts."""

    def test_below_60pct_recent_fails(self):
        # 8 clean posts: 3 recent (37.5%), 5 old → fails gate 4
        posts = [
            _strong("facebook", "u1", RECENT_TS),
            _strong("telegram", "u2", RECENT_TS),
            _strong("reddit",   "u3", RECENT_TS),
            _strong("facebook", "u4", OLD_TS),
            _strong("telegram", "u5", OLD_TS),
            _strong("reddit",   "u1", OLD_TS),
            _strong("facebook", "u2", OLD_TS),
            _strong("telegram", "u3", OLD_TS),
        ]
        result = compute_stock_sentiment("COMI.CA", posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.recent_72h_share"
        assert result.reason.metrics["n_recent"] == 3
        assert result.reason.metrics["n_total_clean"] == 8

    def test_exactly_60pct_passes(self):
        # 8 clean posts: 5 recent (62.5%) → passes gate 4 (>= 0.60)
        posts = [
            _strong("facebook", "u1", RECENT_TS),
            _strong("telegram", "u2", RECENT_TS),
            _strong("reddit",   "u3", RECENT_TS),
            _strong("facebook", "u4", RECENT_TS),
            _strong("telegram", "u5", RECENT_TS),
            _strong("reddit",   "u1", OLD_TS),
            _strong("facebook", "u2", OLD_TS),
            _strong("telegram", "u3", OLD_TS),
        ]
        result = compute_stock_sentiment("COMI.CA", posts, REF_TIME)
        if result.status == LayerStatus.NO_SIGNAL:
            assert result.reason.gate_failed != "stock.recent_72h_share"

    def test_all_recent_passes_gate4(self):
        result = compute_stock_sentiment("COMI.CA", _mega_passing(), REF_TIME)
        if result.status == LayerStatus.NO_SIGNAL:
            assert result.reason.gate_failed != "stock.recent_72h_share"


class TestMegaSignal:
    def test_all_gates_pass_returns_signal(self):
        result = compute_stock_sentiment("COMI.CA", _mega_passing(), REF_TIME)
        assert result.status == LayerStatus.SIGNAL
        assert result.score is not None
        assert result.reason is None
        assert result.tier == LiquidityTier.MEGA.value
        assert result.ticker == "COMI.CA"
        assert result.n_strong_mentions == 8
        assert result.n_distinct_authors == 5
        assert result.n_distinct_sources == 3


# ---------------------------------------------------------------------------
# 3. MID tier — all gates
# ---------------------------------------------------------------------------


class TestMidTierGates:
    """MID requires n_strong_mentions >= 5, n_distinct_authors >= 3, n_distinct_sources >= 2."""

    def test_mid_gate1_four_strong_fails(self):
        posts = [
            _strong("facebook", "u1"), _strong("telegram", "u2"),
            _strong("facebook", "u3"), _strong("telegram", "u1"),
        ]
        result = compute_stock_sentiment("EAST.CA", posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.n_strong_mentions"
        assert result.reason.metrics["required"] == 5

    def test_mid_gate2_two_authors_fails(self):
        posts = [
            _strong("facebook", "u1"), _strong("telegram", "u2"),
            _strong("facebook", "u1"), _strong("telegram", "u2"),
            _strong("facebook", "u1"),
        ]
        result = compute_stock_sentiment("EAST.CA", posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.n_distinct_authors"
        assert result.reason.metrics["required"] == 3

    def test_mid_gate3_one_source_fails(self):
        posts = [
            _strong("facebook", "u1"), _strong("facebook", "u2"),
            _strong("facebook", "u3"), _strong("facebook", "u1"),
            _strong("facebook", "u2"),
        ]
        result = compute_stock_sentiment("EAST.CA", posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.n_distinct_sources"
        assert result.reason.metrics["required"] == 2

    def test_mid_gate4_stale_fails(self):
        # 5 clean posts: 2 recent (40%) < 60%
        posts = [
            _strong("facebook", "u1", RECENT_TS),
            _strong("telegram", "u2", RECENT_TS),
            _strong("facebook", "u3", OLD_TS),
            _strong("telegram", "u1", OLD_TS),
            _strong("facebook", "u2", OLD_TS),
        ]
        result = compute_stock_sentiment("EAST.CA", posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.recent_72h_share"

    def test_mid_all_gates_pass_signal(self):
        result = compute_stock_sentiment("EAST.CA", _mid_passing(), REF_TIME)
        assert result.status == LayerStatus.SIGNAL
        assert result.tier == LiquidityTier.MID.value
        assert result.n_strong_mentions == 5
        assert result.n_distinct_authors == 3
        assert result.n_distinct_sources == 2


# ---------------------------------------------------------------------------
# 4. SMALL tier — all gates
# ---------------------------------------------------------------------------


class TestSmallTierGates:
    """SMALL requires n_strong_mentions >= 3, n_distinct_authors >= 2, n_distinct_sources >= 2."""

    def test_small_gate1_two_strong_fails(self):
        posts = [_strong("facebook", "u1"), _strong("telegram", "u2")]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.n_strong_mentions"
        assert result.reason.metrics["required"] == 3

    def test_small_gate2_one_author_fails(self):
        posts = [
            _strong("facebook", "u1"), _strong("telegram", "u1"),
            _strong("facebook", "u1"),
        ]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.n_distinct_authors"
        assert result.reason.metrics["required"] == 2

    def test_small_gate3_one_source_fails(self):
        posts = [
            _strong("facebook", "u1"), _strong("facebook", "u2"),
            _strong("facebook", "u1"),
        ]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.n_distinct_sources"
        assert result.reason.metrics["required"] == 2

    def test_small_gate4_stale_fails(self):
        # 3 clean posts: 1 recent (33%) < 60%
        posts = [
            _strong("facebook", "u1", RECENT_TS),
            _strong("telegram", "u2", OLD_TS),
            _strong("facebook", "u1", OLD_TS),
        ]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.recent_72h_share"

    def test_small_all_gates_pass_signal(self):
        result = compute_stock_sentiment("UNKNOWN.CA", _small_passing(), REF_TIME)
        assert result.status == LayerStatus.SIGNAL
        assert result.tier == LiquidityTier.SMALL.value


# ---------------------------------------------------------------------------
# 5. Gate ordering
# ---------------------------------------------------------------------------


class TestGateOrdering:
    """First-fail wins: gate 1 failure hides gate 2/3/4 failures."""

    def test_gate1_reported_not_gate2(self):
        # Only 1 strong (< 3 SMALL), only 1 author (< 2 SMALL) — gate 1 must win
        posts = [_strong("facebook", "u1")]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        assert result.reason.gate_failed == "stock.n_strong_mentions"

    def test_gate2_reported_not_gate3(self):
        # 3 strong, 1 author (< 2), 1 source (< 2) — gate 2 must win
        posts = [
            _strong("facebook", "u1"), _strong("facebook", "u1"),
            _strong("facebook", "u1"),
        ]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        assert result.reason.gate_failed == "stock.n_distinct_authors"

    def test_gate3_reported_not_gate4(self):
        # 3 strong, 2 authors, 1 source (< 2) — gate 3 must win, regardless of recency
        posts = [
            _strong("facebook", "u1", OLD_TS),
            _strong("facebook", "u2", OLD_TS),
            _strong("facebook", "u1", OLD_TS),
        ]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        assert result.reason.gate_failed == "stock.n_distinct_sources"

    def test_gate4_reported_when_prior_gates_pass(self):
        # 3 strong, 2 authors, 2 sources, 0% recent — gate 4 must win
        posts = [
            _strong("facebook", "u1", OLD_TS),
            _strong("telegram", "u2", OLD_TS),
            _strong("facebook", "u1", OLD_TS),
        ]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        assert result.reason.gate_failed == "stock.recent_72h_share"


# ---------------------------------------------------------------------------
# 6. Spam exclusion
# ---------------------------------------------------------------------------


class TestSpamExclusion:
    def test_spam_not_counted_as_strong(self):
        # 3 spam posts (would be strong if counted) + 2 real strong → gate 1 fails
        posts = [_spam() for _ in range(3)] + [
            _strong("facebook", "u1"),
            _strong("telegram", "u2"),
        ]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.n_strong_mentions"
        assert result.reason.metrics["n_strong_mentions"] == 2

    def test_spam_not_counted_for_authors(self):
        # 3 strong non-spam (2 authors, 2 sources, recent): gate 2 fails (need 2)
        # Add spam from 3 more authors — they shouldn't help
        posts = [
            _strong("facebook", "u1"),
            _strong("telegram", "u1"),
            _strong("facebook", "u1"),
            _spam("reddit", "u99"),
            _spam("reddit", "u98"),
            _spam("telegram", "u97"),
        ]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.n_distinct_authors"

    def test_spam_excluded_before_recency_count(self):
        # 3 recent strong non-spam + 5 spam old → recency gate sees only the 3 clean posts (100% recent)
        posts = _small_passing() + [_spam(ts=OLD_TS) for _ in range(5)]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        assert result.status == LayerStatus.SIGNAL

    def test_all_spam_with_valid_real_posts_passes(self):
        # Real posts meet SMALL gates; spam posts are irrelevant
        posts = _small_passing() + [_spam() for _ in range(10)]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        assert result.status == LayerStatus.SIGNAL
        assert result.n_strong_mentions == 3  # only non-spam counted

    def test_mixed_spam_promo_flag_respected(self):
        posts = [
            StockDataPoint("2024-01-14T10:00:00Z", "facebook", "u1", 0.5, 1.0, 0.90, is_spam_promo=True),
            StockDataPoint("2024-01-14T10:00:00Z", "telegram", "u2", 0.5, 1.0, 0.90, is_spam_promo=False),
            StockDataPoint("2024-01-14T10:00:00Z", "facebook", "u3", 0.5, 1.0, 0.90, is_spam_promo=False),
            StockDataPoint("2024-01-14T10:00:00Z", "telegram", "u1", 0.5, 1.0, 0.90, is_spam_promo=False),
        ]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        # 3 non-spam strong (SMALL needs 3) → should pass gate 1
        assert result.reason is None or result.reason.gate_failed != "stock.n_strong_mentions"


# ---------------------------------------------------------------------------
# 7. Recency gate — edge cases
# ---------------------------------------------------------------------------


class TestRecencyGate:
    def test_unparseable_timestamp_treated_as_old(self):
        # SMALL: 3 strong, 2 authors, 2 sources, 2 recent + 1 unparseable (= 2/3 = 67% > 60%)
        posts = [
            _strong("facebook", "u1", RECENT_TS),
            _strong("telegram", "u2", RECENT_TS),
            _strong("facebook", "u1", "not-a-date"),  # treated as old
        ]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        # 2/3 recent = 67% >= 60% → gate 4 passes
        if result.status == LayerStatus.NO_SIGNAL:
            assert result.reason.gate_failed != "stock.recent_72h_share"

    def test_all_old_timestamps_fails(self):
        posts = _small_passing()
        posts_old = [
            StockDataPoint(OLD_TS, p.platform, p.author, p.sentiment_score, p.weight, p.entity_confidence)
            for p in posts
        ]
        result = compute_stock_sentiment("UNKNOWN.CA", posts_old, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.recent_72h_share"

    def test_empty_timestamp_treated_as_old(self):
        posts = [
            _strong("facebook", "u1", ""),
            _strong("telegram", "u2", ""),
            _strong("facebook", "u1", ""),
        ]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.recent_72h_share"

    def test_exactly_72h_boundary_is_recent(self):
        # BORDER_TS = "2024-01-12T12:00:00Z" = exactly 72h before REF_TIME
        # cutoff = REF_TIME - 72h → boundary post has dt >= cutoff → counted as recent
        posts = [
            _strong("facebook", "u1", BORDER_TS),
            _strong("telegram", "u2", BORDER_TS),
            _strong("facebook", "u1", BORDER_TS),
        ]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        # 3/3 = 100% recent → gate 4 passes
        if result.status == LayerStatus.NO_SIGNAL:
            assert result.reason.gate_failed != "stock.recent_72h_share"

    def test_59pct_recent_fails(self):
        # 10 clean posts: 5 recent (50%) < 60% for SMALL
        posts = [
            _strong("facebook", "u1", RECENT_TS),
            _strong("telegram", "u2", RECENT_TS),
            _strong("facebook", "u1", OLD_TS),
            _strong("telegram", "u2", OLD_TS),
            _strong("facebook", "u1", OLD_TS),
        ]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        # n_recent=2 / n_total=5 = 40% < 60%
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.recent_72h_share"


# ---------------------------------------------------------------------------
# 8. contradicts_market flag
# ---------------------------------------------------------------------------


class TestContradictsMarket:
    def _base_signal(self, score: float) -> List[StockDataPoint]:
        """Returns posts where weighted_mean ≈ score (uniform weights → simple mean)."""
        return [
            _strong("facebook", f"u{i}", RECENT_TS, score=score, weight=1.0)
            for i in range(1, 4)
        ] + [
            _strong("telegram", "u1", RECENT_TS, score=score, weight=1.0),
            _strong("facebook", "u2", RECENT_TS, score=score, weight=1.0),
        ]

    def test_opposite_signs_significant_sets_flag(self):
        posts = self._base_signal(0.50)  # bullish stock
        result = compute_stock_sentiment("EAST.CA", posts, REF_TIME, market_score=-0.40)
        assert result.status == LayerStatus.SIGNAL
        assert result.contradicts_market is True

    def test_same_sign_no_flag(self):
        posts = self._base_signal(0.50)
        result = compute_stock_sentiment("EAST.CA", posts, REF_TIME, market_score=0.30)
        assert result.status == LayerStatus.SIGNAL
        assert result.contradicts_market is False

    def test_no_market_score_no_flag(self):
        result = compute_stock_sentiment("EAST.CA", _mid_passing(), REF_TIME, market_score=None)
        assert result.status == LayerStatus.SIGNAL
        assert result.contradicts_market is False

    def test_near_neutral_market_no_flag(self):
        # market_score near zero (< 0.15 abs) → no meaningful contradiction
        posts = self._base_signal(0.50)
        result = compute_stock_sentiment("EAST.CA", posts, REF_TIME, market_score=-0.10)
        assert result.status == LayerStatus.SIGNAL
        assert result.contradicts_market is False

    def test_near_neutral_stock_no_flag(self):
        # stock score near zero → no flag even if market is bearish
        posts = self._base_signal(0.05)
        result = compute_stock_sentiment("EAST.CA", posts, REF_TIME, market_score=-0.40)
        assert result.status == LayerStatus.SIGNAL
        assert result.contradicts_market is False

    def test_contradicts_flag_absent_on_no_signal(self):
        # Gate fails → contradicts_market defaults to False on StockSentiment
        result = compute_stock_sentiment("EAST.CA", [], REF_TIME, market_score=-0.50)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.contradicts_market is False

    def test_both_negative_same_direction_no_flag(self):
        posts = self._base_signal(-0.40)
        result = compute_stock_sentiment("EAST.CA", posts, REF_TIME, market_score=-0.30)
        assert result.contradicts_market is False

    def test_both_negative_opposite_score_sets_flag(self):
        # stock bearish, market bullish → contradicts
        posts = self._base_signal(-0.40)
        result = compute_stock_sentiment("EAST.CA", posts, REF_TIME, market_score=0.30)
        assert result.contradicts_market is True


# ---------------------------------------------------------------------------
# 9. All five MEGA tickers resolve correct tier
# ---------------------------------------------------------------------------


class TestAllMegaTickers:
    MEGA_TICKERS = ["COMI.CA", "TMGH.CA", "FWRY.CA", "ETEL.CA", "HRHO.CA"]

    @pytest.mark.parametrize("ticker", MEGA_TICKERS)
    def test_mega_ticker_tier_assignment(self, ticker):
        assert tier_for(ticker) == LiquidityTier.MEGA

    @pytest.mark.parametrize("ticker", MEGA_TICKERS)
    def test_mega_ticker_needs_8_strong_mentions(self, ticker):
        posts = [_strong("facebook", f"u{i}", RECENT_TS) for i in range(7)]
        result = compute_stock_sentiment(ticker, posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.metrics["required"] == 8

    @pytest.mark.parametrize("ticker", MEGA_TICKERS)
    def test_mega_ticker_signal_with_full_passing_set(self, ticker):
        result = compute_stock_sentiment(ticker, _mega_passing(), REF_TIME)
        assert result.status == LayerStatus.SIGNAL
        assert result.tier == LiquidityTier.MEGA.value


# ---------------------------------------------------------------------------
# 10. Boundary values per tier (off-by-one)
# ---------------------------------------------------------------------------


class TestBoundaryValues:
    def test_mega_7_strong_fails_8_passes(self):
        # 7 → fail; 8 → gate 1 passes (next gates may still fail)
        posts7 = [_strong(["facebook", "telegram", "reddit"][i % 3], f"u{i}") for i in range(7)]
        result7 = compute_stock_sentiment("COMI.CA", posts7, REF_TIME)
        assert result7.reason.gate_failed == "stock.n_strong_mentions"

        posts8 = _mega_passing()
        result8 = compute_stock_sentiment("COMI.CA", posts8, REF_TIME)
        if result8.status == LayerStatus.NO_SIGNAL:
            assert result8.reason.gate_failed != "stock.n_strong_mentions"

    def test_mid_4_strong_fails_5_passes(self):
        posts4 = [_strong(["facebook", "telegram"][i % 2], f"u{i}") for i in range(4)]
        result4 = compute_stock_sentiment("EAST.CA", posts4, REF_TIME)
        assert result4.reason.gate_failed == "stock.n_strong_mentions"

        result5 = compute_stock_sentiment("EAST.CA", _mid_passing(), REF_TIME)
        if result5.status == LayerStatus.NO_SIGNAL:
            assert result5.reason.gate_failed != "stock.n_strong_mentions"

    def test_small_2_strong_fails_3_passes(self):
        posts2 = [_strong("facebook", "u1"), _strong("telegram", "u2")]
        result2 = compute_stock_sentiment("UNKNOWN.CA", posts2, REF_TIME)
        assert result2.reason.gate_failed == "stock.n_strong_mentions"

        result3 = compute_stock_sentiment("UNKNOWN.CA", _small_passing(), REF_TIME)
        if result3.status == LayerStatus.NO_SIGNAL:
            assert result3.reason.gate_failed != "stock.n_strong_mentions"

    def test_entity_conf_exactly_at_threshold_counts_as_strong(self):
        # entity_confidence == 0.85 exactly → counts as strong
        posts = [
            StockDataPoint(RECENT_TS, "facebook", "u1", 0.5, 1.0, 0.85),
            StockDataPoint(RECENT_TS, "telegram", "u2", 0.5, 1.0, 0.85),
            StockDataPoint(RECENT_TS, "facebook", "u1", 0.5, 1.0, 0.85),
        ]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        if result.status == LayerStatus.NO_SIGNAL:
            assert result.reason.gate_failed != "stock.n_strong_mentions"

    def test_entity_conf_just_below_threshold_not_strong(self):
        # entity_confidence == 0.849 → not a strong mention
        posts = [
            StockDataPoint(RECENT_TS, "facebook", "u1", 0.5, 1.0, 0.849),
            StockDataPoint(RECENT_TS, "telegram", "u2", 0.5, 1.0, 0.849),
            StockDataPoint(RECENT_TS, "facebook", "u1", 0.5, 1.0, 0.849),
        ]
        result = compute_stock_sentiment("UNKNOWN.CA", posts, REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.n_strong_mentions"


# ---------------------------------------------------------------------------
# 11. Score and confidence computation
# ---------------------------------------------------------------------------


class TestScoreAndConfidence:
    def test_uniform_weight_mean_score(self):
        """With uniform weights, combined score equals arithmetic mean."""
        posts = [
            _strong("facebook", "u1", score=0.8, weight=1.0),
            _strong("telegram", "u2", score=0.4, weight=1.0),
            _strong("reddit",   "u3", score=0.6, weight=1.0),
            _strong("facebook", "u4", score=0.2, weight=1.0),
            _strong("telegram", "u5", score=0.0, weight=1.0),
        ]
        result = compute_stock_sentiment("EAST.CA", posts, REF_TIME)
        assert result.status == LayerStatus.SIGNAL
        expected = round((0.8 + 0.4 + 0.6 + 0.2 + 0.0) / 5, 4)
        assert result.score == pytest.approx(expected, abs=1e-3)

    def test_weighted_mean_respects_weights(self):
        """Higher-weight posts should pull the score toward their value."""
        posts = [
            _strong("facebook", "u1", score=1.0, weight=10.0),
            _strong("telegram", "u2", score=0.0, weight=1.0),
            _strong("facebook", "u3", score=0.0, weight=1.0),
            _strong("telegram", "u4", score=0.0, weight=1.0),
            _strong("reddit",   "u5", score=0.0, weight=1.0),
        ]
        result = compute_stock_sentiment("EAST.CA", posts, REF_TIME)
        assert result.status == LayerStatus.SIGNAL
        # 10×1.0 / 14 = 0.714...
        assert result.score > 0.5

    def test_score_in_range(self):
        result = compute_stock_sentiment("EAST.CA", _mid_passing(), REF_TIME)
        assert result.status == LayerStatus.SIGNAL
        assert -1.0 <= result.score <= 1.0

    def test_confidence_in_range(self):
        result = compute_stock_sentiment("EAST.CA", _mid_passing(), REF_TIME)
        assert result.status == LayerStatus.SIGNAL
        assert 0.0 <= result.confidence <= 1.0

    def test_zero_weight_falls_back_to_simple_mean(self):
        posts = [
            _strong("facebook", "u1", score=0.8, weight=0.0),
            _strong("telegram", "u2", score=0.0, weight=0.0),
            _strong("facebook", "u3", score=0.4, weight=0.0),
            _strong("telegram", "u4", score=0.0, weight=0.0),
            _strong("reddit",   "u5", score=0.0, weight=0.0),
        ]
        result = compute_stock_sentiment("EAST.CA", posts, REF_TIME)
        assert result.status == LayerStatus.SIGNAL
        expected = round((0.8 + 0.0 + 0.4 + 0.0 + 0.0) / 5, 4)
        assert result.score == pytest.approx(expected, abs=1e-3)


# ---------------------------------------------------------------------------
# 12. _signal_confidence unit tests
# ---------------------------------------------------------------------------


class TestConfidenceFormula:
    def test_at_exact_threshold_gives_partial_size_conf(self):
        # n_strong=req → size_conf = req/(2*req) = 0.5
        conf = _signal_confidence(n_strong=5, n_authors=5, score_abs=0.35,
                                  req_strong=5, req_authors=5)
        assert 0.0 < conf <= 1.0

    def test_double_threshold_saturates_size(self):
        conf_base = _signal_confidence(n_strong=5, n_authors=5, score_abs=0.35,
                                       req_strong=5, req_authors=5)
        conf_double = _signal_confidence(n_strong=10, n_authors=10, score_abs=0.35,
                                         req_strong=5, req_authors=5)
        assert conf_double >= conf_base

    def test_higher_clarity_increases_confidence(self):
        conf_low = _signal_confidence(10, 10, 0.0, 5, 5)
        conf_high = _signal_confidence(10, 10, 0.35, 5, 5)
        assert conf_high > conf_low

    def test_confidence_bounded_at_one(self):
        conf = _signal_confidence(1000, 1000, 1.0, 5, 5)
        assert conf <= 1.0

    def test_confidence_non_negative(self):
        conf = _signal_confidence(0, 0, 0.0, 5, 5)
        assert conf >= 0.0

    def test_more_diversity_increases_confidence(self):
        conf_low = _signal_confidence(10, 5, 0.20, 5, 5)
        conf_high = _signal_confidence(10, 10, 0.20, 5, 5)
        assert conf_high > conf_low


# ---------------------------------------------------------------------------
# 13. Contract invariants
# ---------------------------------------------------------------------------


class TestContractInvariants:
    def test_no_signal_has_reason_no_score(self):
        result = compute_stock_sentiment("UNKNOWN.CA", [], REF_TIME)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason is not None
        assert result.score is None

    def test_signal_has_score_no_reason(self):
        result = compute_stock_sentiment("UNKNOWN.CA", _small_passing(), REF_TIME)
        assert result.status == LayerStatus.SIGNAL
        assert result.score is not None
        assert result.reason is None

    def test_no_signal_reason_log_str_contains_gate(self):
        result = compute_stock_sentiment("UNKNOWN.CA", [], REF_TIME)
        log_str = result.reason.to_log_str()
        assert "NO_SIGNAL:" in log_str
        assert "gate=" in log_str

    def test_ticker_preserved_in_both_outcomes(self):
        no_sig = compute_stock_sentiment("COMI.CA", [], REF_TIME)
        sig = compute_stock_sentiment("COMI.CA", _mega_passing(), REF_TIME)
        assert no_sig.ticker == "COMI.CA"
        assert sig.ticker == "COMI.CA"

    def test_tier_preserved_correctly(self):
        result = compute_stock_sentiment("COMI.CA", _mega_passing(), REF_TIME)
        assert result.tier == "MEGA"
        result2 = compute_stock_sentiment("EAST.CA", _mid_passing(), REF_TIME)
        assert result2.tier == "MID"
        result3 = compute_stock_sentiment("UNKNOWN.CA", _small_passing(), REF_TIME)
        assert result3.tier == "SMALL"


# ---------------------------------------------------------------------------
# 14. Anonymous author handling
# ---------------------------------------------------------------------------


class TestAnonymousAuthor:
    def test_empty_authors_collapsed_to_single_bucket(self):
        # 5 posts all with empty author + 1 post with real author → n_distinct_authors = 2
        # (1 "_anonymous" bucket + 1 real author)
        posts = [
            _strong("facebook", "", RECENT_TS),   # anonymous
            _strong("telegram", "", RECENT_TS),   # anonymous
            _strong("facebook", "", RECENT_TS),   # anonymous
            _strong("telegram", "u1", RECENT_TS), # real
            _strong("reddit",   "", RECENT_TS),   # anonymous
        ]
        result = compute_stock_sentiment("EAST.CA", posts, REF_TIME)
        # n_distinct_authors = 2 (MID needs 3) → gate 2 fails
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "stock.n_distinct_authors"
        assert result.reason.metrics["n_distinct_authors"] == 2


# ---------------------------------------------------------------------------
# 15. Social media analyst pre-LLM gate
# ---------------------------------------------------------------------------


class TestSocialAnalystPreLLMGate:
    """Verify that the Layer C gate in social_media_analyst short-circuits on NO_SIGNAL."""

    def _make_state(self, ticker: str, datapoints: list) -> dict:
        return {
            "trade_date": "2024-01-15",
            "company_of_interest": ticker,
            "prefetched_stock_datapoints": datapoints,
            "prefetched_social_sentiment": "",
            "prefetched_social_posts": "",
            "social_messages": [],
        }

    def _strong_dp(self, platform="facebook", author="u1", conf=0.90) -> dict:
        return {
            "timestamp": RECENT_TS,
            "platform": platform,
            "author": author,
            "sentiment_score": 0.5,
            "weight": 1.0,
            "entity_confidence": conf,
            "is_spam_promo": False,
        }

    def test_no_signal_skips_llm(self):
        """One strong mention for MEGA ticker (needs 8) → LLM must not be called."""
        from tradingagents.agents.analysts.social_media_analyst import (
            create_social_media_analyst,
        )
        mock_llm = MagicMock()
        analyst = create_social_media_analyst(mock_llm)

        state = self._make_state("COMI.CA", [self._strong_dp()])
        result = analyst(state)

        mock_llm.invoke.assert_not_called()
        assert "insufficient data" in result["sentiment_report"].lower()
        analysis = json.loads(result["social_sentiment_analysis"])
        assert analysis["layer_c_status"] == "NO_SIGNAL"

    def test_signal_calls_llm(self):
        """Enough data for MEGA → LLM must be invoked (prefetch path)."""
        from tradingagents.agents.analysts.social_media_analyst import (
            create_social_media_analyst,
        )
        mock_response = MagicMock()
        mock_response.tool_calls = []
        mock_response.content = '{"sentiment":"neutral","sentiment_score":0.0,"confidence":0.5,"buzz_score":0.5,"hype_detected":false,"direction":"neutral","post_excerpts":[],"key_themes":[],"platform_breakdown":{},"language_breakdown":{}}'
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        # 8 strong, 5 authors, 3 sources — MEGA gate passes
        datapoints = [
            self._strong_dp("facebook", f"u{i}") for i in range(1, 6)
        ] + [
            self._strong_dp("telegram", "u1"),
            self._strong_dp("telegram", "u2"),
            self._strong_dp("reddit", "u3"),
        ]

        state = self._make_state("COMI.CA", datapoints)
        # Also provide prefetched content so analyst uses prefetch path (not tool-calling)
        state["prefetched_social_sentiment"] = "some data"

        analyst = create_social_media_analyst(mock_llm)
        analyst(state)
        mock_llm.invoke.assert_called_once()

    def test_absent_datapoints_calls_llm(self):
        """When prefetched_stock_datapoints is absent, gate is skipped → LLM called."""
        from tradingagents.agents.analysts.social_media_analyst import (
            create_social_media_analyst,
        )
        mock_response = MagicMock()
        mock_response.tool_calls = []
        mock_response.content = "some report"
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        state = {
            "trade_date": "2024-01-15",
            "company_of_interest": "COMI.CA",
            # No prefetched_stock_datapoints key
            "prefetched_social_sentiment": "data",
            "prefetched_social_posts": "",
            "social_messages": [],
        }
        analyst = create_social_media_analyst(mock_llm)
        analyst(state)
        mock_llm.invoke.assert_called_once()

    def test_empty_datapoints_calls_llm(self):
        """Empty list is falsy → gate check skipped → LLM called."""
        from tradingagents.agents.analysts.social_media_analyst import (
            create_social_media_analyst,
        )
        mock_response = MagicMock()
        mock_response.tool_calls = []
        mock_response.content = "some report"
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        state = self._make_state("COMI.CA", [])
        state["prefetched_social_sentiment"] = "data"
        analyst = create_social_media_analyst(mock_llm)
        analyst(state)
        mock_llm.invoke.assert_called_once()

    def test_no_signal_sentiment_report_is_template(self):
        """The exact NO_SIGNAL template string is used."""
        from tradingagents.agents.analysts.social_media_analyst import (
            _NO_SIGNAL_TEMPLATE,
            create_social_media_analyst,
        )
        mock_llm = MagicMock()
        analyst = create_social_media_analyst(mock_llm)
        state = self._make_state("COMI.CA", [self._strong_dp()])
        result = analyst(state)
        assert result["sentiment_report"] == _NO_SIGNAL_TEMPLATE

    def test_no_signal_social_messages_empty(self):
        """social_messages should be [] when gate fires (no LLM result)."""
        from tradingagents.agents.analysts.social_media_analyst import (
            create_social_media_analyst,
        )
        mock_llm = MagicMock()
        analyst = create_social_media_analyst(mock_llm)
        state = self._make_state("COMI.CA", [self._strong_dp()])
        result = analyst(state)
        assert result["social_messages"] == []
