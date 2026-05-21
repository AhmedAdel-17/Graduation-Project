"""Reddit source — intent-rich queries (no auth)."""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import List

import requests

from ..models import Post

log = logging.getLogger("tradingagents.social_v2.reddit")

REDDIT_HEADERS = {
    "User-Agent": "egx-research-pipeline-v2/1.0 (academic)",
    "Accept": "application/json",
}

QUERIES = [
    'buying $COMI Egypt',
    'sold $ETEL',
    'EGX30 breakout',
    'EGX30 crash',
    'Orascom OCI buying',
    'Egyptian stocks bullish rally',
    'Egyptian stocks bearish selloff',
    'تجميع البورصة المصرية',
    'هيطلع البورصة المصرية',
    'هينزل البورصة المصرية',
    'اشتري سهم اوراسكوم',
    'تصريف البورصة المصرية',
]

TIME_WINDOW = "month"


def scrape(per_query: int = 12, max_total: int = 160) -> List[Post]:
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
            url_full = (
                f"https://www.reddit.com{permalink}"
                if permalink
                else d.get("url", "")
            )
            ts = d.get("created_utc")
            timestamp = (
                datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                if ts
                else datetime.now(timezone.utc).isoformat()
            )
            out.append(
                Post(
                    text=text[:1500],
                    username=d.get("author", "unknown"),
                    timestamp=timestamp,
                    url=url_full,
                    platform="reddit",
                    source=f"reddit-v2:{q[:30]}",
                    engagement=int(d.get("score", 0)),
                )
            )
            added += 1
        log.info("Reddit q=%r -> +%d", q, added)
        time.sleep(random.uniform(1.0, 1.6))

    log.info("Reddit total: %d", len(out))
    return out
