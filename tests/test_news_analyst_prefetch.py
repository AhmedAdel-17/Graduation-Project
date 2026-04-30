"""Integration tests: News Analyst pre-fetched mode vs tool-calling mode (Phase 2b).

These use a MockLLM to avoid live API calls. They verify:
  - When prefetched data is present, llm.invoke() is called directly (no bind_tools).
  - When no prefetched data, the standard tool-calling path invokes bind_tools.
"""
import pytest
from unittest.mock import MagicMock, patch

from tests.conftest import MockLLM


NEWS_JSON_RESPONSE = """
Analysis complete.
```json
{
    "sentiment": "bullish",
    "sentiment_strength": "moderate",
    "confidence_score": 65,
    "confidence_adjustments": ["limited sources"],
    "explanation": "Positive earnings report for COMI.",
    "key_headlines": [],
    "news_coverage": {
        "total_articles": 3,
        "sources_count": 2,
        "languages": ["english"],
        "date_range": "Jan 10-15"
    },
    "risks_from_news": [],
    "catalysts_from_news": ["earnings beat"]
}
```
"""


def _base_state(with_prefetch: bool) -> dict:
    state = {
        "trade_date": "2024-01-15",
        "company_of_interest": "COMI.CA",
        "news_messages": [],
    }
    if with_prefetch:
        state["prefetched_company_news"] = "COMI Q3 earnings beat expectations."
        state["prefetched_market_news"] = "EGX index up 1.2%."
    return state


class TestNewsAnalystPrefetchMode:
    def test_prefetch_mode_does_not_call_bind_tools(self):
        """When prefetched data is present, bind_tools must NOT be called."""
        mock_llm = MockLLM(NEWS_JSON_RESPONSE)
        # Override bind_tools so calling it raises — proves it is never invoked
        mock_llm.bind_tools = MagicMock(side_effect=AssertionError(
            "bind_tools should NOT be called in prefetch mode"
        ))

        from tradingagents.agents.analysts.news_analyst import create_news_analyst

        with patch("tradingagents.agents.analysts.news_analyst.get_config",
                   return_value={"target_market": "EGX"}):
            node = create_news_analyst(mock_llm)
            result = node(_base_state(with_prefetch=True))

        # bind_tools was never called (AssertionError not raised)
        mock_llm.bind_tools.assert_not_called()

    def test_prefetch_mode_returns_sentiment_analysis(self):
        """Prefetch path should return a populated sentiment_analysis dict."""
        mock_llm = MockLLM(NEWS_JSON_RESPONSE)

        from tradingagents.agents.analysts.news_analyst import create_news_analyst

        with patch("tradingagents.agents.analysts.news_analyst.get_config",
                   return_value={"target_market": "EGX"}):
            node = create_news_analyst(mock_llm)
            result = node(_base_state(with_prefetch=True))

        assert "sentiment_analysis" in result
        sa = result["sentiment_analysis"]
        assert sa is not None
        assert sa.get("sentiment") in ("bullish", "bearish", "neutral")

    def test_prefetch_mode_returns_news_report(self):
        """The news_report field should contain the LLM response text."""
        mock_llm = MockLLM(NEWS_JSON_RESPONSE)

        from tradingagents.agents.analysts.news_analyst import create_news_analyst

        with patch("tradingagents.agents.analysts.news_analyst.get_config",
                   return_value={"target_market": "EGX"}):
            node = create_news_analyst(mock_llm)
            result = node(_base_state(with_prefetch=True))

        assert "news_report" in result
        # The report should not be empty when LLM returned content
        assert result["news_report"] != "" or result["sentiment_analysis"] is not None


class TestNewsAnalystFallbackMode:
    def test_fallback_mode_calls_bind_tools(self):
        """When no prefetched data, standard tool-calling path must call bind_tools."""
        from langchain_core.runnables import RunnableLambda

        mock_llm = MockLLM(NEWS_JSON_RESPONSE)
        bind_tools_call_count = [0]
        original_bind_tools = mock_llm.bind_tools

        def tracking_bind_tools(tools, **kwargs):
            bind_tools_call_count[0] += 1
            return original_bind_tools(tools, **kwargs)  # Still returns proper Runnable

        mock_llm.bind_tools = tracking_bind_tools

        from tradingagents.agents.analysts.news_analyst import create_news_analyst

        with patch("tradingagents.agents.analysts.news_analyst.get_config",
                   return_value={"target_market": "EGX"}):
            node = create_news_analyst(mock_llm)
            node(_base_state(with_prefetch=False))

        assert bind_tools_call_count[0] == 1, \
            "bind_tools must be called exactly once in fallback (non-prefetch) mode"

    def test_fallback_mode_still_returns_state_keys(self):
        """Fallback path should still return news_report and sentiment_analysis."""
        mock_llm = MockLLM(NEWS_JSON_RESPONSE)

        from tradingagents.agents.analysts.news_analyst import create_news_analyst

        with patch("tradingagents.agents.analysts.news_analyst.get_config",
                   return_value={"target_market": "EGX"}):
            node = create_news_analyst(mock_llm)
            result = node(_base_state(with_prefetch=False))

        assert "news_report" in result
        assert "sentiment_analysis" in result
