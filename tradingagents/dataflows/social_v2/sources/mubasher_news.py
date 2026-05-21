"""Mubasher EGX news source — RSS-backed.

Mubasher publishes per-exchange RSS feeds in both Arabic and English.
We pull EGX-specific feeds (AR + EN), normalize to the project's `Post`
schema, and let the rest of the pipeline (relevance → entity → intent →
sentiment) consume them like any other source.

Why this source:
   * No auth, no quota, no rate limit.
   * Curated EGX news — high signal-to-noise vs raw social.
   * Bilingual: Arabic feed routes to CAMeLBERT, English to FinBERT.

Feeds:
   AR  http://feeds.mubasher.info/ar/EGX/news
   EN  http://feeds.mubasher.info/en/EGX/news

Filter contract:
   * Drop items older than MAX_AGE_DAYS (default 3, env-overridable).
   * Drop items shorter than MIN_TEXT_CHARS to avoid placeholders.

The pipeline downstream applies its own EGX relevance / quality gates.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import List

import requests

try:
    import feedparser  # type: ignore
except Exception:  # pragma: no cover
    feedparser = None  # type: ignore

from ..models import Post

log = logging.getLogger("tradingagents.social_v2.mubasher")

FEEDS = [
    ("ar", "http://feeds.mubasher.info/ar/EGX/news"),
    ("en", "http://feeds.mubasher.info/en/EGX/news"),
]

DEFAULT_MAX_AGE_DAYS = 3
MIN_TEXT_CHARS = 40
HTTP_TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0"
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


def _fetch_feed_bytes(url: str) -> bytes | None:
    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT
        )
        if response.status_code >= 400:
            log.warning("Mubasher %s -> HTTP %s", url, response.status_code)
            return None
        return response.content
    except Exception as exc:
        log.warning("Mubasher %s fetch failed: %s", url, exc)
        return None


def scrape(max_per_feed: int = 80) -> List[Post]:
    if feedparser is None:
        log.warning("Mubasher SKIPPED — feedparser not installed")
        return []

    max_age_days = int(
        os.getenv("MUBASHER_MAX_POST_AGE_DAYS", str(DEFAULT_MAX_AGE_DAYS))
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    posts: List[Post] = []
    seen_urls: set[str] = set()

    for lang, feed_url in FEEDS:
        raw = _fetch_feed_bytes(feed_url)
        if raw is None:
            continue
        try:
            parsed = feedparser.parse(raw)
        except Exception as exc:
            log.warning("Mubasher %s parse failed: %s", feed_url, exc)
            continue

        entries = list(parsed.entries or [])[:max_per_feed]
        n_kept = n_old = n_short = 0
        for entry in entries:
            url = (getattr(entry, "link", "") or "").strip()
            if not url or url in seen_urls:
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
                    username=f"mubasher-{lang}",
                    timestamp=dt.isoformat(),
                    url=url,
                    platform="news",
                    source=f"mubasher:{lang}",
                    engagement=0,
                )
            )
            n_kept += 1
        log.info(
            "Mubasher %s -> %d kept (entries=%d old>%dd=%d short=%d)",
            lang.upper(),
            n_kept,
            len(entries),
            max_age_days,
            n_old,
            n_short,
        )

    log.info("Mubasher total: %d posts", len(posts))
    return posts
