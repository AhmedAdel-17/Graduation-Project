"""Tests for the fundamentals→factor adapter (remediation Track B, Phase 4b).

The CSV-loading path is integration-y (needs real data), so the deterministic unit
tests target the pure mapping + the fail-open contract. A light integration smoke
exercises the real loader but tolerates absent data.
"""
import pytest

from tradingagents.factors.fundamentals_adapter import (
    ratios_to_factor_fields,
    fundamentals_for_factor,
    build_factor_inputs,
)
from tradingagents.factors.core import FactorInputs


def test_mapping_book_to_price_is_pb_reciprocal():
    f = ratios_to_factor_fields({"pb_ratio": 4.0, "earnings_yield": 0.08,
                                 "roe": 0.12, "net_margin": 0.2, "debt_to_equity": 1.5})
    assert f["book_to_price"] == pytest.approx(0.25)
    assert f["earnings_yield"] == 0.08
    assert f["roe"] == 0.12
    assert f["net_margin"] == 0.2
    assert f["debt_to_equity"] == 1.5


def test_mapping_handles_missing_and_bad_pb():
    f = ratios_to_factor_fields({"pb_ratio": 0.0, "roe": None})
    assert f["book_to_price"] is None      # pb<=0 → undefined
    assert f["roe"] is None
    f2 = ratios_to_factor_fields({"pb_ratio": "n/a", "earnings_yield": "x"})
    assert f2["book_to_price"] is None
    assert f2["earnings_yield"] is None


def test_mapping_on_non_dict_returns_all_none():
    f = ratios_to_factor_fields(None)
    assert set(f) == {"earnings_yield", "book_to_price", "roe", "net_margin", "debt_to_equity"}
    assert all(v is None for v in f.values())


def test_fundamentals_fail_open_on_unknown_ticker():
    # A ticker with no CSV data must return all-None, never raise.
    f = fundamentals_for_factor("ZZZZ_NOT_A_TICKER.CA", "2024-01-01", current_price=10.0)
    assert all(v is None for v in f.values())


def test_build_factor_inputs_returns_factorinputs_with_closes():
    fi = build_factor_inputs("ZZZZ_NOT_A_TICKER.CA", [10.0, 11.0, 12.0], "2024-01-01")
    assert isinstance(fi, FactorInputs)
    assert fi.ticker == "ZZZZ_NOT_A_TICKER.CA"
    assert list(fi.closes) == [10.0, 11.0, 12.0]
    # unknown ticker → fundamentals neutral, but price series intact
    assert fi.earnings_yield is None


def test_build_factor_inputs_smoke_on_real_universe():
    # Light integration: a known EGX ticker. Tolerates absent CSVs (returns neutral).
    fi = build_factor_inputs("COMI.CA", [50.0] * 300, "2024-06-01")
    assert isinstance(fi, FactorInputs)
    # Whatever the data state, fields are float-or-None (never crash, never str).
    for v in (fi.earnings_yield, fi.book_to_price, fi.roe, fi.net_margin, fi.debt_to_equity):
        assert v is None or isinstance(v, float)
