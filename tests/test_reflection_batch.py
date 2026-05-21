"""Tests for PR 7 — post-backtest reflection batch (MEMORY.md §C2 fix).

Reduced scope after the MEMORY §C1 follow-up: ``_flush_reflection_with_forward_returns``
and ``_evaluate_trade_outcomes`` were both deleted because they required
fetching prices AFTER the backtest's end_date (look-ahead). The only valid
regression gate from PR 7 that survives is the grep-style assertion that
``graph.reflect_and_remember(`` does not appear inside ``scripts/backtester.py``
as an executable call — that's still the right invariant.

The Reflector ``outcome`` metadata propagation behaviour is covered by
``tests/test_seed_memories.py`` and the reflection-side unit tests in
``tradingagents/graph/reflection.py``'s own test surface; nothing in this
file exercises Reflector directly anymore.

See ``tests/test_backtester_robustness.py`` for the §C1/C3/C4 regression
gates added when the look-ahead path was deleted.
"""

from __future__ import annotations

from pathlib import Path


def test_in_loop_reflect_and_remember_call_removed():
    """The single ``reflect_and_remember`` call inside run_backtest must be
    gone. Regression gate for MEMORY.md §C2.
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


def test_lookahead_eval_function_remains_deleted():
    """``_evaluate_trade_outcomes`` must not be reintroduced (MEMORY.md §C1).

    Also defends the per-trade ``trade_result`` annotation pipeline that fed
    it from coming back via a different code path.
    """
    src = Path(__file__).resolve().parents[1] / "scripts" / "backtester.py"
    text = src.read_text(encoding="utf-8")
    assert "def _evaluate_trade_outcomes" not in text, (
        "scripts/backtester.py reintroduced _evaluate_trade_outcomes — "
        "this re-opens MEMORY.md §C1 (Hit Rate (fwd) look-ahead)."
    )
    assert "def _flush_reflection_with_forward_returns" not in text, (
        "scripts/backtester.py reintroduced _flush_reflection_with_forward_returns — "
        "the helper depends on look-ahead forward returns and was removed."
    )
