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
from typing import Optional
from .schemas import TechnicalSignals

logger = logging.getLogger("tradingagents.tradingview")

# Try to import tradingview-ta
try:
    from tradingview_ta import TA_Handler, Interval, Exchange
    TRADINGVIEW_AVAILABLE = True
except ImportError:
    TRADINGVIEW_AVAILABLE = False
    logger.info(
        "tradingview-ta not installed. TradingView provider disabled. "
        "Install with: pip install tradingview-ta"
    )


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
