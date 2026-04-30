"""
Authenticated Twitter / X scraper via Playwright.

Why this approach:
   * Nitter mesh is dead in 2026 (Anubis bot-walls, dead DNS).
   * Search engines have de-indexed twitter.com.
   * cdn.syndication.twimg.com rate-limits anonymous IPs.
   * The remaining viable route is a real, logged-in browser session.
   * We use Playwright (headless Chromium) with persisted cookies, NOT the
     paid X API. The user provides cookies once, the scraper re-uses them.

Setup (one-time):
   1. pip install playwright
      python -m playwright install chromium
   2. Log in to x.com manually in a normal browser. Export cookies to JSON
      using a browser extension ("Get cookies.txt LOCALLY" -> "Export as JSON")
      OR run `python -m scripts.twitter_pipeline.v2.sources.twitter_authed --login`
      to launch a one-time interactive login that saves the storage state.
   3. Set env var EGX_X_STORAGE_STATE=/abs/path/to/storage_state.json

Run:
   The scraper is invoked automatically by pipeline_v2 if Playwright +
   storage_state are available. Otherwise it logs the reason and returns [].

Honesty note:
   Scraping x.com against TOS may put the account at risk of suspension. Use
   a research-only account, throttle aggressively (this module sleeps 4-8s
   between searches), and never use credentials of a personal account.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from typing import List
from urllib.parse import quote_plus

from . import Post

log = logging.getLogger("egx.v2.twitter_authed")

STORAGE_ENV = "EGX_X_STORAGE_STATE"

QUERIES_EN = [
    "$COMI Egypt", "$ETEL", "EGX30 buy", "EGX30 sell",
    "Orascom Egypt stock", "Egyptian stocks bullish",
]
QUERIES_AR = [
    "البورصة المصرية تجميع",
    "البورصة المصرية هيطلع",
    "البورصة المصرية هينزل",
    "اشتري سهم البورصة المصرية",
]


def _have_playwright() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


def scrape(max_per_query: int = 12, max_total: int = 80) -> List[Post]:
    storage = os.environ.get(STORAGE_ENV)
    if not storage:
        log.info("Twitter authed scraper SKIPPED — set %s to a Playwright "
                 "storage_state.json to enable.", STORAGE_ENV)
        return []
    if not os.path.exists(storage):
        log.warning("Twitter authed scraper SKIPPED — %s=%s not found",
                    STORAGE_ENV, storage)
        return []
    if not _have_playwright():
        log.warning("Twitter authed scraper SKIPPED — `pip install playwright` "
                    "and run `python -m playwright install chromium`.")
        return []

    from playwright.sync_api import sync_playwright

    queries = QUERIES_EN + QUERIES_AR
    out: List[Post] = []
    seen_urls: set = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            storage_state=storage,
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"),
            locale="en-US",
        )
        page = ctx.new_page()

        for q in queries:
            if len(out) >= max_total:
                break
            url = (f"https://x.com/search?q={quote_plus(q)}"
                   "&src=typed_query&f=live")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                # wait for at least one tweet to render
                page.wait_for_selector("article", timeout=10_000)
            except Exception as e:
                log.warning("x.com search %r failed: %s", q, e)
                time.sleep(3.0)
                continue

            # progressive scroll to load more tweets
            for _ in range(3):
                page.mouse.wheel(0, 4000)
                time.sleep(1.2)

            articles = page.query_selector_all("article")
            added = 0
            for art in articles:
                if added >= max_per_query or len(out) >= max_total:
                    break
                try:
                    text_el = art.query_selector('div[lang]')
                    text = text_el.inner_text() if text_el else ""
                    if len(text) < 5:
                        continue
                    link_el = art.query_selector('a[href*="/status/"]')
                    href = link_el.get_attribute("href") if link_el else ""
                    if not href:
                        continue
                    canonical = (href if href.startswith("http")
                                 else f"https://x.com{href}")
                    if canonical in seen_urls:
                        continue
                    seen_urls.add(canonical)
                    m = re.match(r"https?://(?:x|twitter)\.com/([^/]+)/status/", canonical)
                    user = m.group(1) if m else "unknown"
                    out.append(Post(
                        text=text[:1500],
                        username=user,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        url=canonical,
                        platform="twitter",
                        source="twitter:authed",
                        engagement=0,
                    ))
                    added += 1
                except Exception as e:
                    log.debug("tweet parse failed: %s", e)
            log.info("x.com q=%r -> +%d", q, added)
            time.sleep(random.uniform(4.0, 8.0))

        browser.close()

    log.info("Twitter authed total: %d", len(out))
    return out


# ---- one-time login helper (run as: python -m ... --login) ----------------
def _interactive_login() -> int:
    if not _have_playwright():
        print("Install: pip install playwright && python -m playwright install chromium")
        return 2
    from playwright.sync_api import sync_playwright
    out = os.environ.get(STORAGE_ENV) or "x_storage_state.json"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto("https://x.com/login")
        print(f"Log in manually in the opened browser. When the home feed "
              f"loads, press ENTER here to save state to {out}.")
        input()
        ctx.storage_state(path=out)
        print(f"Saved storage state to {out}. Set {STORAGE_ENV}={out}")
        browser.close()
    return 0


if __name__ == "__main__":
    if "--login" in sys.argv:
        sys.exit(_interactive_login())
    posts = scrape()
    print(f"Got {len(posts)} tweets")
