"""Tests for deterministic baseline strategies (remediation Track A, Phase 0)."""
from tradingagents.eval.baselines import (
    STRATEGIES,
    baseline_decision,
    buy_and_hold_decision,
    momentum_decision,
    sma_crossover_decision,
)


def test_buy_and_hold_always_buys_when_data_present():
    assert buy_and_hold_decision([10.0, 11.0]) == "BUY"
    assert buy_and_hold_decision([]) == "HOLD"


def test_momentum_buys_on_uptrend():
    closes = [float(x) for x in range(1, 100)]  # strictly rising
    assert momentum_decision(closes, lookback=63) == "BUY"


def test_momentum_holds_on_downtrend():
    closes = [float(x) for x in range(100, 1, -1)]  # strictly falling
    assert momentum_decision(closes, lookback=63) == "HOLD"


def test_momentum_holds_on_insufficient_history():
    assert momentum_decision([1.0, 2.0, 3.0], lookback=63) == "HOLD"


def test_momentum_uses_only_past_bars_no_lookahead():
    # The decision must depend only on closes[-1] vs closes[-1-lookback];
    # appending a future bar would change the answer — so we never feed one.
    closes = [10.0] * 64
    closes[-1] = 12.0  # last (current) bar up vs the bar 63 back (10.0)
    assert momentum_decision(closes, lookback=63) == "BUY"


def test_sma_crossover():
    rising = [float(x) for x in range(1, 100)]
    assert sma_crossover_decision(rising, fast=20, slow=50) == "BUY"
    falling = [float(x) for x in range(100, 1, -1)]
    assert sma_crossover_decision(falling, fast=20, slow=50) == "HOLD"
    assert sma_crossover_decision([1.0, 2.0], fast=20, slow=50) == "HOLD"


def test_registry_and_dispatch():
    assert set(STRATEGIES) == {"buy_and_hold", "momentum", "sma_crossover"}
    rising = [float(x) for x in range(1, 100)]
    assert baseline_decision("momentum", rising) == "BUY"
    assert baseline_decision("unknown_strategy", rising) == "HOLD"  # fail-safe
