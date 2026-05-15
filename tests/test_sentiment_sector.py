"""PR 4 test suite — Layer B SectorSentiment aggregator.

Coverage:
  - Gate 1: n_sector_posts < 10  → NO_SIGNAL (gate="sector.n_sector_posts")
  - Gate 2: n_distinct_days < 3  → NO_SIGNAL (gate="sector.n_distinct_days")
  - Gate 3: mean_entity_conf < 0.70 → NO_SIGNAL (gate="sector.agg_entity_confidence")
  - All gates pass → SIGNAL with score / confidence set
  - Gate ordering: volume checked first, then days, then entity confidence
  - All 6 SectorEnum values are reachable (produce SIGNAL with valid data)
  - Bilingual keyword matching: Arabic aliases → correct sector
  - Bilingual keyword matching: English keywords → correct sector
  - Score computation: higher-weight posts dominate
  - Confidence: always in [0, 1] for SIGNAL outputs
  - Contract invariants: SIGNAL has score and no reason; NO_SIGNAL has no score and reason
  - Boundary values: n=9 vs n=10; days=2 vs days=3; conf=0.699 vs 0.700
  - Log format: canonical NO_SIGNAL string propagated from reason
  - Empty post list → NO_SIGNAL (Gate 1 fails with n_posts=0)
  - Unparseable timestamps do not contribute to distinct-days count
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

import pytest

from tradingagents.sentiment.contracts import LayerStatus
from tradingagents.sentiment.sector import (
    SectorDataPoint,
    _count_distinct_days,
    _mean_entity_conf,
    _signal_confidence,
    _weighted_mean,
    classify_post_to_sector,
    compute_sector_sentiment,
)
from tradingagents.sentiment.taxonomy import SectorEnum

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# Reference date — tests build timestamps relative to this
_BASE = datetime(2025, 7, 10, 10, 0, 0, tzinfo=timezone.utc)


def _ts(days_ago: int = 0, hours_ago: int = 0) -> str:
    """Return an ISO-8601 timestamp offset from _BASE."""
    dt = _BASE - timedelta(days=days_ago, hours=hours_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")


def _make(
    n: int,
    *,
    days_spread: int = 5,
    score: float = 0.3,
    weight: float = 0.6,
    entity_conf: float = 0.85,
    platform: str = "facebook",
) -> list[SectorDataPoint]:
    """Build *n* SectorDataPoints spread across *days_spread* distinct calendar days.

    When n > days_spread, multiple posts share the same day (post i lands on
    day i % days_spread).
    """
    posts = []
    for i in range(n):
        day_offset = i % days_spread
        posts.append(
            SectorDataPoint(
                timestamp=_ts(days_ago=day_offset),
                platform=platform,
                sentiment_score=score,
                weight=weight,
                entity_confidence=entity_conf,
            )
        )
    return posts


def _valid(n: int = 12, *, sector: SectorEnum = SectorEnum.BANKS) -> list[SectorDataPoint]:
    """Return a valid post list that passes all three gates."""
    return _make(n, days_spread=5)


# ---------------------------------------------------------------------------
# Class 1 — Gate 1: insufficient post volume
# ---------------------------------------------------------------------------


class TestGate1PostVolume:
    def test_zero_posts_no_signal(self):
        result = compute_sector_sentiment(SectorEnum.BANKS, [])
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason is not None
        assert result.reason.gate_failed == "sector.n_sector_posts"
        assert result.score is None

    def test_nine_posts_no_signal(self):
        posts = _make(9, days_spread=5, entity_conf=0.90)
        result = compute_sector_sentiment(SectorEnum.BANKS, posts)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "sector.n_sector_posts"
        assert result.reason.metrics["n_posts"] == 9
        assert result.reason.metrics["required"] == 10

    def test_ten_posts_passes_gate1(self):
        posts = _make(10, days_spread=5, entity_conf=0.90)
        result = compute_sector_sentiment(SectorEnum.BANKS, posts)
        # Gate 1 passes; Gate 2 may pass depending on spread
        assert result.reason is None or result.reason.gate_failed != "sector.n_sector_posts"

    def test_gate1_includes_sector_label_in_metrics(self):
        posts = _make(3)
        result = compute_sector_sentiment(SectorEnum.TELECOM_TECH, posts)
        assert result.reason.metrics["sector"] == "telecom_tech"

    def test_n_posts_field_set_on_no_signal(self):
        posts = _make(7)
        result = compute_sector_sentiment(SectorEnum.INDUSTRY, posts)
        assert result.n_posts == 7


# ---------------------------------------------------------------------------
# Class 2 — Gate 2: insufficient distinct days
# ---------------------------------------------------------------------------


class TestGate2DistinctDays:
    def test_all_posts_same_day_no_signal(self):
        # 15 posts, all on day 0 → 1 distinct day
        posts = _make(15, days_spread=1, entity_conf=0.90)
        result = compute_sector_sentiment(SectorEnum.REAL_ESTATE, posts)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "sector.n_distinct_days"
        assert result.reason.metrics["n_distinct_days"] == 1

    def test_two_distinct_days_no_signal(self):
        posts = _make(12, days_spread=2, entity_conf=0.90)
        result = compute_sector_sentiment(SectorEnum.INDUSTRY, posts)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "sector.n_distinct_days"

    def test_three_distinct_days_passes_gate2(self):
        posts = _make(12, days_spread=3, entity_conf=0.90)
        result = compute_sector_sentiment(SectorEnum.INDUSTRY, posts)
        # Gate 2 passes; gate 3 checks entity conf
        assert result.reason is None or result.reason.gate_failed != "sector.n_distinct_days"

    def test_gate2_checked_after_gate1(self):
        # 5 posts on 5 distinct days — Gate 1 (volume) fires first, not Gate 2
        posts = _make(5, days_spread=5)
        result = compute_sector_sentiment(SectorEnum.BANKS, posts)
        assert result.reason.gate_failed == "sector.n_sector_posts"

    def test_n_distinct_days_in_metrics(self):
        posts = _make(10, days_spread=2, entity_conf=0.90)
        result = compute_sector_sentiment(SectorEnum.FINANCIAL_SERVICES, posts)
        assert result.reason.gate_failed == "sector.n_distinct_days"
        assert result.reason.metrics["n_distinct_days"] == 2
        assert result.reason.metrics["required"] == 3

    def test_n_posts_and_days_set_on_gate2_no_signal(self):
        posts = _make(12, days_spread=2)
        result = compute_sector_sentiment(SectorEnum.FOOD_BEV, posts)
        assert result.n_posts == 12
        assert result.n_distinct_days == 2


# ---------------------------------------------------------------------------
# Class 3 — Gate 3: aggregate entity confidence
# ---------------------------------------------------------------------------


class TestGate3EntityConfidence:
    def test_low_entity_conf_no_signal(self):
        posts = _make(12, days_spread=5, entity_conf=0.60)
        result = compute_sector_sentiment(SectorEnum.BANKS, posts)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "sector.agg_entity_confidence"

    def test_exactly_069_fails(self):
        posts = _make(12, days_spread=5, entity_conf=0.69)
        result = compute_sector_sentiment(SectorEnum.BANKS, posts)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason.gate_failed == "sector.agg_entity_confidence"

    def test_exactly_070_passes_gate3(self):
        posts = _make(12, days_spread=5, entity_conf=0.70)
        result = compute_sector_sentiment(SectorEnum.BANKS, posts)
        # Gate 3 requires >=0.70; exactly 0.70 should pass
        assert result.status == LayerStatus.SIGNAL

    def test_mixed_conf_above_threshold_passes(self):
        high = _make(6, days_spread=3, entity_conf=0.95)
        low = _make(6, days_spread=3, entity_conf=0.55)
        # mean = (0.95*6 + 0.55*6)/12 = 0.75 → should pass
        posts = high + low
        # Reset days to get 5+ distinct days
        posts_respread: list[SectorDataPoint] = []
        for i, p in enumerate(posts):
            posts_respread.append(
                SectorDataPoint(
                    timestamp=_ts(days_ago=i % 5),
                    platform=p.platform,
                    sentiment_score=p.sentiment_score,
                    weight=p.weight,
                    entity_confidence=p.entity_confidence,
                )
            )
        result = compute_sector_sentiment(SectorEnum.INDUSTRY, posts_respread)
        assert result.status == LayerStatus.SIGNAL

    def test_entity_conf_metrics_propagated(self):
        posts = _make(12, days_spread=5, entity_conf=0.60)
        result = compute_sector_sentiment(SectorEnum.TELECOM_TECH, posts)
        assert "agg_entity_confidence" in result.reason.metrics
        assert result.reason.metrics["agg_entity_confidence"] == pytest.approx(0.6, abs=0.01)

    def test_gate3_checked_after_gate2(self):
        # ≥10 posts but only 2 distinct days + low conf → Gate 2 should fire first
        posts = _make(12, days_spread=2, entity_conf=0.50)
        result = compute_sector_sentiment(SectorEnum.BANKS, posts)
        assert result.reason.gate_failed == "sector.n_distinct_days"


# ---------------------------------------------------------------------------
# Class 4 — SIGNAL path: score, confidence, contract invariants
# ---------------------------------------------------------------------------


class TestSignalPath:
    def test_all_gates_pass_yields_signal(self):
        posts = _valid()
        result = compute_sector_sentiment(SectorEnum.BANKS, posts)
        assert result.status == LayerStatus.SIGNAL

    def test_signal_has_score(self):
        result = compute_sector_sentiment(SectorEnum.BANKS, _valid())
        assert result.score is not None

    def test_signal_has_no_reason(self):
        result = compute_sector_sentiment(SectorEnum.BANKS, _valid())
        assert result.reason is None

    def test_signal_score_in_range(self):
        result = compute_sector_sentiment(SectorEnum.BANKS, _valid())
        assert -1.0 <= result.score <= 1.0

    def test_signal_confidence_in_range(self):
        result = compute_sector_sentiment(SectorEnum.BANKS, _valid())
        assert 0.0 <= result.confidence <= 1.0

    def test_sector_label_on_signal(self):
        result = compute_sector_sentiment(SectorEnum.REAL_ESTATE, _valid())
        assert result.sector == "real_estate"

    def test_n_posts_and_days_on_signal(self):
        posts = _make(15, days_spread=5)
        result = compute_sector_sentiment(SectorEnum.INDUSTRY, posts)
        assert result.n_posts == 15
        assert result.n_distinct_days == 5

    def test_score_rounds_to_4dp(self):
        posts = _make(10, days_spread=5, score=0.123456789)
        result = compute_sector_sentiment(SectorEnum.BANKS, posts)
        if result.status == LayerStatus.SIGNAL:
            assert result.score == pytest.approx(0.1235, abs=1e-4)

    def test_negative_score_signal(self):
        posts = _make(12, days_spread=5, score=-0.40, entity_conf=0.80)
        result = compute_sector_sentiment(SectorEnum.FOOD_BEV, posts)
        assert result.status == LayerStatus.SIGNAL
        assert result.score < 0


# ---------------------------------------------------------------------------
# Class 5 — All 6 sectors produce SIGNAL with valid data
# ---------------------------------------------------------------------------


class TestAllSectorsReachable:
    @pytest.mark.parametrize(
        "sector",
        [
            SectorEnum.BANKS,
            SectorEnum.REAL_ESTATE,
            SectorEnum.INDUSTRY,
            SectorEnum.TELECOM_TECH,
            SectorEnum.FINANCIAL_SERVICES,
            SectorEnum.FOOD_BEV,
        ],
    )
    def test_signal_for_all_sectors(self, sector: SectorEnum):
        posts = _make(12, days_spread=5, entity_conf=0.85, score=0.2)
        result = compute_sector_sentiment(sector, posts)
        assert result.status == LayerStatus.SIGNAL
        assert result.sector == sector.value


# ---------------------------------------------------------------------------
# Class 6 — Score computation: weighted mean
# ---------------------------------------------------------------------------


class TestScoreComputation:
    def test_equal_weights_average(self):
        # All posts same score → result should equal that score
        posts = _make(10, days_spread=5, score=0.5, weight=1.0)
        result = compute_sector_sentiment(SectorEnum.BANKS, posts)
        assert result.score == pytest.approx(0.5, abs=1e-3)

    def test_higher_weight_dominates(self):
        # 8 posts with score=-0.5 weight=0.1 + 2 posts with score=+0.8 weight=5.0
        low_posts = [
            SectorDataPoint(
                timestamp=_ts(days_ago=i % 5),
                platform="facebook",
                sentiment_score=-0.5,
                weight=0.1,
                entity_confidence=0.85,
            )
            for i in range(8)
        ]
        high_posts = [
            SectorDataPoint(
                timestamp=_ts(days_ago=(8 + i) % 5),
                platform="telegram",
                sentiment_score=0.8,
                weight=5.0,
                entity_confidence=0.85,
            )
            for i in range(2)
        ]
        posts = low_posts + high_posts
        result = compute_sector_sentiment(SectorEnum.BANKS, posts)
        assert result.status == LayerStatus.SIGNAL
        # high-weight posts (score=+0.8) should pull mean positive
        assert result.score > 0.0

    def test_zero_weight_fallback_to_simple_mean(self):
        # All weights=0 → falls back to simple mean
        posts = [
            SectorDataPoint(
                timestamp=_ts(days_ago=i % 5),
                platform="facebook",
                sentiment_score=0.4,
                weight=0.0,
                entity_confidence=0.85,
            )
            for i in range(10)
        ]
        result = compute_sector_sentiment(SectorEnum.INDUSTRY, posts)
        if result.status == LayerStatus.SIGNAL:
            assert result.score == pytest.approx(0.4, abs=1e-3)


# ---------------------------------------------------------------------------
# Class 7 — Confidence properties
# ---------------------------------------------------------------------------


class TestConfidenceProperties:
    def test_confidence_increases_with_more_posts(self):
        small = compute_sector_sentiment(SectorEnum.BANKS, _make(10, days_spread=5))
        large = compute_sector_sentiment(SectorEnum.BANKS, _make(50, days_spread=5))
        if small.status == large.status == LayerStatus.SIGNAL:
            assert large.confidence >= small.confidence

    def test_confidence_increases_with_more_days(self):
        r3 = compute_sector_sentiment(SectorEnum.BANKS, _make(15, days_spread=3))
        r7 = compute_sector_sentiment(SectorEnum.BANKS, _make(15, days_spread=7))
        if r3.status == r7.status == LayerStatus.SIGNAL:
            assert r7.confidence >= r3.confidence

    def test_confidence_never_exceeds_1(self):
        posts = _make(200, days_spread=50, score=0.9)
        result = compute_sector_sentiment(SectorEnum.BANKS, posts)
        if result.status == LayerStatus.SIGNAL:
            assert result.confidence <= 1.0

    def test_confidence_never_below_0(self):
        posts = _make(10, days_spread=3, score=0.0)
        result = compute_sector_sentiment(SectorEnum.INDUSTRY, posts)
        if result.status == LayerStatus.SIGNAL:
            assert result.confidence >= 0.0


# ---------------------------------------------------------------------------
# Class 8 — Boundary values (exact threshold edges)
# ---------------------------------------------------------------------------


class TestBoundaryValues:
    def test_n_posts_exactly_9_fails(self):
        posts = _make(9, days_spread=5, entity_conf=0.90)
        result = compute_sector_sentiment(SectorEnum.BANKS, posts)
        assert result.reason.gate_failed == "sector.n_sector_posts"

    def test_n_posts_exactly_10_passes_gate1(self):
        posts = _make(10, days_spread=5, entity_conf=0.90)
        result = compute_sector_sentiment(SectorEnum.BANKS, posts)
        assert result.reason is None or result.reason.gate_failed != "sector.n_sector_posts"

    def test_days_exactly_2_fails(self):
        posts = _make(12, days_spread=2, entity_conf=0.90)
        result = compute_sector_sentiment(SectorEnum.TELECOM_TECH, posts)
        assert result.reason.gate_failed == "sector.n_distinct_days"

    def test_days_exactly_3_passes_gate2(self):
        posts = _make(12, days_spread=3, entity_conf=0.90)
        result = compute_sector_sentiment(SectorEnum.TELECOM_TECH, posts)
        assert result.reason is None or result.reason.gate_failed != "sector.n_distinct_days"

    def test_entity_conf_exactly_069_fails(self):
        posts = _make(12, days_spread=5, entity_conf=0.699)
        result = compute_sector_sentiment(SectorEnum.REAL_ESTATE, posts)
        assert result.reason.gate_failed == "sector.agg_entity_confidence"

    def test_entity_conf_exactly_070_passes(self):
        posts = _make(12, days_spread=5, entity_conf=0.700)
        result = compute_sector_sentiment(SectorEnum.REAL_ESTATE, posts)
        assert result.status == LayerStatus.SIGNAL


# ---------------------------------------------------------------------------
# Class 9 — Bilingual keyword matching (classify_post_to_sector)
# ---------------------------------------------------------------------------


class TestBilingualClassification:
    # Arabic alias matching
    def test_arabic_banks_alias(self):
        assert classify_post_to_sector("البنوك المصرية تحقق أرباحاً قياسية") == SectorEnum.BANKS

    def test_arabic_real_estate_alias(self):
        assert classify_post_to_sector("القطاع العقاري يشهد طفرة كبيرة") == SectorEnum.REAL_ESTATE

    def test_arabic_industry_alias(self):
        assert classify_post_to_sector("الأسمدة والكيماويات تعلن نتائجها") == SectorEnum.INDUSTRY

    def test_arabic_telecom_alias(self):
        assert classify_post_to_sector("قطاع الاتصالات ينمو بسرعة") == SectorEnum.TELECOM_TECH

    def test_arabic_financial_services_alias(self):
        assert classify_post_to_sector("الخدمات المالية تتوسع في السوق") == SectorEnum.FINANCIAL_SERVICES

    def test_arabic_food_bev_alias(self):
        assert classify_post_to_sector("شركات الأغذية والمشروبات الرائدة") == SectorEnum.FOOD_BEV

    # English keyword matching
    def test_english_banks(self):
        assert classify_post_to_sector("Egyptian banking sector reports record profits") == SectorEnum.BANKS

    def test_english_real_estate(self):
        assert classify_post_to_sector("Real estate developers see strong demand") == SectorEnum.REAL_ESTATE

    def test_english_industry(self):
        assert classify_post_to_sector("Industrial manufacturing output rises 8%") == SectorEnum.INDUSTRY

    def test_english_telecom(self):
        assert classify_post_to_sector("Telecom companies invest in 5G rollout") == SectorEnum.TELECOM_TECH

    def test_english_financial_services(self):
        assert classify_post_to_sector("brokerage firms expand Egyptian market coverage") == SectorEnum.FINANCIAL_SERVICES

    def test_english_food_bev(self):
        assert classify_post_to_sector("Food and beverage companies raise prices") == SectorEnum.FOOD_BEV

    def test_no_match_returns_none(self):
        assert classify_post_to_sector("السوق المصري ينتظر الانتخابات") is None

    def test_empty_text_returns_none(self):
        assert classify_post_to_sector("") is None

    def test_case_insensitive_english(self):
        assert classify_post_to_sector("BANKING sector is growing") == SectorEnum.BANKS


# ---------------------------------------------------------------------------
# Class 10 — Log format and NoSignalReason contract invariants
# ---------------------------------------------------------------------------


class TestContractInvariants:
    def test_no_signal_always_has_reason(self):
        posts = _make(5)
        result = compute_sector_sentiment(SectorEnum.BANKS, posts)
        assert result.status == LayerStatus.NO_SIGNAL
        assert result.reason is not None

    def test_signal_never_has_reason(self):
        result = compute_sector_sentiment(SectorEnum.BANKS, _valid())
        assert result.status == LayerStatus.SIGNAL
        assert result.reason is None

    def test_no_signal_score_is_none(self):
        posts = _make(5)
        result = compute_sector_sentiment(SectorEnum.BANKS, posts)
        assert result.score is None

    def test_signal_score_is_not_none(self):
        result = compute_sector_sentiment(SectorEnum.BANKS, _valid())
        assert result.score is not None

    def test_log_str_format_gate1(self):
        posts = _make(3)
        result = compute_sector_sentiment(SectorEnum.BANKS, posts)
        log_str = result.reason.to_log_str()
        assert log_str.startswith("NO_SIGNAL:")
        assert "sector.n_sector_posts" in log_str

    def test_log_str_format_gate2(self):
        posts = _make(12, days_spread=2)
        result = compute_sector_sentiment(SectorEnum.INDUSTRY, posts)
        assert "sector.n_distinct_days" in result.reason.to_log_str()

    def test_log_str_format_gate3(self):
        posts = _make(12, days_spread=5, entity_conf=0.5)
        result = compute_sector_sentiment(SectorEnum.REAL_ESTATE, posts)
        assert "sector.agg_entity_confidence" in result.reason.to_log_str()

    def test_sector_field_is_enum_value_string(self):
        result = compute_sector_sentiment(SectorEnum.FOOD_BEV, _valid())
        assert result.sector == "food_bev"


# ---------------------------------------------------------------------------
# Class 11 — Internal helper unit tests
# ---------------------------------------------------------------------------


class TestInternalHelpers:
    def test_count_distinct_days_three_days(self):
        posts = [
            SectorDataPoint(_ts(days_ago=0), "fb", 0.1, 0.5, 0.8),
            SectorDataPoint(_ts(days_ago=1), "fb", 0.2, 0.5, 0.8),
            SectorDataPoint(_ts(days_ago=2), "fb", 0.3, 0.5, 0.8),
        ]
        assert _count_distinct_days(posts) == 3

    def test_count_distinct_days_ignores_unparseable(self):
        posts = [
            SectorDataPoint(_ts(days_ago=0), "fb", 0.1, 0.5, 0.8),
            SectorDataPoint("not-a-date", "fb", 0.2, 0.5, 0.8),
            SectorDataPoint(_ts(days_ago=1), "fb", 0.3, 0.5, 0.8),
        ]
        # "not-a-date" contributes 0; other two are distinct days
        assert _count_distinct_days(posts) == 2

    def test_mean_entity_conf_uniform(self):
        posts = [
            SectorDataPoint(_ts(), "fb", 0.1, 0.5, 0.80),
            SectorDataPoint(_ts(), "fb", 0.1, 0.5, 0.90),
        ]
        assert _mean_entity_conf(posts) == pytest.approx(0.85)

    def test_mean_entity_conf_empty(self):
        assert _mean_entity_conf([]) == 0.0

    def test_weighted_mean_uniform(self):
        posts = _make(5, days_spread=5, score=0.4, weight=1.0)
        assert _weighted_mean(posts) == pytest.approx(0.4, abs=1e-6)

    def test_signal_confidence_at_floor(self):
        conf = _signal_confidence(n_posts=10, n_distinct_days=3, score_abs=0.0, min_posts=10, min_days=3)
        assert conf >= 0.0

    def test_signal_confidence_at_ceiling(self):
        conf = _signal_confidence(n_posts=1000, n_distinct_days=100, score_abs=1.0, min_posts=10, min_days=3)
        assert conf <= 1.0
