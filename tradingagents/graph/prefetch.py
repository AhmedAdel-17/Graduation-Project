# TradingAgents/graph/prefetch.py
"""
DataPrefetcher — eliminates tool-calling round-trips for News & Social analysts.

Instead of letting agents call tools (which adds 1 extra LLM round-trip each),
we call all data tools in parallel *before* the graph starts and inject the
results into the initial state.  News and Social analysts then check for this
pre-fetched data and skip their tool-binding/tool-call phase entirely — the
LLM only needs to reason over the data, not fetch it.

Savings: ~2 LLM calls, ~6,000 tokens, ~30-60s per trade date.
"""

import logging
import concurrent.futures
from typing import Any, Dict, Optional

logger = logging.getLogger("tradingagents.prefetch")


class DataPrefetcher:
    """
    Pre-fetches data for News and Social analysts in parallel before graph invocation.

    Usage:
        prefetcher = DataPrefetcher(config)
        prefetched = prefetcher.fetch_all(ticker, trade_date)
        # Merge prefetched into initial_state before graph.invoke(...)
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.target_market = config.get("target_market", "US")

    def _fetch_company_news(self, ticker: str, trade_date: str) -> str:
        """Fetch company-specific news."""
        try:
            if self.target_market == "EGX":
                from tradingagents.agents.utils.news_data_tools import get_egx_company_news
                result = get_egx_company_news.invoke({"ticker": ticker, "curr_date": trade_date})
            else:
                from tradingagents.agents.utils.agent_utils import get_news
                result = get_news.invoke({"ticker": ticker, "curr_date": trade_date, "look_back_days": 7})
            return str(result) if result else ""
        except Exception as e:
            logger.warning("Prefetch company news failed for %s: %s", ticker, e)
            return ""

    def _fetch_market_news(self, ticker: str, trade_date: str) -> str:
        """Fetch market-wide / global news."""
        try:
            if self.target_market == "EGX":
                from tradingagents.agents.utils.news_data_tools import get_egx_market_news
                result = get_egx_market_news.invoke({"curr_date": trade_date})
            else:
                from tradingagents.agents.utils.agent_utils import get_global_news
                result = get_global_news.invoke({"curr_date": trade_date, "look_back_days": 7})
            return str(result) if result else ""
        except Exception as e:
            logger.warning("Prefetch market news failed: %s", e)
            return ""

    def _fetch_social_sentiment(self, ticker: str, trade_date: str) -> str:
        """Fetch pre-aggregated social sentiment scores."""
        try:
            from tradingagents.agents.utils.social_media_tools import get_social_sentiment
            result = get_social_sentiment.invoke({"ticker": ticker, "curr_date": trade_date})
            return str(result) if result else ""
        except Exception as e:
            logger.warning("Prefetch social sentiment failed for %s: %s", ticker, e)
            return ""

    def _fetch_social_posts(self, ticker: str, trade_date: str) -> str:
        """Fetch actual social media post content."""
        try:
            from tradingagents.agents.utils.social_media_tools import get_social_media_posts
            result = get_social_media_posts.invoke({"ticker": ticker, "curr_date": trade_date})
            return str(result) if result else ""
        except Exception as e:
            logger.warning("Prefetch social posts failed for %s: %s", ticker, e)
            return ""

    def _fetch_macro_context(self, ticker: str, trade_date: str) -> Any:
        """
        Fetch EGX macro indicators (CBE rate, USD/EGP, EGX30 trend, CPI, etc.).

        Returns a dict on success, or None on total failure.
        The dict is stored in state as `macro_context` (not a string like the
        other prefetch keys — it's a structured dict consumed by prompt builders).
        """
        if self.target_market != "EGX":
            return None
        try:
            from tradingagents.dataflows.macro_provider import get_egx_macro_context
            return get_egx_macro_context(as_of_date=str(trade_date), config=self.config)
        except Exception as e:
            logger.warning("Prefetch macro context failed: %s", e)
            return None

    def fetch_all(self, ticker: str, trade_date: str) -> Dict[str, Any]:
        """
        Fetch all data sources in parallel.

        Returns a dict with keys:
          - prefetched_company_news     (str)
          - prefetched_market_news      (str)
          - prefetched_social_sentiment (str)
          - prefetched_social_posts     (str)
          - macro_context               (dict | None)  — EGX only
        """
        logger.info("Prefetching data for %s on %s ...", ticker, trade_date)

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                "prefetched_company_news": executor.submit(
                    self._fetch_company_news, ticker, str(trade_date)
                ),
                "prefetched_market_news": executor.submit(
                    self._fetch_market_news, ticker, str(trade_date)
                ),
                "prefetched_social_sentiment": executor.submit(
                    self._fetch_social_sentiment, ticker, str(trade_date)
                ),
                "prefetched_social_posts": executor.submit(
                    self._fetch_social_posts, ticker, str(trade_date)
                ),
                "macro_context": executor.submit(
                    self._fetch_macro_context, ticker, str(trade_date)
                ),
            }

            results = {}
            for key, future in futures.items():
                try:
                    results[key] = future.result(timeout=30)
                except concurrent.futures.TimeoutError:
                    logger.warning("Prefetch timeout for %s", key)
                    results[key] = "" if key != "macro_context" else None
                except Exception as e:
                    logger.warning("Prefetch error for %s: %s", key, e)
                    results[key] = "" if key != "macro_context" else None

        non_empty_str = sum(1 for k, v in results.items() if k != "macro_context" and v)
        macro_ok = results.get("macro_context") is not None
        logger.info(
            "Prefetch complete for %s: %d/4 news/social sources populated, macro=%s",
            ticker, non_empty_str, "OK" if macro_ok else "unavailable",
        )
        return results
