"""
FRED API Provider for Egypt Macro Data
=======================================
Lightweight wrapper using raw requests (no fredapi dependency).

FRED series used:
  - FPCPITOTLZGEGY: Egypt CPI (YoY % change) — ANNUAL frequency, World Bank
    methodology. NOT the same as CAPMAS monthly urban headline CPI used in the
    macro CSV. Do not use as a direct substitute.
  - INTGSTEGY91N:   DOES NOT EXIST on FRED (verified 2026-05-14). No Egypt
    T-bill series is currently available on FRED.

The generic fetch_fred_series() function works correctly and is kept for future
use if suitable monthly series are identified.

Requires FRED_API_KEY in .env (free at https://fred.stlouisfed.org/docs/api/).
Returns None gracefully if key is missing or API fails.
"""

import logging
import os
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger("tradingagents.dataflows.fred")

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# Egypt-specific FRED series IDs
EGYPT_CPI_SERIES = "FPCPITOTLZGEGY"       # CPI annual % change
EGYPT_TBILL_SERIES = "INTGSTEGY91N"        # 91-day T-bill rate


def fetch_fred_series(
    series_id: str,
    trade_date: str,
    scale: float = 1.0,
) -> Optional[float]:
    """Fetch the most recent observation for a FRED series on or before trade_date.

    Args:
        series_id: FRED series identifier
        trade_date: Upper date bound (YYYY-MM-DD)
        scale: Divisor to convert from percentage points to decimal
               (e.g. 100.0 to convert 25.8 -> 0.258)

    Returns:
        Most recent value as float, or None if unavailable.
    """
    api_key = os.getenv("FRED_API_KEY", "")
    if not api_key:
        logger.debug("FRED_API_KEY not set — skipping FRED lookup for %s", series_id)
        return None

    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_end": trade_date,
        # Vintage filter: only return data as it was known on trade_date.
        # Without this, FRED returns the latest-revised value, which can
        # include revisions published after trade_date — a data leakage
        # risk in backtests. realtime_start is set to the earliest possible
        # FRED date so we get the full observation history up to trade_date.
        "realtime_start": "1776-07-04",
        "realtime_end": trade_date,
        "sort_order": "desc",
        "limit": 1,
    }

    try:
        resp = requests.get(FRED_BASE_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        observations = data.get("observations", [])
        if not observations:
            logger.info("No FRED observations for %s before %s", series_id, trade_date)
            return None

        value_str = observations[0].get("value", "")
        if value_str in (".", "", None):
            logger.info("FRED returned missing value for %s", series_id)
            return None

        value = float(value_str)
        if scale != 1.0:
            value = value / scale
        return value

    except requests.RequestException as e:
        logger.warning("FRED API request failed for %s: %s", series_id, e)
        return None
    except (ValueError, KeyError) as e:
        logger.warning("FRED API parse error for %s: %s", series_id, e)
        return None


def fetch_egypt_cpi(trade_date: str) -> Optional[float]:
    """Fetch Egypt CPI YoY as decimal (e.g. 0.258 for 25.8%).

    FRED series FPCPITOTLZGEGY reports annual % change.
    """
    return fetch_fred_series(EGYPT_CPI_SERIES, trade_date, scale=100.0)


def fetch_egypt_tbill(trade_date: str) -> Optional[float]:
    """Fetch Egypt 91-day T-bill rate as decimal (e.g. 0.26 for 26%).

    FRED series INTGSTEGY91N reports rate in percentage points.
    """
    return fetch_fred_series(EGYPT_TBILL_SERIES, trade_date, scale=100.0)
