"""
egxpy Integration for Native EGX Stock Data
============================================
egxpy is a dedicated Python library for Egyptian Exchange (EGX) data.
GitHub: https://github.com/egxlytics/egxpy

Installation:
    pip install git+https://github.com/egxlytics/egxpy.git

This module wraps egxpy functions for use with the TradingAgents framework.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
import pandas as pd

# Default liquidity threshold for EGX
DEFAULT_LOW_LIQUIDITY_THRESHOLD = 50000  # 50,000 shares/day

# Flag to track if egxpy is available
EGXPY_AVAILABLE = False

try:
    from egxpy.download import get_OHLCV_data, get_EGXdata, get_EGX_intraday_data
    EGXPY_AVAILABLE = True
except ImportError:
    # egxpy not installed - functions will return helpful error messages
    pass


def get_egxpy_stock_data(
    symbol: str,
    start_date: str,
    end_date: str,
    interval: str = "Daily",
    liquidity_threshold: int = DEFAULT_LOW_LIQUIDITY_THRESHOLD,
) -> Dict[str, Any]:
    """
    Fetch daily OHLCV data for EGX stocks via egxpy.
    
    This is the RECOMMENDED method for EGX data as egxpy is specifically
    designed for the Egyptian Exchange.
    
    Args:
        symbol: EGX stock symbol (e.g., "COMI", "EAST", "HRHO")
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format
        interval: "Daily", "Weekly", or "Monthly"
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
    
    # Check if egxpy is available
    if not EGXPY_AVAILABLE:
        return {
            "symbol": symbol,
            "error": "egxpy not installed. Install with: pip install git+https://github.com/egxlytics/egxpy.git",
            "data": [],
            "fallback": "eodhd"  # Suggest fallback
        }
    
    # Validate date format
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError as e:
        return {
            "symbol": symbol,
            "error": f"Invalid date format: {e}. Use YYYY-MM-DD.",
            "data": []
        }
    
    # Normalize symbol (remove .CA suffix if present, uppercase)
    symbol_clean = symbol.upper().strip()
    if symbol_clean.endswith(".CA"):
        symbol_clean = symbol_clean[:-3]
    
    # Calculate number of bars needed (approximate)
    days_diff = (end_dt - start_dt).days
    if interval == "Weekly":
        n_bars = (days_diff // 7) + 10  # Add buffer
    elif interval == "Monthly":
        n_bars = (days_diff // 30) + 5
    else:  # Daily
        n_bars = days_diff + 30  # Add buffer for holidays/weekends
    
    # Fetch data using egxpy
    try:
        df = get_OHLCV_data(
            symbol=symbol_clean,
            exchange="EGX",
            interval=interval,
            n_bars=n_bars
        )
    except Exception as e:
        return {
            "symbol": symbol_clean,
            "error": f"egxpy fetch failed: {str(e)}",
            "data": [],
            "fallback": "eodhd"
        }
    
    # Check for empty data
    if df is None or df.empty:
        return {
            "symbol": symbol_clean,
            "start_date": start_date,
            "end_date": end_date,
            "total_records": 0,
            "data": [],
            "error": f"No data found for EGX symbol '{symbol_clean}'",
            "low_liquidity": True,
            "volume_missing": True,
            "avg_daily_volume": 0
        }
    
    # Filter by date range if needed
    if 'datetime' in df.columns:
        df['date'] = pd.to_datetime(df['datetime']).dt.date
    elif df.index.name == 'datetime' or isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index()
        df['date'] = pd.to_datetime(df['datetime']).dt.date
    else:
        # Try to find a date column
        for col in df.columns:
            if 'date' in col.lower() or 'time' in col.lower():
                df['date'] = pd.to_datetime(df[col]).dt.date
                break
    
    if 'date' in df.columns:
        df = df[(df['date'] >= start_dt.date()) & (df['date'] <= end_dt.date())]
    
    # Normalize column names
    df.columns = df.columns.str.lower().str.strip()
    
    # Process data into standardized format
    processed_data = []
    volumes = []
    volume_missing = False
    
    for _, row in df.iterrows():
        try:
            # Get date
            if 'date' in row:
                record_date = str(row['date'])
            else:
                record_date = str(row.name) if hasattr(row, 'name') else ""
            
            # Get OHLCV values
            open_price = float(row.get('open', row.get('o', 0)))
            high_price = float(row.get('high', row.get('h', 0)))
            low_price = float(row.get('low', row.get('l', 0)))
            close_price = float(row.get('close', row.get('c', 0)))
            volume = int(row.get('volume', row.get('v', 0)))
            
            # Track volume for liquidity check
            if volume == 0:
                volume_missing = True
            else:
                volumes.append(volume)
            
            processed_data.append({
                "date": record_date,
                "open": round(open_price, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "close": round(close_price, 2),
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
        "symbol": symbol_clean,
        "market": "EGX",
        "currency": "EGP",
        "start_date": start_date,
        "end_date": end_date,
        "total_records": len(processed_data),
        "retrieved_at": datetime.now().isoformat(),
        "data": processed_data,
        "avg_daily_volume": round(avg_daily_volume, 2),
        "low_liquidity": low_liquidity,
        "volume_missing": volume_missing,
        "liquidity_threshold": liquidity_threshold,
        "source": "egxpy (native EGX)",
        "errors": errors if errors else None
    }


def get_egxpy_multi_stock_data(
    symbols: List[str],
    start_date: str,
    end_date: str,
    interval: str = "Daily",
) -> Dict[str, Any]:
    """
    Fetch historical close prices for multiple EGX stocks.
    
    Uses egxpy.download.get_EGXdata for efficient batch fetching.
    
    Args:
        symbols: List of EGX stock symbols
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format
        interval: "Daily", "Weekly", or "Monthly"
    
    Returns:
        Dict with data for each symbol
    """
    if not EGXPY_AVAILABLE:
        return {
            "error": "egxpy not installed",
            "data": {}
        }
    
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError as e:
        return {"error": f"Invalid date format: {e}", "data": {}}
    
    # Clean symbols
    symbols_clean = [s.upper().replace(".CA", "").strip() for s in symbols]
    
    try:
        df = get_EGXdata(
            stock_list=symbols_clean,
            interval=interval,
            start=start_dt,
            end=end_dt
        )
    except Exception as e:
        return {
            "error": f"egxpy batch fetch failed: {str(e)}",
            "data": {}
        }
    
    return {
        "symbols": symbols_clean,
        "start_date": start_date,
        "end_date": end_date,
        "data": df.to_dict() if df is not None else {},
        "source": "egxpy (native EGX)"
    }


def get_egxpy_intraday_data(
    symbol: str,
    n_bars: int = 100,
) -> Dict[str, Any]:
    """
    Fetch intraday data for an EGX stock.
    
    Note: Intraday data availability depends on market hours.
    
    Args:
        symbol: EGX stock symbol
        n_bars: Number of intraday bars to fetch
    
    Returns:
        Dict with intraday OHLCV data
    """
    if not EGXPY_AVAILABLE:
        return {
            "symbol": symbol,
            "error": "egxpy not installed",
            "data": []
        }
    
    symbol_clean = symbol.upper().replace(".CA", "").strip()
    
    try:
        df = get_EGX_intraday_data(
            symbol=symbol_clean,
            n_bars=n_bars
        )
    except Exception as e:
        return {
            "symbol": symbol_clean,
            "error": f"Intraday fetch failed: {str(e)}",
            "data": []
        }
    
    if df is None or df.empty:
        return {
            "symbol": symbol_clean,
            "error": "No intraday data available",
            "data": []
        }
    
    # Convert to list of dicts
    df.columns = df.columns.str.lower()
    data = df.to_dict('records')
    
    return {
        "symbol": symbol_clean,
        "market": "EGX",
        "total_records": len(data),
        "data": data,
        "source": "egxpy (native EGX intraday)"
    }


# Convenience wrapper for interface compatibility
def get_stock_data_egxpy(symbol: str, start_date: str, end_date: str) -> Dict[str, Any]:
    """Wrapper function for interface compatibility."""
    return get_egxpy_stock_data(symbol, start_date, end_date)


def is_egxpy_available() -> bool:
    """Check if egxpy is installed and available."""
    return EGXPY_AVAILABLE
