"""Tests for the cost-aware BUY gate (remediation Track A, Phase 1).

Covers:
- the shared EGX cost model (`egx_costs.round_trip_cost_pct`)
- the deterministic cost-hurdle downgrade (`risk_manager._cost_hurdle_note`)
- the combined final gate (`risk_manager._final_gate`)

All deterministic — no LLM or network.
"""
import pytest

from tradingagents.dataflows.egx_costs import (
    round_trip_cost_pct,
    ROUND_TRIP_COMMISSION_PCT,
)
from tradingagents.agents.managers.risk_manager import (
    _cost_hurdle_note,
    _final_gate,
)


# ── Cost model ───────────────────────────────────────────────────────────────

def test_round_trip_commission_unchanged():
    """The legacy RL constant (0.00378) must be preserved exactly."""
    assert ROUND_TRIP_COMMISSION_PCT == pytest.approx(0.00378, abs=1e-6)


def test_round_trip_cost_includes_slippage():
    """Round-trip cost adds slippage on both sides; low-liq is more expensive."""
    normal = round_trip_cost_pct(low_liquidity=False)
    low_liq = round_trip_cost_pct(low_liquidity=True)
    assert normal == pytest.approx(0.00578, abs=1e-5)   # 0.378% + 2×0.1%
    assert low_liq == pytest.approx(0.01378, abs=1e-5)  # 0.378% + 2×0.5%
    assert low_liq > normal


# ── Cost-hurdle note ─────────────────────────────────────────────────────────

def _bull(upside_pct):
    return {"upside_scenario": {"base_case_upside_pct": upside_pct}}


def test_buy_below_hurdle_is_downgraded():
    # normal-liq hurdle at 2× = ~1.16%; 0.5% upside fails.
    note = _cost_hurdle_note("BUY", _bull(0.5), low_liquidity=False, min_edge_multiple=2.0)
    assert note is not None
    assert "downgrading BUY to HOLD" in note


def test_buy_above_hurdle_passes():
    note = _cost_hurdle_note("BUY", _bull(15.0), low_liquidity=False, min_edge_multiple=2.0)
    assert note is None


def test_negative_upside_is_downgraded():
    note = _cost_hurdle_note("BUY", _bull(-3.0), low_liquidity=False, min_edge_multiple=2.0)
    assert note is not None


def test_low_liquidity_raises_the_bar():
    # 2% upside clears the normal hurdle (~1.16%) but not the low-liq hurdle (~2.76%).
    assert _cost_hurdle_note("BUY", _bull(2.0), low_liquidity=False, min_edge_multiple=2.0) is None
    assert _cost_hurdle_note("BUY", _bull(2.0), low_liquidity=True, min_edge_multiple=2.0) is not None


def test_gate_disabled_when_multiple_zero():
    assert _cost_hurdle_note("BUY", _bull(0.1), low_liquidity=False, min_edge_multiple=0.0) is None


def test_non_buy_not_gated():
    assert _cost_hurdle_note("SELL", _bull(0.1), low_liquidity=False, min_edge_multiple=2.0) is None
    assert _cost_hurdle_note("HOLD", _bull(0.1), low_liquidity=False, min_edge_multiple=2.0) is None


def test_missing_or_unparseable_upside_fails_open():
    # No bull thesis / no upside / junk → cannot assess → no downgrade.
    assert _cost_hurdle_note("BUY", None, low_liquidity=False, min_edge_multiple=2.0) is None
    assert _cost_hurdle_note("BUY", {}, low_liquidity=False, min_edge_multiple=2.0) is None
    assert _cost_hurdle_note("BUY", _bull("n/a"), low_liquidity=False, min_edge_multiple=2.0) is None


# ── Final gate integration ───────────────────────────────────────────────────

def test_final_gate_downgrades_marginal_buy():
    decision, issues = _final_gate(
        "BUY",
        {"symbol": "COMI.CA"},
        current_position={},
        bull_thesis=_bull(0.4),
        low_liquidity=False,
        min_edge_multiple=2.0,
    )
    assert decision == "HOLD"
    assert any("Cost-hurdle" in i for i in issues)


def test_final_gate_allows_strong_buy():
    decision, issues = _final_gate(
        "BUY",
        {"symbol": "COMI.CA"},
        current_position={},
        bull_thesis=_bull(20.0),
        low_liquidity=False,
        min_edge_multiple=2.0,
    )
    assert decision == "BUY"


def test_final_gate_sell_survives_flat_portfolio():
    # SELL is a directional research SIGNAL and is no longer coerced to HOLD when
    # the portfolio holds no shares — holdings are an execution concern, not a
    # reason to rewrite the bearish view. (The old long-only Gate 1 was removed.)
    decision, issues = _final_gate(
        "SELL",
        {"symbol": "COMI.CA"},
        current_position={"shares": 0},
        bull_thesis=_bull(20.0),
        min_edge_multiple=2.0,
    )
    assert decision == "SELL"
    assert not any("long-only" in i for i in issues)


def test_final_gate_backward_compatible_defaults():
    # Old 3-arg call style (no cost args) must still work and not gate.
    decision, issues = _final_gate("BUY", {"symbol": "COMI.CA"}, {})
    assert decision == "BUY"
    assert issues == []
