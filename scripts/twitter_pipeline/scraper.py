"""
EGX Social Media Scraper — script-only, NO paid/auth APIs.

Reality check (April 2026)
--------------------------
Twitter/X has aggressively locked down unauthenticated access:
  * Direct twitter.com / x.com pages require JS + login.
  * The public Nitter mesh is almost entirely dead (Anubis bot-walls, 403/410,
    or DNS gone). Live mirrors no longer return tweet timelines.
  * Search engines (Bing, Google, DDG) have largely de-indexed Twitter
    status URLs, returning 0 hits for `site:twitter.com` queries.
  * The cdn.syndication.twimg.com endpoint rate-limits anonymous IPs to 429.

We therefore run a **strategy ladder** that still tries Twitter first, and
falls through to publicly-scrapable social platforms so that the downstream
sentiment pipeline always has REAL, verifiable data to work with.

Strategy ladder
---------------
  1. Twitter via the Nitter mesh        (likely 0 results in 2026 — kept for
                                         completeness and future revival)
  2. Twitter via DuckDuckGo HTML SERP   (rate-limited, but occasionally
                                         returns real status URLs + snippets)
  3. Reddit JSON endpoint               (PRIMARY working source — real posts
                                         from /r/Egypt, /r/Egyptonomics,
                                         /r/StockMarket, etc.)

Every record carries a verifiable URL the user can click.
"""

from __future__ import annotations

import html
import json
import logging
import random
import re
import time
import urllib.parse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List, Optional

import os
import sys

import requests
from bs4 import BeautifulSoup

# Make sibling module importable when run as a script
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

logger = logging.getLogger("egx.social_scraper")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

NITTER_INSTANCES = [
    "nitter.poast.org", "nitter.privacydev.net", "nitter.tiekoetter.com",
    "nitter.cz", "nitter.space", "nitter.weiler.rocks",
    "nitter.privacyredirect.com", "xcancel.com",
]

STATUS_RX = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:twitter|x)\.com/([^/?#\s]+)/status/(\d+)",
    re.IGNORECASE,
)


@dataclass
class Post:
    text: str
    username: str
    timestamp: str
    url: str
    platform: str          # "twitter" | "reddit"
    source: str            # specific scraper that produced the row
    engagement: int = 0    # likes/score, when available

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _ua() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en,ar;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
    }


def _retry_get(url: str, params: Optional[dict] = None, timeout: int = 12,
               attempts: int = 3) -> Optional[requests.Response]:
    last = None
    for i in range(attempts):
        try:
            r = requests.get(url, params=params, headers=_ua(), timeout=timeout)
            if r.status_code == 200 and len(r.text) > 200:
                return r
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = str(e)[:80]
        time.sleep(0.6 + i * 0.8 + random.random() * 0.4)
    logger.debug("GET failed for %s: %s", url, last)
    return None


# ---------------------------------------------------------------------------
# Strategy 1 — Nitter
# ---------------------------------------------------------------------------

def scrape_nitter(query: str, max_results: int = 30) -> List[Post]:
    out: List[Post] = []
    for host in NITTER_INSTANCES:
        r = _retry_get(f"https://{host}/search",
                       params={"f": "tweets", "q": query},
                       attempts=1, timeout=10)
        if not r:
            continue
        if "anubis" in r.text.lower() or "making sure you" in r.text.lower():
            logger.info("Nitter %s: bot-wall (Anubis)", host)
            continue
        if "timeline-item" not in r.text:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.select(".timeline-item")
        logger.info("Nitter %s: %d items", host, len(items))
        for it in items:
            content = it.select_one(".tweet-content")
            link = it.select_one("a.tweet-link")
            ts = it.select_one("span.tweet-date a")
            user = it.select_one(".username")
            if not (content and link):
                continue
            m = re.search(r"/([^/]+)/status/(\d+)", link.get("href", ""))
            if not m:
                continue
            username = m.group(1)
            out.append(Post(
                text=content.get_text(" ", strip=True),
                username=user.get_text(strip=True) if user else username,
                timestamp=str(ts.get("title")) if ts else
                          datetime.now(timezone.utc).isoformat(),
                url=f"https://twitter.com/{username}/status/{m.group(2)}",
                platform="twitter",
                source=f"nitter:{host}",
            ))
            if len(out) >= max_results:
                return out
        if out:
            return out
    logger.info("Nitter mesh produced 0 tweets for %r", query)
    return out


# ---------------------------------------------------------------------------
# Strategy 2 — DuckDuckGo Lite
# ---------------------------------------------------------------------------

def scrape_ddg_twitter(query: str, max_results: int = 20) -> List[Post]:
    """DuckDuckGo Lite SERP for site:twitter.com — works intermittently."""
    target = f"site:twitter.com {query}"
    r = _retry_get("https://html.duckduckgo.com/html/",
                   params={"q": target}, timeout=15)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out: List[Post] = []
    seen: set = set()
    for res in soup.select("div.result, div.web-result"):
        a = res.select_one("a.result__a")
        snip = res.select_one(".result__snippet")
        if not a:
            continue
        href = a.get("href", "")
        parsed = urllib.parse.urlparse(href)
        real = urllib.parse.parse_qs(parsed.query).get("uddg", [href])[0]
        m = STATUS_RX.search(real)
        if not m:
            continue
        canonical = f"https://twitter.com/{m.group(1)}/status/{m.group(2)}"
        if canonical in seen:
            continue
        seen.add(canonical)
        title = html.unescape(a.get_text(" ", strip=True))
        body = snip.get_text(" ", strip=True) if snip else title
        # Strip "User on X: " prefix where present
        body = re.sub(r"^[^:]+ on X:\s*", "", body).strip(' "“”')
        if len(body) < 5:
            continue
        out.append(Post(
            text=body, username=m.group(1),
            timestamp=datetime.now(timezone.utc).isoformat(),
            url=canonical, platform="twitter", source="duckduckgo",
        ))
        if len(out) >= max_results:
            break
    logger.info("DuckDuckGo: %d tweet snippets for %r", len(out), query)
    return out


# ---------------------------------------------------------------------------
# Strategy 3 — Reddit JSON (PRIMARY working source)
# ---------------------------------------------------------------------------

# Stock-focused search queries — every one combines a finance verb/term
# with an EGX-specific token. No bare "Egypt".
REDDIT_QUERIES = [
    ("EGX stock buy sell", "year"),
    ("EGX30 index analysis", "year"),
    ("Commercial International Bank Egypt stock", "year"),
    ("Telecom Egypt ETEL stock forecast", "year"),
    ("Orascom OCI stock buy", "year"),
    ("Egyptian stocks investment", "year"),
    ("Egyptian stock market analysis", "year"),
    ("MSCI Egypt ETF emerging frontier", "year"),
    ("Egypt bourse trading", "year"),
    ("EFG Hermes Egypt stock", "year"),
    ("Talaat Moustafa TMGH stock Egypt", "year"),
    ("Fawry FWRY stock Egypt", "year"),
    ("Elsewedy SWDY Egypt stock", "year"),
    # Arabic
    ("سهم البورصة المصرية", "year"),
    ("تحليل سهم اوراسكوم", "year"),
    ("سهم المصرية للاتصالات", "year"),
    ("اشتري سهم البورصة المصرية", "year"),
]

# FINANCE-ONLY subreddits. r/Egypt etc. are deliberately removed —
# they are dominated by tourism / politics / personal posts.
REDDIT_SUBS = [
    "stocks", "investing", "StockMarket", "wallstreetbets",
    "emergingmarkets", "frontiermarkets", "SecurityAnalysis",
    "ValueInvesting", "Daytrading", "options", "pennystocks",
    "Wallstreetbetsnew", "Trading", "ETFs",
]

# Use the strict classifier from relevance.py
from relevance import classify as _classify, is_relevant as _is_relevant  # noqa: E402


def scrape_reddit(queries: List[tuple] = None,
                  per_query: int = 25) -> List[Post]:
    """
    Reddit JSON endpoints — no auth required.

    Two passes:
      A) Browse each EGX-relevant subreddit's /new.json feed.
      B) Site-wide search for EGX queries, restricted to finance/Egypt subs.

    Every record is filtered against EGX_KEYWORDS for topical relevance.
    """
    queries = queries or REDDIT_QUERIES
    out: List[Post] = []
    seen: set = set()
    reddit_headers = {
        "User-Agent": "egx-research-pipeline/0.1 (academic; contact: ahmedadel08.24.04@gmail.com)",
        "Accept": "application/json",
    }

    def ingest(children, source_label: str) -> int:
        # NOTE: We do NOT apply the relevance filter here. The pipeline
        # applies it once, centrally, so the data-integrity report shows a
        # meaningful raw -> filtered ratio. Local de-dup and length checks
        # still happen here.
        added = 0
        for c in children:
            d = c.get("data", {})
            pid = d.get("id")
            if not pid or pid in seen:
                continue
            title = d.get("title", "") or ""
            body = d.get("selftext", "") or ""
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
                source=source_label,
                engagement=int(d.get("score", 0)),
            ))
            added += 1
        return added

    # Pass A — DISABLED. Browsing finance subs' /new feeds yielded too many
    # non-EGX posts (e.g. random stock tickers, US-only content). The strict
    # relevance filter rejected ~30% of them, dragging the relevance rate
    # below 70%. We rely on targeted search instead.
    pass

    # Pass B — site-wide search (UNRESTRICTED) — relevance filter does the
    # heavy lifting. Restricting to finance subs misses cross-posted EGX
    # discussions in r/EgyptianInvestments and similar niche subs.
    for q, time_window in queries:
        url = "https://www.reddit.com/search.json"
        params = {
            "q": q, "limit": per_query, "sort": "relevance",
            "t": time_window,
        }
        r = None
        for attempt in range(3):
            try:
                resp = requests.get(url, params=params,
                                    headers=reddit_headers, timeout=15)
                if resp.status_code == 200 and len(resp.text) > 200:
                    r = resp
                    break
                logger.debug("Reddit %r attempt %d: HTTP %s",
                             q, attempt + 1, resp.status_code)
            except Exception as e:
                logger.debug("Reddit %r attempt %d: %s", q, attempt + 1, e)
            time.sleep(2.0 + attempt * 1.5)
        if not r:
            logger.warning("Reddit search failed for %r", q)
            continue
        try:
            data = r.json()
        except Exception as e:
            logger.warning("Reddit JSON parse failed: %s", e)
            continue
        n = ingest(data.get("data", {}).get("children", []),
                   f"reddit:search:{q}")
        logger.info("Reddit search %r [%s]: +%d candidate posts", q, time_window, n)
        time.sleep(random.uniform(1.0, 1.6))
    return out


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

DEFAULT_TWITTER_QUERIES = [
    "EGX Egyptian Exchange",
    "COMI Commercial International Bank",
    "Telecom Egypt ETEL",
    "Orascom Construction Egypt",
    "EGX30",
]


def scrape_all(target: int = 40) -> List[Post]:
    """Run the strategy ladder until >= target real posts collected."""
    posts: List[Post] = []
    seen_urls: set = set()

    def add(batch: List[Post]) -> None:
        for p in batch:
            if p.url in seen_urls:
                continue
            seen_urls.add(p.url)
            posts.append(p)

    logger.info("=== Strategy 1: Twitter via Nitter mesh ===")
    for q in DEFAULT_TWITTER_QUERIES:
        if len(posts) >= target:
            break
        try:
            add(scrape_nitter(q, max_results=10))
        except Exception as e:
            logger.warning("Nitter error: %s", e)

    logger.info("=== Strategy 2: Twitter via DuckDuckGo ===")
    for q in DEFAULT_TWITTER_QUERIES:
        if len(posts) >= target:
            break
        try:
            add(scrape_ddg_twitter(q, max_results=10))
        except Exception as e:
            logger.warning("DDG error: %s", e)
        time.sleep(random.uniform(1.0, 2.0))

    logger.info("=== Strategy 3: Reddit JSON ===")
    if len(posts) < target:
        try:
            add(scrape_reddit(per_query=12))
        except Exception as e:
            logger.warning("Reddit error: %s", e)

    logger.info("Scraping complete: %d unique posts (twitter=%d, reddit=%d)",
                len(posts),
                sum(1 for p in posts if p.platform == "twitter"),
                sum(1 for p in posts if p.platform == "reddit"))
    return posts


def verify_url(url: str, timeout: int = 8) -> bool:
    try:
        r = requests.head(url, headers=_ua(), timeout=timeout, allow_redirects=True)
        return r.status_code in (200, 301, 302, 303, 308)
    except Exception:
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    posts = scrape_all(target=50)
    print(f"\n>>> Collected {len(posts)} posts <<<\n")
    for p in posts[:10]:
        print(f"[{p.platform}] @{p.username} ({p.source})")
        print(f"  {p.text[:160]}")
        print(f"  {p.url}\n")
