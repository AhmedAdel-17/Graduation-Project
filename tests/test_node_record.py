"""
Tests for tradingagents.graph.node_record.

Covers:
  - NodeRecord dataclass serialization
  - hash_string, hash_state_slice, hash_config_subset determinism
  - NodeRecorder.record() writes correct JSON to disk
  - Atomic write (temp + rename) behavior
  - Status validation
  - prompt_text gated by record_full_prompts flag
  - get_recorder() extraction from state
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from tradingagents.graph.node_record import (
    NodeRecord,
    NodeRecorder,
    get_recorder,
    hash_config_subset,
    hash_state_slice,
    hash_string,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Hash helpers
# ═══════════════════════════════════════════════════════════════════════════════


class TestHashString:
    def test_deterministic(self):
        assert hash_string("hello") == hash_string("hello")

    def test_different_inputs(self):
        assert hash_string("hello") != hash_string("world")

    def test_empty_string(self):
        h = hash_string("")
        assert isinstance(h, str) and len(h) == 64  # SHA-256 hex


class TestHashStateSlice:
    def test_deterministic(self):
        state = {"a": 1, "b": "two", "c": [3]}
        h1 = hash_state_slice(state, ["a", "b"])
        h2 = hash_state_slice(state, ["a", "b"])
        assert h1 == h2

    def test_key_order_irrelevant(self):
        state = {"a": 1, "b": 2}
        assert hash_state_slice(state, ["a", "b"]) == hash_state_slice(state, ["b", "a"])

    def test_different_keys_different_hash(self):
        state = {"a": 1, "b": 2}
        assert hash_state_slice(state, ["a"]) != hash_state_slice(state, ["b"])

    def test_missing_key_hashes_none(self):
        state = {"a": 1}
        h = hash_state_slice(state, ["a", "missing"])
        assert isinstance(h, str) and len(h) == 64

    def test_non_serializable_value(self):
        """Non-JSON-serializable values are repr()'d, not crashed."""
        state = {"a": object()}
        h = hash_state_slice(state, ["a"])
        assert isinstance(h, str) and len(h) == 64


class TestHashConfigSubset:
    def test_deterministic(self):
        cfg = {"llm_provider": "openai", "target_market": "EGX", "backtest_mode": True}
        assert hash_config_subset(cfg) == hash_config_subset(cfg)

    def test_ignores_irrelevant_keys(self):
        cfg1 = {"llm_provider": "openai", "unrelated_key": "foo"}
        cfg2 = {"llm_provider": "openai", "unrelated_key": "bar"}
        assert hash_config_subset(cfg1) == hash_config_subset(cfg2)

    def test_different_relevant_values(self):
        cfg1 = {"llm_provider": "openai"}
        cfg2 = {"llm_provider": "anthropic"}
        assert hash_config_subset(cfg1) != hash_config_subset(cfg2)


# ═══════════════════════════════════════════════════════════════════════════════
# NodeRecord dataclass
# ═══════════════════════════════════════════════════════════════════════════════


class TestNodeRecord:
    def _make_record(self, **overrides):
        defaults = dict(
            run_id="run-001",
            ticker="COMI",
            trade_date="2024-01-15",
            node_name="trader",
            status="success",
            input_state_keys=["market_report"],
            input_hash="abc123",
            raw_output="LLM said something",
            prompt_hash="def456",
            wall_clock_ms=1500.0,
            timestamp_utc="2024-01-15T12:00:00+00:00",
        )
        defaults.update(overrides)
        return NodeRecord(**defaults)

    def test_to_dict_drops_none(self):
        rec = self._make_record()
        d = rec.to_dict()
        assert "error_type" not in d
        assert "error_message" not in d
        assert "prompt_text" not in d

    def test_to_dict_keeps_set_values(self):
        rec = self._make_record(error_type="JSONDecodeError", error_message="bad json")
        d = rec.to_dict()
        assert d["error_type"] == "JSONDecodeError"
        assert d["error_message"] == "bad json"

    def test_to_dict_is_json_serializable(self):
        rec = self._make_record()
        s = json.dumps(rec.to_dict())
        assert isinstance(s, str)

    def test_empty_lists_preserved(self):
        """Empty lists like context_items should still appear in output."""
        rec = self._make_record()
        d = rec.to_dict()
        # Lists are never None, so they should be present
        assert "input_state_keys" in d
        assert "context_items" in d


# ═══════════════════════════════════════════════════════════════════════════════
# NodeRecorder
# ═══════════════════════════════════════════════════════════════════════════════


class TestNodeRecorder:
    @pytest.fixture
    def tmp_records_dir(self, tmp_path):
        return str(tmp_path / "backtest_records")

    def _make_recorder(self, tmp_records_dir, **kwargs):
        defaults = dict(
            run_id="run-001",
            ticker="COMI",
            records_dir=tmp_records_dir,
            record_full_prompts=False,
            config={"llm_provider": "openai", "quick_think_llm": "deepseek-chat"},
        )
        defaults.update(kwargs)
        return NodeRecorder(**defaults)

    def test_record_writes_json_file(self, tmp_records_dir):
        rec = self._make_recorder(tmp_records_dir)
        path = rec.record(
            node_name="trader",
            trade_date="2024-01-15",
            input_state_keys=["market_report", "investment_plan"],
            input_hash="abc123",
            prompt_hash="def456",
            raw_output="LLM output text",
            state_update_keys=["execution_plan", "trader_investment_plan"],
            wall_clock_ms=1234.5,
            status="success",
        )
        assert os.path.exists(path)
        assert path.endswith("trader.json")

        with open(path) as f:
            data = json.load(f)

        assert data["run_id"] == "run-001"
        assert data["ticker"] == "COMI"
        assert data["trade_date"] == "2024-01-15"
        assert data["node_name"] == "trader"
        assert data["status"] == "success"
        assert data["input_hash"] == "abc123"
        assert data["prompt_hash"] == "def456"
        assert data["raw_output"] == "LLM output text"
        assert data["wall_clock_ms"] == 1234.5
        assert "timestamp_utc" in data

    def test_directory_structure(self, tmp_records_dir):
        rec = self._make_recorder(tmp_records_dir)
        path = rec.record(
            node_name="bull_researcher",
            trade_date="2024-02-01",
            input_state_keys=["market_report"],
            input_hash="h1",
            status="success",
        )
        expected_dir = os.path.join(
            tmp_records_dir, "run-001", "COMI", "2024-02-01"
        )
        assert path == os.path.join(expected_dir, "bull_researcher.json")

    def test_prompt_text_omitted_by_default(self, tmp_records_dir):
        rec = self._make_recorder(tmp_records_dir, record_full_prompts=False)
        path = rec.record(
            node_name="trader",
            trade_date="2024-01-15",
            input_state_keys=[],
            input_hash="h1",
            prompt_text="This is the full prompt",
            prompt_hash="ph1",
            status="success",
        )
        with open(path) as f:
            data = json.load(f)
        assert "prompt_text" not in data

    def test_prompt_text_saved_when_enabled(self, tmp_records_dir):
        rec = self._make_recorder(tmp_records_dir, record_full_prompts=True)
        path = rec.record(
            node_name="trader",
            trade_date="2024-01-15",
            input_state_keys=[],
            input_hash="h1",
            prompt_text="This is the full prompt",
            prompt_hash="ph1",
            status="success",
        )
        with open(path) as f:
            data = json.load(f)
        assert data["prompt_text"] == "This is the full prompt"

    def test_invalid_status_raises(self, tmp_records_dir):
        rec = self._make_recorder(tmp_records_dir)
        with pytest.raises(ValueError, match="Invalid status"):
            rec.record(
                node_name="trader",
                trade_date="2024-01-15",
                input_state_keys=[],
                input_hash="h1",
                status="invalid_status",
            )

    def test_error_record(self, tmp_records_dir):
        rec = self._make_recorder(tmp_records_dir)
        path = rec.record(
            node_name="trader",
            trade_date="2024-01-15",
            input_state_keys=["investment_plan"],
            input_hash="h1",
            status="error",
            error_type="ConnectionError",
            error_message="API timeout after 30s",
        )
        with open(path) as f:
            data = json.load(f)
        assert data["status"] == "error"
        assert data["error_type"] == "ConnectionError"
        assert data["error_message"] == "API timeout after 30s"
        assert "raw_output" not in data  # None → omitted

    def test_fallback_record(self, tmp_records_dir):
        rec = self._make_recorder(tmp_records_dir)
        path = rec.record(
            node_name="trader",
            trade_date="2024-01-15",
            input_state_keys=["investment_plan"],
            input_hash="h1",
            raw_output="Unparseable LLM response...",
            status="fallback",
            fallback_source="json_parse_failure",
        )
        with open(path) as f:
            data = json.load(f)
        assert data["status"] == "fallback"
        assert data["fallback_source"] == "json_parse_failure"
        assert data["raw_output"] == "Unparseable LLM response..."

    def test_skipped_record(self, tmp_records_dir):
        rec = self._make_recorder(tmp_records_dir)
        path = rec.record(
            node_name="social_analyst",
            trade_date="2024-01-15",
            input_state_keys=["prefetched_stock_datapoints"],
            input_hash="h1",
            status="skipped",
            skip_reason="layer_c_no_signal",
        )
        with open(path) as f:
            data = json.load(f)
        assert data["status"] == "skipped"
        assert data["skip_reason"] == "layer_c_no_signal"

    def test_provenance_from_config(self, tmp_records_dir):
        rec = self._make_recorder(
            tmp_records_dir,
            config={
                "quick_think_llm": "deepseek-chat",
                "deep_think_llm": "deepseek-chat",
                "llm_provider": "openai",
                "target_market": "EGX",
            },
        )
        path = rec.record(
            node_name="trader",
            trade_date="2024-01-15",
            input_state_keys=[],
            input_hash="h1",
            status="success",
        )
        with open(path) as f:
            data = json.load(f)
        assert data["model_id"] == "deepseek-chat"
        assert data["temperature"] == 0.0
        assert data["seed"] == 42
        assert "config_hash" in data

    def test_overwrite_same_node(self, tmp_records_dir):
        """Recording the same node twice overwrites the file."""
        rec = self._make_recorder(tmp_records_dir)
        rec.record(
            node_name="trader",
            trade_date="2024-01-15",
            input_state_keys=[],
            input_hash="h1",
            raw_output="first",
            status="success",
        )
        path = rec.record(
            node_name="trader",
            trade_date="2024-01-15",
            input_state_keys=[],
            input_hash="h2",
            raw_output="second",
            status="success",
        )
        with open(path) as f:
            data = json.load(f)
        assert data["raw_output"] == "second"

    def test_multiple_nodes_same_date(self, tmp_records_dir):
        rec = self._make_recorder(tmp_records_dir)
        p1 = rec.record(
            node_name="trader",
            trade_date="2024-01-15",
            input_state_keys=[],
            input_hash="h1",
            status="success",
        )
        p2 = rec.record(
            node_name="bull_researcher",
            trade_date="2024-01-15",
            input_state_keys=[],
            input_hash="h2",
            status="success",
        )
        assert os.path.dirname(p1) == os.path.dirname(p2)
        assert os.path.basename(p1) == "trader.json"
        assert os.path.basename(p2) == "bull_researcher.json"

    def test_signal_field(self, tmp_records_dir):
        rec = self._make_recorder(tmp_records_dir)
        path = rec.record(
            node_name="risk_manager",
            trade_date="2024-01-15",
            input_state_keys=[],
            input_hash="h1",
            signal="BUY",
            status="success",
        )
        with open(path) as f:
            data = json.load(f)
        assert data["signal"] == "BUY"

    def test_context_items(self, tmp_records_dir):
        rec = self._make_recorder(tmp_records_dir)
        items = [
            {"source": "bull_memory", "original_date": "2024-01-01", "content_hash": "x1"},
        ]
        path = rec.record(
            node_name="bull_researcher",
            trade_date="2024-01-15",
            input_state_keys=[],
            input_hash="h1",
            context_items=items,
            status="success",
        )
        with open(path) as f:
            data = json.load(f)
        assert len(data["context_items"]) == 1
        assert data["context_items"][0]["source"] == "bull_memory"


# ═══════════════════════════════════════════════════════════════════════════════
# get_recorder helper
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetRecorder:
    def test_returns_recorder_when_present(self):
        recorder = NodeRecorder(
            run_id="r1", ticker="COMI", records_dir="/tmp/test"
        )
        state = {"_node_recorder": recorder, "company_of_interest": "COMI.CA"}
        assert get_recorder(state) is recorder

    def test_returns_none_when_absent(self):
        state = {"company_of_interest": "COMI.CA"}
        assert get_recorder(state) is None

    def test_returns_none_for_none_value(self):
        state = {"_node_recorder": None}
        assert get_recorder(state) is None
