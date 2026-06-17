"""Google News Arabic RSS source — broad Egyptian financial coverage.

Why this source:
   * Aggregates *hundreds* of Egyptian news outlets (AlMal, AlBorsa,
     Mubasher, Enterprise, YallaFinance, AhlMasr, Masrawy, ...) behind
     one free, no-auth, no-quota RSS endpoint.
   * Multi-axis queries directly populate the layered sentiment system:
     market-level, sector-level, and event-level queries each contribute
     to their natural layer downstream.

Endpoint:
   https://news.google.com/rss/search?q=<query>&hl=ar&gl=EG&ceid=EG:ar

Filter contract (mirrors other news-grade sources):
   * Drop items older than ``GOOGLE_NEWS_MAX_POST_AGE_DAYS`` (default 3).
   * Drop items shorter than MIN_TEXT_CHARS to avoid placeholders.

Anti-spam:
   * Cross-query dedupe by URL.
   * Per-query results capped at MAX_PER_QUERY.

The pipeline's downstream EGX relevance/quality gates still apply.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import List, Tuple
from urllib.parse import quote_plus

import requests

try:
    import feedparser  # type: ignore
except Exception:  # pragma: no cover
    feedparser = None  # type: ignore

from ..models import Post

log = logging.getLogger("tradingagents.social_v2.google_news_ar")

# Each entry is (label, query). The label is informational — it goes into
# the post's `source` field so the downstream archive shows which query
# surfaced the article.
#
# Queries are chosen to populate the four sentiment layers:
#   * market_*  → feeds the market layer
#   * sector_*  → feeds the sector layer
#   * event_*   → feeds the event layer
#   * ticker_*  → feeds the ticker layer (a small set of bellwether names)
DEFAULT_QUERIES: List[Tuple[str, str]] = [
    # --- Market-level ---
    ("market_egx",       '"البورصة المصرية"'),
    ("market_egx_index", '"مؤشر EGX30" OR "EGX 30"'),
    ("market_stocks",    '"أسهم البورصة المصرية"'),

    # --- Sector-level ---
    ("sector_banks",       '"البنوك المصرية" OR "القطاع المصرفي المصري"'),
    ("sector_real_estate", '"شركات العقارات المصرية" OR "القطاع العقاري المصري"'),
    ("sector_industry",    '"القطاع الصناعي المصري" OR "صناعة الحديد في مصر"'),
    ("sector_telecom",     '"قطاع الاتصالات المصري"'),
    ("sector_fintech",     '"التكنولوجيا المالية" مصر'),
    ("sector_food",        '"قطاع الأغذية المصري"'),
    ("sector_pharma",      '"قطاع الأدوية المصري"'),

    # --- Event-level macro ---
    ("event_egp",        '"تعويم الجنيه" OR "أزمة الدولار"'),
    ("event_imf",        '"صندوق النقد الدولي" مصر'),
    ("event_rates",      '"البنك المركزي المصري" "سعر الفائدة"'),
    ("event_inflation",  'مصر "معدل التضخم"'),
    ("event_gaza",       '"حرب غزة" بورصة OR اقتصاد'),
    ("event_red_sea",    '"البحر الأحمر" "قناة السويس"'),
    ("event_rating",     '"تصنيف مصر الائتماني"'),
]

DEFAULT_MAX_AGE_DAYS = 3
MIN_TEXT_CHARS = 40
MAX_PER_QUERY = 25
HTTP_TIMEOUT = 20
PER_QUERY_SLEEP = 0.6
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0"
)


def _build_rss_url(query: str) -> str:
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl=ar&gl=EG&ceid=EG:ar"
    )


def _strip_html(value: str) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = (
        text.replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    return re.sub(r"\s+", " ", text).strip()


def _parse_dt(entry) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        value = getattr(entry, key, None)
        if value:
            try:
                return datetime(*value[:6], tzinfo=timezone.utc)
            except Exception:
                continue
    return datetime.now(timezone.utc)


def _fetch_feed(url: str) -> bytes | None:
    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT
        )
        if response.status_code >= 400:
            log.warning("Google News %s -> HTTP %s", url, response.status_code)
            return None
        return response.content
    except Exception as exc:
        log.warning("Google News %s fetch failed: %s", url, exc)
        return None


def _parse_queries_env(value: str | None) -> List[Tuple[str, str]] | None:
    """Parse `GOOGLE_NEWS_AR_QUERIES` env override.

    Format: comma-separated ``label=query`` pairs. Label is optional;
    falls back to ``custom_<i>``.
    """
    if not value:
        return None
    out: List[Tuple[str, str]] = []
    for i, raw in enumerate(value.split(",")):
        raw = raw.strip()
        if not raw:
            continue
        if "=" in raw:
            label, query = raw.split("=", 1)
            out.append((label.strip(), query.strip()))
        else:
            out.append((f"custom_{i}", raw))
    return out or None


def scrape(max_per_query: int = MAX_PER_QUERY) -> List[Post]:
    if feedparser is None:
        log.warning("Google News AR SKIPPED — feedparser not installed")
        return []

    queries = _parse_queries_env(os.getenv("GOOGLE_NEWS_AR_QUERIES")) or DEFAULT_QUERIES
    max_age_days = int(
        os.getenv("GOOGLE_NEWS_MAX_POST_AGE_DAYS", str(DEFAULT_MAX_AGE_DAYS))
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    posts: List[Post] = []
    seen_urls: set[str] = set()
    import time

    for label, query in queries:
        raw = _fetch_feed(_build_rss_url(query))
        if raw is None:
            time.sleep(PER_QUERY_SLEEP)
            continue
        try:
            parsed = feedparser.parse(raw)
        except Exception as exc:
            log.warning("Google News %s parse failed: %s", label, exc)
            time.sleep(PER_QUERY_SLEEP)
            continue

        entries = list(parsed.entries or [])[:max_per_query]
        n_kept = n_old = n_short = n_dup = 0
        for entry in entries:
            url = (getattr(entry, "link", "") or "").strip()
            if not url:
                continue
            if url in seen_urls:
                n_dup += 1
                continue
            title = _strip_html(getattr(entry, "title", "") or "")
            summary = _strip_html(getattr(entry, "summary", "") or "")
            text = f"{title}\n{summary}".strip()
            if len(text) < MIN_TEXT_CHARS:
                n_short += 1
                continue
            dt = _parse_dt(entry)
            if dt < cutoff:
                n_old += 1
                continue
            seen_urls.add(url)
            posts.append(
                Post(
                    text=text[:1500],
                    username=f"google-news-ar:{label}",
                    timestamp=dt.isoformat(),
                    url=url,
                    platform="news",
                    source=f"google_news_ar:{label}",
                    engagement=0,
                )
            )
            n_kept += 1
        log.info(
            "Google News AR [%s] -> %d kept (entries=%d old>%dd=%d short=%d dup=%d)",
            label, n_kept, len(entries), max_age_days, n_old, n_short, n_dup,
        )
        time.sleep(PER_QUERY_SLEEP)

    log.info("Google News AR total: %d posts", len(posts))
    return posts
