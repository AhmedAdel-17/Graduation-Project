"""Regression tests for EGX execution cost model in scripts/backtester.py.

Locks:
  - BUY slippage increases execution price
  - SELL slippage decreases execution price
  - Commission = shares × exec_price × 0.189% per side
  - Low-liquidity slippage (0.5%) vs normal (0.1%)
  - T+2 settlement queue: SELL proceeds frozen for 2 business days
  - EGX trading calendar: Sun–Thu, skip Fri/Sat
  - Circuit breaker detection at ±10% daily move

Pure-unit: no LLM, no network, no graph.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scripts.backtester import (
    BacktestingEngine,
    EGX_BROKERAGE_RATE,
    EGX_STAMP_DUTY,
    EGX_FRA_FEE,
    EGX_TOTAL_COST_SIDE,
    EGX_SLIPPAGE_NORMAL,
    EGX_SLIPPAGE_LOW_LIQ,
    EGX_CIRCUIT_BREAKER,
    EGX_SETTLEMENT_DAYS,
)


# ─────────────────────────────────────────────────────────────────────────────
# Cost constants
# ─────────────────────────────────────────────────────────────────────────────


def test_cost_constants_match_egx_fee_schedule():
    """Verify hardcoded cost constants match documented EGX institutional fees."""
    assert EGX_BROKERAGE_RATE == pytest.approx(0.00175, abs=1e-6)
    assert EGX_STAMP_DUTY == pytest.approx(0.00005, abs=1e-6)
    assert EGX_FRA_FEE == pytest.approx(0.00009, abs=1e-6)


def test_total_cost_per_side():
    """Total per-side cost = brokerage + stamp duty + FRA = 0.189%."""
    expected = EGX_BROKERAGE_RATE + EGX_STAMP_DUTY + EGX_FRA_FEE
    assert EGX_TOTAL_COST_SIDE == pytest.approx(expected, abs=1e-9)
    assert EGX_TOTAL_COST_SIDE == pytest.approx(0.00189, abs=1e-6)


def test_slippage_constants():
    """Normal slippage = 0.1%, low-liquidity = 0.5%."""
    assert EGX_SLIPPAGE_NORMAL == pytest.approx(0.001, abs=1e-6)
    assert EGX_SLIPPAGE_LOW_LIQ == pytest.approx(0.005, abs=1e-6)


def test_circuit_breaker_and_settlement_constants():
    """Circuit breaker at ±10%, settlement T+2."""
    assert EGX_CIRCUIT_BREAKER == pytest.approx(0.10, abs=1e-6)
    assert EGX_SETTLEMENT_DAYS == 2


# ─────────────────────────────────────────────────────────────────────────────
# _apply_execution_costs
# ─────────────────────────────────────────────────────────────────────────────


def _make_engine() -> BacktestingEngine:
    return BacktestingEngine(initial_capital=1_000_000.0, benchmark_ticker=None)


class TestApplyExecutionCosts:
    """Lock the _apply_execution_costs method behavior."""

    def test_buy_slippage_increases_price(self):
        """BUY execution price = close × (1 + slippage) — trader pays more."""
        e = _make_engine()
        exec_price, _ = e._apply_execution_costs("BUY", 1000, 50.0, low_liquidity=False)
        expected = 50.0 * (1.0 + EGX_SLIPPAGE_NORMAL)
        assert exec_price == pytest.approx(expected, rel=1e-9)
        assert exec_price > 50.0

    def test_sell_slippage_decreases_price(self):
        """SELL execution price = close × (1 - slippage) — trader receives less."""
        e = _make_engine()
        exec_price, _ = e._apply_execution_costs("SELL", 1000, 50.0, low_liquidity=False)
        expected = 50.0 * (1.0 - EGX_SLIPPAGE_NORMAL)
        assert exec_price == pytest.approx(expected, rel=1e-9)
        assert exec_price < 50.0

    def test_commission_formula(self):
        """Commission = shares × exec_price × EGX_TOTAL_COST_SIDE."""
        e = _make_engine()
        shares = 1000
        close = 100.0
        exec_price, commission = e._apply_execution_costs("BUY", shares, close)
        expected_commission = shares * exec_price * EGX_TOTAL_COST_SIDE
        assert commission == pytest.approx(expected_commission, rel=1e-9)

    def test_low_liquidity_uses_wider_slippage(self):
        """Low-liquidity flag selects 0.5% slippage instead of 0.1%."""
        e = _make_engine()
        exec_normal, _ = e._apply_execution_costs("BUY", 100, 50.0, low_liquidity=False)
        exec_low_liq, _ = e._apply_execution_costs("BUY", 100, 50.0, low_liquidity=True)
        assert exec_low_liq > exec_normal
        expected_low = 50.0 * (1.0 + EGX_SLIPPAGE_LOW_LIQ)
        assert exec_low_liq == pytest.approx(expected_low, rel=1e-9)

    def test_commission_scales_with_shares(self):
        """Doubling shares doubles commission (linear)."""
        e = _make_engine()
        _, comm_100 = e._apply_execution_costs("BUY", 100, 50.0)
        _, comm_200 = e._apply_execution_costs("BUY", 200, 50.0)
        assert comm_200 == pytest.approx(comm_100 * 2, rel=1e-9)

    def test_symmetry_slippage_works_against_trader(self):
        """BUY slippage is positive (pays more), SELL is negative (receives less).
        Both sides reduce the trader's effective return."""
        e = _make_engine()
        buy_price, _ = e._apply_execution_costs("BUY", 100, 100.0)
        sell_price, _ = e._apply_execution_costs("SELL", 100, 100.0)
        # Round-trip slippage cost
        assert buy_price > 100.0
        assert sell_price < 100.0


# ─────────────────────────────────────────────────────────────────────────────
# T+2 Settlement
# ─────────────────────────────────────────────────────────────────────────────


class TestSettlement:
    """Lock T+2 settlement queue behavior."""

    def test_settlement_date_skips_friday_saturday(self):
        """T+2 from Wednesday → next Sunday (skip Fri+Sat on EGX calendar)."""
        e = _make_engine()
        # 2024-01-03 is a Wednesday
        settle = e._get_settlement_date("2024-01-03")
        # Thu +1, skip Fri, skip Sat, Sun +2
        assert settle == "2024-01-07"

    def test_settlement_date_normal_case(self):
        """T+2 from Sunday → Tuesday (no skips needed)."""
        e = _make_engine()
        # 2024-01-07 is a Sunday
        settle = e._get_settlement_date("2024-01-07")
        assert settle == "2024-01-09"

    def test_pending_cash_not_released_early(self):
        """SELL proceeds stay frozen before settlement date."""
        e = _make_engine()
        e.pending_cash_settlements.append(("2024-01-10", 50_000.0))
        initial_cash = e.cash
        e._settle_pending_cash("2024-01-09")
        assert e.cash == initial_cash
        assert len(e.pending_cash_settlements) == 1

    def test_pending_cash_released_on_settlement(self):
        """SELL proceeds released when settlement date is reached."""
        e = _make_engine()
        e.pending_cash_settlements.append(("2024-01-10", 50_000.0))
        initial_cash = e.cash
        e._settle_pending_cash("2024-01-10")
        assert e.cash == initial_cash + 50_000.0
        assert len(e.pending_cash_settlements) == 0

    def test_egx_trading_days(self):
        """EGX trades Sun–Thu. Fri and Sat are off."""
        e = _make_engine()
        # 2024-01-07 = Sunday (trading day)
        assert e._is_egx_trading_day(datetime(2024, 1, 7)) is True
        # 2024-01-08 = Monday
        assert e._is_egx_trading_day(datetime(2024, 1, 8)) is True
        # 2024-01-11 = Thursday
        assert e._is_egx_trading_day(datetime(2024, 1, 11)) is True
        # 2024-01-05 = Friday (off)
        assert e._is_egx_trading_day(datetime(2024, 1, 5)) is False
        # 2024-01-06 = Saturday (off)
        assert e._is_egx_trading_day(datetime(2024, 1, 6)) is False


# ─────────────────────────────────────────────────────────────────────────────
# Circuit Breaker
# ─────────────────────────────────────────────────────────────────────────────


class TestCircuitBreaker:
    """Lock ±10% circuit breaker detection.

    Circuit breaker now compares current price against previous trading
    day's close (from daily OHLCV), not against self.prev_prices.
    """

    @staticmethod
    def _ohlcv(prev_close: float, current: float) -> list:
        """Build minimal OHLCV rows where second-to-last close = prev_close."""
        return [{"close": prev_close}, {"close": current}]

    def test_no_trigger_within_limit(self):
        """9.9% daily move does not trigger circuit breaker."""
        e = _make_engine()
        rows = self._ohlcv(100.0, 109.9)
        assert e._check_circuit_breaker("COMI.CA", 109.9, ohlcv_rows=rows) is False

    def test_trigger_at_10_percent(self):
        """10% daily move triggers circuit breaker."""
        e = _make_engine()
        rows = self._ohlcv(100.0, 110.0)
        assert e._check_circuit_breaker("COMI.CA", 110.0, ohlcv_rows=rows) is True

    def test_trigger_on_drop(self):
        """10% daily drop also triggers circuit breaker."""
        e = _make_engine()
        rows = self._ohlcv(100.0, 90.0)
        assert e._check_circuit_breaker("COMI.CA", 90.0, ohlcv_rows=rows) is True

    def test_no_prev_price_no_trigger(self):
        """No OHLCV data → no circuit breaker (first day)."""
        e = _make_engine()
        assert e._check_circuit_breaker("COMI.CA", 50.0, ohlcv_rows=None) is False
