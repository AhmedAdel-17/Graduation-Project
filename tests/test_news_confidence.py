"""Tests for the news analyst confidence adjustments (P6).

Verifies that the no-news neutral default, sparse-news penalty, and
single-source penalty behave correctly.
"""

import pytest

from tradingagents.agents.analysts.news_analyst import (
    NO_NEWS_NEUTRAL_CONFIDENCE,
    SPARSE_NEWS_PENALTY,
    SINGLE_SOURCE_PENALTY,
)


# ---------------------------------------------------------------------------
# Helpers — simulate the deterministic penalty logic from news_analyst.py
# without invoking the full LLM chain.
# ---------------------------------------------------------------------------

def _apply_confidence_adjustments(sentiment_analysis: dict) -> dict:
    """Re-implement the deterministic penalty block from news_analyst.py.

    This mirrors lines 348-390 of news_analyst.py so we can unit-test the
    logic in isolation.
    """
    coverage = sentiment_analysis.get("news_coverage", {})
    total_articles = int(coverage.get("total_articles", 0) or 0)
    sources_count = int(coverage.get("sources_count", 0) or 0)
    adjustments = sentiment_analysis.setdefault("confidence_adjustments", [])

    if total_articles == 0:
        old_conf = sentiment_analysis.get("confidence_score", 50)
        new_conf = NO_NEWS_NEUTRAL_CONFIDENCE
        sentiment_analysis["confidence_score"] = new_conf
        sentiment_analysis["news_absent"] = True
        adjustments.append(
            f"No news: {old_conf} → {new_conf} (neutral default)"
        )
    elif total_articles < 3:
        old_conf = sentiment_analysis.get("confidence_score", 50)
        new_conf = max(0, old_conf - int(SPARSE_NEWS_PENALTY * 100))
        sentiment_analysis["confidence_score"] = new_conf
        adjustments.append(
            f"Sparse news: {old_conf} → {new_conf}"
        )

    if sources_count == 1 and total_articles > 0:
        old_conf = sentiment_analysis.get("confidence_score", 50)
        new_conf = max(0, old_conf - int(SINGLE_SOURCE_PENALTY * 100))
        sentiment_analysis["confidence_score"] = new_conf
        adjustments.append(
            f"Single source: {old_conf} → {new_conf}"
        )

    return sentiment_analysis


# ---------------------------------------------------------------------------
# No-news neutral default tests
# ---------------------------------------------------------------------------

class TestNoNewsNeutralDefault:
    """When total_articles == 0, confidence is assigned directly to the
    neutral default (35), regardless of what the LLM returned."""

    def test_llm_returned_high_confidence(self):
        """LLM returned 80 but there are no articles — assign 35."""
        sa = {"confidence_score": 80, "news_coverage": {"total_articles": 0}}
        result = _apply_confidence_adjustments(sa)
        assert result["confidence_score"] == NO_NEWS_NEUTRAL_CONFIDENCE

    def test_llm_returned_low_confidence(self):
        """LLM self-penalized to 15 — still assign 35 (raise it)."""
        sa = {"confidence_score": 15, "news_coverage": {"total_articles": 0}}
        result = _apply_confidence_adjustments(sa)
        assert result["confidence_score"] == NO_NEWS_NEUTRAL_CONFIDENCE

    def test_llm_returned_zero(self):
        """LLM returned 0 — assign 35."""
        sa = {"confidence_score": 0, "news_coverage": {"total_articles": 0}}
        result = _apply_confidence_adjustments(sa)
        assert result["confidence_score"] == NO_NEWS_NEUTRAL_CONFIDENCE

    def test_default_when_no_confidence_key(self):
        """No confidence_score key in dict — default 50, assign 35."""
        sa = {"news_coverage": {"total_articles": 0}}
        result = _apply_confidence_adjustments(sa)
        assert result["confidence_score"] == NO_NEWS_NEUTRAL_CONFIDENCE

    def test_news_absent_flag_set(self):
        """news_absent flag must be True when no articles found."""
        sa = {"confidence_score": 50, "news_coverage": {"total_articles": 0}}
        result = _apply_confidence_adjustments(sa)
        assert result["news_absent"] is True

    def test_news_absent_flag_not_set_when_articles_exist(self):
        """news_absent flag must not be set when articles exist."""
        sa = {"confidence_score": 50, "news_coverage": {"total_articles": 5, "sources_count": 2}}
        result = _apply_confidence_adjustments(sa)
        assert "news_absent" not in result

    def test_neutral_default_value_is_35(self):
        """The constant itself is 35."""
        assert NO_NEWS_NEUTRAL_CONFIDENCE == 35


# ---------------------------------------------------------------------------
# Sparse news penalty tests
# ---------------------------------------------------------------------------

class TestSparseNewsPenalty:
    """When 1 <= total_articles < 3, subtract SPARSE_NEWS_PENALTY (10)."""

    def test_one_article_subtracts_10(self):
        sa = {"confidence_score": 50, "news_coverage": {"total_articles": 1, "sources_count": 2}}
        result = _apply_confidence_adjustments(sa)
        assert result["confidence_score"] == 40

    def test_two_articles_subtracts_10(self):
        sa = {"confidence_score": 50, "news_coverage": {"total_articles": 2, "sources_count": 2}}
        result = _apply_confidence_adjustments(sa)
        assert result["confidence_score"] == 40

    def test_three_articles_no_penalty(self):
        sa = {"confidence_score": 50, "news_coverage": {"total_articles": 3, "sources_count": 2}}
        result = _apply_confidence_adjustments(sa)
        assert result["confidence_score"] == 50

    def test_sparse_does_not_go_below_zero(self):
        sa = {"confidence_score": 5, "news_coverage": {"total_articles": 1, "sources_count": 2}}
        result = _apply_confidence_adjustments(sa)
        assert result["confidence_score"] == 0

    def test_sparse_penalty_constant_is_010(self):
        assert SPARSE_NEWS_PENALTY == 0.10


# ---------------------------------------------------------------------------
# Single-source penalty tests
# ---------------------------------------------------------------------------

class TestSingleSourcePenalty:
    """When sources_count == 1 and articles > 0, subtract 10 more."""

    def test_single_source_with_articles(self):
        sa = {"confidence_score": 50, "news_coverage": {"total_articles": 5, "sources_count": 1}}
        result = _apply_confidence_adjustments(sa)
        assert result["confidence_score"] == 40

    def test_single_source_stacks_with_sparse(self):
        """1 article from 1 source: sparse (-10) then single-source (-10)."""
        sa = {"confidence_score": 50, "news_coverage": {"total_articles": 1, "sources_count": 1}}
        result = _apply_confidence_adjustments(sa)
        assert result["confidence_score"] == 30

    def test_single_source_not_applied_when_no_articles(self):
        """Single-source penalty requires articles > 0."""
        sa = {"confidence_score": 50, "news_coverage": {"total_articles": 0, "sources_count": 1}}
        result = _apply_confidence_adjustments(sa)
        # No-news neutral default applied, not single-source penalty
        assert result["confidence_score"] == NO_NEWS_NEUTRAL_CONFIDENCE

    def test_multiple_sources_no_penalty(self):
        sa = {"confidence_score": 50, "news_coverage": {"total_articles": 5, "sources_count": 3}}
        result = _apply_confidence_adjustments(sa)
        assert result["confidence_score"] == 50

    def test_single_source_constant_is_010(self):
        assert SINGLE_SOURCE_PENALTY == 0.10
