"""
Facebook Groups via Apify actor `apify/facebook-groups-scraper`
(actor id 2chN8UQcH1CfxLRNE).

Why Apify (not Playwright):
   * Facebook actively blocks headless browsers; the prior Playwright path
     needed a personal account, broke easily, and risked suspension.
   * Apify's hosted actor handles login + anti-bot for us; we just POST
     start-URLs and consume normalised JSON.

Setup:
   1. Put APIFY_API_TOKEN in .env at the repo root.
   2. (Optional) override the group list via env EGX_FB_GROUP_URLS - comma
      separated full https://www.facebook.com/groups/<slug-or-id> URLs.

Filter contract:
   * keep posts shorter than 150 words
   * keep ONLY posts that mention at least one trading-signal keyword
     (Arabic + English) - empty / ad / generic content rejected here
   * downstream pipeline_v2 stages still apply Layer-0 EGX gate, intent
     classifier, content-type classifier, and the production quality gate

Output: project-standard `Post` instances (platform="facebook"), so the
downstream entity / intent / sentiment chain consumes them unchanged.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests

# Load .env once, idempotently
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tradingagents.utils.text_preprocessor import normalize_text  # noqa: E402

from . import Post

log = logging.getLogger("egx.v2.facebook_apify")

ACTOR_ID = "2chN8UQcH1CfxLRNE"  # apify/facebook-groups-scraper
APIFY_RUN_URL = (
    f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"
)

# User-confirmed target groups (numeric IDs - verified accessible).
TARGET_GROUP_URLS = [
    "https://www.facebook.com/groups/618025406208276/",
    "https://www.facebook.com/groups/4021602644518797",
    "https://www.facebook.com/groups/955090341273238/",
]

# Trading-signal keyword filter from the brief (Arabic + English).
# A post must contain at least one of these to be kept by this source.
SIGNAL_RX = re.compile(
    r"(buy|sell|bullish|bearish|target|breakout|support|resistance|analysis|"
    r"سهم|شراء|بيع|تجميع|تصريف|هيطلع|هينزل|تحليل|اختراق|دعم|مقاومة|هدف)",
    re.IGNORECASE,
)

MAX_WORDS = 150


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def _normalize_fingerprint(text: str) -> str:
    return re.sub(r"\s+", " ", normalize_text(text or "")).strip().lower()


def _looks_like_ad(item: dict) -> bool:
    if item.get("isSponsored") or item.get("sponsored"):
        return True
    text = (item.get("text") or "").lower()
    return "sponsored" in text[:80]


def _ts(item: dict) -> str:
    for key in ("time", "publishedAt", "createdAt", "timestamp", "date"):
        value = item.get(key)
        if value:
            return str(value)
    return datetime.now(timezone.utc).isoformat()


def _engagement(item: dict) -> int:
    likes = (
        item.get("likesCount")
        or item.get("likes")
        or item.get("reactionsCount")
        or 0
    )
    comments = item.get("commentsCount") or item.get("comments") or 0
    try:
        return int(likes or 0) + int(comments or 0)
    except Exception:
        return 0


def _group_title(item: dict) -> str:
    return (
        item.get("groupTitle")
        or item.get("groupName")
        or (item.get("group") or {}).get("name")
        or item.get("facebookId")
        or item.get("inputUrl")
        or ""
    )


def _post_url(item: dict) -> str:
    return (
        item.get("facebookUrl")
        or item.get("topLevelUrl")
        or item.get("url")
        or item.get("postUrl")
        or item.get("link")
        or ""
    )


def _post_text(item: dict) -> str:
    """Pull post text, falling back to sharedPost.text for re-shares."""
    for key in ("text", "content", "postText", "message"):
        value = item.get(key)
        if value:
            return str(value).strip()
    shared = item.get("sharedPost")
    if isinstance(shared, dict):
        for key in ("text", "content", "postText", "message"):
            value = shared.get(key)
            if value:
                return str(value).strip()
    return ""


def _item_post_key(item: dict, text: str) -> str:
    for key in ("postId", "legacyId", "feedbackId"):
        value = item.get(key)
        if value:
            return f"id:{value}"

    group = _normalize_fingerprint(_group_title(item))
    timestamp = _ts(item)
    normalized_text = _normalize_fingerprint(text)
    payload = f"{group}|{timestamp}|{normalized_text}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return f"fp:{digest}"


def _fetch_group_items(
    group_url: str,
    token: str,
    results_per_group: int,
    timeout: int,
) -> List[dict]:
    payload = {
        "startUrls": [{"url": group_url}],
        "resultsLimit": results_per_group,
        "sort": "newest",
    }
    try:
        # One actor run per group keeps the result cap truly per-group instead
        # of relying on actor-level batching semantics.
        response = requests.post(
            APIFY_RUN_URL,
            params={"token": token},
            json=payload,
            timeout=timeout,
        )
    except Exception as exc:
        log.exception("Apify call failed for %s: %s", group_url, exc)
        return []

    if response.status_code >= 300:
        log.error(
            "Apify HTTP %s for %s: %s",
            response.status_code,
            group_url,
            response.text[:300],
        )
        return []

    try:
        items = response.json()
    except Exception as exc:
        log.error("Apify response not JSON for %s: %s", group_url, exc)
        return []

    if not isinstance(items, list):
        log.error("Apify returned non-list payload for %s", group_url)
        return []

    for item in items:
        if isinstance(item, dict) and not item.get("inputUrl"):
            item["inputUrl"] = group_url
    return items


def scrape(results_per_group: int = 300, timeout: int = 300) -> List[Post]:
    token = os.getenv("APIFY_API_TOKEN")
    if not token:
        log.warning("Facebook (Apify) SKIPPED - APIFY_API_TOKEN not set in env")
        return []

    raw_urls = os.getenv("EGX_FB_GROUP_URLS")
    urls = (
        [url.strip() for url in raw_urls.split(",") if url.strip()]
        if raw_urls
        else TARGET_GROUP_URLS
    )

    all_items: List[dict] = []
    dead_urls: List[str] = []
    live_urls: List[str] = []

    log.info(
        "Apify FB actor: fetching %d groups at %d posts/group",
        len(urls),
        results_per_group,
    )
    for group_url in urls:
        items = _fetch_group_items(group_url, token, results_per_group, timeout)
        if not items:
            dead_urls.append(group_url)
            continue
        errors = [
            item for item in items if isinstance(item, dict) and item.get("error")
        ]
        if errors and len(errors) == len(items):
            dead_urls.append(group_url)
            continue
        live_urls.append(group_url)
        all_items.extend(items)
        log.info(
            "Apify FB group %s -> %d raw items",
            group_url,
            len(items),
        )

    log.info("Apify returned %d raw items across %d groups", len(all_items), len(urls))
    if dead_urls:
        log.warning(
            "Apify: %d / %d start URLs not available (private/deleted/mistyped). "
            "Examples: %s",
            len(dead_urls),
            len(urls),
            dead_urls[:3],
        )
    log.info("Apify: live URLs returning posts: %d / %d", len(live_urls), len(urls))

    deduped_items: List[dict] = []
    seen_keys = set()
    duplicate_items = 0
    for item in all_items:
        if not isinstance(item, dict):
            continue
        if item.get("error"):
            continue
        text = _post_text(item)
        if not text:
            deduped_items.append(item)
            continue
        dedup_key = _item_post_key(item, text)
        if dedup_key in seen_keys:
            duplicate_items += 1
            continue
        seen_keys.add(dedup_key)
        deduped_items.append(item)

    out: List[Post] = []
    n_empty = n_ad = n_long = n_no_signal = n_error = 0

    for item in deduped_items:
        if not isinstance(item, dict):
            continue
        if item.get("error"):
            n_error += 1
            continue

        text = _post_text(item)
        if not text:
            n_empty += 1
            continue
        if _looks_like_ad(item):
            n_ad += 1
            continue
        if _word_count(text) > MAX_WORDS:
            n_long += 1
            continue
        if not SIGNAL_RX.search(text):
            n_no_signal += 1
            continue

        post_url = _post_url(item)
        group = _group_title(item) or "facebook-group"
        post_id = (
            item.get("postId")
            or item.get("legacyId")
            or item.get("feedbackId")
        )
        if (
            not post_url
            or ("/groups/" in post_url and "/posts/" not in post_url and "/permalink/" not in post_url)
        ):
            base = post_url or item.get("inputUrl") or "https://www.facebook.com/groups/"
            digest = hashlib.sha1(
                f"{_normalize_fingerprint(text[:500])}|{_ts(item)}".encode("utf-8")
            ).hexdigest()[:12]
            tag = post_id or digest
            post_url = f"{base.rstrip('/')}/#post-{tag}"

        out.append(
            Post(
                text=text[:1500],
                username=group,
                timestamp=_ts(item),
                url=post_url,
                platform="facebook",
                source=f"facebook-group:{group}",
                engagement=_engagement(item),
            )
        )

    log.info(
        "Facebook (Apify) kept %d / %d after source filters "
        "(dedup=%d, error=%d, empty=%d, ad=%d, long>%dw=%d, no-signal-kw=%d)",
        len(out),
        len(all_items),
        duplicate_items,
        n_error,
        n_empty,
        n_ad,
        MAX_WORDS,
        n_long,
        n_no_signal,
    )
    if not out and len(dead_urls) == len(urls):
        log.error(
            "Facebook (Apify): ALL %d start URLs were not_available - "
            "set EGX_FB_GROUP_URLS to public groups you have access to "
            "(or numeric group IDs).",
            len(urls),
        )
    return out


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    posts = scrape(results_per_group=50)
    print(f"Got {len(posts)} Facebook posts")
    for post in posts[:5]:
        print(f"[{post.platform}/{post.source}] eng={post.engagement}")
        print(f"  {post.text[:140]}")
        print(f"  {post.url}\n")
