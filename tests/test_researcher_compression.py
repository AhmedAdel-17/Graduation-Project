"""Tests for Bull/Bear researcher prompt compression (Phase 2d).

Covers:
  - When structured JSON analyses are available, full verbose text reports
    must NOT appear in the LLM prompt
  - When no structured JSON exists, full text reports are used as fallback
  - Both bull and bear researchers apply the same logic
"""
import json
import pytest
from unittest.mock import MagicMock, patch


BULL_RESPONSE = """
Bull case for COMI: Strong technicals and undervaluation.
```json
{
    "thesis_type": "bullish",
    "direction": "BUY",
    "conviction_level": "high",
    "time_horizon": {"primary": "medium_term", "entry_window": "1-2 sessions", "expected_duration": "3 months"},
    "signal_summary": {"technical": "bullish", "fundamental": "bullish", "sentiment": "bullish", "alignment_score": "strong"},
    "liquidity_assessment": {"is_low_liquidity": false, "recommended_position_size": "5% of portfolio", "execution_strategy": "VWAP over 2 sessions"},
    "key_catalysts": ["Earnings beat", "EGX30 momentum"],
    "target_range": {"low": 88, "mid": 95, "high": 103, "currency": "EGP"},
    "invalidation_conditions": ["Price closes below 70 EGP on high volume", "Q4 earnings miss"],
    "risk_reward_ratio": "2.5:1"
}
```
"""

BEAR_RESPONSE = """
Bear case for COMI: Macro risks and currency pressure.
```json
{
    "thesis_type": "bearish",
    "direction": "HOLD",
    "conviction_level": "moderate",
    "time_horizon": {"primary": "short_term"},
    "signal_summary": {"technical": "neutral", "fundamental": "bearish", "sentiment": "neutral", "alignment_score": "weak"},
    "liquidity_assessment": {"is_low_liquidity": false},
    "key_risks": ["EGP devaluation risk", "Rising NPL ratio"],
    "downside_risk": 68,
    "invalidation_conditions": ["EGP stabilises above 50/USD", "NPL ratio improves below 5%"],
    "risk_reward_ratio": "1:2"
}
```
"""


def _researcher_state(with_json_analyses: bool) -> dict:
    state = {
        "company_of_interest": "COMI.CA",
        "trade_date": "2024-01-15",
        "market_report": "VERY LONG MARKET TEXT " * 100,
        "fundamentals_report": "VERY LONG FUNDAMENTALS " * 100,
        "news_report": "VERY LONG NEWS " * 100,
        "sentiment_report": "VERY LONG SENTIMENT " * 100,
        "investment_debate_state": {
            "history": "",
            "bull_history": "",
            "bear_history": "",
            "current_response": "",
            "judge_decision": "",
            "count": 0,
        },
        "low_liquidity": False,
    }
    if with_json_analyses:
        state["technical_analysis"] = {"trend_direction": "bullish", "confidence_score": 72}
        state["fundamental_analysis"] = {"financial_health": "strong", "valuation_gap": "undervalued"}
        state["sentiment_analysis"] = {"sentiment": "bullish", "confidence_score": 65}
    else:
        state["technical_analysis"] = {}
        state["fundamental_analysis"] = {}
        state["sentiment_analysis"] = {}
    return state


class TestBullResearcherCompression:
    def test_json_mode_drops_verbose_market_report(self):
        """When structured JSON available, full text market report must NOT appear in prompt."""
        captured = []

        class SpyLLM:
            def invoke(self, prompt, *a, **kw):
                captured.append(prompt if isinstance(prompt, str) else str(prompt))
                return type("R", (), {"content": BULL_RESPONSE})()

        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        from tradingagents.agents.researchers.bull_researcher import create_bull_researcher

        node = create_bull_researcher(SpyLLM(), memory)
        node(_researcher_state(with_json_analyses=True))

        assert captured, "LLM was never invoked"
        prompt = captured[0]
        assert "VERY LONG MARKET TEXT" not in prompt, \
            "Full text market report should be dropped when JSON available (Phase 2d)"
        assert "trend_direction" in prompt, \
            "JSON technical_analysis should appear in prompt"

    def test_json_mode_drops_verbose_fundamentals_report(self):
        captured = []

        class SpyLLM:
            def invoke(self, prompt, *a, **kw):
                captured.append(prompt if isinstance(prompt, str) else str(prompt))
                return type("R", (), {"content": BULL_RESPONSE})()

        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        from tradingagents.agents.researchers.bull_researcher import create_bull_researcher

        node = create_bull_researcher(SpyLLM(), memory)
        node(_researcher_state(with_json_analyses=True))

        prompt = captured[0]
        assert "VERY LONG FUNDAMENTALS" not in prompt, \
            "Full fundamentals report should be dropped when JSON available"
        assert "financial_health" in prompt, \
            "JSON fundamental_analysis should appear in prompt"

    def test_fallback_mode_uses_text_reports(self):
        """When no structured JSON, prompt must use full text reports as fallback."""
        captured = []

        class SpyLLM:
            def invoke(self, prompt, *a, **kw):
                captured.append(prompt if isinstance(prompt, str) else str(prompt))
                return type("R", (), {"content": BULL_RESPONSE})()

        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        from tradingagents.agents.researchers.bull_researcher import create_bull_researcher

        node = create_bull_researcher(SpyLLM(), memory)
        node(_researcher_state(with_json_analyses=False))

        prompt = captured[0]
        assert "VERY LONG MARKET TEXT" in prompt, \
            "Should fall back to full text reports when no JSON available"

    def test_bull_node_returns_state_keys(self):
        """Bull node must return investment_debate_state in output."""
        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        from tests.conftest import MockLLM
        from tradingagents.agents.researchers.bull_researcher import create_bull_researcher

        node = create_bull_researcher(MockLLM(BULL_RESPONSE), memory)
        result = node(_researcher_state(with_json_analyses=True))

        assert "investment_debate_state" in result


class TestBearResearcherCompression:
    def test_json_mode_drops_verbose_reports(self):
        """Bear researcher should also skip verbose reports when JSON available."""
        captured = []

        class SpyLLM:
            def invoke(self, prompt, *a, **kw):
                captured.append(prompt if isinstance(prompt, str) else str(prompt))
                return type("R", (), {"content": BEAR_RESPONSE})()

        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        from tradingagents.agents.researchers.bear_researcher import create_bear_researcher

        node = create_bear_researcher(SpyLLM(), memory)
        node(_researcher_state(with_json_analyses=True))

        assert captured, "LLM was never invoked"
        prompt = captured[0]
        assert "VERY LONG MARKET TEXT" not in prompt, \
            "Bear researcher should also drop verbose reports when JSON available"

    def test_bear_fallback_uses_text_reports(self):
        captured = []

        class SpyLLM:
            def invoke(self, prompt, *a, **kw):
                captured.append(prompt if isinstance(prompt, str) else str(prompt))
                return type("R", (), {"content": BEAR_RESPONSE})()

        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        from tradingagents.agents.researchers.bear_researcher import create_bear_researcher

        node = create_bear_researcher(SpyLLM(), memory)
        node(_researcher_state(with_json_analyses=False))

        prompt = captured[0]
        assert "VERY LONG MARKET TEXT" in prompt
