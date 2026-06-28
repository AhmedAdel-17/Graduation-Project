"""Unit tests for tradingagents/backtest/decision_metrics.py.

Synthetic decisions + a deterministic price path with KNOWN forward moves, so
hit-rate, confusion matrix, and IC have hand-checkable expected values.
"""

from __future__ import annotations

from tradingagents.backtest.decision_metrics import (
    Decision,
    compute_decision_quality,
    forward_returns_for_decisions,
)


def _linear_dates(n: int):
    """n consecutive ISO dates (calendar; the module only cares about ordering
    and trading-day *index*, not gaps)."""
    return [f"2024-01-{i + 1:02d}" for i in range(n)]


def test_forward_returns_fixed_horizon():
    dates = _linear_dates(11)
    # Price doubles by index: +10% step each day -> close[i] = 100*(1.1**i)
    prices = {d: 100.0 * (1.1 ** i) for i, d in enumerate(dates)}
    decs = [Decision(date=dates[0], action="BUY")]
    out = forward_returns_for_decisions(decs, prices, horizon_days=5)
    _, fwd = out[0]
    assert abs(fwd - (1.1 ** 5 - 1.0)) < 1e-9


def test_not_evaluable_past_series_end():
    dates = _linear_dates(5)
    prices = {d: 100.0 for d in dates}
    decs = [Decision(date=dates[3], action="BUY")]  # +5 days exceeds the series
    out = forward_returns_for_decisions(decs, prices, horizon_days=5)
    assert out[0][1] is None


def test_perfect_agent_hit_rate_and_ic():
    # Two regimes: first 5 days rising, last 6 falling. A perfect agent BUYs in
    # the rising regime and SELLs in the falling one.
    dates = _linear_dates(12)
    prices = {}
    for i, d in enumerate(dates):
        prices[d] = 100.0 + (i * 5.0 if i <= 5 else (5 * 5.0) - (i - 5) * 5.0)
    decs = [
        Decision(date=dates[0], action="BUY", confidence=0.9),
        Decision(date=dates[6], action="SELL", confidence=0.9),
    ]
    q = compute_decision_quality(decs, prices, horizons=(3,), hold_band_pct=0.005)
    h = q["horizons"]["3"]
    assert h["actionable_n"] == 2
    assert h["actionable_hit_rate"] == 1.0
    # Perfect directional alignment -> IC should be strongly positive.
    assert h["information_coefficient"] is None or h["information_coefficient"] > 0.0
    assert h["by_decision"]["BUY"]["hit_rate"] == 1.0
    assert h["by_decision"]["SELL"]["hit_rate"] == 1.0


def test_hold_scored_correct_when_flat():
    dates = _linear_dates(8)
    prices = {d: 100.0 for d in dates}  # perfectly flat
    decs = [Decision(date=dates[0], action="HOLD")]
    q = compute_decision_quality(decs, prices, horizons=(3,), hold_band_pct=0.01)
    h = q["horizons"]["3"]
    assert h["by_decision"]["HOLD"]["hit_rate"] == 1.0
    assert h["confusion_matrix"]["HOLD"]["FLAT"] == 1


def test_confusion_matrix_and_distribution():
    dates = _linear_dates(10)
    prices = {d: 100.0 * (1.02 ** i) for i, d in enumerate(dates)}  # always rising
    decs = [
        Decision(date=dates[0], action="BUY"),
        Decision(date=dates[1], action="SELL"),
        Decision(date=dates[2], action="HOLD"),
    ]
    q = compute_decision_quality(decs, prices, horizons=(2,), hold_band_pct=0.01)
    assert q["action_distribution"] == {"BUY": 1, "SELL": 1, "HOLD": 1}
    cm = q["horizons"]["2"]["confusion_matrix"]
    # Every forward move is UP (+~4%).
    assert cm["BUY"]["UP"] == 1
    assert cm["SELL"]["UP"] == 1
    assert cm["HOLD"]["UP"] == 1
    assert q["horizons"]["2"]["base_rate_up"] == 1.0


def test_empty_and_degenerate_inputs():
    assert compute_decision_quality([], {}, horizons=(5,))["n_decisions"] == 0
    # Decisions but no usable prices -> nothing evaluable, no crash.
    q = compute_decision_quality(
        [{"date": "2024-01-01", "decision": "BUY"}], {}, horizons=(5,)
    )
    assert q["horizons"]["5"]["n_evaluated"] == 0


def test_dict_input_shapes():
    dates = _linear_dates(8)
    prices = {d: 100.0 + i for i, d in enumerate(dates)}
    # Accept the audit_log shape (parsed_decision) and confidence on 0-100 scale.
    decs = [{"date": dates[0], "parsed_decision": "BUY", "confidence": 80}]
    q = compute_decision_quality(decs, prices, horizons=(3,))
    assert q["n_decisions"] == 1
    assert q["action_distribution"]["BUY"] == 1
