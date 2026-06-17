"""Investing.com Arabic pilot source.

Pilot rationale:
   Investing.com (sa.investing.com) carries a steady stream of Arabic
   coverage of Egyptian equities, often with analyst-leaning commentary
   that complements the more news-y Mubasher / AlMal stream.

How we access it without scraping:
   We use Google News Arabic RSS scoped to ``site:sa.investing.com``.
   This bypasses Investing.com's bot defences entirely (we never hit
   their server) and reuses the same proven RSS path as
   ``google_news_ar``. If volume is too thin to be worth the slot,
   set ``ENABLE_INVESTING_COM_AR=0`` to disable.

Filter contract: same as the other news-grade sources (recency + min length).
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

log = logging.getLogger("tradingagents.social_v2.investing_com_ar")

# site-filtered Google News queries targeting Investing.com Arabic.
DEFAULT_QUERIES: List[Tuple[str, str]] = [
    ("investing_market",     'site:sa.investing.com "البورصة المصرية"'),
    ("investing_egx30",      'site:sa.investing.com "EGX 30"'),
    ("investing_banks",      'site:sa.investing.com البنوك المصرية'),
    ("investing_real_estate",'site:sa.investing.com عقارات مصر'),
    ("investing_macro",      'site:sa.investing.com مصر اقتصاد'),
    ("investing_egp",        'site:sa.investing.com الجنيه المصري'),
]

DEFAULT_MAX_AGE_DAYS = 5
MIN_TEXT_CHARS = 40
MAX_PER_QUERY = 20
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
            log.warning("Investing.com AR %s -> HTTP %s", url, response.status_code)
            return None
        return response.content
    except Exception as exc:
        log.warning("Investing.com AR %s fetch failed: %s", url, exc)
        return None


def scrape(max_per_query: int = MAX_PER_QUERY) -> List[Post]:
    if os.getenv("ENABLE_INVESTING_COM_AR", "1") == "0":
        log.info("Investing.com AR disabled via env")
        return []
    if feedparser is None:
        log.warning("Investing.com AR SKIPPED — feedparser not installed")
        return []

    max_age_days = int(
        os.getenv("INVESTING_COM_AR_MAX_POST_AGE_DAYS", str(DEFAULT_MAX_AGE_DAYS))
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    posts: List[Post] = []
    seen_urls: set[str] = set()
    import time

    for label, query in DEFAULT_QUERIES:
        raw = _fetch_feed(_build_rss_url(query))
        if raw is None:
            time.sleep(PER_QUERY_SLEEP)
            continue
        try:
            parsed = feedparser.parse(raw)
        except Exception as exc:
            log.warning("Investing.com AR %s parse failed: %s", label, exc)
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
                    username=f"investing-com-ar:{label}",
                    timestamp=dt.isoformat(),
                    url=url,
                    platform="news",
                    source=f"investing_com_ar:{label}",
                    engagement=0,
                )
            )
            n_kept += 1
        log.info(
            "Investing.com AR [%s] -> %d kept (entries=%d old>%dd=%d short=%d dup=%d)",
            label, n_kept, len(entries), max_age_days, n_old, n_short, n_dup,
        )
        time.sleep(PER_QUERY_SLEEP)

    log.info("Investing.com AR total: %d posts", len(posts))
    return posts
