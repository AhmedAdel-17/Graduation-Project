"""
EGX Official Disclosure Source
================================
Scrapes regulatory disclosures from the Egyptian Exchange (EGX) public portal.

EGX companies are legally required under FRA regulations to publish:
  - Quarterly and annual financial statements
  - Dividend announcements
  - Board / AGM decisions
  - Material events (mergers, acquisitions, capital changes)

These are the HIGHEST-SIGNAL news items for EGX stocks — they come directly
from the issuer, are FRA-regulated, and arrive before any news outlet reports them.

Source: https://www.egx.com.eg/ar/Announcements.aspx (public, no auth needed)
Fallback: Google News RSS filtered for official EGX disclosure language.

Usage:
    articles = fetch_egx_disclosures("COMI", days=30)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional

logger = logging.getLogger("tradingagents.news.egx_disclosure")

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    logger.info("beautifulsoup4 not installed. EGX disclosure HTML parsing disabled. "
                "Install: pip install beautifulsoup4")

# =============================================================================
# EGX Disclosure Portal — public endpoints
# =============================================================================

EGX_ANNOUNCEMENT_URL = "https://www.egx.com.eg/ar/Announcements.aspx"
EGX_SEARCH_URL = "https://www.egx.com.eg/ar/Announcements.aspx"

# Arabic disclosure keywords — identifies an article as a formal EGX filing
DISCLOSURE_KEYWORDS_AR = [
    "إفصاح", "إعلان", "توزيع أرباح", "جمعية عمومية", "قوائم مالية",
    "نتائج أعمال", "قرار مجلس إدارة", "زيادة رأس المال", "طرح عام",
    "استحواذ", "اندماج", "إصدار سندات", "تقرير ربع سنوي", "ميزانية",
]
DISCLOSURE_KEYWORDS_EN = [
    "disclosure", "announcement", "dividend", "AGM", "EGM",
    "financial statements", "earnings", "board decision", "capital increase",
    "acquisition", "merger", "bond issuance", "quarterly results",
]

# Google News RSS query that targets EGX formal disclosures
_DISCLOSURE_RSS_TEMPLATE = (
    "https://news.google.com/rss/search?"
    "q={ticker}+%D8%A5%D9%81%D8%B5%D8%A7%D8%AD+%D8%A8%D9%88%D8%B1%D8%B5%D8%A9"
    "&hl=ar&gl=EG&ceid=EG:ar"
)


def _is_disclosure(text: str) -> bool:
    """Return True if text contains formal disclosure/regulatory language."""
    text_lower = text.lower()
    for kw in DISCLOSURE_KEYWORDS_AR:
        if kw in text:
            return True
    for kw in DISCLOSURE_KEYWORDS_EN:
        if kw in text_lower:
            return True
    return False


def _is_within_days(date_str: str, days: int) -> bool:
    if not date_str:
        return True
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00").split("+")[0])
        return dt >= datetime.now() - timedelta(days=days)
    except (ValueError, TypeError):
        return True


def _parse_feed_date(entry) -> str:
    """Extract ISO date from feedparser entry."""
    parsed = entry.get("published_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6]).strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            pass
    return entry.get("published", entry.get("updated", ""))


# ---------------------------------------------------------------------------
# Primary: EGX portal HTML scraping
# ---------------------------------------------------------------------------

def _scrape_egx_portal(ticker: str, days: int) -> List[dict]:
    """
    Attempt to scrape the EGX announcements portal.
    Returns list of disclosure article dicts, or empty list on failure.
    """
    if not REQUESTS_AVAILABLE or not BS4_AVAILABLE:
        logger.debug("Skipping EGX portal scrape: missing requests or bs4")
        return []

    ticker_clean = ticker.upper().replace(".CA", "").strip()
    articles = []

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            ),
            "Accept-Language": "ar,en;q=0.9",
            "Referer": "https://www.egx.com.eg/",
        }
        params = {
            "search": ticker_clean,
            "lang": "ar",
        }
        resp = requests.get(
            EGX_ANNOUNCEMENT_URL,
            params=params,
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # EGX portal uses a table or repeated div structure for announcements.
        # Try multiple selectors — portal HTML changes periodically.
        rows = (
            soup.select("table.announcement-table tr")
            or soup.select("div.announcement-item")
            or soup.select("tr.GridRow")
            or soup.select(".rgRow, .rgAltRow")
        )

        cutoff = datetime.now() - timedelta(days=days)

        for row in rows:
            cells = row.find_all(["td", "div"])
            if not cells:
                continue

            # Try to extract date, title from cells
            text_parts = [c.get_text(strip=True) for c in cells]
            full_text = " | ".join(text_parts)

            if not full_text.strip():
                continue

            # Try to parse a date from the row
            date_str = ""
            date_pattern = re.search(r'\d{1,2}[/\-]\d{1,2}[/\-]\d{4}', full_text)
            if date_pattern:
                raw_date = date_pattern.group(0).replace("/", "-")
                try:
                    parts = raw_date.split("-")
                    if len(parts[2]) == 4:  # DD-MM-YYYY
                        dt = datetime(int(parts[2]), int(parts[1]), int(parts[0]))
                    else:  # YYYY-MM-DD
                        dt = datetime.strptime(raw_date, "%Y-%m-%d")
                    if dt < cutoff:
                        continue
                    date_str = dt.strftime("%Y-%m-%dT%H:%M:%S")
                except (ValueError, IndexError):
                    pass

            # Use the longest text cell as the title
            title = max(text_parts, key=len) if text_parts else full_text[:150]
            title = title.strip()[:200]

            if len(title) < 10:
                continue

            # Only include items that look like actual disclosures
            if ticker_clean.lower() not in full_text.lower() and not _is_disclosure(full_text):
                continue

            articles.append({
                "title": title,
                "summary": full_text[:500],
                "source": "egx_portal",
                "published_at": date_str,
                "url": EGX_ANNOUNCEMENT_URL,
                "language": "ar",
            })

        logger.info("EGX portal: %d disclosures found for %s", len(articles), ticker_clean)

    except Exception as exc:
        logger.warning("EGX portal scrape failed for %s: %s", ticker_clean, exc)

    return articles


# ---------------------------------------------------------------------------
# Fallback: Google News RSS filtered for disclosure language
# ---------------------------------------------------------------------------

def _fetch_disclosure_rss(ticker: str, days: int) -> List[dict]:
    """
    Fetch EGX-related disclosure articles via Google News RSS.
    Filters only articles that contain formal disclosure keywords.
    """
    ticker_clean = ticker.upper().replace(".CA", "").strip()
    articles = []

    try:
        import feedparser
    except ImportError:
        logger.debug("feedparser not available for disclosure RSS fallback")
        return []

    # Two queries: one for Arabic disclosure language, one for English
    rss_queries = [
        (
            "ar",
            f"https://news.google.com/rss/search?q={ticker_clean}+"
            "%D8%A5%D9%81%D8%B5%D8%A7%D8%AD+%D8%A8%D9%88%D8%B1%D8%B5%D8%A9"
            "&hl=ar&gl=EG&ceid=EG:ar",
        ),
        (
            "en",
            f"https://news.google.com/rss/search?q={ticker_clean}+EGX+disclosure+announcement"
            "&hl=en&gl=EG&ceid=EG:en",
        ),
    ]

    for lang, url in rss_queries:
        try:
            if REQUESTS_AVAILABLE:
                resp = requests.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=12,
                )
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
            else:
                feed = feedparser.parse(url)

            for entry in feed.entries[:30]:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                searchable = f"{title} {summary}"

                if not _is_disclosure(searchable):
                    continue

                pub_date = _parse_feed_date(entry)
                if not _is_within_days(pub_date, days):
                    continue

                clean_summary = re.sub(r'<[^>]+>', '', summary)[:500]
                articles.append({
                    "title": title,
                    "summary": clean_summary,
                    "source": "egx_disclosure_rss",
                    "published_at": pub_date,
                    "url": entry.get("link", ""),
                    "language": lang,
                })

        except Exception as exc:
            logger.warning("Disclosure RSS (%s) failed: %s", lang, exc)

    logger.info("Disclosure RSS fallback: %d items for %s", len(articles), ticker_clean)
    return articles


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_egx_disclosures(ticker: str, days: int = 30) -> List[dict]:
    """
    Fetch official EGX regulatory disclosures for a ticker.

    Strategy:
      1. Try EGX portal HTML scraping (highest signal, requires bs4)
      2. Fall back to Google News RSS filtered for disclosure language

    Args:
        ticker: EGX ticker (e.g., "COMI" or "COMI.CA")
        days: How many days back to search (default 30 — disclosures are infrequent)

    Returns:
        List of article dicts tagged source="egx_portal" or "egx_disclosure_rss"
    """
    ticker_clean = ticker.upper().replace(".CA", "").strip()
    logger.info("Fetching EGX disclosures for %s (last %d days)", ticker_clean, days)

    # Try portal first
    portal_articles = _scrape_egx_portal(ticker_clean, days)
    if portal_articles:
        return portal_articles

    # Fall back to RSS-based disclosure filtering
    return _fetch_disclosure_rss(ticker_clean, days)
