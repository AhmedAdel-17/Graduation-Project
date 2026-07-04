"""
Market insights — top movers, losers and sector performance for the dashboard home.

Pure, deterministic, offline: reads the local per-ticker OHLCV CSVs in
``data/egx_ohlcv/<TICKER>.CA.csv`` (columns: date, open, high, low, close, ...)
and computes trailing returns over 1M / 3M / 1Y / 5Y windows. No LLM, no network.

Results are cached in-process for a short TTL so the home screen stays snappy and
we don't re-parse ~30 CSVs on every request.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger("tradingagents.market_insights")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OHLCV_DIR = PROJECT_ROOT / "data" / "egx_ohlcv"

# Trailing windows in trading days (~21/mo). Row-offset based so they are
# robust to gaps and need no calendar arithmetic.
TIMEFRAMES: Dict[str, int] = {
    "month": 21,
    "quarter": 63,
    "year": 252,
    "5year": 1260,
}

# Ticker → (English name, sector). Mirrors dashboard/src/data/egxTickerMeta.ts.
TICKER_META: Dict[str, Tuple[str, str]] = {
    "COMI.CA": ("Commercial International Bank", "Banks"),
    "ADIB.CA": ("Abu Dhabi Islamic Bank – Egypt", "Banks"),
    "TMGH.CA": ("T M G Holding", "Real Estate"),
    "HELI.CA": ("Heliopolis Housing & Development", "Real Estate"),
    "PHDC.CA": ("Palm Hills Development", "Real Estate"),
    "ORAS.CA": ("Orascom Construction", "Real Estate"),
    "EMFD.CA": ("Emaar Misr for Development", "Real Estate"),
    "ORHD.CA": ("Orascom Hotels and Development", "Real Estate"),
    "ABUK.CA": ("Abu Qir Fertilizers", "Industry & Materials"),
    "EAST.CA": ("Eastern Tobacco", "Industry & Materials"),
    "EGAL.CA": ("Egypt Aluminum", "Industry & Materials"),
    "EGCH.CA": ("Egyptian Chemical Industries (Kima)", "Industry & Materials"),
    "ORWE.CA": ("Oriental Weavers", "Industry & Materials"),
    "AMOC.CA": ("Alexandria Mineral Oils (AMOC)", "Industry & Materials"),
    "MCQE.CA": ("Misr Cement (Qena)", "Industry & Materials"),
    "ARCC.CA": ("Arabian Cement", "Industry & Materials"),
    "ISPH.CA": ("Ibnsina Pharma", "Industry & Materials"),
    "RMDA.CA": ("Tenth of Ramadan Pharma (Rameda)", "Industry & Materials"),
    "GBCO.CA": ("GB Corp (GB Auto)", "Industry & Materials"),
    "ETEL.CA": ("Telecom Egypt", "Telecom & Tech"),
    "FWRY.CA": ("Fawry for Banking & Payment Technology", "Telecom & Tech"),
    "EFIH.CA": ("e-finance for Digital & Financial Investments", "Telecom & Tech"),
    "RAYA.CA": ("Raya Holding", "Telecom & Tech"),
    "OIH.CA": ("Orascom Investment Holding", "Telecom & Tech"),
    "HRHO.CA": ("EFG Hermes Holding", "Financial Services"),
    "BTFH.CA": ("Beltone Financial Holding", "Financial Services"),
    "CCAP.CA": ("Qalaa Holdings", "Financial Services"),
    "VLMR.CA": ("Valmore Holding", "Financial Services"),
    "JUFO.CA": ("Juhayna Food Industries", "Food & Beverage"),
    "EFID.CA": ("Edita Food Industries", "Food & Beverage"),
}


def _meta(ticker: str) -> Tuple[str, str]:
    return TICKER_META.get(ticker, (ticker.replace(".CA", ""), "Other"))


# ── in-process cache ────────────────────────────────────────────────────────
_CACHE: Dict[str, Tuple[float, object]] = {}
_TTL_SECONDS = 15 * 60


def _cached(key: str, builder):
    hit = _CACHE.get(key)
    now = time.time()
    if hit and (now - hit[0]) < _TTL_SECONDS:
        return hit[1]
    value = builder()
    _CACHE[key] = (now, value)
    return value


def _universe() -> List[str]:
    """Current EGX-30 tracked universe; falls back to the meta keys."""
    try:
        from tradingagents.default_config import EGX_TICKERS

        return list(EGX_TICKERS)
    except Exception:  # pragma: no cover - defensive
        return list(TICKER_META.keys())


def _load_closes() -> Dict[str, pd.Series]:
    """Return {ticker: close-price Series} for every tracked ticker with a CSV."""
    closes: Dict[str, pd.Series] = {}
    if not OHLCV_DIR.exists():
        logger.warning("OHLCV directory not found: %s", OHLCV_DIR)
        return closes
    universe = set(_universe())
    for csv_path in sorted(OHLCV_DIR.glob("*.CA.csv")):
        ticker = csv_path.name.replace(".csv", "")
        if ticker not in universe:
            continue
        try:
            df = pd.read_csv(csv_path, usecols=["close"])
            s = pd.to_numeric(df["close"], errors="coerce").dropna()
            if len(s) >= 2:
                closes[ticker] = s.reset_index(drop=True)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Skipping %s: %s", csv_path.name, exc)
    return closes


def _return_over(series: pd.Series, lookback: int) -> Optional[float]:
    """Percent return over the last ``lookback`` rows; None if insufficient history."""
    if len(series) <= lookback:
        return None
    last = float(series.iloc[-1])
    prev = float(series.iloc[-1 - lookback])
    if prev <= 0:
        return None
    return (last - prev) / prev * 100.0


def _compute_movers() -> Dict[str, Dict[str, List[dict]]]:
    closes = _load_closes()
    out: Dict[str, Dict[str, List[dict]]] = {}
    for tf, lookback in TIMEFRAMES.items():
        rows: List[dict] = []
        for ticker, series in closes.items():
            r = _return_over(series, lookback)
            if r is None:
                continue
            name, sector = _meta(ticker)
            rows.append({"ticker": ticker, "name": name, "sector": sector, "change": round(r, 2)})
        rows.sort(key=lambda x: x["change"], reverse=True)
        gainers = rows[:5]
        losers = list(reversed(rows[-5:])) if len(rows) >= 1 else []
        # Avoid a ticker appearing in both lists when the universe is tiny.
        gainer_syms = {g["ticker"] for g in gainers}
        losers = [l for l in losers if l["ticker"] not in gainer_syms][:5]
        out[tf] = {"gainers": gainers, "losers": losers}
    return out


def _compute_sectors() -> Dict[str, List[dict]]:
    closes = _load_closes()
    out: Dict[str, List[dict]] = {}
    for tf, lookback in TIMEFRAMES.items():
        buckets: Dict[str, List[float]] = {}
        for ticker, series in closes.items():
            r = _return_over(series, lookback)
            if r is None:
                continue
            _, sector = _meta(ticker)
            buckets.setdefault(sector, []).append(r)
        rows = [
            {
                "sector": sector,
                "change": round(sum(vals) / len(vals), 2),
                "count": len(vals),
            }
            for sector, vals in buckets.items()
            if vals
        ]
        rows.sort(key=lambda x: x["change"], reverse=True)
        out[tf] = rows
    return out


def get_market_movers() -> Dict[str, Dict[str, List[dict]]]:
    """Top 5 gainers/losers per timeframe (month/quarter/year/5year)."""
    return _cached("movers", _compute_movers)


def get_sector_performance() -> Dict[str, List[dict]]:
    """Sector-average returns per timeframe, ranked best-first."""
    return _cached("sectors", _compute_sectors)


if __name__ == "__main__":
    import json

    print(json.dumps(get_market_movers(), indent=2))
    print(json.dumps(get_sector_performance(), indent=2))
