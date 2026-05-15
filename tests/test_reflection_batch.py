"""Tests for PR 7 — post-backtest reflection batch (MEMORY.md §C2 fix).

Covers:
- The in-loop ``graph.reflect_and_remember(...)`` call has been removed from
  ``scripts/backtester.py`` (the grep-style structural assertion).
- ``_flush_reflection_with_forward_returns`` walks the captured (date, state)
  queue and calls ``graph._run_reflections`` once per realized-outcome trade.
- Trades whose forward window is incomplete are skipped (no causal leakage).
- HOLD dates (no entry in ``trade_history``) are skipped.
- The graph's ``curr_state`` is restored after the batch even when reflection
  raises mid-loop.
- ``Reflector._default_metadata`` propagates verdict + forward_return into
  the ``outcome`` metadata key as a JSON string when returns_losses provided.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.backtester import BacktestingEngine
from tradingagents.graph.reflection import Reflector


# ─── Structural: the in-loop call is gone ─────────────────────────────────────


def test_in_loop_reflect_and_remember_call_removed():
    """The single ``reflect_and_remember`` call inside run_backtest must be
    gone. Calls to that method on the engine class itself (e.g., in test
    helpers or unrelated paths) don't count — we specifically check the
    backtester source for the offending pattern.

    This is the regression gate for MEMORY.md §C2.
    """
    src = Path(__file__).resolve().parents[1] / "scripts" / "backtester.py"
    text = src.read_text(encoding="utf-8")
    # Allow the call to appear in a comment / docstring but not as an
    # executable statement. The simplest robust check: the literal
    # `graph.reflect_and_remember(` (with paren) must not appear at all.
    assert "graph.reflect_and_remember(" not in text, (
        "scripts/backtester.py still contains an in-loop "
        "`graph.reflect_and_remember(...)` call — MEMORY.md §C2 NOT fixed."
    )


# ─── _flush_reflection_with_forward_returns ────────────────────────────────────


def _make_engine_with_queue(queue, trade_history):
    """Build a minimal BacktestingEngine without going through __init__'s
    heavy data setup. We only need the fields the flush method touches."""
    engine = BacktestingEngine.__new__(BacktestingEngine)
    engine._reflection_state_queue = queue
    engine.trade_history = trade_history
    return engine


def test_flush_runs_one_reflection_per_realized_outcome():
    """For each queued (date, state), look up the matching trade record and
    call graph._run_reflections exactly once with realized outcome."""
    queue = [
        ("2024-01-15", {"company_of_interest": "COMI.CA", "trade_date": "2024-01-15"}),
        ("2024-02-01", {"company_of_interest": "COMI.CA", "trade_date": "2024-02-01"}),
        ("2024-02-15", {"company_of_interest": "COMI.CA", "trade_date": "2024-02-15"}),
    ]
    trade_history = [
        {"date": "2024-01-15", "action": "BUY",
         "forward_return_20d": 0.045, "trade_result": "WIN"},
        {"date": "2024-02-01", "action": "BUY",
         "forward_return_20d": -0.023, "trade_result": "LOSS"},
        {"date": "2024-02-15", "action": "SELL",
         "forward_return_20d": 0.005, "trade_result": "NEUTRAL"},
    ]
    engine = _make_engine_with_queue(queue, trade_history)

    graph = MagicMock()
    graph.curr_state = {"sentinel": "pre_flush"}

    result = engine._flush_reflection_with_forward_returns(graph, lag_days=10)

    assert result == {"flushed": 3, "skipped": 0}
    assert graph._run_reflections.call_count == 3

    # Each call's returns_losses dict carries the realized outcome.
    calls = graph._run_reflections.call_args_list
    rl0 = calls[0].args[0]
    assert rl0["verdict"] == "WIN"
    assert rl0["forward_return"] == pytest.approx(0.045)
    assert rl0["action"] == "BUY"
    assert rl0["date"] == "2024-01-15"
    assert rl0["forward_horizon_days"] == 20

    rl1 = calls[1].args[0]
    assert rl1["verdict"] == "LOSS"
    assert rl1["forward_return"] == pytest.approx(-0.023)


def test_flush_skips_holds_with_no_trade_record():
    """A queued date with no entry in trade_history is treated as a HOLD —
    reflection has nothing to evaluate, so skip cleanly."""
    queue = [
        ("2024-01-15", {"company_of_interest": "COMI.CA"}),
        ("2024-01-16", {"company_of_interest": "COMI.CA"}),  # HOLD — no trade
        ("2024-01-17", {"company_of_interest": "COMI.CA"}),
    ]
    trade_history = [
        {"date": "2024-01-15", "action": "BUY",
         "forward_return_20d": 0.04, "trade_result": "WIN"},
        {"date": "2024-01-17", "action": "SELL",
         "forward_return_20d": -0.02, "trade_result": "LOSS"},
    ]
    engine = _make_engine_with_queue(queue, trade_history)
    graph = MagicMock()

    result = engine._flush_reflection_with_forward_returns(graph, lag_days=10)
    assert result == {"flushed": 2, "skipped": 1}
    assert graph._run_reflections.call_count == 2


def test_flush_skips_trades_with_no_realized_forward_return():
    """A trade where the forward window has not closed yet (None) is
    skipped — keeps reflection causal."""
    queue = [
        ("2024-03-25", {"company_of_interest": "COMI.CA"}),
        ("2024-03-30", {"company_of_interest": "COMI.CA"}),
    ]
    trade_history = [
        {"date": "2024-03-25", "action": "BUY",
         "forward_return_20d": None, "trade_result": "PENDING"},
        {"date": "2024-03-30", "action": "BUY",
         "forward_return_20d": None, "trade_result": "PENDING"},
    ]
    engine = _make_engine_with_queue(queue, trade_history)
    graph = MagicMock()

    result = engine._flush_reflection_with_forward_returns(graph, lag_days=10)
    assert result == {"flushed": 0, "skipped": 2}
    assert graph._run_reflections.call_count == 0


def test_flush_falls_back_to_shorter_horizon_when_20d_missing():
    """When forward_return_20d is None but forward_return_10d or _5d is
    populated, the helper falls back so we don't lose a usable reflection."""
    queue = [("2024-01-15", {"company_of_interest": "COMI.CA"})]
    trade_history = [
        {"date": "2024-01-15", "action": "BUY",
         "forward_return_20d": None,
         "forward_return_10d": 0.03,
         "forward_return_5d": 0.01,
         "trade_result": "WIN"},
    ]
    engine = _make_engine_with_queue(queue, trade_history)
    graph = MagicMock()

    result = engine._flush_reflection_with_forward_returns(graph, lag_days=10)
    assert result["flushed"] == 1
    rl = graph._run_reflections.call_args_list[0].args[0]
    assert rl["forward_return"] == pytest.approx(0.03)


def test_flush_swaps_graph_curr_state_to_historical_snapshot():
    """For each queued entry the graph's curr_state must equal the captured
    state at the moment _run_reflections is called. After the batch, the
    original curr_state must be restored."""
    historical_a = {"company_of_interest": "COMI.CA", "marker": "A"}
    historical_b = {"company_of_interest": "COMI.CA", "marker": "B"}
    queue = [
        ("2024-01-15", historical_a),
        ("2024-02-15", historical_b),
    ]
    trade_history = [
        {"date": "2024-01-15", "action": "BUY",
         "forward_return_20d": 0.04, "trade_result": "WIN"},
        {"date": "2024-02-15", "action": "BUY",
         "forward_return_20d": -0.02, "trade_result": "LOSS"},
    ]
    engine = _make_engine_with_queue(queue, trade_history)

    observed_markers = []
    graph = MagicMock()
    graph.curr_state = {"sentinel": "PRE"}

    def _capture_state(_returns_losses):
        observed_markers.append(graph.curr_state["marker"])

    graph._run_reflections.side_effect = _capture_state

    engine._flush_reflection_with_forward_returns(graph, lag_days=10)
    assert observed_markers == ["A", "B"]
    # State is restored after the batch.
    assert graph.curr_state == {"sentinel": "PRE"}


def test_flush_restores_curr_state_even_when_reflection_raises():
    """Exception inside _run_reflections must not corrupt graph.curr_state."""
    queue = [("2024-01-15", {"company_of_interest": "COMI.CA"})]
    trade_history = [
        {"date": "2024-01-15", "action": "BUY",
         "forward_return_20d": 0.04, "trade_result": "WIN"},
    ]
    engine = _make_engine_with_queue(queue, trade_history)
    graph = MagicMock()
    graph.curr_state = {"sentinel": "PRE"}
    graph._run_reflections.side_effect = RuntimeError("LLM down")

    result = engine._flush_reflection_with_forward_returns(graph, lag_days=10)
    assert result == {"flushed": 0, "skipped": 1}
    assert graph.curr_state == {"sentinel": "PRE"}


def test_flush_empty_queue_returns_zeros():
    engine = _make_engine_with_queue([], [])
    graph = MagicMock()
    result = engine._flush_reflection_with_forward_returns(graph, lag_days=10)
    assert result == {"flushed": 0, "skipped": 0}
    graph._run_reflections.assert_not_called()


def test_flush_clears_queue_after_run():
    queue = [("2024-01-15", {"company_of_interest": "COMI.CA"})]
    trade_history = [
        {"date": "2024-01-15", "action": "BUY",
         "forward_return_20d": 0.04, "trade_result": "WIN"},
    ]
    engine = _make_engine_with_queue(queue, trade_history)
    graph = MagicMock()
    engine._flush_reflection_with_forward_returns(graph, lag_days=10)
    assert engine._reflection_state_queue == []


# ─── Reflector._default_metadata outcome propagation ──────────────────────────


def test_default_metadata_includes_outcome_when_returns_losses_passed():
    reflector = Reflector.__new__(Reflector)  # skip __init__ — no LLM needed
    meta = reflector._default_metadata(
        current_state={"company_of_interest": "COMI.CA", "trade_date": "2024-01-15"},
        agent_name="bull_memory",
        returns_losses={
            "verdict": "WIN",
            "forward_return": 0.045,
            "forward_horizon_days": 20,
            "action": "BUY",
        },
    )
    assert meta["agent_name"] == "bull_memory"
    assert meta["memory_type"] == "reflection"
    assert meta["ticker"] == "COMI.CA"
    assert meta["trade_date"] == "2024-01-15"
    assert "outcome" in meta
    # outcome is a JSON-encoded scalar (Chroma metadata is scalar-only).
    outcome = json.loads(meta["outcome"])
    assert outcome["verdict"] == "WIN"
    assert outcome["forward_return"] == pytest.approx(0.045)
    assert outcome["horizon_days"] == 20
    assert outcome["action"] == "BUY"


def test_default_metadata_omits_outcome_when_returns_losses_none():
    reflector = Reflector.__new__(Reflector)
    meta = reflector._default_metadata(
        current_state={"company_of_interest": "COMI.CA", "trade_date": "2024-01-15"},
        agent_name="bear_memory",
        returns_losses=None,
    )
    assert "outcome" not in meta


def test_default_metadata_omits_outcome_when_no_useful_fields():
    """returns_losses present but with neither verdict nor forward_return →
    no outcome key emitted (we'd be storing an empty JSON object)."""
    reflector = Reflector.__new__(Reflector)
    meta = reflector._default_metadata(
        current_state={"company_of_interest": "COMI.CA"},
        agent_name="trader_memory",
        returns_losses={"date": "2024-01-15"},  # no verdict, no forward_return
    )
    assert "outcome" not in meta
