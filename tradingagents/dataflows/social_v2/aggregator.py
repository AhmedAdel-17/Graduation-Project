"""Weighted aggregation: per-stock and market-wide signals.

Ported from scripts/social_pipeline/v2/aggregator.py.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List


ACTIONABLE_INTENTS = {"BUY", "SELL", "BULLISH", "BEARISH", "REACTION"}
MIN_TOTAL_POSTS = 50
MIN_MENTIONS_PER_STOCK = 3


@dataclass
class ScoredPost:
    text: str
    url: str
    platform: str
    source: str
    timestamp: str
    engagement: int
    mentions: list
    intent: dict
    content: dict
    sentiment: dict

    def sentiment_score(self) -> float:
        return max(-1.0, min(1.0, float(self.sentiment.get("score", 0.0))))

    def _intent_factor(self) -> float:
        intents = set(self.intent.get("intents") or [])
        if intents & ACTIONABLE_INTENTS:
            return 1.0
        if "HOLD" in intents:
            return 0.6
        return 0.3

    def base_weight(self) -> float:
        content_weight = self.content.get("weight", 0.3)
        platform_factor = 1.2 if self.platform == "facebook" else 1.0
        return content_weight * self._intent_factor() * platform_factor

    def engagement_factor(self) -> float:
        return math.log1p(max(0, self.engagement))


def _label_from_score(score: float) -> str:
    if score >= 0.15:
        return "bullish"
    if score <= -0.15:
        return "bearish"
    return "neutral"


def aggregate(posts: List[ScoredPost]) -> Dict[str, Any]:
    by_symbol: Dict[str, dict] = defaultdict(lambda: {
        "n": 0,
        "weighted_sum": 0.0,
        "weight_total": 0.0,
        "intent_breakdown": defaultdict(int),
        "by_source": defaultdict(int),
        "by_content_type": defaultdict(int),
        "distinct_sources": set(),
        "distinct_days": set(),
        "examples": [],
    })

    market_bucket = by_symbol["EGX_MARKET"]

    for post in posts:
        sentiment_score = post.sentiment_score()
        base_weight = post.base_weight()
        engagement_factor = post.engagement_factor()
        targets = []

        for mention in post.mentions:
            symbol = mention.symbol
            if symbol.startswith("EGX_"):
                targets.append(("EGX_MARKET", mention.confidence))
                continue
            targets.append((symbol, mention.confidence))

        if not targets:
            targets = [("EGX_MARKET", 0.5)]

        day_key = (post.timestamp or "")[:10]

        for symbol, entity_confidence in targets:
            weight = base_weight * entity_confidence
            effective_weight = weight * engagement_factor
            slot = by_symbol[symbol]
            slot["n"] += 1
            slot["weighted_sum"] += sentiment_score * effective_weight
            slot["weight_total"] += effective_weight
            for label in post.intent.get("intents", []):
                slot["intent_breakdown"][label] += 1
            slot["by_source"][post.platform] += 1
            slot["by_content_type"][post.content.get("label", "OTHER")] += 1
            slot["distinct_sources"].add(post.platform)
            if day_key:
                slot["distinct_days"].add(day_key)
            if len(slot["examples"]) < 3:
                slot["examples"].append({
                    "url": post.url,
                    "platform": post.platform,
                    "intents": post.intent.get("intents", []),
                    "sentiment_label": post.sentiment.get("label"),
                    "sentiment_score": round(post.sentiment.get("score", 0.0), 3),
                    "weighted_score": round(sentiment_score * effective_weight, 3),
                    "weight": round(weight, 3),
                    "engagement_factor": round(engagement_factor, 3),
                    "text": (post.text or "")[:200].replace("\n", " "),
                })

        if "EGX_MARKET" not in {t[0] for t in targets}:
            market_weight = base_weight * 0.3
            market_bucket["n"] += 1
            market_bucket["weighted_sum"] += sentiment_score * market_weight * engagement_factor
            market_bucket["weight_total"] += market_weight * engagement_factor
            market_bucket["distinct_sources"].add(post.platform)
            if day_key:
                market_bucket["distinct_days"].add(day_key)

    out: Dict[str, dict] = {}
    for symbol, slot in by_symbol.items():
        if slot["weight_total"] <= 0 or slot["n"] == 0:
            continue
        weighted_sentiment = slot["weighted_sum"] / slot["weight_total"]
        avg_weight = slot["weight_total"] / slot["n"]
        quality = max(0.0, min(1.0, avg_weight))
        size = slot["n"] / (slot["n"] + 5.0)
        confidence = 0.6 * quality + 0.4 * size
        out[symbol] = {
            "symbol": symbol,
            "n": slot["n"],
            "weighted_sentiment": round(weighted_sentiment, 3),
            "label": _label_from_score(weighted_sentiment),
            "confidence": round(confidence, 3),
            "intent_breakdown": dict(slot["intent_breakdown"]),
            "by_source": dict(slot["by_source"]),
            "by_content_type": dict(slot["by_content_type"]),
            "n_distinct_sources": len(slot["distinct_sources"]),
            "n_distinct_days": len(slot["distinct_days"]),
            "examples": slot["examples"],
        }
    return out


def _neutral_market_signal() -> dict:
    return {
        "symbol": "EGX_MARKET",
        "n": 0,
        "weighted_sentiment": 0.0,
        "label": "neutral",
        "confidence": 0.0,
        "intent_breakdown": {},
        "by_source": {},
        "by_content_type": {},
        "n_distinct_sources": 0,
        "n_distinct_days": 0,
        "examples": [],
    }


def _scale_signal(signal: dict, confidence_multiplier: float) -> dict:
    scaled = dict(signal)
    scaled["confidence"] = round(scaled.get("confidence", 0.0) * confidence_multiplier, 3)
    return scaled


def _to_stock_card(signal: dict) -> dict:
    return {
        "symbol": signal["symbol"],
        "sentiment": signal["label"],
        "score": round(signal["weighted_sentiment"], 3),
        "confidence": signal["confidence"],
        "mentions": signal["n"],
    }


def _sort_key(signal: dict) -> tuple[float, int, float]:
    return (
        abs(signal.get("weighted_sentiment", 0.0)) * signal.get("confidence", 0.0),
        signal.get("n", 0),
        abs(signal.get("weighted_sentiment", 0.0)),
    )


def split_outputs(
    per_symbol: Dict[str, dict],
    total_posts: int,
    used_posts: int | None = None,
    source_counts: dict | None = None,
) -> Dict[str, Any]:
    used_posts = total_posts if used_posts is None else used_posts
    confidence_multiplier = min(1.0, used_posts / float(MIN_TOTAL_POSTS))

    market_signal = _scale_signal(
        per_symbol.get("EGX_MARKET", _neutral_market_signal()),
        confidence_multiplier,
    )

    per_stock = {}
    for symbol, signal in per_symbol.items():
        if symbol.startswith("EGX_"):
            continue
        if signal["n"] < MIN_MENTIONS_PER_STOCK:
            continue
        per_stock[symbol] = _scale_signal(signal, confidence_multiplier)

    ranked = sorted(per_stock.values(), key=_sort_key, reverse=True)
    all_stocks = [_to_stock_card(signal) for signal in ranked]
    top_stocks = all_stocks[:10]

    if used_posts == 0:
        reason = "no posts passed quality gate"
    elif used_posts < MIN_TOTAL_POSTS:
        reason = f"low-confidence-sample(total_posts={used_posts})"
    else:
        reason = ""

    metadata = {
        "total_posts": total_posts,
        "used_posts": used_posts,
        "confidence": market_signal["confidence"],
        "confidence_multiplier": round(confidence_multiplier, 3),
        "source_counts": dict(source_counts or {}),
        "thresholds": {
            "min_total_posts": MIN_TOTAL_POSTS,
            "min_mentions_per_stock": MIN_MENTIONS_PER_STOCK,
        },
    }

    return {
        "status": "OK",
        "reason": reason,
        "market_sentiment": market_signal,
        "per_stock_sentiment": per_stock,
        "top_stocks": top_stocks,
        "all_stocks": all_stocks,
        "metadata": metadata,
    }
