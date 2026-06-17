"""Telegram public-preview source.

Scrapes the public web preview at ``https://t.me/s/<channel>`` for any
PUBLIC Telegram channel. No login, no Telegram API, no auth.

Trade-off:
   * The preview only shows the last ~20 messages of each channel.
   * Channels that have switched to private membership return 302 with no
     preview — we silently skip them.
   * For high-signal, high-volume EGX coverage you want Telethon (a real
     user account joined to the channels) — out of scope here.

Channel selection:
   Default list = a small starter set of public Egyptian financial channels
   that exist at the time of writing. Most active EGX trader channels are
   PRIVATE — set `EGX_TELEGRAM_CHANNELS` (comma-separated handles, no @) to
   override with channels you actually follow that have public previews.

Filter contract:
   * Drop empty messages and pure-link messages.
   * Drop messages older than MAX_AGE_DAYS (default 7 — news-grade content
     stays relevant longer than retail FB posts).
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List

import requests

from ..models import Post

log = logging.getLogger("tradingagents.social_v2.telegram_public")

DEFAULT_CHANNELS = [
    # Curated 2026-06 from a live public-preview probe — only channels that
    # (a) actually expose a t.me/s/ preview (HTTP 200) AND (b) carry EGX /
    # finance content. Channels that 302 (gone private) were dropped because
    # each one still costs an HTTP round-trip per run for nothing.
    # --- EGX / markets focus (highest signal) ---
    "EGX_30",                # EGX30 index channel
    "alborsanews",           # Al Borsa markets news
    "stock_egypt",           # Egyptian stocks aggregator
    "AlMalNews",             # Al Mal financial news
    "egystocks",             # Egyptian stocks aggregator
    "EgyptianStockExchange", # EGX broadcast
    "Egypt_Stocks",          # Egypt stocks
    "mainstocks",            # markets aggregator
    "egyptbusiness",         # Egypt Business news
    # --- General Egyptian news (macro / event layer signal) ---
    "ahram_news",            # Al-Ahram news
    # Override with EGX_TELEGRAM_CHANNELS to point at channels you follow that
    # have public previews. Private channels return 302 and are skipped.
]

# News-grade channels stay relevant longer than retail chatter, and the
# preview only holds ~20 messages, so a wider window keeps the signal alive on
# quieter days. Override with TELEGRAM_MAX_POST_AGE_DAYS.
DEFAULT_MAX_AGE_DAYS = 14
MIN_TEXT_CHARS = 20
HTTP_TIMEOUT = 15
PER_CHANNEL_SLEEP = 1.0

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0"
)

_TS_RX = re.compile(r'<time[^>]*datetime="([^"]+)"', re.IGNORECASE)
_MSG_RX = re.compile(
    r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_POST_ID_RX = re.compile(r'data-post="([^"]+)"')
_VIEWS_RX = re.compile(
    r'<span class="tgme_widget_message_views">([^<]+)</span>',
    re.IGNORECASE,
)
_LINK_ONLY = re.compile(r"^\s*https?://\S+\s*$", re.IGNORECASE)


def _strip_html(value: str) -> str:
    text = re.sub(r"<br[^>]*>", "\n", value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    return re.sub(r"[ \t]+", " ", text).strip()


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _views_to_int(views_str: str) -> int:
    s = (views_str or "").strip().upper().replace(",", "")
    if not s:
        return 0
    try:
        if s.endswith("K"):
            return int(float(s[:-1]) * 1_000)
        if s.endswith("M"):
            return int(float(s[:-1]) * 1_000_000)
        return int(float(s))
    except Exception:
        return 0


def _fetch_channel(channel: str, max_age_days: int) -> List[Post]:
    url = f"https://t.me/s/{channel}"
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=HTTP_TIMEOUT,
            allow_redirects=False,
        )
    except Exception as exc:
        log.warning("Telegram %s fetch failed: %s", channel, exc)
        return []

    if response.status_code != 200:
        log.info(
            "Telegram %s -> HTTP %s (likely private channel, skipping)",
            channel,
            response.status_code,
        )
        return []

    html = response.text or ""
    timestamps = _TS_RX.findall(html)
    messages_raw = _MSG_RX.findall(html)
    post_ids = _POST_ID_RX.findall(html)
    views = _VIEWS_RX.findall(html)

    # Pair messages with timestamps positionally — both lists ordered top→bottom.
    n = min(len(messages_raw), len(timestamps))
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    posts: List[Post] = []
    n_old = n_short = n_link = 0
    for i in range(n):
        text = _strip_html(messages_raw[i])
        if not text or len(text) < MIN_TEXT_CHARS:
            n_short += 1
            continue
        if _LINK_ONLY.match(text):
            n_link += 1
            continue
        dt = _parse_dt(timestamps[i]) or datetime.now(timezone.utc)
        if dt < cutoff:
            n_old += 1
            continue
        post_id = post_ids[i] if i < len(post_ids) else f"{channel}/{i}"
        post_url = f"https://t.me/{post_id}"
        engagement = _views_to_int(views[i]) if i < len(views) else 0
        posts.append(
            Post(
                text=text[:1500],
                username=channel,
                timestamp=dt.isoformat(),
                url=post_url,
                platform="telegram",
                source=f"telegram-public:{channel}",
                engagement=engagement,
            )
        )
    log.info(
        "Telegram %s -> %d kept (msgs=%d old>%dd=%d short=%d link-only=%d)",
        channel,
        len(posts),
        n,
        max_age_days,
        n_old,
        n_short,
        n_link,
    )
    return posts


def scrape() -> List[Post]:
    raw_channels = os.getenv("EGX_TELEGRAM_CHANNELS")
    channels = (
        [c.strip().lstrip("@") for c in raw_channels.split(",") if c.strip()]
        if raw_channels
        else list(DEFAULT_CHANNELS)
    )
    max_age_days = int(
        os.getenv("TELEGRAM_MAX_POST_AGE_DAYS", str(DEFAULT_MAX_AGE_DAYS))
    )

    out: List[Post] = []
    for ch in channels:
        out.extend(_fetch_channel(ch, max_age_days))
        time.sleep(PER_CHANNEL_SLEEP)

    log.info("Telegram total: %d posts", len(out))
    return out
