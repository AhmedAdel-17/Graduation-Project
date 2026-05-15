"""Tests for Research Manager structured thesis consumption (Phase 2e).

Covers:
  - Structured bull/bear theses are injected into the LLM prompt
  - Debate history is trimmed to last 4 lines
  - Bull/bear thesis dicts are preserved through to output state
  - Research manager returns an investment_plan with a decision
"""
import json
import pytest
from unittest.mock import MagicMock

from tests.conftest import MockLLM


RM_RESPONSE = """
After reviewing both theses, the bull case is stronger.
Decision: BUY with moderate conviction.
```json
{"decision": "BUY", "confidence": 0.75, "rationale": "Strong fundamentals with technical support."}
```
"""


def _rm_state(with_theses=True, history_lines=6):
    ids = {
        "history": "\n".join([f"Exchange {i}" for i in range(history_lines)]),
        "bull_history": "",
        "bear_history": "",
        "current_response": "",
        "judge_decision": "",
        "count": 2,
    }
    if with_theses:
        ids["bull_thesis"] = {"direction": "BUY", "target_price": 95, "conviction_level": "high"}
        ids["bear_thesis"] = {"direction": "HOLD", "downside_risk": 68, "conviction_level": "moderate"}

    return {
        "investment_debate_state": ids,
        "market_report": "x",
        "sentiment_report": "x",
        "news_report": "x",
        "fundamentals_report": "x",
        "company_of_interest": "COMI.CA",
        "current_position": {},
    }


class TestResearchManagerThesesConsumption:
    def test_bull_thesis_json_appears_in_prompt(self):
        """Structured bull thesis JSON must be injected into the LLM prompt."""
        captured = []

        class SpyLLM:
            def invoke(self, prompt, *a, **kw):
                captured.append(str(prompt))
                return type("R", (), {"content": RM_RESPONSE})()

        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        from tradingagents.agents.managers.research_manager import create_research_manager

        node = create_research_manager(SpyLLM(), memory)
        node(_rm_state(with_theses=True))

        assert captured, "LLM was not invoked"
        prompt = captured[0]
        assert "target_price" in prompt, \
            "Bull thesis JSON should appear in prompt (Phase 2e)"

    def test_bear_thesis_json_appears_in_prompt(self):
        captured = []

        class SpyLLM:
            def invoke(self, prompt, *a, **kw):
                captured.append(str(prompt))
                return type("R", (), {"content": RM_RESPONSE})()

        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        from tradingagents.agents.managers.research_manager import create_research_manager

        node = create_research_manager(SpyLLM(), memory)
        node(_rm_state(with_theses=True))

        prompt = captured[0]
        assert "downside_risk" in prompt, \
            "Bear thesis JSON should appear in prompt (Phase 2e)"

    def test_trims_history_to_last_4_lines(self):
        """Debate history should be trimmed to last 4 lines only."""
        captured = []

        class SpyLLM:
            def invoke(self, prompt, *a, **kw):
                captured.append(str(prompt))
                return type("R", (), {"content": RM_RESPONSE})()

        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        from tradingagents.agents.managers.research_manager import create_research_manager

        # Use 20 history lines
        long_history = "\n".join([f"Exchange {i}" for i in range(20)])
        state = _rm_state(with_theses=True, history_lines=0)
        state["investment_debate_state"]["history"] = long_history

        node = create_research_manager(SpyLLM(), memory)
        node(state)

        prompt = captured[0]
        # Old lines must be gone
        assert "Exchange 0" not in prompt, "Old history should be trimmed"
        # Recent lines must be present
        assert "Exchange 19" in prompt, "Most recent exchange should be present"

    def test_preserves_bull_bear_thesis_in_output_state(self):
        """Bull/bear thesis dicts must survive through to the output state."""
        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        from tradingagents.agents.managers.research_manager import create_research_manager

        node = create_research_manager(MockLLM(RM_RESPONSE), memory)
        result = node(_rm_state(with_theses=True))

        ids = result["investment_debate_state"]
        assert ids["bull_thesis"] == {
            "direction": "BUY",
            "target_price": 95,
            "conviction_level": "high",
        }
        assert ids["bear_thesis"] == {
            "direction": "HOLD",
            "downside_risk": 68,
            "conviction_level": "moderate",
        }

    def test_returns_investment_plan(self):
        """Output must include an investment_plan string."""
        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        from tradingagents.agents.managers.research_manager import create_research_manager

        node = create_research_manager(MockLLM(RM_RESPONSE), memory)
        result = node(_rm_state(with_theses=True))

        assert "investment_plan" in result
        assert isinstance(result["investment_plan"], str)
        assert len(result["investment_plan"]) > 0


class TestResearchManagerFallback:
    def test_no_theses_falls_back_to_history(self):
        """When no structured theses available, history text is used as fallback."""
        captured = []

        class SpyLLM:
            def invoke(self, prompt, *a, **kw):
                captured.append(str(prompt))
                return type("R", (), {"content": RM_RESPONSE})()

        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        from tradingagents.agents.managers.research_manager import create_research_manager

        state = _rm_state(with_theses=False, history_lines=4)
        node = create_research_manager(SpyLLM(), memory)
        node(state)

        prompt = captured[0]
        # When no structured thesis, recent history lines should appear
        assert "Exchange" in prompt, \
            "When no structured theses, history should appear in prompt"
