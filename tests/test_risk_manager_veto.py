"""Tests for Risk Manager deterministic veto + prompt compression (Phase 3b/3c).

Covers:
  - When approved=False, LLM.invoke() must NOT be called (zero LLM invocations)
  - When approved=True, LLM is invoked for qualitative judgment
  - Veto path sets final_trade_decision="HOLD" and risk_veto=True
  - Approved-path prompt uses compact exec_plan JSON, not verbose text reports
"""
import json
import pytest
from unittest.mock import patch, MagicMock

from tests.conftest import MockLLM


def _build_state(approved_override=None, decision: str = "BUY"):
    """Build a state dict that covers risk manager's expected inputs."""
    exec_plan = {
        "symbol": "COMI.CA",
        "decision": decision,
        "position_sizing": {"target_shares": 1000},
        "entry_logic": {"order_type": "limit", "entry_zone": {"limit_price": 77.5}},
        "exit_logic": {"stop_loss": {"price": 73.0}, "take_profit": {"price": 95.0}},
    }
    return {
        "company_of_interest": "COMI.CA",
        "trade_date": "2024-01-15",
        "market_report": "RSI 31.2, MACD bullish crossover.",
        "sentiment_report": "Moderate positive.",
        "news_report": "Q3 beat.",
        "fundamentals_report": "P/E 8.2x, ROE 22.4%.",
        "trader_investment_plan": json.dumps({"execution_plan": exec_plan}),
        "execution_plan": {"execution_plan": exec_plan},
        "risk_debate_state": {
            "history": "Risky: BUY is good.\nSafe: Be careful.\nNeutral: Balanced view.",
            "risky_history": "",
            "safe_history": "",
            "neutral_history": "",
            "current_risky_response": "",
            "current_safe_response": "",
            "current_neutral_response": "",
            "latest_speaker": "",
            "judge_decision": "",
            "count": 3,
        },
        "investment_plan": "BUY COMI.",
        "portfolio_value": 10_000_000,
        "avg_daily_volume": 500_000,
        "current_price": 77.5,
        "low_liquidity": False,
        "current_position": {},
        # Optional override so tests can simulate approved/rejected without
        # running real risk_checks
        "_test_approved_override": approved_override,
    }


def _make_violation(severity="critical"):
    """Create a mock RiskViolation-like object."""
    v = MagicMock()
    v.severity = severity
    v.rule_name = "no_short_selling"
    v.explanation = "Short detected"
    v.limit_value = "N/A"
    v.actual_value = "SELL without position"
    v.remediation = "Remove short or close position first"
    v.to_dict.return_value = {
        "rule_name": v.rule_name,
        "severity": v.severity,
        "explanation": v.explanation,
    }
    return v


class TestRiskManagerVeto:
    """Phase 3b: Early return when deterministic check vetoes."""

    def test_veto_returns_hold_without_llm_call(self):
        """When approved=False, risk manager must return immediately — 0 LLM calls."""
        llm = MockLLM("This should never be called")
        llm.invoke = MagicMock(side_effect=AssertionError(
            "LLM.invoke must NOT be called on the veto path"
        ))

        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        from tradingagents.agents.managers.risk_manager import create_risk_manager

        with patch("tradingagents.agents.managers.risk_manager.get_config",
                   return_value={"target_market": "EGX", "backtest_mode": False}), \
             patch("tradingagents.agents.managers.risk_manager.run_all_risk_checks",
                   return_value=(False, [_make_violation("critical")])):
            node = create_risk_manager(llm, memory)
            result = node(_build_state())

        assert result["final_trade_decision"] == "HOLD"
        assert result["risk_veto"] is True
        llm.invoke.assert_not_called()

    def test_veto_sets_risk_assessment_approved_false(self):
        """risk_assessment.approved should be False on the veto path."""
        llm = MockLLM("ignored")
        llm.invoke = MagicMock(side_effect=AssertionError("no LLM on veto"))

        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        from tradingagents.agents.managers.risk_manager import create_risk_manager

        with patch("tradingagents.agents.managers.risk_manager.get_config",
                   return_value={"target_market": "EGX", "backtest_mode": False}), \
             patch("tradingagents.agents.managers.risk_manager.run_all_risk_checks",
                   return_value=(False, [_make_violation("critical")])):
            node = create_risk_manager(llm, memory)
            result = node(_build_state())

        assert result["risk_assessment"]["approved"] is False

    def test_veto_preserves_risk_debate_state(self):
        """Veto early-return must not wipe existing risk_debate_state."""
        llm = MockLLM("ignored")
        llm.invoke = MagicMock(side_effect=AssertionError("no LLM on veto"))

        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        from tradingagents.agents.managers.risk_manager import create_risk_manager

        state = _build_state()
        state["risk_debate_state"]["history"] = "previous debate text"

        with patch("tradingagents.agents.managers.risk_manager.get_config",
                   return_value={"target_market": "EGX", "backtest_mode": False}), \
             patch("tradingagents.agents.managers.risk_manager.run_all_risk_checks",
                   return_value=(False, [_make_violation("critical")])):
            node = create_risk_manager(llm, memory)
            result = node(state)

        assert result["risk_debate_state"]["history"] == "previous debate text"


class TestRiskManagerApprovedPath:
    """Phase 3b: When approved=True, LLM must be invoked."""

    def test_approved_path_calls_llm(self):
        """When approved=True, LLM is invoked for qualitative judgment."""
        llm = MockLLM(
            'Risk approved.\n```json\n{"action": "BUY", "confidence": 0.85}\n```'
        )

        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        from tradingagents.agents.managers.risk_manager import create_risk_manager

        with patch("tradingagents.agents.managers.risk_manager.get_config",
                   return_value={"target_market": "EGX", "backtest_mode": False}), \
             patch("tradingagents.agents.managers.risk_manager.run_all_risk_checks",
                   return_value=(True, [])):
            node = create_risk_manager(llm, memory)
            result = node(_build_state())

        # On approved path, LLM decides the final signal
        assert result["final_trade_decision"] in ("BUY", "SELL", "HOLD")
        assert result.get("risk_veto") is not True

    def test_approved_path_risk_veto_not_set(self):
        """risk_veto should not be True when checks pass."""
        llm = MockLLM('```json\n{"action": "BUY", "confidence": 0.9}\n```')

        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        from tradingagents.agents.managers.risk_manager import create_risk_manager

        with patch("tradingagents.agents.managers.risk_manager.get_config",
                   return_value={"target_market": "EGX", "backtest_mode": False}), \
             patch("tradingagents.agents.managers.risk_manager.run_all_risk_checks",
                   return_value=(True, [])):
            node = create_risk_manager(llm, memory)
            result = node(_build_state())

        assert not result.get("risk_veto")


class TestRiskManagerPromptCompression:
    """Phase 3c: Approved-path prompt should use compact exec_plan JSON."""

    def test_prompt_contains_exec_plan_reference(self):
        """Verify LLM receives the execution plan symbol in its prompt."""
        captured_prompts = []

        class CaptureLLM:
            def invoke(self, prompt, *a, **kw):
                captured_prompts.append(
                    prompt if isinstance(prompt, str) else str(prompt)
                )
                return type("R", (), {"content": '```json\n{"action": "BUY", "confidence": 0.8}\n```'})()

        memory = MagicMock()
        memory.get_memories = MagicMock(return_value=[])

        from tradingagents.agents.managers.risk_manager import create_risk_manager

        with patch("tradingagents.agents.managers.risk_manager.get_config",
                   return_value={"target_market": "EGX", "backtest_mode": False}), \
             patch("tradingagents.agents.managers.risk_manager.run_all_risk_checks",
                   return_value=(True, [])):
            node = create_risk_manager(CaptureLLM(), memory)
            node(_build_state())

        assert captured_prompts, "LLM was not invoked on the approved path"
        prompt = captured_prompts[0]
        # The exec plan's ticker or a compact reference to it must appear
        assert "COMI.CA" in prompt
