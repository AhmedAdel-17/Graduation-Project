"""Bing News Arabic RSS source — second free news aggregator.

Why this source exists:
   * It is an *independent* aggregator to Google News (different crawler,
     different ranking), so it both widens coverage and acts as the natural
     fallback when Google News rate-limits or returns an empty feed.
   * It reuses the EXACT same multi-axis Arabic query set as
     ``google_news_ar`` (market / sector / event / ticker), so every Bing
     article lands in the same sentiment layer its Google twin would —
     keeping one source of truth for the query taxonomy.

Endpoint (free, no auth, no quota):
   https://www.bing.com/news/search?q=<query>&format=RSS&setlang=ar&cc=EG

Filter contract mirrors ``google_news_ar``: drop items older than
``BING_NEWS_MAX_POST_AGE_DAYS`` (default 3) and shorter than MIN_TEXT_CHARS.
Downstream EGX relevance + quality gates still apply.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import List, Tuple
from urllib.parse import quote_plus

import requests

try:
    import feedparser  # type: ignore
except Exception:  # pragma: no cover
    feedparser = None  # type: ignore

from ..models import Post
# Reuse Google News AR's helpers + query taxonomy so the two aggregators stay
# in lockstep and the layered queries live in exactly one place.
from .google_news_ar import (
    DEFAULT_QUERIES,
    HTTP_TIMEOUT,
    MAX_PER_QUERY,
    MIN_TEXT_CHARS,
    PER_QUERY_SLEEP,
    USER_AGENT,
    _parse_dt,
    _parse_queries_env,
    _strip_html,
)

log = logging.getLogger("tradingagents.social_v2.bing_news_ar")

DEFAULT_MAX_AGE_DAYS = 3


def _build_rss_url(query: str) -> str:
    return (
        "https://www.bing.com/news/search?"
        f"q={quote_plus(query)}&format=RSS&setlang=ar&cc=EG"
    )


def _fetch_feed(url: str) -> bytes | None:
    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT
        )
        if response.status_code >= 400:
            log.warning("Bing News %s -> HTTP %s", url, response.status_code)
            return None
        return response.content
    except Exception as exc:
        log.warning("Bing News %s fetch failed: %s", url, exc)
        return None


def scrape(max_per_query: int = MAX_PER_QUERY) -> List[Post]:
    if feedparser is None:
        log.warning("Bing News AR SKIPPED — feedparser not installed")
        return []
    if os.getenv("ENABLE_BING_NEWS_AR", "1") == "0":
        log.info("Bing News AR disabled via ENABLE_BING_NEWS_AR=0")
        return []

    queries: List[Tuple[str, str]] = (
        _parse_queries_env(os.getenv("GOOGLE_NEWS_AR_QUERIES")) or DEFAULT_QUERIES
    )
    max_age_days = int(
        os.getenv("BING_NEWS_MAX_POST_AGE_DAYS", str(DEFAULT_MAX_AGE_DAYS))
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    posts: List[Post] = []
    seen_urls: set[str] = set()

    for label, query in queries:
        raw = _fetch_feed(_build_rss_url(query))
        if raw is None:
            time.sleep(PER_QUERY_SLEEP)
            continue
        try:
            parsed = feedparser.parse(raw)
        except Exception as exc:
            log.warning("Bing News %s parse failed: %s", label, exc)
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
                    username=f"bing-news-ar:{label}",
                    timestamp=dt.isoformat(),
                    url=url,
                    platform="news",
                    source=f"bing_news_ar:{label}",
                    engagement=0,
                )
            )
            n_kept += 1
        log.info(
            "Bing News AR [%s] -> %d kept (entries=%d old>%dd=%d short=%d dup=%d)",
            label, n_kept, len(entries), max_age_days, n_old, n_short, n_dup,
        )
        time.sleep(PER_QUERY_SLEEP)

    log.info("Bing News AR total: %d posts", len(posts))
    return posts
