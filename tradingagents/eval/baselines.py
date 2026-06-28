"""Deterministic baseline strategies to measure the LLM system against.

A multi-agent LLM stack is only worth its cost and latency if it beats a trivial
rule. These strategies take point-in-time closes (up to and INCLUDING the run date —
never future bars) and emit the same BUY/HOLD vocabulary the paper-trading harness
scores, so "llm" vs "momentum" is apples-to-apples on the same realized prices, net
of the same EGX costs.

EGX is long-only, so the only deterministic actions are BUY (take the position) or
HOLD (stay in cash). None of these look at anything after ``closes[-1]``.
"""
from __future__ import annotations

from typing import Callable, Dict, Sequence

# Trading-day lookbacks (EGX daily bars).
DEFAULT_MOMENTUM_LOOKBACK = 63   # ~3 months
DEFAULT_SMA_FAST = 20
DEFAULT_SMA_SLOW = 50


def buy_and_hold_decision(closes: Sequence[float]) -> str:
    """Always BUY — the market-exposure baseline. Beating this means the system
    adds value over simply being long."""
    return "BUY" if closes else "HOLD"


def momentum_decision(
    closes: Sequence[float],
    lookback: int = DEFAULT_MOMENTUM_LOOKBACK,
) -> str:
    """BUY if trailing ``lookback``-day return is positive, else HOLD.

    The single most important comparator: cross-sectional/time-series momentum is the
    most robust documented equity anomaly. If the LLM can't beat this one-liner net
    of costs, it isn't generating alpha.
    """
    if not closes or len(closes) < lookback + 1:
        return "HOLD"
    past = closes[-1 - lookback]
    if past <= 0:
        return "HOLD"
    return "BUY" if (closes[-1] - past) / past > 0 else "HOLD"


def sma_crossover_decision(
    closes: Sequence[float],
    fast: int = DEFAULT_SMA_FAST,
    slow: int = DEFAULT_SMA_SLOW,
) -> str:
    """BUY when the fast SMA is above the slow SMA (trend-following), else HOLD."""
    if not closes or len(closes) < slow:
        return "HOLD"
    fast_sma = sum(closes[-fast:]) / fast
    slow_sma = sum(closes[-slow:]) / slow
    return "BUY" if fast_sma > slow_sma else "HOLD"


# Registry so callers (CLI, harness) can iterate baselines by name.
STRATEGIES: Dict[str, Callable[[Sequence[float]], str]] = {
    "buy_and_hold": buy_and_hold_decision,
    "momentum": momentum_decision,
    "sma_crossover": sma_crossover_decision,
}


def baseline_decision(strategy: str, closes: Sequence[float]) -> str:
    """Dispatch to a named baseline. Unknown strategy ⇒ HOLD (fail-safe)."""
    fn = STRATEGIES.get(strategy)
    return fn(closes) if fn else "HOLD"
