"""
TradingView-TA Provider for EGX
================================
Provides real-time technical analysis signals and price data
from TradingView without requiring an API key.

Features:
- Real-time technical signals (28 indicators, moving averages, oscillators)
- Current price snapshot (near real-time)
- No API key needed
- Falls back gracefully if tradingview-ta is not installed

Usage:
    signals = get_tradingview_signals("COMI")
    price = get_tradingview_price("COMI")
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional
from .schemas import TechnicalSignals

logger = logging.getLogger("tradingagents.tradingview")

# Try to import tradingview-ta (live snapshot signals + price)
try:
    from tradingview_ta import TA_Handler, Interval, Exchange
    TRADINGVIEW_AVAILABLE = True
except ImportError:
    TRADINGVIEW_AVAILABLE = False
    logger.info(
        "tradingview-ta not installed. TradingView provider disabled. "
        "Install with: pip install tradingview-ta"
    )

# Try to import tvDatafeed (historical OHLCV bars from TradingView's chart feed,
# no API key). This is the keyless free source with the best EGX coverage — it
# even returns history for names yfinance has dropped (e.g. ORAS.CA). Anonymous
# ("nologin") mode is used; it is occasionally flaky, so calls are retried.
try:
    from tvDatafeed import TvDatafeed, Interval as _TvInterval
    TVDATAFEED_AVAILABLE = True
except ImportError:
    TVDATAFEED_AVAILABLE = False
    logger.info(
        "tvdatafeed not installed. TradingView OHLCV history disabled. "
        "Install with: pip install tvdatafeed"
    )

DEFAULT_LOW_LIQUIDITY_THRESHOLD = 50000  # shares/day (mirror y_finance / eodhd)

# Cached anonymous TvDatafeed client. Instantiation opens a websocket (~2-5s),
# so reuse one process-wide instance instead of reconnecting per call.
_TV_CLIENT = None


def _get_tv_client():
    """Return a cached anonymous TvDatafeed client (or None if unavailable)."""
    global _TV_CLIENT
    if not TVDATAFEED_AVAILABLE:
        return None
    if _TV_CLIENT is None:
        try:
            _TV_CLIENT = TvDatafeed()  # nologin / anonymous mode
        except Exception as e:
            logger.warning("TvDatafeed init failed: %s", e)
            return None
    return _TV_CLIENT


def _clean_symbol(symbol: str) -> str:
    """Remove .CA suffix for TradingView (uses raw ticker)."""
    return symbol.upper().replace(".CA", "").strip()


def _create_handler(
    symbol: str,
    interval: str = "1d",
) -> Optional[object]:
    """
    Create a TradingView TA_Handler for an EGX stock.
    
    Args:
        symbol: Raw ticker (without .CA suffix)
        interval: Time interval for analysis
        
    Returns:
        TA_Handler instance or None if not available
    """
    if not TRADINGVIEW_AVAILABLE:
        return None

    # Map interval strings to TradingView constants
    interval_map = {
        "1m": Interval.INTERVAL_1_MINUTE,
        "5m": Interval.INTERVAL_5_MINUTES,
        "15m": Interval.INTERVAL_15_MINUTES,
        "1h": Interval.INTERVAL_1_HOUR,
        "4h": Interval.INTERVAL_4_HOURS,
        "1d": Interval.INTERVAL_1_DAY,
        "1w": Interval.INTERVAL_1_WEEK,
        "1M": Interval.INTERVAL_1_MONTH,
    }

    tv_interval = interval_map.get(interval, Interval.INTERVAL_1_DAY)

    try:
        handler = TA_Handler(
            symbol=_clean_symbol(symbol),
            screener="egypt",
            exchange="EGX",
            interval=tv_interval,
        )
        return handler
    except Exception as e:
        logger.warning("Failed to create TradingView handler for %s: %s", symbol, e)
        return None


def get_tradingview_signals(symbol: str, interval: str = "1d") -> Optional[TechnicalSignals]:
    """
    Get real-time technical analysis signals from TradingView.
    
    Returns a TechnicalSignals schema with:
    - summary: "BUY" / "SELL" / "NEUTRAL" / "STRONG_BUY" / "STRONG_SELL"
    - moving_averages: Dict of MA signals
    - oscillators: Dict of oscillator signals
    - indicators: Dict of raw indicator values
    
    Args:
        symbol: EGX ticker (with or without .CA suffix)
        interval: Time interval (default "1d")
        
    Returns:
        TechnicalSignals or None if unavailable
    """
    handler = _create_handler(symbol, interval)
    if handler is None:
        return None

    try:
        analysis = handler.get_analysis()

        return TechnicalSignals(
            symbol=_clean_symbol(symbol),
            summary=analysis.summary.get("RECOMMENDATION", "NEUTRAL"),
            moving_averages={
                "recommendation": analysis.moving_averages.get("RECOMMENDATION", "NEUTRAL"),
                "buy": analysis.moving_averages.get("BUY", 0),
                "sell": analysis.moving_averages.get("SELL", 0),
                "neutral": analysis.moving_averages.get("NEUTRAL", 0),
            },
            oscillators={
                "recommendation": analysis.oscillators.get("RECOMMENDATION", "NEUTRAL"),
                "buy": analysis.oscillators.get("BUY", 0),
                "sell": analysis.oscillators.get("SELL", 0),
                "neutral": analysis.oscillators.get("NEUTRAL", 0),
            },
            indicators={
                "rsi": analysis.indicators.get("RSI", None),
                "macd": analysis.indicators.get("MACD.macd", None),
                "macd_signal": analysis.indicators.get("MACD.signal", None),
                "ema_20": analysis.indicators.get("EMA20", None),
                "sma_50": analysis.indicators.get("SMA50", None),
                "sma_200": analysis.indicators.get("SMA200", None),
                "bb_upper": analysis.indicators.get("BB.upper", None),
                "bb_lower": analysis.indicators.get("BB.lower", None),
                "atr": analysis.indicators.get("ATR", None),
                "adx": analysis.indicators.get("ADX", None),
                "close": analysis.indicators.get("close", None),
                "volume": analysis.indicators.get("volume", None),
                "change": analysis.indicators.get("change", None),
            },
            source="TradingView",
        )
    except Exception as e:
        logger.warning("TradingView analysis failed for %s: %s", symbol, e)
        return None


def get_tradingview_price(symbol: str) -> Optional[dict]:
    """
    Get current/near-real-time price from TradingView.
    
    This is a lightweight alternative to Mubasher scraping.
    
    Returns:
        Dict with price, change, volume, source — or None
    """
    handler = _create_handler(symbol, "1d")
    if handler is None:
        return None

    try:
        analysis = handler.get_analysis()
        indicators = analysis.indicators

        return {
            "price": indicators.get("close", 0),
            "change": indicators.get("change", 0),
            "change_pct": indicators.get("change", 0),  # TradingView returns as %
            "volume": indicators.get("volume", 0),
            "open": indicators.get("open", 0),
            "high": indicators.get("high", 0),
            "low": indicators.get("low", 0),
            "source": "TradingView (near real-time)",
        }
    except Exception as e:
        logger.warning("TradingView price fetch failed for %s: %s", symbol, e)
        return None


def get_tradingview_ohlcv(
    symbol: str,
    start_date: str,
    end_date: str,
    liquidity_threshold: int = DEFAULT_LOW_LIQUIDITY_THRESHOLD,
    max_records: Optional[int] = 20,
) -> Dict[str, Any]:
    """Fetch daily OHLCV history for an EGX stock from TradingView (tvDatafeed).

    Keyless, near-real-time, best free EGX coverage (returns history even for
    names yfinance has dropped, e.g. ORAS.CA). Returns the SAME dict contract as
    ``y_finance.get_YFin_data_online`` so it slots into the provider chains.

    Look-ahead safety: the window is filtered ``start_date <= d < end_date``
    (END-EXCLUSIVE, matching yfinance). tvDatafeed only returns the most-recent
    ``n_bars`` ending *now*, so for a past ``end_date`` whose window the recent
    bars don't reach, this naturally returns empty and the caller falls through
    to the next provider — it never injects recent data into a past window.
    """
    symbol_clean = _clean_symbol(symbol)
    symbol_upper = f"{symbol_clean}.CA"

    try:
        datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    except (ValueError, TypeError) as e:
        return {"symbol": symbol_upper, "error": f"Invalid date format: {e}. Use YYYY-MM-DD.", "data": []}

    client = _get_tv_client()
    if client is None:
        return {"symbol": symbol_upper, "data": [], "error": "tvdatafeed not available"}

    # Size the request from the window span (business days ≈ span * 5/7) plus a
    # buffer; cap to keep anonymous mode responsive.
    from datetime import datetime as _dt
    span_days = max((end_dt - _dt.strptime(start_date, "%Y-%m-%d")).days, 1)
    n_bars = min(max(int(span_days * 5 / 7) + 10, 30), 5000)

    df = None
    last_exc: Optional[Exception] = None
    for attempt in range(2):
        try:
            df = client.get_hist(
                symbol=symbol_clean, exchange="EGX",
                interval=_TvInterval.in_daily, n_bars=n_bars,
            )
            if df is not None and not df.empty:
                break
        except Exception as e:
            last_exc = e
        import time as _t
        _t.sleep(1.0)

    if df is None or df.empty:
        return {
            "symbol": symbol_upper, "data": [],
            "error": f"No TradingView data for '{symbol_clean}'"
                     + (f" ({last_exc})" if last_exc else ""),
        }

    records = []
    volume_missing_count = 0
    phantom_dropped = 0
    total_volume = 0
    for idx, row in df.iterrows():
        d = idx.strftime("%Y-%m-%d")
        # END-EXCLUSIVE window filter (yfinance parity → look-ahead-safe).
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
        if v == 0 and o == h == l == c:
            phantom_dropped += 1
            continue
        if v == 0:
            volume_missing_count += 1
        total_volume += v
        records.append({"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v})

    if not records:
        return {
            "symbol": symbol_upper, "start_date": start_date, "end_date": end_date,
            "total_records": 0, "data": [],
            "error": f"No TradingView rows for '{symbol_clean}' in window {start_date} → {end_date}",
            "low_liquidity": True, "volume_missing": True, "avg_daily_volume": 0,
            "source": "TradingView",
        }

    if max_records is not None and len(records) > max_records:
        records = records[-max_records:]

    num_records = len(records)
    avg_daily_volume = total_volume / num_records if num_records > 0 else 0
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
        "low_liquidity": avg_daily_volume < liquidity_threshold,
        "liquidity_threshold": liquidity_threshold,
        "volume_missing": volume_missing_count > 0,
        "volume_missing_count": volume_missing_count,
        "phantom_dropped": phantom_dropped,
        "source": "TradingView",
        "errors": None,
    }
