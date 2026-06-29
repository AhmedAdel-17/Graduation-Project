"""
Data Freshness Module
=====================
Checks recency of every data source the pipeline depends on and returns
a per-source freshness report.  Market-closed awareness prevents false
"stale" alarms on weekends / holidays.

Statuses:
  Fresh                     — data is within its expected TTL
  Fresh (market closed)     — data is within TTL after extending for non-trading hours
  Stale                     — data exceeds its expected TTL
  Missing                   — no data found at all
  Available                 — data exists but freshness cannot be determined (e.g. memory)

Used by:
  - GET /api/data-freshness  (api_server.py)
  - Prometheus gauges        (observability/metrics.py)
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo

logger = logging.getLogger("tradingagents.data_freshness")

# ── Project root & data paths ─────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_CACHE = _PROJECT_ROOT / "tradingagents" / "dataflows" / "data_cache"
_FUNDAMENTALS_DIR = _DATA_CACHE / "egx_fundamentals"
_MACRO_DIR = _DATA_CACHE / "egx_macro"

# ── EGX market schedule ───────────────────────────────────────────────────
# EGX is open Sun–Thu, 10:00–14:30 Cairo time.
# Friday & Saturday are the weekend.
_EGX_TZ = ZoneInfo("Africa/Cairo")
_EGX_WEEKEND_DAYS = {4, 5}  # Friday=4, Saturday=5


def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _now_cairo() -> _dt.datetime:
    return _now_utc().astimezone(_EGX_TZ)


def _is_market_day(d: _dt.date) -> bool:
    """Return True if *d* is a regular EGX trading day (Sun–Thu)."""
    return d.weekday() not in _EGX_WEEKEND_DAYS


def _is_market_open() -> bool:
    """Return True if EGX is currently in trading hours."""
    now = _now_cairo()
    if not _is_market_day(now.date()):
        return False
    # EGX hours: 10:00–14:30 Cairo time
    t = now.time()
    return _dt.time(10, 0) <= t < _dt.time(14, 30)


def _last_trading_day(ref: _dt.date | None = None) -> _dt.date:
    """Return the most recent trading day on or before *ref*."""
    if ref is None:
        ref = _now_cairo().date()
    d = ref
    for _ in range(7):
        if _is_market_day(d):
            return d
        d -= _dt.timedelta(days=1)
    return d


def _age_seconds(ts: _dt.datetime | None) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.timezone.utc)
    return (_now_utc() - ts).total_seconds()


def _status_for(
    age_secs: float | None,
    fresh_threshold_secs: float,
    *,
    market_aware: bool = False,
) -> str:
    """Classify freshness status.

    Returns one of:
      ``"Fresh"``                 — within threshold
      ``"Fresh (market closed)"`` — within threshold after extending for non-trading gap
      ``"Stale"``                 — exceeds threshold
      ``"Missing"``               — no data at all
    """
    if age_secs is None:
        return "Missing"

    if age_secs <= fresh_threshold_secs:
        return "Fresh"

    if market_aware and not _is_market_open():
        now_cairo = _now_cairo()
        if not _is_market_day(now_cairo.date()):
            # Weekend — extend threshold by 3 days (Thu close → Sun)
            extended = fresh_threshold_secs + 3 * 86_400
        else:
            # Before open or after close — extend by 1 day
            extended = fresh_threshold_secs + 86_400

        if age_secs <= extended:
            return "Fresh (market closed)"

    return "Stale"


# ═══════════════════════════════════════════════════════════════════════════
#  Shadow-run fallback: derive freshness from the latest completed run
# ═══════════════════════════════════════════════════════════════════════════

def _latest_shadow_run() -> Dict[str, Any] | None:
    """Return the most recent completed shadow run from the dashboard store."""
    try:
        from server.dashboard_store import list_shadow_runs
        runs = list_shadow_runs(limit=1)
        if runs:
            return runs[0]
    except Exception:
        pass
    return None


def _parse_shadow_ts(ts_str: str | None) -> _dt.datetime | None:
    """Parse a timestamp from the shadow run store.

    The dashboard SQLite store writes ``datetime.now()`` (local Cairo time)
    without timezone info.  We attach ``Africa/Cairo`` so age calculations
    are correct.
    """
    if not ts_str:
        return None
    try:
        ts = _dt.datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_EGX_TZ)
        return ts
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  Individual source checkers
# ═══════════════════════════════════════════════════════════════════════════

def _check_price(tickers: list[str], last_run: Dict | None) -> Dict[str, Any]:
    """Check yfinance OHLCV cache freshness, falling back to shadow run metadata."""
    latest_ts: _dt.datetime | None = None
    cached_tickers: int = 0
    timestamp_meaning = "cache entry write time"

    # Primary: scan diskcache for ohlcv keys
    try:
        from tradingagents.dataflows.cache_manager import CacheManager
        cache = CacheManager(str(_DATA_CACHE))
        tickers_seen: set[str] = set()

        if cache._enabled and cache._cache is not None:
            for key in cache._cache:
                if not isinstance(key, str) or not key.startswith("ohlcv:"):
                    continue
                val = cache._cache.get(key)
                if val is None:
                    continue
                retrieved = None
                if isinstance(val, dict):
                    rat = val.get("retrieved_at")
                    if isinstance(rat, str):
                        try:
                            retrieved = _dt.datetime.fromisoformat(rat)
                        except Exception:
                            pass
                    elif isinstance(rat, _dt.datetime):
                        retrieved = rat
                if retrieved is not None:
                    if retrieved.tzinfo is None:
                        retrieved = retrieved.replace(tzinfo=_dt.timezone.utc)
                    if latest_ts is None or retrieved > latest_ts:
                        latest_ts = retrieved
                    parts = key.split(":")
                    if len(parts) > 1:
                        tickers_seen.add(parts[1])
            cached_tickers = len(tickers_seen)
    except Exception as exc:
        logger.debug("price cache scan failed: %s", exc)

    # Fallback: shadow run metadata
    if latest_ts is None and last_run:
        price_date = last_run.get("price_date")
        if price_date:
            try:
                pd = _dt.datetime.fromisoformat(price_date)
                if pd.tzinfo is None:
                    # price_date is a date string like "2026-06-21", treat as Cairo EOD
                    pd = _dt.datetime.combine(
                        _dt.date.fromisoformat(price_date),
                        _dt.time(14, 30),
                        tzinfo=_EGX_TZ,
                    )
                latest_ts = pd
                cached_tickers = 1
                timestamp_meaning = "price as-of date from last shadow run"
            except Exception:
                pass

    age = _age_seconds(latest_ts)
    st = _status_for(age, 4 * 3600, market_aware=True)

    return {
        "source": "price/yfinance",
        "latest_timestamp": latest_ts.isoformat() if latest_ts else None,
        "age_seconds": round(age) if age is not None else None,
        "age_human": _human_age(age),
        "status": st,
        "label": "Latest available trading data" if st != "Missing" else None,
        "timestamp_meaning": timestamp_meaning,
        "cached_tickers": cached_tickers,
        "detail": "yfinance OHLCV via diskcache (TTL 4h)",
    }


def _check_fundamentals(tickers: list[str]) -> Dict[str, Any]:
    """Check CSV file mtimes for fundamentals data."""
    ratios_dir = _FUNDAMENTALS_DIR / "key_ratios"
    if not ratios_dir.exists():
        return {
            "source": "fundamentals",
            "latest_timestamp": None,
            "age_seconds": None,
            "age_human": None,
            "status": "Missing",
            "timestamp_meaning": None,
            "affected_tickers": tickers,
            "detail": f"Directory not found: {ratios_dir}",
        }

    csv_files = list(ratios_dir.glob("*_ratios.csv"))
    if not csv_files:
        return {
            "source": "fundamentals",
            "latest_timestamp": None,
            "age_seconds": None,
            "age_human": None,
            "status": "Missing",
            "timestamp_meaning": None,
            "affected_tickers": tickers,
            "detail": "No ratios CSV files found",
        }

    newest_mtime = max(f.stat().st_mtime for f in csv_files)
    newest_dt = _dt.datetime.fromtimestamp(newest_mtime, tz=_dt.timezone.utc)
    age = _age_seconds(newest_dt)
    st = _status_for(age, 90 * 86_400)

    # Find which tickers are stale (>90d)
    stale: list[str] = []
    now_ts = time.time()
    for f in csv_files:
        if (now_ts - f.stat().st_mtime) > 90 * 86_400:
            name = f.stem.replace("_ratios", "")
            stale.append(f"{name}.CA")

    return {
        "source": "fundamentals",
        "latest_timestamp": newest_dt.isoformat(),
        "age_seconds": round(age) if age is not None else None,
        "age_human": _human_age(age),
        "status": st,
        "timestamp_meaning": "file modified time (not data reporting period)",
        "total_files": len(csv_files),
        "affected_tickers": stale if stale else None,
        "detail": f"{len(csv_files)} ratio files, staleness threshold 90d",
    }


def _check_news(last_run: Dict | None) -> Dict[str, Any]:
    """Check news cache freshness, falling back to shadow run metadata."""
    latest_ts: _dt.datetime | None = None
    timestamp_meaning = "cache entry write time"

    # Primary: scan diskcache for news keys
    try:
        from tradingagents.dataflows.cache_manager import CacheManager
        cache = CacheManager(str(_DATA_CACHE))

        if cache._enabled and cache._cache is not None:
            for key in cache._cache:
                if not isinstance(key, str) or not key.startswith("news:"):
                    continue
                val = cache._cache.get(key)
                if val is None:
                    continue
                if isinstance(val, dict):
                    rat = val.get("retrieved_at")
                    if isinstance(rat, str):
                        try:
                            ts = _dt.datetime.fromisoformat(rat)
                            if ts.tzinfo is None:
                                ts = ts.replace(tzinfo=_dt.timezone.utc)
                            if latest_ts is None or ts > latest_ts:
                                latest_ts = ts
                        except Exception:
                            pass
    except Exception as exc:
        logger.debug("news cache scan failed: %s", exc)

    # Also check local news CSVs
    news_csv_dir = _DATA_CACHE / "egx_news" / "csv"
    if news_csv_dir.exists():
        for f in news_csv_dir.glob("*.csv"):
            mtime = _dt.datetime.fromtimestamp(f.stat().st_mtime, tz=_dt.timezone.utc)
            if latest_ts is None or mtime > latest_ts:
                latest_ts = mtime
                timestamp_meaning = "local news CSV file modified time"

    # Fallback: shadow run created_at implies news was fetched then
    if latest_ts is None and last_run:
        ts = _parse_shadow_ts(last_run.get("created_at"))
        if ts is not None:
            latest_ts = ts
            timestamp_meaning = "last shadow run completion time (news fetched during run)"

    age = _age_seconds(latest_ts)
    st = _status_for(age, 30 * 60)  # 30 min TTL

    return {
        "source": "news",
        "latest_timestamp": latest_ts.isoformat() if latest_ts else None,
        "age_seconds": round(age) if age is not None else None,
        "age_human": _human_age(age),
        "status": st,
        "timestamp_meaning": timestamp_meaning,
        "detail": "News cache TTL 30min; local CSVs as fallback",
    }


def _check_social(last_run: Dict | None) -> Dict[str, Any]:
    """Check social v2 signal cache freshness, falling back to shadow run metadata."""
    latest_ts: _dt.datetime | None = None
    timestamp_meaning = "signal cache entry time"

    # Primary: check diskcache signal region
    try:
        cache_dir = (
            _PROJECT_ROOT / "tradingagents" / "dataflows" / "social_v2" / "_cache"
        )
        signal_dir = cache_dir / "signal"
        if signal_dir.exists():
            import diskcache
            sc = diskcache.Cache(str(signal_dir))
            for key in sc:
                val = sc.get(key)
                if isinstance(val, dict):
                    ts_str = val.get("timestamp") or val.get("created_at")
                    if isinstance(ts_str, str):
                        try:
                            ts = _dt.datetime.fromisoformat(ts_str)
                            if ts.tzinfo is None:
                                ts = ts.replace(tzinfo=_dt.timezone.utc)
                            if latest_ts is None or ts > latest_ts:
                                latest_ts = ts
                        except Exception:
                            pass
            sc.close()
    except Exception as exc:
        logger.debug("social cache scan failed: %s", exc)

    # Fallback: shadow run created_at implies social was fetched then
    if latest_ts is None and last_run:
        ts = _parse_shadow_ts(last_run.get("created_at"))
        if ts is not None:
            latest_ts = ts
            timestamp_meaning = "last shadow run completion time (social fetched during run)"

    age = _age_seconds(latest_ts)
    st = _status_for(age, 3600)  # 1h TTL

    return {
        "source": "social",
        "latest_timestamp": latest_ts.isoformat() if latest_ts else None,
        "age_seconds": round(age) if age is not None else None,
        "age_human": _human_age(age),
        "status": st,
        "timestamp_meaning": timestamp_meaning,
        "detail": "Social v2 signal cache TTL 30min, Apify cache TTL 1h",
    }


def _check_macro(last_run: Dict | None) -> Dict[str, Any]:
    """Check macro data freshness, split by sub-source.

    The macro context is assembled from 3 source categories:
      - Live (yfinance): USD/EGP, EGX30 proxy, Brent — fetched every run
      - Event-driven (CSV): CBE policy rate — changes on CBE announcements
      - Static config: T-bill yield, CPI — hardcoded, updated manually

    Reporting all of these as one "28d ago" timestamp is misleading.
    Instead we split them and derive the overall status from the worst.
    """
    sub_sources: list[Dict[str, Any]] = []

    # ── CBE policy rate (event-driven CSV) ──────────────────────────────
    cbe_file = _MACRO_DIR / "cbe_policy_rates.csv"
    if cbe_file.exists():
        mtime = cbe_file.stat().st_mtime
        cbe_dt = _dt.datetime.fromtimestamp(mtime, tz=_dt.timezone.utc)
        cbe_age = _age_seconds(cbe_dt)
        # CBE rate is event-driven — 90d threshold (rate changes are rare)
        cbe_st = _status_for(cbe_age, 90 * 86_400)
        sub_sources.append({
            "name": "CBE policy rate",
            "status": cbe_st,
            "age_human": _human_age(cbe_age),
            "note": "event-driven (changes on CBE MPC decisions)",
        })
    else:
        sub_sources.append({
            "name": "CBE policy rate",
            "status": "Missing",
            "age_human": None,
            "note": "cbe_policy_rates.csv not found",
        })

    # ── Live yfinance fields (USD/EGP, EGX30, Brent) ───────────────────
    # These are fetched fresh on every pipeline run.  Freshness = last run time.
    live_ts: _dt.datetime | None = None
    if last_run:
        macro_ctx = last_run.get("macro_context")
        if isinstance(macro_ctx, str):
            try:
                import json
                macro_ctx = json.loads(macro_ctx)
            except Exception:
                macro_ctx = None

        as_of = None
        if isinstance(macro_ctx, dict):
            as_of = macro_ctx.get("as_of_date")

        # Use shadow run created_at as the fetch timestamp
        run_ts = _parse_shadow_ts(last_run.get("created_at"))
        if run_ts:
            live_ts = run_ts

    if live_ts:
        live_age = _age_seconds(live_ts)
        # Live data is fetched per-run; stale if no run in 24h
        live_st = _status_for(live_age, 24 * 3600)
        live_note = f"fetched live from yfinance (as-of {as_of})" if as_of else "fetched live from yfinance"
    else:
        live_age = None
        live_st = "Missing"
        live_note = "no shadow run recorded yet"

    for field_name in ["USD/EGP", "EGX30 proxy", "Brent oil"]:
        sub_sources.append({
            "name": field_name,
            "status": live_st,
            "age_human": _human_age(live_age),
            "note": live_note,
        })

    # ── Static config (T-bill, CPI) ────────────────────────────────────
    sub_sources.append({
        "name": "T-bill yield",
        "status": "Available",
        "age_human": None,
        "note": "config constant (updated manually when auction data available)",
    })
    sub_sources.append({
        "name": "CPI",
        "status": "Available",
        "age_human": None,
        "note": "config constant (updated manually from CAPMAS monthly)",
    })

    # ── Overall status: worst of all sub-sources ────────────────────────
    STATUS_RANK = {"Missing": 0, "Stale": 1, "Available": 2, "Fresh (market closed)": 3, "Fresh": 4}
    worst = min(sub_sources, key=lambda s: STATUS_RANK.get(s["status"], 2))
    overall_status = worst["status"]
    # If worst is "Available" (config constants), treat overall as Fresh
    if overall_status == "Available":
        overall_status = "Fresh"

    # Use live timestamp as the representative (most meaningful to the user)
    rep_ts = live_ts
    rep_age = _age_seconds(rep_ts) if rep_ts else None

    return {
        "source": "macro",
        "latest_timestamp": rep_ts.isoformat() if rep_ts else None,
        "age_seconds": round(rep_age) if rep_age is not None else None,
        "age_human": _human_age(rep_age),
        "status": overall_status,
        "timestamp_meaning": "last pipeline run (live fields fetched per-run)",
        "sub_sources": sub_sources,
        "detail": "6 fields from 3 source types: yfinance (live), CBE CSV (event-driven), config (static)",
    }


def _check_memory() -> Dict[str, Any]:
    """Check ChromaDB / vector memory status.

    Memory has no write timestamp exposed by ChromaDB, so we report
    ``"Available"`` (documents exist) or ``"Missing"`` (no documents),
    never ``"Fresh"`` — we cannot claim freshness without a timestamp.
    """
    try:
        import chromadb

        persist_dir = os.environ.get("CHROMA_PERSIST_DIR", "./chroma_db")
        chroma_path = Path(persist_dir)

        if not chroma_path.exists():
            return {
                "source": "memory",
                "latest_timestamp": None,
                "age_seconds": None,
                "age_human": None,
                "status": "Missing",
                "total_documents": 0,
                "detail": f"ChromaDB directory not found: {persist_dir}",
            }

        client = chromadb.PersistentClient(path=str(chroma_path))
        collections = client.list_collections()
        collections_info: dict[str, int] = {}
        total = 0
        for coll in collections:
            c = client.get_collection(coll.name)
            n = c.count()
            collections_info[coll.name] = n
            total += n

        return {
            "source": "memory",
            "latest_timestamp": None,
            "age_seconds": None,
            "age_human": None,
            "status": "Available" if total > 0 else "Missing",
            "timestamp_meaning": "unknown (ChromaDB does not expose write timestamps)",
            "total_documents": total,
            "collections": collections_info,
            "detail": f"ChromaDB vector store — {total} documents across {len(collections)} collections",
        }
    except Exception as exc:
        logger.warning("memory freshness check failed: %s", exc)
        return _error_entry("memory", str(exc))


# ═══════════════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════════════

def check_all_freshness(tickers: list[str] | None = None) -> Dict[str, Any]:
    """Return freshness report for all data sources.

    Parameters
    ----------
    tickers : list[str] | None
        EGX ticker list (defaults to ``EGX_TICKERS`` from config).

    Returns
    -------
    dict with keys ``sources`` (list of per-source dicts), ``checked_at``,
    ``market_status``.
    """
    if tickers is None:
        try:
            from tradingagents.default_config import EGX_TICKERS
            tickers = EGX_TICKERS
        except Exception:
            tickers = []

    now_cairo = _now_cairo()
    is_trading = _is_market_open()
    last_td = _last_trading_day()

    # Fetch latest shadow run for fallback freshness
    last_run = _latest_shadow_run()

    sources = [
        _check_price(tickers, last_run),
        _check_fundamentals(tickers),
        _check_news(last_run),
        _check_social(last_run),
        _check_macro(last_run),
        _check_memory(),
    ]

    # Update Prometheus gauges (best-effort)
    _update_prometheus(sources)

    def _count(status_prefix: str) -> int:
        return sum(1 for s in sources if s["status"].startswith(status_prefix))

    return {
        "checked_at": _now_utc().isoformat(),
        "market_status": {
            "is_trading_hours": is_trading,
            "last_trading_day": last_td.isoformat(),
            "current_time_cairo": now_cairo.strftime("%Y-%m-%d %H:%M %Z"),
            "note": "EGX market is currently open"
                    if is_trading
                    else "EGX hours: Sun–Thu 10:00–14:30 Cairo time",
        },
        "summary": {
            "total_sources": len(sources),
            "fresh": _count("Fresh"),
            "stale": _count("Stale"),
            "missing": _count("Missing"),
            "available": _count("Available"),
        },
        "sources": sources,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Prometheus integration
# ═══════════════════════════════════════════════════════════════════════════

def _update_prometheus(sources: list[dict]) -> None:
    """Push freshness gauges to Prometheus (best-effort, no crash).

    stale_current and missing_current are **Gauges** set to the current
    count on each call — they do NOT accumulate across polls.
    """
    try:
        from tradingagents.observability.metrics import (
            data_freshness_age_seconds,
            data_stale_current,
            data_missing_current,
            data_fetch_last_success_timestamp,
        )

        # Reset all source gauges to 0 first, then set current values
        known_sources = {"price/yfinance", "fundamentals", "news", "social", "macro", "memory"}
        for name in known_sources:
            data_stale_current.labels(source=name).set(0)
            data_missing_current.labels(source=name).set(0)

        for src in sources:
            name = src["source"]
            age = src.get("age_seconds")
            ts_str = src.get("latest_timestamp")
            status = src.get("status", "Missing")

            if age is not None:
                data_freshness_age_seconds.labels(source=name, ticker="all").set(age)

            if ts_str:
                try:
                    ts = _dt.datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=_dt.timezone.utc)
                    data_fetch_last_success_timestamp.labels(
                        source=name, ticker="all"
                    ).set(ts.timestamp())
                except Exception:
                    pass

            if status == "Stale":
                data_stale_current.labels(source=name).set(1)
            elif status == "Missing":
                data_missing_current.labels(source=name).set(1)

    except ImportError:
        pass  # metrics module not available
    except Exception as exc:
        logger.debug("prometheus freshness update failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _human_age(seconds: float | None) -> str | None:
    """Convert seconds to a human-readable age string."""
    if seconds is None:
        return None
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86_400:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m ago"
    days = int(seconds // 86_400)
    return f"{days}d ago"


def _error_entry(source: str, error: str) -> Dict[str, Any]:
    return {
        "source": source,
        "latest_timestamp": None,
        "age_seconds": None,
        "age_human": None,
        "status": "Missing",
        "timestamp_meaning": None,
        "error": error,
        "detail": f"Check failed: {error}",
    }
