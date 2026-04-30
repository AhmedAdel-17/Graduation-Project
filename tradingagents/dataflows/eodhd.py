"""
EODHD.com API Integration for EGX Stock Data
============================================
EODHD provides reliable historical and real-time stock data.
EGX symbols use format: SYMBOL.CA (Cairo Stock Exchange)

API Documentation: https://eodhd.com/financial-apis/
"""

import os
import requests
from typing import Dict, Any, List, Optional
from datetime import datetime
from dateutil.relativedelta import relativedelta

# Get API key from environment or config
EODHD_API_KEY = os.getenv("EODHD_API_KEY", "696cff318de733.38444726")
EODHD_BASE_URL = "https://eodhd.com/api"

# Default liquidity threshold for EGX
DEFAULT_LOW_LIQUIDITY_THRESHOLD = 50000  # 50,000 shares/day


def get_eodhd_stock_data(
    symbol: str,
    start_date: str,
    end_date: str,
    exchange: str = "CA",  # Cairo Stock Exchange
    liquidity_threshold: int = DEFAULT_LOW_LIQUIDITY_THRESHOLD,
) -> Dict[str, Any]:
    """
    Fetch daily OHLCV data for EGX stocks via EODHD API.
    
    EGX Symbol Format:
        - Symbols must use the Cairo Stock Exchange code: CA
        - Example: COMI.CA (Commercial International Bank)
        - If no exchange suffix provided, .CA is automatically appended
    
    Args:
        symbol: Stock ticker (e.g., "COMI" or "COMI.CA")
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format
        exchange: Exchange code (default: CA for Cairo)
        liquidity_threshold: ADV threshold for low liquidity flag
    
    Returns:
        Dict containing:
        - symbol: The ticker symbol used
        - start_date: Query start date
        - end_date: Query end date
        - total_records: Number of daily bars returned
        - retrieved_at: Timestamp of data retrieval
        - data: List of daily bars with {date, open, high, low, close, volume}
        - avg_daily_volume: Average daily volume over the period
        - low_liquidity: True if ADV < threshold
        - volume_missing: True if any bars have missing/zero volume
        - errors: List of any issues encountered
    """
    errors = []
    
    # Validate date format
    try:
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError as e:
        return {
            "symbol": symbol,
            "error": f"Invalid date format: {e}. Use YYYY-MM-DD.",
            "data": []
        }
    
    # Normalize symbol format
    original_symbol = symbol
    symbol_upper = symbol.upper().strip()
    
    # Extract exchange code if present, otherwise use default
    if "." in symbol_upper:
        parts = symbol_upper.rsplit(".", 1)
        symbol_code = parts[0]
        exchange_code = parts[1]
    else:
        symbol_code = symbol_upper
        exchange_code = exchange
    
    # Full symbol for EODHD
    full_symbol = f"{symbol_code}.{exchange_code}"
    
    if original_symbol.upper() != full_symbol:
        errors.append(f"Symbol normalized: '{original_symbol}' -> '{full_symbol}'")
    
    # Build API URL
    url = f"{EODHD_BASE_URL}/eod/{full_symbol}"
    
    params = {
        "api_token": EODHD_API_KEY,
        "from": start_date,
        "to": end_date,
        "period": "d",  # Daily
        "fmt": "json"
    }
    
    # Make API request
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.Timeout:
        return {
            "symbol": full_symbol,
            "error": "Request timed out",
            "data": []
        }
    except requests.exceptions.HTTPError as e:
        return {
            "symbol": full_symbol,
            "error": f"HTTP error: {e}",
            "data": []
        }
    except requests.exceptions.RequestException as e:
        return {
            "symbol": full_symbol,
            "error": f"Request failed: {e}",
            "data": []
        }
    except ValueError as e:
        return {
            "symbol": full_symbol,
            "error": f"Invalid JSON response: {e}",
            "data": []
        }
    
    # Check for empty data
    if not data or len(data) == 0:
        return {
            "symbol": full_symbol,
            "start_date": start_date,
            "end_date": end_date,
            "total_records": 0,
            "data": [],
            "error": f"No data found for '{full_symbol}' between {start_date} and {end_date}",
            "low_liquidity": True,
            "volume_missing": True,
            "avg_daily_volume": 0
        }
    
    # Check for API error response
    if isinstance(data, dict) and "error" in data:
        return {
            "symbol": full_symbol,
            "error": data.get("error", "Unknown API error"),
            "data": []
        }
    
    # Process data into standardized format
    processed_data = []
    volumes = []
    volume_missing = False
    
    for record in data:
        try:
            date = record.get("date", "")
            open_price = float(record.get("open", 0))
            high_price = float(record.get("high", 0))
            low_price = float(record.get("low", 0))
            close_price = float(record.get("close", 0))
            volume = int(record.get("volume", 0))
            
            # Track volume for liquidity check
            if volume == 0:
                volume_missing = True
            else:
                volumes.append(volume)
            
            processed_data.append({
                "date": date,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
            })
        except (ValueError, TypeError) as e:
            errors.append(f"Error parsing record: {e}")
            continue
    
    # Calculate average daily volume
    avg_daily_volume = sum(volumes) / len(volumes) if volumes else 0
    
    # Determine liquidity status
    low_liquidity = avg_daily_volume < liquidity_threshold
    
    return {
        "symbol": full_symbol,
        "start_date": start_date,
        "end_date": end_date,
        "total_records": len(processed_data),
        "retrieved_at": datetime.now().isoformat(),
        "data": processed_data,
        "avg_daily_volume": round(avg_daily_volume, 2),
        "low_liquidity": low_liquidity,
        "volume_missing": volume_missing,
        "liquidity_threshold": liquidity_threshold,
        "source": "EODHD.com",
        "errors": errors if errors else None
    }


def get_eodhd_indicators(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int = 90,
) -> Dict[str, Any]:
    """
    Calculate technical indicators from EODHD price data.
    
    This function fetches price data and calculates indicators locally.
    Supported indicators: SMA, EMA, RSI, MACD, BBANDS
    
    Args:
        symbol: Stock ticker
        indicator: Indicator name (e.g., "RSI", "SMA", "MACD")
        curr_date: Current date
        look_back_days: Days of data to fetch
    
    Returns:
        Dict with indicator values
    """
    # Calculate date range
    end_date = curr_date
    start_date_dt = datetime.strptime(curr_date, "%Y-%m-%d") - relativedelta(days=look_back_days)
    start_date = start_date_dt.strftime("%Y-%m-%d")
    
    # Fetch price data
    price_data = get_eodhd_stock_data(symbol, start_date, end_date)
    
    if price_data.get("error") or not price_data.get("data"):
        return {
            "symbol": symbol,
            "indicator": indicator,
            "error": price_data.get("error", "No data available"),
            "values": []
        }
    
    # Extract close prices
    closes = [d["close"] for d in price_data["data"]]
    dates = [d["date"] for d in price_data["data"]]
    
    # Calculate indicator
    indicator_upper = indicator.upper()
    
    if indicator_upper == "SMA" or indicator_upper.startswith("SMA_"):
        period = 20  # Default period
        if "_" in indicator_upper:
            try:
                period = int(indicator_upper.split("_")[1])
            except:
                pass
        
        values = _calculate_sma(closes, period)
        
    elif indicator_upper == "EMA" or indicator_upper.startswith("EMA_"):
        period = 20
        if "_" in indicator_upper:
            try:
                period = int(indicator_upper.split("_")[1])
            except:
                pass
        
        values = _calculate_ema(closes, period)
        
    elif indicator_upper == "RSI":
        values = _calculate_rsi(closes, 14)
        
    elif indicator_upper == "MACD":
        macd_line, signal_line, histogram = _calculate_macd(closes)
        return {
            "symbol": symbol,
            "indicator": "MACD",
            "values": {
                "macd_line": macd_line[-10:] if macd_line else [],
                "signal_line": signal_line[-10:] if signal_line else [],
                "histogram": histogram[-10:] if histogram else [],
            },
            "source": "EODHD.com + Local Calculation"
        }
    else:
        return {
            "symbol": symbol,
            "indicator": indicator,
            "error": f"Unsupported indicator: {indicator}",
            "supported": ["SMA", "SMA_N", "EMA", "EMA_N", "RSI", "MACD"]
        }
    
    return {
        "symbol": symbol,
        "indicator": indicator,
        "values": values[-10:] if values else [],  # Return last 10 values
        "dates": dates[-10:] if dates else [],
        "source": "EODHD.com + Local Calculation"
    }


def _calculate_sma(prices: List[float], period: int) -> List[float]:
    """Calculate Simple Moving Average."""
    if len(prices) < period:
        return []
    
    sma = []
    for i in range(period - 1, len(prices)):
        avg = sum(prices[i - period + 1:i + 1]) / period
        sma.append(round(avg, 4))
    
    return sma


def _calculate_ema(prices: List[float], period: int) -> List[float]:
    """Calculate Exponential Moving Average."""
    if len(prices) < period:
        return []
    
    multiplier = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]  # Start with SMA
    
    for price in prices[period:]:
        ema.append(round((price - ema[-1]) * multiplier + ema[-1], 4))
    
    return ema


def _calculate_rsi(prices: List[float], period: int = 14) -> List[float]:
    """Calculate Relative Strength Index."""
    if len(prices) < period + 1:
        return []
    
    deltas = [prices[i + 1] - prices[i] for i in range(len(prices) - 1)]
    
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    rsi = []
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
        if avg_loss == 0:
            rsi.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi.append(round(100 - (100 / (1 + rs)), 2))
    
    return rsi


def _calculate_macd(prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """Calculate MACD (Moving Average Convergence Divergence)."""
    if len(prices) < slow + signal:
        return [], [], []
    
    ema_fast = _calculate_ema(prices, fast)
    ema_slow = _calculate_ema(prices, slow)
    
    # Align EMAs
    offset = slow - fast
    ema_fast = ema_fast[offset:]
    
    # Calculate MACD line
    macd_line = [round(f - s, 4) for f, s in zip(ema_fast, ema_slow)]
    
    # Calculate signal line (EMA of MACD)
    signal_line = _calculate_ema(macd_line, signal)
    
    # Calculate histogram
    offset = len(macd_line) - len(signal_line)
    histogram = [round(m - s, 4) for m, s in zip(macd_line[offset:], signal_line)]
    
    return macd_line, signal_line, histogram


# Convenience wrapper for interface compatibility
def get_stock_data_eodhd(symbol: str, start_date: str, end_date: str) -> Dict[str, Any]:
    """Wrapper function for interface compatibility."""
    return get_eodhd_stock_data(symbol, start_date, end_date)


def get_indicators_eodhd(symbol: str, indicator: str, curr_date: str, look_back_days: int = 90) -> Dict[str, Any]:
    """Wrapper function for interface compatibility."""
    return get_eodhd_indicators(symbol, indicator, curr_date, look_back_days)
