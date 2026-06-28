"""Canonical EGX ticker normalization.

Single source of truth for the ``.CA``-suffix logic that was previously duplicated
(with subtly different rules) across ``gateway.py`` and ``y_finance.py`` — see
MEMORY.md §H. ``eodhd.py`` deliberately keeps its own parametrized exchange-code
logic because it supports non-CA exchanges; this helper is the EGX/.CA-specific
normalizer used by the Yahoo path and the ``DataGateway``.
"""

from __future__ import annotations

EGX_SUFFIX = ".CA"


def normalize_egx_ticker(symbol: str) -> str:
    """Return the EGX ticker upper-cased, stripped, with a single ``.CA`` suffix.

    Behaviour matches the prior inline logic in ``gateway._normalize_symbol`` and
    ``y_finance.get_YFin_data_online`` exactly::

        "comi"      -> "COMI.CA"
        "COMI"      -> "COMI.CA"
        " comi.ca " -> "COMI.CA"
        "COMI.CA"   -> "COMI.CA"
    """
    symbol = symbol.upper().strip()
    if not symbol.endswith(EGX_SUFFIX):
        symbol += EGX_SUFFIX
    return symbol
