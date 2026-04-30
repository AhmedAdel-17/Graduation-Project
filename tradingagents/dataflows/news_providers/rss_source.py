"""
Arabic RSS Feed Provider
=========================
Fetches financial news from Arabic and English RSS feeds 
covering the Egyptian market. Uses feedparser — no API key needed.

Features:
- Parses multiple Arabic financial news feeds
- Filters articles by ticker mention
- No API key required (free, unlimited)
- Bilingual: Arabic + English sources

Usage:
    articles = fetch_rss_articles("COMI", days=7)
"""

import logging
import re
from typing import List, Optional
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("tradingagents.news.rss")

# Try to import feedparser
try:
    import feedparser
    FEEDPARSER_AVAILABLE = True
except ImportError:
    FEEDPARSER_AVAILABLE = False
    logger.info("feedparser not installed. RSS provider disabled. Install: pip install feedparser")


# =============================================================================
# RSS Feed Registry
# =============================================================================
# Each feed has: url, language, and focus area.
# These are verified public financial news feeds with RSS support.
# =============================================================================

RSS_FEEDS = {
    "google_egx_ar": {
        "url": "https://news.google.com/rss/search?q=%D8%A7%D9%84%D8%A8%D9%88%D8%B1%D8%B5%D8%A9+%D8%A7%D9%84%D9%85%D8%B5%D8%B1%D9%8A%D8%A9&hl=ar&gl=EG&ceid=EG:ar",
        "language": "ar",
        "focus": "Google News: 'البورصة المصرية' (Egyptian Exchange) in Arabic",
    },
    "google_egx_en": {
        "url": "https://news.google.com/rss/search?q=EGX+Egypt+stock+market&hl=en&gl=EG&ceid=EG:en",
        "language": "en",
        "focus": "Google News: EGX Egypt stock market in English",
    },
    "google_egypt_economy": {
        "url": "https://news.google.com/rss/search?q=%D8%A7%D9%82%D8%AA%D8%B5%D8%A7%D8%AF+%D9%85%D8%B5%D8%B1+%D8%A8%D9%88%D8%B1%D8%B5%D8%A9&hl=ar&gl=EG&ceid=EG:ar",
        "language": "ar",
        "focus": "Google News: 'اقتصاد مصر بورصة' (Egypt economy stock market)",
    },
}

# =============================================================================
# Ticker → keyword mappings for article filtering
# =============================================================================
# Maps EGX tickers to Arabic and English keywords used to detect
# if an article mentions the company. Includes common abbreviations.
# =============================================================================

TICKER_KEYWORDS = {
    "COMI": ["COMI", "CIB", "البنك التجاري الدولي", "التجاري الدولي"],
    "HRHO": ["HRHO", "Hermes", "هيرميس"],
    "EAST": ["EAST", "Eastern Company", "الشرقية", "ايسترن"],
    "EFIH": ["EFIH", "EFG Hermes", "إي اف جي", "هيرميس"],
    "SWDY": ["SWDY", "Elsewedy", "السويدي"],
    "TMGH": ["TMGH", "Talaat Moustafa", "طلعت مصطفى"],
    "ORWE": ["ORWE", "Oriental Weavers", "السجاد الشرقية"],
    "PHDC": ["PHDC", "Palm Hills", "بالم هيلز"],
    "MNHD": ["MNHD", "Madinet Nasr", "مدينة نصر"],
    "ABUK": ["ABUK", "Abu Qir", "أبو قير"],
    "ETEL": ["ETEL", "Telecom Egypt", "المصرية للاتصالات", "تليكوم"],
    "EKHO": ["EKHO", "Egyptian Kuwaiti", "المصرية الكويتية"],
    "CLHO": ["CLHO", "Cleopatra Hospital", "كليوباترا"],
}

# General market keywords — used when no specific ticker is provided
MARKET_KEYWORDS = [
    "EGX", "البورصة المصرية", "بورصة", "Egyptian Exchange",
    "stock market", "سوق المال", "هيئة الرقابة المالية",
    "FRA", "Egyptian economy", "الاقتصاد المصري",
]


def _article_mentions_ticker(text: str, ticker: str) -> bool:
    """
    Check if article text mentions the given ticker or company.
    
    Case-insensitive for English, exact match for Arabic.
    """
    ticker_clean = ticker.upper().replace(".CA", "").strip()
    keywords = TICKER_KEYWORDS.get(ticker_clean, [ticker_clean])
    
    text_lower = text.lower()
    
    for kw in keywords:
        # Arabic keywords: exact substring match (Arabic is case-insensitive)
        if any(ord(c) > 127 for c in kw):
            if kw in text:
                return True
        else:
            # English keywords: case-insensitive word boundary match
            if re.search(r'\b' + re.escape(kw.lower()) + r'\b', text_lower):
                return True
    
    return False


def _article_mentions_market(text: str) -> bool:
    """Check if article text mentions EGX market in general."""
    text_lower = text.lower()
    for kw in MARKET_KEYWORDS:
        if any(ord(c) > 127 for c in kw):
            if kw in text:
                return True
        else:
            if kw.lower() in text_lower:
                return True
    return False


def _parse_feed_date(entry) -> Optional[str]:
    """
    Extract and normalize publication date from an RSS entry.
    
    feedparser provides parsed date as a time.struct_time in 'published_parsed'.
    """
    import time as _time
    
    parsed = entry.get("published_parsed")
    if parsed:
        try:
            dt = datetime(*parsed[:6])
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            pass
    
    # Fallback to raw string
    return entry.get("published", entry.get("updated", ""))


def _is_within_days(date_str: str, days: int) -> bool:
    """Check if a date string is within the last N days."""
    if not date_str:
        return True  # Include articles with no date (better to have noise than miss data)
    
    try:
        # Try ISO format first
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00").split("+")[0])
        cutoff = datetime.now() - timedelta(days=days)
        return dt >= cutoff
    except (ValueError, TypeError):
        return True  # Include on parse failure


def fetch_rss_articles(
    ticker: str,
    days: int = 7,
    max_per_feed: int = 20,
    include_market_news: bool = True,
) -> List[dict]:
    """
    Fetch articles from Arabic/English RSS feeds that mention the ticker.
    
    Args:
        ticker: EGX ticker (e.g., "COMI" or "COMI.CA")
        days: Number of days to look back
        max_per_feed: Max articles to scan per feed
        include_market_news: Also include general EGX market articles
        
    Returns:
        List of article dicts with standard fields
        
    Raises:
        RuntimeError: If feedparser not installed
    """
    if not FEEDPARSER_AVAILABLE:
        raise RuntimeError("feedparser not installed. Install: pip install feedparser")
    
    ticker_clean = ticker.upper().replace(".CA", "").strip()
    articles = []
    feeds_succeeded = []
    feeds_failed = []
    
    for feed_name, feed_info in RSS_FEEDS.items():
        try:
            logger.info("Parsing RSS feed: %s (%s)", feed_name, feed_info["url"])
            
            # Use requests with a proper User-Agent to avoid bot-blocking
            # Many Egyptian news sites block feedparser's default urllib
            try:
                import requests as _req
                resp = _req.get(
                    feed_info["url"],
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept": "application/rss+xml, application/xml, text/xml, */*",
                    },
                    timeout=10,
                    allow_redirects=True,
                )
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
            except Exception as fetch_err:
                logger.warning("RSS fetch '%s' with requests failed: %s, trying direct", feed_name, fetch_err)
                feed = feedparser.parse(feed_info["url"])
            
            if feed.bozo and not feed.entries:
                # Only skip if truly empty. feedparser can report bozo=True
                # for malformed-but-parseable XML — we still want those entries.
                logger.warning("RSS feed '%s' empty (bozo: %s)", feed_name, feed.bozo_exception)
                feeds_failed.append(feed_name)
                continue
            
            if feed.bozo and feed.entries:
                logger.info("RSS '%s' has bozo errors but %d entries — proceeding",
                           feed_name, len(feed.entries))
            
            feed_articles = 0
            for entry in feed.entries[:max_per_feed]:
                # Build searchable text from title + summary
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                searchable = f"{title} {summary}"
                
                # Check if article mentions our ticker or general market
                is_relevant = _article_mentions_ticker(searchable, ticker_clean)
                if not is_relevant and include_market_news:
                    is_relevant = _article_mentions_market(searchable)
                
                if not is_relevant:
                    continue
                
                # Check date recency
                pub_date = _parse_feed_date(entry)
                if not _is_within_days(pub_date, days):
                    continue
                
                # Clean summary (remove HTML tags)
                clean_summary = re.sub(r'<[^>]+>', '', summary)[:500]
                
                articles.append({
                    "title": title,
                    "summary": clean_summary,
                    "source": feed_name,
                    "published_at": pub_date,
                    "url": entry.get("link", ""),
                    "language": feed_info["language"],
                })
                feed_articles += 1
            
            feeds_succeeded.append(feed_name)
            logger.info("RSS '%s': %d relevant articles from %d scanned", 
                       feed_name, feed_articles, min(len(feed.entries), max_per_feed))
            
        except Exception as e:
            logger.warning("RSS feed '%s' failed: %s", feed_name, e)
            feeds_failed.append(feed_name)
    
    logger.info(
        "RSS total: %d articles from %d feeds (failed: %d)",
        len(articles), len(feeds_succeeded), len(feeds_failed)
    )
    
    return articles


def fetch_rss_global(days: int = 7, max_per_feed: int = 10) -> List[dict]:
    """
    Fetch general EGX market news from RSS feeds (not ticker-specific).
    
    Good for global market context — captures macro news, regulatory changes, etc.
    """
    if not FEEDPARSER_AVAILABLE:
        raise RuntimeError("feedparser not installed")
    
    articles = []
    
    for feed_name, feed_info in RSS_FEEDS.items():
        try:
            try:
                import requests as _req
                resp = _req.get(
                    feed_info["url"],
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept": "application/rss+xml, application/xml, text/xml, */*",
                    },
                    timeout=10,
                    allow_redirects=True,
                )
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
            except Exception:
                feed = feedparser.parse(feed_info["url"])
            
            for entry in feed.entries[:max_per_feed]:
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                searchable = f"{title} {summary}"
                
                if not _article_mentions_market(searchable):
                    continue
                
                pub_date = _parse_feed_date(entry)
                if not _is_within_days(pub_date, days):
                    continue
                
                clean_summary = re.sub(r'<[^>]+>', '', summary)[:500]
                
                articles.append({
                    "title": title,
                    "summary": clean_summary,
                    "source": feed_name,
                    "published_at": pub_date,
                    "url": entry.get("link", ""),
                    "language": feed_info["language"],
                })
                
        except Exception as e:
            logger.warning("RSS global feed '%s' failed: %s", feed_name, e)
    
    return articles

