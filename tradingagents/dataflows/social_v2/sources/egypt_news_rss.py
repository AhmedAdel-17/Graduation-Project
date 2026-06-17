"""Curated direct Egyptian-outlet RSS source.

Complements the search-aggregators (Google News / Bing News) with a small set
of *direct* outlet RSS feeds. Direct feeds give non-aggregator diversity: they
surface articles the search crawlers rank low or miss, and they keep delivering
if the aggregators are throttled.

The default seed list is intentionally tiny — only feeds verified live to
return Egyptian business/finance content. The real value is the extension
point: operators add their own working feeds via ``EGX_EXTRA_RSS_FEEDS``
(comma-separated ``label=url`` pairs), e.g.::

    EGX_EXTRA_RSS_FEEDS="almal=https://almalnews.com/feed/,amwal=https://amwalalghad.com/feed/"

Filter contract mirrors the other news-grade sources: drop items older than
``EGYPT_RSS_MAX_POST_AGE_DAYS`` (default 3) and shorter than MIN_TEXT_CHARS.
Downstream EGX relevance + quality gates still apply, so a noisy feed costs at
worst a few dropped items, never a bad signal.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

import requests

try:
    import feedparser  # type: ignore
except Exception:  # pragma: no cover
    feedparser = None  # type: ignore

from ..models import Post
from .google_news_ar import (
    HTTP_TIMEOUT,
    MIN_TEXT_CHARS,
    PER_QUERY_SLEEP,
    USER_AGENT,
    _parse_dt,
    _strip_html,
)

log = logging.getLogger("tradingagents.social_v2.egypt_news_rss")

# (label, feed_url). Verified live 2026-06; keep only feeds that actually
# return relevant content — a dead feed just wastes an HTTP round-trip.
DEFAULT_FEEDS: List[Tuple[str, str]] = [
    ("hapijournal", "https://hapijournal.com/feed/"),
]

DEFAULT_MAX_AGE_DAYS = 3
MAX_PER_FEED = 40


def _parse_feeds_env(value: str | None) -> List[Tuple[str, str]]:
    """Parse ``EGX_EXTRA_RSS_FEEDS`` (comma-separated ``label=url``)."""
    if not value:
        return []
    out: List[Tuple[str, str]] = []
    for i, raw in enumerate(value.split(",")):
        raw = raw.strip()
        if not raw:
            continue
        if "=" in raw:
            label, url = raw.split("=", 1)
            out.append((label.strip(), url.strip()))
        else:
            out.append((f"extra_{i}", raw))
    return out


def _fetch_feed(url: str) -> bytes | None:
    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT
        )
        if response.status_code >= 400:
            log.warning("Egypt RSS %s -> HTTP %s", url, response.status_code)
            return None
        return response.content
    except Exception as exc:
        log.warning("Egypt RSS %s fetch failed: %s", url, exc)
        return None


def scrape(max_per_feed: int = MAX_PER_FEED) -> List[Post]:
    if feedparser is None:
        log.warning("Egypt RSS SKIPPED — feedparser not installed")
        return []

    feeds = list(DEFAULT_FEEDS) + _parse_feeds_env(os.getenv("EGX_EXTRA_RSS_FEEDS"))
    max_age_days = int(
        os.getenv("EGYPT_RSS_MAX_POST_AGE_DAYS", str(DEFAULT_MAX_AGE_DAYS))
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    posts: List[Post] = []
    seen_urls: set[str] = set()

    for label, feed_url in feeds:
        raw = _fetch_feed(feed_url)
        if raw is None:
            time.sleep(PER_QUERY_SLEEP)
            continue
        try:
            parsed = feedparser.parse(raw)
        except Exception as exc:
            log.warning("Egypt RSS %s parse failed: %s", label, exc)
            time.sleep(PER_QUERY_SLEEP)
            continue

        entries = list(parsed.entries or [])[:max_per_feed]
        n_kept = n_old = n_short = n_dup = 0
        for entry in entries:
            url = (getattr(entry, "link", "") or "").strip()
            if not url or url in seen_urls:
                n_dup += 1 if url else 0
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
                    username=f"egypt-rss:{label}",
                    timestamp=dt.isoformat(),
                    url=url,
                    platform="news",
                    source=f"egypt_news_rss:{label}",
                    engagement=0,
                )
            )
            n_kept += 1
        log.info(
            "Egypt RSS [%s] -> %d kept (entries=%d old>%dd=%d short=%d dup=%d)",
            label, n_kept, len(entries), max_age_days, n_old, n_short, n_dup,
        )
        time.sleep(PER_QUERY_SLEEP)

    log.info("Egypt RSS total: %d posts", len(posts))
    return posts
