"""Weekly news scraper + categorizer for the EGX sentiment archive.

Scrapes Egyptian financial news from multiple sources, categorizes each article
by sector / market index / company / market-wide relevance, and archives
everything to the Postgres ``news_archive`` table.

Sources:
    * Enterprise Press      (English, Egypt business — Scrapling)
    * Youm7 Economy         (Arabic, major daily — Scrapling)
    * Al-Mal News           (Arabic, financial newspaper — Scrapling)
    * Amwal-Mag RSS         (Arabic, financial magazine — RSS via Scrapling)
    * NewsAPI               (bilingual, broad EGX queries — API)
    * Google News AR RSS    (Arabic, multi-axis queries — existing social_v2)
    * Mubasher RSS          (Arabic, EGX news — existing social_v2)

Categorization uses the social_v2 enrichment modules:
    * entities.extract()            → stock/company symbols
    * sectors.extract_sector_mentions() + ticker_sector() → sector tags
    * indices.ticker_to_indices()   → EGX30/70/100 membership
    * events.extract_events()       → macro/event tags
    * entities.has_market_term()    → is_market_wide flag

Prerequisites:
    * POSTGRES_URL set (else nothing is archived)
    * NEWSAPI_KEY set for NewsAPI source (others work without keys)
    * scrapling installed for web scrapers

Usage:
    python scripts/news_weekly_fetch.py
    python scripts/news_weekly_fetch.py --max-per-source 80
    python scripts/news_weekly_fetch.py --stats-only
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
log = logging.getLogger("tradingagents.news_weekly_fetch")


def _enrich_article(article: dict) -> dict:
    """Run the social_v2 enrichment extractors on a single article.

    Adds: symbols, sectors, indices, events, is_market_wide, intents, content_label,
    sentiment_score, sentiment_label.
    """
    full_text = f"{article.get('title', '')} {article.get('summary', '')}".strip()
    if not full_text:
        return article

    try:
        from tradingagents.dataflows.social_v2.entities import (
            extract as extract_entities,
            has_market_term,
        )
        mentions = extract_entities(full_text)
        article["symbols"] = [m.symbol for m in mentions]
        article["is_market_wide"] = has_market_term(full_text) or bool(
            any(s.startswith("EGX_") for s in article["symbols"])
        )
    except Exception as exc:
        log.debug("Entity extraction failed: %s", exc)
        article.setdefault("symbols", [])
        article.setdefault("is_market_wide", False)

    try:
        from tradingagents.dataflows.social_v2.sectors import extract_sector_mentions
        sec_mentions = extract_sector_mentions(full_text)
        sector_names = set()
        for sm in sec_mentions:
            name = getattr(sm, "sector", None) or (sm if isinstance(sm, str) else None)
            if name:
                sector_names.add(str(name).lower())

        from tradingagents.sentiment.taxonomy import (
            SectorEnum,
            ticker_to_sector,
        )
        for sym in article.get("symbols", []):
            try:
                sec = ticker_to_sector(sym)
                if sec is not None and sec != SectorEnum.UNKNOWN:
                    sector_names.add(str(sec.value).lower())
            except Exception:
                pass
        article["sectors"] = sorted(sector_names)
    except Exception as exc:
        log.debug("Sector extraction failed: %s", exc)
        article.setdefault("sectors", [])

    try:
        from tradingagents.sentiment.taxonomy import ticker_to_indices
        idx_set = set()
        for sym in article.get("symbols", []):
            try:
                for idx in ticker_to_indices(sym):
                    idx_set.add(idx.value)
            except Exception:
                pass
        article["indices"] = sorted(idx_set)
    except Exception as exc:
        log.debug("Index extraction failed: %s", exc)
        article.setdefault("indices", [])

    try:
        from tradingagents.dataflows.social_v2.events import extract_events
        ev_mentions = extract_events(full_text)
        article["events"] = [
            str(e.event if hasattr(e, "event") else e) for e in ev_mentions
        ]
    except Exception as exc:
        log.debug("Event extraction failed: %s", exc)
        article.setdefault("events", [])

    try:
        from tradingagents.dataflows.social_v2.intent import detect as detect_intent
        intent_result = detect_intent(full_text)
        article["intents"] = list(intent_result.to_dict().get("intents", []))
    except Exception:
        article.setdefault("intents", [])

    try:
        from tradingagents.dataflows.social_v2.content_type import classify as classify_content
        content_result = classify_content(full_text)
        article["content_label"] = content_result.to_dict().get("label", "")
    except Exception:
        article.setdefault("content_label", "")

    try:
        from tradingagents.dataflows.social_v2.sentiment_runner import analyze_egx_batch
        results = analyze_egx_batch([full_text])
        if results:
            article["sentiment_score"] = results[0].get("score")
            article["sentiment_label"] = results[0].get("label")
    except Exception:
        pass

    return article


def _relevance_filter(articles: List[dict]) -> List[dict]:
    """Keep only articles that mention at least one EGX entity, sector, or market term."""
    kept = []
    for art in articles:
        if art.get("symbols"):
            kept.append(art)
        elif art.get("sectors"):
            kept.append(art)
        elif art.get("events"):
            kept.append(art)
        elif art.get("is_market_wide"):
            kept.append(art)
    return kept


def _deduplicate(articles: List[dict]) -> List[dict]:
    """Deduplicate by title similarity (same logic as news aggregator)."""
    from difflib import SequenceMatcher

    unique: List[dict] = []
    unique_titles: List[str] = []

    for art in articles:
        title = art.get("title", "").strip().lower()[:120]
        if not title:
            continue
        is_dup = any(
            SequenceMatcher(None, title, t).ratio() >= 0.82
            for t in unique_titles
        )
        if not is_dup:
            unique.append(art)
            unique_titles.append(title)

    removed = len(articles) - len(unique)
    if removed:
        log.info("Deduplication removed %d duplicate articles", removed)
    return unique


def _scrape_social_v2_news_sources() -> List[dict]:
    """Reuse the social_v2 news sources (Mubasher RSS, Google News AR, etc.)."""
    articles: List[dict] = []

    try:
        from tradingagents.dataflows.social_v2.sources import mubasher_news
        posts = mubasher_news.scrape(max_per_feed=80)
        for p in posts:
            articles.append({
                "title": p.text[:200] if p.text else "",
                "summary": p.text[200:700] if p.text and len(p.text) > 200 else "",
                "source": "mubasher_rss",
                "published_at": p.timestamp,
                "url": p.url,
                "language": "ar",
            })
        log.info("Mubasher RSS: %d articles", len(posts))
    except Exception as exc:
        log.warning("Mubasher RSS failed: %s", exc)

    try:
        from tradingagents.dataflows.social_v2.sources import google_news_ar
        posts = google_news_ar.scrape()
        for p in posts:
            articles.append({
                "title": p.text[:200] if p.text else "",
                "summary": p.text[200:700] if p.text and len(p.text) > 200 else "",
                "source": "google_news_ar",
                "published_at": p.timestamp,
                "url": p.url,
                "language": "ar",
            })
        log.info("Google News AR: %d articles", len(posts))
    except Exception as exc:
        log.warning("Google News AR failed: %s", exc)

    try:
        from tradingagents.dataflows.social_v2.sources import bing_news_ar
        posts = bing_news_ar.scrape()
        for p in posts:
            articles.append({
                "title": p.text[:200] if p.text else "",
                "summary": p.text[200:700] if p.text and len(p.text) > 200 else "",
                "source": "bing_news_ar",
                "published_at": p.timestamp,
                "url": p.url,
                "language": "ar",
            })
        log.info("Bing News AR: %d articles", len(posts))
    except Exception as exc:
        log.warning("Bing News AR failed: %s", exc)

    try:
        from tradingagents.dataflows.social_v2.sources import egypt_news_rss
        posts = egypt_news_rss.scrape()
        for p in posts:
            articles.append({
                "title": p.text[:200] if p.text else "",
                "summary": p.text[200:700] if p.text and len(p.text) > 200 else "",
                "source": "egypt_rss",
                "published_at": p.timestamp,
                "url": p.url,
                "language": "ar",
            })
        log.info("Egypt News RSS: %d articles", len(posts))
    except Exception as exc:
        log.warning("Egypt News RSS failed: %s", exc)

    return articles


def _print_categorization_summary(articles: List[dict]) -> None:
    """Print a categorization summary to the log."""
    sector_counts: Dict[str, int] = {}
    index_counts: Dict[str, int] = {}
    symbol_counts: Dict[str, int] = {}
    market_wide = 0
    event_counts: Dict[str, int] = {}

    for art in articles:
        for s in art.get("sectors", []):
            sector_counts[s] = sector_counts.get(s, 0) + 1
        for i in art.get("indices", []):
            index_counts[i] = index_counts.get(i, 0) + 1
        for sym in art.get("symbols", []):
            if not sym.startswith("EGX_"):
                symbol_counts[sym] = symbol_counts.get(sym, 0) + 1
        if art.get("is_market_wide"):
            market_wide += 1
        for ev in art.get("events", []):
            event_counts[ev] = event_counts.get(ev, 0) + 1

    log.info("=== CATEGORIZATION SUMMARY ===")
    log.info("Total articles: %d", len(articles))
    log.info("Market-wide articles: %d", market_wide)

    if sector_counts:
        top_sectors = sorted(sector_counts.items(), key=lambda x: -x[1])[:10]
        log.info("Sectors: %s", ", ".join(f"{s}:{c}" for s, c in top_sectors))

    if index_counts:
        log.info("Indices: %s", ", ".join(f"{i}:{c}" for i, c in sorted(index_counts.items())))

    if symbol_counts:
        top_symbols = sorted(symbol_counts.items(), key=lambda x: -x[1])[:15]
        log.info("Stocks: %s", ", ".join(f"{s}:{c}" for s, c in top_symbols))

    if event_counts:
        top_events = sorted(event_counts.items(), key=lambda x: -x[1])[:10]
        log.info("Events: %s", ", ".join(f"{e}:{c}" for e, c in top_events))

    uncategorized = sum(
        1 for a in articles
        if not a.get("symbols") and not a.get("sectors") and not a.get("is_market_wide")
    )
    log.info("Uncategorized (no symbol/sector/market match): %d", uncategorized)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Weekly EGX news scraper + categorizer → news_archive."
    )
    ap.add_argument(
        "--max-per-source", type=int, default=50,
        help="Max articles per Scrapling source (default 50).",
    )
    ap.add_argument(
        "--stats-only", action="store_true",
        help="Skip scraping; just print archive health.",
    )
    ap.add_argument(
        "--skip-sentiment", action="store_true",
        help="Skip sentiment analysis (faster, just categorize).",
    )
    args = ap.parse_args()

    if args.stats_only:
        from tradingagents.dataflows.news_providers.news_store import stats
        s = stats()
        if "error" in s:
            log.warning("Stats error: %s", s["error"])
        else:
            log.info(
                "News archive: %d total, %d in last 8 days, %d market-wide",
                s["total"], s["recent_8d"], s["market_wide_8d"],
            )
            if s.get("top_symbols"):
                log.info("Top symbols: %s", s["top_symbols"])
            if s.get("top_sectors"):
                log.info("Top sectors: %s", s["top_sectors"])
        return

    if not os.getenv("POSTGRES_URL"):
        log.warning(
            "POSTGRES_URL is not set — articles will be scraped and categorized "
            "but NOT archived. Set POSTGRES_URL to persist."
        )

    log.info("=== PHASE 1: SCRAPING ===")

    all_articles: List[dict] = []

    # Scrapling-based sources (Enterprise, Youm7, Al-Mal, Amwal, NewsAPI)
    try:
        from tradingagents.dataflows.news_providers.scrapling_sources import scrape_all
        scrapling_articles = scrape_all(max_per_source=args.max_per_source)
        all_articles.extend(scrapling_articles)
        log.info("Scrapling sources: %d articles", len(scrapling_articles))
    except Exception as exc:
        log.exception("Scrapling sources failed: %s", exc)

    # Social_v2 news sources (Mubasher, Google News AR, Bing, Egypt RSS)
    try:
        social_news = _scrape_social_v2_news_sources()
        all_articles.extend(social_news)
        log.info("Social_v2 news sources: %d articles", len(social_news))
    except Exception as exc:
        log.exception("Social_v2 news sources failed: %s", exc)

    log.info("Total raw articles: %d", len(all_articles))

    # Deduplicate
    all_articles = _deduplicate(all_articles)
    log.info("After deduplication: %d articles", len(all_articles))

    log.info("=== PHASE 2: ENRICHMENT & CATEGORIZATION ===")

    enriched: List[dict] = []
    for i, art in enumerate(all_articles):
        if args.skip_sentiment:
            full_text = f"{art.get('title', '')} {art.get('summary', '')}".strip()
            try:
                from tradingagents.dataflows.social_v2.entities import (
                    extract as extract_entities, has_market_term,
                )
                mentions = extract_entities(full_text)
                art["symbols"] = [m.symbol for m in mentions]
                art["is_market_wide"] = has_market_term(full_text) or bool(
                    any(s.startswith("EGX_") for s in art["symbols"])
                )
            except Exception:
                art.setdefault("symbols", [])
                art.setdefault("is_market_wide", False)
            try:
                from tradingagents.dataflows.social_v2.sectors import extract_sector_mentions
                from tradingagents.sentiment.taxonomy import SectorEnum, ticker_to_sector, ticker_to_indices
                sec_mentions = extract_sector_mentions(full_text)
                sector_names = set()
                for sm in sec_mentions:
                    name = getattr(sm, "sector", None) or (sm if isinstance(sm, str) else None)
                    if name:
                        sector_names.add(str(name).lower())
                for sym in art.get("symbols", []):
                    try:
                        sec = ticker_to_sector(sym)
                        if sec and sec != SectorEnum.UNKNOWN:
                            sector_names.add(str(sec.value).lower())
                    except Exception:
                        pass
                art["sectors"] = sorted(sector_names)
                idx_set = set()
                for sym in art.get("symbols", []):
                    try:
                        for idx in ticker_to_indices(sym):
                            idx_set.add(idx.value)
                    except Exception:
                        pass
                art["indices"] = sorted(idx_set)
            except Exception:
                art.setdefault("sectors", [])
                art.setdefault("indices", [])
            try:
                from tradingagents.dataflows.social_v2.events import extract_events
                ev = extract_events(full_text)
                art["events"] = [str(e.event if hasattr(e, "event") else e) for e in ev]
            except Exception:
                art.setdefault("events", [])
        else:
            art = _enrich_article(art)
        enriched.append(art)
        if (i + 1) % 50 == 0:
            log.info("Enriched %d / %d articles...", i + 1, len(all_articles))

    log.info("Enrichment complete: %d articles categorized", len(enriched))

    # Filter: keep only EGX-relevant articles
    relevant = _relevance_filter(enriched)
    log.info("EGX-relevant: %d / %d articles", len(relevant), len(enriched))

    _print_categorization_summary(relevant)

    log.info("=== PHASE 3: ARCHIVE ===")

    try:
        from tradingagents.dataflows.news_providers.news_store import archive, stats
        n = archive(relevant)
        log.info("Archived: %d rows offered to news_archive", n)

        s = stats()
        if "error" not in s:
            log.info(
                "Archive health: %d total, %d recent, %d market-wide",
                s["total"], s["recent_8d"], s["market_wide_8d"],
            )
    except Exception as exc:
        log.exception("Archive failed: %s", exc)

    log.info("=== DONE ===")


if __name__ == "__main__":
    main()
