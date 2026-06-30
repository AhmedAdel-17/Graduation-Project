"""
Tests for tradingagents.dataflows.symbol_utils.

Covers:
  - normalize_egx_ticker: case, suffix, whitespace handling
  - ensure_ca_suffix: adds .CA when missing, preserves when present
  - Edge cases: empty string, double suffix, mixed case suffix
  - Round-trip: normalize -> ensure_ca_suffix is idempotent
"""
from __future__ import annotations

import pytest

from tradingagents.dataflows.symbol_utils import (
    normalize_egx_ticker,
    ensure_ca_suffix,
)


class TestNormalizeEgxTicker:
    """normalize_egx_ticker must always return bare uppercase symbol."""

    @pytest.mark.parametrize("input_val,expected", [
        ("COMI", "COMI"),
        ("comi", "COMI"),
        ("COMI.CA", "COMI"),
        ("comi.CA", "COMI"),
        ("comi.ca", "COMI"),
        ("Comi.Ca", "COMI"),
        (" COMI ", "COMI"),
        (" comi.CA ", "COMI"),
        ("  EAST.ca  ", "EAST"),
        ("TMGH", "TMGH"),
    ])
    def test_standard_cases(self, input_val, expected):
        assert normalize_egx_ticker(input_val) == expected

    def test_empty_string(self):
        assert normalize_egx_ticker("") == ""

    def test_whitespace_only(self):
        assert normalize_egx_ticker("   ") == ""

    def test_no_double_removal(self):
        """Should not produce weird results for tickers containing 'CA'."""
        # CCAP contains "CA" but only ".CA" suffix should be removed
        assert normalize_egx_ticker("CCAP.CA") == "CCAP"
        assert normalize_egx_ticker("CCAP") == "CCAP"

    def test_idempotent(self):
        """Normalizing an already-normalized ticker changes nothing."""
        assert normalize_egx_ticker("COMI") == "COMI"
        assert normalize_egx_ticker(normalize_egx_ticker("comi.CA")) == "COMI"


class TestEnsureCaSuffix:
    """ensure_ca_suffix must always return TICKER.CA format."""

    @pytest.mark.parametrize("input_val,expected", [
        ("COMI", "COMI.CA"),
        ("comi", "COMI.CA"),
        ("COMI.CA", "COMI.CA"),
        ("comi.ca", "COMI.CA"),
        (" east.CA ", "EAST.CA"),
        ("TMGH", "TMGH.CA"),
    ])
    def test_standard_cases(self, input_val, expected):
        assert ensure_ca_suffix(input_val) == expected

    def test_empty_string(self):
        assert ensure_ca_suffix("") == ".CA"

    def test_idempotent(self):
        """Applying ensure_ca_suffix twice gives the same result."""
        result = ensure_ca_suffix("comi")
        assert ensure_ca_suffix(result) == "COMI.CA"


class TestRoundTrip:
    """normalize -> ensure_ca_suffix and vice versa are consistent."""

    @pytest.mark.parametrize("ticker", [
        "COMI.CA", "comi", "EAST.ca", " TMGH ", "HELI.CA",
    ])
    def test_normalize_then_suffix(self, ticker):
        bare = normalize_egx_ticker(ticker)
        suffixed = ensure_ca_suffix(bare)
        # Must always produce BARE.CA
        assert suffixed == f"{bare}.CA"
        # Re-normalizing the suffixed form returns the bare
        assert normalize_egx_ticker(suffixed) == bare
