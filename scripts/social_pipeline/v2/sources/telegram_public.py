"""
Telegram public-channel scraper (no API, no login).

Why useful : EGX trader communities heavily use public Telegram channels for
              real-time signal calls in Arabic ("هيطلع", "تجميع", "هدف"). These
              are pure trader voice — exactly the OPINION-grade content the
              v2 pipeline needs.
Data quality: HIGH for signal extraction, but channels publish heterogeneous
              content (some are signal-only, others post analysis or news).
              The downstream content-type filter normalises this.
Method      : `https://t.me/s/<channel>` returns a static HTML preview of the
              latest ~20 messages, no auth required, no rate-limiting beyond
              standard polite throttling.
Limitations : Channels can be made private or removed at any time. The list
              below is seed-only — add more public EGX channel handles as you
              discover them. Channels with image-only posts produce empty text.

Honesty note: I cannot guarantee any specific channel handle is live at
              time-of-run, so the scraper degrades gracefully (logs misses,
              returns whatever it gets).
"""

from __future__ import annotations

import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import List

import requests
from bs4 import BeautifulSoup

from . import Post

log = logging.getLogger("egx.v2.telegram")

# Cheap pre-filter: drop messages that have ZERO EGX-related tokens before
# they reach Layer-0. This is just a precision optimisation — the strict
# classifier downstream still validates everything.
_QUICK_EGX_RX = re.compile(
    r"(EGX|COMI|ETEL|OCI|ORAS|TMG|SWDY|FWRY|ABUK|HRHO|EFG|"
    r"\.CA\b|"
    r"البورصة\s+المصرية|بورصة\s+مصر|"
    r"اوراسكوم|أوراسكوم|طلعت\s+مصطفى|التجاري\s+الدولي|"
    r"المصرية\s+للاتصالات|السويدي|فوري|هيرميس|أبو\s+قير|ابو\s+قير)",
    re.IGNORECASE,
)


def _is_egx_candidate(text: str) -> bool:
    return bool(_QUICK_EGX_RX.search(text or ""))

# Live EGX-discussion public channels. Channels that returned 0 messages
# in prior runs have been removed; add new ones here as you discover them.
SEED_CHANNELS = [
    "egx_news",          # confirmed live
    "egyptstockmarket",  # confirmed live
    "egypt_stocks",      # confirmed live
    # candidates worth retrying
    "egxtoday",
    "borsamasr",
    "alborsa_news",
]

# AGGRESSIVE noise filter — drop FX / gold / crypto / generic finance even
# if an EGX-keyword brushed past _is_egx_candidate. Telegram channels often
# repost the same blurb across topics.
_NOISE_RX = re.compile(
    r"\b(forex|fx\s+signal|xau|xag|gold\s+target|gold\s+signal|"
    r"bitcoin|btc|eth|crypto|nasdaq|s&p\s*500|dow\s+jones|"
    r"signal\s+#\d+|tp\d+\s*[:=]|sl\s*[:=])\b|"
    r"الذهب\s+(صعود|هبوط|توصية|هدف)|"
    r"عملات\s+رقمية|بيتكوين|إيثريوم|"
    r"يورو\s*دولار|EURUSD|GBPUSD|USDJPY",
    re.IGNORECASE,
)


def _is_noise(text: str) -> bool:
    return bool(_NOISE_RX.search(text or ""))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _fetch(handle: str) -> str | None:
    url = f"https://t.me/s/{handle}"
    for attempt in range(2):
        try:
            r = requests.get(url, headers={"User-Agent": UA,
                                            "Accept-Language": "ar,en;q=0.8"},
                             timeout=12)
            if r.status_code == 200 and len(r.text) > 500:
                return r.text
            log.debug("telegram %s HTTP %s", handle, r.status_code)
        except Exception as e:
            log.debug("telegram %s attempt %d: %s", handle, attempt + 1, e)
        time.sleep(1.0 + attempt)
    return None


def _parse(handle: str, html: str) -> List[Post]:
    soup = BeautifulSoup(html, "html.parser")
    out: List[Post] = []
    msgs = soup.select(".tgme_widget_message_wrap .tgme_widget_message")
    n_dropped = 0
    for m in msgs:
        text_div = m.select_one(".tgme_widget_message_text")
        if not text_div:
            continue
        text = text_div.get_text(" ", strip=True)
        if len(text) < 6:
            continue
        if not _is_egx_candidate(text):
            n_dropped += 1
            continue
        if _is_noise(text):
            n_dropped += 1
            continue
        link_a = m.select_one("a.tgme_widget_message_date")
        href = link_a.get("href", "") if link_a else ""
        time_tag = m.select_one("time.time")
        ts = (time_tag.get("datetime") if time_tag else None) or \
             datetime.now(timezone.utc).isoformat()
        # views as a soft engagement signal
        views_tag = m.select_one(".tgme_widget_message_views")
        views = 0
        if views_tag:
            v = views_tag.get_text(strip=True)
            mt = re.match(r"([\d.]+)\s*([KkMm]?)", v)
            if mt:
                base = float(mt.group(1))
                mult = {"k": 1_000, "K": 1_000, "m": 1_000_000, "M": 1_000_000}.get(
                    mt.group(2), 1)
                views = int(base * mult)
        out.append(Post(
            text=text[:1500],
            username=handle,
            timestamp=ts,
            url=href or f"https://t.me/{handle}",
            platform="telegram",
            source=f"telegram:{handle}",
            engagement=views,
        ))
    if n_dropped:
        log.debug("telegram %s: dropped %d msgs by EGX pre-filter", handle, n_dropped)
    return out


def scrape(handles: List[str] | None = None,
           max_total: int = 200) -> List[Post]:
    handles = handles or SEED_CHANNELS
    out: List[Post] = []
    live = 0
    for h in handles:
        if len(out) >= max_total:
            break
        html = _fetch(h)
        if not html:
            log.info("telegram %s: not reachable", h)
            continue
        # t.me returns 200 with a "channel doesn't exist" page; detect it
        if "tgme_widget_message_wrap" not in html:
            log.info("telegram %s: no messages (private/empty/dead)", h)
            continue
        batch = _parse(h, html)
        if batch:
            live += 1
            log.info("telegram %s: +%d msgs", h, len(batch))
            out.extend(batch)
        time.sleep(random.uniform(0.6, 1.2))
    log.info("Telegram total: %d msgs from %d live channels", len(out), live)
    return out
