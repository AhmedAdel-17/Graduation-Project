"""
Reddit source v2 — intent-rich queries.

v1 used neutral queries like "Egyptian stock market analysis" which
returned discussion / educational posts. v2 biases the query set toward
TRADER-VOICE phrases ("buying", "sold", "target", "تجميع", "هيطلع")
combined with EGX-specific tokens. Precision-by-construction.

Why useful : Reddit's JSON endpoints work without auth, allow recent-time
              filters, and several niche subreddits collect EGX/MENA
              traders.
Data quality: medium — includes some long analysis posts, filtered out by
              content_type=ANALYSIS downstream weighting.
Method      : reddit search.json (un-authed) with descriptive UA.
Limitations : Reddit search relevance is poor; per-query throttle 1.0-1.5s.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import List

import requests

from . import Post

log = logging.getLogger("egx.v2.reddit")

REDDIT_HEADERS = {
    "User-Agent": "egx-research-pipeline-v2/1.0 (academic; contact: ahmedadel08.24.04@gmail.com)",
    "Accept": "application/json",
}

# Trader-voice queries ONLY. Every query pairs a STRONG intent verb with an
# EGX-specific token. No neutral / discussion / analysis queries. Queries that
# returned 0 trader-grade posts in prior runs have been removed.
QUERIES = [
    # English buy/sell intent
    'buying $COMI Egypt',
    'sold $ETEL',
    'EGX30 breakout',
    'EGX30 crash',
    'Orascom OCI buying',
    'Orascom bearish dump',
    'Egyptian stocks bullish rally',
    'Egyptian stocks bearish selloff',
    # Arabic intent verbs (Egyptian dialect)
    'تجميع البورصة المصرية',
    'هيطلع البورصة المصرية',
    'هينزل البورصة المصرية',
    'اشتري سهم اوراسكوم',
    'تصريف البورصة المصرية',
]

# Time window: "month" gets fresher trader voices than "year"
TIME_WINDOW = "month"


def scrape(per_query: int = 15, max_total: int = 200) -> List[Post]:
    out: List[Post] = []
    seen: set = set()

    for q in QUERIES:
        if len(out) >= max_total:
            break
        params = {"q": q, "limit": per_query, "sort": "relevance", "t": TIME_WINDOW}
        url = "https://www.reddit.com/search.json"
        resp = None
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, headers=REDDIT_HEADERS, timeout=15)
                if r.status_code == 200 and len(r.text) > 200:
                    resp = r
                    break
                log.debug("Reddit %r attempt %d: HTTP %s", q, attempt + 1, r.status_code)
            except Exception as e:
                log.debug("Reddit %r attempt %d: %s", q, attempt + 1, e)
            time.sleep(1.5 + attempt)
        if not resp:
            log.warning("Reddit query failed: %r", q)
            continue
        try:
            data = resp.json()
        except Exception as e:
            log.warning("Reddit JSON parse failed for %r: %s", q, e)
            continue
        added = 0
        for c in data.get("data", {}).get("children", []):
            d = c.get("data", {})
            pid = d.get("id")
            if not pid or pid in seen:
                continue
            title = (d.get("title") or "").strip()
            body = (d.get("selftext") or "").strip()
            text = (title + "\n" + body).strip()
            if len(text) < 10:
                continue
            seen.add(pid)
            permalink = d.get("permalink", "")
            url_full = (f"https://www.reddit.com{permalink}"
                        if permalink else d.get("url", ""))
            ts = d.get("created_utc")
            timestamp = (datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                         if ts else datetime.now(timezone.utc).isoformat())
            out.append(Post(
                text=text[:1500],
                username=d.get("author", "unknown"),
                timestamp=timestamp,
                url=url_full,
                platform="reddit",
                source=f"reddit-v2:{q[:30]}",
                engagement=int(d.get("score", 0)),
            ))
            added += 1
        log.info("Reddit q=%r -> +%d", q, added)
        time.sleep(random.uniform(1.0, 1.6))

    log.info("Reddit total: %d", len(out))
    return out
