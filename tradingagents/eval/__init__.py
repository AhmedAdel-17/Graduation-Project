"""Evaluation utilities: deterministic baselines and survivorship-safe universe.

These exist to answer the two questions a backtest number alone can't:
  1. Does the LLM system beat a trivial deterministic rule net of costs?
     (``baselines``)
  2. Is the evaluation universe survivor-biased? (``universe``)
"""
from tradingagents.eval.baselines import (  # noqa: F401
    STRATEGIES,
    baseline_decision,
    buy_and_hold_decision,
    momentum_decision,
    sma_crossover_decision,
)
from tradingagents.eval.universe import (  # noqa: F401
    Membership,
    load_membership,
    members_as_of,
    is_active,
    current_universe,
)
