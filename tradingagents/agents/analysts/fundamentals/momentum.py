"""
P3: Momentum, Relative Strength & Volume features for EGX Fundamental Analyst.

Pure functions only — no LLM calls, no side effects, no network I/O.
All computations use data ≤ trade_date (no look-ahead).

Features produced:
  M1-M3:  return_20d / return_60d / return_120d
  M4-M6:  price_vs_sma20 / price_vs_sma50 / price_vs_sma200
  M7:     trend_slope_60d (annualized OLS slope of ln(close))
  M8-M9:  volume_ratio_20d / volume_confirmed
  M10:    momentum_label (strong_up / moderate_up / neutral / moderate_down / strong_down / insufficient_history)
  R1-R4:  rs_20d / rs_60d / rs_120d / rs_label
"""
from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional, TypedDict

logger = logging.getLogger(__name__)


class MomentumPack(TypedDict, total=False):
    """All P3 momentum and relative-strength features."""
    # M1-M3: period returns
    return_20d: Optional[float]
    return_60d: Optional[float]
    return_120d: Optional[float]
    # M4-M6: price vs SMA
    price_vs_sma20: Optional[float]
    price_vs_sma50: Optional[float]
    price_vs_sma200: Optional[float]
    # M7: trend slope
    trend_slope_60d: Optional[float]
    # M8-M9: volume
    volume_ratio_20d: Optional[float]
    volume_confirmed: bool
    # M10: derived label
    momentum_label: str
    # R1-R4: relative strength vs EGX30
    rs_20d: Optional[float]
    rs_60d: Optional[float]
    rs_120d: Optional[float]
    rs_label: str


# ---------------------------------------------------------------------------
# Core computation helpers
# ---------------------------------------------------------------------------

def _period_return(closes: List[float], n: int) -> Optional[float]:
    """Return over the last *n* bars: (close[-1] / close[-n-1]) - 1."""
    if len(closes) < n + 1:
        return None
    base = closes[-(n + 1)]
    if base == 0:
        return None
    return (closes[-1] / base) - 1


def _sma(series: List[float], window: int) -> Optional[float]:
    """Simple moving average of the last *window* values."""
    if len(series) < window:
        return None
    return sum(series[-window:]) / window


def _price_vs_sma(closes: List[float], window: int) -> Optional[float]:
    """(close / SMA(window)) - 1.  Positive → above SMA."""
    avg = _sma(closes, window)
    if avg is None or avg == 0:
        return None
    return (closes[-1] / avg) - 1


def _trend_slope_annualized(closes: List[float], window: int = 60) -> Optional[float]:
    """
    OLS slope of ln(close) over the last *window* bars, annualized.

    Returns the annualized growth rate implied by the log-linear fit.
    Requires at least 30 bars even when window=60 (graceful degradation).
    """
    min_bars = max(30, window // 2)
    n = min(window, len(closes))
    if n < min_bars:
        return None
    segment = closes[-n:]
    # Guard against non-positive prices
    try:
        ln_prices = [math.log(p) for p in segment if p > 0]
    except (ValueError, TypeError):
        return None
    if len(ln_prices) < min_bars:
        return None

    # Simple OLS: y = a + b*x
    n_pts = len(ln_prices)
    x_mean = (n_pts - 1) / 2.0
    y_mean = sum(ln_prices) / n_pts
    numerator = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(ln_prices))
    denominator = sum((i - x_mean) ** 2 for i in range(n_pts))
    if denominator == 0:
        return None
    slope_per_bar = numerator / denominator
    # Annualize: ~252 trading days/year
    return slope_per_bar * 252


def _volume_ratio(volumes: List[float], window: int = 20) -> Optional[float]:
    """Latest volume / SMA(volume, window)."""
    if len(volumes) < window:
        return None
    avg = sum(volumes[-window:]) / window
    if avg == 0:
        return None
    return volumes[-1] / avg


# ---------------------------------------------------------------------------
# Momentum label
# ---------------------------------------------------------------------------

def _derive_momentum_label(
    return_60d: Optional[float],
    price_vs_sma50: Optional[float],
    volume_confirmed: bool,
) -> str:
    """
    Classify momentum into a human-readable label.

    Thresholds are from general finance practice, NOT tuned on any benchmark.
    """
    if return_60d is None:
        return "insufficient_history"

    above_sma50 = price_vs_sma50 is not None and price_vs_sma50 > 0
    below_sma50 = price_vs_sma50 is not None and price_vs_sma50 < 0

    if return_60d > 0.15 and above_sma50 and volume_confirmed:
        return "strong_up"
    if return_60d > 0.05 and above_sma50:
        return "moderate_up"
    if return_60d < -0.15 and below_sma50 and volume_confirmed:
        return "strong_down"
    if return_60d < -0.05 and below_sma50:
        return "moderate_down"
    return "neutral"


# ---------------------------------------------------------------------------
# Relative strength vs EGX30
# ---------------------------------------------------------------------------

def _find_egx30_close(egx30_map: Dict[str, float], target_date: str, max_lookback: int = 3) -> Optional[float]:
    """
    Find the EGX30 close on or before target_date (up to max_lookback days back).

    Uses exact string comparison on YYYY-MM-DD keys.
    """
    if not egx30_map:
        return None
    # Try exact match first
    if target_date in egx30_map:
        return egx30_map[target_date]
    # Search backward up to max_lookback calendar days
    from datetime import datetime, timedelta
    try:
        dt = datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        return None
    for i in range(1, max_lookback + 1):
        candidate = (dt - timedelta(days=i)).strftime("%Y-%m-%d")
        if candidate in egx30_map:
            return egx30_map[candidate]
    return None


def _compute_rs(
    ticker_return: Optional[float],
    egx30_map: Dict[str, float],
    dates: List[str],
    n: int,
) -> Optional[float]:
    """
    Relative strength: ticker_return(n) - egx30_return(n).

    dates: list of YYYY-MM-DD strings parallel to closes (ascending, ≤ trade_date).
    """
    if ticker_return is None:
        return None
    if len(dates) < n + 1:
        return None
    end_date = dates[-1]
    start_date = dates[-(n + 1)]
    end_price = _find_egx30_close(egx30_map, end_date)
    start_price = _find_egx30_close(egx30_map, start_date)
    if end_price is None or start_price is None or start_price == 0:
        return None
    egx30_return = (end_price / start_price) - 1
    return ticker_return - egx30_return


def _derive_rs_label(rs_60d: Optional[float]) -> str:
    """Classify relative strength into a label."""
    if rs_60d is None:
        return "insufficient_data"
    if rs_60d > 0.05:
        return "outperforming"
    if rs_60d < -0.05:
        return "underperforming"
    return "neutral"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_momentum_pack(
    closes: List[float],
    volumes: List[float],
    dates: List[str],
    trade_date: str,
    egx30_map: Optional[Dict[str, float]] = None,
) -> MomentumPack:
    """
    Compute all P3 momentum and relative-strength features.

    Args:
        closes:     List of close prices (ascending by date).
        volumes:    List of volumes (parallel to closes).
        dates:      List of YYYY-MM-DD date strings (parallel to closes).
        trade_date: As-of date. All data must be ≤ this date.
                    The function truncates internally as a safety net.
        egx30_map:  {YYYY-MM-DD: close_price} from egx30_loader.
                    If None or empty, RS features will be insufficient_data.

    Returns:
        MomentumPack with all M1-M10 and R1-R4 fields.
    """
    # Truncate to trade_date (safety net — caller should pre-truncate)
    safe_closes: List[float] = []
    safe_volumes: List[float] = []
    safe_dates: List[str] = []
    for i, d in enumerate(dates):
        if d <= trade_date:
            safe_closes.append(closes[i] if i < len(closes) else 0)
            safe_volumes.append(volumes[i] if i < len(volumes) else 0)
            safe_dates.append(d)

    # Truncate EGX30 map
    safe_egx30: Dict[str, float] = {}
    if egx30_map:
        safe_egx30 = {d: p for d, p in egx30_map.items() if d <= trade_date}

    # M1-M3: period returns
    return_20d = _period_return(safe_closes, 20)
    return_60d = _period_return(safe_closes, 60)
    return_120d = _period_return(safe_closes, 120)

    # M4-M6: price vs SMA
    price_vs_sma20 = _price_vs_sma(safe_closes, 20)
    price_vs_sma50 = _price_vs_sma(safe_closes, 50)
    price_vs_sma200 = _price_vs_sma(safe_closes, 200)

    # M7: trend slope
    trend_slope_60d = _trend_slope_annualized(safe_closes, 60)

    # M8-M9: volume
    vol_ratio = _volume_ratio(safe_volumes, 20)
    vol_confirmed = vol_ratio is not None and vol_ratio > 1.2

    # M10: label
    momentum_label = _derive_momentum_label(return_60d, price_vs_sma50, vol_confirmed)

    # R1-R3: relative strength
    rs_20d = _compute_rs(return_20d, safe_egx30, safe_dates, 20)
    rs_60d = _compute_rs(return_60d, safe_egx30, safe_dates, 60)
    rs_120d = _compute_rs(return_120d, safe_egx30, safe_dates, 120)

    # R4: label
    rs_label = _derive_rs_label(rs_60d)

    return MomentumPack(
        return_20d=return_20d,
        return_60d=return_60d,
        return_120d=return_120d,
        price_vs_sma20=price_vs_sma20,
        price_vs_sma50=price_vs_sma50,
        price_vs_sma200=price_vs_sma200,
        trend_slope_60d=trend_slope_60d,
        volume_ratio_20d=vol_ratio,
        volume_confirmed=vol_confirmed,
        momentum_label=momentum_label,
        rs_20d=rs_20d,
        rs_60d=rs_60d,
        rs_120d=rs_120d,
        rs_label=rs_label,
    )
