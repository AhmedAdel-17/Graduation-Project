"""
StockTwits Data Source for EGX Social Media Analysis
=====================================================
StockTwits integration for US market fallback compatibility.

Note: StockTwits does NOT cover EGX stocks. This module exists
for API completeness when the system is used for US markets.
For EGX tickers, it returns empty results immediately.
"""

from typing import List
from .schema import SocialPost


def fetch_stocktwits_data(
    ticker: str,
    curr_date: str,
    look_back_days: int = 7,
) -> List[SocialPost]:
    """
    Fetch StockTwits data for a stock.
    
    Returns empty for EGX tickers since StockTwits has no EGX coverage.
    Could be extended for US market usage.
    
    Args:
        ticker: Stock ticker symbol
        curr_date: Current date
        look_back_days: Days of data to fetch
        
    Returns:
        List of SocialPost (empty for EGX tickers)
    """
    ticker = ticker.upper().replace(".CA", "").strip()
    
    # EGX tickers are not on StockTwits
    from tradingagents.dataflows.config import get_config
    config = get_config()
    target_market = config.get("target_market", "US")
    
    if target_market == "EGX":
        return []
    
    # For US markets, could implement StockTwits API here
    # StockTwits API: https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json
    return []
