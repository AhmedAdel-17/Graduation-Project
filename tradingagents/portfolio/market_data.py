"""Market data for the portfolio optimizer (roadmap P1).

Two responsibilities, split by testability:

* **Pure (unit-tested):** ``compute_covariance`` — Ledoit-Wolf shrinkage
  covariance from a returns matrix. Shrinkage is essential on EGX: history is
  short/gappy and some names are illiquid, so the raw sample covariance is
  unstable / singular. ``build_covariance`` adds a min-history gate.
* **Best-effort I/O (not unit-tested with network):** ``get_return_history`` and
  ``get_live_prices`` pull from the existing data layer (yfinance / gateway).
  They degrade to empty/partial results and never raise into the caller, so the
  optimizer can fall back to a diagonal prior when history is unavailable.

The optimizer takes the covariance DataFrame as an injected argument, so it has
no dependency on this module's I/O — keeping the optimizer fully offline-testable.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger("tradingagents.portfolio.market_data")

_TRADING_DAYS = 252


def compute_covariance(
    returns: pd.DataFrame, *, annualize: bool = True
) -> tuple[pd.DataFrame, float]:
    """Ledoit-Wolf shrinkage covariance over the complete-case returns matrix.

    Returns ``(covariance_df, shrinkage)``. ``covariance_df`` is indexed by the
    input columns (tickers). Annualized by ×252 by default. Raises ``ValueError``
    on an empty matrix or fewer than 2 observations.
    """
    df = returns.dropna(axis=1, how="all").dropna(axis=0, how="any")
    if df.shape[1] == 0:
        raise ValueError("no usable return columns for covariance")
    if df.shape[0] < 2:
        raise ValueError("need >= 2 complete observations for covariance")

    scale = _TRADING_DAYS if annualize else 1.0

    if df.shape[1] == 1:
        # Ledoit-Wolf is degenerate for a single asset → plain sample variance.
        var = float(np.var(df.iloc[:, 0].to_numpy(), ddof=1)) * scale
        return pd.DataFrame([[var]], index=df.columns, columns=df.columns), 0.0

    from sklearn.covariance import LedoitWolf

    lw = LedoitWolf().fit(df.to_numpy())
    cov = lw.covariance_ * scale
    return pd.DataFrame(cov, index=df.columns, columns=df.columns), float(lw.shrinkage_)


def build_covariance(
    returns: pd.DataFrame, *, min_history_days: int = 60, annualize: bool = True
) -> tuple[Optional[pd.DataFrame], list[str], float]:
    """Covariance with a per-ticker min-history gate.

    Returns ``(covariance_df_or_None, excluded_tickers, shrinkage)``. Tickers with
    fewer than ``min_history_days`` non-NaN observations are dropped (and listed);
    if nothing usable remains, the covariance is ``None`` (optimizer falls back to
    a diagonal prior).
    """
    excluded = [c for c in returns.columns
                if int(returns[c].notna().sum()) < min_history_days]
    usable = [c for c in returns.columns if c not in excluded]
    if not usable:
        return None, excluded, 0.0
    try:
        cov, shrink = compute_covariance(returns[usable], annualize=annualize)
        return cov, excluded, shrink
    except ValueError as exc:
        logger.warning("build_covariance: %s", exc)
        return None, list(returns.columns), 0.0


def diagonal_covariance(tickers: Sequence[str], annual_vol: float = 0.30) -> pd.DataFrame:
    """A neutral fallback covariance: equal annualized variance, zero correlation.
    Used when no return history is available so the MVO still runs (concentration
    is then governed by the position caps, not the data)."""
    n = len(tickers)
    var = annual_vol ** 2
    return pd.DataFrame(np.eye(n) * var, index=list(tickers), columns=list(tickers))


# ---------------------------------------------------------------------------
# Best-effort I/O (degrades gracefully; never raises into the caller)
# ---------------------------------------------------------------------------

def _close_series(raw: object) -> Optional[pd.Series]:
    """Extract a date-indexed close-price Series from whatever
    ``y_finance.get_YFin_data_online`` returns.

    That function returns a **dict** ``{symbol, ..., data: [{date, close, ...}]}``
    (NOT a DataFrame) — assuming a DataFrame here silently dropped every price
    (live-test 2026-06-15). This adapter handles the dict shape and stays
    defensive about a DataFrame in case the upstream contract changes.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        rows = raw.get("data") or []
        if not rows:
            return None
        try:
            idx = pd.to_datetime([r.get("date") for r in rows])
            vals = pd.to_numeric([r.get("close") for r in rows], errors="coerce")
            s = pd.Series(vals, index=idx, dtype="float64").dropna()
            return s if not s.empty else None
        except Exception as exc:  # pragma: no cover — malformed payload
            logger.debug("_close_series: dict parse failed (%s)", exc)
            return None
    if isinstance(raw, pd.DataFrame):  # pragma: no cover — defensive
        if raw.empty:
            return None
        col = "Close" if "Close" in raw.columns else ("close" if "close" in raw.columns else raw.columns[-1])
        s = pd.Series(raw[col].to_numpy(), index=pd.to_datetime(raw.index), dtype="float64").dropna()
        return s if not s.empty else None
    return None


def get_return_history(
    tickers: Sequence[str], end_date: date | datetime | str, *, lookback_days: int = 504,
) -> pd.DataFrame:
    """Daily simple returns for ``tickers`` up to ``end_date`` (best-effort).

    Pulls OHLCV via the existing yfinance dataflow and computes pct-change
    returns. Missing tickers are simply absent from the result. Returns an empty
    DataFrame on total failure — never raises.
    """
    try:
        from tradingagents.dataflows import y_finance
    except Exception as exc:  # pragma: no cover
        logger.warning("get_return_history: data layer unavailable (%s)", exc)
        return pd.DataFrame()

    end = pd.to_datetime(end_date)
    start = end - pd.Timedelta(days=int(lookback_days * 1.6))  # calendar buffer for trading days
    series: dict[str, pd.Series] = {}
    for t in tickers:
        try:
            # max_records=None: the analyst tool caps to 20 bars for token budget,
            # which starves covariance/momentum — request the full history here.
            raw = y_finance.get_YFin_data_online(
                t, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), max_records=None)
            close = _close_series(raw)
            if close is not None and len(close) >= 2:
                series[t] = close.pct_change()
        except Exception as exc:  # pragma: no cover — per-ticker best-effort
            logger.debug("get_return_history: %s skipped (%s)", t, exc)
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).dropna(how="all").tail(lookback_days)


def _local_market_cap(ticker: str) -> Optional[float]:
    """Latest non-empty ``market_cap`` for a ticker from the local EGX key-ratios
    CSV (``<DATA_DIR>/egx_fundamentals/key_ratios/<SYM>_ratios.csv``). Best-effort:
    returns ``None`` when the file/column is absent. Offline + deterministic."""
    try:
        from pathlib import Path
        import csv as _csv
        from tradingagents.dataflows.config import DATA_DIR
        from tradingagents.dataflows.local import EGX_FUNDAMENTALS_DIR
    except Exception:  # pragma: no cover — import edge
        return None
    sym = ticker.strip().upper()
    if sym.endswith(".CA"):
        sym = sym[:-3]
    path = Path(DATA_DIR) / EGX_FUNDAMENTALS_DIR / "key_ratios" / f"{sym}_ratios.csv"
    if not path.exists():
        return None
    try:
        rows = list(_csv.DictReader(path.open(encoding="utf-8")))
    except Exception:  # pragma: no cover — malformed csv
        return None
    for row in reversed(rows):  # latest period first
        raw = (row.get("market_cap") or "").strip()
        if raw and raw.lower() != "none":
            try:
                val = float(raw)
                if val > 0:
                    return val
            except ValueError:
                continue
    return None


_RATIO_FIELDS = ("pe_ratio", "roe", "roa", "debt_to_equity", "net_margin", "eps", "market_cap")


def _latest_ratios(ticker: str) -> dict[str, float]:
    """Latest non-empty value for each key ratio from the local EGX key-ratios CSV.
    Different periods populate different columns, so each field is taken from its
    most recent non-empty row independently. Offline + best-effort ({} on miss)."""
    try:
        from pathlib import Path
        import csv as _csv
        from tradingagents.dataflows.config import DATA_DIR
        from tradingagents.dataflows.local import EGX_FUNDAMENTALS_DIR
    except Exception:  # pragma: no cover — import edge
        return {}
    sym = ticker.strip().upper()
    if sym.endswith(".CA"):
        sym = sym[:-3]
    path = Path(DATA_DIR) / EGX_FUNDAMENTALS_DIR / "key_ratios" / f"{sym}_ratios.csv"
    if not path.exists():
        return {}
    try:
        rows = list(_csv.DictReader(path.open(encoding="utf-8")))
    except Exception:  # pragma: no cover — malformed csv
        return {}
    out: dict[str, float] = {}
    for field in _RATIO_FIELDS:
        for row in reversed(rows):  # latest period first
            raw = (row.get(field) or "").strip()
            if raw and raw.lower() != "none":
                try:
                    out[field] = float(raw)
                    break
                except ValueError:
                    continue
    return out


def get_fundamental_ratios(tickers: Sequence[str]) -> dict[str, dict[str, float]]:
    """Per-ticker fundamental ratios for the view engine (Phase 2), from the local
    EGX key-ratios CSVs. Offline + deterministic; tickers with no file are absent."""
    out: dict[str, dict[str, float]] = {}
    for t in tickers:
        ratios = _latest_ratios(t)
        if ratios:
            out[t] = ratios
    return out


def get_market_cap_weights(
    tickers: Sequence[str],
) -> tuple[dict[str, float], list[str]]:
    """Market-cap weights over ``tickers`` for the Black-Litterman equilibrium
    prior (He & Litterman 1999: the CAPM-equilibrium portfolio is the market-cap
    portfolio). Returns ``(weights, imputed)`` where ``weights`` sums to 1 over
    the input universe and ``imputed`` lists tickers whose cap was missing and
    filled with the median known cap (disclosed in the proposal audit).

    Returns ``({}, list(tickers))`` when no cap is available for any name — the
    caller then degrades to its own neutral prior (e.g. current weights)."""
    caps: dict[str, float] = {}
    for t in tickers:
        cap = _local_market_cap(t)
        if cap is not None and cap > 0:
            caps[t] = cap
    if not caps:
        return {}, list(tickers)

    median_cap = float(np.median(list(caps.values())))
    full: dict[str, float] = {}
    imputed: list[str] = []
    for t in tickers:
        if t in caps:
            full[t] = caps[t]
        else:
            full[t] = median_cap
            imputed.append(t)
    total = sum(full.values())
    if total <= 0:  # pragma: no cover — guarded by median_cap > 0
        return {}, list(tickers)
    return {t: full[t] / total for t in tickers}, imputed


def get_live_prices(tickers: Sequence[str]) -> dict[str, float]:
    """Latest close per ticker (best-effort). Missing tickers are omitted."""
    out: dict[str, float] = {}
    try:
        from tradingagents.dataflows import y_finance
    except Exception as exc:  # pragma: no cover
        logger.warning("get_live_prices: data layer unavailable (%s)", exc)
        return out
    end = pd.Timestamp.utcnow()
    start = end - pd.Timedelta(days=10)
    for t in tickers:
        try:
            raw = y_finance.get_YFin_data_online(t, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
            close = _close_series(raw)
            if close is not None and len(close) and float(close.iloc[-1]) > 0:
                out[t] = float(close.iloc[-1])
        except Exception as exc:  # pragma: no cover
            logger.debug("get_live_prices: %s skipped (%s)", t, exc)
    return out


__all__ = [
    "compute_covariance", "build_covariance", "diagonal_covariance",
    "get_return_history", "get_live_prices", "get_market_cap_weights",
    "get_fundamental_ratios",
]
