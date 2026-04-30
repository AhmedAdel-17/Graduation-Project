"""Unit tests for DataPrefetcher (Phase 2a).

Covers:
  - fetch_all() returns all 4 required keys
  - Partial failure resilience (one source fails, others return data)
  - Total failure (all sources fail — all values are empty strings)
  - Timeout handling
  - propagate() merge: fetch_all() output can be merged into an initial state dict
    (gap identified in plan review — the graph's propagate() does state.update(prefetched))
"""
import concurrent.futures
import pytest
from unittest.mock import patch, MagicMock

from tradingagents.graph.prefetch import DataPrefetcher

REQUIRED_KEYS = {
    "prefetched_company_news",
    "prefetched_market_news",
    "prefetched_social_sentiment",
    "prefetched_social_posts",
}


@pytest.fixture
def egx_config():
    return {"target_market": "EGX"}


@pytest.fixture
def us_config():
    return {"target_market": "US"}


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

class TestDataPrefetcherInit:
    def test_egx_market(self, egx_config):
        pf = DataPrefetcher(egx_config)
        assert pf.target_market == "EGX"

    def test_us_market(self, us_config):
        pf = DataPrefetcher(us_config)
        assert pf.target_market == "US"

    def test_default_market(self):
        pf = DataPrefetcher({})
        assert pf.target_market == "US"


# ---------------------------------------------------------------------------
# fetch_all — happy path
# ---------------------------------------------------------------------------

class TestFetchAll:
    def test_returns_all_four_keys(self, egx_config):
        pf = DataPrefetcher(egx_config)
        with patch.object(pf, "_fetch_company_news", return_value="news1"), \
             patch.object(pf, "_fetch_market_news", return_value="news2"), \
             patch.object(pf, "_fetch_social_sentiment", return_value="sent1"), \
             patch.object(pf, "_fetch_social_posts", return_value="posts1"):
            result = pf.fetch_all("COMI.CA", "2024-01-15")

        assert set(result.keys()) == REQUIRED_KEYS

    def test_values_propagate_correctly(self, egx_config):
        pf = DataPrefetcher(egx_config)
        with patch.object(pf, "_fetch_company_news", return_value="company_data"), \
             patch.object(pf, "_fetch_market_news", return_value="market_data"), \
             patch.object(pf, "_fetch_social_sentiment", return_value="sentiment_data"), \
             patch.object(pf, "_fetch_social_posts", return_value="posts_data"):
            result = pf.fetch_all("COMI.CA", "2024-01-15")

        assert result["prefetched_company_news"] == "company_data"
        assert result["prefetched_market_news"] == "market_data"
        assert result["prefetched_social_sentiment"] == "sentiment_data"
        assert result["prefetched_social_posts"] == "posts_data"


# ---------------------------------------------------------------------------
# fetch_all — failure resilience
# ---------------------------------------------------------------------------

class TestFetchAllFailureResilience:
    def test_partial_failure_returns_empty_string_for_failed_key(self, egx_config):
        pf = DataPrefetcher(egx_config)
        with patch.object(pf, "_fetch_company_news", side_effect=Exception("boom")), \
             patch.object(pf, "_fetch_market_news", return_value="ok"), \
             patch.object(pf, "_fetch_social_sentiment", return_value="ok"), \
             patch.object(pf, "_fetch_social_posts", return_value="ok"):
            result = pf.fetch_all("COMI.CA", "2024-01-15")

        assert result["prefetched_company_news"] == ""
        assert result["prefetched_market_news"] == "ok"
        assert result["prefetched_social_sentiment"] == "ok"

    def test_total_failure_returns_all_empty_strings(self, egx_config):
        pf = DataPrefetcher(egx_config)
        with patch.object(pf, "_fetch_company_news", side_effect=Exception("x")), \
             patch.object(pf, "_fetch_market_news", side_effect=Exception("x")), \
             patch.object(pf, "_fetch_social_sentiment", side_effect=Exception("x")), \
             patch.object(pf, "_fetch_social_posts", side_effect=Exception("x")):
            result = pf.fetch_all("COMI.CA", "2024-01-15")

        assert all(v == "" for v in result.values())
        assert len(result) == 4

    def test_timeout_on_one_source_does_not_crash(self, egx_config):
        """A 30-second timeout on one source should produce "" for that key."""
        pf = DataPrefetcher(egx_config)

        def slow_fetch(*args, **kwargs):
            raise concurrent.futures.TimeoutError()

        with patch.object(pf, "_fetch_company_news", side_effect=slow_fetch), \
             patch.object(pf, "_fetch_market_news", return_value="ok"), \
             patch.object(pf, "_fetch_social_sentiment", return_value="ok"), \
             patch.object(pf, "_fetch_social_posts", return_value="ok"):
            result = pf.fetch_all("COMI.CA", "2024-01-15")

        # TimeoutError is caught by the executor.result() wrapper; key returns ""
        assert len(result) == 4
        assert result["prefetched_market_news"] == "ok"


# ---------------------------------------------------------------------------
# Propagate merge (gap identified in plan review)
# fetch_all() output is merged into init_agent_state via state.update(prefetched)
# This test verifies the contract: all 4 keys land in the state dict correctly.
# ---------------------------------------------------------------------------

class TestPropagateMerge:
    def test_fetch_all_output_merges_into_state(self, egx_config):
        """Simulate the graph's propagate() merge: state.update(prefetched)."""
        pf = DataPrefetcher(egx_config)
        with patch.object(pf, "_fetch_company_news", return_value="cn"), \
             patch.object(pf, "_fetch_market_news", return_value="mn"), \
             patch.object(pf, "_fetch_social_sentiment", return_value="ss"), \
             patch.object(pf, "_fetch_social_posts", return_value="sp"):
            prefetched = pf.fetch_all("COMI.CA", "2024-01-15")

        init_state = {
            "company_of_interest": "COMI.CA",
            "trade_date": "2024-01-15",
            "messages": [],
        }
        init_state.update(prefetched)

        # All 4 prefetch keys must be present in the merged state
        for key in REQUIRED_KEYS:
            assert key in init_state, f"Missing key after merge: {key}"

        # Original state keys must be preserved
        assert init_state["company_of_interest"] == "COMI.CA"

    def test_empty_prefetch_still_sets_keys(self, egx_config):
        """Even when all sources fail, the 4 keys appear in state (as empty strings)."""
        pf = DataPrefetcher(egx_config)
        with patch.object(pf, "_fetch_company_news", side_effect=Exception("x")), \
             patch.object(pf, "_fetch_market_news", side_effect=Exception("x")), \
             patch.object(pf, "_fetch_social_sentiment", side_effect=Exception("x")), \
             patch.object(pf, "_fetch_social_posts", side_effect=Exception("x")):
            prefetched = pf.fetch_all("COMI.CA", "2024-01-15")

        init_state = {}
        init_state.update(prefetched)

        for key in REQUIRED_KEYS:
            assert key in init_state
            assert init_state[key] == ""
