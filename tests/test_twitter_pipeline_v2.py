from __future__ import annotations

from scripts.twitter_pipeline.scraper import Post
from scripts.twitter_pipeline.v2 import pipeline_v2
from scripts.twitter_pipeline.v2.aggregator import ScoredPost, aggregate, split_outputs
from scripts.twitter_pipeline.v2.entities import Mention, extract
from scripts.twitter_pipeline.v2.quality_gate import evaluate
from scripts.twitter_pipeline.v2.sources import facebook_apify


def _post(text: str, platform: str = "facebook", url: str = "https://example.com/post") -> Post:
    return Post(
        text=text,
        username="tester",
        timestamp="2026-04-25T12:00:00Z",
        url=url,
        platform=platform,
        source=f"{platform}:test",
        engagement=5,
    )


def test_entity_registry_matches_english_and_arabic_aliases():
    cases = [
        ("$COMI.CA strong buy today", "COMI", 1.0),
        ("ORAS will breakout soon", "ORAS", 0.95),
        ("\u0633\u0647\u0645 \u0639\u0628\u0648\u0631 \u0644\u0627\u0646\u062f \u0627\u062e\u062a\u0631\u0627\u0642 \u0627\u064a\u062c\u0627\u0628\u064a", "OLFI", 0.75),
        ("\u0627\u0628\u0646 \u0633\u064a\u0646\u0627 \u0641\u064a\u0647 \u062a\u062c\u0645\u064a\u0639", "ISPH", 0.75),
        ("\u0642\u0646\u0627\u0629 \u0627\u0644\u0633\u0648\u064a\u0633 \u0644\u062a\u0648\u0637\u064a\u0646 \u0627\u0644\u062a\u0643\u0646\u0648\u0644\u0648\u062c\u064a\u0627 \u0647\u062f\u0641 95", "SCTS", 0.85),
        ("\u0631\u0627\u064a\u0643\u0645 \u0641\u064a \u0627\u0645 \u0627\u0645 \u062c\u0631\u0648\u0628", "MTIE", 0.75),
        ("GIHD \u0633\u0647\u0645 \u0645\u0645\u062a\u0627\u0632", "GIHD", 0.95),
    ]

    for text, expected_symbol, min_confidence in cases:
        mentions = extract(text)
        assert mentions, text
        assert mentions[0].symbol == expected_symbol
        assert mentions[0].confidence >= min_confidence

    assert not extract("EAST winds are strong in the middle east today")


def test_quality_gate_keeps_analysis_hold_none_and_medium_length_posts():
    analysis = evaluate(
        "\u062a\u062d\u0644\u064a\u0644 \u0633\u0647\u0645 \u0627\u0628\u0646 \u0633\u064a\u0646\u0627 \u0648\u0627\u062e\u062a\u0631\u0627\u0642 \u0645\u0642\u0627\u0648\u0645\u0629",
        {"intents": ["BULLISH"], "score": 0.7},
        {"label": "ANALYSIS", "weight": 0.4},
    )
    assert analysis["keep"] is True
    assert analysis["bucket"] == "downgraded"

    hold = evaluate(
        "holding is better here",
        {"intents": ["HOLD"], "score": 0.0},
        {"label": "OPINION", "weight": 1.0},
    )
    assert hold["keep"] is True
    assert hold["bucket"] == "downgraded"

    no_intent = evaluate(
        "this setup still looks interesting for next week",
        {"intents": ["NONE"], "score": 0.0},
        {"label": "OTHER", "weight": 0.3},
    )
    assert no_intent["keep"] is True
    assert no_intent["bucket"] == "downgraded"

    medium_length = evaluate(
        "word " * 120,
        {"intents": ["NONE"], "score": 0.0},
        {"label": "OTHER", "weight": 0.3},
    )
    assert medium_length["keep"] is True

    too_long = evaluate(
        "word " * 151,
        {"intents": ["BUY"], "score": 0.7},
        {"label": "OPINION", "weight": 1.0},
    )
    assert too_long["keep"] is False
    assert too_long["bucket"] == "hard_drop"


def test_aggregate_applies_facebook_priority_weight():
    bullish_fb = ScoredPost(
        text="buy isph now",
        url="https://example.com/fb",
        platform="facebook",
        source="facebook-group:test",
        timestamp="2026-04-25T12:00:00Z",
        engagement=10,
        mentions=[Mention(symbol="ISPH", confidence=1.0)],
        intent={"intents": ["BUY"], "score": 1.0},
        content={"label": "OPINION", "weight": 1.0},
        sentiment={"score": 0.6, "confidence": 1.0, "label": "bullish"},
    )
    bearish_reddit = ScoredPost(
        text="sell isph now",
        url="https://example.com/reddit",
        platform="reddit",
        source="reddit:test",
        timestamp="2026-04-25T12:05:00Z",
        engagement=10,
        mentions=[Mention(symbol="ISPH", confidence=1.0)],
        intent={"intents": ["SELL"], "score": -1.0},
        content={"label": "OPINION", "weight": 1.0},
        sentiment={"score": -0.6, "confidence": 1.0, "label": "bearish"},
    )

    per_symbol = aggregate([bullish_fb, bearish_reddit])
    assert per_symbol["ISPH"]["weighted_sentiment"] > 0.0
    assert per_symbol["ISPH"]["by_source"]["facebook"] == 1
    assert per_symbol["ISPH"]["by_source"]["reddit"] == 1


def test_split_outputs_scales_confidence_and_preserves_dual_schema():
    per_symbol = {
        "EGX_MARKET": {
            "symbol": "EGX_MARKET",
            "n": 4,
            "weighted_sentiment": 0.2,
            "label": "bullish",
            "confidence": 0.8,
            "intent_breakdown": {"BUY": 2},
            "by_source": {"facebook": 4},
            "by_content_type": {"OPINION": 4},
            "examples": [],
        },
        "ISPH": {
            "symbol": "ISPH",
            "n": 3,
            "weighted_sentiment": 0.72,
            "label": "bullish",
            "confidence": 0.8,
            "intent_breakdown": {"BUY": 3},
            "by_source": {"facebook": 3},
            "by_content_type": {"OPINION": 3},
            "examples": [],
        },
        "OLFI": {
            "symbol": "OLFI",
            "n": 2,
            "weighted_sentiment": -0.5,
            "label": "bearish",
            "confidence": 0.7,
            "intent_breakdown": {"SELL": 2},
            "by_source": {"reddit": 2},
            "by_content_type": {"OPINION": 2},
            "examples": [],
        },
    }

    output = split_outputs(
        per_symbol,
        total_posts=20,
        used_posts=20,
        source_counts={"facebook_apify": 12},
    )

    assert output["status"] == "OK"
    assert output["reason"] == "low-confidence-sample(total_posts=20)"
    assert output["market_sentiment"]["confidence"] == 0.32
    assert "ISPH" in output["per_stock_sentiment"]
    assert output["per_stock_sentiment"]["ISPH"]["confidence"] == 0.32
    assert "OLFI" not in output["per_stock_sentiment"]
    assert output["top_stocks"][0] == {
        "symbol": "ISPH",
        "sentiment": "bullish",
        "score": 0.72,
        "confidence": 0.32,
        "mentions": 3,
    }
    assert output["metadata"]["confidence_multiplier"] == 0.4
    assert output["metadata"]["source_counts"] == {"facebook_apify": 12}


def test_split_outputs_returns_neutral_market_when_no_posts_survive():
    output = split_outputs({}, total_posts=0, used_posts=0, source_counts={})
    assert output["status"] == "OK"
    assert output["market_sentiment"]["label"] == "neutral"
    assert output["market_sentiment"]["weighted_sentiment"] == 0.0
    assert output["market_sentiment"]["confidence"] == 0.0
    assert output["top_stocks"] == []
    assert output["all_stocks"] == []


def test_facebook_apify_uses_per_group_runs_and_deduplicates(monkeypatch):
    class DummyResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
            self.text = "ok"

        def json(self):
            return self._payload

    group_one = "https://www.facebook.com/groups/1/"
    group_two = "https://www.facebook.com/groups/2/"
    calls = []
    payloads = {
        group_one: [
            {
                "groupTitle": "Group One",
                "text": "\u0633\u0647\u0645 \u0627\u0628\u0646 \u0633\u064a\u0646\u0627 \u0634\u0631\u0627\u0621 \u0642\u0648\u064a",
                "postId": "1",
                "facebookUrl": group_one,
                "time": "2026-04-25T12:00:00Z",
            },
            {
                "groupTitle": "Group One",
                "text": "\u0633\u0647\u0645 \u0627\u0628\u0646 \u0633\u064a\u0646\u0627 \u0634\u0631\u0627\u0621 \u0642\u0648\u064a",
                "postId": "1",
                "facebookUrl": group_one,
                "time": "2026-04-25T12:00:00Z",
            },
        ],
        group_two: [
            {
                "groupTitle": "Group Two",
                "text": "\u062a\u062d\u0644\u064a\u0644 \u0639\u0628\u0648\u0631 \u0644\u0627\u0646\u062f \u062f\u0639\u0645 \u0642\u0648\u064a",
                "facebookUrl": group_two,
                "time": "2026-04-25T13:00:00Z",
            },
            {
                "groupTitle": "Group Two",
                "text": "\u062a\u062d\u0644\u064a\u0644 \u0639\u0628\u0648\u0631 \u0644\u0627\u0646\u062f \u062f\u0639\u0645 \u0642\u0648\u064a",
                "facebookUrl": group_two,
                "time": "2026-04-25T13:00:00Z",
            },
        ],
    }

    def fake_post(url, params=None, json=None, timeout=None):
        assert url == facebook_apify.APIFY_RUN_URL
        calls.append(json)
        group_url = json["startUrls"][0]["url"]
        return DummyResponse(payloads[group_url])

    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    monkeypatch.setenv("EGX_FB_GROUP_URLS", f"{group_one},{group_two}")
    monkeypatch.setattr(facebook_apify.requests, "post", fake_post)

    posts = facebook_apify.scrape(results_per_group=300)

    assert len(calls) == 2
    assert all(call["resultsLimit"] == 300 for call in calls)
    assert len(posts) == 2
    assert len({post.url for post in posts}) == 2
    assert all("#post-" in post.url for post in posts)


def test_stage_relevance_uses_light_facebook_egx_guard():
    posts = [
        _post("\u0633\u0647\u0645 \u0627\u0628\u0646 \u0633\u064a\u0646\u0627 \u0634\u0631\u0627\u0621 \u0642\u0648\u064a", url="https://example.com/a"),
        _post("\u0627\u0644\u0628\u0648\u0631\u0635\u0629 \u0627\u0644\u0645\u0635\u0631\u064a\u0629 \u062a\u062d\u062a \u0636\u063a\u0637", url="https://example.com/b"),
        _post("\u0628\u064a\u0639 \u0627\u0644\u0630\u0647\u0628 \u0648\u0627\u0644\u062f\u0648\u0644\u0627\u0631 \u0627\u0644\u064a\u0648\u0645", url="https://example.com/c"),
    ]

    kept, dropped = pipeline_v2.stage_relevance(posts)
    kept_texts = {post.text for post in kept}

    assert posts[0].text in kept_texts
    assert posts[1].text in kept_texts
    assert posts[2].text not in kept_texts
    assert dropped
