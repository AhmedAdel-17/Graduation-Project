from langchain_core.tools import tool
from typing import Annotated
from tradingagents.dataflows.interface import route_to_vendor

@tool
def get_news(
    ticker: Annotated[str, "Ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve news data for a given ticker symbol.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted string containing news data
    """
    # Clamp end_date to trade_date — prevents the LLM from accidentally
    # requesting news beyond the backtest trade date.
    from tradingagents.dataflows.config import get_config as _get_cfg
    _trade_date = _get_cfg().get("trade_date")
    if _trade_date and end_date > _trade_date:
        end_date = _trade_date
    return route_to_vendor("get_news", ticker, start_date, end_date)

@tool
def get_global_news(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Number of days to look back"] = 7,
    limit: Annotated[int, "Maximum number of articles to return"] = 5,
) -> str:
    """
    Retrieve global news data.
    Uses the configured news_data vendor.
    Args:
        curr_date (str): Current date in yyyy-mm-dd format
        look_back_days (int): Number of days to look back (default 7)
        limit (int): Maximum number of articles to return (default 5)
    Returns:
        str: A formatted string containing global news data
    """
    # Clamp curr_date to trade_date — same guard as get_egx_company_news.
    from tradingagents.dataflows.config import get_config as _get_cfg
    _trade_date = _get_cfg().get("trade_date")
    if _trade_date and curr_date > _trade_date:
        curr_date = _trade_date
    return route_to_vendor("get_global_news", curr_date, look_back_days, limit)

@tool
def get_insider_sentiment(
    ticker: Annotated[str, "ticker symbol for the company"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
) -> str:
    """
    Retrieve insider sentiment information about a company.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A report of insider sentiment data
    """
    return route_to_vendor("get_insider_sentiment", ticker, curr_date)

@tool
def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
) -> str:
    """
    Retrieve insider transaction information about a company.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A report of insider transaction data
    """
    return route_to_vendor("get_insider_transactions", ticker, curr_date)


# =============================================================================
# EGX (Egyptian Exchange) News Tools
# =============================================================================
# Access CSV and text-based EGX news data
# Supports Arabic and English content
# =============================================================================

from tradingagents.dataflows.local import (
    get_egx_news_from_csv,
    get_egx_news_from_text,
    get_egx_news_combined,
)
import json


@tool
def get_egx_company_news(
    ticker: Annotated[str, "EGX ticker symbol (e.g., COMI, EAST)"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Number of days to look back"] = 7,
) -> str:
    """
    Retrieve EGX company news from local CSV and text files.
    Supports Arabic and English content.

    Args:
        ticker: EGX ticker symbol
        curr_date: Current date
        look_back_days: How many days of news to retrieve

    Returns:
        str: JSON-formatted news with language and source metadata
    """
    # Clamp curr_date to trade_date to prevent future news leakage
    from tradingagents.dataflows.config import get_config
    _trade_date = get_config().get("trade_date")
    if _trade_date and curr_date > _trade_date:
        curr_date = _trade_date
    result = get_egx_news_combined(ticker, curr_date, look_back_days)
    return json.dumps(result, indent=2, ensure_ascii=False, default=str)


@tool
def get_egx_market_news(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Number of days to look back"] = 7,
) -> str:
    """
    Retrieve EGX market-wide news (not company-specific).
    Supports Arabic and English content.

    Args:
        curr_date: Current date
        look_back_days: How many days of news to retrieve

    Returns:
        str: JSON-formatted market news with language and source metadata
    """
    # Clamp curr_date to trade_date to prevent future news leakage
    from tradingagents.dataflows.config import get_config
    _trade_date = get_config().get("trade_date")
    if _trade_date and curr_date > _trade_date:
        curr_date = _trade_date
    result = get_egx_news_from_csv("ALL", curr_date, look_back_days)
    return json.dumps(result, indent=2, ensure_ascii=False, default=str)

