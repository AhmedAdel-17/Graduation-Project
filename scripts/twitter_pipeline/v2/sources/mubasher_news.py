"""
Mubasher.info — Arabic financial news aggregator (Egyptian section).

Why useful : `mubasher.info/egx` carries Egypt-specific market news. Headlines
              are typically short, factual, and explicitly tag the company —
              easy entity extraction; perfect NEWS-class content (weight 0.6
              in the v2 aggregator).
Data quality: HIGH topical relevance (every item is tagged to EGX). Lower
              opinion content — we treat these as NEWS-grade.
Method      : public RSS / news listing, parsed with BeautifulSoup. No auth.
Limitations : Domain layout can change; the scraper degrades to 0 results
              instead of crashing. NEWS posts have lower weight than OPINION.

Honesty note: At time-of-write the public listing structure may have
              changed. The fetcher tolerates HTML-shape drift by trying
              several CSS selectors and only emits a record when both
              headline text and a usable URL are present.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from . import Post

log = logging.getLogger("egx.v2.mubasher")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

LISTING_URLS = [
    "https://www.mubasher.info/markets/EGX/news",
    "https://www.mubasher.info/countries/eg/news",
    "https://english.mubasher.info/markets/EGX/news",
]


def _get(url: str, timeout: int = 15) -> str | None:
    for attempt in range(2):
        try:
            r = requests.get(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ar,en;q=0.7",
            }, timeout=timeout)
            if r.status_code == 200 and len(r.text) > 1000:
                return r.text
            log.debug("mubasher %s HTTP %s", url, r.status_code)
        except Exception as e:
            log.debug("mubasher %s attempt %d: %s", url, attempt + 1, e)
        time.sleep(1.0 + attempt)
    return None


# Try a few candidate item selectors; first to yield >5 items wins.
ITEM_SELECTORS = [
    "article.news-item",
    "li.news-list-item",
    "div.news-row",
    "a.news-link",
    "article",
    "li.title",
]


def _extract_items(base_url: str, html: str) -> List[Post]:
    soup = BeautifulSoup(html, "html.parser")
    posts: List[Post] = []
    chosen = None
    for sel in ITEM_SELECTORS:
        items = soup.select(sel)
        if len(items) >= 5:
            chosen = (sel, items)
            break
    if not chosen:
        # last resort: collect any anchor that looks like a news link
        items = [a for a in soup.find_all("a", href=True)
                 if "/news/" in a.get("href", "") and len(a.get_text(strip=True)) > 20]
        if len(items) >= 5:
            chosen = ("a[href*=/news/]", items)
    if not chosen:
        log.info("mubasher: no item selector matched at %s", base_url)
        return posts

    sel, items = chosen
    log.info("mubasher: %s -> %d items via %r", base_url, len(items), sel)

    seen_urls: set = set()
    for it in items:
        # title text + href
        a = it if it.name == "a" else (it.find("a", href=True))
        if not a:
            continue
        href = a.get("href", "")
        if not href or "/news/" not in href:
            continue
        full = urljoin(base_url, href)
        if full in seen_urls:
            continue
        seen_urls.add(full)
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 12:
            continue
        # try to find a publish-time on the same item
        ts_tag = it.find(attrs={"datetime": True}) if hasattr(it, "find") else None
        ts = (ts_tag.get("datetime") if ts_tag else
              datetime.now(timezone.utc).isoformat())
        posts.append(Post(
            text=title[:500],
            username="mubasher",
            timestamp=ts,
            url=full,
            platform="mubasher",
            source="mubasher:listing",
            engagement=0,
        ))
    return posts


def scrape(max_total: int = 80) -> List[Post]:
    out: List[Post] = []
    for url in LISTING_URLS:
        if len(out) >= max_total:
            break
        html = _get(url)
        if not html:
            continue
        batch = _extract_items(url, html)
        out.extend(batch)
    log.info("Mubasher total: %d", len(out))
    return out
