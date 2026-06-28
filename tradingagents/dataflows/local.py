from typing import Annotated
import pandas as pd
import os
import logging
from .config import DATA_DIR
from datetime import datetime
from dateutil.relativedelta import relativedelta
import json
from .reddit_utils import fetch_top_from_category
from tqdm import tqdm

logger = logging.getLogger("tradingagents.dataflows.local")

# Token-budget guard: tool outputs returned to LLM analysts are capped at this
# many characters. Previously hard-coded as a bare ``2000`` at each return site.
MAX_TOOL_OUTPUT_CHARS = 2000


def _truncate_tool_output(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    """Cap ``text`` at ``limit`` chars with a truncation marker, else return as-is.

    Equivalent to the prior inline ``s[:2000] + "\\n...[TRUNCATED]..." if len(s) > 2000``
    logic used by the finnhub-news and SimFin statement readers.
    """
    if len(text) > limit:
        return text[:limit] + "\n...[TRUNCATED]..."
    return text


def get_YFin_data_window(
    symbol: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    # calculate past days
    date_obj = datetime.strptime(curr_date, "%Y-%m-%d")
    before = date_obj - relativedelta(days=look_back_days)
    start_date = before.strftime("%Y-%m-%d")

    # read in data
    data = pd.read_csv(
        os.path.join(
            DATA_DIR,
            f"market_data/price_data/{symbol}-YFin-data-2015-01-01-2025-03-25.csv",
        )
    )

    # Extract just the date part for comparison
    data["DateOnly"] = data["Date"].str[:10]

    # Filter data between the start and end dates (inclusive)
    filtered_data = data[
        (data["DateOnly"] >= start_date) & (data["DateOnly"] <= curr_date)
    ]

    # Drop the temporary column we created
    filtered_data = filtered_data.drop("DateOnly", axis=1)

    # Limit to max 300 recent rows to prevent token overflow
    if len(filtered_data) > 300:
        filtered_data = filtered_data.iloc[-300:]

    with pd.option_context(
        "display.max_rows", None, "display.max_columns", None, "display.width", None
    ):
        df_string = filtered_data.to_string()

    return (
        f"## Raw Market Data for {symbol} from {start_date} to {curr_date}:\n\n"
        + df_string
    )

def get_YFin_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    # read in data
    data = pd.read_csv(
        os.path.join(
            DATA_DIR,
            f"market_data/price_data/{symbol}-YFin-data-2015-01-01-2025-03-25.csv",
        )
    )

    if end_date > "2025-03-25":
        raise Exception(
            f"Get_YFin_Data: {end_date} is outside of the data range of 2015-01-01 to 2025-03-25"
        )

    # Extract just the date part for comparison
    data["DateOnly"] = data["Date"].str[:10]

    # Filter data between the start and end dates (inclusive)
    filtered_data = data[
        (data["DateOnly"] >= start_date) & (data["DateOnly"] <= end_date)
    ]

    # Drop the temporary column we created
    filtered_data = filtered_data.drop("DateOnly", axis=1)

    # remove the index from the dataframe
    filtered_data = filtered_data.reset_index(drop=True)

    # Limit to max 300 recent rows to prevent token overflow
    if len(filtered_data) > 300:
        filtered_data = filtered_data.iloc[-300:]

    return filtered_data

def get_finnhub_news(
    query: Annotated[str, "Search query or ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
):
    """
    Retrieve news about a company within a time frame

    Args
        query (str): Search query or ticker symbol
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns
        str: dataframe containing the news of the company in the time frame

    """

    try:
        result = get_data_in_range(query, start_date, end_date, "news_data", DATA_DIR)
    except Exception as e:
        logger.warning("get_finnhub_news failed: %s", e)
        return "## Finnhub News: No data available or error occurred."

    if len(result) == 0:
        return ""

    combined_result = ""
    article_count = 0
    MAX_ARTICLES = 5
    
    # Sort days in reverse to get most recent news first
    sorted_days = sorted(result.keys(), reverse=True)
    
    for day in sorted_days:
        data = result[day]
        if len(data) == 0:
            continue
        for entry in data:
            if article_count >= MAX_ARTICLES:
                break
                
            summary = entry["summary"]
            if len(summary) > 500:
                summary = summary[:500] + "..."
                
            current_news = (
                "### " + entry["headline"] + f" ({day})" + "\n" + summary
            )
            combined_result += current_news + "\n\n"
            article_count += 1
            
        if article_count >= MAX_ARTICLES:
            break

    final_result_str = "## " + query + " News, from " + start_date + " to " + end_date + ":\n" + str(combined_result)
    logger.debug("get_finnhub_news returning string of length %d", len(final_result_str))
    final_result_str = _truncate_tool_output(final_result_str)
    if len(final_result_str) > 500:
         final_result_str = final_result_str[:500] + "\n...[TRUNCATED to 500]..."
    return final_result_str


def get_finnhub_company_insider_sentiment(
    ticker: Annotated[str, "ticker symbol for the company"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
):
    """
    Retrieve insider sentiment about a company (retrieved from public SEC information) for the past 15 days
    Args:
        ticker (str): ticker symbol of the company
        curr_date (str): current date you are trading on, yyyy-mm-dd
    Returns:
        str: a report of the sentiment in the past 15 days starting at curr_date
    """

    date_obj = datetime.strptime(curr_date, "%Y-%m-%d")
    before = date_obj - relativedelta(days=15)  # Default 15 days lookback
    before = before.strftime("%Y-%m-%d")

    data = get_data_in_range(ticker, before, curr_date, "insider_senti", DATA_DIR)

    if len(data) == 0:
        return ""

    result_str = ""
    seen_dicts = []
    for date, senti_list in data.items():
        for entry in senti_list:
            if entry not in seen_dicts:
                result_str += f"### {entry['year']}-{entry['month']}:\nChange: {entry['change']}\nMonthly Share Purchase Ratio: {entry['mspr']}\n\n"
                seen_dicts.append(entry)

    return (
        f"## {ticker} Insider Sentiment Data for {before} to {curr_date}:\n"
        + result_str
        + "The change field refers to the net buying/selling from all insiders' transactions. The mspr field refers to monthly share purchase ratio."
    )


def get_finnhub_company_insider_transactions(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
):
    """
    Retrieve insider transcaction information about a company (retrieved from public SEC information) for the past 15 days
    Args:
        ticker (str): ticker symbol of the company
        curr_date (str): current date you are trading at, yyyy-mm-dd
    Returns:
        str: a report of the company's insider transaction/trading informtaion in the past 15 days
    """

    date_obj = datetime.strptime(curr_date, "%Y-%m-%d")
    before = date_obj - relativedelta(days=15)  # Default 15 days lookback
    before = before.strftime("%Y-%m-%d")

    data = get_data_in_range(ticker, before, curr_date, "insider_trans", DATA_DIR)

    if len(data) == 0:
        return ""

    result_str = ""

    seen_dicts = []
    for date, senti_list in data.items():
        for entry in senti_list:
            if entry not in seen_dicts:
                result_str += f"### Filing Date: {entry['filingDate']}, {entry['name']}:\nChange:{entry['change']}\nShares: {entry['share']}\nTransaction Price: {entry['transactionPrice']}\nTransaction Code: {entry['transactionCode']}\n\n"
                seen_dicts.append(entry)

    return (
        f"## {ticker} insider transactions from {before} to {curr_date}:\n"
        + result_str
        + "The change field reflects the variation in share count—here a negative number indicates a reduction in holdings—while share specifies the total number of shares involved. The transactionPrice denotes the per-share price at which the trade was executed, and transactionDate marks when the transaction occurred. The name field identifies the insider making the trade, and transactionCode (e.g., S for sale) clarifies the nature of the transaction. FilingDate records when the transaction was officially reported, and the unique id links to the specific SEC filing, as indicated by the source. Additionally, the symbol ties the transaction to a particular company, isDerivative flags whether the trade involves derivative securities, and currency notes the currency context of the transaction."
    )

def get_data_in_range(ticker, start_date, end_date, data_type, data_dir, period=None):
    """
    Gets finnhub data saved and processed on disk.
    Args:
        start_date (str): Start date in YYYY-MM-DD format.
        end_date (str): End date in YYYY-MM-DD format.
        data_type (str): Type of data from finnhub to fetch. Can be insider_trans, SEC_filings, news_data, insider_senti, or fin_as_reported.
        data_dir (str): Directory where the data is saved.
        period (str): Default to none, if there is a period specified, should be annual or quarterly.
    """

    if period:
        data_path = os.path.join(
            data_dir,
            "finnhub_data",
            data_type,
            f"{ticker}_{period}_data_formatted.json",
        )
    else:
        data_path = os.path.join(
            data_dir, "finnhub_data", data_type, f"{ticker}_data_formatted.json"
        )

    data = open(data_path, "r")
    data = json.load(data)

    # filter keys (date, str in format YYYY-MM-DD) by the date range (str, str in format YYYY-MM-DD)
    filtered_data = {}
    for key, value in data.items():
        if start_date <= key <= end_date and len(value) > 0:
            filtered_data[key] = value
    return filtered_data

def get_simfin_balance_sheet(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[
        str,
        "reporting frequency of the company's financial history: annual / quarterly",
    ],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
):
    data_path = os.path.join(
        DATA_DIR,
        "fundamental_data",
        "simfin_data_all",
        "balance_sheet",
        "companies",
        "us",
        f"us-balance-{freq}.csv",
    )
    df = pd.read_csv(data_path, sep=";")

    # Convert date strings to datetime objects and remove any time components
    df["Report Date"] = pd.to_datetime(df["Report Date"], utc=True).dt.normalize()
    df["Publish Date"] = pd.to_datetime(df["Publish Date"], utc=True).dt.normalize()

    # Convert the current date to datetime and normalize
    curr_date_dt = pd.to_datetime(curr_date, utc=True).normalize()

    # Filter the DataFrame for the given ticker and for reports that were published on or before the current date
    filtered_df = df[(df["Ticker"] == ticker) & (df["Publish Date"] <= curr_date_dt)]

    # Check if there are any available reports; if not, return a notification
    if filtered_df.empty:
        logger.info("No balance sheet available before the given current date.")
        return ""

    # Get the most recent balance sheet by selecting the row with the latest Publish Date
    latest_balance_sheet = filtered_df.loc[filtered_df["Publish Date"].idxmax()]

    # drop the SimFinID column
    latest_balance_sheet = latest_balance_sheet.drop("SimFinId")

    res_str = (
        f"## {freq} balance sheet for {ticker} released on {str(latest_balance_sheet['Publish Date'])[0:10]}: \n"
        + str(latest_balance_sheet)
        + "\n\nThis includes metadata like reporting dates and currency, share details, and a breakdown of assets, liabilities, and equity. Assets are grouped as current (liquid items like cash and receivables) and noncurrent (long-term investments and property). Liabilities are split between short-term obligations and long-term debts, while equity reflects shareholder funds such as paid-in capital and retained earnings. Together, these components ensure that total assets equal the sum of liabilities and equity."
    )
    logger.debug("get_simfin_balance_sheet returning length %d", len(res_str))
    res_str = _truncate_tool_output(res_str)
    return res_str


def get_simfin_cashflow(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[
        str,
        "reporting frequency of the company's financial history: annual / quarterly",
    ],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
):
    data_path = os.path.join(
        DATA_DIR,
        "fundamental_data",
        "simfin_data_all",
        "cash_flow",
        "companies",
        "us",
        f"us-cashflow-{freq}.csv",
    )
    df = pd.read_csv(data_path, sep=";")

    # Convert date strings to datetime objects and remove any time components
    df["Report Date"] = pd.to_datetime(df["Report Date"], utc=True).dt.normalize()
    df["Publish Date"] = pd.to_datetime(df["Publish Date"], utc=True).dt.normalize()

    # Convert the current date to datetime and normalize
    curr_date_dt = pd.to_datetime(curr_date, utc=True).normalize()

    # Filter the DataFrame for the given ticker and for reports that were published on or before the current date
    filtered_df = df[(df["Ticker"] == ticker) & (df["Publish Date"] <= curr_date_dt)]

    # Check if there are any available reports; if not, return a notification
    if filtered_df.empty:
        logger.info("No cash flow statement available before the given current date.")
        return ""

    # Get the most recent cash flow statement by selecting the row with the latest Publish Date
    latest_cash_flow = filtered_df.loc[filtered_df["Publish Date"].idxmax()]

    # drop the SimFinID column
    latest_cash_flow = latest_cash_flow.drop("SimFinId")

    res_str = (
        f"## {freq} cash flow statement for {ticker} released on {str(latest_cash_flow['Publish Date'])[0:10]}: \n"
        + str(latest_cash_flow)
        + "\n\nThis includes metadata like reporting dates and currency, share details, and a breakdown of cash movements. Operating activities show cash generated from core business operations, including net income adjustments for non-cash items and working capital changes. Investing activities cover asset acquisitions/disposals and investments. Financing activities include debt transactions, equity issuances/repurchases, and dividend payments. The net change in cash represents the overall increase or decrease in the company's cash position during the reporting period."
    )
    logger.debug("get_simfin_cashflow returning length %d", len(res_str))
    res_str = _truncate_tool_output(res_str)
    return res_str


def get_simfin_income_statements(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[
        str,
        "reporting frequency of the company's financial history: annual / quarterly",
    ],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
):
    data_path = os.path.join(
        DATA_DIR,
        "fundamental_data",
        "simfin_data_all",
        "income_statements",
        "companies",
        "us",
        f"us-income-{freq}.csv",
    )
    df = pd.read_csv(data_path, sep=";")

    # Convert date strings to datetime objects and remove any time components
    df["Report Date"] = pd.to_datetime(df["Report Date"], utc=True).dt.normalize()
    df["Publish Date"] = pd.to_datetime(df["Publish Date"], utc=True).dt.normalize()

    # Convert the current date to datetime and normalize
    curr_date_dt = pd.to_datetime(curr_date, utc=True).normalize()

    # Filter the DataFrame for the given ticker and for reports that were published on or before the current date
    filtered_df = df[(df["Ticker"] == ticker) & (df["Publish Date"] <= curr_date_dt)]

    # Check if there are any available reports; if not, return a notification
    if filtered_df.empty:
        logger.info("No income statement available before the given current date.")
        return ""

    # Get the most recent income statement by selecting the row with the latest Publish Date
    latest_income = filtered_df.loc[filtered_df["Publish Date"].idxmax()]

    # drop the SimFinID column
    latest_income = latest_income.drop("SimFinId")

    res_str = (
        f"## {freq} income statement for {ticker} released on {str(latest_income['Publish Date'])[0:10]}: \n"
        + str(latest_income)
        + "\n\nThis includes metadata like reporting dates and currency, share details, and a comprehensive breakdown of the company's financial performance. Starting with Revenue, it shows Cost of Revenue and resulting Gross Profit. Operating Expenses are detailed, including SG&A, R&D, and Depreciation. The statement then shows Operating Income, followed by non-operating items and Interest Expense, leading to Pretax Income. After accounting for Income Tax and any Extraordinary items, it concludes with Net Income, representing the company's bottom-line profit or loss for the period."
    )
    logger.debug("get_simfin_income_statements returning length %d", len(res_str))
    res_str = _truncate_tool_output(res_str)
    return res_str


def get_reddit_global_news(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Number of days to look back"] = 7,
    limit: Annotated[int, "Maximum number of articles to return"] = 5,
) -> str:
    """
    Retrieve the latest top reddit news
    Args:
        curr_date: Current date in yyyy-mm-dd format
        look_back_days: Number of days to look back (default 7)
        limit: Maximum number of articles to return (default 5)
    Returns:
        str: A formatted string containing the latest news articles posts on reddit
    """

    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = curr_date_dt - relativedelta(days=look_back_days)
    before = before.strftime("%Y-%m-%d")

    posts = []
    # iterate from before to curr_date
    curr_iter_date = datetime.strptime(before, "%Y-%m-%d")

    total_iterations = (curr_date_dt - curr_iter_date).days + 1
    pbar = tqdm(desc=f"Getting Global News on {curr_date}", total=total_iterations)

    while curr_iter_date <= curr_date_dt:
        curr_date_str = curr_iter_date.strftime("%Y-%m-%d")
        fetch_result = fetch_top_from_category(
            "global_news",
            curr_date_str,
            limit,
            data_path=os.path.join(DATA_DIR, "reddit_data"),
        )
        posts.extend(fetch_result)
        curr_iter_date += relativedelta(days=1)
        pbar.update(1)

    pbar.close()

    if len(posts) == 0:
        return ""

    news_str = ""
    article_count = 0
    MAX_ARTICLES = 5
    
    for post in posts[:MAX_ARTICLES]:
        content = post.get("content", "")
        if len(content) > 500:
            content = content[:500] + "..."
            
        if content == "":
            news_str += f"### {post['title']}\n\n"
        else:
            news_str += f"### {post['title']}\n\n{content}\n\n"
        
        article_count += 1

    result = f"## Global News Reddit, from {before} to {curr_date} (Top {article_count}):\n{news_str}"
    if len(result) > 500:
        result = result[:500] + "\n...[TRUNCATED to 500]..."
    return result


def get_reddit_company_news(
    query: Annotated[str, "Search query or ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve the latest top reddit news
    Args:
        query: Search query or ticker symbol
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format
    Returns:
        str: A formatted string containing news articles posts on reddit
    """

    start_date_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_date_dt = datetime.strptime(end_date, "%Y-%m-%d")

    posts = []
    # iterate from start_date to end_date
    curr_date = start_date_dt

    total_iterations = (end_date_dt - curr_date).days + 1
    pbar = tqdm(
        desc=f"Getting Company News for {query} from {start_date} to {end_date}",
        total=total_iterations,
    )

    while curr_date <= end_date_dt:
        curr_date_str = curr_date.strftime("%Y-%m-%d")
        try:
            fetch_result = fetch_top_from_category(
                "company_news",
                curr_date_str,
                10,  # max limit per day
                query,
                data_path=os.path.join(DATA_DIR, "reddit_data"),
            )
            posts.extend(fetch_result)
        except Exception as e:
            logger.warning("Reddit fetch failed for %s: %s", curr_date_str, e)
            
        curr_date += relativedelta(days=1)
        pbar.update(1)

    pbar.close()

    if len(posts) == 0:
        return ""

    news_str = ""
    article_count = 0
    MAX_ARTICLES = 5

    # Reverse posts to get latest first? Reddit usually returns top/hot, not necessarily chronological.
    # Assuming fetch_top_from_category returns robust list. 
    # We'll just take the top 5 from the accumulated list.
    
    for post in posts[:MAX_ARTICLES]:
        content = post.get("content", "")
        if len(content) > 500:
            content = content[:500] + "..."
            
        if content == "":
            news_str += f"### {post['title']}\n\n"
        else:
            news_str += f"### {post['title']}\n\n{content}\n\n"
        
        article_count += 1

    result = f"## {query} News Reddit, from {start_date} to {end_date} (Top {article_count}):\n\n{news_str}"
    if len(result) > 500:
        result = result[:500] + "\n...[TRUNCATED to 500]..."
    return result


# =============================================================================
# EGX (Egyptian Exchange) Fundamental Data Ingestion
# =============================================================================
# Load company financials from manually prepared CSV files
# (exported from Mubasher or EGX reports)
# 
# Expected directory structure:
#   {DATA_DIR}/egx_fundamentals/
#     ├── income_statements/
#     │   └── {SYMBOL}_income.csv
#     ├── balance_sheets/
#     │   └── {SYMBOL}_balance.csv
#     └── key_ratios/
#         └── {SYMBOL}_ratios.csv
# =============================================================================

from typing import Dict, Any, List, Optional

# EGX fundamental data configuration
EGX_FUNDAMENTALS_DIR = "egx_fundamentals"

# Required fields for each financial statement type
EGX_INCOME_REQUIRED_FIELDS = [
    "period_end_date",
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
]

EGX_INCOME_OPTIONAL_FIELDS = [
    "cost_of_revenue",
    "operating_expenses",
    "interest_expense",
    "tax_expense",
    "ebitda",
    "eps_basic",
    "eps_diluted",
]

EGX_BALANCE_REQUIRED_FIELDS = [
    "period_end_date",
    "total_assets",
    "total_liabilities",
    "total_equity",
]

EGX_BALANCE_OPTIONAL_FIELDS = [
    "cash_and_equivalents",
    "accounts_receivable",
    "inventory",
    "current_assets",
    "fixed_assets",
    "current_liabilities",
    "long_term_debt",
    "retained_earnings",
    "shares_outstanding",
]

EGX_RATIOS_REQUIRED_FIELDS = [
    "period_end_date",
]

EGX_RATIOS_OPTIONAL_FIELDS = [
    "pe_ratio",
    "eps",
    "debt_to_equity",
    "current_ratio",
    "roe",
    "roa",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "book_value_per_share",
    "dividend_yield",
    "price_to_book",
]


def _validate_egx_csv_fields(
    df: pd.DataFrame,
    required_fields: List[str],
    optional_fields: List[str],
    data_type: str
) -> Dict[str, Any]:
    """
    Validate CSV columns and identify missing fields.
    
    Returns:
        Dict with validation results and field mapping
    """
    # Normalize column names (lowercase, strip whitespace)
    df.columns = df.columns.str.lower().str.strip().str.replace(' ', '_')
    
    present_required = [f for f in required_fields if f in df.columns]
    missing_required = [f for f in required_fields if f not in df.columns]
    present_optional = [f for f in optional_fields if f in df.columns]
    missing_optional = [f for f in optional_fields if f not in df.columns]
    
    is_valid = len(missing_required) == 0
    
    return {
        "is_valid": is_valid,
        "data_type": data_type,
        "present_required": present_required,
        "missing_required": missing_required,
        "present_optional": present_optional,
        "missing_optional": missing_optional,
        "total_columns": len(df.columns),
        "validation_message": (
            f"Valid {data_type}" if is_valid 
            else f"INVALID: Missing required fields: {missing_required}"
        )
    }


def _parse_numeric_value(value: Any) -> Optional[float]:
    """
    Parse a value to float, handling EGX/Mubasher formatting.
    Handles: commas, parentheses for negatives, Arabic numerals, currency symbols.
    """
    if pd.isna(value) or value == "" or value == "-" or value == "N/A":
        return None
    
    if isinstance(value, (int, float)):
        return float(value)
    
    try:
        # Convert to string and clean
        val_str = str(value).strip()
        
        # Handle parentheses as negative (accounting format)
        is_negative = val_str.startswith('(') and val_str.endswith(')')
        if is_negative:
            val_str = val_str[1:-1]
        
        # Remove currency symbols and formatting
        val_str = val_str.replace('EGP', '').replace('ج.م', '').replace(',', '').strip()
        
        # Convert to float
        result = float(val_str)
        return -result if is_negative else result
        
    except (ValueError, TypeError):
        return None


def _row_to_structured_dict(
    row: pd.Series,
    required_fields: List[str],
    optional_fields: List[str]
) -> Dict[str, Any]:
    """
    Convert a DataFrame row to a structured dict with explicit missing field tracking.
    """
    result = {
        "values": {},
        "missing_fields": [],
        "currency": "EGP",
        "market": "EGX"
    }
    
    all_fields = required_fields + optional_fields
    
    for field in all_fields:
        if field in row.index:
            parsed_value = _parse_numeric_value(row[field])
            if parsed_value is not None:
                result["values"][field] = parsed_value
            else:
                result["values"][field] = None
                result["missing_fields"].append(field)
        else:
            result["values"][field] = None
            result["missing_fields"].append(field)
    
    return result


def get_egx_income_statement(
    ticker: Annotated[str, "EGX ticker symbol (e.g., COMI, EAST)"],
    freq: Annotated[str, "Reporting frequency: 'annual' or 'quarterly'"] = "annual",
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"] = None,
) -> Dict[str, Any]:
    """
    Load EGX income statement from CSV file.
    
    Expected CSV format:
        period_end_date,revenue,cost_of_revenue,gross_profit,operating_expenses,
        operating_income,interest_expense,tax_expense,net_income,ebitda,eps_basic
    
    Args:
        ticker: EGX ticker symbol (without .CA suffix)
        freq: 'annual' or 'quarterly'
        curr_date: Filter to statements published before this date
        
    Returns:
        Dict with structured income statement data and validation info
    """
    # Normalize ticker (remove .CA suffix if present)
    ticker = ticker.upper().replace('.CA', '').strip()
    
    # Build file path
    data_path = os.path.join(
        DATA_DIR,
        EGX_FUNDAMENTALS_DIR,
        "income_statements",
        f"{ticker}_income_{freq}.csv"
    )
    
    # Check if file exists
    if not os.path.exists(data_path):
        return {
            "error": f"Income statement file not found: {data_path}",
            "ticker": ticker,
            "freq": freq,
            "data": None,
            "missing_fields": ["ALL - file not found"],
            "suggestion": f"Please export {ticker} income statement from Mubasher/EGX and save as CSV"
        }
    
    try:
        df = pd.read_csv(data_path)
    except Exception as e:
        return {
            "error": f"Failed to read CSV: {str(e)}",
            "ticker": ticker,
            "data": None
        }
    
    # Validate fields
    validation = _validate_egx_csv_fields(
        df, EGX_INCOME_REQUIRED_FIELDS, EGX_INCOME_OPTIONAL_FIELDS, "income_statement"
    )
    
    if not validation["is_valid"]:
        return {
            "error": validation["validation_message"],
            "ticker": ticker,
            "validation": validation,
            "data": None
        }
    
    # Parse period_end_date and filter by curr_date if provided.
    # Apply EGX mandatory filing lag to prevent look-ahead bias:
    #   Annual reports: EGX requires filing within 120 days of fiscal year-end
    #   Quarterly reports: EGX requires filing within 45 days of quarter-end
    # We only include a report if period_end_date + filing_lag <= curr_date,
    # meaning the report is guaranteed to have been publicly filed by curr_date.
    df['period_end_date'] = pd.to_datetime(df['period_end_date'], errors='coerce')

    if curr_date:
        curr_date_dt = pd.to_datetime(curr_date)
        # Use publish_date column if available (preferred — exact filing date)
        if 'publish_date' in df.columns:
            df['publish_date'] = pd.to_datetime(df['publish_date'], errors='coerce')
            df = df[df['publish_date'] <= curr_date_dt]
        else:
            # Conservative proxy: apply EGX mandatory filing lag
            filing_lag_days = 120 if freq == "annual" else 45
            df = df[df['period_end_date'] <= (curr_date_dt - pd.Timedelta(days=filing_lag_days))]

    if df.empty:
        return {
            "error": f"No income statements found for {ticker} before {curr_date} (after filing lag)",
            "ticker": ticker,
            "data": None
        }

    # Get the most recent statement
    df = df.sort_values('period_end_date', ascending=False)
    latest_row = df.iloc[0]
    
    # Convert to structured dict
    structured_data = _row_to_structured_dict(
        latest_row, EGX_INCOME_REQUIRED_FIELDS, EGX_INCOME_OPTIONAL_FIELDS
    )
    
    return {
        "ticker": ticker,
        "market": "EGX",
        "currency": "EGP",
        "statement_type": "income_statement",
        "frequency": freq,
        "period_end_date": latest_row['period_end_date'].strftime('%Y-%m-%d'),
        "data": structured_data["values"],
        "missing_fields": structured_data["missing_fields"],
        "validation": validation,
        "source": "CSV (Mubasher/EGX export)"
    }


def get_egx_balance_sheet(
    ticker: Annotated[str, "EGX ticker symbol (e.g., COMI, EAST)"],
    freq: Annotated[str, "Reporting frequency: 'annual' or 'quarterly'"] = "annual",
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"] = None,
) -> Dict[str, Any]:
    """
    Load EGX balance sheet from CSV file.
    
    Expected CSV format:
        period_end_date,cash_and_equivalents,accounts_receivable,inventory,
        current_assets,fixed_assets,total_assets,current_liabilities,
        long_term_debt,total_liabilities,retained_earnings,total_equity,
        shares_outstanding
    
    Args:
        ticker: EGX ticker symbol (without .CA suffix)
        freq: 'annual' or 'quarterly'
        curr_date: Filter to statements published before this date
        
    Returns:
        Dict with structured balance sheet data and validation info
    """
    ticker = ticker.upper().replace('.CA', '').strip()
    
    data_path = os.path.join(
        DATA_DIR,
        EGX_FUNDAMENTALS_DIR,
        "balance_sheets",
        f"{ticker}_balance_{freq}.csv"
    )
    
    if not os.path.exists(data_path):
        return {
            "error": f"Balance sheet file not found: {data_path}",
            "ticker": ticker,
            "freq": freq,
            "data": None,
            "missing_fields": ["ALL - file not found"],
            "suggestion": f"Please export {ticker} balance sheet from Mubasher/EGX and save as CSV"
        }
    
    try:
        df = pd.read_csv(data_path)
    except Exception as e:
        return {
            "error": f"Failed to read CSV: {str(e)}",
            "ticker": ticker,
            "data": None
        }
    
    validation = _validate_egx_csv_fields(
        df, EGX_BALANCE_REQUIRED_FIELDS, EGX_BALANCE_OPTIONAL_FIELDS, "balance_sheet"
    )
    
    if not validation["is_valid"]:
        return {
            "error": validation["validation_message"],
            "ticker": ticker,
            "validation": validation,
            "data": None
        }
    
    df['period_end_date'] = pd.to_datetime(df['period_end_date'], errors='coerce')

    if curr_date:
        curr_date_dt = pd.to_datetime(curr_date)
        if 'publish_date' in df.columns:
            df['publish_date'] = pd.to_datetime(df['publish_date'], errors='coerce')
            df = df[df['publish_date'] <= curr_date_dt]
        else:
            filing_lag_days = 120 if freq == "annual" else 45
            df = df[df['period_end_date'] <= (curr_date_dt - pd.Timedelta(days=filing_lag_days))]

    if df.empty:
        return {
            "error": f"No balance sheets found for {ticker} before {curr_date} (after filing lag)",
            "ticker": ticker,
            "data": None
        }

    df = df.sort_values('period_end_date', ascending=False)
    latest_row = df.iloc[0]

    structured_data = _row_to_structured_dict(
        latest_row, EGX_BALANCE_REQUIRED_FIELDS, EGX_BALANCE_OPTIONAL_FIELDS
    )
    
    # Calculate derived metrics if possible
    derived_metrics = {}
    values = structured_data["values"]
    
    if values.get("total_assets") and values.get("total_liabilities"):
        calculated_equity = values["total_assets"] - values["total_liabilities"]
        derived_metrics["calculated_equity"] = round(calculated_equity, 2)
        
        # Check if provided equity matches calculated
        if values.get("total_equity"):
            equity_diff = abs(values["total_equity"] - calculated_equity)
            if equity_diff > 1:  # Allow for rounding
                derived_metrics["equity_discrepancy"] = round(equity_diff, 2)
    
    return {
        "ticker": ticker,
        "market": "EGX",
        "currency": "EGP",
        "statement_type": "balance_sheet",
        "frequency": freq,
        "period_end_date": latest_row['period_end_date'].strftime('%Y-%m-%d'),
        "data": structured_data["values"],
        "derived_metrics": derived_metrics,
        "missing_fields": structured_data["missing_fields"],
        "validation": validation,
        "source": "CSV (Mubasher/EGX export)"
    }


def get_egx_key_ratios(
    ticker: Annotated[str, "EGX ticker symbol (e.g., COMI, EAST)"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"] = None,
) -> Dict[str, Any]:
    """
    Load EGX key financial ratios from CSV file.
    
    Expected CSV format:
        period_end_date,pe_ratio,eps,debt_to_equity,current_ratio,roe,roa,
        gross_margin,operating_margin,net_margin,book_value_per_share,
        dividend_yield,price_to_book
    
    Args:
        ticker: EGX ticker symbol (without .CA suffix)
        curr_date: Filter to ratios calculated before this date
        
    Returns:
        Dict with structured ratio data and validation info
    """
    ticker = ticker.upper().replace('.CA', '').strip()
    
    data_path = os.path.join(
        DATA_DIR,
        EGX_FUNDAMENTALS_DIR,
        "key_ratios",
        f"{ticker}_ratios.csv"
    )
    
    if not os.path.exists(data_path):
        return {
            "error": f"Key ratios file not found: {data_path}",
            "ticker": ticker,
            "data": None,
            "missing_fields": ["ALL - file not found"],
            "suggestion": f"Please export {ticker} key ratios from Mubasher/EGX and save as CSV"
        }
    
    try:
        df = pd.read_csv(data_path)
    except Exception as e:
        return {
            "error": f"Failed to read CSV: {str(e)}",
            "ticker": ticker,
            "data": None
        }
    
    validation = _validate_egx_csv_fields(
        df, EGX_RATIOS_REQUIRED_FIELDS, EGX_RATIOS_OPTIONAL_FIELDS, "key_ratios"
    )
    
    # Note: For ratios, we don't require all fields - just the date
    df['period_end_date'] = pd.to_datetime(df['period_end_date'], errors='coerce')

    if curr_date:
        curr_date_dt = pd.to_datetime(curr_date)
        if 'publish_date' in df.columns:
            df['publish_date'] = pd.to_datetime(df['publish_date'], errors='coerce')
            df = df[df['publish_date'] <= curr_date_dt]
        else:
            # Key ratios are typically released alongside annual reports: 120-day lag
            df = df[df['period_end_date'] <= (curr_date_dt - pd.Timedelta(days=120))]

    if df.empty:
        return {
            "error": f"No key ratios found for {ticker} before {curr_date} (after filing lag)",
            "ticker": ticker,
            "data": None
        }
    
    df = df.sort_values('period_end_date', ascending=False)
    latest_row = df.iloc[0]
    
    structured_data = _row_to_structured_dict(
        latest_row, EGX_RATIOS_REQUIRED_FIELDS, EGX_RATIOS_OPTIONAL_FIELDS
    )
    
    # Categorize ratios for easier consumption
    ratios = structured_data["values"]
    categorized = {
        "valuation": {
            "pe_ratio": ratios.get("pe_ratio"),
            "price_to_book": ratios.get("price_to_book"),
            "eps": ratios.get("eps"),
            "book_value_per_share": ratios.get("book_value_per_share"),
        },
        "profitability": {
            "roe": ratios.get("roe"),
            "roa": ratios.get("roa"),
            "gross_margin": ratios.get("gross_margin"),
            "operating_margin": ratios.get("operating_margin"),
            "net_margin": ratios.get("net_margin"),
        },
        "leverage": {
            "debt_to_equity": ratios.get("debt_to_equity"),
            "current_ratio": ratios.get("current_ratio"),
        },
        "income": {
            "dividend_yield": ratios.get("dividend_yield"),
        }
    }
    
    return {
        "ticker": ticker,
        "market": "EGX",
        "currency": "EGP",
        "data_type": "key_ratios",
        "period_end_date": latest_row['period_end_date'].strftime('%Y-%m-%d'),
        "ratios": ratios,
        "categorized": categorized,
        "missing_fields": structured_data["missing_fields"],
        "validation": validation,
        "source": "CSV (Mubasher/EGX export)"
    }


def get_egx_fundamentals_summary(
    ticker: Annotated[str, "EGX ticker symbol (e.g., COMI, EAST)"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"] = None,
) -> Dict[str, Any]:
    """
    Load all available EGX fundamental data for a ticker.
    Combines income statement, balance sheet, and key ratios.
    
    Returns:
        Dict with all available fundamental data and completeness metrics
    """
    ticker = ticker.upper().replace('.CA', '').strip()
    
    income = get_egx_income_statement(ticker, "annual", curr_date)
    balance = get_egx_balance_sheet(ticker, "annual", curr_date)
    ratios = get_egx_key_ratios(ticker, curr_date)
    
    # Calculate data completeness
    available_statements = 0
    total_statements = 3
    
    if income.get("data"):
        available_statements += 1
    if balance.get("data"):
        available_statements += 1
    if ratios.get("ratios"):
        available_statements += 1
    
    completeness = available_statements / total_statements
    
    return {
        "ticker": ticker,
        "market": "EGX",
        "currency": "EGP",
        "as_of_date": curr_date,
        "income_statement": income,
        "balance_sheet": balance,
        "key_ratios": ratios,
        "data_completeness": {
            "available_statements": available_statements,
            "total_statements": total_statements,
            "completeness_ratio": completeness,
            "is_complete": completeness == 1.0
        },
        "source": "CSV files (Mubasher/EGX exports)"
    }


# =============================================================================
# EGX (Egyptian Exchange) News Data Ingestion
# =============================================================================
# Load news from manually saved text files and CSV files
# Sources: Enterprise.news, Mubasher Egypt, other Egyptian financial news
# 
# Expected directory structure:
#   {DATA_DIR}/egx_news/
#     ├── text/
#     │   └── {SYMBOL}_news_{DATE}.txt
#     ├── csv/
#     │   └── {SYMBOL}_news.csv
#     └── global/
#         └── egx_market_news.csv
# =============================================================================

# EGX news configuration
EGX_NEWS_DIR = "egx_news"

# Known news sources
EGX_NEWS_SOURCES = {
    "enterprise": {
        "name": "Enterprise.news",
        "type": "english",
        "reliability": "high"
    },
    "mubasher": {
        "name": "Mubasher Egypt",
        "type": "bilingual",
        "reliability": "high"
    },
    "egx_official": {
        "name": "EGX Official Disclosures",
        "type": "bilingual",
        "reliability": "official"
    },
    "other": {
        "name": "Other Source",
        "type": "unknown",
        "reliability": "unverified"
    }
}


def _detect_language(text: str) -> str:
    """
    Simple heuristic to detect if text is Arabic, English, or mixed.
    No external libraries required.
    """
    if not text:
        return "unknown"
    
    # Count Arabic characters (Unicode range for Arabic)
    arabic_chars = sum(1 for c in text if '\u0600' <= c <= '\u06FF' or '\u0750' <= c <= '\u077F')
    english_chars = sum(1 for c in text if 'a' <= c.lower() <= 'z')
    
    total_chars = arabic_chars + english_chars
    if total_chars == 0:
        return "unknown"
    
    arabic_ratio = arabic_chars / total_chars
    
    if arabic_ratio > 0.7:
        return "arabic"
    elif arabic_ratio < 0.3:
        return "english"
    else:
        return "mixed"


def _parse_news_source(source_string: str) -> Dict[str, Any]:
    """
    Parse and normalize news source identifier.
    """
    if not source_string:
        return EGX_NEWS_SOURCES["other"]
    
    source_lower = source_string.lower().strip()
    
    if "enterprise" in source_lower:
        return EGX_NEWS_SOURCES["enterprise"]
    elif "mubasher" in source_lower:
        return EGX_NEWS_SOURCES["mubasher"]
    elif "egx" in source_lower or "disclosure" in source_lower:
        return EGX_NEWS_SOURCES["egx_official"]
    else:
        return {
            "name": source_string,
            "type": "unknown",
            "reliability": "unverified"
        }


def get_egx_news_from_text(
    ticker: Annotated[str, "EGX ticker symbol (e.g., COMI, EAST)"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Number of days to look back for news"] = 7,
) -> Dict[str, Any]:
    """
    Load EGX company news from text files.
    
    Expected file format:
        {DATA_DIR}/egx_news/text/{TICKER}_news_{DATE}.txt
        
    Each text file should contain news articles separated by "---"
    with optional metadata lines at the start:
        SOURCE: Enterprise.news
        DATE: 2025-01-15
        LANGUAGE: english
        ---
        Article 1 headline and content...
        ---
        Article 2 headline and content...
    
    Args:
        ticker: EGX ticker symbol
        curr_date: Current date
        look_back_days: How many days of news to retrieve
        
    Returns:
        Dict with structured news data and metadata
    """
    ticker = ticker.upper().replace('.CA', '').strip()
    
    news_dir = os.path.join(DATA_DIR, EGX_NEWS_DIR, "text")
    
    if not os.path.exists(news_dir):
        return {
            "ticker": ticker,
            "error": f"News directory not found: {news_dir}",
            "articles": [],
            "total_articles": 0
        }
    
    # Calculate date range
    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_date_dt = curr_date_dt - relativedelta(days=look_back_days)
    
    articles = []
    files_checked = []
    
    # Look for news files in the date range
    check_date = start_date_dt
    while check_date <= curr_date_dt:
        date_str = check_date.strftime("%Y-%m-%d")
        
        # Try different filename patterns
        patterns = [
            f"{ticker}_news_{date_str}.txt",
            f"{ticker}_{date_str}.txt",
            f"{ticker.lower()}_news_{date_str}.txt",
        ]
        
        for pattern in patterns:
            file_path = os.path.join(news_dir, pattern)
            files_checked.append(pattern)
            
            if os.path.exists(file_path):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    # Parse the file content
                    parsed = _parse_text_news_file(content, date_str, ticker)
                    articles.extend(parsed)
                    
                except Exception as e:
                    articles.append({
                        "error": f"Failed to read {file_path}: {str(e)}",
                        "date": date_str
                    })
        
        check_date += relativedelta(days=1)
    
    return {
        "ticker": ticker,
        "market": "EGX",
        "date_range": {
            "start": start_date_dt.strftime("%Y-%m-%d"),
            "end": curr_date
        },
        "articles": articles,
        "total_articles": len([a for a in articles if "error" not in a]),
        "files_checked": len(files_checked),
        "source": "Text files (manual export)"
    }


def _parse_text_news_file(content: str, default_date: str, ticker: str) -> List[Dict[str, Any]]:
    """
    Parse a news text file into structured articles.
    """
    articles = []
    
    # Split by article separator
    parts = content.split("---")
    
    # Check if first part contains metadata
    metadata = {
        "source": "unknown",
        "date": default_date,
        "language": None
    }
    
    start_idx = 0
    if parts and parts[0].strip():
        first_part = parts[0].strip()
        lines = first_part.split('\n')
        
        is_metadata = False
        for line in lines:
            line = line.strip()
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip().lower()
                value = value.strip()
                
                if key == "source":
                    metadata["source"] = value
                    is_metadata = True
                elif key == "date":
                    metadata["date"] = value
                    is_metadata = True
                elif key == "language":
                    metadata["language"] = value
                    is_metadata = True
        
        if is_metadata:
            start_idx = 1
    
    # Parse articles
    for i, part in enumerate(parts[start_idx:], start=1):
        content_text = part.strip()
        if not content_text:
            continue
        
        # Extract headline (first line) and body (rest)
        lines = content_text.split('\n', 1)
        headline = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        
        # Detect language if not specified
        language = metadata["language"] or _detect_language(headline + " " + body)
        
        # Parse source
        source_info = _parse_news_source(metadata["source"])
        
        articles.append({
            "id": f"{ticker}_{metadata['date']}_{i}",
            "ticker": ticker,
            "headline": headline,
            "body": body,
            "full_text": content_text,
            "date": metadata["date"],
            "language": language,
            "source": source_info["name"],
            "source_reliability": source_info["reliability"],
            "metadata": {
                "word_count": len(content_text.split()),
                "has_body": len(body) > 0
            }
        })
    
    return articles


def get_egx_news_from_csv(
    ticker: Annotated[str, "EGX ticker symbol (e.g., COMI, EAST), or 'ALL' for market news"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Number of days to look back for news"] = 7,
) -> Dict[str, Any]:
    """
    Load EGX company or market news from CSV file.
    
    Expected CSV format:
        date,headline,body,source,language
        2025-01-15,CIB Reports Strong Q4 Results,Commercial International Bank...,Enterprise.news,english
        2025-01-14,سهم CIB يرتفع,ارتفع سهم البنك التجاري الدولي...,Mubasher,arabic
    
    File locations:
        - Company news: {DATA_DIR}/egx_news/csv/{TICKER}_news.csv
        - Market news: {DATA_DIR}/egx_news/global/egx_market_news.csv
    
    Args:
        ticker: EGX ticker symbol or "ALL" for market-wide news
        curr_date: Current date
        look_back_days: How many days of news to retrieve
        
    Returns:
        Dict with structured news data and metadata
    """
    ticker = ticker.upper().replace('.CA', '').strip()
    
    # Determine file path
    if ticker == "ALL":
        csv_path = os.path.join(DATA_DIR, EGX_NEWS_DIR, "global", "egx_market_news.csv")
    else:
        csv_path = os.path.join(DATA_DIR, EGX_NEWS_DIR, "csv", f"{ticker}_news.csv")
    
    if not os.path.exists(csv_path):
        return {
            "ticker": ticker,
            "error": f"News CSV not found: {csv_path}",
            "articles": [],
            "total_articles": 0,
            "suggestion": f"Please export {ticker} news from Enterprise.news/Mubasher and save as CSV"
        }
    
    try:
        df = pd.read_csv(csv_path)
    except Exception as e:
        return {
            "ticker": ticker,
            "error": f"Failed to read CSV: {str(e)}",
            "articles": [],
            "total_articles": 0
        }
    
    # Normalize column names
    df.columns = df.columns.str.lower().str.strip().str.replace(' ', '_')
    
    # Validate required columns
    required_cols = ["date", "headline"]
    missing_cols = [c for c in required_cols if c not in df.columns]
    
    if missing_cols:
        return {
            "ticker": ticker,
            "error": f"Missing required columns: {missing_cols}",
            "articles": [],
            "total_articles": 0,
            "available_columns": list(df.columns)
        }
    
    # Parse dates and filter by range
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    
    curr_date_dt = pd.to_datetime(curr_date)
    start_date_dt = curr_date_dt - pd.Timedelta(days=look_back_days)
    
    df = df[(df['date'] >= start_date_dt) & (df['date'] <= curr_date_dt)]
    df = df.sort_values('date', ascending=False)
    
    if df.empty:
        return {
            "ticker": ticker,
            "date_range": {
                "start": start_date_dt.strftime("%Y-%m-%d"),
                "end": curr_date
            },
            "articles": [],
            "total_articles": 0,
            "message": f"No news found for {ticker} in the specified date range"
        }
    
    # Convert to structured articles
    articles = []
    for idx, row in df.iterrows():
        headline = str(row.get('headline', '')).strip()
        body = str(row.get('body', '')).strip() if 'body' in row else ""
        source_str = str(row.get('source', '')).strip() if 'source' in row else "unknown"
        language = str(row.get('language', '')).strip() if 'language' in row else None
        
        # Auto-detect language if not specified
        if not language or language == 'nan':
            language = _detect_language(headline + " " + body)
        
        # Parse source
        source_info = _parse_news_source(source_str)
        
        date_str = row['date'].strftime('%Y-%m-%d') if pd.notna(row['date']) else "unknown"
        
        articles.append({
            "id": f"{ticker}_{date_str}_{len(articles)+1}",
            "ticker": ticker if ticker != "ALL" else row.get('ticker', 'EGX'),
            "headline": headline,
            "body": body,
            "full_text": f"{headline}\n\n{body}" if body else headline,
            "date": date_str,
            "language": language,
            "source": source_info["name"],
            "source_reliability": source_info["reliability"],
            "metadata": {
                "word_count": len((headline + " " + body).split()),
                "has_body": len(body) > 0
            }
        })
    
    return {
        "ticker": ticker,
        "market": "EGX",
        "date_range": {
            "start": start_date_dt.strftime("%Y-%m-%d"),
            "end": curr_date
        },
        "articles": articles,
        "total_articles": len(articles),
        "languages_found": list(set(a["language"] for a in articles)),
        "sources_found": list(set(a["source"] for a in articles)),
        "source": "CSV file (Mubasher/Enterprise export)"
    }


def get_egx_news_combined(
    ticker: Annotated[str, "EGX ticker symbol (e.g., COMI, EAST)"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Number of days to look back for news"] = 7,
) -> Dict[str, Any]:
    """
    Load EGX news from both text files and CSV, combining results.
    Deduplicates by headline similarity.
    
    Returns:
        Dict with combined news from all sources
    """
    ticker = ticker.upper().replace('.CA', '').strip()
    
    # Get news from both sources
    text_news = get_egx_news_from_text(ticker, curr_date, look_back_days)
    csv_news = get_egx_news_from_csv(ticker, curr_date, look_back_days)
    
    # Combine articles
    all_articles = []
    seen_headlines = set()
    
    # Add text articles first
    for article in text_news.get("articles", []):
        if "error" not in article:
            headline_key = article.get("headline", "").lower()[:50]
            if headline_key not in seen_headlines:
                seen_headlines.add(headline_key)
                all_articles.append(article)
    
    # Add CSV articles, avoiding duplicates
    for article in csv_news.get("articles", []):
        if "error" not in article:
            headline_key = article.get("headline", "").lower()[:50]
            if headline_key not in seen_headlines:
                seen_headlines.add(headline_key)
                all_articles.append(article)
    
    # Sort by date descending
    all_articles.sort(key=lambda x: x.get("date", ""), reverse=True)
    
    # Collect all sources and languages
    sources = list(set(a.get("source", "unknown") for a in all_articles))
    languages = list(set(a.get("language", "unknown") for a in all_articles))
    
    return {
        "ticker": ticker,
        "market": "EGX",
        "date_range": {
            "start": (datetime.strptime(curr_date, "%Y-%m-%d") - relativedelta(days=look_back_days)).strftime("%Y-%m-%d"),
            "end": curr_date
        },
        "articles": all_articles,
        "total_articles": len(all_articles),
        "sources_found": sources,
        "languages_found": languages,
        "data_sources": {
            "text_files": text_news.get("total_articles", 0),
            "csv_files": csv_news.get("total_articles", 0)
        },
        "errors": {
            "text": text_news.get("error"),
            "csv": csv_news.get("error")
        }
    }