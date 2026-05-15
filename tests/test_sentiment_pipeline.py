from __future__ import annotations

from pathlib import Path

import pytest

from scripts.twitter_pipeline.v2.aggregator import ScoredPost, aggregate
from scripts.twitter_pipeline.v2.entities import Mention
from scripts.twitter_pipeline.v2.sentiment_validation import (
    build_validation_report,
    compute_validation_stats,
    find_latest_results_json,
    load_pipeline_output,
    recompute_aggregation,
    score_items_with_sentiment,
    validate_scored_output,
)


@pytest.fixture(scope="module")
def latest_results_path() -> str:
    try:
        return find_latest_results_json()
    except FileNotFoundError as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="module")
def latest_payload(latest_results_path: str) -> dict:
    payload = load_pipeline_output(latest_results_path)
    if not payload.get("items"):
        pytest.skip(f"No scored items found in {latest_results_path}")
    return payload


def test_latest_pipeline_output_can_be_rescored_and_reaggregated(latest_payload: dict):
    scored_items, sentiment_debug = score_items_with_sentiment(latest_payload["items"])
    per_symbol, output = recompute_aggregation(
        scored_items,
        source_counts=latest_payload.get("source_counts"),
        total_posts=latest_payload.get("metadata", {}).get("total_posts", len(scored_items)),
    )
    stats = compute_validation_stats(scored_items, per_symbol)
    validation = validate_scored_output(scored_items, per_symbol, output)

    assert stats["total_posts_processed"] == len(scored_items)
    assert output["market_sentiment"]["label"] in {"bullish", "bearish", "neutral"}
    assert isinstance(output["top_stocks"], list)
    assert isinstance(output["all_stocks"], list)
    assert sentiment_debug["model_usage"]
    assert validation["validated"] is True
    assert stats["per_stock"]


def test_validation_report_matches_requested_sample_shape(latest_results_path: str):
    report = build_validation_report(latest_results_path)

    assert set(report.keys()) >= {"market_sentiment", "top_stocks", "stats"}
    assert report["stats"]["total_posts_processed"] > 0
    assert report["market_sentiment"]["label"] in {"bullish", "bearish", "neutral"}
    assert all(
        -1.0 <= item["score"] <= 1.0
        for item in report["top_stocks"]
    ) or not report["top_stocks"]


def test_validation_distribution_is_well_formed(latest_payload: dict):
    scored_items, _ = score_items_with_sentiment(latest_payload["items"])
    per_symbol, _ = recompute_aggregation(
        scored_items,
        source_counts=latest_payload.get("source_counts"),
        total_posts=latest_payload.get("metadata", {}).get("total_posts", len(scored_items)),
    )
    stats = compute_validation_stats(scored_items, per_symbol)

    total_pct = stats["bullish_pct"] + stats["bearish_pct"] + stats["neutral_pct"]
    assert abs(total_pct - 1.0) <= 0.01
    assert -1.0 <= stats["avg_sentiment"] <= 1.0
    for symbol_stats in stats["per_stock"].values():
        assert symbol_stats["mentions"] >= 1
        assert -1.0 <= symbol_stats["avg_sentiment"] <= 1.0
        assert 0.0 <= symbol_stats["confidence"] <= 1.0


def test_confidence_increases_with_number_of_posts():
    one_post = ScoredPost(
        text="buy comi now",
        url="https://example.com/1",
        platform="facebook",
        source="facebook:test",
        timestamp="2026-04-25T12:00:00Z",
        engagement=10,
        mentions=[Mention(symbol="COMI", confidence=1.0)],
        intent={"intents": ["BUY"], "score": 1.0},
        content={"label": "OPINION", "weight": 1.0},
        sentiment={"score": 0.6, "confidence": 0.9, "label": "bullish", "model_used": "finbert"},
    )

    low_conf = aggregate([one_post])["COMI"]["confidence"]
    high_conf = aggregate([one_post, one_post, one_post, one_post])["COMI"]["confidence"]

    assert high_conf > low_conf
