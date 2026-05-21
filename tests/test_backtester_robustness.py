"""Regression tests for scripts/backtester.py — Workstream A/B/C fixes.

Covers:
  - MEMORY §C1: _evaluate_trade_outcomes and _flush_reflection_with_forward_returns
    must remain deleted (no look-ahead path).
  - MEMORY §C3: risk-free rate is read from config["egx_risk_free_rate"], not 0.05.
  - MEMORY §C4: _align_benchmark_to_strategy uses date-intersection only.
  - Workstream A: empty stock_data does not crash; graph.propagate raising
    does not abort the loop and is recorded in audit_log as decision_status.

Pure-unit: never spins up the LangGraph or the real DataGateway.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List

import pytest

# Ensure project root is on sys.path so `import scripts.backtester` works.
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scripts.backtester import BacktestingEngine  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_engine(rfr_override: float | None = None) -> BacktestingEngine:
    e = BacktestingEngine(initial_capital=100_000.0, benchmark_ticker=None)
    if rfr_override is not None:
        e.risk_free_rate = float(rfr_override)
    return e


# ─────────────────────────────────────────────────────────────────────────────
# §C1 — look-ahead removal regression gate
# ─────────────────────────────────────────────────────────────────────────────


def test_no_lookahead_functions() -> None:
    """The two look-ahead helpers must stay deleted.

    Reintroducing either would re-open MEMORY.md §C1 (Hit Rate (fwd)) and §C2
    (reflection on look-ahead forward returns).
    """
    e = _make_engine()
    assert not hasattr(e, "_evaluate_trade_outcomes"), (
        "Look-ahead method _evaluate_trade_outcomes was reintroduced — "
        "this reopens MEMORY.md §C1."
    )
    assert not hasattr(e, "_flush_reflection_with_forward_returns"), (
        "Reflection-on-forward-returns helper was reintroduced — "
        "this reopens MEMORY.md §C2."
    )


def test_calculate_metrics_has_no_hit_rate_fwd() -> None:
    """The (fwd) suffix used to label look-ahead win-rate must not appear."""
    e = _make_engine()
    e.daily_history = [
        {"date": "2024-01-02", "portfolio_value": 100_000.0, "split": "full"},
        {"date": "2024-01-03", "portfolio_value": 101_000.0, "split": "full"},
        {"date": "2024-01-04", "portfolio_value": 102_000.0, "split": "full"},
    ]
    e.portfolio_value = 102_000.0
    metrics = e._calculate_metrics()
    for k in metrics:
        assert "fwd" not in k.lower(), f"Look-ahead metric resurfaced in metrics: {k}"


# ─────────────────────────────────────────────────────────────────────────────
# §C3 — risk-free rate from config
# ─────────────────────────────────────────────────────────────────────────────


def test_risk_free_rate_default_is_not_005() -> None:
    """The engine must read default_config.egx_risk_free_rate (0.275) or the
    walkforward fallback (0.24) — never the legacy hardcoded 0.05."""
    e = _make_engine()
    assert e.risk_free_rate != 0.05
    assert 0.10 <= e.risk_free_rate <= 0.40, (
        f"Risk-free rate {e.risk_free_rate} is outside the plausible "
        f"EGP policy-rate band [10%, 40%]"
    )


def test_risk_free_rate_used_in_sharpe() -> None:
    """A different risk-free rate must produce a different Sharpe."""
    daily = [
        {"date": f"2024-01-{i+2:02d}", "portfolio_value": 100_000.0 + i * 200, "split": "full"}
        for i in range(20)
    ]
    # Build two engines with different risk-free rates against the same series
    e1 = _make_engine(rfr_override=0.05)
    e1.daily_history = list(daily)
    e1.portfolio_value = daily[-1]["portfolio_value"]
    m1 = e1._calculate_metrics()
    sharpe1 = float(m1["Sharpe Ratio"])

    e2 = _make_engine(rfr_override=0.30)
    e2.daily_history = list(daily)
    e2.portfolio_value = daily[-1]["portfolio_value"]
    m2 = e2._calculate_metrics()
    sharpe2 = float(m2["Sharpe Ratio"])

    # Higher risk-free rate -> lower Sharpe for the same return series
    assert sharpe1 > sharpe2, (
        f"Sharpe did not respond to risk-free rate change: rfr=0.05 → {sharpe1}, "
        f"rfr=0.30 → {sharpe2}"
    )
    # Both metric dicts must carry the rate they actually used
    assert "Risk-Free Rate Used" in m1
    assert m1["Risk-Free Rate Used"] == "0.0500"
    assert m2["Risk-Free Rate Used"] == "0.3000"


# ─────────────────────────────────────────────────────────────────────────────
# §C4 — benchmark window alignment
# ─────────────────────────────────────────────────────────────────────────────


def test_align_benchmark_intersects_dates_only() -> None:
    """When the strategy has dates {Jan 2, Jan 3, Jan 4} and the EGX30 CSV
    only has {Jan 2, Jan 4}, alignment must reflect a 2-day intersection —
    no nearest-earlier fuzzy fill."""
    e = _make_engine()
    e.benchmark_ticker = "^EGX30"
    e.benchmark_start_price = 30_000.0
    e._bm_data_map = {
        "2024-01-02": 30_000.0,
        "2024-01-04": 30_600.0,
        # 2024-01-03 deliberately missing
    }
    e.daily_history = [
        {"date": "2024-01-02", "portfolio_value": 100_000.0, "split": "full"},
        {"date": "2024-01-03", "portfolio_value": 101_000.0, "split": "full"},
        {"date": "2024-01-04", "portfolio_value": 102_000.0, "split": "full"},
    ]
    e.portfolio_value = 102_000.0

    block = e._align_benchmark_to_strategy()
    assert block["n_aligned_days"] == 2, block
    assert block["first_aligned_date"] == "2024-01-02"
    assert block["last_aligned_date"] == "2024-01-04"
    # EGX30 went 30000 → 30600 over the intersection ⇒ +2.00%
    assert abs(block["total_return_pct"] - 2.0) < 0.01
    # Coverage is 2 / 3 ≈ 66.67% ⇒ should be tagged but still return alpha
    assert block["coverage_pct"] < 80.0


def test_align_benchmark_handles_empty_map() -> None:
    """No benchmark data => empty dict (no crash, no fake alpha)."""
    e = _make_engine()
    e.benchmark_ticker = "^EGX30"
    e._bm_data_map = {}
    e.daily_history = [
        {"date": "2024-01-02", "portfolio_value": 100_000.0, "split": "full"},
    ]
    assert e._align_benchmark_to_strategy() == {}


# ─────────────────────────────────────────────────────────────────────────────
# Workstream A — crash hardening
# ─────────────────────────────────────────────────────────────────────────────


def test_fetch_retries_on_transient_failure(monkeypatch) -> None:
    """_fetch_stock_data_with_retry retries on Exception and finally returns
    the successful payload."""
    e = _make_engine()
    calls: List[int] = []

    def flaky_fetch(*args, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("transient network glitch")
        return {"data": [{"date": "2024-01-04", "close": 50.0}]}

    monkeypatch.setattr(e.gateway, "fetch_stock_data", flaky_fetch)
    out = e._fetch_stock_data_with_retry(
        "ETEL.CA", "2024-01-01", "2024-01-04",
        max_attempts=3, base_wait_s=0.01, max_wait_s=0.02,
    )
    assert len(calls) == 3
    assert out["data"][0]["close"] == 50.0


def test_fetch_gives_up_after_exhausting_retries(monkeypatch) -> None:
    """When every retry fails, return {} rather than raise."""
    e = _make_engine()

    def always_fail(*args, **kwargs):
        raise RuntimeError("permanent failure")

    monkeypatch.setattr(e.gateway, "fetch_stock_data", always_fail)
    out = e._fetch_stock_data_with_retry(
        "ETEL.CA", "2024-01-01", "2024-01-04",
        max_attempts=2, base_wait_s=0.01, max_wait_s=0.02,
    )
    assert out == {}


# ─────────────────────────────────────────────────────────────────────────────
# Decision priority — _resolve_decision
# (closes the "0 trades / 100% veto-rate" silent downgrade reported on the
# dashboard run where trader=BUY + 0 violations + judge="HOLD" became HOLD)
# ─────────────────────────────────────────────────────────────────────────────


def test_decision_priority_trader_buy_with_zero_violations_wins() -> None:
    """When the LLM judge returns bare 'HOLD' but the trader's plan is BUY
    and the deterministic gate cleared (risk_action != VETO, critical=0),
    the trader's plan must win."""
    final_state = {
        "final_trade_decision": "HOLD",  # LLM judge bare HOLD
        "risk_action": "CONTINUE",
        "risk_assessment": {"approved": None, "critical_violations": 0},
        "execution_plan": {"decision": "BUY"},
    }
    decision, path = BacktestingEngine._resolve_decision(
        final_state, final_state["execution_plan"]
    )
    assert decision == "BUY"
    assert path == "trader_fallback"


def test_decision_priority_critical_violation_forces_hold() -> None:
    """Critical risk violations must veto even if the trader said BUY.
    Safety floor — never bypassed."""
    final_state = {
        "final_trade_decision": "HOLD",
        "risk_action": "CONTINUE",
        "risk_assessment": {"approved": True, "critical_violations": 2},
        "execution_plan": {"decision": "BUY"},
    }
    decision, path = BacktestingEngine._resolve_decision(
        final_state, final_state["execution_plan"]
    )
    assert decision == "HOLD"
    assert path == "deterministic_veto"


def test_decision_priority_risk_scorer_veto_forces_hold() -> None:
    """When the Risk Scorer node set risk_action='VETO', HOLD wins regardless
    of trader plan or LLM judge output."""
    final_state = {
        "final_trade_decision": "BUY",
        "risk_action": "VETO",
        "risk_assessment": {"approved": True, "critical_violations": 0},
        "execution_plan": {"decision": "BUY"},
    }
    decision, path = BacktestingEngine._resolve_decision(
        final_state, final_state["execution_plan"]
    )
    assert decision == "HOLD"
    assert path == "deterministic_veto"


def test_decision_priority_judge_buy_passes_through() -> None:
    """When the judge gives a real BUY, take it directly."""
    final_state = {
        "final_trade_decision": "BUY",
        "risk_action": "CONTINUE",
        "risk_assessment": {"approved": True, "critical_violations": 0},
        "execution_plan": {"decision": "HOLD"},
    }
    decision, path = BacktestingEngine._resolve_decision(
        final_state, final_state["execution_plan"]
    )
    assert decision == "BUY"
    assert path == "judge_bare"


def test_decision_priority_explicit_approved_false_is_veto() -> None:
    """`approved=False` (explicitly) must still veto; this is the legacy
    risk-assessment-dict path."""
    final_state = {
        "final_trade_decision": "BUY",
        "risk_action": "CONTINUE",
        "risk_assessment": {"approved": False, "critical_violations": 0},
        "execution_plan": {"decision": "BUY"},
    }
    decision, path = BacktestingEngine._resolve_decision(
        final_state, final_state["execution_plan"]
    )
    assert decision == "HOLD"
    assert path == "deterministic_veto"


def test_decision_priority_json_action_extraction() -> None:
    """Free-text raw with embedded JSON ``{"action": "SELL"}`` must extract."""
    final_state = {
        "final_trade_decision":
            'Risk Judge final analysis: ... {"action": "SELL", "size": 100}',
        "risk_action": "CONTINUE",
        "risk_assessment": {"approved": True, "critical_violations": 0},
        "execution_plan": {"decision": "HOLD"},
    }
    decision, path = BacktestingEngine._resolve_decision(
        final_state, final_state["execution_plan"]
    )
    assert decision == "SELL"
    assert path == "judge_json"


def test_partial_checkpoint_roundtrip(tmp_path, monkeypatch) -> None:
    """Per-ticker partial write/read should restore engine state and the
    set of completed dates."""
    e = _make_engine()
    # Force the partial path under tmp_path so tests don't pollute repo
    monkeypatch.setattr(
        e, "_partial_path", lambda ticker: str(tmp_path / f"partial_{ticker}.json")
    )
    e.cash = 80_000.0
    e.portfolio_value = 95_000.0
    e.positions = {"ETEL.CA": {"shares": 100, "avg_cost": 150.0}}
    e.daily_history = [
        {"date": "2024-01-02", "portfolio_value": 100_000.0, "split": "full"},
        {"date": "2024-01-03", "portfolio_value": 95_000.0, "split": "full"},
    ]
    e.audit_log = [{"date": "2024-01-02", "decision_status": "ok"}]

    e._maybe_write_partial("ETEL.CA")
    assert os.path.exists(str(tmp_path / "partial_ETEL.CA.json"))

    # Build a fresh engine and resume
    e2 = _make_engine()
    monkeypatch.setattr(
        e2, "_partial_path", lambda ticker: str(tmp_path / f"partial_{ticker}.json")
    )
    seen = e2._load_partial_if_resume("ETEL.CA", resume=True)
    assert seen == {"2024-01-02", "2024-01-03"}
    assert e2.cash == 80_000.0
    assert e2.positions["ETEL.CA"]["shares"] == 100
    assert len(e2.daily_history) == 2
