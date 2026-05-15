from typing import Annotated, Dict, List, Any, Optional
from datetime import datetime
from dateutil.relativedelta import relativedelta
import yfinance as yf
import os
from .stockstats_utils import StockstatsUtils

# =============================================================================
# EGX (Egyptian Exchange) Configuration
# =============================================================================
# EGX symbols on Yahoo Finance use the ".CA" suffix (Cairo Stock Exchange)
# Example: Commercial International Bank = COMI.CA
# Daily bars only - no intraday data assumed
# =============================================================================

# Default liquidity threshold for EGX (average daily volume)
# Stocks with ADV below this threshold are flagged as low liquidity
DEFAULT_LOW_LIQUIDITY_THRESHOLD = 50000  # 50,000 shares/day


def get_YFin_data_online(
    symbol: Annotated[str, "Ticker symbol (EGX format: SYMBOL.CA, e.g., COMI.CA)"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
    liquidity_threshold: Annotated[int, "ADV threshold for low liquidity flag"] = DEFAULT_LOW_LIQUIDITY_THRESHOLD,
) -> Dict[str, Any]:
    """
    Fetch daily OHLCV data for EGX stocks via yfinance.
    
    EGX Symbol Format:
        - Symbols must use the Cairo Stock Exchange suffix: .CA
        - Example: COMI.CA (Commercial International Bank)
        - If no suffix provided, .CA is automatically appended
    
    Returns:
        Dict containing:
        - symbol: The ticker symbol used
        - start_date: Query start date
        - end_date: Query end date
        - total_records: Number of daily bars returned
        - retrieved_at: Timestamp of data retrieval
        - data: List of daily bars with mandatory fields {date, open, high, low, close, volume}
        - avg_daily_volume: Average daily volume over the period
        - low_liquidity: True if ADV < threshold
        - volume_missing: True if any bars have missing/zero volume
        - liquidity_threshold: The threshold used for low_liquidity flag
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
    
    # Normalize EGX symbol format - ensure .CA suffix for Cairo Stock Exchange
    original_symbol = symbol
    symbol_upper = symbol.upper().strip()
    
    if not symbol_upper.endswith(".CA"):
        symbol_upper = f"{symbol_upper}.CA"
        errors.append(f"Symbol normalized: '{original_symbol}' -> '{symbol_upper}' (EGX format)")
    
    # Create ticker object
    ticker = yf.Ticker(symbol_upper)
    
    # Fetch historical data for the specified date range (daily bars only)
    try:
        data = ticker.history(start=start_date, end=end_date, interval="1d")
        
        # Fallback: if date range returns empty, try period-based fetch
        if data.empty:
            # Calculate approximate period needed
            from dateutil.relativedelta import relativedelta
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            days_diff = (end_dt - start_dt).days
            
            if days_diff <= 7:
                period = "1wk"
            elif days_diff <= 30:
                period = "1mo"
            elif days_diff <= 90:
                period = "3mo"
            else:
                period = "6mo"
            
            errors.append(f"Date range returned empty, using period='{period}' fallback")
            data = ticker.history(period=period, interval="1d")

            # Filter out bars after end_date to prevent future data leakage
            # in backtests. period-based fetch is anchored to wall-clock today,
            # not to end_date, so without this filter a historical backtest
            # would see prices from after the simulated trade_date.
            if not data.empty:
                import pandas as pd
                end_dt_ts = pd.Timestamp(end_date)
                if data.index.tz is not None:
                    end_dt_ts = end_dt_ts.tz_localize(data.index.tz)
                data = data[data.index <= end_dt_ts]
            
    except Exception as e:
        return {
            "symbol": symbol_upper,
            "error": f"Failed to fetch data: {e}",
            "data": []
        }
    
    # Check if data is empty
    if data.empty:
        return {
            "symbol": symbol_upper,
            "start_date": start_date,
            "end_date": end_date,
            "total_records": 0,
            "data": [],
            "error": f"No data found for EGX symbol '{symbol_upper}' between {start_date} and {end_date}",
            "low_liquidity": True,
            "volume_missing": True,
            "avg_daily_volume": 0
        }
    
    # Remove timezone info from index for cleaner output
    if data.index.tz is not None:
        data.index = data.index.tz_localize(None)
    
    # Process data into structured format with mandatory OHLCV fields
    ohlcv_records: List[Dict[str, Any]] = []
    volume_missing_count = 0
    total_volume = 0
    
    for idx, row in data.iterrows():
        date_str = idx.strftime("%Y-%m-%d")
        
        # Extract OHLCV - all fields are mandatory
        volume = row.get("Volume", 0)
        if volume is None or volume == 0 or (hasattr(volume, '__nan__') or str(volume) == 'nan'):
            volume = 0
            volume_missing_count += 1
        else:
            volume = int(volume)
        
        total_volume += volume
        
        record = {
            "date": date_str,
            "open": round(float(row.get("Open", 0)), 2),
            "high": round(float(row.get("High", 0)), 2),
            "low": round(float(row.get("Low", 0)), 2),
            "close": round(float(row.get("Close", 0)), 2),
            "volume": volume
        }
        ohlcv_records.append(record)
    
    print(f"DEBUG: y_finance.get_YFin_data_online generated {len(ohlcv_records)} records before truncation", flush=True)
    
    # Limit to max 20 recent records to prevent token overflow (Groq TPM limit 12k/request)
    MAX_RECORDS = 20
    if len(ohlcv_records) > MAX_RECORDS:
        ohlcv_records = ohlcv_records[-MAX_RECORDS:]
        errors.append(f"Result truncated to last {MAX_RECORDS} records to prevent context overflow.")

    # Calculate average daily volume and liquidity flag
    num_records = len(ohlcv_records)
    avg_daily_volume = total_volume / num_records if num_records > 0 else 0
    low_liquidity = avg_daily_volume < liquidity_threshold
    volume_missing = volume_missing_count > 0
    
    # Add liquidity warning to errors if applicable
    if low_liquidity:
        errors.append(
            f"LOW LIQUIDITY WARNING: ADV ({avg_daily_volume:.0f}) < threshold ({liquidity_threshold})"
        )
    
    if volume_missing:
        errors.append(
            f"VOLUME DATA MISSING: {volume_missing_count}/{num_records} bars have zero/missing volume"
        )
    
    # Build structured response
    result = {
        "symbol": symbol_upper,
        "market": "EGX",
        "currency": "EGP",
        "start_date": start_date,
        "end_date": end_date,
        "total_records": num_records,
        "retrieved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data": ohlcv_records,
        "avg_daily_volume": round(avg_daily_volume, 2),
        "low_liquidity": low_liquidity,
        "liquidity_threshold": liquidity_threshold,
        "volume_missing": volume_missing,
        "volume_missing_count": volume_missing_count,
        "errors": errors if errors else None
    }
    
    return result


def get_YFin_data_online_csv(
    symbol: Annotated[str, "Ticker symbol (EGX format: SYMBOL.CA, e.g., COMI.CA)"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Legacy CSV-format wrapper for backward compatibility.
    Returns data as CSV string with headers.
    """
    result = get_YFin_data_online(symbol, start_date, end_date)
    
    if "error" in result and not result.get("data"):
        return f"# Error: {result['error']}\n"
    
    # Build CSV header
    header = f"# Stock data for {result['symbol']} from {start_date} to {end_date}\n"
    header += f"# Market: {result.get('market', 'EGX')} | Currency: {result.get('currency', 'EGP')}\n"
    header += f"# Total records: {result['total_records']}\n"
    header += f"# Avg Daily Volume: {result['avg_daily_volume']:.0f}\n"
    header += f"# Low Liquidity: {result['low_liquidity']}\n"
    header += f"# Volume Missing: {result['volume_missing']}\n"
    header += f"# Data retrieved on: {result['retrieved_at']}\n"
    
    if result.get("errors"):
        for err in result["errors"]:
            header += f"# WARNING: {err}\n"
    
    header += "\n"
    
    # Build CSV body
    csv_lines = ["date,open,high,low,close,volume"]
    for record in result["data"]:
        csv_lines.append(
            f"{record['date']},{record['open']},{record['high']},{record['low']},{record['close']},{record['volume']}"
        )
    
    return header + "\n".join(csv_lines)



def get_stock_stats_indicators_window(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[
        str, "The current trading date you are trading on, YYYY-mm-dd"
    ],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:

    best_ind_params = {
        # Moving Averages
        "close_50_sma": (
            "50 SMA: A medium-term trend indicator. "
            "Usage: Identify trend direction and serve as dynamic support/resistance. "
            "Tips: It lags price; combine with faster indicators for timely signals."
        ),
        "close_200_sma": (
            "200 SMA: A long-term trend benchmark. "
            "Usage: Confirm overall market trend and identify golden/death cross setups. "
            "Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries."
        ),
        "close_10_ema": (
            "10 EMA: A responsive short-term average. "
            "Usage: Capture quick shifts in momentum and potential entry points. "
            "Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals."
        ),
        # MACD Related
        "macd": (
            "MACD: Computes momentum via differences of EMAs. "
            "Usage: Look for crossovers and divergence as signals of trend changes. "
            "Tips: Confirm with other indicators in low-volatility or sideways markets."
        ),
        "macds": (
            "MACD Signal: An EMA smoothing of the MACD line. "
            "Usage: Use crossovers with the MACD line to trigger trades. "
            "Tips: Should be part of a broader strategy to avoid false positives."
        ),
        "macdh": (
            "MACD Histogram: Shows the gap between the MACD line and its signal. "
            "Usage: Visualize momentum strength and spot divergence early. "
            "Tips: Can be volatile; complement with additional filters in fast-moving markets."
        ),
        # Momentum Indicators
        "rsi": (
            "RSI: Measures momentum to flag overbought/oversold conditions. "
            "Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. "
            "Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis."
        ),
        # Volatility Indicators
        "boll": (
            "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. "
            "Usage: Acts as a dynamic benchmark for price movement. "
            "Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals."
        ),
        "boll_ub": (
            "Bollinger Upper Band: Typically 2 standard deviations above the middle line. "
            "Usage: Signals potential overbought conditions and breakout zones. "
            "Tips: Confirm signals with other tools; prices may ride the band in strong trends."
        ),
        "boll_lb": (
            "Bollinger Lower Band: Typically 2 standard deviations below the middle line. "
            "Usage: Indicates potential oversold conditions. "
            "Tips: Use additional analysis to avoid false reversal signals."
        ),
        "atr": (
            "ATR: Averages true range to measure volatility. "
            "Usage: Set stop-loss levels and adjust position sizes based on current market volatility. "
            "Tips: It's a reactive measure, so use it as part of a broader risk management strategy."
        ),
        # Volume-Based Indicators
        "vwma": (
            "VWMA: A moving average weighted by volume. "
            "Usage: Confirm trends by integrating price action with volume data. "
            "Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses."
        ),
        "mfi": (
            "MFI: The Money Flow Index is a momentum indicator that uses both price and volume to measure buying and selling pressure. "
            "Usage: Identify overbought (>80) or oversold (<20) conditions and confirm the strength of trends or reversals. "
            "Tips: Use alongside RSI or MACD to confirm signals; divergence between price and MFI can indicate potential reversals."
        ),
    }

    if indicator not in best_ind_params:
        raise ValueError(
            f"Indicator {indicator} is not supported. Please choose from: {list(best_ind_params.keys())}"
        )

    end_date = curr_date
    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = curr_date_dt - relativedelta(days=look_back_days)

    # Optimized: Get stock data once and calculate indicators for all dates
    try:
        indicator_data = _get_stock_stats_bulk(symbol, indicator, curr_date)
        
        # Generate the date range we need
        current_dt = curr_date_dt
        date_values = []
        
        while current_dt >= before:
            date_str = current_dt.strftime('%Y-%m-%d')
            
            # Look up the indicator value for this date
            if date_str in indicator_data:
                indicator_value = indicator_data[date_str]
            else:
                indicator_value = "N/A: Not a trading day (weekend or holiday)"
            
            date_values.append((date_str, indicator_value))
            current_dt = current_dt - relativedelta(days=1)
        
        # Build the result string
        ind_string = ""
        # Limit to 300 lines
        max_lines = 300
        count = 0
        for date_str, value in date_values:
            if count >= max_lines:
                break
            ind_string += f"{date_str}: {value}\n"
            count += 1
        
    except Exception as e:
        print(f"Error getting bulk stockstats data: {e}")
        # Fallback to original implementation if bulk method fails
        ind_string = ""
        curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        while curr_date_dt >= before:
            indicator_value = get_stockstats_indicator(
                symbol, indicator, curr_date_dt.strftime("%Y-%m-%d")
            )
            ind_string += f"{curr_date_dt.strftime('%Y-%m-%d')}: {indicator_value}\n"
            curr_date_dt = curr_date_dt - relativedelta(days=1)

    result_str = (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {end_date}:\n\n"
        + ind_string
        + "\n\n"
        + best_ind_params.get(indicator, "No description available.")
    )

    print(f"DEBUG: y_finance.get_stock_stats_indicators_window returning string of length {len(result_str)}", flush=True)
    
    # SAFETY NET: Global truncation for this function output
    if len(result_str) > 2000:
        result_str = result_str[:2000] + "\n...[TRUNCATED to 2000 chars]..."
        
    return result_str


def _get_stock_stats_bulk(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to calculate"],
    curr_date: Annotated[str, "current date for reference"]
) -> dict:
    """
    Optimized bulk calculation of stock stats indicators.
    Fetches data once and calculates indicator for all available dates.
    Returns dict mapping date strings to indicator values.
    """
    from .config import get_config
    import pandas as pd
    from stockstats import wrap
    import os
    
    config = get_config()
    online = config["data_vendors"]["technical_indicators"] != "local"
    
    if not online:
        # Local data path
        try:
            data = pd.read_csv(
                os.path.join(
                    config.get("data_cache_dir", "data"),
                    f"{symbol}-YFin-data-2015-01-01-2025-03-25.csv",
                )
            )
            df = wrap(data)
        except FileNotFoundError:
            raise Exception("Stockstats fail: Yahoo Finance data not fetched yet!")
    else:
        # Online data fetching with caching
        # Use curr_date (the simulated trade date) as end_date, NOT today.
        # Using today_date would leak future indicator values into backtests.
        curr_date_dt = pd.to_datetime(curr_date)

        end_date = curr_date_dt
        start_date = end_date - pd.DateOffset(years=15)
        start_date_str = start_date.strftime("%Y-%m-%d")
        end_date_str = end_date.strftime("%Y-%m-%d")
        
        os.makedirs(config["data_cache_dir"], exist_ok=True)
        
        data_file = os.path.join(
            config["data_cache_dir"],
            f"{symbol}-YFin-data-{start_date_str}-{end_date_str}.csv",
        )
        
        if os.path.exists(data_file):
            data = pd.read_csv(data_file)
            data["Date"] = pd.to_datetime(data["Date"])
        else:
            data = yf.download(
                symbol,
                start=start_date_str,
                end=end_date_str,
                multi_level_index=False,
                progress=False,
                auto_adjust=True,
            )
            data = data.reset_index()
            data.to_csv(data_file, index=False)
        
        df = wrap(data)
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
    
    # Calculate the indicator for all rows at once
    df[indicator]  # This triggers stockstats to calculate the indicator
    
    # Create a dictionary mapping date strings to indicator values
    result_dict = {}
    for _, row in df.iterrows():
        date_str = row["Date"]
        indicator_value = row[indicator]
        
        # Handle NaN/None values
        if pd.isna(indicator_value):
            result_dict[date_str] = "N/A"
        else:
            result_dict[date_str] = str(indicator_value)
    
    return result_dict


def get_stockstats_indicator(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[
        str, "The current trading date you are trading on, YYYY-mm-dd"
    ],
) -> str:

    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    curr_date = curr_date_dt.strftime("%Y-%m-%d")

    try:
        indicator_value = StockstatsUtils.get_stock_stats(
            symbol,
            indicator,
            curr_date,
        )
    except Exception as e:
        print(
            f"Error getting stockstats indicator data for indicator {indicator} on {curr_date}: {e}"
        )
        return ""

    return str(indicator_value)


def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date (not used for yfinance)"] = None
):
    """Get balance sheet data from yfinance."""
    try:
        ticker_obj = yf.Ticker(ticker.upper())
        
        if freq.lower() == "quarterly":
            data = ticker_obj.quarterly_balance_sheet
        else:
            data = ticker_obj.balance_sheet
            
        if data.empty:
            return f"No balance sheet data found for symbol '{ticker}'"
            
        # Convert to CSV string for consistency with other functions
        csv_string = data.to_csv()
        
        # Add header information
        header = f"# Balance Sheet data for {ticker.upper()} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        return header + csv_string
        
    except Exception as e:
        return f"Error retrieving balance sheet for {ticker}: {str(e)}"


def get_cashflow(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date (not used for yfinance)"] = None
):
    """Get cash flow data from yfinance."""
    try:
        ticker_obj = yf.Ticker(ticker.upper())
        
        if freq.lower() == "quarterly":
            data = ticker_obj.quarterly_cashflow
        else:
            data = ticker_obj.cashflow
            
        if data.empty:
            return f"No cash flow data found for symbol '{ticker}'"
            
        # Convert to CSV string for consistency with other functions
        csv_string = data.to_csv()
        
        # Add header information
        header = f"# Cash Flow data for {ticker.upper()} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        return header + csv_string
        
    except Exception as e:
        return f"Error retrieving cash flow for {ticker}: {str(e)}"


def get_income_statement(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date (not used for yfinance)"] = None
):
    """Get income statement data from yfinance."""
    try:
        ticker_obj = yf.Ticker(ticker.upper())
        
        if freq.lower() == "quarterly":
            data = ticker_obj.quarterly_income_stmt
        else:
            data = ticker_obj.income_stmt
            
        if data.empty:
            return f"No income statement data found for symbol '{ticker}'"
            
        # Convert to CSV string for consistency with other functions
        csv_string = data.to_csv()
        
        # Add header information
        header = f"# Income Statement data for {ticker.upper()} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        return header + csv_string
        
    except Exception as e:
        return f"Error retrieving income statement for {ticker}: {str(e)}"


def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol of the company"]
):
    """Get insider transactions data from yfinance."""
    try:
        ticker_obj = yf.Ticker(ticker.upper())
        data = ticker_obj.insider_transactions
        
        if data is None or data.empty:
            return f"No insider transactions data found for symbol '{ticker}'"
            
        # Convert to CSV string for consistency with other functions
        csv_string = data.to_csv()
        
        # Add header information
        header = f"# Insider Transactions data for {ticker.upper()}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        return header + csv_string
        
    except Exception as e:
        return f"Error retrieving insider transactions for {ticker}: {str(e)}"

def get_fundamentals_summary(
    ticker: Annotated[str, "ticker symbol of the company, e.g. COMI.CA"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None
) -> str:
    """
    Get a comprehensive summary of fundamental data from yfinance.
    Aggregates available Balance Sheet, Income Statement, Cash Flow, and Key Ratios.
    """
    try:
        # Normalize symbol for yfinance
        symbol_upper = ticker.upper().strip()
        if not symbol_upper.endswith(".CA") and not symbol_upper.endswith(".EG"):
             # Simple heuristic: if it's 3-4 letters and not US, assume EGX for this context
             # But better to respect input. If user passes COMI, we might need COMI.CA
             pass
        
        ticker_obj = yf.Ticker(symbol_upper)
        
        # 1. Info / Ratios
        info = ticker_obj.info
        
        # Extract key metrics if available
        ratios = {
            "Trailing PE": info.get("trailingPE"),
            "Forward PE": info.get("forwardPE"),
            "Price to Book": info.get("priceToBook"),
            "Return on Equity": info.get("returnOnEquity"),
            "Debt to Equity": info.get("debtToEquity"),
            "Profit Margins": info.get("profitMargins"),
            "Revenue Growth": info.get("revenueGrowth"),
            "Market Cap": info.get("marketCap"),
            "Currency": info.get("currency"),
            "Sector": info.get("sector"),
            "Industry": info.get("industry")
        }
        
        # 2. Financial Statements (Most recent annual)
        # We use the defaults (annual) to keep it summarized
        inc = ticker_obj.income_stmt
        bal = ticker_obj.balance_sheet
        cf = ticker_obj.cashflow
        
        # Format as a readable report
        report = []
        report.append(f"# Fundamental Summary for {symbol_upper}")
        report.append(f"**Sector:** {ratios['Sector']} | **Industry:** {ratios['Industry']} | **Currency:** {ratios['Currency']}")
        report.append(f"**Market Cap:** {ratios['Market Cap']:,} {ratios['Currency']}" if ratios['Market Cap'] else "**Market Cap:** N/A")
        report.append("\n## Key Ratios")
        for k, v in ratios.items():
            if k not in ["Sector", "Industry", "Currency", "Market Cap"]:
                val = f"{v:.4f}" if isinstance(v, float) else str(v)
                report.append(f"- **{k}:** {val}")
        
        report.append("\n## Recent Income Statement (Top Items)")
        if not inc.empty:
            # Get most recent year
            recent_date = inc.columns[0]
            report.append(f"**Period Ending:** {recent_date.date()}")
            # Pick a few key rows if they exist
            for key in ["Total Revenue", "Gross Profit", "Net Income", "EBITDA", "Operating Income"]:
                 if key in inc.index:
                      val = inc.loc[key, recent_date]
                      report.append(f"- **{key}:** {val:,.0f}")
        else:
            report.append("(No Income Statement data found)")
            
        report.append("\n## Recent Balance Sheet (Top Items)")
        if not bal.empty:
            recent_date = bal.columns[0]
            report.append(f"**Period Ending:** {recent_date.date()}")
            for key in ["Total Assets", "Total Liabilities Net Minority Interest", "Total Equity Gross Minority Interest", "Cash And Cash Equivalents"]:
                 if key in bal.index:
                      val = bal.loc[key, recent_date]
                      report.append(f"- **{key}:** {val:,.0f}")
        else:
             report.append("(No Balance Sheet data found)")

        return "\n".join(report)

    except Exception as e:
        return f"Error fetching fundamental summary for {ticker}: {str(e)}"