"""Facebook Groups via Apify actor `apify/facebook-groups-scraper`
(actor id 2chN8UQcH1CfxLRNE).

Rate-limit defense:
   * Per-group results cached in social_v2.cache (1h TTL).
   * On HTTP 429 / 402 / 5xx, falls back to the most-recent cached payload
     for that group (even if expired) and logs a warning. This is the
     production fallback so transient Apify quota exhaustion does not zero
     out the sentiment signal.

Setup:
   1. APIFY_API_TOKEN must be in `.env` (loaded via dotenv).
   2. Override the group list with EGX_FB_GROUP_URLS (comma-separated).

Filter contract:
   * <= 150 words
   * must contain a trading-signal keyword (AR + EN)
   * not flagged as sponsored
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import List

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

from tradingagents.utils.text_preprocessor import normalize_text

from .. import cache as v2_cache
from ..models import Post

log = logging.getLogger("tradingagents.social_v2.facebook_apify")

ACTOR_ID = "2chN8UQcH1CfxLRNE"
APIFY_RUN_URL = (
    f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"
)

# User-confirmed EGX Arabic Facebook groups (2026-05).
DEFAULT_GROUP_URLS = [
    "https://www.facebook.com/groups/618025406208276/",   # جروب الخبره
    "https://www.facebook.com/groups/4021602644518797",   # البورصة المصرية
    "https://www.facebook.com/groups/955090341273238/",   # بورصة مصر — Egypt Stock Exchange
    "https://www.facebook.com/groups/1445825095646831/",  # اسهم عليها العين
    "https://www.facebook.com/groups/428469887311443",    # بورصة الغد
]

SIGNAL_RX = re.compile(
    r"(buy|sell|bullish|bearish|target|breakout|support|resistance|analysis|"
    r"سهم|شراء|بيع|تجميع|تصريف|هيطلع|هينزل|تحليل|اختراق|دعم|مقاومة|هدف)",
    re.IGNORECASE,
)

MAX_WORDS = 150
# Apify HTTP codes that warrant cache fallback (quota / rate / 5xx).
_FALLBACK_HTTP_CODES = {402, 403, 408, 409, 429, 500, 502, 503, 504}

# Post-age filter: drop posts older than this many days. Apify returns the
# newest N posts per group regardless of age — without this filter, a quiet
# group could feed us week-old posts that no longer reflect current sentiment.
# Override with env EGX_FB_MAX_POST_AGE_DAYS.
DEFAULT_MAX_POST_AGE_DAYS = 3


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


def _parse_post_dt(ts_str: str):
    """Parse Apify timestamp strings (ISO 8601, unix int, or RFC variants)."""
    if not ts_str:
        return None
    s = str(ts_str).strip()
    try:
        if s.isdigit():
            return datetime.fromtimestamp(int(s), tz=timezone.utc)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _too_old(ts_str: str, max_age_days: int) -> bool:
    dt = _parse_post_dt(ts_str)
    if dt is None:
        # Can't determine age — keep it (don't silently drop on parse failure).
        return False
    age = datetime.now(timezone.utc) - dt
    return age.total_seconds() > max_age_days * 86400


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


def _cache_key(group_url: str, results_per_group: int) -> str:
    # Intentionally drop results_per_group so a cached payload from a
    # smaller-batch run can serve a larger-batch caller (and vice versa).
    # The cached value just has more or fewer posts than requested; both
    # are valid and downstream dedup handles the rest. Keying on the size
    # used to cause cache misses across the demo/agent boundary.
    return f"fb:{group_url}"


def _fetch_group_items(
    group_url: str,
    token: str,
    results_per_group: int,
    timeout: int,
) -> tuple[List[dict], str]:
    """Return (items, source_tag) where source_tag is 'fresh', 'cached', or 'failed'."""
    cache_key = _cache_key(group_url, results_per_group)

    payload = {
        "startUrls": [{"url": group_url}],
        "resultsLimit": results_per_group,
        "sort": "newest",
    }
    try:
        response = requests.post(
            APIFY_RUN_URL,
            params={"token": token},
            json=payload,
            timeout=timeout,
        )
    except Exception as exc:
        log.warning("Apify network error for %s: %s — trying cache fallback", group_url, exc)
        cached = v2_cache.apify_get(cache_key)
        return (cached, "cached") if cached else ([], "failed")

    if response.status_code in _FALLBACK_HTTP_CODES:
        log.warning(
            "Apify HTTP %s for %s (likely quota/rate) — trying cache fallback",
            response.status_code,
            group_url,
        )
        cached = v2_cache.apify_get(cache_key)
        return (cached, "cached") if cached else ([], "failed")

    if response.status_code >= 300:
        log.error(
            "Apify HTTP %s for %s: %s",
            response.status_code,
            group_url,
            response.text[:300],
        )
        return [], "failed"

    try:
        items = response.json()
    except Exception as exc:
        log.error("Apify response not JSON for %s: %s", group_url, exc)
        return [], "failed"

    if not isinstance(items, list):
        log.error("Apify returned non-list payload for %s", group_url)
        return [], "failed"

    for item in items:
        if isinstance(item, dict) and not item.get("inputUrl"):
            item["inputUrl"] = group_url

    if items:
        v2_cache.apify_set(cache_key, items)
    return items, "fresh"


def scrape(results_per_group: int = 200, timeout: int = 300) -> List[Post]:
    token = os.getenv("APIFY_API_TOKEN")
    if not token:
        log.warning("Facebook (Apify) SKIPPED — APIFY_API_TOKEN not set in env")
        return []

    raw_urls = os.getenv("EGX_FB_GROUP_URLS")
    urls = (
        [url.strip() for url in raw_urls.split(",") if url.strip()]
        if raw_urls
        else DEFAULT_GROUP_URLS
    )

    all_items: List[dict] = []
    source_tags: dict[str, int] = {"fresh": 0, "cached": 0, "failed": 0}

    log.info(
        "Apify FB: fetching %d groups at %d posts/group",
        len(urls),
        results_per_group,
    )
    for group_url in urls:
        items, tag = _fetch_group_items(group_url, token, results_per_group, timeout)
        source_tags[tag] = source_tags.get(tag, 0) + 1
        if items:
            all_items.extend(items)
            log.info("Apify FB group %s -> %d items (%s)", group_url, len(items), tag)

    log.info(
        "Apify FB raw items: %d (groups fresh=%d cached=%d failed=%d)",
        len(all_items),
        source_tags["fresh"],
        source_tags["cached"],
        source_tags["failed"],
    )

    deduped_items: List[dict] = []
    seen_keys: set[str] = set()
    duplicate_items = 0
    for item in all_items:
        if not isinstance(item, dict) or item.get("error"):
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

    max_age_days = int(
        os.getenv("EGX_FB_MAX_POST_AGE_DAYS", str(DEFAULT_MAX_POST_AGE_DAYS))
    )

    out: List[Post] = []
    n_empty = n_ad = n_long = n_no_signal = n_too_old = 0

    for item in deduped_items:
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
        if _too_old(_ts(item), max_age_days):
            n_too_old += 1
            continue

        post_url = _post_url(item)
        group = _group_title(item) or "facebook-group"
        post_id = (
            item.get("postId") or item.get("legacyId") or item.get("feedbackId")
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
        "Apify FB kept %d / %d after filters "
        "(dedup=%d empty=%d ad=%d long=%d no-kw=%d too-old>%dd=%d)",
        len(out),
        len(all_items),
        duplicate_items,
        n_empty,
        n_ad,
        n_long,
        n_no_signal,
        max_age_days,
        n_too_old,
    )
    return out
