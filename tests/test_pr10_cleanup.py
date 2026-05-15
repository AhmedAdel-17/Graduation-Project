"""PR 10 test suite — Zombie source cleanup + calibration script.

Tests:
  1. Zombie sources are gone from the filesystem.
  2. `aggregator.py` no longer imports twitter_source.
  3. `PLATFORM_SOURCES` no longer contains "twitter".
  4. Calibration script pure functions (no I/O).
  5. Smoke-import: `tradingagents.sentiment` still importable.

50 tests across 7 test classes.  No LLM, no I/O beyond filesystem checks.
"""

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

# ── repo root ──────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_SCRIPTS))


# ── helpers ────────────────────────────────────────────────────────────────

def _results_json(
    per_stock: Dict[str, Any],
    stamp: str = "20260502_120000",
) -> str:
    """Return JSON string mimicking a pipeline_v2 results file."""
    return json.dumps({
        "timestamp": f"2026-05-02T12:00:00Z",
        "market_sentiment": {"score": 0.1, "regime": "NEUTRAL"},
        "per_stock_sentiment": per_stock,
    })


def _make_post(
    entity_conf: float = 0.90,
    author: str = "user1",
    source: str = "telegram",
    spam: bool = False,
    timestamp: str = "2026-05-01T10:00:00",
) -> Dict[str, Any]:
    return {
        "entity_conf": entity_conf,
        "author": author,
        "source": source,
        "spam": spam,
        "timestamp": timestamp,
    }


# ── 1. Zombie file deletion ─────────────────────────────────────────────────

class TestZombieSourcesDeleted:
    _V2_SOURCES = _ROOT / "scripts" / "twitter_pipeline" / "v2" / "sources"
    _DATAFLOWS  = _ROOT / "tradingagents" / "dataflows" / "social_media_sources"

    def test_twitter_authed_deleted(self):
        assert not (self._V2_SOURCES / "twitter_authed.py").exists(), \
            "twitter_authed.py should have been deleted in PR 10"

    def test_facebook_groups_deleted(self):
        assert not (self._V2_SOURCES / "facebook_groups.py").exists(), \
            "facebook_groups.py should have been deleted in PR 10"

    def test_mubasher_news_deleted(self):
        assert not (self._V2_SOURCES / "mubasher_news.py").exists(), \
            "mubasher_news.py should have been deleted in PR 10"

    def test_twitter_source_deleted(self):
        assert not (self._DATAFLOWS / "twitter_source.py").exists(), \
            "twitter_source.py should have been deleted in PR 10"

    def test_facebook_apify_still_present(self):
        assert (self._V2_SOURCES / "facebook_apify.py").exists(), \
            "facebook_apify.py (primary source) must NOT be deleted"

    def test_reddit_targeted_still_present(self):
        assert (self._V2_SOURCES / "reddit_targeted.py").exists()

    def test_telegram_public_still_present(self):
        assert (self._V2_SOURCES / "telegram_public.py").exists()


# ── 2. aggregator.py no longer imports twitter ─────────────────────────────

class TestAggregatorTwitterRemoved:
    _AGG = _ROOT / "tradingagents" / "dataflows" / "social_media_sources" / "aggregator.py"

    def _source(self) -> str:
        return self._AGG.read_text(encoding="utf-8")

    def test_twitter_source_not_imported(self):
        src = self._source()
        assert "from .twitter_source import" not in src, \
            "aggregator.py must not import twitter_source after PR 10"

    def test_fetch_twitter_data_not_referenced(self):
        src = self._source()
        assert "fetch_twitter_data" not in src

    def test_platform_sources_no_twitter_key(self):
        from tradingagents.dataflows.social_media_sources.aggregator import PLATFORM_SOURCES
        assert "twitter" not in PLATFORM_SOURCES, \
            "'twitter' must be removed from PLATFORM_SOURCES in PR 10"

    def test_telegram_still_in_platform_sources(self):
        from tradingagents.dataflows.social_media_sources.aggregator import PLATFORM_SOURCES
        assert "telegram" in PLATFORM_SOURCES

    def test_reddit_still_in_platform_sources(self):
        from tradingagents.dataflows.social_media_sources.aggregator import PLATFORM_SOURCES
        assert "reddit" in PLATFORM_SOURCES


# ── 3. Sentiment package still importable ─────────────────────────────────

class TestSentimentPackageIntact:
    def test_sentiment_package_imports(self):
        import tradingagents.sentiment as s
        assert hasattr(s, "compute_market_sentiment")
        assert hasattr(s, "compute_macro_sentiment")
        assert hasattr(s, "compute_sector_sentiment")
        assert hasattr(s, "compute_stock_sentiment")

    def test_surfacing_importable(self):
        from tradingagents.sentiment.surfacing import (
            build_sentiment_context_event,
            extract_sentiment_audit_record,
            format_sentiment_for_api,
            format_sentiment_for_cli,
        )
        assert callable(extract_sentiment_audit_record)

    def test_blender_importable(self):
        from tradingagents.agents.utils.scoring import (
            SentimentBlend,
            blend_sentiment,
            calculate_unified_score,
        )
        assert callable(blend_sentiment)


# ── 4. Calibration script — pure functions ─────────────────────────────────

# Import the module under test (offline-safe — only pure functions)
sys.path.insert(0, str(_SCRIPTS))
from calibrate_tier_thresholds import (
    _compute_observation,
    _find_results_files,
    _infer_date_from_filename,
    _parse_timestamp,
    _percentile,
    _recommend_threshold,
    _summarise_metric,
    compute_recommendations,
)


class TestCalibrateHelpers:
    def test_percentile_empty(self):
        assert _percentile([], 50) == 0.0

    def test_percentile_single(self):
        assert _percentile([5.0], 50) == pytest.approx(5.0)

    def test_percentile_p50(self):
        assert _percentile([1, 2, 3, 4, 5], 50) == pytest.approx(3.0)

    def test_percentile_p0(self):
        assert _percentile([1, 2, 3], 0) == pytest.approx(1.0)

    def test_percentile_p100(self):
        assert _percentile([1, 2, 3], 100) == pytest.approx(3.0)

    def test_summarise_empty(self):
        s = _summarise_metric([])
        assert s["n"] == 0

    def test_summarise_basic(self):
        s = _summarise_metric([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        assert s["n"] == 10
        assert s["p50"] == pytest.approx(5.5)

    def test_recommend_threshold_ceiling(self):
        assert _recommend_threshold(3.2, 5) == 4

    def test_recommend_threshold_exact(self):
        assert _recommend_threshold(4.0, 5) == 4

    def test_recommend_threshold_min_one(self):
        assert _recommend_threshold(0.1, 5) >= 1

    def test_infer_date_from_filename(self):
        assert _infer_date_from_filename("results_20260502_143021.json") == "2026-05-02"

    def test_infer_date_none(self):
        assert _infer_date_from_filename("results.json") is None

    def test_parse_timestamp_iso(self):
        ts = _parse_timestamp("2026-05-01T12:00:00")
        assert ts is not None
        assert ts > 0

    def test_parse_timestamp_float(self):
        assert _parse_timestamp(1746000000.0) == pytest.approx(1746000000.0)

    def test_parse_timestamp_none(self):
        assert _parse_timestamp(None) is None

    def test_parse_timestamp_empty(self):
        assert _parse_timestamp("") is None


class TestComputeObservation:
    def test_empty_posts_returns_none(self):
        assert _compute_observation("COMI", [], "2026-05-01") is None

    def test_all_spam_returns_none(self):
        posts = [_make_post(spam=True) for _ in range(5)]
        assert _compute_observation("COMI", posts, "2026-05-01") is None

    def test_low_entity_conf_excluded(self):
        posts = [_make_post(entity_conf=0.50)]  # below 0.85 threshold
        assert _compute_observation("COMI", posts, "2026-05-01") is None

    def test_strong_posts_counted(self):
        posts = [_make_post(entity_conf=0.90, author=f"u{i}", source="tg") for i in range(3)]
        obs = _compute_observation("COMI", posts, "2026-05-01")
        assert obs is not None
        assert obs["n_strong_mentions"] == 3

    def test_distinct_authors_counted(self):
        posts = [
            _make_post(author="alice"),
            _make_post(author="bob"),
            _make_post(author="alice"),  # duplicate
        ]
        obs = _compute_observation("COMI", posts, "2026-05-01")
        assert obs["n_distinct_authors"] == 2

    def test_distinct_sources_counted(self):
        posts = [
            _make_post(source="telegram"),
            _make_post(source="reddit"),
            _make_post(source="telegram"),  # duplicate
        ]
        obs = _compute_observation("COMI", posts, "2026-05-01")
        assert obs["n_distinct_sources"] == 2

    def test_ticker_forwarded(self):
        posts = [_make_post()]
        obs = _compute_observation("TMGH", posts, "2026-05-01")
        assert obs["ticker"] == "TMGH"

    def test_recent_72h_share_none_without_date(self):
        posts = [_make_post()]
        obs = _compute_observation("COMI", posts, None)
        assert obs["recent_72h_share"] is None


class TestComputeRecommendations:
    def _make_observations(self, tier_ticker: str, n: int) -> Dict[str, list]:
        """Generate n observations for a given ticker."""
        obs_list = []
        for i in range(n):
            obs_list.append({
                "ticker": tier_ticker,
                "date": "2026-05-01",
                "n_strong_mentions": 6 + i,
                "n_distinct_authors": 4 + (i % 3),
                "n_distinct_sources": 2 + (i % 2),
                "recent_72h_share": 0.7,
            })
        return {tier_ticker: obs_list}

    def test_mega_tier_processed(self):
        obs = self._make_observations("COMI", 15)
        recs = compute_recommendations(obs)
        assert "MEGA" in recs

    def test_insufficient_data_keeps_current(self):
        # Only 5 observations — less than the 10-obs minimum
        obs = self._make_observations("COMI", 5)
        recs = compute_recommendations(obs)
        mega = recs.get("MEGA", {})
        ns = mega.get("n_strong_mentions", {})
        # Should keep current, not change
        assert ns.get("recommended") == ns.get("current") or ns.get("n_observations", 0) == 0

    def test_sufficient_data_may_change(self):
        # 20 observations with high n_strong_mentions
        obs = self._make_observations("COMI", 20)
        recs = compute_recommendations(obs)
        mega = recs.get("MEGA", {})
        ns = mega.get("n_strong_mentions", {})
        assert ns.get("recommended") is not None

    def test_all_tiers_in_output(self):
        obs = {}
        for ticker in ("COMI", "EAST", "HELI"):  # MEGA, SMALL, SMALL
            obs[ticker] = [{"ticker": ticker, "date": "2026-05-01",
                            "n_strong_mentions": 4, "n_distinct_authors": 3,
                            "n_distinct_sources": 2, "recent_72h_share": 0.7}] * 15
        recs = compute_recommendations(obs)
        for tier in ("MEGA", "MID", "SMALL"):
            assert tier in recs

    def test_empty_observations_safe(self):
        recs = compute_recommendations({})
        assert isinstance(recs, dict)
        for tier in ("MEGA", "MID", "SMALL"):
            assert tier in recs


# ── 5. Calibration script file structure ──────────────────────────────────

class TestCalibrationScriptExists:
    def test_script_file_exists(self):
        script = _SCRIPTS / "calibrate_tier_thresholds.py"
        assert script.exists(), "calibrate_tier_thresholds.py must exist in scripts/"

    def test_script_has_main(self):
        script = _SCRIPTS / "calibrate_tier_thresholds.py"
        src = script.read_text(encoding="utf-8")
        assert "def main(" in src

    def test_script_is_offline_safe(self):
        """Script must not import any live-API modules at module level."""
        script = _SCRIPTS / "calibrate_tier_thresholds.py"
        src = script.read_text(encoding="utf-8")
        # These modules would trigger live network calls
        for bad_import in ("apify", "playwright", "selenium"):
            assert bad_import not in src, \
                f"calibrate_tier_thresholds.py must not import {bad_import}"
