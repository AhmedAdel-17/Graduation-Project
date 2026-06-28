"""
EGX trading-cost model — single source of truth.

This module centralises the Egyptian Exchange (EGX) round-trip cost stack so that
*every* consumer agrees on what a trade costs:

  - ``scripts/backtester.py``  — execution-time fills (slippage + commission)
  - ``tradingagents/rl/dataset.py`` — reward shaping (transaction-cost penalty)
  - decision logic (remediation Phase 1) — cost-aware BUY/SELL gating, so a thesis
    whose expected move does not clear the round-trip cost is not traded

Before this module the constants lived only in the backtester, and the RL dataset
kept a *separate* hardcoded copy (``DEFAULT_TX_COST_PCT``). Drift between the two
silently corrupted reward shaping. Import from here instead.

Cost stack (per EGX fee schedule + FRA regulatory levies):
  - Brokerage : 0.175% per side
  - Stamp duty: 0.005% per side
  - FRA levy  : 0.009% per side
  => 0.189% commission per side, 0.378% commission round-trip.

Slippage is modelled separately because it is liquidity-dependent (wider effective
spread on thin names) and is an execution artefact, not a statutory fee.
"""
from __future__ import annotations

import logging
from typing import Tuple

logger = logging.getLogger("tradingagents.egx_costs")

# ── Statutory / brokerage commission (per side) ──────────────────────────────
EGX_BROKERAGE_RATE: float = 0.00175   # 0.175% institutional brokerage fee
EGX_STAMP_DUTY: float = 0.00005       # 0.005% stamp duty (both sides)
EGX_FRA_FEE: float = 0.00009          # 0.009% FRA regulatory levy
EGX_TOTAL_COST_SIDE: float = EGX_BROKERAGE_RATE + EGX_STAMP_DUTY + EGX_FRA_FEE  # ~0.189%/side

# Commission-only round-trip (entry + exit). Matches the legacy RL dataset value
# (0.00378) so reward shaping is unchanged by the consolidation.
ROUND_TRIP_COMMISSION_PCT: float = 2.0 * EGX_TOTAL_COST_SIDE  # ~0.378%

# ── Slippage (per side, liquidity-dependent) ─────────────────────────────────
EGX_SLIPPAGE_NORMAL: float = 0.001    # 0.1%  — normal-liquidity stocks
EGX_SLIPPAGE_LOW_LIQ: float = 0.005   # 0.5%  — low-liquidity stocks (wider spreads)

# ── Market-structure facts (re-exported for convenience) ─────────────────────
EGX_CIRCUIT_BREAKER: float = 0.10     # ±10% daily price move halts trading
EGX_SETTLEMENT_DAYS: int = 2          # T+2 settlement


def slippage_pct(low_liquidity: bool = False) -> float:
    """Per-side slippage fraction for the given liquidity bucket."""
    return EGX_SLIPPAGE_LOW_LIQ if low_liquidity else EGX_SLIPPAGE_NORMAL


def round_trip_cost_pct(low_liquidity: bool = False) -> float:
    """Total expected round-trip cost as a fraction of notional (entry + exit).

    Includes BOTH commission and slippage on each side. This is the number the
    *decision* logic should compare an expected move against: a BUY whose
    expected upside does not clear this hurdle is destroying value on costs alone.

    Example: a normal-liquidity name costs ~0.578% round-trip
    (0.378% commission + 2×0.1% slippage); a low-liquidity name ~1.378%.
    """
    return ROUND_TRIP_COMMISSION_PCT + 2.0 * slippage_pct(low_liquidity)


def apply_execution_costs(
    action: str,
    shares: float,
    close_price: float,
    low_liquidity: bool = False,
) -> Tuple[float, float]:
    """Apply slippage + commission to produce a realistic fill.

    Slippage works against the trader on both sides:
      BUY  → exec_price = close_price * (1 + slippage)   (pays more)
      SELL → exec_price = close_price * (1 - slippage)   (receives less)

    Commission is charged on the executed value (not the close price).

    Returns ``(exec_price, total_commission_egp)``.
    """
    slip = slippage_pct(low_liquidity)
    if str(action).upper() == "BUY":
        exec_price = close_price * (1.0 + slip)
    else:
        exec_price = close_price * (1.0 - slip)
    total_commission = shares * exec_price * EGX_TOTAL_COST_SIDE
    return exec_price, total_commission
