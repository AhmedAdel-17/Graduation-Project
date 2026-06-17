"""Facebook EGX posts via Google dorking — the Apify-fallback social source.

WHY THIS EXISTS
   ``facebook_apify`` is the PRIMARY retail-social source, but it burns a
   paid Apify quota. When that quota is exhausted (HTTP 402/429) and even
   the stale-cache fallback in ``facebook_apify`` is cold, the Facebook
   sentiment layer goes dark. This module is the next line of defense: it
   recovers PUBLIC Facebook group posts that search engines have already
   indexed, via ``site:facebook.com`` dork queries — no Facebook
   credentials, no Apify quota. The pipeline fires it only when Apify came
   back thin (see ``pipeline.stage_scrape``).

TRANSPORT TIERS (auto-selected by which env keys are present, tried in order;
the first tier that returns posts wins, so paid quota is only spent when the
free tier above it yields nothing)

   1. Google Custom Search JSON API   (GOOGLE_CSE_KEY + GOOGLE_CSE_CX)
        The official Google "dorking" endpoint — a Programmable Search
        Engine configured to *search the entire web*. Clean JSON
        (title/snippet/link), honors ``site:`` operators, FREE 100
        queries/day. RECOMMENDED primary. Set up at
        https://programmablesearchengine.google.com (create an engine,
        toggle "Search the entire web", copy the Search engine ID = cx;
        get the key from https://developers.google.com/custom-search/v1/introduction).

   2. Serper.dev SERP API             (SERPER_API_KEY)
        Paid Google-SERP aggregator (2,500 free credits to start). Higher
        volume than CSE; only burns credits when this fallback actually
        fires AND tier 1 returned nothing.

   3. Free HTML best-effort           (always available, no key)
        Rotating-UA scrape of DuckDuckGo / Bing HTML. Frequently
        challenge-blocked (HTTP 202) and low FB yield — a COURTESY tier
        that MAY RETURN ZERO. Honest by design, like the WEAK
        ``telegram_public`` source.

HONEST LIMITATIONS
   * Search snippets are title + ~160 chars, NOT full post text.
   * Timestamps are usually absent → age filtering is best-effort.
   * Facebook deindexes much group content → expect tens, not hundreds.
   This is a signal-PRESERVING fallback, not an Apify replacement.

FILTER CONTRACT (mirrors ``facebook_apify``)
   * <= MAX_WORDS words
   * must contain a trading-signal keyword (AR + EN)
   * best-effort recency drop (FACEBOOK_DORK_MAX_POST_AGE_DAYS)

OUTPUT
   ``Post(platform="facebook", source="facebook_dork:<provider>", engagement=0)``
   — platform="facebook" routes it through the pipeline's LIGHT_GUARDED
   EGX guard exactly like the Apify posts.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple
from urllib.parse import quote_plus

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

from .. import cache as v2_cache
from ..models import Post

# Reuse the Apify source's filter contract + curated group list so the two
# Facebook sources stay in lockstep (one source of truth for "what counts as
# a tradeable EGX FB post").
from .facebook_apify import (
    DEFAULT_GROUP_URLS,
    MAX_WORDS,
    SIGNAL_RX,
    _parse_post_dt,
    _word_count,
)

log = logging.getLogger("tradingagents.social_v2.facebook_dork")

# Trading-signal keyword axes appended to each per-group dork to bias the
# index toward tradeable chatter (vs. group chit-chat). AR + EN.
_DORK_KEYWORDS = '(سهم OR شراء OR بيع OR هدف OR تحليل OR توصية OR stock OR buy OR sell OR target)'

# Broad dorks across all public EGX groups (not just our 5), to widen the net
# when the curated groups are sparsely indexed.
_BROAD_DORKS: List[Tuple[str, str]] = [
    ("broad_market", 'site:facebook.com/groups "البورصة المصرية" سهم'),
    ("broad_stocks", 'site:facebook.com/groups أسهم مصر (شراء OR بيع OR توصية)'),
]

DEFAULT_MAX_AGE_DAYS = 7  # looser than Apify's 3d — dork timestamps are flaky
MIN_TEXT_CHARS = 40
MAX_RESULTS_PER_QUERY = 10  # CSE hard-caps a page at 10
PER_QUERY_SLEEP = 0.5
HTTP_TIMEOUT = 20
# Overall cap so a misconfigured run can't hammer a paid provider.
MAX_QUERIES_PER_RUN = 12

# Cache dork results (per query) in the existing Apify cache region (generic
# list cache, 60-min TTL) so repeated agent invocations for the same ticker
# within the hour don't re-burn the scarce CSE/Serper quota.
_CACHE_PREFIX = "dork:"

_GROUP_ID_RX = re.compile(r"/groups/(\d+)")
_FB_GROUP_POST_RX = re.compile(r"facebook\.com/groups/\d+")
_HTML_TAG_RX = re.compile(r"<[^>]+>")

_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


@dataclass
class _Hit:
    """One search-result row, provider-agnostic."""

    title: str
    snippet: str
    url: str
    ts: Optional[str] = None  # ISO 8601 if the provider exposed a date
    provider: Optional[str] = None  # which transport tier surfaced it


# --------------------------------------------------------------------------- #
# Query construction
# --------------------------------------------------------------------------- #
def _group_ids() -> List[str]:
    """EGX group IDs, honoring the EGX_FB_GROUP_URLS override used by Apify."""
    raw = os.getenv("EGX_FB_GROUP_URLS")
    urls = (
        [u.strip() for u in raw.split(",") if u.strip()]
        if raw
        else DEFAULT_GROUP_URLS
    )
    ids: List[str] = []
    for url in urls:
        m = _GROUP_ID_RX.search(url)
        if m:
            ids.append(m.group(1))
    return ids


def _build_queries() -> List[Tuple[str, str]]:
    """(label, dork) pairs: precise per-group dorks first, then broad dorks."""
    queries: List[Tuple[str, str]] = []
    for gid in _group_ids():
        queries.append(
            (f"group_{gid}", f"site:facebook.com/groups/{gid} {_DORK_KEYWORDS}")
        )
    queries.extend(_BROAD_DORKS)
    return queries[:MAX_QUERIES_PER_RUN]


# --------------------------------------------------------------------------- #
# Transport tiers — each returns List[_Hit] (possibly empty)
# --------------------------------------------------------------------------- #
def _strip_site_operator(query: str) -> str:
    """Remove `site:...` tokens from a dork query before sending to CSE.

    The CSE engine is configured with explicit site restrictions (the 5 EGX
    Facebook groups), so Google deprecated the "Search the entire web" toggle
    and the engine already scopes results to those groups. Passing a `site:`
    operator ON TOP of that restriction is redundant and may suppress results.
    The other transport tiers (Serper, HTML) are true web searches and keep
    the site: operator — it's the correct way to dork there.
    """
    return re.sub(r"site:\S+\s*", "", query).strip()


def _provider_google_cse(query: str, timeout: int) -> List[_Hit]:
    key = os.getenv("GOOGLE_CSE_KEY")
    cx = os.getenv("GOOGLE_CSE_CX")
    if not (key and cx):
        return []
    # CSE sites are already restricted to the configured EGX FB groups.
    cse_query = _strip_site_operator(query)
    if not cse_query:
        cse_query = query  # safety: don't send an empty query
    url = (
        "https://www.googleapis.com/customsearch/v1"
        f"?key={key}&cx={cx}&q={quote_plus(cse_query)}"
        f"&num={MAX_RESULTS_PER_QUERY}&lr=lang_ar&gl=eg"
    )
    try:
        resp = requests.get(url, timeout=timeout)
    except Exception as exc:
        log.warning("CSE network error for %r: %s", query, exc)
        return []
    if resp.status_code == 429:
        log.warning("CSE quota exhausted (HTTP 429) for %r", query)
        return []
    if resp.status_code >= 400:
        log.warning("CSE HTTP %s for %r: %s", resp.status_code, query, resp.text[:200])
        return []
    try:
        items = resp.json().get("items", []) or []
    except Exception as exc:
        log.warning("CSE response not JSON for %r: %s", query, exc)
        return []
    hits: List[_Hit] = []
    for it in items:
        hits.append(
            _Hit(
                title=it.get("title", "") or "",
                snippet=it.get("snippet", "") or "",
                url=(it.get("link", "") or "").strip(),
                ts=_cse_published(it),
            )
        )
    return hits


def _cse_published(item: dict) -> Optional[str]:
    """Best-effort publish date from CSE pagemap metatags."""
    pagemap = item.get("pagemap") or {}
    for tag in pagemap.get("metatags", []) or []:
        for key in ("article:published_time", "og:updated_time", "datePublished"):
            if tag.get(key):
                return str(tag[key])
    return None


def _provider_serper(query: str, timeout: int) -> List[_Hit]:
    key = os.getenv("SERPER_API_KEY")
    if not key:
        return []
    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json={"q": query, "gl": "eg", "hl": "ar", "num": MAX_RESULTS_PER_QUERY},
            timeout=timeout,
        )
    except Exception as exc:
        log.warning("Serper network error for %r: %s", query, exc)
        return []
    if resp.status_code >= 400:
        log.warning("Serper HTTP %s for %r: %s", resp.status_code, query, resp.text[:200])
        return []
    try:
        organic = resp.json().get("organic", []) or []
    except Exception as exc:
        log.warning("Serper response not JSON for %r: %s", query, exc)
        return []
    hits: List[_Hit] = []
    for it in organic:
        hits.append(
            _Hit(
                title=it.get("title", "") or "",
                snippet=it.get("snippet", "") or "",
                url=(it.get("link", "") or "").strip(),
                ts=it.get("date"),
            )
        )
    return hits


def _provider_html(query: str, timeout: int) -> List[_Hit]:
    """Free, no-auth, BEST-EFFORT scrape. May legitimately return []."""
    ua = _UA_POOL[int(time.time()) % len(_UA_POOL)]
    headers = {"User-Agent": ua, "Accept-Language": "ar-EG,ar;q=0.9,en;q=0.8"}
    endpoints = [
        ("ddg", "https://html.duckduckgo.com/html/?q=" + quote_plus(query)),
        ("bing", "https://www.bing.com/search?q=" + quote_plus(query) + "&setlang=ar&cc=EG"),
    ]
    for name, url in endpoints:
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
        except Exception as exc:
            log.debug("HTML %s network error for %r: %s", name, query, exc)
            continue
        if resp.status_code != 200:
            log.debug("HTML %s HTTP %s for %r (likely challenge)", name, resp.status_code, query)
            continue
        hits = _parse_html_results(resp.text)
        if hits:
            log.info("HTML dork via %s -> %d FB hits for %r", name, len(hits), query)
            return hits
    return []


def _parse_html_results(body: str) -> List[_Hit]:
    """Pull facebook.com/groups result links out of a SERP HTML page.

    Deliberately conservative: we only keep anchors that point at a FB group
    URL and use the anchor text as the title. Snippets are not reliably
    recoverable from obfuscated SERP HTML, so the title carries the signal.
    """
    hits: List[_Hit] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<a[^>]+href="(?P<href>https?://[^"]*facebook\.com/groups/[^"]+)"[^>]*>(?P<txt>.*?)</a>',
        body,
        re.IGNORECASE | re.DOTALL,
    ):
        href = m.group("href")
        if href in seen:
            continue
        seen.add(href)
        title = _strip_html(m.group("txt"))
        if len(title) < 8:
            continue
        hits.append(_Hit(title=title, snippet="", url=href, ts=None))
    return hits


def _strip_html(value: str) -> str:
    if not value:
        return ""
    text = _HTML_TAG_RX.sub(" ", value)
    text = (
        text.replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    return re.sub(r"\s+", " ", text).strip()


def _select_providers() -> List[Tuple[str, Callable[[str, int], List[_Hit]]]]:
    """Ordered provider chain, filtered to what's configured this run."""
    chain: List[Tuple[str, Callable[[str, int], List[_Hit]]]] = []
    if os.getenv("GOOGLE_CSE_KEY") and os.getenv("GOOGLE_CSE_CX"):
        chain.append(("google_cse", _provider_google_cse))
    if os.getenv("SERPER_API_KEY"):
        chain.append(("serper", _provider_serper))
    if os.getenv("FACEBOOK_DORK_ALLOW_HTML", "1") != "0":
        chain.append(("html", _provider_html))
    return chain


# --------------------------------------------------------------------------- #
# Hit -> Post
# --------------------------------------------------------------------------- #
def _too_old(ts: Optional[str], max_age_days: int) -> bool:
    if not ts:
        return False  # unknown age — keep (don't drop on missing timestamp)
    dt = _parse_post_dt(ts)
    if dt is None:
        return False
    age = datetime.now(timezone.utc) - dt
    return age.total_seconds() > max_age_days * 86400


def _hit_to_post(hit: _Hit, provider: str, max_age_days: int) -> Optional[Post]:
    if not hit.url or not _FB_GROUP_POST_RX.search(hit.url):
        return None
    text = f"{_strip_html(hit.title)}\n{_strip_html(hit.snippet)}".strip()
    if len(text) < MIN_TEXT_CHARS:
        return None
    if _word_count(text) > MAX_WORDS:
        return None
    if not SIGNAL_RX.search(text):
        return None
    if _too_old(hit.ts, max_age_days):
        return None
    return Post(
        text=text[:1500],
        username=f"facebook-dork:{provider}",
        timestamp=hit.ts or datetime.now(timezone.utc).isoformat(),
        url=hit.url,
        platform="facebook",
        source=f"facebook_dork:{provider}",
        engagement=0,
    )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def scrape(timeout: int = HTTP_TIMEOUT) -> List[Post]:
    if os.getenv("ENABLE_FACEBOOK_DORK", "1") == "0":
        log.info("Facebook dork disabled via ENABLE_FACEBOOK_DORK=0")
        return []

    providers = _select_providers()
    if not providers:
        log.warning("Facebook dork SKIPPED — no provider configured (and HTML disabled)")
        return []

    queries = _build_queries()
    max_age_days = int(
        os.getenv("FACEBOOK_DORK_MAX_POST_AGE_DAYS", str(DEFAULT_MAX_AGE_DAYS))
    )

    log.info(
        "Facebook dork: %d queries across providers %s",
        len(queries),
        [name for name, _ in providers],
    )

    posts: List[Post] = []
    seen_urls: set[str] = set()
    n_dropped = 0

    for label, query in queries:
        hits = _gather_hits(query, providers, timeout)
        for hit in hits:
            if hit.url in seen_urls:
                continue
            post = _hit_to_post(hit, hit.provider or "dork", max_age_days)
            if post is None:
                n_dropped += 1
                continue
            seen_urls.add(hit.url)
            posts.append(post)
        time.sleep(PER_QUERY_SLEEP)

    log.info(
        "Facebook dork total: %d posts kept (%d hits dropped by filters)",
        len(posts),
        n_dropped,
    )
    return posts


def _gather_hits(
    query: str,
    providers: List[Tuple[str, Callable[[str, int], List[_Hit]]]],
    timeout: int,
) -> List[_Hit]:
    """Run the provider chain for one query, with a per-query result cache.

    The first provider that returns hits wins (so paid quota is only spent
    when the cheaper tier above it returned nothing).
    """
    cache_key = f"{_CACHE_PREFIX}{query}"
    cached = v2_cache.apify_get(cache_key)
    if cached:
        return [_Hit(**row) for row in cached]

    for name, fn in providers:
        hits = fn(query, timeout)
        if hits:
            for h in hits:
                h.provider = name
            v2_cache.apify_set(
                cache_key,
                [
                    {"title": h.title, "snippet": h.snippet, "url": h.url, "ts": h.ts, "provider": h.provider}
                    for h in hits
                ],
            )
            return hits
    return []
