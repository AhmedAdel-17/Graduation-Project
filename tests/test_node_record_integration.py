"""
Integration tests for node recording in Trader and Bull Researcher.

Uses a mock LLM to verify that instrumented nodes produce correct
JSON records when a NodeRecorder is present in AgentState.
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.graph.node_record import NodeRecorder


# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_llm_response(content: str):
    """Create a mock LLM response with .content and .tool_calls."""
    resp = MagicMock()
    resp.content = content
    resp.tool_calls = []
    return resp


def _make_recorder(tmp_path, record_full_prompts=False):
    return NodeRecorder(
        run_id="test-run-001",
        ticker="COMI",
        records_dir=str(tmp_path / "backtest_records"),
        record_full_prompts=record_full_prompts,
        config={
            "llm_provider": "openai",
            "quick_think_llm": "deepseek-chat",
            "deep_think_llm": "deepseek-chat",
            "target_market": "EGX",
            "backtest_mode": True,
        },
    )


def _base_state(recorder=None):
    """Minimal AgentState-like dict for testing nodes."""
    state = {
        "company_of_interest": "COMI.CA",
        "trade_date": "2024-01-15",
        "investment_plan": "BUY based on strong fundamentals",
        "market_report": "Technical signals are bullish",
        "sentiment_report": "Social sentiment neutral",
        "news_report": "Positive earnings news",
        "fundamentals_report": "Strong balance sheet",
        "technical_analysis": {"rsi": 55, "macd_signal": "bullish"},
        "fundamental_analysis": {"pe_ratio": 12.5},
        "sentiment_analysis": {"overall": "neutral"},
        "investment_debate_state": {
            "history": "",
            "bull_history": "",
            "bear_history": "",
            "current_response": "",
            "judge_decision": "",
            "count": 0,
            "bull_thesis": None,
            "bear_thesis": None,
        },
        "low_liquidity": False,
        "current_position": {},
        "avg_daily_volume": 500000,
        "current_price": 50.0,
        "portfolio_value": 10_000_000,
        "sentiment_blend_result": None,
        "macro_context": None,
        "_node_recorder": recorder,
    }
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# Trader integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestTraderRecording:

    _TRADER_LLM_RESPONSE = """Based on the analysis, here is the execution plan:

```json
{
    "execution_plan": {
        "symbol": "COMI.CA",
        "market": "EGX",
        "currency": "EGP",
        "decision": "BUY",
        "conviction": "moderate"
    },
    "final_recommendation": "FINAL TRANSACTION PROPOSAL: **BUY**"
}
```"""

    @patch("tradingagents.agents.trader.trader.get_config")
    def test_trader_writes_record_on_success(self, mock_config, tmp_path):
        mock_config.return_value = {
            "target_market": "EGX",
            "backtest_mode": True,
            "memory_min_similarity": 0.30,
        }
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _mock_llm_response(self._TRADER_LLM_RESPONSE)
        mock_memory = MagicMock()
        mock_memory.get_memories.return_value = []

        from tradingagents.agents.trader.trader import create_trader
        trader_fn = create_trader(mock_llm, mock_memory)

        recorder = _make_recorder(tmp_path)
        state = _base_state(recorder)

        trader_fn(state)

        # Verify record was written
        record_path = os.path.join(
            str(tmp_path / "backtest_records"),
            "test-run-001", "COMI", "2024-01-15", "trader.json",
        )
        assert os.path.exists(record_path), f"Record not found at {record_path}"

        with open(record_path) as f:
            rec = json.load(f)

        assert rec["run_id"] == "test-run-001"
        assert rec["ticker"] == "COMI"
        assert rec["trade_date"] == "2024-01-15"
        assert rec["node_name"] == "trader"
        assert rec["status"] == "success"
        assert rec["signal"] == "BUY"
        assert rec["raw_output"] == self._TRADER_LLM_RESPONSE
        assert "prompt_hash" in rec
        assert len(rec["prompt_hash"]) == 64  # SHA-256 hex
        assert "input_hash" in rec
        assert rec["wall_clock_ms"] > 0
        assert rec["model_id"] == "deepseek-chat"
        assert rec["temperature"] == 0.0
        assert rec["seed"] == 42
        # prompt_text should NOT be saved (record_full_prompts=False)
        assert "prompt_text" not in rec

    @patch("tradingagents.agents.trader.trader.get_config")
    def test_trader_records_fallback_on_bad_json(self, mock_config, tmp_path):
        mock_config.return_value = {
            "target_market": "EGX",
            "backtest_mode": True,
            "memory_min_similarity": 0.30,
        }
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _mock_llm_response(
            "I recommend buying but here is broken json:\n```json\n{broken\n```"
        )
        mock_memory = MagicMock()
        mock_memory.get_memories.return_value = []

        from tradingagents.agents.trader.trader import create_trader
        trader_fn = create_trader(mock_llm, mock_memory)

        recorder = _make_recorder(tmp_path)
        state = _base_state(recorder)

        result = trader_fn(state)

        record_path = os.path.join(
            str(tmp_path / "backtest_records"),
            "test-run-001", "COMI", "2024-01-15", "trader.json",
        )
        with open(record_path) as f:
            rec = json.load(f)

        assert rec["status"] == "fallback"
        assert rec["fallback_source"] == "json_parse_failure"
        assert "broken" in rec["raw_output"]
        # Fallback still produces a HOLD execution plan
        assert result["execution_plan"]["execution_plan"]["decision"] == "HOLD"

    @patch("tradingagents.agents.trader.trader.get_config")
    def test_trader_no_record_without_recorder(self, mock_config, tmp_path):
        """When _node_recorder is None, no record is written and behavior is unchanged."""
        mock_config.return_value = {
            "target_market": "EGX",
            "backtest_mode": True,
            "memory_min_similarity": 0.30,
        }
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _mock_llm_response(self._TRADER_LLM_RESPONSE)
        mock_memory = MagicMock()
        mock_memory.get_memories.return_value = []

        from tradingagents.agents.trader.trader import create_trader
        trader_fn = create_trader(mock_llm, mock_memory)

        state = _base_state(recorder=None)
        result = trader_fn(state)

        # Should still work correctly
        assert result["execution_plan"]["execution_plan"]["decision"] == "BUY"
        # No records directory created
        records_dir = str(tmp_path / "backtest_records")
        assert not os.path.exists(records_dir)

    @patch("tradingagents.agents.trader.trader.get_config")
    def test_trader_saves_prompt_text_when_enabled(self, mock_config, tmp_path):
        mock_config.return_value = {
            "target_market": "EGX",
            "backtest_mode": True,
            "memory_min_similarity": 0.30,
        }
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _mock_llm_response(self._TRADER_LLM_RESPONSE)
        mock_memory = MagicMock()
        mock_memory.get_memories.return_value = []

        from tradingagents.agents.trader.trader import create_trader
        trader_fn = create_trader(mock_llm, mock_memory)

        recorder = _make_recorder(tmp_path, record_full_prompts=True)
        state = _base_state(recorder)

        trader_fn(state)

        record_path = os.path.join(
            str(tmp_path / "backtest_records"),
            "test-run-001", "COMI", "2024-01-15", "trader.json",
        )
        with open(record_path) as f:
            rec = json.load(f)

        assert "prompt_text" in rec
        assert "Institutional Trader" in rec["prompt_text"]


# ═══════════════════════════════════════════════════════════════════════════════
# Bull Researcher integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestBullResearcherRecording:

    _BULL_LLM_RESPONSE = """Based on the analyst signals, COMI presents a strong bullish case.

```json
{
    "thesis_type": "bullish",
    "conviction_level": "high",
    "time_horizon": {"primary": "medium_term"},
    "signal_summary": {"technical": "bullish", "fundamental": "bullish", "sentiment": "neutral", "alignment_score": "strong"},
    "invalidation_conditions": ["PE expansion above 20x"]
}
```"""

    @patch("tradingagents.agents.researchers.bull_researcher.get_config")
    def test_bull_writes_record_on_success(self, mock_config, tmp_path):
        mock_config.return_value = {
            "target_market": "EGX",
            "memory_min_similarity": 0.30,
        }
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _mock_llm_response(self._BULL_LLM_RESPONSE)
        mock_memory = MagicMock()
        mock_memory.get_memories.return_value = []

        from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
        bull_fn = create_bull_researcher(mock_llm, mock_memory)

        recorder = _make_recorder(tmp_path)
        state = _base_state(recorder)

        bull_fn(state)

        record_path = os.path.join(
            str(tmp_path / "backtest_records"),
            "test-run-001", "COMI", "2024-01-15", "bull_researcher.json",
        )
        assert os.path.exists(record_path)

        with open(record_path) as f:
            rec = json.load(f)

        assert rec["node_name"] == "bull_researcher"
        assert rec["status"] == "success"
        assert rec["signal"] == "high"  # conviction_level
        assert "thesis_type" in rec["raw_output"]
        assert len(rec["prompt_hash"]) == 64
        assert rec["wall_clock_ms"] > 0

    @patch("tradingagents.agents.researchers.bull_researcher.get_config")
    def test_bull_records_fallback_on_no_json(self, mock_config, tmp_path):
        mock_config.return_value = {
            "target_market": "EGX",
            "memory_min_similarity": 0.30,
        }
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _mock_llm_response(
            "COMI is a great stock to buy. Strong fundamentals."
        )
        mock_memory = MagicMock()
        mock_memory.get_memories.return_value = []

        from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
        bull_fn = create_bull_researcher(mock_llm, mock_memory)

        recorder = _make_recorder(tmp_path)
        state = _base_state(recorder)

        result = bull_fn(state)

        record_path = os.path.join(
            str(tmp_path / "backtest_records"),
            "test-run-001", "COMI", "2024-01-15", "bull_researcher.json",
        )
        with open(record_path) as f:
            rec = json.load(f)

        # No JSON block in the response → bull_thesis is None
        # But this is NOT a JSONDecodeError fallback — it's just "no json found"
        # The node treats this as success (bull_thesis=None is valid)
        assert rec["status"] == "success"
        assert "signal" not in rec  # None values are omitted from the record

    @patch("tradingagents.agents.researchers.bull_researcher.get_config")
    def test_bull_no_record_without_recorder(self, mock_config, tmp_path):
        mock_config.return_value = {
            "target_market": "EGX",
            "memory_min_similarity": 0.30,
        }
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _mock_llm_response(self._BULL_LLM_RESPONSE)
        mock_memory = MagicMock()
        mock_memory.get_memories.return_value = []

        from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
        bull_fn = create_bull_researcher(mock_llm, mock_memory)

        state = _base_state(recorder=None)
        result = bull_fn(state)

        # Node still works correctly
        assert result["investment_debate_state"]["bull_thesis"]["thesis_type"] == "bullish"
        # No records
        assert not os.path.exists(str(tmp_path / "backtest_records"))


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-node: both nodes record to same run directory
# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════════
# Risk Manager integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestRiskManagerRecording:

    _RM_LLM_RESPONSE = """Constitution compliance: Clauses 1, 10, 11 satisfied.

Debate quality: Substantive three-way debate with credible risk quantification.

Qualitative risks: No regime-change or management-quality concerns identified.

```json
{"action": "BUY", "confidence": 0.75}
```"""

    @patch("tradingagents.agents.managers.risk_manager.get_config")
    def test_risk_manager_writes_record(self, mock_config, tmp_path):
        mock_config.return_value = {
            "target_market": "EGX",
            "memory_min_similarity": 0.30,
        }
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _mock_llm_response(self._RM_LLM_RESPONSE)
        mock_memory = MagicMock()
        mock_memory.get_memories.return_value = []

        from tradingagents.agents.managers.risk_manager import create_risk_manager
        rm_fn = create_risk_manager(mock_llm, mock_memory)

        recorder = _make_recorder(tmp_path)
        state = _base_state(recorder)
        state["risk_assessment"] = {"overall": "ALLOW"}
        state["risk_action"] = "ALLOW"
        state["risk_metrics"] = {"adv_pct": 0.05}
        state["risk_debate_state"] = {
            "history": "Risky: upside. Safe: caution.",
            "risky_history": "", "safe_history": "", "neutral_history": "",
            "latest_speaker": "MergedRiskDebate",
            "current_risky_response": "", "current_safe_response": "",
            "current_neutral_response": "", "count": 3,
        }
        state["execution_plan"] = {
            "execution_plan": {"symbol": "COMI.CA", "decision": "BUY"}
        }

        result = rm_fn(state)

        record_path = os.path.join(
            str(tmp_path / "backtest_records"),
            "test-run-001", "COMI", "2024-01-15", "risk_manager.json",
        )
        assert os.path.exists(record_path)

        with open(record_path) as f:
            rec = json.load(f)

        assert rec["node_name"] == "risk_manager"
        assert rec["status"] == "success"
        assert rec["signal"] == "BUY"
        assert len(rec["prompt_hash"]) == 64
        assert rec["wall_clock_ms"] > 0
        # state_update should contain the return dict
        assert "state_update" in rec
        assert "final_trade_decision" in rec["state_update"]
        assert rec["state_update"]["final_trade_decision"] == "BUY"

    @patch("tradingagents.agents.managers.risk_manager.get_config")
    def test_risk_manager_final_gate_override_recorded(self, mock_config, tmp_path):
        """When final gate overrides SELL→HOLD (no position), signal should be HOLD."""
        mock_config.return_value = {
            "target_market": "EGX",
            "memory_min_similarity": 0.30,
        }
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _mock_llm_response(
            '```json\n{"action": "SELL", "confidence": 0.6}\n```'
        )
        mock_memory = MagicMock()
        mock_memory.get_memories.return_value = []

        from tradingagents.agents.managers.risk_manager import create_risk_manager
        rm_fn = create_risk_manager(mock_llm, mock_memory)

        recorder = _make_recorder(tmp_path)
        state = _base_state(recorder)
        state["current_position"] = {"shares": 0}  # no position → gate overrides to HOLD
        state["risk_assessment"] = {}
        state["risk_action"] = "ALLOW"
        state["risk_metrics"] = {}
        state["risk_debate_state"] = {"history": "", "count": 3,
            "risky_history": "", "safe_history": "", "neutral_history": "",
            "latest_speaker": "", "current_risky_response": "",
            "current_safe_response": "", "current_neutral_response": ""}
        state["execution_plan"] = {"execution_plan": {"symbol": "COMI.CA", "decision": "SELL"}}

        result = rm_fn(state)

        record_path = os.path.join(
            str(tmp_path / "backtest_records"),
            "test-run-001", "COMI", "2024-01-15", "risk_manager.json",
        )
        with open(record_path) as f:
            rec = json.load(f)

        # Final gate overrides SELL→HOLD, and recorder runs after the gate
        assert rec["signal"] == "HOLD"
        assert result["final_trade_decision"] == "HOLD"


# ═══════════════════════════════════════════════════════════════════════════════
# Social Media Analyst integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestSocialAnalystRecording:

    @patch("tradingagents.agents.analysts.social_media_analyst.get_config")
    @patch("tradingagents.agents.analysts.social_media_analyst._try_layer_c_gate")
    def test_social_analyst_skipped_on_layer_c_no_signal(
        self, mock_gate, mock_config, tmp_path
    ):
        """Layer C NO_SIGNAL → status=skipped, skip_reason=layer_c_no_signal, state_update present."""
        mock_config.return_value = {"target_market": "EGX"}
        mock_gate.return_value = True  # NO_SIGNAL

        mock_llm = MagicMock()  # should NOT be called

        from tradingagents.agents.analysts.social_media_analyst import create_social_media_analyst
        social_fn = create_social_media_analyst(mock_llm)

        recorder = _make_recorder(tmp_path)
        state = _base_state(recorder)
        state["prefetched_stock_datapoints"] = [{"sentiment_score": 0.0}]
        state["prefetched_social_sentiment"] = ""
        state["prefetched_social_posts"] = ""
        state["social_messages"] = []

        result = social_fn(state)

        # LLM should NOT have been called
        mock_llm.invoke.assert_not_called()

        record_path = os.path.join(
            str(tmp_path / "backtest_records"),
            "test-run-001", "COMI", "2024-01-15", "social_analyst.json",
        )
        assert os.path.exists(record_path)

        with open(record_path) as f:
            rec = json.load(f)

        assert rec["node_name"] == "social_analyst"
        assert rec["status"] == "skipped"
        assert rec["skip_reason"] == "layer_c_no_signal"
        # No raw_output or prompt_hash for skipped nodes
        assert "raw_output" not in rec
        assert "prompt_hash" not in rec
        # state_update should still be present
        assert "state_update" in rec
        assert "sentiment_report" in rec["state_update"]

    @patch("tradingagents.agents.analysts.social_media_analyst.get_config")
    def test_social_analyst_records_on_prefetched_success(self, mock_config, tmp_path):
        """Normal prefetched path → status=success with state_update."""
        mock_config.return_value = {"target_market": "EGX"}

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _mock_llm_response(
            '```json\n{"narrative": "Retail tone is cautious.", "cited_post_ids": []}\n```'
        )

        from tradingagents.agents.analysts.social_media_analyst import create_social_media_analyst
        social_fn = create_social_media_analyst(mock_llm)

        recorder = _make_recorder(tmp_path)
        state = _base_state(recorder)
        state["prefetched_stock_datapoints"] = None  # skip Layer C gate
        state["prefetched_social_sentiment"] = '{"market_sentiment": {}}'
        state["prefetched_social_posts"] = "Some posts here"
        state["social_messages"] = []

        result = social_fn(state)

        record_path = os.path.join(
            str(tmp_path / "backtest_records"),
            "test-run-001", "COMI", "2024-01-15", "social_analyst.json",
        )
        assert os.path.exists(record_path)

        with open(record_path) as f:
            rec = json.load(f)

        assert rec["node_name"] == "social_analyst"
        assert rec["status"] == "success"
        assert len(rec["prompt_hash"]) == 64
        assert rec["wall_clock_ms"] > 0
        assert "state_update" in rec
        assert "sentiment_blend_result" in rec["state_update"]


# ═══════════════════════════════════════════════════════════════════════════════
# state_update verification (all nodes that pass it)
# ═══════════════════════════════════════════════════════════════════════════════


class TestStateUpdateInRecords:

    @patch("tradingagents.agents.trader.trader.get_config")
    def test_trader_record_contains_state_update(self, mock_config, tmp_path):
        """Verify state_update dict is present and contains expected keys."""
        mock_config.return_value = {
            "target_market": "EGX",
            "backtest_mode": True,
            "memory_min_similarity": 0.30,
        }
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = _mock_llm_response(
            TestTraderRecording._TRADER_LLM_RESPONSE
        )
        mock_memory = MagicMock()
        mock_memory.get_memories.return_value = []

        from tradingagents.agents.trader.trader import create_trader
        trader_fn = create_trader(mock_llm, mock_memory)

        recorder = _make_recorder(tmp_path)
        state = _base_state(recorder)
        trader_fn(state)

        record_path = os.path.join(
            str(tmp_path / "backtest_records"),
            "test-run-001", "COMI", "2024-01-15", "trader.json",
        )
        with open(record_path) as f:
            rec = json.load(f)

        assert "state_update" in rec
        su = rec["state_update"]
        # Trader writes these keys
        assert "trader_investment_plan" in su
        assert "execution_plan" in su
        assert "sender" in su
        # _node_recorder must NOT appear (sanitized away)
        assert "_node_recorder" not in su


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-node: both nodes record to same run directory
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultiNodeRecording:

    @patch("tradingagents.agents.trader.trader.get_config")
    @patch("tradingagents.agents.researchers.bull_researcher.get_config")
    def test_both_nodes_record_to_same_date_dir(
        self, mock_bull_config, mock_trader_config, tmp_path
    ):
        mock_bull_config.return_value = {
            "target_market": "EGX",
            "memory_min_similarity": 0.30,
        }
        mock_trader_config.return_value = {
            "target_market": "EGX",
            "backtest_mode": True,
            "memory_min_similarity": 0.30,
        }

        mock_llm = MagicMock()
        mock_memory = MagicMock()
        mock_memory.get_memories.return_value = []

        recorder = _make_recorder(tmp_path)
        state = _base_state(recorder)

        # Run bull
        mock_llm.invoke.return_value = _mock_llm_response(
            TestBullResearcherRecording._BULL_LLM_RESPONSE
        )
        from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
        bull_fn = create_bull_researcher(mock_llm, mock_memory)
        bull_fn(state)

        # Run trader
        mock_llm.invoke.return_value = _mock_llm_response(
            TestTraderRecording._TRADER_LLM_RESPONSE
        )
        from tradingagents.agents.trader.trader import create_trader
        trader_fn = create_trader(mock_llm, mock_memory)
        trader_fn(state)

        date_dir = os.path.join(
            str(tmp_path / "backtest_records"),
            "test-run-001", "COMI", "2024-01-15",
        )
        files = sorted(os.listdir(date_dir))
        assert files == ["bull_researcher.json", "trader.json"]
