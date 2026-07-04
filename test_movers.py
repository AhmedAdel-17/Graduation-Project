import os
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from tradingagents.default_config import EGX_TICKERS

def get_market_movers():
    # 1. Day movers from yfinance
    try:
        data = yf.download(EGX_TICKERS, period="5d")
        closes = data['Close']
        # Forward fill to handle any NAs
        closes = closes.ffill()
        if len(closes) >= 2:
            last = closes.iloc[-1]
            prev = closes.iloc[-2]
            day_change = ((last - prev) / prev) * 100
        else:
            day_change = pd.Series(index=EGX_TICKERS, dtype=float)
    except Exception as e:
        print(f"Error fetching day data: {e}")
        day_change = pd.Series(index=EGX_TICKERS, dtype=float)

    # Historical from CSVs
    csv_dir = os.path.join(os.path.dirname(__file__), "data", "egx30_ohlcv")
    
    week_change = {}
    month_change = {}
    year_change = {}
    
    for ticker in EGX_TICKERS:
        csv_path = os.path.join(csv_dir, f"{ticker}.csv")
        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path)
                if not df.empty and len(df) >= 2:
                    last_close = df['close'].iloc[-1]
                    
                    # Week (~5 days)
                    if len(df) >= 6:
                        w_close = df['close'].iloc[-6]
                        week_change[ticker] = ((last_close - w_close) / w_close) * 100
                    
                    # Month (~21 days)
                    if len(df) >= 22:
                        m_close = df['close'].iloc[-22]
                        month_change[ticker] = ((last_close - m_close) / m_close) * 100
                    
                    # Year (~252 days)
                    if len(df) >= 253:
                        y_close = df['close'].iloc[-253]
                        year_change[ticker] = ((last_close - y_close) / y_close) * 100
            except Exception as e:
                pass

    # Compile results
    def get_top(change_series, n=5):
        if isinstance(change_series, dict):
            s = pd.Series(change_series).dropna()
        else:
            s = change_series.dropna()
        
        # Remove any 0s or infs if necessary, but infs should be fine
        if len(s) == 0:
            return [], []
            
        gainers = s.nlargest(n)
        losers = s.nsmallest(n)
        
        g_list = [{"ticker": k, "change": float(v)} for k, v in gainers.items()]
        l_list = [{"ticker": k, "change": float(v)} for k, v in losers.items()]
        return g_list, l_list

    d_g, d_l = get_top(day_change)
    w_g, w_l = get_top(week_change)
    m_g, m_l = get_top(month_change)
    y_g, y_l = get_top(year_change)
    
    return {
        "day": {"gainers": d_g, "losers": d_l},
        "week": {"gainers": w_g, "losers": w_l},
        "month": {"gainers": m_g, "losers": m_l},
        "year": {"gainers": y_g, "losers": y_l},
    }

if __name__ == "__main__":
    print(get_market_movers())
