"""Tests for merged risk debator (Phase 3a).

Covers:
  - All 3 perspective sections are extracted into the output state
  - count is incremented by 3 in a single call
  - Full analyst text reports are NOT passed to the LLM prompt (token savings)
  - Graceful handling when execution_plan is absent
  - Regex extraction fallback works when emoji headings are present
"""
import json
import pytest
from unittest.mock import patch

from tests.conftest import MockLLM


MERGED_RESPONSE = """
### 🔴 RISKY ANALYST (Risk-Taking Perspective)
The risk/reward clearly favors action. COMI at 77 EGP is 15% below intrinsic value.
Upside target: 95 EGP within 3 months. Historical precedent: post-Q3 rallies averaged 12%.

### 🟢 SAFE ANALYST (Conservative Perspective)
Capital preservation is paramount. If EGP devalues further, real returns erode.
Downside risk: 68 EGP (-12%). Low liquidity means 3+ days to exit full position.

### 🟡 NEUTRAL ANALYST (Balanced Perspective)
Both sides have merit. The bull case depends on no macro shock. The bear case
overstates currency risk. Risk-adjusted, a HALF position is optimal.

### 📋 SYNTHESIS
Key tension: strong micro fundamentals vs. macro/liquidity headwinds. Risk Manager
should weigh the stop-loss placement most heavily — a 73 EGP stop provides 6% downside
vs. 23% upside.
"""


@pytest.fixture
def mock_state():
    return {
        "risk_debate_state": {"history": "", "count": 0},
        "execution_plan": {
            "execution_plan": {
                "symbol": "COMI.CA",
                "decision": "BUY",
                "conviction": "moderate",
            }
        },
        "technical_analysis": {"trend_direction": "bullish", "confidence_score": 72},
        "fundamental_analysis": {"financial_health": "strong", "valuation_gap": "undervalued"},
        "sentiment_analysis": {"sentiment": "bullish", "confidence_score": 65},
        "investment_debate_state": {
            "bull_thesis": {"direction": "BUY", "target_price": 95},
            "bear_thesis": {"direction": "HOLD", "downside_risk": 68},
        },
        "investment_plan": "BUY COMI with moderate conviction.",
        "trader_investment_plan": "Execute BUY via VWAP over 2 sessions.",
        "company_of_interest": "COMI.CA",
        "low_liquidity": False,
    }


class TestMergedRiskDebateOutput:
    def test_produces_all_three_sections(self, mock_state):
        from tradingagents.agents.risk_mgmt.merged_debator import create_merged_risk_debator

        llm = MockLLM(MERGED_RESPONSE)
        with patch("tradingagents.agents.risk_mgmt.merged_debator.get_config",
                   return_value={"target_market": "EGX"}):
            node = create_merged_risk_debator(llm)
            result = node(mock_state)

        rds = result["risk_debate_state"]
        assert rds["current_risky_response"], "Risky section not extracted"
        assert rds["current_safe_response"], "Safe section not extracted"
        assert rds["current_neutral_response"], "Neutral section not extracted"

    def test_count_incremented_by_three(self, mock_state):
        from tradingagents.agents.risk_mgmt.merged_debator import create_merged_risk_debator

        llm = MockLLM(MERGED_RESPONSE)
        with patch("tradingagents.agents.risk_mgmt.merged_debator.get_config",
                   return_value={"target_market": "EGX"}):
            node = create_merged_risk_debator(llm)
            result = node(mock_state)

        assert result["risk_debate_state"]["count"] == 3

    def test_count_accumulates_from_existing(self, mock_state):
        """count starts at N and should end at N+3."""
        mock_state["risk_debate_state"]["count"] = 6
        from tradingagents.agents.risk_mgmt.merged_debator import create_merged_risk_debator

        llm = MockLLM(MERGED_RESPONSE)
        with patch("tradingagents.agents.risk_mgmt.merged_debator.get_config",
                   return_value={"target_market": "EGX"}):
            node = create_merged_risk_debator(llm)
            result = node(mock_state)

        assert result["risk_debate_state"]["count"] == 9

    def test_history_appended_to_existing(self, mock_state):
        """Prior history should be preserved and new debate appended."""
        mock_state["risk_debate_state"]["history"] = "Prior round.\n"
        from tradingagents.agents.risk_mgmt.merged_debator import create_merged_risk_debator

        llm = MockLLM(MERGED_RESPONSE)
        with patch("tradingagents.agents.risk_mgmt.merged_debator.get_config",
                   return_value={"target_market": "EGX"}):
            node = create_merged_risk_debator(llm)
            result = node(mock_state)

        history = result["risk_debate_state"]["history"]
        assert "Prior round." in history
        assert "RISKY ANALYST" in history


class TestMergedRiskDebatePromptCompression:
    def test_does_not_pass_full_analyst_reports(self, mock_state):
        """Prompt must NOT contain verbose full-text analyst reports (Phase 3a)."""
        call_args = []

        class SpyLLM:
            def invoke(self, prompt, *a, **kw):
                call_args.append(prompt)
                return type("R", (), {"content": MERGED_RESPONSE})()

        from tradingagents.agents.risk_mgmt.merged_debator import create_merged_risk_debator

        with patch("tradingagents.agents.risk_mgmt.merged_debator.get_config",
                   return_value={"target_market": "EGX"}):
            node = create_merged_risk_debator(SpyLLM())
            # Inject very long text reports that should NOT appear in the prompt
            mock_state["market_report"] = "VERY LONG MARKET REPORT " * 200
            mock_state["fundamentals_report"] = "VERY LONG FUND REPORT " * 200
            node(mock_state)

        assert call_args, "LLM was never invoked"
        prompt_text = call_args[0] if isinstance(call_args[0], str) else str(call_args[0])
        assert "VERY LONG MARKET REPORT" not in prompt_text, \
            "Full market report leaked into merged risk debate prompt"
        assert "VERY LONG FUND REPORT" not in prompt_text, \
            "Full fundamentals report leaked into merged risk debate prompt"

    def test_exec_plan_json_present_in_prompt(self, mock_state):
        """Execution plan JSON should appear in the prompt."""
        call_args = []

        class SpyLLM:
            def invoke(self, prompt, *a, **kw):
                call_args.append(prompt)
                return type("R", (), {"content": MERGED_RESPONSE})()

        from tradingagents.agents.risk_mgmt.merged_debator import create_merged_risk_debator

        with patch("tradingagents.agents.risk_mgmt.merged_debator.get_config",
                   return_value={"target_market": "EGX"}):
            node = create_merged_risk_debator(SpyLLM())
            node(mock_state)

        prompt_text = call_args[0]
        assert "COMI.CA" in prompt_text


class TestMergedRiskDebateEdgeCases:
    def test_handles_empty_execution_plan(self, mock_state):
        """Should not crash even when execution_plan is empty."""
        mock_state["execution_plan"] = {}
        from tradingagents.agents.risk_mgmt.merged_debator import create_merged_risk_debator

        llm = MockLLM(MERGED_RESPONSE)
        with patch("tradingagents.agents.risk_mgmt.merged_debator.get_config",
                   return_value={"target_market": "EGX"}):
            node = create_merged_risk_debator(llm)
            result = node(mock_state)  # Must not raise

        assert "risk_debate_state" in result

    def test_handles_missing_investment_debate_state(self, mock_state):
        """Should not crash when investment_debate_state is absent."""
        del mock_state["investment_debate_state"]
        from tradingagents.agents.risk_mgmt.merged_debator import create_merged_risk_debator

        llm = MockLLM(MERGED_RESPONSE)
        with patch("tradingagents.agents.risk_mgmt.merged_debator.get_config",
                   return_value={"target_market": "EGX"}):
            node = create_merged_risk_debator(llm)
            result = node(mock_state)  # Must not raise

        assert "risk_debate_state" in result

    def test_section_fallback_when_no_headings(self, mock_state):
        """When LLM omits emoji headings, the slice-based fallback is used.

        The fallback slices debate_text as [:800], [800:1600], [1600:2400].
        The response must be >800 chars to produce a non-empty safe section.
        """
        # Build a response long enough (>1600 chars) so all three slices are non-empty
        risky_part = ("The risk is worth taking. COMI at 77 EGP is 15% below fair value. "
                      "Upside target 95 EGP. Post-Q3 rallies averaged 12%. " * 10)
        safe_part = ("Capital preservation is paramount. EGP devaluation risk is real. "
                     "Downside scenario to 68 EGP (-12%). Low liquidity exit risk. " * 10)
        plain_response = risky_part + "\n\n" + safe_part

        from tradingagents.agents.risk_mgmt.merged_debator import create_merged_risk_debator

        llm = MockLLM(plain_response)
        with patch("tradingagents.agents.risk_mgmt.merged_debator.get_config",
                   return_value={"target_market": "EGX"}):
            node = create_merged_risk_debator(llm)
            result = node(mock_state)

        rds = result["risk_debate_state"]
        # The first slice [:800] always produces the risky section non-empty
        assert rds["current_risky_response"] != "", \
            "Risky section must be non-empty even without emoji headings"
        # The [800:1600] slice should also be non-empty since text > 800 chars
        assert rds["current_safe_response"] != "", \
            "Safe section must be non-empty when response is long enough for slice fallback"
