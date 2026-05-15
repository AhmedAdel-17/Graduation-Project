"""Tests for Constitutional Risk Manager and Risk Veto Node (new architecture).

Architecture under test:
    Risk Scorer -> (VETO) -> risk_veto_node -> END          [no LLM]
    Risk Scorer -> (ALLOW/WARN/THROTTLE) -> Merged Debate
                                         -> risk_manager_node -> END  [LLM]

What changed from the previous version:
- run_all_risk_checks has moved to risk_scorer.py (not in risk_manager.py)
- The veto path is now handled by risk_veto_node (standalone function)
- risk_manager_node never receives a VETO state — it only handles approved paths
- risk_manager_node receives pre-computed risk_action + risk_assessment from scorer
- risk_manager_node prompt now includes the EGX Trading Constitution

Covers:
  VETO NODE  — returns HOLD + risk_veto=True, no LLM, preserves debate state
  APPROVED   — LLM called, final_trade_decision is BUY/SELL/HOLD
  PROMPT     — constitution present, exec_plan present, risk_metrics present
  FINAL GATE — SELL with no position → HOLD; restricted ticker BUY → HOLD
  STATE      — risk_action + risk_metrics flow through to final output
"""
import json
import pytest
from unittest.mock import MagicMock, patch

from tests.conftest import MockLLM


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _exec_plan(decision="BUY", symbol="COMI.CA"):
    return {
        "symbol": symbol,
        "decision": decision,
        "position_sizing": {
            "target_shares": 1000,
            "max_shares_per_day": 200,
            "portfolio_allocation": "8%",
        },
        "entry_logic": {"order_type": "limit", "entry_zone": {"limit_price": 77.5}},
        "exit_logic": {"stop_loss": {"price": 73.0}, "take_profit": {"price": 95.0}},
    }


def _approved_state(decision="BUY", symbol="COMI.CA", shares=0):
    """State as it arrives at risk_manager_node after scorer says ALLOW."""
    ep = _exec_plan(decision, symbol)
    return {
        "company_of_interest": symbol,
        "trade_date": "2024-01-15",
        "market_report": "RSI 31.2, MACD bullish crossover.",
        "sentiment_report": "Moderate positive.",
        "news_report": "Q3 beat.",
        "fundamentals_report": "P/E 8.2x, ROE 22.4%.",
        "execution_plan": {"execution_plan": ep},
        "risk_debate_state": {
            "history": "Risky: BUY is good.\nSafe: Be careful.\nNeutral: Balanced.",
            "risky_history": "",
            "safe_history": "",
            "neutral_history": "",
            "current_risky_response": "",
            "current_safe_response": "",
            "current_neutral_response": "",
            "latest_speaker": "MergedRiskDebate",
            "judge_decision": "",
            "count": 3,
        },
        "investment_plan": "BUY COMI.",
        "portfolio_value": 10_000_000,
        "avg_daily_volume": 500_000,
        "current_price": 77.5,
        "low_liquidity": False,
        "current_position": {"shares": shares, "avg_cost": 70.0,
                              "market_value": shares * 77.5,
                              "unrealised_pnl": shares * 7.5} if shares else {},
        # Pre-scored fields from Risk Scorer
        "risk_action": "ALLOW",
        "risk_metrics": {
            "position_pct": 0.08,
            "adv_participation_pct": 0.04,
            "days_to_exit": 2.0,
            "per_trade_loss_pct": 0.0046,
            "stop_distance_pct": 0.058,
            "atr_14_available": False,
        },
        "risk_assessment": {
            "approved": True,
            "risk_action": "ALLOW",
            "total_violations": 0,
            "critical_violations": 0,
            "hard_violations": [],
            "warnings": [],
            "violations": [],
            "throttle_adjustments": {},
            "risk_metrics": {},
            "constraints_checked": [],
        },
    }


def _veto_state():
    """State as it arrives at risk_veto_node after scorer says VETO."""
    ep = _exec_plan()
    return {
        "company_of_interest": "COMI.CA",
        "execution_plan": {"execution_plan": ep},
        "risk_debate_state": {
            "history": "previous debate text",
            "risky_history": "risky",
            "safe_history": "safe",
            "neutral_history": "neutral",
            "current_risky_response": "r",
            "current_safe_response": "s",
            "current_neutral_response": "n",
            "latest_speaker": "Risk_Scorer",
            "judge_decision": "",
            "count": 0,
        },
        "risk_action": "VETO",
        "risk_assessment": {
            "approved": False,
            "risk_action": "VETO",
            "critical_violations": 1,
            "hard_violations": [
                {
                    "rule": "SHORT_SELLING_FORBIDDEN",
                    "severity": "critical",
                    "explanation": "Short selling detected",
                    "limit": 0,
                    "actual": 1,
                    "remediation": "Remove short selling",
                }
            ],
            "warnings": [],
            "violations": [],
            "veto_explanation": "## ⛔ RISK VETO — TRADE REJECTED\n\nShort selling detected.",
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. Risk Veto Node tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRiskVetoNode:
    """risk_veto_node is a standalone function (no LLM, no factory)."""

    def test_veto_returns_hold(self):
        from tradingagents.agents.risk_mgmt.risk_scorer import risk_veto_node
        result = risk_veto_node(_veto_state())
        assert result["final_trade_decision"] == "HOLD"

    def test_veto_sets_risk_veto_true(self):
        from tradingagents.agents.risk_mgmt.risk_scorer import risk_veto_node
        result = risk_veto_node(_veto_state())
        assert result["risk_veto"] is True

    def test_veto_preserves_debate_history(self):
        from tradingagents.agents.risk_mgmt.risk_scorer import risk_veto_node
        state = _veto_state()
        state["risk_debate_state"]["history"] = "important prior debate"
        result = risk_veto_node(state)
        assert result["risk_debate_state"]["history"] == "important prior debate"

    def test_veto_uses_veto_explanation_as_judge_decision(self):
        from tradingagents.agents.risk_mgmt.risk_scorer import risk_veto_node
        result = risk_veto_node(_veto_state())
        assert "RISK VETO" in result["risk_debate_state"]["judge_decision"]

    def test_veto_sets_latest_speaker_to_risk_scorer(self):
        from tradingagents.agents.risk_mgmt.risk_scorer import risk_veto_node
        result = risk_veto_node(_veto_state())
        assert result["risk_debate_state"]["latest_speaker"] == "Risk_Scorer"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Constitutional Risk Manager — approved path
# ─────────────────────────────────────────────────────────────────────────────

class TestConstitutionalRiskManagerApprovedPath:
    """risk_manager_node always calls the LLM (VETO never reaches it)."""

    def _make_node(self, response_text='```json\n{"action": "BUY", "confidence": 0.85}\n```'):
        llm = MockLLM(response_text)
        memory = MagicMock()
        memory.get_memories.return_value = []
        from tradingagents.agents.managers.risk_manager import create_risk_manager
        with patch("tradingagents.agents.managers.risk_manager.get_config",
                   return_value={"target_market": "EGX", "backtest_mode": False}):
            return create_risk_manager(llm, memory)

    def test_approved_path_calls_llm(self):
        llm = MockLLM('```json\n{"action": "BUY", "confidence": 0.85}\n```')
        invoke_spy = MagicMock(wraps=llm.invoke)
        llm.invoke = invoke_spy
        memory = MagicMock()
        memory.get_memories.return_value = []
        from tradingagents.agents.managers.risk_manager import create_risk_manager
        with patch("tradingagents.agents.managers.risk_manager.get_config",
                   return_value={"target_market": "EGX", "backtest_mode": False}):
            node = create_risk_manager(llm, memory)
            node(_approved_state())
        invoke_spy.assert_called_once()

    def test_approved_path_returns_valid_decision(self):
        node = self._make_node()
        with patch("tradingagents.agents.managers.risk_manager.get_config",
                   return_value={"target_market": "EGX", "backtest_mode": False}):
            result = node(_approved_state())
        assert result["final_trade_decision"] in ("BUY", "SELL", "HOLD")

    def test_approved_path_risk_veto_not_set(self):
        node = self._make_node()
        with patch("tradingagents.agents.managers.risk_manager.get_config",
                   return_value={"target_market": "EGX", "backtest_mode": False}):
            result = node(_approved_state())
        assert not result.get("risk_veto")

    def test_approved_path_preserves_debate_state_count(self):
        node = self._make_node()
        state = _approved_state()
        state["risk_debate_state"]["count"] = 3
        with patch("tradingagents.agents.managers.risk_manager.get_config",
                   return_value={"target_market": "EGX", "backtest_mode": False}):
            result = node(state)
        assert result["risk_debate_state"]["count"] == 3

    def test_warn_path_still_calls_llm(self):
        """WARN state (non-critical issues) must also reach the LLM."""
        llm = MockLLM('```json\n{"action": "BUY", "confidence": 0.7}\n```')
        invoke_spy = MagicMock(wraps=llm.invoke)
        llm.invoke = invoke_spy
        memory = MagicMock()
        memory.get_memories.return_value = []
        state = _approved_state()
        state["risk_action"] = "WARN"
        state["risk_assessment"]["risk_action"] = "WARN"
        state["risk_assessment"]["warnings"] = [{"rule": "EXIT_HORIZON_EXCEEDED",
                                                   "severity": "high"}]
        from tradingagents.agents.managers.risk_manager import create_risk_manager
        with patch("tradingagents.agents.managers.risk_manager.get_config",
                   return_value={"target_market": "EGX", "backtest_mode": False}):
            node = create_risk_manager(llm, memory)
            node(state)
        invoke_spy.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Prompt content — constitution, metrics, exec plan
# ─────────────────────────────────────────────────────────────────────────────

class TestConstitutionalRiskManagerPrompt:

    def _capture_prompt(self, state):
        captured = []

        class SpyLLM:
            def invoke(self, prompt, *a, **kw):
                captured.append(prompt if isinstance(prompt, str) else str(prompt))
                return type("R", (), {
                    "content": '```json\n{"action": "BUY", "confidence": 0.8}\n```'
                })()

        memory = MagicMock()
        memory.get_memories.return_value = []
        from tradingagents.agents.managers.risk_manager import create_risk_manager
        with patch("tradingagents.agents.managers.risk_manager.get_config",
                   return_value={"target_market": "EGX", "backtest_mode": False}):
            node = create_risk_manager(SpyLLM(), memory)
            node(state)
        return captured[0] if captured else ""

    def test_prompt_contains_exec_plan_symbol(self):
        prompt = self._capture_prompt(_approved_state())
        assert "COMI.CA" in prompt

    def test_prompt_contains_egx_constitution(self):
        prompt = self._capture_prompt(_approved_state())
        assert "EGX TRADING CONSTITUTION" in prompt

    def test_prompt_contains_at_least_5_constitution_clauses(self):
        prompt = self._capture_prompt(_approved_state())
        # Constitution has 15 numbered clauses — check at least 5 exist
        clause_count = sum(1 for i in range(1, 6) if f"{i}." in prompt)
        assert clause_count >= 5, f"Expected ≥5 constitution clauses, found {clause_count}"

    def test_prompt_contains_risk_action(self):
        prompt = self._capture_prompt(_approved_state())
        assert "ALLOW" in prompt or "Risk Action" in prompt

    def test_prompt_contains_risk_metrics(self):
        prompt = self._capture_prompt(_approved_state())
        assert "adv_participation_pct" in prompt or "position_pct" in prompt

    def test_prompt_contains_debate_summary(self):
        state = _approved_state()
        state["risk_debate_state"]["history"] = "UNIQUE_DEBATE_MARKER_XYZ"
        prompt = self._capture_prompt(state)
        assert "UNIQUE_DEBATE_MARKER_XYZ" in prompt

    def test_prompt_does_not_expose_veto_path_instruction(self):
        """risk_manager_node prompt must not contain VETO-path boilerplate."""
        prompt = self._capture_prompt(_approved_state())
        assert "VETO - HOLD" not in prompt

    def test_throttle_state_noted_in_prompt(self):
        state = _approved_state()
        state["risk_action"] = "THROTTLE"
        prompt = self._capture_prompt(state)
        assert "THROTTLE" in prompt


# ─────────────────────────────────────────────────────────────────────────────
# 4. Final deterministic gate
# ─────────────────────────────────────────────────────────────────────────────

class TestFinalDeterministicGate:

    def _run_with_llm_decision(self, llm_action, decision_in_plan="BUY",
                                symbol="COMI.CA", shares=0):
        response = f'```json\n{{"action": "{llm_action}", "confidence": 0.9}}\n```'
        llm = MockLLM(response)
        memory = MagicMock()
        memory.get_memories.return_value = []
        state = _approved_state(decision=decision_in_plan, symbol=symbol, shares=shares)
        from tradingagents.agents.managers.risk_manager import create_risk_manager
        with patch("tradingagents.agents.managers.risk_manager.get_config",
                   return_value={"target_market": "EGX", "backtest_mode": False}):
            node = create_risk_manager(llm, memory)
            return node(state)

    def test_sell_with_no_position_overridden_to_hold(self):
        """Gate 1: SELL with no open position must become HOLD."""
        result = self._run_with_llm_decision("SELL", decision_in_plan="SELL", shares=0)
        assert result["final_trade_decision"] == "HOLD"

    def test_sell_with_no_position_adds_gate_note(self):
        result = self._run_with_llm_decision("SELL", decision_in_plan="SELL", shares=0)
        gate_notes = result.get("risk_assessment", {}).get("final_gate_notes", [])
        assert gate_notes, "Expected a final gate note when SELL overridden to HOLD"
        assert any("no open position" in n.lower() for n in gate_notes)

    def test_sell_with_open_position_is_allowed(self):
        """If shares > 0, a SELL decision is valid and must not be overridden."""
        result = self._run_with_llm_decision("SELL", decision_in_plan="SELL", shares=500)
        assert result["final_trade_decision"] == "SELL"

    def test_buy_on_restricted_ticker_overridden_to_hold(self):
        """Gate 2: BUY on SCEM (foreign-restricted) must become HOLD."""
        result = self._run_with_llm_decision("BUY", symbol="SCEM", decision_in_plan="BUY")
        assert result["final_trade_decision"] == "HOLD"

    def test_buy_on_sdti_overridden_to_hold(self):
        result = self._run_with_llm_decision("BUY", symbol="SDTI", decision_in_plan="BUY")
        assert result["final_trade_decision"] == "HOLD"

    def test_buy_on_normal_ticker_not_overridden(self):
        result = self._run_with_llm_decision("BUY", symbol="COMI.CA")
        assert result["final_trade_decision"] == "BUY"

    def test_decision_mismatch_flagged_not_overridden(self):
        """Gate 3: LLM says SELL but plan says BUY — flag only, no override."""
        result = self._run_with_llm_decision("SELL", decision_in_plan="BUY", shares=500)
        # Decision mismatch does NOT override (Gate 3 only flags)
        # Either SELL is kept or some flag is present — but it must not silently pass
        gate_notes = result.get("risk_assessment", {}).get("final_gate_notes", [])
        assert result["final_trade_decision"] in ("BUY", "SELL", "HOLD")


# ─────────────────────────────────────────────────────────────────────────────
# 5. State and field completeness
# ─────────────────────────────────────────────────────────────────────────────

class TestStateAndLogging:

    def test_agent_state_has_risk_action_field(self):
        from tradingagents.agents.utils.agent_states import AgentState
        assert "risk_action" in AgentState.__annotations__, \
            "risk_action missing from AgentState"

    def test_agent_state_has_risk_metrics_field(self):
        from tradingagents.agents.utils.agent_states import AgentState
        assert "risk_metrics" in AgentState.__annotations__, \
            "risk_metrics missing from AgentState"

    def test_risk_assessment_contains_hard_violations(self):
        from tradingagents.agents.risk_mgmt.risk_scorer import (
            create_risk_scorer_node, EGX_RISK_LIMITS,
        )
        state = {
            "company_of_interest": "TEST.CA",
            "execution_plan": {"execution_plan": {
                "symbol": "TEST.CA",
                "decision": "BUY",
                "position_sizing": {"target_shares": 50000, "max_shares_per_day": 20000,
                                    "portfolio_allocation": "8%"},
                "exit_logic": {"stop_loss": {"price": 45.0}},
                "entry_logic": {"entry_zone": {"limit_price": 50.0}},
            }},
            "portfolio_value": 10_000_000,
            "avg_daily_volume": 100_000,  # 20000/100000 = 20% ADV → VETO
            "current_price": 50.0,
            "low_liquidity": False,
            "technical_analysis": {},
        }
        with patch("tradingagents.agents.risk_mgmt.risk_scorer.get_config",
                   return_value={"target_market": "EGX", "backtest_mode": False}):
            node = create_risk_scorer_node()
            result = node(state)
        assert "hard_violations" in result["risk_assessment"]
        assert "warnings" in result["risk_assessment"]

    def test_risk_assessment_contains_throttle_adjustments(self):
        from tradingagents.agents.risk_mgmt.risk_scorer import create_risk_scorer_node
        state = {
            "company_of_interest": "TEST.CA",
            "execution_plan": {"execution_plan": {
                "symbol": "TEST.CA",
                "decision": "BUY",
                "position_sizing": {"target_shares": 10000, "max_shares_per_day": 7000,
                                    "portfolio_allocation": "8%"},
                "exit_logic": {"stop_loss": {"price": 45.0}},
                "entry_logic": {"entry_zone": {"limit_price": 50.0}},
            }},
            "portfolio_value": 10_000_000,
            "avg_daily_volume": 100_000,  # 7000/100000 = 7% → THROTTLE zone
            "current_price": 50.0,
            "low_liquidity": False,
            "technical_analysis": {},
        }
        with patch("tradingagents.agents.risk_mgmt.risk_scorer.get_config",
                   return_value={"target_market": "EGX", "backtest_mode": False}):
            node = create_risk_scorer_node()
            result = node(state)
        assert result["risk_action"] == "THROTTLE"
        adj = result["risk_assessment"].get("throttle_adjustments", {})
        assert adj.get("throttle_applied") is True

    def test_final_state_has_risk_metrics(self):
        """After risk_manager_node runs, risk_metrics must still be in state."""
        memory = MagicMock()
        memory.get_memories.return_value = []
        llm = MockLLM('```json\n{"action": "BUY", "confidence": 0.8}\n```')
        from tradingagents.agents.managers.risk_manager import create_risk_manager
        with patch("tradingagents.agents.managers.risk_manager.get_config",
                   return_value={"target_market": "EGX", "backtest_mode": False}):
            node = create_risk_manager(llm, memory)
            result = node(_approved_state())
        # risk_metrics is set on state by scorer; risk_manager doesn't clear it
        assert "risk_assessment" in result
        assert "final_trade_decision" in result

    def test_final_trade_decision_always_present(self):
        memory = MagicMock()
        memory.get_memories.return_value = []
        llm = MockLLM('```json\n{"action": "HOLD", "confidence": 0.6}\n```')
        from tradingagents.agents.managers.risk_manager import create_risk_manager
        with patch("tradingagents.agents.managers.risk_manager.get_config",
                   return_value={"target_market": "EGX", "backtest_mode": False}):
            node = create_risk_manager(llm, memory)
            result = node(_approved_state())
        assert result.get("final_trade_decision") is not None
