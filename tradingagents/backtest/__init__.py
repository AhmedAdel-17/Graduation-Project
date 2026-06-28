"""Backtest evaluation helpers.

This package holds *post-hoc*, leak-safe evaluation utilities that turn a
backtest's recorded decisions into thesis-grade quality metrics. Nothing in
here is consulted on the live decision path — it is reporting only.
"""

from tradingagents.backtest.decision_metrics import (  # noqa: F401
    compute_decision_quality,
    forward_returns_for_decisions,
    per_decision_detail,
)
