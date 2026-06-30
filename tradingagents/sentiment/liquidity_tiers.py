"""Liquidity-tier assignment for stock-level sentiment thresholds.

PROVISIONAL membership — the MEGA / MID seed lists are engineering judgment
based on EGX-30 liquidity headlines. They will be replaced by output of the
calibration script (PR 10) which uses 30-day rolling ADV, average discussion
volume, and source diversity. Until then, unknown tickers default to SMALL
(most conservative gate — fewest false signals).
"""
from __future__ import annotations

from enum import Enum


class LiquidityTier(str, Enum):
    MEGA = "MEGA"
    MID = "MID"
    SMALL = "SMALL"


# PROVISIONAL — top names by ADV / market cap / discussion frequency.
# Calibrate from rolling 30-day window before locking.
_MEGA: frozenset[str] = frozenset({"COMI", "TMGH", "FWRY", "ETEL", "HRHO"})

# PROVISIONAL — remainder of EGX-30 / EGX-70 universe per default_config.EGX_TICKERS.
_MID: frozenset[str] = frozenset(
    {
        "ADIB", "CIEB", "EXPA", "HDBK", "QNBA", "SAUD",
        "HELI", "PHDC", "OCDI", "ORAS", "EMFD",
        "EAST", "SWDY", "ABUK", "MFPC", "EGAL", "EGCH", "EFIC",
        "EFIH", "RAYA",
        "BTFH", "CICH",
        "JUFO", "EFID", "DOMT",
    }
)


def _normalize(ticker: str) -> str:
    t = ticker.strip().upper()
    if t.endswith(".CA"):
        t = t[:-3]
    return t


def tier_for(ticker: str) -> LiquidityTier:
    """Return the liquidity tier for a ticker. Unknown → SMALL (conservative)."""
    t = _normalize(ticker)
    if t in _MEGA:
        return LiquidityTier.MEGA
    if t in _MID:
        return LiquidityTier.MID
    return LiquidityTier.SMALL
