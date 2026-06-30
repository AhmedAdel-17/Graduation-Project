"""
EGX Ticker Symbol Normalization Utilities.

Single source of truth for ticker string normalization across the project.
All code that transforms ticker strings should use these functions instead
of inline `.upper().replace(".CA", "").strip()` chains.

Two canonical forms:
  - bare:    "COMI"     (internal storage, CSV filenames, manifest keys)
  - suffixed: "COMI.CA" (yfinance API, display, user-facing)
"""
from __future__ import annotations


def normalize_egx_ticker(ticker: str) -> str:
    """Normalize an EGX ticker to its bare canonical form.

    Accepts any combination of case, whitespace, and .CA suffix:
      "comi.CA" -> "COMI"
      " East.ca " -> "EAST"
      "TMGH" -> "TMGH"
      "  comi  " -> "COMI"

    This is the standard normalization for:
      - CSV filenames and data lookups
      - Manifest keys and freshness checks
      - Internal state and cache keys
      - Memory/reflection ticker fields
    """
    return ticker.upper().replace(".CA", "").strip()


def ensure_ca_suffix(ticker: str) -> str:
    """Ensure a ticker has the .CA suffix (yfinance convention).

    Accepts any form and returns uppercase with .CA:
      "comi" -> "COMI.CA"
      "COMI.CA" -> "COMI.CA"
      " east.ca " -> "EAST.CA"

    This is the standard normalization for:
      - yfinance API calls
      - User-facing display (CLI, dashboard, reports)
      - EGX_TICKERS config list format
    """
    bare = normalize_egx_ticker(ticker)
    return f"{bare}.CA"
