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
from datetime import datetime, timezone


BACKTEST_NEWS_TOLERANCE_DAYS = 1


def _is_historical_backtest_date(curr_date: str) -> bool:
    """Return True when live news would leak future data for ``curr_date``."""
    try:
        parsed = datetime.strptime(str(curr_date), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return False
    return (datetime.now(timezone.utc).date() - parsed).days > BACKTEST_NEWS_TOLERANCE_DAYS


_ARTICLE_DROP_KEYS = {"url", "sentiment", "raw_text", "full_text", "content"}


def _slim_articles(articles: list) -> list:
    """Strip high-token fields that add no analytical value for the LLM.

    url: Google News URLs are base64-encoded and can be 400+ chars each.
    sentiment/raw_text/full_text/content: either null or redundant with summary.
    summary: truncate to 300 chars so the LLM gets the gist without token bloat.
    """
    slimmed = []
    for a in articles:
        slim = {k: v for k, v in a.items() if k not in _ARTICLE_DROP_KEYS}
        if "summary" in slim and slim["summary"]:
            slim["summary"] = slim["summary"][:300]
        slimmed.append(slim)
    return slimmed


def _fetch_live_news(ticker_clean: str, look_back_days: int) -> dict:
    """
    Pull news from the 12-source live aggregator (Mubasher, Al Borsa,
    Google News AR/EN, NewsAPI, EGX disclosures, RSS feeds, etc.).

    Returns a dict shaped like the local-CSV response so callers can use
    either source interchangeably. Returns None on failure.
    """
    try:
        from tradingagents.dataflows.news_providers.aggregator import (
            fetch_aggregated_news,
        )
        agg = fetch_aggregated_news(ticker_clean, days=look_back_days)
        if not isinstance(agg, dict):
            return None
        articles = agg.get("articles", []) or []
        if not articles:
            return None

        articles = _slim_articles(articles)
        sources = sorted({a.get("source", "?") for a in articles if a.get("source")})
        languages = sorted({a.get("language", "?") for a in articles if a.get("language")})

        return {
            "ticker": ticker_clean,
            "market": "EGX",
            "articles": articles,
            "total_articles": len(articles),
            "sources_found": sources,
            "languages_found": languages,
            "data_sources": {
                "live_aggregator": True,
                "providers_queried": agg.get("sources_queried", []),
                "providers_failed": agg.get("sources_failed", []),
            },
        }
    except Exception as e:
        import logging
        logging.getLogger("tradingagents.news_data_tools").warning(
            "Live news aggregator failed for %s: %s", ticker_clean, e
        )
        return None


@tool
def get_egx_company_news(
    ticker: Annotated[str, "EGX ticker symbol (e.g., COMI, EAST)"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Number of days to look back"] = 7,
) -> str:
    """
    Retrieve EGX company news.

    Source priority chain:
      1. Live aggregator — Mubasher, Al Borsa, Google News (AR/EN), NewsAPI,
         EGX disclosure portal, RSS feeds. Returns 10-30 articles when working.
      2. Local CSV / text files — last-resort fallback for offline runs.

    Returns JSON with language and source metadata.
    """
    # Clamp curr_date to trade_date to prevent future news leakage
    from tradingagents.dataflows.config import get_config
    _trade_date = get_config().get("trade_date")
    if _trade_date and curr_date > _trade_date:
        curr_date = _trade_date

    # Normalise ticker for the aggregator (it expects bare symbol, no .CA)
    ticker_clean = ticker.upper().replace(".CA", "")

    # ── 1. Try live aggregator first (12 sources) ──────────────────────────
    # Live RSS/Google/NewsAPI sources are wall-clock based. In historical
    # backtests they would leak current articles into past decisions, so use
    # only date-filtered local/historical sources for old trade dates.
    if not _is_historical_backtest_date(curr_date):
        live = _fetch_live_news(ticker_clean, look_back_days)
        if live and live.get("total_articles", 0) > 0:
            return json.dumps(live, indent=2, ensure_ascii=False, default=str)

    # ── 2. Fall back to local CSV / text files ─────────────────────────────
    result = get_egx_news_combined(ticker, curr_date, look_back_days)
    return json.dumps(result, indent=2, ensure_ascii=False, default=str)


@tool
def get_egx_market_news(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Number of days to look back"] = 7,
) -> str:
    """
    Retrieve EGX market-wide news (not company-specific).

    Source priority chain:
      1. Live aggregator with EGX-wide query (Mubasher, Al Borsa, Google News
         macro searches, NewsAPI EGX queries).
      2. Local CSV / text files — last-resort fallback.
    """
    from tradingagents.dataflows.config import get_config
    _trade_date = get_config().get("trade_date")
    if _trade_date and curr_date > _trade_date:
        curr_date = _trade_date

    # ── 1. Live aggregator with broad EGX query ───────────────────────────
    # Skip wall-clock news providers for historical backtests.
    if not _is_historical_backtest_date(curr_date):
        live = _fetch_live_news("EGX", look_back_days)
        if live and live.get("total_articles", 0) > 0:
            return json.dumps(live, indent=2, ensure_ascii=False, default=str)

    # ── 2. Fall back to local CSV ─────────────────────────────────────────
    result = get_egx_news_from_csv("ALL", curr_date, look_back_days)
    return json.dumps(result, indent=2, ensure_ascii=False, default=str)
