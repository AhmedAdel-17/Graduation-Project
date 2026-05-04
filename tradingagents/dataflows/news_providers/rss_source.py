"""
Arabic RSS Feed Provider
=========================
Fetches financial news from Arabic and English RSS feeds
covering the Egyptian market. Uses feedparser — no API key needed.

Features:
- 10+ feeds: Google News, Al Mal, Youm7, Masrawy, Sada El Balad, Mubasher, EGX
- Filters articles by ticker mention (Arabic + English keywords)
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

try:
    import feedparser
    FEEDPARSER_AVAILABLE = True
except ImportError:
    FEEDPARSER_AVAILABLE = False
    logger.info("feedparser not installed. RSS provider disabled. Install: pip install feedparser")


# =============================================================================
# RSS Feed Registry — verified public Egyptian financial news feeds
# =============================================================================

RSS_FEEDS = {
    # ── Google News (broad, bilingual) ───────────────────────────────────────
    "google_egx_ar": {
        "url": "https://news.google.com/rss/search?q=%D8%A7%D9%84%D8%A8%D9%88%D8%B1%D8%B5%D8%A9+%D8%A7%D9%84%D9%85%D8%B5%D8%B1%D9%8A%D8%A9&hl=ar&gl=EG&ceid=EG:ar",
        "language": "ar",
        "focus": "Google News: البورصة المصرية in Arabic",
        "priority": 1,
    },
    "google_egx_en": {
        "url": "https://news.google.com/rss/search?q=EGX+Egypt+stock+market&hl=en&gl=EG&ceid=EG:en",
        "language": "en",
        "focus": "Google News: EGX Egypt stock market in English",
        "priority": 1,
    },
    "google_egypt_economy": {
        "url": "https://news.google.com/rss/search?q=%D8%A7%D9%82%D8%AA%D8%B5%D8%A7%D8%AF+%D9%85%D8%B5%D8%B1+%D8%A8%D9%88%D8%B1%D8%B5%D8%A9&hl=ar&gl=EG&ceid=EG:ar",
        "language": "ar",
        "focus": "Google News: اقتصاد مصر بورصة",
        "priority": 1,
    },
    # ── Al Mal News — leading EGX-focused outlet ─────────────────────────────
    "almal_news": {
        "url": "https://almalnews.com/feed/",
        "language": "ar",
        "focus": "Al Mal News — premier Egyptian financial newspaper",
        "priority": 2,
    },
    # ── Youm7 Economy ─────────────────────────────────────────────────────────
    "youm7_economy": {
        "url": "https://www.youm7.com/Section/RSS/98",
        "language": "ar",
        "focus": "Youm7 Economy — high-traffic Egyptian news economy section",
        "priority": 2,
    },
    # ── Masrawy Economy ───────────────────────────────────────────────────────
    "masrawy_economy": {
        "url": "https://www.masrawy.com/news/economy/rss",
        "language": "ar",
        "focus": "Masrawy Economy — popular Egyptian portal economy section",
        "priority": 2,
    },
    # ── Sada El Balad Economy ────────────────────────────────────────────────
    "sada_economy": {
        "url": "https://www.elbalad.news/rss",
        "language": "ar",
        "focus": "Sada El Balad — general Egyptian news with economic coverage",
        "priority": 3,
    },
    # ── Mubasher (موبايل بارجر) ───────────────────────────────────────────────
    "mubasher_news": {
        "url": "https://www.mubasher.info/news/rss/countries/EG",
        "language": "ar",
        "focus": "Mubasher — real-time EGX financial news and disclosures",
        "priority": 2,
    },
    # ── Enterprise English — Egypt business in English ────────────────────────
    "enterprise_egypt": {
        "url": "https://enterprise.press/feed/",
        "language": "en",
        "focus": "Enterprise — English-language Egyptian business daily",
        "priority": 2,
    },
    # ── Daily News Egypt ─────────────────────────────────────────────────────
    "daily_news_egypt": {
        "url": "https://dailynewsegypt.com/category/business-economy/feed/",
        "language": "en",
        "focus": "Daily News Egypt — English business & economy",
        "priority": 3,
    },
}

# =============================================================================
# Ticker → keyword mappings — all 30 EGX tickers
# =============================================================================

TICKER_KEYWORDS = {
    # Banks
    "COMI":  ["COMI", "CIB", "البنك التجاري الدولي", "التجاري الدولي", "Commercial International Bank"],
    "ADIB":  ["ADIB", "أبوظبي الإسلامي", "Abu Dhabi Islamic Bank"],
    "CIEB":  ["CIEB", "CIB Egypt", "سي اي بي"],
    "EXPA":  ["EXPA", "تنمية الصادرات", "Export Development Bank"],
    "HDBK":  ["HDBK", "الإسكان والتعمير", "Housing Development Bank"],
    "QNBA":  ["QNBA", "قطر الوطني الأهلي", "QNB Al Ahli"],
    "SAUD":  ["SAUD", "السعودي المصري", "Saudi Egyptian"],
    # Real Estate
    "TMGH":  ["TMGH", "طلعت مصطفى", "Talaat Moustafa"],
    "HELI":  ["HELI", "هليوبوليس", "Heliopolis Housing"],
    "PHDC":  ["PHDC", "بالم هيلز", "Palm Hills"],
    "OCDI":  ["OCDI", "أوراسكوم للإنشاء", "Orascom Construction"],
    "ORAS":  ["ORAS", "أوراسكوم للتطوير", "Orascom Development"],
    "EMFD":  ["EMFD", "إعمار مصر", "Emaar Misr"],
    # Industry
    "EAST":  ["EAST", "الشرقية للدخان", "Eastern Company"],
    "ESRS":  ["ESRS", "عز للصلب", "Ezz Steel"],
    "SWDY":  ["SWDY", "السويدي", "Elsewedy Electric"],
    "ABUK":  ["ABUK", "أبو قير للأسمدة", "Abu Qir Fertilizers"],
    "MFPC":  ["MFPC", "موبكو", "Misr Fertilizers", "MOPCO"],
    "EGAL":  ["EGAL", "مصر للألومنيوم", "Egyptian Aluminum"],
    "EGCH":  ["EGCH", "كيما", "Egyptian Chemical"],
    "EFIC":  ["EFIC", "المالية والصناعية", "Egyptian Financial Industrial"],
    # Telecom & Tech
    "ETEL":  ["ETEL", "المصرية للاتصالات", "Telecom Egypt"],
    "FWRY":  ["FWRY", "فوري", "Fawry"],
    "EFIH":  ["EFIH", "EFG Hermes", "إي إف جي هيرميس"],
    "RAYA":  ["RAYA", "راية", "Raya Holding"],
    # Financial Services
    "HRHO":  ["HRHO", "هيرميس", "EFG Hermes", "Hermes Holding"],
    "BTFH":  ["BTFH", "بلتون", "Beltone Financial"],
    "CICH":  ["CIch", "CI Capital", "سي آي كابيتال"],
    # Food & Beverage
    "JUFO":  ["JUFO", "جهينة", "Juhayna"],
    "EFID":  ["EFID", "مصر للصناعات الغذائية", "Egyptian Food"],
    "DOMT":  ["DOMT", "دومتي", "Domty"],
}

MARKET_KEYWORDS = [
    "EGX", "البورصة المصرية", "بورصة", "Egyptian Exchange",
    "stock market", "سوق المال", "هيئة الرقابة المالية",
    "FRA", "Egyptian economy", "الاقتصاد المصري", "الأسهم المصرية",
    "مؤشر EGX30", "EGX30", "EGX70",
]


def _article_mentions_ticker(text: str, ticker: str) -> bool:
    """Case-insensitive English + exact Arabic substring match."""
    ticker_clean = ticker.upper().replace(".CA", "").strip()
    keywords = TICKER_KEYWORDS.get(ticker_clean, [ticker_clean])
    text_lower = text.lower()
    for kw in keywords:
        if any(ord(c) > 127 for c in kw):
            if kw in text:
                return True
        else:
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
    """Extract and normalize publication date from an RSS entry."""
    parsed = entry.get("published_parsed")
    if parsed:
        try:
            dt = datetime(*parsed[:6])
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            pass
    return entry.get("published", entry.get("updated", ""))


def _is_within_days(date_str: str, days: int) -> bool:
    """Check if a date string falls within the last N days."""
    if not date_str:
        return True  # include articles with no date rather than drop them
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00").split("+")[0])
        cutoff = datetime.now() - timedelta(days=days)
        return dt >= cutoff
    except (ValueError, TypeError):
        return True


def _fetch_single_feed(feed_name: str, feed_info: dict,
                       ticker_clean: str, days: int,
                       max_per_feed: int, include_market_news: bool) -> List[dict]:
    """
    Fetch and filter a single RSS feed. Returns list of article dicts.
    Isolated so failures in one feed don't affect others.
    """
    articles = []
    try:
        # Prefer requests + feedparser for better bot tolerance
        try:
            import requests as _req
            resp = _req.get(
                feed_info["url"],
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0 Safari/537.36"
                    ),
                    "Accept": "application/rss+xml, application/xml, text/xml, */*",
                    "Accept-Language": "ar,en;q=0.9",
                },
                timeout=12,
                allow_redirects=True,
            )
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as fetch_err:
            logger.debug("RSS '%s' requests failed (%s), trying direct", feed_name, fetch_err)
            feed = feedparser.parse(feed_info["url"])

        if feed.bozo and not feed.entries:
            logger.warning("RSS '%s' empty (bozo: %s)", feed_name, feed.bozo_exception)
            return []

        feed_count = 0
        for entry in feed.entries[:max_per_feed]:
            title = entry.get("title", "")
            summary = entry.get("summary", entry.get("description", ""))
            searchable = f"{title} {summary}"

            is_relevant = _article_mentions_ticker(searchable, ticker_clean)
            if not is_relevant and include_market_news:
                is_relevant = _article_mentions_market(searchable)
            if not is_relevant:
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
            feed_count += 1

        logger.info("RSS '%s': %d relevant articles", feed_name, feed_count)
    except Exception as exc:
        logger.warning("RSS feed '%s' failed: %s", feed_name, exc)

    return articles


def fetch_rss_articles(
    ticker: str,
    days: int = 7,
    max_per_feed: int = 20,
    include_market_news: bool = True,
) -> List[dict]:
    """
    Fetch articles from all Arabic/English RSS feeds that mention the ticker.

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
    all_articles = []

    # Sort feeds by priority so highest-quality sources are tried first
    sorted_feeds = sorted(RSS_FEEDS.items(), key=lambda x: x[1].get("priority", 9))

    for feed_name, feed_info in sorted_feeds:
        articles = _fetch_single_feed(
            feed_name, feed_info, ticker_clean,
            days, max_per_feed, include_market_news,
        )
        all_articles.extend(articles)

    logger.info("RSS total: %d articles from %d feeds", len(all_articles), len(RSS_FEEDS))
    return all_articles


def fetch_rss_global(days: int = 7, max_per_feed: int = 10) -> List[dict]:
    """
    Fetch general EGX market news from all RSS feeds (not ticker-specific).
    Good for macro context: regulatory changes, market-wide events, CBE decisions.
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
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=12,
                    allow_redirects=True,
                )
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
            except Exception:
                feed = feedparser.parse(feed_info["url"])

            for entry in feed.entries[:max_per_feed]:
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                if not _article_mentions_market(f"{title} {summary}"):
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
        except Exception as exc:
            logger.warning("RSS global feed '%s' failed: %s", feed_name, exc)

    return articles
