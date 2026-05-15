"""PR 3 test suite — Layer A MarketSentiment aggregator.

Coverage:
  - Gate 1: n_total_posts < 50 → NO_SIGNAL with gate="market.n_total_posts"
  - Gate 2: n_distinct_sources < 2 → NO_SIGNAL with gate="market.n_distinct_sources"
  - Gate 3: recent_24h_share < 0.30 → NO_SIGNAL with gate="market.recent_24h_share"
  - All gates pass → SIGNAL with score, regime, volatility_mood, confidence set
  - Regime classification from score bands (5 bands)
  - Volatility mood classification from score std (3 bands)
  - Contract invariants: SIGNAL has score/no reason; NO_SIGNAL has no score/reason
  - Confidence bounds: always in [0, 1]
  - Empty post list → NO_SIGNAL (Gate 1 fails with n_posts=0)
  - Gate ordering: volume checked before source-diversity
  - Timestamp edge cases: unparseable / empty → conservative (not recent)
  - Weighted average: higher-weight posts dominate score
  - Exact boundary values: n_posts=49 vs 50; share=0.299 vs 0.30

All tests pass ``reference_time`` explicitly for determinism.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.sentiment.contracts import (
    LayerStatus,
    MarketRegime,
    VolatilityMood,
)
from tradingagents.sentiment.market import (
    MarketDataPoint,
    _parse_timestamp,
    _regime_from_score,
    _recent_share,
    _volatility_from_std,
    _weighted_stats,
    compute_market_sentiment,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# Fixed reference time — all timestamp calculations are relative to this.
REF = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

# One hour before reference — clearly recent (within 24 h window)
_RECENT_TS = (REF - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S%z")

# 48 hours before reference — stale (outside 24 h window)
_STALE_TS = (REF - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S%z")


def _make(
    n: int,
    *,
    platform: str = "facebook",
    score: float = 0.2,
    weight: float = 0.5,
    hours_ago: float = 1.0,
) -> list[MarketDataPoint]:
    """Return *n* identical MarketDataPoints with a recent timestamp."""
    ts = (REF - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S%z")
    return [MarketDataPoint(timestamp=ts, platform=platform, sentiment_score=score, weight=weight)] * n


def _make_multi(
    n_per_source: int,
    platforms: list[str],
    *,
    score: float = 0.2,
    weight: float = 0.5,
    hours_ago: float = 1.0,
) -> list[MarketDataPoint]:
    """Return *n_per_source* posts per platform, all recent."""
    ts = (REF - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S%z")
    posts = []
    for p in platforms:
        posts.extend(
            [MarketDataPoint(timestamp=ts, platform=p, sentiment_score=score, weight=weight)]
            * n_per_source
        )
    return posts


# ---------------------------------------------------------------------------
# 1. Gate 1: insufficient post volume
# ---------------------------------------------------------------------------

class TestGate1Volume:

    def test_empty_posts_no_signal(self):
        result = compute_market_sentiment([], reference_time=REF)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason is not None
        assert result.reason.gate_failed == "market.n_total_posts"
        assert result.score is None

    def test_49_posts_no_signal(self):
        posts = _make_multi(25, ["facebook", "telegram"])  # 50 posts
        posts = posts[:49]  # trim to 49
        result = compute_market_sentiment(posts, reference_time=REF)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "market.n_total_posts"
        assert result.reason.metrics["n_posts"] == 49
        assert result.reason.metrics["required"] == 50

    def test_exactly_50_posts_passes_gate_1(self):
        # 50 posts from 2 sources, all recent — all gates should pass
        posts = _make_multi(25, ["facebook", "telegram"])
        result = compute_market_sentiment(posts, reference_time=REF)
        # Gate 1 passed (may still emit SIGNAL if other gates pass too)
        assert result.status != LayerStatus.NO_SIGNAL or \
               result.reason.gate_failed != "market.n_total_posts"

    def test_no_signal_score_is_none(self):
        """Contract invariant: NO_SIGNAL must have score=None."""
        result = compute_market_sentiment(_make(10), reference_time=REF)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.score is None

    def test_no_signal_metrics_present(self):
        posts = _make(30)
        result = compute_market_sentiment(posts, reference_time=REF)
        assert result.reason is not None
        assert "n_posts" in result.reason.metrics
        assert "required" in result.reason.metrics

    def test_gate_1_checked_before_gate_2(self):
        """With 10 posts from a single source, Gate 1 must fire first."""
        posts = _make(10, platform="facebook")  # 10 < 50; also 1 source < 2
        result = compute_market_sentiment(posts, reference_time=REF)
        assert result.reason.gate_failed == "market.n_total_posts"


# ---------------------------------------------------------------------------
# 2. Gate 2: insufficient source diversity
# ---------------------------------------------------------------------------

class TestGate2SourceDiversity:

    def test_single_source_no_signal(self):
        # 60 posts, all from one platform → Gate 2 fails
        posts = _make(60, platform="facebook")
        result = compute_market_sentiment(posts, reference_time=REF)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "market.n_distinct_sources"
        assert result.reason.metrics["n_distinct_sources"] == 1
        assert result.reason.metrics["required"] == 2

    def test_single_source_score_is_none(self):
        posts = _make(60, platform="telegram")
        result = compute_market_sentiment(posts, reference_time=REF)
        assert result.score is None

    def test_two_sources_passes_gate_2(self):
        # 50 posts across 2 sources, all recent — should reach Gate 3 at minimum
        posts = _make_multi(25, ["facebook", "telegram"])
        result = compute_market_sentiment(posts, reference_time=REF)
        if result.status == LayerStatus.NO_SIGNAL:
            assert result.reason.gate_failed != "market.n_distinct_sources"

    def test_three_sources_passes_gate_2(self):
        posts = _make_multi(20, ["facebook", "telegram", "reddit"])
        result = compute_market_sentiment(posts, reference_time=REF)
        if result.status == LayerStatus.NO_SIGNAL:
            assert result.reason.gate_failed != "market.n_distinct_sources"

    def test_gate2_metrics_in_log_str(self):
        posts = _make(60, platform="facebook")
        result = compute_market_sentiment(posts, reference_time=REF)
        log_str = result.reason.to_log_str()
        assert "n_distinct_sources=1" in log_str
        assert "required=2" in log_str


# ---------------------------------------------------------------------------
# 3. Gate 3: staleness / recency
# ---------------------------------------------------------------------------

class TestGate3Recency:

    def test_all_stale_posts_no_signal(self):
        # 60 posts from 2 sources, all 48 h old → recent_share = 0 %
        posts = _make_multi(30, ["facebook", "telegram"], hours_ago=48)
        result = compute_market_sentiment(posts, reference_time=REF)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "market.recent_24h_share"

    def test_25pct_recency_no_signal(self):
        # 40 recent + 120 stale = 25 % recency (< 30 %)
        recent = _make_multi(20, ["facebook", "telegram"], hours_ago=1)
        stale = _make_multi(60, ["facebook", "telegram"], hours_ago=48)
        posts = recent + stale
        result = compute_market_sentiment(posts, reference_time=REF)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "market.recent_24h_share"
        assert result.reason.metrics["recent_24h_share"] == pytest.approx(
            40 / 160, abs=0.01
        )

    def test_exactly_30pct_recency_passes(self):
        # 30 recent + 70 stale = 30 % recency (border = passes)
        recent = _make_multi(15, ["facebook", "telegram"], hours_ago=1)
        stale = _make_multi(35, ["facebook", "telegram"], hours_ago=48)
        posts = recent + stale
        result = compute_market_sentiment(posts, reference_time=REF)
        # Should pass gate 3; must be SIGNAL or fail on a different gate
        if result.status == LayerStatus.NO_SIGNAL:
            assert result.reason.gate_failed != "market.recent_24h_share"

    def test_stale_reason_metrics(self):
        posts = _make_multi(30, ["facebook", "telegram"], hours_ago=48)
        result = compute_market_sentiment(posts, reference_time=REF)
        m = result.reason.metrics
        assert "recent_24h_share" in m
        assert "required" in m
        assert m["recent_24h_share"] == pytest.approx(0.0, abs=0.01)

    def test_unparseable_timestamps_are_stale(self):
        """Posts with bad timestamps must not contribute to recency numerator."""
        bad_ts_posts = [
            MarketDataPoint("not-a-date", "facebook", 0.2, 0.5),
            MarketDataPoint("", "telegram", 0.2, 0.5),
            MarketDataPoint("2025/06/15", "reddit", 0.2, 0.5),  # wrong separator
        ] * 20  # 60 posts total, 3 sources
        result = compute_market_sentiment(bad_ts_posts, reference_time=REF)
        assert result.status == LayerStatus.NO_SIGNAL
        # Could be gate 3 (stale) — recent_share = 0
        assert result.reason.gate_failed in (
            "market.n_total_posts",
            "market.n_distinct_sources",
            "market.recent_24h_share",
        )


# ---------------------------------------------------------------------------
# 4. SIGNAL path — all gates pass
# ---------------------------------------------------------------------------

class TestSignalPath:

    def _valid_posts(self, n_per_source: int = 30, score: float = 0.2) -> list[MarketDataPoint]:
        """Build a minimal valid post list (60 posts, 2 sources, all recent)."""
        return _make_multi(n_per_source, ["facebook", "telegram"], score=score)

    def test_signal_status(self):
        result = compute_market_sentiment(self._valid_posts(), reference_time=REF)
        assert result.status == LayerStatus.SIGNAL

    def test_signal_score_is_set(self):
        result = compute_market_sentiment(self._valid_posts(score=0.2), reference_time=REF)
        assert result.score is not None
        assert isinstance(result.score, float)

    def test_signal_reason_is_none(self):
        result = compute_market_sentiment(self._valid_posts(), reference_time=REF)
        assert result.reason is None

    def test_signal_score_matches_uniform_input(self):
        """With uniform score=0.25 and uniform weight=0.5, mean must be 0.25."""
        posts = _make_multi(30, ["facebook", "telegram"], score=0.25, weight=0.5)
        result = compute_market_sentiment(posts, reference_time=REF)
        assert result.status == LayerStatus.SIGNAL
        assert result.score == pytest.approx(0.25, abs=0.001)

    def test_confidence_in_bounds(self):
        result = compute_market_sentiment(self._valid_posts(), reference_time=REF)
        assert result.status == LayerStatus.SIGNAL
        assert 0.0 <= result.confidence <= 1.0

    def test_n_posts_reported(self):
        posts = self._valid_posts(n_per_source=30)
        result = compute_market_sentiment(posts, reference_time=REF)
        assert result.n_posts == 60

    def test_n_distinct_sources_reported(self):
        posts = _make_multi(25, ["facebook", "telegram", "reddit"])
        result = compute_market_sentiment(posts, reference_time=REF)
        assert result.n_distinct_sources == 3

    def test_volatility_mood_set_on_signal(self):
        result = compute_market_sentiment(self._valid_posts(), reference_time=REF)
        assert result.volatility_mood != VolatilityMood.NO_SIGNAL

    def test_regime_set_on_signal(self):
        result = compute_market_sentiment(self._valid_posts(score=0.2), reference_time=REF)
        assert result.regime != MarketRegime.NO_SIGNAL


# ---------------------------------------------------------------------------
# 5. Regime classification
# ---------------------------------------------------------------------------

class TestRegimeClassification:

    def _signal(self, score: float) -> "MarketSentiment":  # noqa: F821
        posts = _make_multi(30, ["facebook", "telegram"], score=score)
        return compute_market_sentiment(posts, reference_time=REF)

    @pytest.mark.parametrize("score,expected_regime", [
        (0.40, MarketRegime.EUPHORIA),
        (0.35, MarketRegime.EUPHORIA),   # exact boundary → EUPHORIA
        (0.20, MarketRegime.GREED),
        (0.15, MarketRegime.GREED),       # exact boundary → GREED
        (0.00, MarketRegime.NEUTRAL),
        (0.10, MarketRegime.NEUTRAL),
        (-0.10, MarketRegime.NEUTRAL),
        (-0.15, MarketRegime.FEAR),       # exact boundary → FEAR
        (-0.20, MarketRegime.FEAR),
        (-0.35, MarketRegime.PANIC),      # exact boundary → PANIC
        (-0.40, MarketRegime.PANIC),
    ])
    def test_regime_bands(self, score: float, expected_regime: MarketRegime):
        result = self._signal(score)
        assert result.status == LayerStatus.SIGNAL, (
            f"Expected SIGNAL for score={score} but got NO_SIGNAL: {result.reason}"
        )
        assert result.regime == expected_regime, (
            f"score={score}: expected {expected_regime}, got {result.regime}"
        )

    def test_all_five_regimes_reachable(self):
        regimes = set()
        for score in (0.40, 0.20, 0.00, -0.20, -0.40):
            r = self._signal(score)
            if r.status == LayerStatus.SIGNAL:
                regimes.add(r.regime)
        assert regimes == {
            MarketRegime.EUPHORIA,
            MarketRegime.GREED,
            MarketRegime.NEUTRAL,
            MarketRegime.FEAR,
            MarketRegime.PANIC,
        }


# ---------------------------------------------------------------------------
# 6. Volatility mood classification
# ---------------------------------------------------------------------------

class TestVolatilityMood:

    def _make_mixed(self, scores: list[float]) -> list[MarketDataPoint]:
        """Create one post per score from alternating platforms (facebook/telegram)."""
        platforms = ["facebook", "telegram"]
        posts = []
        for i, s in enumerate(scores):
            ts = (REF - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S%z")
            posts.append(
                MarketDataPoint(
                    timestamp=ts,
                    platform=platforms[i % 2],
                    sentiment_score=s,
                    weight=0.5,
                )
            )
        return posts

    def test_calm_mood_uniform_scores(self):
        # All scores identical → std=0 → CALM
        posts = _make_multi(30, ["facebook", "telegram"], score=0.2)
        result = compute_market_sentiment(posts, reference_time=REF)
        assert result.status == LayerStatus.SIGNAL
        assert result.volatility_mood == VolatilityMood.CALM

    def test_stressed_mood_bimodal_scores(self):
        # Half +0.9, half -0.9 → std ≈ 0.9 → STRESSED
        pos = [0.9] * 30
        neg = [-0.9] * 30
        posts = self._make_mixed(pos + neg)
        result = compute_market_sentiment(posts, reference_time=REF)
        assert result.status == LayerStatus.SIGNAL
        assert result.volatility_mood == VolatilityMood.STRESSED

    def test_elevated_mood_moderate_spread(self):
        # Std ≈ 0.42: mix of 0.5 and -0.3 across 60 posts
        scores = [0.5] * 30 + [-0.3] * 30
        posts = self._make_mixed(scores)
        result = compute_market_sentiment(posts, reference_time=REF)
        assert result.status == LayerStatus.SIGNAL
        # std = sqrt(((0.5-0.1)^2 * 30 + (-0.3-0.1)^2 * 30) / 60) ≈ sqrt(0.16) ≈ 0.40
        assert result.volatility_mood in (VolatilityMood.ELEVATED, VolatilityMood.STRESSED)


# ---------------------------------------------------------------------------
# 7. Confidence properties
# ---------------------------------------------------------------------------

class TestConfidence:

    def test_confidence_increases_with_post_count(self):
        base = _make_multi(25, ["facebook", "telegram"])  # 50 posts, minimal
        large = _make_multi(50, ["facebook", "telegram"])  # 100 posts
        r_base = compute_market_sentiment(base, reference_time=REF)
        r_large = compute_market_sentiment(large, reference_time=REF)
        assert r_base.status == r_large.status == LayerStatus.SIGNAL
        assert r_large.confidence >= r_base.confidence

    def test_confidence_increases_with_score_clarity(self):
        weak = _make_multi(30, ["facebook", "telegram"], score=0.05)
        strong = _make_multi(30, ["facebook", "telegram"], score=0.40)
        r_weak = compute_market_sentiment(weak, reference_time=REF)
        r_strong = compute_market_sentiment(strong, reference_time=REF)
        assert r_weak.status == r_strong.status == LayerStatus.SIGNAL
        assert r_strong.confidence >= r_weak.confidence

    def test_confidence_never_exceeds_one(self):
        # Max-effort: 200 posts, 3 sources, all recent, score = 1.0
        posts = _make_multi(67, ["facebook", "telegram", "reddit"], score=1.0)
        result = compute_market_sentiment(posts, reference_time=REF)
        assert result.status == LayerStatus.SIGNAL
        assert result.confidence <= 1.0

    def test_confidence_always_positive_on_signal(self):
        posts = _make_multi(25, ["facebook", "telegram"], score=0.0)
        result = compute_market_sentiment(posts, reference_time=REF)
        assert result.status == LayerStatus.SIGNAL
        assert result.confidence >= 0.0


# ---------------------------------------------------------------------------
# 8. Weighted average correctness
# ---------------------------------------------------------------------------

class TestWeightedAverage:

    def test_high_weight_post_dominates(self):
        """One post with weight=10.0 should dominate 9 posts with weight=0.1."""
        ts = (REF - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S%z")
        heavy = MarketDataPoint(ts, "facebook", 0.8, 10.0)
        lights = [MarketDataPoint(ts, "telegram", -0.5, 0.1)] * 49
        posts = [heavy] + lights
        result = compute_market_sentiment(posts, reference_time=REF)
        assert result.status == LayerStatus.SIGNAL
        # Weighted mean ≈ (0.8*10 + 49*(-0.5)*0.1) / (10 + 49*0.1) = (8-2.45)/(10+4.9) ≈ 0.37
        assert result.score > 0.0  # positive net weight wins
        assert result.regime == MarketRegime.EUPHORIA  # ≥ 0.35

    def test_zero_weight_posts_handled(self):
        """Posts with weight=0 must not cause division errors."""
        ts = (REF - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S%z")
        posts = (
            [MarketDataPoint(ts, "facebook", 0.3, 0.0)] * 30
            + [MarketDataPoint(ts, "telegram", 0.3, 0.5)] * 30
        )
        result = compute_market_sentiment(posts, reference_time=REF)
        assert result.status == LayerStatus.SIGNAL
        assert result.score == pytest.approx(0.3, abs=0.01)


# ---------------------------------------------------------------------------
# 9. Internal helper unit tests
# ---------------------------------------------------------------------------

class TestParseTimestamp:

    @pytest.mark.parametrize("ts", [
        "2025-06-15T11:00:00+00:00",
        "2025-06-15T11:00:00.000000+00:00",
        "2025-06-15T11:00:00Z",
        "2025-06-15T11:00:00.000000Z",
        "2025-06-15 11:00:00",
        "2025-06-15",
    ])
    def test_valid_formats_parse(self, ts: str):
        result = _parse_timestamp(ts)
        assert result is not None

    @pytest.mark.parametrize("ts", [
        "",
        "not-a-date",
        "15/06/2025",
        "2025/06/15",
        "June 15 2025",
    ])
    def test_invalid_formats_return_none(self, ts: str):
        result = _parse_timestamp(ts)
        assert result is None


class TestRecentShare:

    def test_all_recent(self):
        posts = _make(50, hours_ago=1)
        share = _recent_share(posts, REF)
        assert share == pytest.approx(1.0, abs=0.01)

    def test_all_stale(self):
        posts = _make(50, hours_ago=48)
        share = _recent_share(posts, REF)
        assert share == pytest.approx(0.0, abs=0.01)

    def test_half_recent(self):
        recent = _make(25, hours_ago=1)
        stale = _make(25, hours_ago=48)
        share = _recent_share(recent + stale, REF)
        assert share == pytest.approx(0.5, abs=0.01)

    def test_empty_posts_returns_zero(self):
        assert _recent_share([], REF) == 0.0

    def test_unparseable_timestamps_treated_as_stale(self):
        bad = [MarketDataPoint("bad-ts", "facebook", 0.2, 0.5)] * 25
        recent = _make(25, hours_ago=1)
        share = _recent_share(bad + recent, REF)
        assert share == pytest.approx(0.5, abs=0.01)


class TestWeightedStats:

    def test_empty_returns_zeros(self):
        mean, std = _weighted_stats([])
        assert mean == 0.0
        assert std == 0.0

    def test_uniform_score_zero_std(self):
        posts = _make(50, score=0.3, weight=0.5)
        mean, std = _weighted_stats(posts)
        assert mean == pytest.approx(0.3, abs=0.001)
        assert std == pytest.approx(0.0, abs=0.001)

    def test_mixed_scores_correct_mean(self):
        ts = _RECENT_TS
        posts = [
            MarketDataPoint(ts, "facebook", 1.0, 1.0),
            MarketDataPoint(ts, "telegram", -1.0, 1.0),
        ]
        mean, std = _weighted_stats(posts)
        assert mean == pytest.approx(0.0, abs=0.001)
        assert std == pytest.approx(1.0, abs=0.001)

    def test_weighted_mean_biased_toward_heavy_post(self):
        ts = _RECENT_TS
        posts = [
            MarketDataPoint(ts, "facebook", 1.0, 9.0),
            MarketDataPoint(ts, "telegram", 0.0, 1.0),
        ]
        mean, _ = _weighted_stats(posts)
        assert mean == pytest.approx(0.9, abs=0.001)


class TestRegimeFromScore:

    @pytest.mark.parametrize("score,expected", [
        (1.0, MarketRegime.EUPHORIA),
        (0.35, MarketRegime.EUPHORIA),
        (0.34, MarketRegime.GREED),
        (0.15, MarketRegime.GREED),
        (0.14, MarketRegime.NEUTRAL),
        (0.0, MarketRegime.NEUTRAL),
        (-0.14, MarketRegime.NEUTRAL),
        (-0.15, MarketRegime.FEAR),
        (-0.34, MarketRegime.FEAR),
        (-0.35, MarketRegime.PANIC),
        (-1.0, MarketRegime.PANIC),
    ])
    def test_bands(self, score: float, expected: MarketRegime):
        assert _regime_from_score(score) == expected


class TestVolatilityFromStd:

    @pytest.mark.parametrize("std,expected", [
        (0.0, VolatilityMood.CALM),
        (0.34, VolatilityMood.CALM),
        (0.35, VolatilityMood.ELEVATED),
        (0.54, VolatilityMood.ELEVATED),
        (0.55, VolatilityMood.STRESSED),
        (1.0, VolatilityMood.STRESSED),
    ])
    def test_bands(self, std: float, expected: VolatilityMood):
        assert _volatility_from_std(std) == expected


# ---------------------------------------------------------------------------
# 10. Canonical log-string format (audit trail)
# ---------------------------------------------------------------------------

class TestLogFormat:

    def test_gate1_log_str_format(self):
        posts = _make(10)
        result = compute_market_sentiment(posts, reference_time=REF)
        log_str = result.reason.to_log_str()
        assert log_str.startswith("NO_SIGNAL:")
        assert "gate=market.n_total_posts" in log_str
        assert "n_posts=10" in log_str

    def test_gate2_log_str_format(self):
        posts = _make(60, platform="facebook")
        result = compute_market_sentiment(posts, reference_time=REF)
        log_str = result.reason.to_log_str()
        assert "gate=market.n_distinct_sources" in log_str
        assert "n_distinct_sources=1" in log_str

    def test_gate3_log_str_format(self):
        posts = _make_multi(30, ["facebook", "telegram"], hours_ago=48)
        result = compute_market_sentiment(posts, reference_time=REF)
        assert result.reason.gate_failed == "market.recent_24h_share"
        log_str = result.reason.to_log_str()
        assert "gate=market.recent_24h_share" in log_str
        assert "recent_24h_share=0.0" in log_str
