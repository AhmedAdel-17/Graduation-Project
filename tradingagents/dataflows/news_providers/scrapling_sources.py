"""Scrapling-based scrapers for Egyptian financial news sites.

Uses the Scrapling library (https://github.com/d4vinci/Scrapling) to scrape
news articles from Egyptian financial websites that don't provide clean RSS/API.

Each scraper returns a list of article dicts with keys:
    title, summary, source, published_at, url, language
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import List

log = logging.getLogger("tradingagents.news.scrapling_sources")

try:
    from scrapling import Fetcher
    SCRAPLING_AVAILABLE = True
except ImportError:
    SCRAPLING_AVAILABLE = False
    log.info("scrapling not installed — web scrapers disabled")


def _fetcher() -> "Fetcher":
    return Fetcher()


def _text(element) -> str:
    """Extract text from a Scrapling element safely."""
    try:
        return element.get_all_text().strip()
    except Exception:
        return ""


def _href(element) -> str:
    """Extract href from a Scrapling element."""
    try:
        return element.attrib.get("href", "")
    except Exception:
        return ""


def _resolve_url(href: str, base: str) -> str:
    """Resolve relative URLs."""
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        from urllib.parse import urlparse
        parsed = urlparse(base)
        return f"{parsed.scheme}://{parsed.netloc}{href}"
    return href


# ---------------------------------------------------------------------------
# Enterprise Press (English, Egypt-focused business news)
# ---------------------------------------------------------------------------

def scrape_enterprise_press(max_articles: int = 50) -> List[dict]:
    """Scrape Enterprise Press stories — high-quality English Egypt business news."""
    if not SCRAPLING_AVAILABLE:
        return []
    articles: List[dict] = []
    base = "https://enterprise.press"

    try:
        f = _fetcher()
        page = f.get(f"{base}/stories/", timeout=20)
        if page.status != 200:
            log.warning("Enterprise Press: HTTP %d", page.status)
            return []

        links = page.css("h2 a, h3 a, .story__title a, a.story")
        seen_urls = set()

        for link in links:
            title = _text(link)
            href = _resolve_url(_href(link), base)
            if not title or len(title) < 15 or href in seen_urls:
                continue
            seen_urls.add(href)
            articles.append({
                "title": title,
                "summary": "",
                "source": "enterprise_press",
                "published_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "url": href,
                "language": "en",
            })
            if len(articles) >= max_articles:
                break

        log.info("Enterprise Press: %d articles scraped", len(articles))
    except Exception as exc:
        log.warning("Enterprise Press scrape failed: %s", exc)
    return articles


# ---------------------------------------------------------------------------
# Youm7 / اليوم السابع — Economy section (Arabic)
# ---------------------------------------------------------------------------

YOUM7_ECONOMY_URLS = [
    "https://www.youm7.com/Section/أخبار-الاقتصاد-والبورصة/297/1",
    "https://www.youm7.com/Section/أخبار-الاقتصاد-والبورصة/297/2",
]


def scrape_youm7_economy(max_articles: int = 60) -> List[dict]:
    """Scrape Youm7 economy/bourse section — major Arabic daily."""
    if not SCRAPLING_AVAILABLE:
        return []
    articles: List[dict] = []
    seen_urls = set()
    base = "https://www.youm7.com"

    f = _fetcher()
    for page_url in YOUM7_ECONOMY_URLS:
        if len(articles) >= max_articles:
            break
        try:
            page = f.get(page_url, timeout=20)
            if page.status != 200:
                log.info("Youm7 %s: HTTP %d", page_url[-30:], page.status)
                continue

            links = page.css("h3 a, h2 a")
            for link in links:
                title = _text(link)
                href = _resolve_url(_href(link), base)
                if not title or len(title) < 15 or href in seen_urls:
                    continue
                seen_urls.add(href)
                articles.append({
                    "title": title,
                    "summary": "",
                    "source": "youm7",
                    "published_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    "url": href,
                    "language": "ar",
                })
                if len(articles) >= max_articles:
                    break
        except Exception as exc:
            log.warning("Youm7 page failed: %s", exc)

    log.info("Youm7 economy: %d articles scraped", len(articles))
    return articles


# ---------------------------------------------------------------------------
# Al-Mal News / المال (Arabic financial newspaper)
# ---------------------------------------------------------------------------

ALMAL_URLS = [
    "https://almalnews.com/category/بورصات/",
    "https://almalnews.com/category/أسواق/",
]


def scrape_almal_news(max_articles: int = 40) -> List[dict]:
    """Scrape Al-Mal News — dedicated Arabic financial newspaper."""
    if not SCRAPLING_AVAILABLE:
        return []
    articles: List[dict] = []
    seen_urls = set()
    base = "https://almalnews.com"

    f = _fetcher()
    for page_url in ALMAL_URLS:
        if len(articles) >= max_articles:
            break
        try:
            page = f.get(page_url, timeout=20)
            if page.status != 200:
                log.info("Al-Mal %s: HTTP %d", page_url[-30:], page.status)
                continue

            links = page.css("h2 a, h3 a, .entry-title a")
            for link in links:
                title = _text(link)
                href = _resolve_url(_href(link), base)
                if not title or len(title) < 15 or href in seen_urls:
                    continue
                seen_urls.add(href)
                articles.append({
                    "title": title,
                    "summary": "",
                    "source": "almal_news",
                    "published_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    "url": href,
                    "language": "ar",
                })
                if len(articles) >= max_articles:
                    break
        except Exception as exc:
            log.warning("Al-Mal scrape failed: %s", exc)

    log.info("Al-Mal News: %d articles scraped", len(articles))
    return articles


# ---------------------------------------------------------------------------
# Amwal-Mag RSS (Arabic financial magazine)
# ---------------------------------------------------------------------------

def scrape_amwal_rss(max_articles: int = 30) -> List[dict]:
    """Parse Amwal-Mag RSS feed — Arabic financial magazine."""
    if not SCRAPLING_AVAILABLE:
        return []
    articles: List[dict] = []

    try:
        f = _fetcher()
        page = f.get("https://amwal-mag.com/feed/", timeout=15)
        if page.status != 200:
            log.info("Amwal RSS: HTTP %d", page.status)
            return []

        items = page.css("item")
        for item in items[:max_articles]:
            title_el = item.css("title")
            link_el = item.css("link")
            desc_el = item.css("description")
            pubdate_el = item.css("pubDate")

            title = _text(title_el[0]) if title_el else ""
            if not title or len(title) < 10:
                continue

            url = _text(link_el[0]) if link_el else ""
            summary = _text(desc_el[0])[:500] if desc_el else ""
            pubdate = _text(pubdate_el[0]) if pubdate_el else ""

            pub_iso = ""
            if pubdate:
                try:
                    from email.utils import parsedate_to_datetime
                    pub_iso = parsedate_to_datetime(pubdate).isoformat()
                except Exception:
                    pub_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

            articles.append({
                "title": title,
                "summary": summary,
                "source": "amwal_mag",
                "published_at": pub_iso,
                "url": url,
                "language": "ar",
            })

        log.info("Amwal RSS: %d articles", len(articles))
    except Exception as exc:
        log.warning("Amwal RSS failed: %s", exc)
    return articles


# ---------------------------------------------------------------------------
# NewsAPI — broad EGX market + per-ticker queries
# ---------------------------------------------------------------------------

def scrape_newsapi_broad(max_articles: int = 40) -> List[dict]:
    """Fetch broad EGX market news via NewsAPI (bilingual)."""
    import os
    api_key = os.getenv("NEWSAPI_KEY", "") or os.getenv("NEWS_API_KEY", "")
    if not api_key:
        log.info("NewsAPI: no key set, skipping")
        return []

    try:
        import requests
    except ImportError:
        return []

    articles: List[dict] = []
    queries = [
        ("en", '"Egyptian Stock Exchange" OR "EGX30" OR "Egypt bourse" OR "Cairo stock"'),
        ("ar", '"البورصة المصرية" OR "مؤشر EGX" OR "البورصة اليوم" OR "أسهم مصر"'),
        ("en", '"Egypt economy" OR "Egyptian pound" OR "EGP" OR "Central Bank Egypt"'),
    ]

    from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    for lang, query in queries:
        if len(articles) >= max_articles:
            break
        params = {
            "q": query,
            "from": from_date,
            "sortBy": "publishedAt",
            "pageSize": min(20, max_articles),
            "apiKey": api_key,
        }
        if lang == "en":
            params["language"] = "en"

        try:
            resp = requests.get(
                "https://newsapi.org/v2/everything",
                params=params, timeout=12,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "ok":
                for raw in data.get("articles", []):
                    title = raw.get("title", "")
                    if not title or title == "[Removed]":
                        continue
                    articles.append({
                        "title": title,
                        "summary": (raw.get("description", "") or "")[:500],
                        "source": f"newsapi_{lang}",
                        "published_at": raw.get("publishedAt", ""),
                        "url": raw.get("url", ""),
                        "language": lang,
                    })
        except Exception as exc:
            log.warning("NewsAPI (%s) failed: %s", lang, exc)

    log.info("NewsAPI broad: %d articles", len(articles))
    return articles[:max_articles]


# ---------------------------------------------------------------------------
# Aggregate all Scrapling sources
# ---------------------------------------------------------------------------

def scrape_all(max_per_source: int = 50) -> List[dict]:
    """Run all Scrapling-based + NewsAPI scrapers and return combined articles."""
    all_articles: List[dict] = []

    sources = [
        ("enterprise_press", lambda: scrape_enterprise_press(max_per_source)),
        ("youm7_economy", lambda: scrape_youm7_economy(max_per_source)),
        ("almal_news", lambda: scrape_almal_news(max_per_source)),
        ("amwal_rss", lambda: scrape_amwal_rss(30)),
        ("newsapi_broad", lambda: scrape_newsapi_broad(40)),
    ]

    source_counts = {}
    for name, fn in sources:
        try:
            batch = fn()
            source_counts[name] = len(batch)
            all_articles.extend(batch)
        except Exception as exc:
            log.exception("Source %s failed: %s", name, exc)
            source_counts[name] = 0

    log.info("All scrapling sources: %d total articles | %s", len(all_articles), source_counts)
    return all_articles
