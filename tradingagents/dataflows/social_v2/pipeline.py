"""EGX Trading-Signal Sentiment Pipeline — production orchestrator.

Stages:
    1. SCRAPE         Facebook (Apify, PRIMARY) + Reddit
    2. RELEVANCE      Layer-0 EGX gate (FB bypasses via group-level guard)
    3. ENRICH         entities + intent + content-type
    4. QUALITY GATE   permissive — downgrade weak posts instead of dropping
    5. SENTIMENT      multi-model transformer engine + VADER baseline
    6. AGGREGATE      weighted per-stock + market signal
    7. ARCHIVE        best-effort Postgres write for historical corpus

This is callable as a library (`run_pipeline()`) from the LangGraph agent
prefetch path. The standalone script entrypoint is preserved at
`scripts/social_pipeline/v2/pipeline.py` for offline runs.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, List

from tradingagents.utils.text_preprocessor import normalize_text

from . import post_store
from .aggregator import (
    MIN_MENTIONS_PER_STOCK,
    MIN_TOTAL_POSTS,
    ScoredPost,
    aggregate,
    aggregate_events,
    aggregate_indices,
    aggregate_sectors,
    split_outputs,
)
from .content_type import classify as classify_content
from .entities import extract as extract_entities, has_market_term
from .events import extract_events
from .intent import detect as detect_intent
from .models import Post
from .quality_gate import evaluate as quality_evaluate
from .relevance import classify as classify_relevance
from .sectors import extract_sector_mentions
from .sentiment_runner import analyze_egx_batch, analyze_vader_batch
from .sources import (
    bing_news_ar,
    egypt_news_rss,
    facebook_apify,
    facebook_dork,
    google_news_ar,
    investing_com_ar,
    mubasher_news,
    reddit_targeted,
    telegram_public,
)

log = logging.getLogger("tradingagents.social_v2.pipeline")

# Sources whose origin-level curation already proves EGX relevance:
#   * facebook — posts come from confirmed EGX Arabic groups
#   * telegram — posts come from confirmed EGX channels
#   * news     — feed URL is the EGX-specific Mubasher endpoint
# These bypass the full Layer-0 classifier and pass if they extract any
# entity OR market term.
LIGHT_GUARDED_PLATFORMS = {"facebook", "telegram", "news"}


def _normalized_text_key(text: str) -> str:
    normalized = normalize_text(text or "")
    return re.sub(r"\s+", " ", normalized).strip().lower()


def _passes_facebook_egx_guard(post: Post) -> bool:
    """Light EGX guard for curated-origin platforms.

    Accepts any of four EGX-relevance signals — the source itself is curated
    (EGX-Arabic FB groups, EGX-specific RSS, Egyptian news searches), so any
    layer of the layered sentiment system is a valid reason to keep the post:

      * a ticker / company mention   → ticker layer
      * a market-index term          → market layer
      * a sector keyword             → sector layer
      * an event keyword             → event layer
    """
    if extract_entities(post.text):
        return True
    if has_market_term(post.text):
        return True
    if extract_sector_mentions(post.text):
        return True
    if extract_events(post.text):
        return True
    return False


def stage_scrape(
    fb_per_group: int = 200,
    reddit_per_query: int = 12,
    reddit_max: int = 160,
    mubasher_per_feed: int = 80,
) -> tuple[List[Post], dict]:
    log.info("STAGE 1 — SCRAPE")
    by_source: dict[str, list] = {}
    raw: List[Post] = []

    # Source order matters only for log clarity; cross-source dedup happens
    # later on URL + normalized text. The diversification is the point —
    # if Apify is rate-limited, Mubasher news + Telegram + Reddit still
    # deliver a signal.
    sources = [
        ("facebook_apify", lambda: facebook_apify.scrape(results_per_group=fb_per_group)),
        ("mubasher", lambda: mubasher_news.scrape(max_per_feed=mubasher_per_feed)),
        ("google_news_ar", lambda: google_news_ar.scrape()),
        ("bing_news_ar", lambda: bing_news_ar.scrape()),
        ("egypt_news_rss", lambda: egypt_news_rss.scrape()),
        ("investing_com_ar", lambda: investing_com_ar.scrape()),
        ("telegram_public", lambda: telegram_public.scrape()),
        ("reddit", lambda: reddit_targeted.scrape(per_query=reddit_per_query, max_total=reddit_max)),
    ]
    for name, fn in sources:
        try:
            batch = fn()
        except Exception as exc:
            log.exception("source %s failed: %s", name, exc)
            batch = []
        by_source[name] = batch
        raw.extend(batch)

    # Conditional Facebook fallback. facebook_apify is PRIMARY but burns a
    # paid Apify quota; when it comes back thin (quota exhausted / cold cache),
    # recover public FB group posts via Google dorking so the retail-social
    # layer doesn't go dark. Gated on the Apify yield so the scarce dork quota
    # (free CSE is 100 q/day) is only spent when actually needed.
    fb_apify_count = len(by_source.get("facebook_apify", []))
    dork_threshold = int(os.getenv("FACEBOOK_DORK_MIN_APIFY_POSTS", "10"))
    if fb_apify_count < dork_threshold:
        try:
            dork_batch = facebook_dork.scrape()
        except Exception as exc:
            log.exception("source facebook_dork failed: %s", exc)
            dork_batch = []
        by_source["facebook_dork"] = dork_batch
        raw.extend(dork_batch)
        log.info(
            "Apify thin (%d < %d) — facebook_dork fallback yielded %d posts",
            fb_apify_count, dork_threshold, len(dork_batch),
        )
    else:
        by_source["facebook_dork"] = []

    # URL dedup
    seen_urls = set()
    url_dedup: List[Post] = []
    for post in raw:
        if post.url in seen_urls:
            continue
        seen_urls.add(post.url)
        url_dedup.append(post)

    # Normalized-text dedup
    seen_text = set()
    final: List[Post] = []
    for post in url_dedup:
        key = _normalized_text_key(post.text)
        if key and key in seen_text:
            continue
        if key:
            seen_text.add(key)
        final.append(post)

    log.info(
        "Scraped %d raw (url-dedup %d, text-dedup %d) — %s",
        len(raw),
        len(url_dedup),
        len(final),
        {name: len(batch) for name, batch in by_source.items()},
    )
    return final, {name: len(batch) for name, batch in by_source.items()}


def stage_relevance(posts: List[Post]) -> List[Post]:
    """Layer-0 EGX relevance gate.

    Platform-specific tightness:
      * facebook — light guard (group itself is the EGX signal); accept if
        any extracted Mention OR market term.
      * reddit   — strict gate: requires a real ticker/issuer mention
        (extract_entities returns non-empty AND not just a generic EGX_
        index match). Reddit full-text search returns many off-topic posts
        that match on a single English finance verb + "Egypt" — without
        this guard we accept tourism posts as bank-stock signal.
      * default  — classify_relevance() (finance context AND ticker/market).
    """
    log.info("STAGE 2 — RELEVANCE")
    kept: List[Post] = []
    for post in posts:
        if post.platform in LIGHT_GUARDED_PLATFORMS and _passes_facebook_egx_guard(post):
            kept.append(post)
            continue
        if post.platform == "reddit":
            mentions = extract_entities(post.text)
            # Reject if no mentions, or if all mentions are generic EGX_
            # index terms (which the broad "egx" substring can match in
            # any unrelated post containing those 3 chars).
            real_mentions = [m for m in mentions if not m.symbol.startswith("EGX_")]
            if not real_mentions:
                continue
            kept.append(post)
            continue
        ok, _ = classify_relevance(post.text)
        if ok:
            kept.append(post)
    log.info("Relevance: %d / %d kept", len(kept), len(posts))
    return kept


def stage_enrich(posts: List[Post]) -> List[dict]:
    log.info("STAGE 3 — ENRICH")
    out: List[dict] = []
    for post in posts:
        out.append({
            "post": post,
            "mentions": extract_entities(post.text),
            "sector_mentions": extract_sector_mentions(post.text),
            "event_mentions": extract_events(post.text),
            "intent": detect_intent(post.text).to_dict(),
            "content": classify_content(post.text).to_dict(),
        })
    return out


def stage_quality_gate(enriched: List[dict]) -> List[dict]:
    log.info("STAGE 4 — QUALITY GATE")
    kept: List[dict] = []
    for record in enriched:
        decision = quality_evaluate(
            record["post"].text, record["intent"], record["content"]
        )
        record["quality_gate"] = decision
        if decision["keep"]:
            kept.append(record)
    log.info("Quality gate: %d / %d kept", len(kept), len(enriched))
    return kept


def stage_sentiment(enriched: List[dict]) -> List[dict]:
    log.info("STAGE 5 — SENTIMENT")
    if not enriched:
        return enriched
    texts = [record["post"].text for record in enriched]
    egx_results = analyze_egx_batch(texts)
    vader_results = analyze_vader_batch(texts)
    for record, egx, vader in zip(enriched, egx_results, vader_results):
        record["sentiment"] = egx
        record["sentiment_vader"] = vader
    return enriched


def stage_aggregate(enriched: List[dict]) -> Dict[str, Dict[str, dict]]:
    """Build the layered aggregate.

    Returns a dict with four sub-dicts:
      * ``per_symbol`` (market + per-ticker, as before)
      * ``per_index`` (EGX30 / EGX70 / EGX100 — the market-level layer)
      * ``per_sector``
      * ``per_event``
    """
    log.info("STAGE 6 — AGGREGATE (layered)")
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
                sector_mentions=record.get("sector_mentions", []),
                event_mentions=record.get("event_mentions", []),
            )
        )
    return {
        "per_symbol": aggregate(scored),
        "per_index": aggregate_indices(scored),
        "per_sector": aggregate_sectors(scored),
        "per_event": aggregate_events(scored),
    }


def run_pipeline(
    fb_per_group: int = 200,
    reddit_per_query: int = 12,
    reddit_max: int = 160,
    archive_posts: bool = True,
) -> dict:
    """Run the full pipeline and return the production payload.

    Returns dict with keys:
        status, market_sentiment, per_stock_sentiment, top_stocks,
        all_stocks, metadata, per_symbol_full, source_counts
    """
    raw, source_counts = stage_scrape(fb_per_group, reddit_per_query, reddit_max)
    relevant = stage_relevance(raw)
    pre_gate = stage_enrich(relevant)
    enriched = stage_quality_gate(pre_gate)
    enriched = stage_sentiment(enriched)
    aggregated = stage_aggregate(enriched)

    output = split_outputs(
        aggregated["per_symbol"],
        total_posts=len(relevant),
        used_posts=len(enriched),
        source_counts=source_counts,
        per_sector=aggregated["per_sector"],
        per_event=aggregated["per_event"],
        per_index=aggregated["per_index"],
    )
    output["per_symbol_full"] = aggregated["per_symbol"]
    output["per_index_full"] = aggregated["per_index"]
    output["per_sector_full"] = aggregated["per_sector"]
    output["per_event_full"] = aggregated["per_event"]
    output["source_counts"] = source_counts

    if archive_posts and enriched:
        try:
            n = post_store.archive(enriched)
            if n:
                log.info("STAGE 7 — ARCHIVE: %d rows accepted", n)
        except Exception as exc:
            log.warning("Archive failed (continuing): %s", exc)

    log.info(
        "Pipeline done: market=%s (n=%d conf=%.2f) indices=%d sectors=%d tickers=%d events=%d MIN_TOTAL=%d",
        output["market_sentiment"]["label"].upper(),
        output["market_sentiment"]["n"],
        output["market_sentiment"]["confidence"],
        len(output.get("index_sentiment", {})),
        len(output.get("sector_sentiment", {})),
        len(output["per_stock_sentiment"]),
        len(output.get("event_sentiment", {})),
        MIN_TOTAL_POSTS,
    )
    return output
