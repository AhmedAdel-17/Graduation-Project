"""Layer-0 EGX relevance classifier.

Two-signal gate: a post passes only if it contains BOTH a finance/trading
term AND an EGX-specific term (ticker, Arabic issuer alias, or market term).
Facebook posts from confirmed EGX groups bypass this in pipeline.py because
the group itself is the EGX signal.
"""

from __future__ import annotations

import re
from typing import Tuple

from tradingagents.utils.text_preprocessor import normalize_text

from .entities import (
    SYMBOL_REGISTRY,
    has_finance_context,
    has_market_term,
    CASHTAG_RX,
    DOTCA_RX,
)

# Compile alias lookup once
_EN_ALIASES: set[str] = set()
_AR_ALIASES: set[str] = set()
for _sym, _meta in SYMBOL_REGISTRY.items():
    _EN_ALIASES |= _meta["en"]
    _AR_ALIASES |= _meta["ar"]


def _has_ticker_mention(raw: str, normalized: str) -> bool:
    if CASHTAG_RX.search(raw) or DOTCA_RX.search(raw):
        return True
    for alias in _EN_ALIASES:
        if alias in normalized:
            return True
    for alias in _AR_ALIASES:
        if alias in normalized:
            return True
    return False


def classify(text: str) -> Tuple[bool, dict]:
    """Return (is_relevant, debug_info)."""
    if not text or len(text.strip()) < 10:
        return False, {"reason": "too-short"}
    normalized = normalize_text(text).lower()
    fin = has_finance_context(normalized)
    market = has_market_term(normalized)
    ticker = _has_ticker_mention(text, normalized)
    relevant = fin and (market or ticker)
    return relevant, {
        "finance_context": fin,
        "market_term": market,
        "ticker_mention": ticker,
    }
