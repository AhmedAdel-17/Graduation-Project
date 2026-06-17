"""EGX index membership helpers for the layered sentiment aggregator.

The sentiment engine produces a sentiment signal *per index* (EGX30 / EGX70 /
EGX100) — the market-level view that is far more data-rich than any single
thinly-discussed stock. Two paths feed an index bucket:

1. **Ticker mention roll-up.** A post mentioning COMI contributes to EGX30 and
   EGX100 (COMI is a blue chip); a post mentioning a mid-cap contributes to
   EGX70 and EGX100. Membership is sourced from the canonical taxonomy
   (``tradingagents.sentiment.taxonomy``) so there is exactly one source of
   truth, overridable via the ``EGX30_CONSTITUENTS`` / ``EGX70_CONSTITUENTS``
   env vars.
2. **Direct index-term mention.** A post that names an index outright
   ("EGX30 broke resistance", "المؤشر الثلاثيني") is detected as an ``EGX_30`` /
   ``EGX_70`` / ``EGX_100`` pseudo-symbol by ``entities.extract`` and routed
   straight into that index bucket.

Kept deterministic and dependency-light, consistent with ``sectors.py``.
"""

from __future__ import annotations

from typing import Dict, List

from tradingagents.sentiment.taxonomy import IndexEnum, ticker_to_indices

# Canonical index codes used as bucket keys in the aggregator output.
INDICES = ("EGX30", "EGX70", "EGX100")

# Map the EGX_* pseudo-symbols emitted by entities.MARKET_INDEX_TERMS to the
# canonical index codes. EGX_BROAD is intentionally absent — it represents the
# whole-market mood and is handled by the EGX_MARKET bucket, not an index.
_PSEUDO_SYMBOL_TO_INDEX: Dict[str, str] = {
    "EGX_30": "EGX30",
    "EGX_70": "EGX70",
    "EGX_100": "EGX100",
}


def index_pseudo_symbol_to_code(symbol: str) -> str | None:
    """Translate an ``EGX_30``/``EGX_70``/``EGX_100`` mention symbol to a code."""
    return _PSEUDO_SYMBOL_TO_INDEX.get(symbol.upper())


def indices_from_ticker(ticker: str) -> List[str]:
    """Return the index codes a single ticker rolls up into (may be empty)."""
    return [idx.value for idx in _sorted_indices(ticker_to_indices(ticker))]


def indices_from_ticker_mentions(ticker_symbols: List[str]) -> Dict[str, float]:
    """Roll up ticker mentions into index confidences.

    A confirmed ticker mention is a strong index signal, so confidence is fixed
    at 0.9 (same convention as ``sectors.sectors_from_ticker_mentions``). Returns
    ``{index_code: max_confidence}``.
    """
    out: Dict[str, float] = {}
    for sym in ticker_symbols:
        for idx in ticker_to_indices(sym):
            out[idx.value] = max(out.get(idx.value, 0.0), 0.9)
    return out


def _sorted_indices(indices) -> List[IndexEnum]:
    # Stable, specific-first ordering: EGX30, EGX70, EGX100.
    order = {IndexEnum.EGX30: 0, IndexEnum.EGX70: 1, IndexEnum.EGX100: 2}
    return sorted(indices, key=lambda i: order.get(i, 9))
