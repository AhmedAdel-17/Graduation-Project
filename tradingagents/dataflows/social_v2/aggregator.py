"""Weighted aggregation: layered EGX sentiment signals.

Produces four layers from the same enriched-post stream:

* **market**  — overall EGX mood (single signal).
* **sectors** — per-sector mood (banks, real estate, telecom, ...).
* **tickers** — per-ticker mood (the existing per-stock output).
* **events**  — per-macro-event mood (war, pandemic, EGP devaluation, IMF,
  rates, inflation, oil, elections, regulation, sovereign debt).

The four layers share the same weighted-sum / confidence math; only the
grouping key differs. The output of ``split_outputs`` includes new
``sector_sentiment`` and ``event_sentiment`` blocks alongside the existing
``market_sentiment`` and ``per_stock_sentiment`` (kept for backwards
compatibility).

Originally ported from scripts/social_pipeline/v2/aggregator.py.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .indices import index_pseudo_symbol_to_code, indices_from_ticker_mentions
from .sectors import sectors_from_ticker_mentions, ticker_sector


ACTIONABLE_INTENTS = {"BUY", "SELL", "BULLISH", "BEARISH", "REACTION"}
MIN_TOTAL_POSTS = 50
MIN_MENTIONS_PER_STOCK = 3
MIN_MENTIONS_PER_SECTOR = 5
MIN_MENTIONS_PER_EVENT = 3
MIN_MENTIONS_PER_INDEX = 5


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
    # Layered-aggregation inputs (optional — old callers still work).
    sector_mentions: list = field(default_factory=list)
    event_mentions: list = field(default_factory=list)

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
        # Floor at 1.0 so news articles (engagement=0 by definition) still
        # contribute weight. Social posts with real engagement still scale
        # logarithmically above the floor: log1p(10)≈2.4, log1p(100)≈4.6.
        return max(1.0, math.log1p(max(0, self.engagement)))


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


def _empty_slot() -> dict:
    return {
        "n": 0,
        "weighted_sum": 0.0,
        "weight_total": 0.0,
        "intent_breakdown": defaultdict(int),
        "by_source": defaultdict(int),
        "by_content_type": defaultdict(int),
        "distinct_sources": set(),
        "distinct_days": set(),
        "examples": [],
    }


def _finalize_slot(key: str, slot: dict, label_field: str = "symbol") -> dict | None:
    if slot["weight_total"] <= 0 or slot["n"] == 0:
        return None
    weighted_sentiment = slot["weighted_sum"] / slot["weight_total"]
    avg_weight = slot["weight_total"] / slot["n"]
    quality = max(0.0, min(1.0, avg_weight))
    size = slot["n"] / (slot["n"] + 5.0)
    confidence = 0.6 * quality + 0.4 * size
    return {
        label_field: key,
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


def _record_post(slot: dict, post: ScoredPost, sentiment_score: float,
                 effective_weight: float, weight: float,
                 engagement_factor: float, day_key: str) -> None:
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


def aggregate_sectors(posts: List[ScoredPost]) -> Dict[str, dict]:
    """Per-sector weighted sentiment.

    Two paths contribute to a sector:
      1. Ticker mentions roll up via ``ticker_sector()`` (strongest signal).
      2. Explicit sector keywords on the post (sector_mentions, weaker).
    """
    by_sector: Dict[str, dict] = defaultdict(_empty_slot)

    for post in posts:
        sentiment_score = post.sentiment_score()
        base_weight = post.base_weight()
        engagement_factor = post.engagement_factor()
        day_key = (post.timestamp or "")[:10]

        targets: Dict[str, float] = {}
        for mention in post.mentions:
            sym = mention.symbol
            if sym.startswith("EGX_"):
                continue
            sector = ticker_sector(sym)
            if sector == "OTHER":
                continue
            targets[sector] = max(targets.get(sector, 0.0), 0.9 * mention.confidence)
        for sm in post.sector_mentions:
            targets[sm.sector] = max(targets.get(sm.sector, 0.0), sm.confidence)

        if not targets:
            continue

        for sector, sector_confidence in targets.items():
            weight = base_weight * sector_confidence
            effective_weight = weight * engagement_factor
            _record_post(by_sector[sector], post, sentiment_score,
                         effective_weight, weight, engagement_factor, day_key)

    out: Dict[str, dict] = {}
    for sector, slot in by_sector.items():
        finalized = _finalize_slot(sector, slot, label_field="sector")
        if finalized is not None:
            out[sector] = finalized
    return out


def aggregate_events(posts: List[ScoredPost]) -> Dict[str, dict]:
    """Per-macro-event weighted sentiment."""
    by_event: Dict[str, dict] = defaultdict(_empty_slot)

    for post in posts:
        if not post.event_mentions:
            continue
        sentiment_score = post.sentiment_score()
        base_weight = post.base_weight()
        engagement_factor = post.engagement_factor()
        day_key = (post.timestamp or "")[:10]

        for em in post.event_mentions:
            weight = base_weight * em.confidence
            effective_weight = weight * engagement_factor
            _record_post(by_event[em.event], post, sentiment_score,
                         effective_weight, weight, engagement_factor, day_key)

    out: Dict[str, dict] = {}
    for event, slot in by_event.items():
        finalized = _finalize_slot(event, slot, label_field="event")
        if finalized is not None:
            out[event] = finalized
    return out


def aggregate_indices(posts: List[ScoredPost]) -> Dict[str, dict]:
    """Per-index (EGX30 / EGX70 / EGX100) weighted sentiment.

    This is the headline market-level layer the redesign targets. Two paths
    contribute to an index:

      1. Ticker mentions roll up via ``indices_from_ticker_mentions()`` — a
         COMI mention feeds EGX30 + EGX100, a mid-cap feeds EGX70 + EGX100.
      2. Direct index-term mentions ("EGX30", "المؤشر الثلاثيني") arrive as
         ``EGX_30`` / ``EGX_70`` / ``EGX_100`` pseudo-symbols and route straight
         into the matching bucket (strongest signal).

    EGX100 deliberately double-counts (it is the union) so it is the broadest,
    highest-volume index view; EGX30 / EGX70 are the blue-chip / mid-cap splits.
    """
    by_index: Dict[str, dict] = defaultdict(_empty_slot)

    for post in posts:
        sentiment_score = post.sentiment_score()
        base_weight = post.base_weight()
        engagement_factor = post.engagement_factor()
        day_key = (post.timestamp or "")[:10]

        targets: Dict[str, float] = {}

        # Path 2 — direct index-term mentions (strongest).
        ticker_syms: List[str] = []
        for mention in post.mentions:
            sym = mention.symbol
            code = index_pseudo_symbol_to_code(sym)
            if code is not None:
                targets[code] = max(targets.get(code, 0.0), mention.confidence)
            elif not sym.startswith("EGX_"):
                ticker_syms.append(sym)

        # Path 1 — ticker roll-up (slightly damped vs a direct index mention).
        for code, conf in indices_from_ticker_mentions(ticker_syms).items():
            targets[code] = max(targets.get(code, 0.0), conf)

        if not targets:
            continue

        for index_code, index_confidence in targets.items():
            weight = base_weight * index_confidence
            effective_weight = weight * engagement_factor
            _record_post(by_index[index_code], post, sentiment_score,
                         effective_weight, weight, engagement_factor, day_key)

    out: Dict[str, dict] = {}
    for index_code, slot in by_index.items():
        finalized = _finalize_slot(index_code, slot, label_field="index")
        if finalized is not None:
            out[index_code] = finalized
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
    per_sector: Dict[str, dict] | None = None,
    per_event: Dict[str, dict] | None = None,
    per_index: Dict[str, dict] | None = None,
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

    sector_sentiment: Dict[str, dict] = {}
    for sector, signal in (per_sector or {}).items():
        if signal["n"] < MIN_MENTIONS_PER_SECTOR:
            continue
        sector_sentiment[sector] = _scale_signal(signal, confidence_multiplier)

    event_sentiment: Dict[str, dict] = {}
    for event, signal in (per_event or {}).items():
        if signal["n"] < MIN_MENTIONS_PER_EVENT:
            continue
        event_sentiment[event] = _scale_signal(signal, confidence_multiplier)

    index_sentiment: Dict[str, dict] = {}
    for index_code, signal in (per_index or {}).items():
        if signal["n"] < MIN_MENTIONS_PER_INDEX:
            continue
        index_sentiment[index_code] = _scale_signal(signal, confidence_multiplier)

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
            "min_mentions_per_sector": MIN_MENTIONS_PER_SECTOR,
            "min_mentions_per_event": MIN_MENTIONS_PER_EVENT,
            "min_mentions_per_index": MIN_MENTIONS_PER_INDEX,
        },
    }

    return {
        "status": "OK",
        "reason": reason,
        # Layered output: market → indices → sectors → tickers → events.
        "market_sentiment": market_signal,
        "index_sentiment": index_sentiment,
        "sector_sentiment": sector_sentiment,
        "per_stock_sentiment": per_stock,
        "event_sentiment": event_sentiment,
        # Convenience views kept for the dashboard / API.
        "top_stocks": top_stocks,
        "all_stocks": all_stocks,
        "metadata": metadata,
    }
