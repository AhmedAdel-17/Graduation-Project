"""
Facebook EGX-trader-group scraper (Playwright + persisted session).

Why: Egyptian retail traders concentrate on Arabic Facebook groups for
trade calls. This is the single highest-signal source for EGX retail mood.

Setup (one-time):
    1.  pip install playwright
        python -m playwright install chromium
    2.  Either:
        a) Interactive login — saves storage_state.json:
           python -m scripts.twitter_pipeline.v2.sources.facebook_groups --login
        b) Or set EGX_FB_STORAGE_STATE to an existing Playwright storage state.
    3.  Optional: EGX_FB_GROUPS="group_id1,group_id2" overrides the seed list.

Run:
    Auto-invoked by pipeline_v2.  When credentials are missing the module
    logs a clear SKIPPED message and returns []; the pipeline keeps running.

Honesty:
    Facebook actively obstructs scraping. This module:
      - uses a real headless Chromium with persisted cookies (TOS grey area
        but not API abuse);
      - throttles 4-8s between groups;
      - uses Facebook's mbasic / m. mobile views first (lighter HTML, more
        scraper-friendly) and falls back to the desktop www view.
    Use a research-only account.

Group list:
    Public group IDs / handles vary; some require join. The seed list below
    is the brief's target set, expressed as the public URL slug or numeric
    ID where known. Edit/extend in TARGET_GROUPS or via env.
"""

from __future__ import annotations

import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from typing import List

from . import Post

log = logging.getLogger("egx.v2.facebook")

STORAGE_ENV = "EGX_FB_STORAGE_STATE"
GROUPS_ENV  = "EGX_FB_GROUPS"

# Default seeds — these match the brief. Slugs/IDs may need updating
# if Facebook renames or makes a group private.
TARGET_GROUPS = [
    "EGXInvestorsClub",
    "EgyptianStockMarketInvestors",
    "EGX30Traders",
    "TheEgyptianInvestors",
    # Arabic-named groups: often only resolvable by numeric ID.
    # Replace these placeholders with real IDs you have access to:
    "%D8%A7%D9%84%D8%A8%D9%88%D8%B1%D8%B5%D8%A9.%D8%A7%D9%84%D9%85%D8%B5%D8%B1%D9%8A%D8%A9.EGX",
    "EGXmasrya",
    "egypt.stock.analysis",
    "egx.traders.club",
]


def _have_playwright() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


def _is_blocked(text: str) -> bool:
    return ("log in to facebook" in text.lower()
            or "you must log in" in text.lower()
            or "checkpoint required" in text.lower())


def _scrape_group(page, group: str, max_posts: int) -> List[Post]:
    """Scrape one group by trying mobile then desktop."""
    out: List[Post] = []

    candidates = [
        f"https://m.facebook.com/groups/{group}",
        f"https://mbasic.facebook.com/groups/{group}",
        f"https://www.facebook.com/groups/{group}",
    ]
    for url in candidates:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        except Exception as e:
            log.warning("FB %s nav failed: %s", group, e)
            continue
        body_text = page.content()
        if _is_blocked(body_text):
            log.warning("FB %s blocked at %s — auth invalid?", group, url)
            continue

        # progressive scroll to load posts
        for _ in range(4):
            page.mouse.wheel(0, 4000)
            time.sleep(1.0)

        # Heuristic post selectors for both mobile and desktop variants
        selectors = [
            'div[role="article"]',                  # desktop
            'article',                              # mobile
            'div._5pcr',                            # mobile classic
            'div[data-ft]',                         # mbasic
        ]
        nodes = []
        for sel in selectors:
            try:
                nodes = page.query_selector_all(sel)
                if len(nodes) >= 3:
                    break
            except Exception:
                pass
        if not nodes:
            log.info("FB %s: no post nodes at %s", group, url)
            continue
        log.info("FB %s: %d candidate post nodes at %s",
                 group, len(nodes), url)

        for node in nodes[:max_posts * 2]:
            if len(out) >= max_posts:
                break
            try:
                text = (node.inner_text() or "").strip()
            except Exception:
                continue
            if len(text) < 10:
                continue
            # crude meta-strip: remove "See more"/"Like Comment Share" tails
            text = re.sub(r"\b(See more|Like\s+Comment\s+Share|"
                          r"شاهد المزيد|إعجاب\s+تعليق\s+مشاركة)\b.*$",
                          "", text, flags=re.IGNORECASE | re.DOTALL).strip()
            if len(text) < 10:
                continue
            # try to grab a permalink
            href = ""
            try:
                a = node.query_selector('a[href*="/groups/"]') or \
                    node.query_selector('a[href*="/permalink/"]') or \
                    node.query_selector('a[href*="/posts/"]')
                if a:
                    h = a.get_attribute("href") or ""
                    href = h if h.startswith("http") else f"https://facebook.com{h}"
            except Exception:
                pass
            # crude engagement: count digits next to "like"/"إعجاب"
            engagement = 0
            m = re.search(r"(\d[\d,\.]*)\s*(?:likes?|reactions?|إعجاب|تفاعل)",
                          text, re.IGNORECASE)
            if m:
                try:
                    engagement = int(m.group(1).replace(",", "").split(".")[0])
                except Exception:
                    engagement = 0

            out.append(Post(
                text=text[:1500],
                username=group,
                timestamp=datetime.now(timezone.utc).isoformat(),
                url=href or url,
                platform="facebook",
                source=f"facebook:{group}",
                engagement=engagement,
            ))
        if out:
            return out  # this URL worked, no need to try fallbacks
    return out


def scrape(max_per_group: int = 20, max_total: int = 200) -> List[Post]:
    storage = os.environ.get(STORAGE_ENV)
    if not storage:
        log.info("Facebook scraper SKIPPED — set %s to a Playwright "
                 "storage_state.json (run with --login to create one).",
                 STORAGE_ENV)
        return []
    if not os.path.exists(storage):
        log.warning("Facebook scraper SKIPPED — %s=%s not found",
                    STORAGE_ENV, storage)
        return []
    if not _have_playwright():
        log.warning("Facebook scraper SKIPPED — pip install playwright && "
                    "python -m playwright install chromium")
        return []

    groups = os.environ.get(GROUPS_ENV)
    group_list = [g.strip() for g in groups.split(",")] if groups else TARGET_GROUPS

    from playwright.sync_api import sync_playwright

    out: List[Post] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            storage_state=storage,
            user_agent=("Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"),
            viewport={"width": 412, "height": 915},
            locale="ar-EG",
        )
        page = ctx.new_page()

        for g in group_list:
            if len(out) >= max_total:
                break
            try:
                batch = _scrape_group(page, g, max_per_group)
                log.info("FB group %s -> +%d posts", g, len(batch))
                out.extend(batch)
            except Exception as e:
                log.exception("FB group %s failed: %s", g, e)
            time.sleep(random.uniform(4.0, 8.0))

        browser.close()

    # de-dup
    seen, dedup = set(), []
    for p in out:
        key = p.url + "|" + p.text[:60]
        if key in seen:
            continue
        seen.add(key)
        dedup.append(p)
    log.info("Facebook total: %d posts (after dedup: %d) from %d groups",
             len(out), len(dedup), len(group_list))
    return dedup


def _interactive_login() -> int:
    if not _have_playwright():
        print("Install: pip install playwright && python -m playwright install chromium")
        return 2
    from playwright.sync_api import sync_playwright
    out = os.environ.get(STORAGE_ENV) or "fb_storage_state.json"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(locale="ar-EG")
        page = ctx.new_page()
        page.goto("https://www.facebook.com/login")
        print(f"Log in to Facebook in the opened browser. When the home "
              f"feed loads, press ENTER here to save state to {out}.")
        input()
        ctx.storage_state(path=out)
        print(f"Saved storage state to {out}. Set {STORAGE_ENV}={out}")
        browser.close()
    return 0


if __name__ == "__main__":
    if "--login" in sys.argv:
        sys.exit(_interactive_login())
    posts = scrape()
    print(f"Got {len(posts)} Facebook posts")
