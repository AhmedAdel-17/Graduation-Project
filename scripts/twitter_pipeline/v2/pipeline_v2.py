"""
EGX Trading-Signal Sentiment Pipeline - v2 orchestrator.

Pipeline stages:

  1. SCRAPE        - pluggable sources (reddit_targeted, telegram_public,
                     facebook_apify — PRIMARY)
  2. RELEVANCE     - Layer-0: strict EGX classifier, with a lighter
                     Facebook-specific EGX guard
  3. ENTITIES      - extract per-post stock symbols
  4. INTENT        - trader-voice detection (BUY / SELL / HOLD / ...)
  5. CONTENT-TYPE  - opinion / news / analysis / question
  6. QUALITY GATE  - keep useful posts, downgrade weak ones, hard-drop only
                     unusable noise
  7. SENTIMENT     - project's multi-model engine (FinBERT/CAMeLBERT/XLM-R)
                     with VADER baseline for benchmarking
  8. AGGREGATE     - weighted per-stock + market signal

Outputs: console summary + results_<stamp>.json + signals_<stamp>.csv.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import List

# import path setup
_HERE = os.path.dirname(os.path.abspath(__file__))
_V1 = os.path.abspath(os.path.join(_HERE, ".."))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for path in (_ROOT, _V1, _HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

# v1 modules we re-use
from scraper import Post  # noqa: E402
from relevance import classify as classify_relevance  # noqa: E402

# shared normalization utilities
from tradingagents.utils.text_preprocessor import normalize_text  # noqa: E402

# v2 modules
from entities import (  # noqa: E402
    extract as extract_entities,
    has_market_term,
)
from intent import detect as detect_intent  # noqa: E402
from content_type import classify as classify_content  # noqa: E402
from aggregator import (  # noqa: E402
    MIN_MENTIONS_PER_STOCK,
    MIN_TOTAL_POSTS,
    ScoredPost,
    aggregate,
    render_summary,
    split_outputs,
)
from quality_gate import evaluate as quality_evaluate  # noqa: E402
from sentiment_validation import (  # noqa: E402
    compute_validation_stats,
    score_items_with_sentiment,
)

from sources import (  # noqa: E402
    facebook_apify,
    reddit_targeted,
    telegram_public,
    # Deleted in PR 10 (were always 0 posts in production):
    #   facebook_groups.py  — Playwright + pw-cookies fallback; duplicate of Apify
    #   twitter_authed.py   — auth cookies expired; X.com blocks anonymous scraping
    #   mubasher_news.py    — CSS selectors broken upstream
)

# Load APIFY_API_TOKEN and friends from .env at the repo root.
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

LOG_DIR = os.path.join(_HERE, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(LOG_DIR, "pipeline_v2.log"),
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger("egx.v2")


def _normalized_text_key(text: str) -> str:
    normalized = normalize_text(text or "")
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()
    return normalized


def _passes_facebook_egx_guard(post: Post) -> bool:
    mentions = extract_entities(post.text)
    return bool(mentions) or has_market_term(post.text)


# ---------------------------------------------------------------------------
# Stage 1: scrape
# ---------------------------------------------------------------------------

def stage_scrape() -> tuple[List[Post], dict]:
    log.info("=" * 70)
    log.info("STAGE 1 - SCRAPE")
    log.info("=" * 70)

    by_source: dict[str, list] = {}
    raw: List[Post] = []

    # Active sources (PR 3: dead sources unwired; PR 10: zombie files deleted).
    # Source priority: facebook_apify (PRIMARY, bypasses Layer-0 relevance) →
    # telegram (highest SNR) → reddit (market/sector only).
    # Deleted in PR 10: facebook_groups.py, twitter_authed.py, mubasher_news.py
    # (all returned 0 posts in production — see MEMORY.md §Q, §R, §S).
    for name, fn in [
        ("facebook_apify", lambda: facebook_apify.scrape(results_per_group=300)),
        ("telegram", lambda: telegram_public.scrape(max_total=120)),
        ("reddit", lambda: reddit_targeted.scrape(per_query=12, max_total=160)),
    ]:
        try:
            batch = fn()
        except Exception as exc:
            log.exception("source %s failed: %s", name, exc)
            batch = []
        by_source[name] = batch
        raw.extend(batch)

    seen_urls = set()
    url_dedup: List[Post] = []
    for post in raw:
        if post.url in seen_urls:
            continue
        seen_urls.add(post.url)
        url_dedup.append(post)

    seen_text = set()
    final_dedup: List[Post] = []
    text_dupes = 0
    for post in url_dedup:
        text_key = _normalized_text_key(post.text)
        if text_key and text_key in seen_text:
            text_dupes += 1
            continue
        if text_key:
            seen_text.add(text_key)
        final_dedup.append(post)

    log.info(
        "Raw collected: %d  (after URL de-dup: %d, text de-dup: %d)",
        len(raw),
        len(url_dedup),
        len(final_dedup),
    )
    if text_dupes:
        log.info("  cross-source normalized-text duplicates removed: %d", text_dupes)
    for name, batch in by_source.items():
        log.info("  %-15s %4d", name, len(batch))
    return final_dedup, {name: len(batch) for name, batch in by_source.items()}


# ---------------------------------------------------------------------------
# Stage 2: relevance gate (Layer-0)
# ---------------------------------------------------------------------------

LIGHT_GUARDED_PLATFORMS = {"facebook"}


def stage_relevance(posts: List[Post]) -> tuple[List[Post], List[dict]]:
    log.info(
        "STAGE 2 - RELEVANCE (Layer-0 strict EGX gate, light-guard=%s)",
        sorted(LIGHT_GUARDED_PLATFORMS),
    )
    kept: List[Post] = []
    dropped_examples: List[dict] = []
    light_guard_kept = 0

    for post in posts:
        if post.platform in LIGHT_GUARDED_PLATFORMS and _passes_facebook_egx_guard(post):
            kept.append(post)
            light_guard_kept += 1
            continue

        ok, debug = classify_relevance(post.text)
        if ok:
            kept.append(post)
        elif len(dropped_examples) < 5:
            dropped_examples.append({"text": post.text[:120], "hits": debug})

    precision = 100.0 * len(kept) / max(1, len(posts))
    log.info(
        "  relevant: %d / %d  (precision = %.1f%%)  [facebook-light-guard: %d]",
        len(kept),
        len(posts),
        precision,
        light_guard_kept,
    )
    return kept, dropped_examples


# ---------------------------------------------------------------------------
# Stage 3-5: enrich each post (entities, intent, content-type)
# ---------------------------------------------------------------------------

def stage_enrich(posts: List[Post]) -> List[dict]:
    """Return list of dicts ready for sentiment + aggregator."""
    log.info("STAGE 3-5 - ENRICH (entities / intent / content-type)")
    out = []
    intent_counts: dict[str, int] = {}
    content_counts: dict[str, int] = {}

    for post in posts:
        mentions = extract_entities(post.text)
        intent = detect_intent(post.text)
        content = classify_content(post.text)
        for label in intent.intents or ["NONE"]:
            intent_counts[label] = intent_counts.get(label, 0) + 1
        content_counts[content.label] = content_counts.get(content.label, 0) + 1
        out.append(
            {
                "post": post,
                "mentions": mentions,
                "intent": intent.to_dict(),
                "content": content.to_dict(),
            }
        )

    log.info(
        "  intent breakdown : %s",
        {key: value for key, value in sorted(intent_counts.items(), key=lambda item: -item[1])},
    )
    log.info(
        "  content types    : %s",
        {key: value for key, value in sorted(content_counts.items(), key=lambda item: -item[1])},
    )
    return out


# ---------------------------------------------------------------------------
# Stage 6: quality gate + sentiment
# ---------------------------------------------------------------------------

def stage_quality_gate(enriched: List[dict]) -> tuple[List[dict], dict]:
    log.info("STAGE 6 - QUALITY GATE")

    kept: List[dict] = []
    dropped: List[dict] = []
    bucket_counts = {"ok": 0, "downgraded": 0, "hard_drop": 0}
    hard_drop_reasons: dict[str, int] = {}
    downgraded_reasons: dict[str, int] = {}

    for record in enriched:
        decision = quality_evaluate(
            record["post"].text,
            record["intent"],
            record["content"],
        )
        record["quality_gate"] = decision
        bucket = decision["bucket"]
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        reason = decision["reason"]
        reason_base = reason.split("(")[0]

        if decision["keep"]:
            kept.append(record)
            if bucket == "downgraded":
                downgraded_reasons[reason_base] = downgraded_reasons.get(reason_base, 0) + 1
        else:
            dropped.append(
                {
                    "reason": reason,
                    "intent": record["intent"]["intents"],
                    "content": record["content"]["label"],
                    "text": record["post"].text[:120],
                }
            )
            hard_drop_reasons[reason_base] = hard_drop_reasons.get(reason_base, 0) + 1

    log.info(
        "  quality kept: %d / %d   mix=%s   downgraded-kept=%s   hard-drop=%s",
        len(kept),
        len(enriched),
        bucket_counts,
        downgraded_reasons,
        hard_drop_reasons,
    )
    for example in dropped[:3]:
        log.info(
            "  rejected_post: reason=%s content=%s text=%s",
            example["reason"],
            example["content"],
            example["text"],
        )
    return kept, {
        "counts": bucket_counts,
        "downgraded_reasons": downgraded_reasons,
        "hard_drop_reasons": hard_drop_reasons,
        "examples": dropped[:5],
    }


def stage_sentiment(enriched: List[dict]) -> tuple[List[dict], dict]:
    log.info("STAGE 7 - SENTIMENT (project engine + VADER baseline)")
    if not enriched:
        return enriched, {
            "total_posts_processed": 0,
            "model_usage": {},
            "rejected_posts": [],
            "rejected_count": 0,
            "disagreement_count": 0,
            "disagreement_examples": [],
        }

    items = []
    for record in enriched:
        items.append(
            {
                **record["post"].to_dict(),
                "mentions": [mention.to_dict() for mention in record["mentions"]],
                "intent": record["intent"],
                "content": record["content"],
                "quality_gate": record.get("quality_gate", {}),
            }
        )

    scored_items, sentiment_debug = score_items_with_sentiment(items)
    for record, item in zip(enriched, scored_items):
        record["sentiment"] = item["sentiment"]
        record["sentiment_vader"] = item["sentiment_vader"]

    log.info("  total_posts_processed : %d", sentiment_debug["total_posts_processed"])
    log.info("  sentiment_models      : %s", sentiment_debug["model_usage"])
    log.info(
        "  sentiment_disagreements: %d",
        sentiment_debug["disagreement_count"],
    )
    return enriched, sentiment_debug


# ---------------------------------------------------------------------------
# Stage 8: aggregate
# ---------------------------------------------------------------------------

def stage_aggregate(enriched: List[dict]) -> dict:
    log.info("STAGE 8 - WEIGHTED AGGREGATION")
    scored: List[ScoredPost] = []
    for record in enriched:
        post = record["post"]
        scored.append(
            ScoredPost(
                text=post.text,
                url=post.url,
                platform=post.platform,
                source=post.source,
                timestamp=post.timestamp,
                engagement=post.engagement,
                mentions=record["mentions"],
                intent=record["intent"],
                content=record["content"],
                sentiment=record["sentiment"],
            )
        )

    per_symbol = aggregate(scored)
    for line in render_summary(per_symbol):
        log.info("%s", line)
    return per_symbol


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    log.info("######  EGX SENTIMENT PIPELINE v2  ######")

    raw, source_counts = stage_scrape()
    if not raw:
        log.warning("No posts collected - continuing with neutral output.")

    relevant, dropped_examples = stage_relevance(raw)
    if raw and not relevant:
        log.warning("No EGX-relevant posts after Layer-0 gate - continuing with neutral output.")

    pre_gate = stage_enrich(relevant)
    enriched, quality_report = stage_quality_gate(pre_gate)
    if pre_gate and not enriched:
        log.warning("No posts left after quality gate - continuing with neutral output.")

    enriched, sentiment_debug = stage_sentiment(enriched)
    per_symbol = stage_aggregate(enriched)
    stats = compute_validation_stats(enriched, per_symbol)
    output = split_outputs(
        per_symbol,
        total_posts=len(relevant),
        used_posts=len(enriched),
        source_counts=source_counts,
    )

    # ---- METRICS ---------------------------------------------------------
    n_raw = sum(source_counts.values())
    n_relevant = len(relevant)
    n_intent = sum(
        1
        for record in pre_gate
        if record["intent"].get("intents") not in (None, [], ["NONE"])
    )
    n_opinion = sum(1 for record in pre_gate if record["content"]["label"] == "OPINION")
    n_with_symbol = sum(1 for record in pre_gate if record["mentions"])
    n_quality = len(enriched)

    log.info("=" * 70)
    log.info("PIPELINE METRICS")
    log.info("  raw_posts            : %d", n_raw)
    log.info("  relevant_posts       : %d", n_relevant)
    log.info("  search_precision     : %.1f%%", 100.0 * n_relevant / max(1, n_raw))
    log.info(
        "  posts_with_intent    : %d / %d  (%.1f%%)",
        n_intent,
        n_relevant,
        100.0 * n_intent / max(1, n_relevant),
    )
    log.info(
        "  opinion_grade_posts  : %d / %d  (%.1f%%)",
        n_opinion,
        n_relevant,
        100.0 * n_opinion / max(1, n_relevant),
    )
    log.info(
        "  posts_with_symbol    : %d / %d  (%.1f%%)",
        n_with_symbol,
        n_relevant,
        100.0 * n_with_symbol / max(1, n_relevant),
    )
    log.info(
        "  quality_gate_kept    : %d / %d  (%.1f%%)",
        n_quality,
        n_relevant,
        100.0 * n_quality / max(1, n_relevant),
    )
    log.info("  symbols_covered      : %d", len([key for key in per_symbol if key != "EGX_MARKET"]))
    log.info("  status               : %s", output["status"])
    if output.get("reason"):
        log.info("  signal_note          : %s", output["reason"])
    log.info(
        "  sentiment_stats      : avg=%+.3f bullish=%.1f%% bearish=%.1f%% neutral=%.1f%%",
        stats["avg_sentiment"],
        stats["bullish_pct"] * 100.0,
        stats["bearish_pct"] * 100.0,
        stats["neutral_pct"] * 100.0,
    )

    market_signal = output["market_sentiment"]
    log.info(
        "  market_sentiment     : %s  (n=%d  conf=%.2f  score=%+.2f)",
        market_signal["label"].upper(),
        market_signal["n"],
        market_signal["confidence"],
        market_signal["weighted_sentiment"],
    )
    log.info(
        "  per_stock kept (n>=%d): %d",
        MIN_MENTIONS_PER_STOCK,
        len(output["per_stock_sentiment"]),
    )
    for card in output["top_stocks"][:5]:
        log.info(
            "    %-6s  mentions=%-3d  score=%+.2f  conf=%.2f  -> %s",
            card["symbol"],
            card["mentions"],
            card["score"],
            card["confidence"],
            card["sentiment"].upper(),
        )
    log.info("=" * 70)

    # ---- PERSIST ---------------------------------------------------------
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = os.path.join(LOG_DIR, f"results_{stamp}.json")
    csv_path = os.path.join(LOG_DIR, f"signals_{stamp}.csv")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_counts": source_counts,
        "thresholds": {
            "min_total_posts": MIN_TOTAL_POSTS,
            "min_mentions_per_stock": MIN_MENTIONS_PER_STOCK,
        },
        "status": output["status"],
        "reason": output.get("reason", ""),
        "market_sentiment": output["market_sentiment"],
        "per_stock_sentiment": output["per_stock_sentiment"],
        "top_stocks": output["top_stocks"],
        "all_stocks": output["all_stocks"],
        "metadata": output["metadata"],
        "stats": stats,
        "metrics": {
            "raw_posts": n_raw,
            "relevant_posts": n_relevant,
            "quality_kept": n_quality,
            "search_precision_pct": round(100.0 * n_relevant / max(1, n_raw), 2),
            "posts_with_intent": n_intent,
            "opinion_grade_posts": n_opinion,
            "posts_with_symbol": n_with_symbol,
        },
        "sentiment_debug": sentiment_debug,
        "quality_gate": quality_report,
        "per_symbol_full": per_symbol,
        "items": [
            {
                **record["post"].to_dict(),
                "mentions": [mention.to_dict() for mention in record["mentions"]],
                "intent": record["intent"],
                "content": record["content"],
                "quality_gate": record["quality_gate"],
                "sentiment": record.get("sentiment", {}),
                "sentiment_vader": record.get("sentiment_vader", {}),
            }
            for record in enriched
        ],
        "dropped_examples": dropped_examples,
    }

    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
    log.info("Wrote %s", json_path)

    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "symbol",
                "n",
                "weighted_sentiment",
                "label",
                "confidence",
                "intent_breakdown",
                "by_source",
                "by_content_type",
            ]
        )
        for symbol, signal in per_symbol.items():
            writer.writerow(
                [
                    symbol,
                    signal["n"],
                    signal["weighted_sentiment"],
                    signal["label"],
                    signal["confidence"],
                    json.dumps(signal["intent_breakdown"], ensure_ascii=False),
                    json.dumps(signal["by_source"]),
                    json.dumps(signal["by_content_type"]),
                ]
            )
    log.info("Wrote %s", csv_path)
    log.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
