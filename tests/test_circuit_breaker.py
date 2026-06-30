"""
Regression tests for the EGX circuit breaker logic in backtester.py.

The circuit breaker should compare the current evaluation-date close against
the **previous trading day's** close (from daily OHLCV), NOT against the
previous evaluation date's price.

With interval=20, a stock can easily move ±30% over 20 days while never
exceeding ±10% on any single day.  The old implementation compared against
``self.prev_prices[ticker]`` (the previous evaluation-date price), causing
false positives that suppressed the agent from evaluating during rallies.

Evidence from the 2026-06-16 benchmark:
  - TMGH.CA Jan 22 +30.4%, Feb 11 +36.6%, Mar 3 +55.6% — all false positives
  - SWDY.CA Jan 22 +13.1%, Mar 3 +11.1%, Jun 16 +35.8% — all false positives
  - FWRY.CA Mar 24 +35.0%, Apr 14 +11.4%, May 26 -20.8%, Jul 7 +14.2%
"""

import pytest
from scripts.backtester import BacktestingEngine, EGX_CIRCUIT_BREAKER


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv_rows(closes: list[float]) -> list[dict]:
    """Build a minimal OHLCV row list from a list of close prices."""
    return [{"close": c} for c in closes]


def _make_backtester(**kwargs) -> BacktestingEngine:
    """Create a minimal BacktestingEngine for unit testing."""
    return BacktestingEngine(
        initial_capital=kwargs.get("initial_capital", 1_000_000),
    )


# ---------------------------------------------------------------------------
# Test 1: +30% cumulative over 20 days with daily moves <10% — NO halt
# ---------------------------------------------------------------------------

class TestNoFalsePositiveOnCumulativeRally:
    """A stock rising +30% over 20 days with daily moves <10% must NOT halt."""

    def test_tmgh_style_rally_no_halt(self):
        """TMGH.CA went 23.86 → 31.11 (+30.4%) over 20 days.
        If the previous trading day close was 29.50, the daily move is
        31.11 / 29.50 - 1 = 5.5%, well under 10%.
        """
        bt = _make_backtester()
        # 20 days of gradual rise: ~1.3% per day for 20 days ≈ +30% total
        closes = [23.86 + i * 0.36 for i in range(20)]  # 23.86 → 30.70
        closes.append(31.11)  # final day
        ohlcv = _make_ohlcv_rows(closes)

        halted = bt._check_circuit_breaker("TMGH.CA", 31.11, ohlcv_rows=ohlcv)
        assert halted is False, (
            "Circuit breaker falsely triggered on a +30% cumulative rally "
            "with daily moves under 10%."
        )

    def test_swdy_style_rally_no_halt(self):
        """SWDY.CA went 27.74 → 31.37 (+13.1%) over 20 days.
        With gradual daily moves of ~0.6%, this must not halt.
        """
        bt = _make_backtester()
        closes = [27.74 + i * 0.18 for i in range(20)]  # gradual rise
        closes.append(31.37)
        ohlcv = _make_ohlcv_rows(closes)

        halted = bt._check_circuit_breaker("SWDY.CA", 31.37, ohlcv_rows=ohlcv)
        assert halted is False, (
            "Circuit breaker falsely triggered on a +13% cumulative rally "
            "with daily moves under 10%."
        )


# ---------------------------------------------------------------------------
# Test 2: True +11% from previous trading day — SHOULD halt
# ---------------------------------------------------------------------------

class TestTrueDailyCircuitBreaker:
    """A stock moving ±11% from the previous trading day SHOULD trigger."""

    def test_11pct_up_halts(self):
        """A single-day +11% move must trigger the circuit breaker."""
        bt = _make_backtester()
        prev_close = 50.0
        current = 55.50  # +11%
        ohlcv = _make_ohlcv_rows([45.0, 48.0, prev_close, current])

        halted = bt._check_circuit_breaker("TEST.CA", current, ohlcv_rows=ohlcv)
        assert halted is True, (
            f"Circuit breaker should trigger on +11% daily move "
            f"({prev_close} → {current})."
        )

    def test_11pct_down_halts(self):
        """A single-day -11% move must trigger the circuit breaker."""
        bt = _make_backtester()
        prev_close = 50.0
        current = 44.50  # -11%
        ohlcv = _make_ohlcv_rows([55.0, 52.0, prev_close, current])

        halted = bt._check_circuit_breaker("TEST.CA", current, ohlcv_rows=ohlcv)
        assert halted is True, (
            f"Circuit breaker should trigger on -11% daily move "
            f"({prev_close} → {current})."
        )

    def test_exactly_10pct_halts(self):
        """Exactly ±10% should trigger (>=, not >)."""
        bt = _make_backtester()
        prev_close = 100.0
        current = 110.0  # exactly +10%
        ohlcv = _make_ohlcv_rows([95.0, 98.0, prev_close, current])

        halted = bt._check_circuit_breaker("TEST.CA", current, ohlcv_rows=ohlcv)
        assert halted is True, "Exactly +10% should trigger the circuit breaker."

    def test_9pct_does_not_halt(self):
        """A +9% daily move should NOT trigger."""
        bt = _make_backtester()
        prev_close = 100.0
        current = 109.0  # +9%
        ohlcv = _make_ohlcv_rows([95.0, 98.0, prev_close, current])

        halted = bt._check_circuit_breaker("TEST.CA", current, ohlcv_rows=ohlcv)
        assert halted is False, "+9% daily move should NOT trigger circuit breaker."


# ---------------------------------------------------------------------------
# Test 3: No future OHLCV rows are used
# ---------------------------------------------------------------------------

class TestNoFutureDataInCircuitBreaker:
    """Circuit breaker uses only the second-to-last row (previous trading day).
    It does not peek beyond the current evaluation date."""

    def test_prev_trading_day_close_extraction(self):
        """_prev_trading_day_close must return the second-to-last row's close."""
        rows = _make_ohlcv_rows([10.0, 20.0, 30.0, 40.0, 50.0])
        # second-to-last is 40.0
        prev = BacktestingEngine._prev_trading_day_close(rows)
        assert prev == 40.0, f"Expected 40.0, got {prev}"

    def test_single_row_returns_none(self):
        """With only one OHLCV row, no previous trading day exists."""
        rows = _make_ohlcv_rows([50.0])
        prev = BacktestingEngine._prev_trading_day_close(rows)
        assert prev is None, "Single-row OHLCV should return None."

    def test_empty_rows_returns_none(self):
        """Empty OHLCV should return None."""
        prev = BacktestingEngine._prev_trading_day_close([])
        assert prev is None, "Empty OHLCV should return None."


# ---------------------------------------------------------------------------
# Test 4: Previous evaluation-date price is NOT used for circuit breaker
# ---------------------------------------------------------------------------

class TestPrevEvalDateNotUsed:
    """Even if self.prev_prices has a stale evaluation-date price, the
    circuit breaker must use the daily OHLCV previous close instead."""

    def test_stale_prev_prices_ignored_when_ohlcv_available(self):
        """With OHLCV data, prev_prices should be irrelevant."""
        bt = _make_backtester()
        # Simulate: prev evaluation date had price 50.0 (20 days ago)
        bt.prev_prices["TEST.CA"] = 50.0

        # Current price is 65.0 — that's +30% vs prev eval date
        # but only +2% vs yesterday's close of 63.7
        current = 65.0
        ohlcv = _make_ohlcv_rows([55.0, 58.0, 60.0, 63.7, current])

        halted = bt._check_circuit_breaker("TEST.CA", current, ohlcv_rows=ohlcv)
        assert halted is False, (
            "Circuit breaker should compare against previous trading day close "
            "(63.7 → 65.0 = +2.0%), not prev evaluation date (50 → 65 = +30%)."
        )

    def test_no_ohlcv_falls_back_to_no_halt(self):
        """Without OHLCV data, circuit breaker should NOT halt
        (safer than using a stale evaluation-date price)."""
        bt = _make_backtester()
        bt.prev_prices["TEST.CA"] = 50.0
        current = 65.0  # +30% vs stale prev_prices

        halted = bt._check_circuit_breaker("TEST.CA", current, ohlcv_rows=None)
        assert halted is False, (
            "Without OHLCV data, circuit breaker should not halt "
            "(no reliable previous trading day close available)."
        )


# ---------------------------------------------------------------------------
# Test 5: Realistic TMGH.CA scenario — gradual rally should not halt
# ---------------------------------------------------------------------------

class TestRealisticTMGHScenario:
    """Simulate TMGH.CA Jan 2 → Jan 22 with realistic daily data.
    Stock went from ~23.86 to ~31.11 over 20 days — approx +1.3%/day.
    None of the daily moves should exceed 10%.
    """

    def test_tmgh_jan_rally_daily_data(self):
        """With realistic daily closes, no single day exceeds ±10%."""
        bt = _make_backtester()
        # 15 trading days (EGX: Sun-Thu) from Jan 2 to Jan 22
        # Gradual climb with some volatility, max daily move ~4%
        daily_closes = [
            23.86, 24.10, 24.55, 25.00, 25.30,   # Week 1
            25.80, 26.20, 26.50, 27.10, 27.60,   # Week 2
            28.20, 29.00, 29.50, 30.40, 31.11,   # Week 3
        ]
        ohlcv = _make_ohlcv_rows(daily_closes)

        # Evaluation on Jan 22: current_price = 31.11
        # Previous trading day close = 30.40
        # Daily change = (31.11 - 30.40) / 30.40 = 2.3% — should NOT halt
        halted = bt._check_circuit_breaker("TMGH.CA", 31.11, ohlcv_rows=ohlcv)
        assert halted is False, (
            "TMGH.CA Jan 22 should NOT be halted — daily move from 30.40 to 31.11 "
            "is only +2.3%, well under the 10% circuit breaker."
        )

    def test_tmgh_with_true_limit_up_day(self):
        """If one day truly gaps +11%, that single day should halt."""
        bt = _make_backtester()
        daily_closes = [
            23.86, 24.10, 24.55, 25.00, 25.30,
            25.80, 26.20, 26.50, 27.10, 27.60,
            28.20, 29.00, 29.50, 30.40,
            33.74,  # +11% gap on the final day
        ]
        ohlcv = _make_ohlcv_rows(daily_closes)

        halted = bt._check_circuit_breaker("TMGH.CA", 33.74, ohlcv_rows=ohlcv)
        assert halted is True, (
            "A true +11% daily gap (30.40 → 33.74) should trigger circuit breaker."
        )
