"""
Reusable sentiment scoring + validation utilities for the EGX v2 pipeline.

This module is shared by the production pipeline and the pytest harness so we
only maintain one rescoring / re-aggregation path.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_V1 = os.path.abspath(os.path.join(_HERE, ".."))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for path in (_ROOT, _V1, _HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

from aggregator import ScoredPost, aggregate, split_outputs  # noqa: E402
from entities import Mention  # noqa: E402
from sentiment import analyze_egx_batch, analyze_vader_batch  # noqa: E402

LOG_DIR = os.path.join(_HERE, "logs")
log = logging.getLogger("egx.v2.validation")


def _copy_item(item: dict) -> dict:
    copied = dict(item)
    copied["mentions"] = [dict(mention) for mention in item.get("mentions", [])]
    copied["intent"] = dict(item.get("intent", {}))
    copied["content"] = dict(item.get("content", {}))
    copied["quality_gate"] = dict(item.get("quality_gate", {}))
    if item.get("sentiment"):
        copied["sentiment"] = dict(item["sentiment"])
    if item.get("sentiment_vader"):
        copied["sentiment_vader"] = dict(item["sentiment_vader"])
    return copied


def _coerce_mentions(raw_mentions: List[dict]) -> List[Mention]:
    mentions: List[Mention] = []
    for mention in raw_mentions or []:
        symbol = mention.get("symbol")
        if not symbol:
            continue
        mentions.append(
            Mention(
                symbol=symbol,
                confidence=float(mention.get("confidence", 0.0)),
                evidence=list(mention.get("evidence", [])),
            )
        )
    return mentions


def find_latest_results_json(log_dir: str | None = None) -> str:
    base = Path(log_dir or LOG_DIR)
    candidates = sorted(
        base.glob("results_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"No results_*.json files found under {base}")
    return str(candidates[0])


def load_pipeline_output(path: str | None = None) -> dict:
    target = path or find_latest_results_json()
    with open(target, "r", encoding="utf-8") as handle:
        return json.load(handle)


def score_items_with_sentiment(items: List[dict]) -> Tuple[List[dict], dict]:
    scored_items: List[dict] = []
    rejected_posts: List[dict] = []

    for item in items:
        text = (item.get("text") or "").strip()
        if not text:
            rejected_posts.append(
                {
                    "reason": "empty-text",
                    "platform": item.get("platform"),
                    "url": item.get("url"),
                }
            )
            continue
        scored_items.append(_copy_item(item))

    texts = [item["text"] for item in scored_items]
    if not texts:
        return scored_items, {
            "total_posts_processed": 0,
            "model_usage": {},
            "rejected_posts": rejected_posts[:5],
            "rejected_count": len(rejected_posts),
            "disagreement_count": 0,
            "disagreement_examples": [],
        }

    egx_results = analyze_egx_batch(texts)
    vader_results = analyze_vader_batch(texts)

    model_usage: Counter = Counter()
    disagreement_examples: List[dict] = []
    disagreement_count = 0

    for index, (item, egx_signal, vader_signal) in enumerate(
        zip(scored_items, egx_results, vader_results),
        start=1,
    ):
        item["sentiment"] = dict(egx_signal)
        item["sentiment_vader"] = dict(vader_signal)
        model_usage[egx_signal.get("model_used", "unknown")] += 1

        log.info(
            "sentiment[%03d] platform=%s model=%s label=%s score=%+.3f vader=%s",
            index,
            item.get("platform"),
            egx_signal.get("model_used"),
            egx_signal.get("label"),
            float(egx_signal.get("score", 0.0)),
            vader_signal.get("label"),
        )

        if egx_signal.get("label") != vader_signal.get("label"):
            disagreement_count += 1
            if len(disagreement_examples) < 5:
                disagreement_examples.append(
                    {
                        "platform": item.get("platform"),
                        "model_used": egx_signal.get("model_used"),
                        "egx_label": egx_signal.get("label"),
                        "vader_label": vader_signal.get("label"),
                        "text": item.get("text", "")[:160],
                    }
                )

    if rejected_posts:
        log.warning("Rejected %d empty posts before sentiment scoring", len(rejected_posts))
        for rejected in rejected_posts[:5]:
            log.warning("  rejected: %s", rejected)

    log.info(
        "Sentiment model usage: %s",
        dict(model_usage),
    )
    log.info(
        "Sentiment disagreements (EGX vs VADER): %d / %d",
        disagreement_count,
        len(scored_items),
    )

    return scored_items, {
        "total_posts_processed": len(scored_items),
        "model_usage": dict(model_usage),
        "rejected_posts": rejected_posts[:5],
        "rejected_count": len(rejected_posts),
        "disagreement_count": disagreement_count,
        "disagreement_examples": disagreement_examples,
    }


def recompute_aggregation(
    items: List[dict],
    source_counts: dict | None = None,
    total_posts: int | None = None,
) -> Tuple[dict, dict]:
    scored_posts: List[ScoredPost] = []
    for item in items:
        scored_posts.append(
            ScoredPost(
                text=item.get("text", ""),
                url=item.get("url", ""),
                platform=item.get("platform", "unknown"),
                source=item.get("source", "unknown"),
                timestamp=item.get("timestamp", ""),
                engagement=int(item.get("engagement") or 0),
                mentions=_coerce_mentions(item.get("mentions", [])),
                intent=dict(item.get("intent", {})),
                content=dict(item.get("content", {})),
                sentiment=dict(item.get("sentiment", {})),
            )
        )

    per_symbol = aggregate(scored_posts)
    output = split_outputs(
        per_symbol,
        total_posts=total_posts if total_posts is not None else len(items),
        used_posts=len(items),
        source_counts=source_counts,
    )
    return per_symbol, output


def compute_validation_stats(items: List[dict], per_symbol: dict) -> dict:
    sentiments = [item.get("sentiment", {}) for item in items if item.get("sentiment")]
    total = len(sentiments)
    if total == 0:
        return {
            "total_posts_processed": 0,
            "avg_sentiment": 0.0,
            "bullish_pct": 0.0,
            "bearish_pct": 0.0,
            "neutral_pct": 0.0,
            "per_stock": {},
        }

    counts = Counter(sentiment.get("label", "neutral") for sentiment in sentiments)
    avg_sentiment = sum(float(sentiment.get("score", 0.0)) for sentiment in sentiments) / total

    per_stock = {}
    for symbol, signal in per_symbol.items():
        if symbol.startswith("EGX_"):
            continue
        per_stock[symbol] = {
            "mentions": signal["n"],
            "avg_sentiment": signal["weighted_sentiment"],
            "confidence": signal["confidence"],
        }

    return {
        "total_posts_processed": total,
        "avg_sentiment": round(avg_sentiment, 3),
        "bullish_pct": round(counts.get("bullish", 0) / total, 3),
        "bearish_pct": round(counts.get("bearish", 0) / total, 3),
        "neutral_pct": round(counts.get("neutral", 0) / total, 3),
        "per_stock": per_stock,
    }


def validate_scored_output(
    items: List[dict],
    per_symbol: dict,
    output: dict,
    require_stock_signal: bool = True,
) -> dict:
    assert all((item.get("text") or "").strip() for item in items), "no empty text processed"

    for item in items:
        score = float(item.get("sentiment", {}).get("score", 0.0))
        assert -1.0 <= score <= 1.0, f"sentiment score out of range: {score}"

    stock_signals = [symbol for symbol in per_symbol if not symbol.startswith("EGX_")]
    if require_stock_signal:
        assert stock_signals, "at least 1 stock has sentiment"

    market_confidence = float(output.get("market_sentiment", {}).get("confidence", 0.0))
    assert 0.0 <= market_confidence <= 1.0, "market confidence must be in [0, 1]"

    return {
        "validated": True,
        "stocks_with_sentiment": len(stock_signals),
        "market_confidence": round(market_confidence, 3),
    }


def build_validation_report(path: str | None = None) -> dict:
    payload = load_pipeline_output(path)
    items, sentiment_debug = score_items_with_sentiment(payload.get("items", []))
    per_symbol, output = recompute_aggregation(
        items,
        source_counts=payload.get("source_counts"),
        total_posts=payload.get("metadata", {}).get("total_posts", len(items)),
    )
    stats = compute_validation_stats(items, per_symbol)
    validation = validate_scored_output(items, per_symbol, output)

    return {
        "market_sentiment": output["market_sentiment"],
        "top_stocks": output["top_stocks"],
        "stats": stats,
        "sentiment_debug": sentiment_debug,
        "validation": validation,
    }
