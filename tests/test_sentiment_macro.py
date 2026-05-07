"""Tests for Layer A0 MacroSentiment aggregator (tradingagents/sentiment/macro.py).

Test inventory (14 classes, ~65 tests):
  TestGate1SourceCredibility     — posts that fail the credibility gate
  TestGate2Corroboration         — credible posts that fail corroboration
  TestGate3HalfLife              — corroborated but expired events
  TestCompositeRegime            — RISK_OFF/RISK_ON/NEUTRAL dominance rules
  TestSourceCredibilityMapping   — domain → SourceCredibility enum
  TestNormalizeDomain            — scheme/www/path/port stripping
  TestHalfLifeByCategory         — half-lives from config match contracts
  TestCorroborationWindowBoundary — exactly-48-h edge cases
  TestEventFields                — MacroEvent field correctness
  TestMagnitudeFromScore         — magnitude band assignment
  TestConfidenceFormula          — event_confidence formula values
  TestContractInvariants         — NO_SIGNAL/SIGNAL invariant enforcement
  TestMultipleConcurrentEvents   — multiple categories simultaneously active
  TestAuditStrings               — canonical NO_SIGNAL log string format
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.sentiment.contracts import (
    MacroCategory,
    MacroDirection,
    MacroMagnitude,
    NoSignalReason,
    SourceCredibility,
)
from tradingagents.sentiment.macro import (
    MacroDataPoint,
    _credibility_weight,
    _event_confidence,
    _find_corroboration,
    _magnitude_from_score,
    _normalize_domain,
    _source_credibility,
    compute_macro_sentiment,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REF = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

# 10 h ago — well within any half-life
_T_RECENT = (_REF - timedelta(hours=10)).isoformat()
# 1 h ago
_T_VERY_RECENT = (_REF - timedelta(hours=1)).isoformat()
# 20 h ago (still within 48-h window from T_RECENT)
_T_SECOND = (_REF - timedelta(hours=20)).isoformat()


def _p(
    source_domain: str,
    direction: str = "RISK_OFF",
    category: str = "RATE_DECISION",
    timestamp: str = _T_RECENT,
    sentiment_score: float = -0.6,
    weight: float = 1.0,
    headline: str = "CBE raises rates",
    post_id: str = "",
    url: str = "",
) -> MacroDataPoint:
    """Convenience factory — valid credible macro post."""
    return MacroDataPoint(
        timestamp=timestamp,
        source_domain=source_domain,
        headline=headline,
        category=category,
        direction=direction,
        sentiment_score=sentiment_score,
        weight=weight,
        post_id=post_id,
        url=url,
    )


def _two_source_posts(
    domain_a: str = "reuters.com",
    domain_b: str = "bloomberg.com",
    direction: str = "RISK_OFF",
    category: str = "RATE_DECISION",
    hours_apart: float = 10.0,
) -> list[MacroDataPoint]:
    """Return two credible posts from different domains within the same category."""
    t_a = (_REF - timedelta(hours=20)).isoformat()
    t_b = (_REF - timedelta(hours=20 - hours_apart)).isoformat()
    return [
        _p(domain_a, direction=direction, category=category, timestamp=t_a),
        _p(domain_b, direction=direction, category=category, timestamp=t_b),
    ]


# ---------------------------------------------------------------------------
# 1. Gate 1 — source credibility
# ---------------------------------------------------------------------------


class TestGate1SourceCredibility:
    def test_empty_posts_returns_no_signal(self):
        result = compute_macro_sentiment([], reference_time=_REF)
        assert result.composite_regime == MacroDirection.NO_SIGNAL

    def test_empty_posts_reason_gate(self):
        result = compute_macro_sentiment([], reference_time=_REF)
        assert result.reason is not None
        assert result.reason.gate_failed == "macro.source_credibility"

    def test_only_rumor_sources_no_signal(self):
        posts = [
            _p("randomsite.com"),
            _p("unknown-blog.net"),
        ]
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime == MacroDirection.NO_SIGNAL
        assert result.reason.gate_failed == "macro.source_credibility"

    def test_empty_domain_treated_as_rumor(self):
        result = compute_macro_sentiment(
            [_p("")], reference_time=_REF
        )
        assert result.composite_regime == MacroDirection.NO_SIGNAL
        assert result.reason.gate_failed == "macro.source_credibility"

    def test_mixed_rumor_and_tier2_passes_gate1(self):
        posts = [
            _p("randomsite.com"),
            _p("almalnews.com"),  # TIER2
            _p("enterprise.press"),  # TIER2 — provides second source
        ]
        result = compute_macro_sentiment(posts, reference_time=_REF)
        # Gate 1 passes; Gate 2 may still fail but reason is not gate 1
        assert result.reason is None or result.reason.gate_failed != "macro.source_credibility"

    def test_gate1_metrics_n_total_posts(self):
        result = compute_macro_sentiment(
            [_p("badsite.xyz"), _p("another-bad.xyz")],
            reference_time=_REF,
        )
        assert result.reason.metrics["n_total_posts"] == 2

    def test_gate1_metrics_n_credible_zero(self):
        result = compute_macro_sentiment([_p("badsite.xyz")], reference_time=_REF)
        assert result.reason.metrics["n_credible_posts"] == 0


# ---------------------------------------------------------------------------
# 2. Gate 2 — corroboration
# ---------------------------------------------------------------------------


class TestGate2Corroboration:
    def test_single_credible_source_no_signal(self):
        posts = [_p("reuters.com")]
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime == MacroDirection.NO_SIGNAL
        assert result.reason.gate_failed == "macro.corroborating_sources"

    def test_two_posts_same_domain_no_signal(self):
        """Two Reuters posts ≠ two distinct sources."""
        posts = [
            _p("reuters.com", timestamp=_T_RECENT),
            _p("reuters.com", timestamp=_T_VERY_RECENT),
        ]
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime == MacroDirection.NO_SIGNAL
        assert result.reason.gate_failed == "macro.corroborating_sources"

    def test_two_credible_sources_far_apart_no_signal(self):
        """Posts from 2 sources but >48 h apart — window misses."""
        t_a = (_REF - timedelta(hours=60)).isoformat()  # 60 h ago
        t_b = (_REF - timedelta(hours=5)).isoformat()   # 5 h ago
        posts = [
            _p("reuters.com", timestamp=t_a),
            _p("bloomberg.com", timestamp=t_b),
        ]
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime == MacroDirection.NO_SIGNAL
        assert result.reason.gate_failed == "macro.corroborating_sources"

    def test_two_credible_sources_within_window_signal(self):
        posts = _two_source_posts()
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime != MacroDirection.NO_SIGNAL

    def test_only_no_signal_direction_posts_fails_gate2(self):
        posts = [
            _p("reuters.com", direction="NO_SIGNAL"),
            _p("bloomberg.com", direction="NO_SIGNAL"),
        ]
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime == MacroDirection.NO_SIGNAL
        assert result.reason.gate_failed == "macro.corroborating_sources"

    def test_only_none_category_posts_fails_gate2(self):
        posts = [
            _p("reuters.com", category="NONE"),
            _p("bloomberg.com", category="NONE"),
        ]
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime == MacroDirection.NO_SIGNAL
        assert result.reason.gate_failed == "macro.corroborating_sources"

    def test_unparseable_timestamps_fails_gate2(self):
        posts = [
            _p("reuters.com", timestamp="not-a-date"),
            _p("bloomberg.com", timestamp="also-bad"),
        ]
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime == MacroDirection.NO_SIGNAL
        assert result.reason.gate_failed == "macro.corroborating_sources"

    def test_gate2_metrics_n_credible_posts(self):
        result = compute_macro_sentiment([_p("reuters.com")], reference_time=_REF)
        assert result.reason.metrics["n_credible_posts"] == 1

    def test_gate2_metrics_min_corroborating_sources(self):
        result = compute_macro_sentiment([_p("reuters.com")], reference_time=_REF)
        assert result.reason.metrics["min_corroborating_sources"] == 2


# ---------------------------------------------------------------------------
# 3. Gate 3 — half-life expiry
# ---------------------------------------------------------------------------


class TestGate3HalfLife:
    def _expired_posts(self, category: str, half_life_hours: int) -> list[MacroDataPoint]:
        """Corroborated posts detected beyond their half-life."""
        t_a = (_REF - timedelta(hours=half_life_hours + 10)).isoformat()
        t_b = (_REF - timedelta(hours=half_life_hours + 5)).isoformat()
        return [
            _p("reuters.com", category=category, timestamp=t_a),
            _p("bloomberg.com", category=category, timestamp=t_b),
        ]

    def _fresh_posts(self, category: str, half_life_hours: int) -> list[MacroDataPoint]:
        """Corroborated posts well within their half-life."""
        t_a = (_REF - timedelta(hours=half_life_hours - 20)).isoformat()
        t_b = (_REF - timedelta(hours=half_life_hours - 15)).isoformat()
        return [
            _p("reuters.com", category=category, timestamp=t_a),
            _p("bloomberg.com", category=category, timestamp=t_b),
        ]

    def test_rate_decision_expired_no_signal(self):
        posts = self._expired_posts("RATE_DECISION", 120)
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime == MacroDirection.NO_SIGNAL
        assert result.reason.gate_failed == "macro.half_life_expired"

    def test_rate_decision_fresh_signal(self):
        posts = self._fresh_posts("RATE_DECISION", 120)
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime != MacroDirection.NO_SIGNAL

    def test_geopolitical_expired_no_signal(self):
        # GEOPOLITICAL half-life = 48 h
        posts = self._expired_posts("GEOPOLITICAL", 48)
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime == MacroDirection.NO_SIGNAL
        assert result.reason.gate_failed == "macro.half_life_expired"

    def test_egp_devaluation_fresh_signal(self):
        # EGP_DEVALUATION half-life = 240 h — very long-lived
        posts = self._fresh_posts("EGP_DEVALUATION", 240)
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime != MacroDirection.NO_SIGNAL

    def test_mixed_expired_and_fresh_returns_signal(self):
        """Expired GEOPOLITICAL + fresh RATE_DECISION → fresh event active."""
        expired = self._expired_posts("GEOPOLITICAL", 48)
        fresh = self._fresh_posts("RATE_DECISION", 120)
        result = compute_macro_sentiment(expired + fresh, reference_time=_REF)
        assert result.composite_regime != MacroDirection.NO_SIGNAL
        categories = {e.category for e in result.active_events}
        assert MacroCategory.RATE_DECISION in categories
        assert MacroCategory.GEOPOLITICAL not in categories

    def test_gate3_metrics_n_corroborated(self):
        posts = self._expired_posts("RATE_DECISION", 120)
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.reason.metrics["n_corroborated_events"] >= 1
        assert result.reason.metrics["n_active_events"] == 0

    def test_exactly_at_boundary_expired(self):
        """age_hours == half_life → strictly > → expired."""
        half_life = 120  # RATE_DECISION
        t_a = (_REF - timedelta(hours=half_life)).isoformat()  # exactly half_life old
        t_b = (_REF - timedelta(hours=half_life - 5)).isoformat()
        posts = [
            _p("reuters.com", category="RATE_DECISION", timestamp=t_a),
            _p("bloomberg.com", category="RATE_DECISION", timestamp=t_b),
        ]
        result = compute_macro_sentiment(posts, reference_time=_REF)
        # age == half_life: NOT strictly > → should be ACTIVE (not expired)
        # This validates the > (strictly greater) boundary semantics
        assert result.composite_regime != MacroDirection.NO_SIGNAL


# ---------------------------------------------------------------------------
# 4. Composite regime rules
# ---------------------------------------------------------------------------


class TestCompositeRegime:
    def _posts_for_direction(
        self,
        direction: str,
        category: str = "RATE_DECISION",
        sentiment_score: float = -0.5,
    ) -> list[MacroDataPoint]:
        return _two_source_posts(direction=direction, category=category)

    def test_risk_off_only(self):
        posts = self._posts_for_direction("RISK_OFF")
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime == MacroDirection.RISK_OFF

    def test_risk_on_only(self):
        posts = _two_source_posts(
            domain_a="reuters.com",
            domain_b="bloomberg.com",
            direction="RISK_ON",
            category="IMF_PROGRAM",
        )
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime == MacroDirection.RISK_ON

    def test_neutral_only(self):
        posts = _two_source_posts(direction="NEUTRAL", category="INFLATION_PRINT")
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime == MacroDirection.NEUTRAL

    def test_risk_off_dominates_risk_on(self):
        risk_off = _two_source_posts(direction="RISK_OFF", category="RATE_DECISION")
        risk_on = _two_source_posts(
            domain_a="almalnews.com",
            domain_b="enterprise.press",
            direction="RISK_ON",
            category="IMF_PROGRAM",
        )
        result = compute_macro_sentiment(risk_off + risk_on, reference_time=_REF)
        assert result.composite_regime == MacroDirection.RISK_OFF

    def test_risk_off_dominates_neutral(self):
        risk_off = _two_source_posts(direction="RISK_OFF", category="RATE_DECISION")
        neutral = _two_source_posts(
            domain_a="almalnews.com",
            domain_b="enterprise.press",
            direction="NEUTRAL",
            category="INFLATION_PRINT",
        )
        result = compute_macro_sentiment(risk_off + neutral, reference_time=_REF)
        assert result.composite_regime == MacroDirection.RISK_OFF

    def test_risk_on_dominates_neutral(self):
        risk_on = _two_source_posts(direction="RISK_ON", category="IMF_PROGRAM")
        neutral = _two_source_posts(
            domain_a="almalnews.com",
            domain_b="enterprise.press",
            direction="NEUTRAL",
            category="INFLATION_PRINT",
        )
        result = compute_macro_sentiment(risk_on + neutral, reference_time=_REF)
        assert result.composite_regime == MacroDirection.RISK_ON


# ---------------------------------------------------------------------------
# 5. Source credibility mapping
# ---------------------------------------------------------------------------


class TestSourceCredibilityMapping:
    @pytest.mark.parametrize(
        "domain,expected",
        [
            ("cbe.org.eg", SourceCredibility.OFFICIAL),
            ("mof.gov.eg", SourceCredibility.OFFICIAL),
            ("fra.gov.eg", SourceCredibility.OFFICIAL),
            ("egx.com.eg", SourceCredibility.OFFICIAL),
            ("reuters.com", SourceCredibility.TIER1_NEWS),
            ("bloomberg.com", SourceCredibility.TIER1_NEWS),
            ("mubasher.info", SourceCredibility.TIER1_NEWS),
            ("enterprise.press", SourceCredibility.TIER2_NEWS),
            ("almalnews.com", SourceCredibility.TIER2_NEWS),
            ("dailynewsegypt.com", SourceCredibility.TIER2_NEWS),
            ("alborsaanews.com", SourceCredibility.TIER2_NEWS),
            ("randomsite.com", SourceCredibility.RUMOR),
            ("unknown-blog.net", SourceCredibility.RUMOR),
            ("", SourceCredibility.RUMOR),
        ],
    )
    def test_mapping(self, domain, expected):
        assert _source_credibility(domain) == expected


# ---------------------------------------------------------------------------
# 6. Domain normalisation
# ---------------------------------------------------------------------------


class TestNormalizeDomain:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("reuters.com", "reuters.com"),
            ("www.reuters.com", "reuters.com"),
            ("https://reuters.com", "reuters.com"),
            ("https://www.bloomberg.com", "bloomberg.com"),
            ("http://www.cbe.org.eg", "cbe.org.eg"),
            ("https://reuters.com/news/article", "reuters.com"),
            ("reuters.com:443", "reuters.com"),
            ("REUTERS.COM", "reuters.com"),
            ("  bloomberg.com  ", "bloomberg.com"),
        ],
    )
    def test_normalize(self, raw, expected):
        assert _normalize_domain(raw) == expected

    def test_normalized_domain_matches_tier1(self):
        """www-prefixed domain resolves to the same credibility tier."""
        assert _source_credibility("www.reuters.com") == SourceCredibility.TIER1_NEWS

    def test_scheme_stripped_matches_official(self):
        assert _source_credibility("https://cbe.org.eg") == SourceCredibility.OFFICIAL


# ---------------------------------------------------------------------------
# 7. Half-life by category
# ---------------------------------------------------------------------------


class TestHalfLifeByCategory:
    @pytest.mark.parametrize(
        "category,expected_half_life",
        [
            ("RATE_DECISION", 120),
            ("EGP_DEVALUATION", 240),
            ("IMF_PROGRAM", 168),
            ("INFLATION_PRINT", 72),
            ("TAX_REGULATION", 168),
            ("GEOPOLITICAL", 48),
            ("COMMODITY_SHOCK", 72),
        ],
    )
    def test_half_life_in_active_event(self, category, expected_half_life):
        t_a = (_REF - timedelta(hours=10)).isoformat()
        t_b = (_REF - timedelta(hours=5)).isoformat()
        posts = [
            _p("reuters.com", category=category, timestamp=t_a),
            _p("bloomberg.com", category=category, timestamp=t_b),
        ]
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime != MacroDirection.NO_SIGNAL
        event = next(e for e in result.active_events if e.category.value == category)
        assert event.half_life_hours == expected_half_life


# ---------------------------------------------------------------------------
# 8. Corroboration window boundary
# ---------------------------------------------------------------------------


class TestCorroborationWindowBoundary:
    def test_exactly_48h_apart_corroborates(self):
        """Posts separated by exactly 48 h are inside the window (<=)."""
        t_a = (_REF - timedelta(hours=50)).isoformat()  # anchor
        t_b = (_REF - timedelta(hours=2)).isoformat()   # 48 h after t_a
        posts = [
            _p("reuters.com", timestamp=t_a),
            _p("bloomberg.com", timestamp=t_b),
        ]
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime != MacroDirection.NO_SIGNAL

    def test_just_over_48h_apart_no_corroboration(self):
        """Posts separated by >48 h are outside the window."""
        t_a = (_REF - timedelta(hours=55)).isoformat()  # anchor
        t_b = (_REF - timedelta(hours=2)).isoformat()   # 53 h after t_a → outside
        posts = [
            _p("reuters.com", timestamp=t_a),
            _p("bloomberg.com", timestamp=t_b),
        ]
        # Gate 2 fails — no 48-h window with ≥2 distinct sources
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime == MacroDirection.NO_SIGNAL
        assert result.reason.gate_failed == "macro.corroborating_sources"

    def test_three_sources_one_outside_window_still_corroborates(self):
        """First two sources within 48 h are sufficient; third doesn't matter."""
        t_a = (_REF - timedelta(hours=30)).isoformat()
        t_b = (_REF - timedelta(hours=20)).isoformat()  # 10 h after t_a
        t_c = (_REF - timedelta(hours=80)).isoformat()  # 50 h before t_a
        posts = [
            _p("reuters.com", timestamp=t_a),
            _p("bloomberg.com", timestamp=t_b),
            _p("almalnews.com", timestamp=t_c),
        ]
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime != MacroDirection.NO_SIGNAL

    def _find_result(self, parsed, min_s, window_h):
        return _find_corroboration(parsed, min_s, window_h)

    def test_find_corroboration_empty_returns_none(self):
        detected, sources = _find_corroboration([], 2, 48)
        assert detected is None
        assert sources == set()

    def test_find_corroboration_single_returns_none(self):
        dt = _REF - timedelta(hours=5)
        post = _p("reuters.com")
        detected, sources = _find_corroboration([(dt, post)], 2, 48)
        assert detected is None


# ---------------------------------------------------------------------------
# 9. MacroEvent field correctness
# ---------------------------------------------------------------------------


class TestEventFields:
    def _basic_result(self, direction: str = "RISK_OFF") -> object:
        t_a = (_REF - timedelta(hours=10)).isoformat()
        t_b = (_REF - timedelta(hours=5)).isoformat()
        posts = [
            _p("cbe.org.eg", direction=direction, timestamp=t_a, headline="CBE raises rates"),
            _p("reuters.com", direction=direction, timestamp=t_b, headline="Egypt raises rates"),
        ]
        return compute_macro_sentiment(posts, reference_time=_REF)

    def test_category_correct(self):
        result = self._basic_result()
        assert result.active_events[0].category == MacroCategory.RATE_DECISION

    def test_direction_correct(self):
        result = self._basic_result("RISK_OFF")
        assert result.active_events[0].direction == MacroDirection.RISK_OFF

    def test_headline_from_most_credible_source(self):
        """OFFICIAL (cbe.org.eg) outranks TIER1 (reuters.com) → CBE headline wins."""
        result = self._basic_result()
        assert result.active_events[0].headline == "CBE raises rates"

    def test_detected_at_is_earliest_corroborating_timestamp(self):
        t_a = _REF - timedelta(hours=10)
        t_b = _REF - timedelta(hours=5)
        posts = [
            _p("cbe.org.eg", timestamp=t_a.isoformat()),
            _p("reuters.com", timestamp=t_b.isoformat()),
        ]
        result = compute_macro_sentiment(posts, reference_time=_REF)
        event = result.active_events[0]
        assert event.detected_at == t_a

    def test_evidence_has_one_ref_per_corroborating_source(self):
        result = self._basic_result()
        event = result.active_events[0]
        sources = {ref.source for ref in event.evidence}
        assert len(sources) == 2
        assert "cbe.org.eg" in sources
        assert "reuters.com" in sources

    def test_official_source_credibility_on_event(self):
        result = self._basic_result()
        assert result.active_events[0].source_credibility == SourceCredibility.OFFICIAL

    def test_tier1_source_credibility_when_no_official(self):
        posts = _two_source_posts("reuters.com", "bloomberg.com")
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.active_events[0].source_credibility == SourceCredibility.TIER1_NEWS

    def test_tier2_source_credibility_when_only_tier2(self):
        posts = _two_source_posts("almalnews.com", "enterprise.press")
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.active_events[0].source_credibility == SourceCredibility.TIER2_NEWS

    def test_half_life_hours_on_event(self):
        result = self._basic_result()
        # RATE_DECISION half-life = 120 h
        assert result.active_events[0].half_life_hours == 120

    def test_evidence_post_ids_non_empty(self):
        result = self._basic_result()
        for ref in result.active_events[0].evidence:
            assert ref.post_id  # non-empty string

    def test_evidence_entity_confidence_bounds(self):
        result = self._basic_result()
        for ref in result.active_events[0].evidence:
            assert 0.0 <= ref.entity_confidence <= 1.0


# ---------------------------------------------------------------------------
# 10. Magnitude from score
# ---------------------------------------------------------------------------


class TestMagnitudeFromScore:
    @pytest.mark.parametrize(
        "score,expected",
        [
            (0.0, MacroMagnitude.LOW),
            (0.10, MacroMagnitude.LOW),
            (0.24, MacroMagnitude.LOW),
            (0.25, MacroMagnitude.MEDIUM),
            (0.30, MacroMagnitude.MEDIUM),
            (0.49, MacroMagnitude.MEDIUM),
            (0.50, MacroMagnitude.HIGH),
            (0.75, MacroMagnitude.HIGH),
            (1.00, MacroMagnitude.HIGH),
            (-0.25, MacroMagnitude.MEDIUM),
            (-0.50, MacroMagnitude.HIGH),
            (-0.10, MacroMagnitude.LOW),
        ],
    )
    def test_magnitude_bands(self, score, expected):
        assert _magnitude_from_score(score) == expected


# ---------------------------------------------------------------------------
# 11. Confidence formula
# ---------------------------------------------------------------------------


class TestConfidenceFormula:
    def test_official_two_sources_confidence(self):
        # 0.60 × 1.0 (OFFICIAL) + 0.40 × (2 / 4) = 0.60 + 0.20 = 0.80
        conf = _event_confidence(
            n_corroborating_sources=2,
            best_cred=SourceCredibility.OFFICIAL,
            min_corroborating=2,
        )
        assert abs(conf - 0.80) < 1e-6

    def test_tier1_two_sources_confidence(self):
        # 0.60 × 0.9 + 0.40 × 0.5 = 0.54 + 0.20 = 0.74
        conf = _event_confidence(
            n_corroborating_sources=2,
            best_cred=SourceCredibility.TIER1_NEWS,
            min_corroborating=2,
        )
        assert abs(conf - 0.74) < 1e-6

    def test_tier2_two_sources_confidence(self):
        # 0.60 × 0.7 + 0.40 × 0.5 = 0.42 + 0.20 = 0.62
        conf = _event_confidence(
            n_corroborating_sources=2,
            best_cred=SourceCredibility.TIER2_NEWS,
            min_corroborating=2,
        )
        assert abs(conf - 0.62) < 1e-6

    def test_four_sources_saturates_corroboration(self):
        # n=4, min=2 → corroboration_conf = min(1.0, 4/4) = 1.0
        # 0.60 × 1.0 + 0.40 × 1.0 = 1.00
        conf = _event_confidence(
            n_corroborating_sources=4,
            best_cred=SourceCredibility.OFFICIAL,
            min_corroborating=2,
        )
        assert abs(conf - 1.0) < 1e-6

    def test_confidence_bounded_0_to_1(self):
        for n in range(1, 10):
            for cred in (SourceCredibility.OFFICIAL, SourceCredibility.TIER1_NEWS, SourceCredibility.TIER2_NEWS):
                conf = _event_confidence(n, cred, 2)
                assert 0.0 <= conf <= 1.0

    def test_credibility_weights(self):
        assert _credibility_weight(SourceCredibility.OFFICIAL) == 1.0
        assert _credibility_weight(SourceCredibility.TIER1_NEWS) == 0.9
        assert _credibility_weight(SourceCredibility.TIER2_NEWS) == 0.7
        assert _credibility_weight(SourceCredibility.RUMOR) == 0.0


# ---------------------------------------------------------------------------
# 12. Contract invariants
# ---------------------------------------------------------------------------


class TestContractInvariants:
    def test_no_signal_has_reason(self):
        result = compute_macro_sentiment([], reference_time=_REF)
        assert result.composite_regime == MacroDirection.NO_SIGNAL
        assert result.reason is not None

    def test_no_signal_active_events_empty(self):
        result = compute_macro_sentiment([], reference_time=_REF)
        assert result.active_events == []

    def test_signal_reason_is_none(self):
        posts = _two_source_posts()
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert result.composite_regime != MacroDirection.NO_SIGNAL
        assert result.reason is None

    def test_signal_active_events_non_empty(self):
        posts = _two_source_posts()
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert len(result.active_events) >= 1

    def test_event_confidence_in_bounds(self):
        posts = _two_source_posts()
        result = compute_macro_sentiment(posts, reference_time=_REF)
        for event in result.active_events:
            assert 0.0 <= event.confidence <= 1.0

    def test_event_half_life_positive(self):
        posts = _two_source_posts()
        result = compute_macro_sentiment(posts, reference_time=_REF)
        for event in result.active_events:
            assert event.half_life_hours > 0

    def test_no_signal_composite_requires_reason(self):
        """MacroSentiment invariant: NO_SIGNAL without reason raises ValueError."""
        from tradingagents.sentiment.contracts import MacroSentiment
        with pytest.raises(ValueError):
            MacroSentiment(composite_regime=MacroDirection.NO_SIGNAL, reason=None)

    def test_signal_composite_rejects_reason(self):
        """MacroSentiment invariant: non-NO_SIGNAL with reason raises ValueError."""
        from tradingagents.sentiment.contracts import MacroSentiment
        reason = NoSignalReason(
            gate_failed="test.gate",
            human_readable="test",
            metrics={},
        )
        with pytest.raises(ValueError):
            MacroSentiment(
                composite_regime=MacroDirection.RISK_OFF,
                reason=reason,
            )

    def test_result_is_frozen(self):
        posts = _two_source_posts()
        result = compute_macro_sentiment(posts, reference_time=_REF)
        with pytest.raises(Exception):
            result.composite_regime = MacroDirection.NO_SIGNAL  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 13. Multiple concurrent events
# ---------------------------------------------------------------------------


class TestMultipleConcurrentEvents:
    def test_two_categories_both_active(self):
        rate_posts = _two_source_posts(
            "reuters.com", "bloomberg.com",
            direction="RISK_OFF", category="RATE_DECISION",
        )
        imf_posts = _two_source_posts(
            "almalnews.com", "enterprise.press",
            direction="RISK_ON", category="IMF_PROGRAM",
        )
        result = compute_macro_sentiment(rate_posts + imf_posts, reference_time=_REF)
        assert len(result.active_events) == 2
        categories = {e.category for e in result.active_events}
        assert MacroCategory.RATE_DECISION in categories
        assert MacroCategory.IMF_PROGRAM in categories

    def test_two_categories_risk_off_dominates(self):
        rate_posts = _two_source_posts(direction="RISK_OFF", category="RATE_DECISION")
        imf_posts = _two_source_posts(
            "almalnews.com", "enterprise.press",
            direction="RISK_ON", category="IMF_PROGRAM",
        )
        result = compute_macro_sentiment(rate_posts + imf_posts, reference_time=_REF)
        assert result.composite_regime == MacroDirection.RISK_OFF

    def test_same_direction_different_categories_deduped_correctly(self):
        rate_posts = _two_source_posts(direction="RISK_OFF", category="RATE_DECISION")
        egp_posts = _two_source_posts(
            "almalnews.com", "enterprise.press",
            direction="RISK_OFF", category="EGP_DEVALUATION",
        )
        result = compute_macro_sentiment(rate_posts + egp_posts, reference_time=_REF)
        assert result.composite_regime == MacroDirection.RISK_OFF
        assert len(result.active_events) == 2

    def test_three_distinct_categories(self):
        posts = (
            _two_source_posts(direction="RISK_OFF", category="RATE_DECISION")
            + _two_source_posts(
                "almalnews.com", "enterprise.press",
                direction="NEUTRAL", category="INFLATION_PRINT",
            )
            + [
                _p("fra.gov.eg", category="TAX_REGULATION", direction="RISK_OFF",
                   timestamp=(_REF - timedelta(hours=8)).isoformat()),
                _p("dailynewsegypt.com", category="TAX_REGULATION", direction="RISK_OFF",
                   timestamp=(_REF - timedelta(hours=4)).isoformat()),
            ]
        )
        result = compute_macro_sentiment(posts, reference_time=_REF)
        assert len(result.active_events) == 3
        assert result.composite_regime == MacroDirection.RISK_OFF


# ---------------------------------------------------------------------------
# 14. Audit string format
# ---------------------------------------------------------------------------


class TestAuditStrings:
    def test_gate1_log_string_contains_gate_id(self):
        result = compute_macro_sentiment([], reference_time=_REF)
        log_str = result.reason.to_log_str()
        assert "macro.source_credibility" in log_str

    def test_gate2_log_string_contains_gate_id(self):
        result = compute_macro_sentiment([_p("reuters.com")], reference_time=_REF)
        log_str = result.reason.to_log_str()
        assert "macro.corroborating_sources" in log_str

    def test_gate3_log_string_contains_gate_id(self):
        t_a = (_REF - timedelta(hours=130)).isoformat()
        t_b = (_REF - timedelta(hours=125)).isoformat()
        posts = [
            _p("reuters.com", category="RATE_DECISION", timestamp=t_a),
            _p("bloomberg.com", category="RATE_DECISION", timestamp=t_b),
        ]
        result = compute_macro_sentiment(posts, reference_time=_REF)
        log_str = result.reason.to_log_str()
        assert "macro.half_life_expired" in log_str

    def test_canonical_format_starts_with_no_signal(self):
        result = compute_macro_sentiment([], reference_time=_REF)
        assert result.reason.to_log_str().startswith("NO_SIGNAL:")

    def test_canonical_format_contains_metrics(self):
        result = compute_macro_sentiment([], reference_time=_REF)
        log_str = result.reason.to_log_str()
        # Metrics are rendered as key=value pairs
        assert "n_total_posts=0" in log_str

    def test_str_dunder_equals_to_log_str(self):
        result = compute_macro_sentiment([], reference_time=_REF)
        assert str(result.reason) == result.reason.to_log_str()

    def test_default_reference_time_does_not_raise(self):
        """reference_time=None should default to UTC now without error."""
        # Use posts that will fail early (no credible source) so we don't
        # depend on system time for the half-life check.
        result = compute_macro_sentiment([_p("badsite.xyz")])
        assert result.composite_regime == MacroDirection.NO_SIGNAL
