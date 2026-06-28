"""
Local EGX OHLCV provider — last-resort offline fallback.
=========================================================
Reads the pre-built per-ticker daily OHLCV CSVs in ``data/egx30_ohlcv/`` (and
the legacy ``data/egx_ohlcv/``) and returns the SAME dict contract as
``y_finance.get_YFin_data_online`` so it can slot straight into the
``DataGateway`` provider chain as the final fallback.

Why this exists:
    yfinance is rate-limited and intermittently (or permanently, for a few
    thin names) empty for EGX tickers, and the EODHD live fallback needs an API
    key and network. When BOTH live sources fail, a backtest would otherwise
    skip the date and report an all-HOLD / all-zero run with blank reasoning.
    These CSVs are current (refreshed through mid-2026) and cover the full
    EGX-30 universe, so they keep the pipeline producing real decisions offline.

Look-ahead safety:
    The date window is filtered ``start_date <= d < end_date`` (END-EXCLUSIVE),
    matching ``yfinance.Ticker.history(start, end)`` exactly. This guarantees a
    backtest evaluating date ``D`` never sees ``D``'s own bar regardless of
    which provider served the request — no provider-dependent drift, no
    accidental look-ahead.
"""

import csv as _csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .symbol_utils import normalize_egx_ticker

logger = logging.getLogger("tradingagents.dataflows.local_ohlcv")

# Default liquidity threshold for EGX (mirror y_finance / eodhd)
DEFAULT_LOW_LIQUIDITY_THRESHOLD = 50000  # shares/day

# Project root = three parents up from this file
# (tradingagents/dataflows/local_ohlcv.py -> repo root)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_OHLCV_DIRS = [
    _REPO_ROOT / "data" / "egx30_ohlcv",
    _REPO_ROOT / "data" / "egx_ohlcv",
]


def _resolve_csv_path(symbol_upper: str) -> Optional[Path]:
    """Return the first existing CSV path for ``symbol_upper`` (e.g. COMI.CA)."""
    for d in _OHLCV_DIRS:
        p = d / f"{symbol_upper}.csv"
        if p.exists():
            return p
    return None


def has_local_ohlcv(symbol: str) -> bool:
    """True if a local CSV exists for this EGX symbol."""
    return _resolve_csv_path(normalize_egx_ticker(symbol)) is not None


def get_local_ohlcv_data(
    symbol: str,
    start_date: str,
    end_date: str,
    liquidity_threshold: int = DEFAULT_LOW_LIQUIDITY_THRESHOLD,
    max_records: Optional[int] = 20,
) -> Dict[str, Any]:
    """Read daily OHLCV for an EGX ticker from the local CSV cache.

    Returns the same dict shape as ``get_YFin_data_online`` so the gateway and
    other callers can treat it identically. Never raises — returns a result
    with empty ``data`` and an ``error`` field on any failure.

    Args:
        symbol: EGX ticker (``COMI`` or ``COMI.CA`` — normalized internally).
        start_date / end_date: ``YYYY-MM-DD``. Window is start-inclusive,
            END-EXCLUSIVE (matches yfinance; see module docstring).
        max_records: Cap to the last N bars (token-budget guard, parity with
            the yfinance provider). ``None`` returns the full window.
    """
    symbol_upper = normalize_egx_ticker(symbol)

    # Validate date format up front (consistent error contract with siblings)
    try:
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")
    except (ValueError, TypeError) as e:
        return {"symbol": symbol_upper, "error": f"Invalid date format: {e}. Use YYYY-MM-DD.", "data": []}

    path = _resolve_csv_path(symbol_upper)
    if path is None:
        return {
            "symbol": symbol_upper,
            "data": [],
            "error": f"No local OHLCV CSV for '{symbol_upper}' (looked in data/egx30_ohlcv, data/egx_ohlcv)",
        }

    errors: List[str] = []
    records: List[Dict[str, Any]] = []
    volume_missing_count = 0
    phantom_dropped = 0
    total_volume = 0

    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = _csv.DictReader(f)
            for row in reader:
                d = (row.get("date") or "").strip()
                if not d:
                    continue
                # Window filter: start-inclusive, END-EXCLUSIVE (yfinance parity).
                # ISO yyyy-mm-dd strings compare correctly lexicographically.
                if d < start_date or d >= end_date:
                    continue
                try:
                    o = round(float(row["open"]), 2)
                    h = round(float(row["high"]), 2)
                    l = round(float(row["low"]), 2)
                    c = round(float(row["close"]), 2)
                    v = int(float(row.get("volume") or 0))
                except (KeyError, ValueError, TypeError):
                    continue

                # Drop phantom forward-filled bars (flat OHLC + zero volume) — not
                # real sessions. Same rule as the yfinance provider.
                if v == 0 and o == h == l == c:
                    phantom_dropped += 1
                    continue
                if v == 0:
                    volume_missing_count += 1

                total_volume += v
                records.append({"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v})
    except Exception as e:
        logger.warning("Local OHLCV read failed for %s (%s): %s", symbol_upper, path, e)
        return {"symbol": symbol_upper, "data": [], "error": f"Local CSV read failed: {e}"}

    # CSVs may already be sorted, but don't assume — sort ascending by date.
    records.sort(key=lambda r: r["date"])

    if not records:
        return {
            "symbol": symbol_upper,
            "start_date": start_date,
            "end_date": end_date,
            "total_records": 0,
            "data": [],
            "error": f"No local rows for '{symbol_upper}' in window {start_date} → {end_date}",
            "low_liquidity": True,
            "volume_missing": True,
            "avg_daily_volume": 0,
            "source": "local_csv",
        }

    if max_records is not None and len(records) > max_records:
        records = records[-max_records:]
        errors.append(f"Result truncated to last {max_records} records to prevent context overflow.")

    num_records = len(records)
    avg_daily_volume = total_volume / num_records if num_records > 0 else 0
    low_liquidity = avg_daily_volume < liquidity_threshold
    volume_missing = volume_missing_count > 0

    if phantom_dropped:
        errors.append(f"DROPPED {phantom_dropped} phantom forward-filled bar(s) (flat OHLC, zero volume)")

    return {
        "symbol": symbol_upper,
        "market": "EGX",
        "currency": "EGP",
        "start_date": start_date,
        "end_date": end_date,
        "total_records": num_records,
        "retrieved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data": records,
        "avg_daily_volume": round(avg_daily_volume, 2),
        "low_liquidity": low_liquidity,
        "liquidity_threshold": liquidity_threshold,
        "volume_missing": volume_missing,
        "volume_missing_count": volume_missing_count,
        "phantom_dropped": phantom_dropped,
        # Delayed/offline data — mark provenance so the audit trail is honest.
        "source": "local_csv (delayed)",
        "errors": errors if errors else None,
    }
