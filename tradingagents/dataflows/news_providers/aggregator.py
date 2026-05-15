"""
News Aggregator
================
Combines news from all providers, deduplicates by headline similarity,
and returns a unified NewsResponse.

Source priority chain:
    1. EGX Official Disclosures  (highest signal — FRA-regulated filings)
    2. NewsAPI.org               (requires API key, bilingual EN+AR)
    3. Arabic/English RSS feeds  (10+ feeds, always available, no key)
    4. Google News legacy        (free fallback, timestamps fixed)
    5. Local CSV/text files      (last resort — static, may be stale)

Deduplication:
    Uses fuzzy similarity (difflib SequenceMatcher) rather than the naive
    50-char prefix — catches re-published headlines with minor wording changes.

Usage:
    from tradingagents.dataflows.news_providers.aggregator import fetch_aggregated_news
    result = fetch_aggregated_news("COMI", days=7)
"""

import logging
import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import List, Dict, Any

from ..schemas import NewsArticle, NewsResponse

logger = logging.getLogger("tradingagents.news.aggregator")

# Similarity threshold: headlines with ratio >= this are considered duplicates.
# 0.82 catches "X reports earnings" vs "X reports Q3 earnings" but not unrelated stories.
DEDUP_SIMILARITY_THRESHOLD = 0.82


# =============================================================================
# Deduplication — Gap 5 fix
# =============================================================================

def _similarity(a: str, b: str) -> float:
    """Return SequenceMatcher ratio for two strings (0.0 – 1.0)."""
    a_clean = a.lower().strip()[:120]
    b_clean = b.lower().strip()[:120]
    return SequenceMatcher(None, a_clean, b_clean).ratio()


def _deduplicate_articles(articles: List[dict]) -> List[dict]:
    """
    Remove duplicate articles using fuzzy headline similarity.

    Improvement over the old 50-char prefix: catches headlines that are
    re-worded versions of the same story (common in Egyptian wire services).
    """
    unique: List[dict] = []
    unique_titles: List[str] = []

    for article in articles:
        title = article.get("title", "").strip()
        if not title:
            continue

        # Check against all accepted titles so far
        is_dup = any(
            _similarity(title, t) >= DEDUP_SIMILARITY_THRESHOLD
            for t in unique_titles
        )
        if not is_dup:
            unique.append(article)
            unique_titles.append(title)

    removed = len(articles) - len(unique)
    if removed:
        logger.info("Deduplication removed %d duplicate articles", removed)
    return unique


# =============================================================================
# Date sorting
# =============================================================================

def _parse_iso_date(date_str: str) -> datetime:
    """Parse an ISO-ish date string to datetime; return datetime.min on failure."""
    if not date_str:
        return datetime.min
    try:
        clean = date_str.replace("Z", "+00:00").split("+")[0].strip()
        return datetime.fromisoformat(clean)
    except (ValueError, TypeError):
        return datetime.min


def _sort_by_date(articles: List[dict]) -> List[dict]:
    """Sort articles newest-first, articles with no date go last."""
    return sorted(articles, key=lambda a: _parse_iso_date(a.get("published_at", "")), reverse=True)


# =============================================================================
# Google News legacy parser — Gap 6 fix
# =============================================================================

def _parse_google_news_legacy(news_str: str, cutoff_date: str) -> List[dict]:
    """
    Parse the markdown-ish string returned by the legacy Google News scraper
    into structured article dicts.

    Gap 6 fix: we now embed today's date as a synthetic `published_at` so
    articles are not indefinitely included on date-parse failure, and we
    skip articles whose inferred date is clearly before `cutoff_date`.
    """
    if not news_str or len(news_str) < 20:
        return []

    # Synthetic fallback date: articles from this source have no embedded timestamp,
    # so we use 'today' as a conservative approximation.
    today_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    articles = []
    blocks = re.split(r'###\s+', news_str)
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        title_match = re.match(r'(.+?)\s*\(source:\s*(.+?)\)', block)
        if title_match:
            title = title_match.group(1).strip()
            source = title_match.group(2).strip()
            summary = block[title_match.end():].strip()[:500]
        else:
            # No source annotation — use full block as title
            lines = block.split('\n')
            title = lines[0].strip()[:200]
            source = "google_news_legacy"
            summary = ' '.join(lines[1:]).strip()[:500]

        if len(title) < 10:
            continue

        # Try to extract an embedded date from title or summary text
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', f"{title} {summary}")
        pub_date = date_match.group(1) + "T00:00:00" if date_match else today_iso

        articles.append({
            "title": title,
            "summary": summary,
            "source": source,
            "published_at": pub_date,
            "url": "",
            "language": "en",
        })

    return articles


# =============================================================================
# Main aggregation entry point
# =============================================================================

def fetch_aggregated_news(
    ticker: str,
    days: int = 7,
    max_articles: int = 20,
    include_global: bool = True,
) -> Dict[str, Any]:
    """
    Fetch news from all available sources, deduplicate, and validate.

    Args:
        ticker: EGX ticker (e.g., "COMI" or "COMI.CA")
        days: Number of days to look back
        max_articles: Maximum total articles to return
        include_global: Include general EGX market news too

    Returns:
        Dict matching NewsResponse schema
    """
    all_articles: List[dict] = []
    sources_queried: List[str] = []
    sources_failed: List[str] = []
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # ── Source 1: EGX Official Disclosures (Gap 3) ───────────────────────────
    try:
        from .egx_disclosure_source import fetch_egx_disclosures
        # Use longer lookback for disclosures — they are infrequent
        disclosure_days = max(days, 30)
        disc_articles = fetch_egx_disclosures(ticker, days=disclosure_days)
        all_articles.extend(disc_articles)
        sources_queried.append("egx_disclosures")
        logger.info("EGX Disclosures: %d articles", len(disc_articles))
    except Exception as exc:
        logger.warning("EGX Disclosures failed: %s", exc)
        sources_failed.append(f"egx_disclosures: {exc}")

    # ── Source 2: NewsAPI.org — bilingual EN + AR (Gaps 1 & 4) ──────────────
    try:
        from .newsapi_source import fetch_newsapi_articles
        news_articles = fetch_newsapi_articles(ticker, days=days, max_articles=10)
        all_articles.extend(news_articles)
        sources_queried.append("newsapi")
        logger.info("NewsAPI: %d articles", len(news_articles))
    except RuntimeError as exc:
        logger.info("NewsAPI skipped: %s", exc)
        sources_failed.append(f"newsapi: {exc}")
    except Exception as exc:
        logger.warning("NewsAPI error: %s", exc)
        sources_failed.append(f"newsapi: {exc}")

    # ── Source 3: Arabic/English RSS feeds (Gap 2) ───────────────────────────
    try:
        from .rss_source import fetch_rss_articles
        rss_articles = fetch_rss_articles(
            ticker, days=days, include_market_news=include_global
        )
        all_articles.extend(rss_articles)
        sources_queried.append("rss_feeds")
        logger.info("RSS feeds: %d articles", len(rss_articles))
    except RuntimeError as exc:
        logger.info("RSS skipped: %s", exc)
        sources_failed.append(f"rss: {exc}")
    except Exception as exc:
        logger.warning("RSS error: %s", exc)
        sources_failed.append(f"rss: {exc}")

    # ── Source 4: Google News legacy (Gap 6 — timestamps fixed) ─────────────
    try:
        from ..google import get_google_news
        ticker_clean = ticker.upper().replace(".CA", "").strip()
        end_date = datetime.now().strftime("%Y-%m-%d")
        news_str = get_google_news(f"{ticker_clean} EGX Egypt", cutoff_date, end_date)
        if news_str and len(news_str) > 20:
            legacy_articles = _parse_google_news_legacy(news_str, cutoff_date)
            all_articles.extend(legacy_articles)
            sources_queried.append("google_news")
            logger.info("Google News legacy: %d articles parsed", len(legacy_articles))
    except Exception as exc:
        logger.info("Google News legacy skipped: %s", exc)
        sources_failed.append(f"google_news: {exc}")

    # ── Source 5: Local CSV/text files (last resort) ─────────────────────────
    try:
        from ..local import get_egx_news_combined
        ticker_clean = ticker.upper().replace(".CA", "").strip()
        curr_date = datetime.now().strftime("%Y-%m-%d")
        local_result = get_egx_news_combined(ticker_clean, curr_date, days)
        local_articles = local_result.get("articles", [])
        for la in local_articles:
            all_articles.append({
                "title": la.get("headline", la.get("title", "")),
                "summary": la.get("body", la.get("summary", ""))[:500],
                "source": la.get("source", "local_csv"),
                "published_at": la.get("date", la.get("published_at", "")),
                "url": "",
                "language": la.get("language", "unknown"),
            })
        if local_articles:
            sources_queried.append("local_csv")
            logger.info("Local CSV: %d articles", len(local_articles))
    except Exception as exc:
        logger.debug("Local CSV skipped: %s", exc)
        sources_failed.append(f"local: {exc}")

    # ── Post-processing ───────────────────────────────────────────────────────
    # 1. Fuzzy deduplicate (Gap 5)
    unique_articles = _deduplicate_articles(all_articles)

    # 2. Sort newest-first
    sorted_articles = _sort_by_date(unique_articles)

    # 3. Cap total
    final_articles = sorted_articles[:max_articles]

    # 4. Pydantic validation (include raw on failure — partial > nothing)
    validated: List[Any] = []
    for art in final_articles:
        try:
            validated.append(NewsArticle(**art))
        except Exception:
            validated.append(art)

    ticker_clean = ticker.upper().replace(".CA", "").strip()
    response = NewsResponse(
        query=ticker_clean,
        total_articles=len(validated),
        articles=[NewsArticle(**a) if isinstance(a, dict) else a for a in validated],
        sources_queried=sources_queried,
        sources_failed=sources_failed,
    )

    logger.info(
        "Aggregation complete for %s: %d articles from %d sources (%d failed)",
        ticker_clean, response.total_articles,
        len(sources_queried), len(sources_failed),
    )
    return response.model_dump()
