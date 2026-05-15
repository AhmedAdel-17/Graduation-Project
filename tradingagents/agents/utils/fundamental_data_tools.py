from langchain_core.tools import tool
from typing import Annotated
from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_fundamentals(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
) -> str:
    """
    Retrieve comprehensive fundamental data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing comprehensive fundamental data
    """
    return route_to_vendor("get_fundamentals", ticker, curr_date)


@tool
def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve balance sheet data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing balance sheet data
    """
    return route_to_vendor("get_balance_sheet", ticker, freq, curr_date)


@tool
def get_cashflow(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve cash flow statement data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing cash flow statement data
    """
    return route_to_vendor("get_cashflow", ticker, freq, curr_date)


@tool
def get_income_statement(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve income statement data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing income statement data
    """
    return route_to_vendor("get_income_statement", ticker, freq, curr_date)


# =============================================================================
# EGX (Egyptian Exchange) Fundamental Data Tools
# =============================================================================
# These tools access CSV-based fundamental data for EGX companies
# Data source: Manually prepared CSVs from Mubasher/EGX reports
# =============================================================================

from tradingagents.dataflows.local import (
    get_egx_income_statement,
    get_egx_balance_sheet,
    get_egx_key_ratios,
    get_egx_fundamentals_summary,
)
import json


@tool
def get_egx_fundamentals(
    ticker: Annotated[str, "EGX ticker symbol (e.g., COMI, EAST)"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"] = None,
) -> str:
    """
    Retrieve comprehensive EGX fundamental data from CSV files.
    Combines income statement, balance sheet, and key ratios.
    
    Args:
        ticker: EGX ticker symbol (without .CA suffix)
        curr_date: Filter to data published before this date
        
    Returns:
        str: JSON-formatted fundamental data summary with completeness metrics
    """
    result = get_egx_fundamentals_summary(ticker, curr_date)
    return json.dumps(result, indent=2, default=str)


@tool
def get_egx_income(
    ticker: Annotated[str, "EGX ticker symbol (e.g., COMI, EAST)"],
    freq: Annotated[str, "Reporting frequency: 'annual' or 'quarterly'"] = "annual",
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"] = None,
) -> str:
    """
    Retrieve EGX income statement from CSV file.
    
    Args:
        ticker: EGX ticker symbol
        freq: 'annual' or 'quarterly'
        curr_date: Filter to statements before this date
        
    Returns:
        str: JSON-formatted income statement with validation info
    """
    result = get_egx_income_statement(ticker, freq, curr_date)
    return json.dumps(result, indent=2, default=str)


@tool
def get_egx_balance(
    ticker: Annotated[str, "EGX ticker symbol (e.g., COMI, EAST)"],
    freq: Annotated[str, "Reporting frequency: 'annual' or 'quarterly'"] = "annual",
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"] = None,
) -> str:
    """
    Retrieve EGX balance sheet from CSV file.
    
    Args:
        ticker: EGX ticker symbol
        freq: 'annual' or 'quarterly'
        curr_date: Filter to statements before this date
        
    Returns:
        str: JSON-formatted balance sheet with validation info
    """
    result = get_egx_balance_sheet(ticker, freq, curr_date)
    return json.dumps(result, indent=2, default=str)


@tool
def get_egx_ratios(
    ticker: Annotated[str, "EGX ticker symbol (e.g., COMI, EAST)"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"] = None,
) -> str:
    """
    Retrieve EGX key financial ratios (PE, EPS, D/E, etc.) from CSV file.
    
    Args:
        ticker: EGX ticker symbol
        curr_date: Filter to ratios before this date
        
    Returns:
        str: JSON-formatted key ratios with categorization
    """
    result = get_egx_key_ratios(ticker, curr_date)
    return json.dumps(result, indent=2, default=str)