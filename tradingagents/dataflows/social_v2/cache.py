"""Disk-backed result cache for the v2 pipeline.

Two distinct cache regions:

  * `apify_cache`  — per-group Facebook scrape results. TTL 60 min.
                      Used to ride out Apify rate-limit windows without
                      losing recent data.
  * `signal_cache` — full per-ticker aggregated signal. TTL 30 min.
                      Lets repeated agent invocations for the same ticker on
                      the same day reuse compute without re-running scraping
                      and sentiment inference.

If diskcache is unavailable the helpers degrade to a no-op (every get returns
None, every set is dropped) so the pipeline still runs.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger("tradingagents.social_v2.cache")

_DEFAULT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_cache"
)
_CACHE_DIR = os.environ.get("SOCIAL_V2_CACHE_DIR", _DEFAULT_DIR)

try:
    import diskcache  # type: ignore

    _APIFY_CACHE: Any = diskcache.Cache(os.path.join(_CACHE_DIR, "apify"))
    _SIGNAL_CACHE: Any = diskcache.Cache(os.path.join(_CACHE_DIR, "signal"))
    _AVAILABLE = True
except Exception as exc:  # pragma: no cover - import-time degradation
    logger.warning("diskcache unavailable, social_v2 caching disabled: %s", exc)
    _APIFY_CACHE = None
    _SIGNAL_CACHE = None
    _AVAILABLE = False

APIFY_TTL_SECONDS = 60 * 60
SIGNAL_TTL_SECONDS = 30 * 60


def is_available() -> bool:
    return _AVAILABLE


def apify_get(key: str) -> Optional[list]:
    if not _AVAILABLE:
        return None
    try:
        return _APIFY_CACHE.get(key)
    except Exception as exc:
        logger.debug("apify_get failed for %s: %s", key, exc)
        return None


def apify_set(key: str, value: list) -> None:
    if not _AVAILABLE or value is None:
        return
    try:
        _APIFY_CACHE.set(key, value, expire=APIFY_TTL_SECONDS)
    except Exception as exc:
        logger.debug("apify_set failed for %s: %s", key, exc)


def signal_get(key: str) -> Optional[dict]:
    if not _AVAILABLE:
        return None
    try:
        return _SIGNAL_CACHE.get(key)
    except Exception as exc:
        logger.debug("signal_get failed for %s: %s", key, exc)
        return None


def signal_set(key: str, value: dict) -> None:
    if not _AVAILABLE or value is None:
        return
    try:
        _SIGNAL_CACHE.set(key, value, expire=SIGNAL_TTL_SECONDS)
    except Exception as exc:
        logger.debug("signal_set failed for %s: %s", key, exc)
