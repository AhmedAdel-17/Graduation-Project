"""
News Aggregator
================
Combines news from all providers, deduplicates by headline,
and returns a unified NewsResponse.

Fallback chain:
    1. NewsAPI.org (if API key available)
    2. Arabic RSS feeds (always available, no key needed)
    3. Google News (existing legacy provider)
    4. Local CSV/text files (last resort)

Usage:
    from tradingagents.dataflows.news_providers.aggregator import fetch_aggregated_news
    result = fetch_aggregated_news("COMI", days=7)
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from ..schemas import NewsArticle, NewsResponse

logger = logging.getLogger("tradingagents.news.aggregator")


def _deduplicate_articles(articles: List[dict]) -> List[dict]:
    """
    Remove duplicate articles based on headline similarity.
    
    Uses first 50 chars of lowercase title as fingerprint.
    This is simple but effective for financial news where
    exact headlines are common across wire services.
    """
    seen = set()
    unique = []
    
    for article in articles:
        title = article.get("title", "")
        # Fingerprint: first 50 chars, lowercased, stripped
        fingerprint = title.lower().strip()[:50]
        
        if not fingerprint or fingerprint in seen:
            continue
        
        seen.add(fingerprint)
        unique.append(article)
    
    return unique


def _sort_by_date(articles: List[dict]) -> List[dict]:
    """Sort articles by published_at descending (newest first)."""
    def _parse_date(article):
        date_str = article.get("published_at", "")
        if not date_str:
            return datetime.min
        try:
            # ISO format
            return datetime.fromisoformat(date_str.replace("Z", "+00:00").split("+")[0])
        except (ValueError, TypeError):
            return datetime.min
    
    return sorted(articles, key=_parse_date, reverse=True)


def fetch_aggregated_news(
    ticker: str,
    days: int = 7,
    max_articles: int = 15,
    include_global: bool = True,
) -> Dict[str, Any]:
    """
    Fetch news from all available sources, deduplicate, and validate.
    
    This is the main entry point for news data in the system.
    
    Args:
        ticker: EGX ticker (e.g., "COMI" or "COMI.CA")
        days: Number of days to look back
        max_articles: Maximum total articles to return
        include_global: Include general market news too
        
    Returns:
        Dict matching NewsResponse schema with all articles combined
    """
    all_articles = []
    sources_queried = []
    sources_failed = []
    
    # --- Source 1: NewsAPI.org (requires API key) ---
    try:
        from .newsapi_source import fetch_newsapi_articles
        articles = fetch_newsapi_articles(ticker, days=days, max_articles=10)
        all_articles.extend(articles)
        sources_queried.append("newsapi")
        logger.info("NewsAPI: %d articles", len(articles))
    except RuntimeError as e:
        logger.info("NewsAPI skipped: %s", e)
        sources_failed.append(f"newsapi: {e}")
    except Exception as e:
        logger.warning("NewsAPI error: %s", e)
        sources_failed.append(f"newsapi: {e}")

    # --- Source 2: Arabic RSS feeds (no API key needed) ---
    try:
        from .rss_source import fetch_rss_articles
        articles = fetch_rss_articles(ticker, days=days, include_market_news=include_global)
        all_articles.extend(articles)
        sources_queried.append("rss_feeds")
        logger.info("RSS feeds: %d articles", len(articles))
    except RuntimeError as e:
        logger.info("RSS skipped: %s", e)
        sources_failed.append(f"rss: {e}")
    except Exception as e:
        logger.warning("RSS error: %s", e)
        sources_failed.append(f"rss: {e}")

    # --- Source 3: Google News (existing legacy, free) ---
    try:
        from ..google import get_google_news
        ticker_clean = ticker.upper().replace(".CA", "").strip()
        # get_google_news returns a formatted string, not structured data
        from datetime import timedelta
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")
        
        news_str = get_google_news(f"{ticker_clean} EGX Egypt", start_date, end_date)
        if news_str and len(news_str) > 20:
            # Parse the markdown-ish format back into articles
            # Format: ### Title (source: Source)\n\nSnippet\n\n
            import re
            blocks = re.split(r'###\s+', news_str)
            for block in blocks:
                block = block.strip()
                if not block:
                    continue
                # Extract title and source
                title_match = re.match(r'(.+?)\s*\(source:\s*(.+?)\)', block)
                if title_match:
                    title = title_match.group(1).strip()
                    source = title_match.group(2).strip()
                    # Rest is the summary
                    summary = block[title_match.end():].strip()[:500]
                    all_articles.append({
                        "title": title,
                        "summary": summary,
                        "source": source,
                        "published_at": "",
                        "url": "",
                        "language": "en",
                    })
            sources_queried.append("google_news")
            logger.info("Google News: parsed articles from response")
    except Exception as e:
        logger.info("Google News skipped: %s", e)
        sources_failed.append(f"google_news: {e}")

    # --- Source 4: Local CSV/text files (last resort) ---
    try:
        from ..local import get_egx_news_combined
        ticker_clean = ticker.upper().replace(".CA", "").strip()
        curr_date = datetime.now().strftime("%Y-%m-%d")
        local_result = get_egx_news_combined(ticker_clean, curr_date, days)
        
        local_articles = local_result.get("articles", [])
        for la in local_articles:
            # Normalize local format to our standard
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
    except Exception as e:
        logger.debug("Local CSV skipped: %s", e)
        sources_failed.append(f"local: {e}")

    # --- Post-processing ---
    # Deduplicate
    unique_articles = _deduplicate_articles(all_articles)
    
    # Sort by date
    sorted_articles = _sort_by_date(unique_articles)
    
    # Limit total
    final_articles = sorted_articles[:max_articles]
    
    # Validate with Pydantic
    validated_articles = []
    for art in final_articles:
        try:
            validated = NewsArticle(**art)
            validated_articles.append(validated.model_dump())
        except Exception as e:
            logger.debug("Article validation skipped: %s", e)
            # Include raw article anyway — partial data beats no data
            validated_articles.append(art)
    
    # Build response
    ticker_clean = ticker.upper().replace(".CA", "").strip()
    response = NewsResponse(
        query=ticker_clean,
        total_articles=len(validated_articles),
        articles=[NewsArticle(**a) if isinstance(a, dict) else a for a in validated_articles],
        sources_queried=sources_queried,
        sources_failed=sources_failed,
    )
    
    logger.info(
        "News aggregation complete: %d articles from %d sources (%d failed)",
        response.total_articles, len(sources_queried), len(sources_failed)
    )
    
    return response.model_dump()
