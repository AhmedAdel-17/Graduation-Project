"""
Tests for fundamentals coverage report helper functions.

Tests pure logic — no filesystem access, no real CSV files.
"""
from __future__ import annotations

import pytest

from scripts.fundamentals_coverage_report import (
    classify_pe_tier,
    classify_quality,
    count_cbe_provisional,
    count_quarterly_populated_rows,
    _is_populated,
)


# ── _is_populated ───────────────────────────────────────────────────────────


class TestIsPopulated:

    @pytest.mark.parametrize("val", [None, "", "nan", "NaN", "None", "N/A", "--", "-"])
    def test_empty_values(self, val):
        assert _is_populated(val) is False

    @pytest.mark.parametrize("val", ["123.45", "0", "-1.5", "1000000"])
    def test_populated_values(self, val):
        assert _is_populated(val) is True

    def test_inf_not_populated(self):
        assert _is_populated("inf") is False

    def test_non_numeric_string_not_populated(self):
        assert _is_populated("MANUAL_ENTRY_REQUIRED") is False


# ── classify_pe_tier ────────────────────────────────────────────────────────


class TestClassifyPETier:

    def test_none_when_zero_populated(self):
        assert classify_pe_tier(0, 5) == "none"

    def test_none_when_zero_total(self):
        assert classify_pe_tier(0, 0) == "none"

    def test_sparse_below_threshold(self):
        assert classify_pe_tier(1, 5) == "sparse"
        assert classify_pe_tier(3, 5) == "sparse"

    def test_adequate_at_threshold(self):
        assert classify_pe_tier(4, 5) == "adequate"

    def test_adequate_above_threshold(self):
        assert classify_pe_tier(5, 5) == "adequate"


# ── classify_quality ────────────────────────────────────────────────────────


class TestClassifyQuality:

    def test_stub_always_low(self):
        assert classify_quality(
            sector="operational", is_stub=True, annual_depth=5,
            gross_profit_populated=5, bank_fields_populated=0,
            bank_fields_total=0,
        ) == "low"

    def test_operational_high_with_gross_profit(self):
        assert classify_quality(
            sector="operational", is_stub=False, annual_depth=5,
            gross_profit_populated=4, bank_fields_populated=0,
            bank_fields_total=0,
        ) == "high"

    def test_operational_medium_without_gross_profit(self):
        """Non-bank with 4 annual rows but 0 gross_profit → medium, not high."""
        assert classify_quality(
            sector="operational", is_stub=False, annual_depth=4,
            gross_profit_populated=0, bank_fields_populated=0,
            bank_fields_total=0,
        ) == "medium"

    def test_bank_high_without_gross_profit(self):
        """Banks should NOT be penalized for missing gross_profit.
        With 5 annual rows and bank-relevant fields populated → high."""
        assert classify_quality(
            sector="banks", is_stub=False, annual_depth=5,
            gross_profit_populated=0,
            bank_fields_populated=10,  # interest_income + interest_expense + net_income
            bank_fields_total=15,
        ) == "high"

    def test_bank_medium_with_low_depth(self):
        assert classify_quality(
            sector="banks", is_stub=False, annual_depth=3,
            gross_profit_populated=0, bank_fields_populated=5,
            bank_fields_total=9,
        ) == "medium"

    def test_bank_low_no_bank_fields(self):
        """Bank with 5 annual rows but 0 bank-relevant fields → medium (depth >= 2)."""
        assert classify_quality(
            sector="banks", is_stub=False, annual_depth=5,
            gross_profit_populated=0, bank_fields_populated=0,
            bank_fields_total=15,
        ) == "medium"

    def test_bank_low_depth(self):
        assert classify_quality(
            sector="banks", is_stub=False, annual_depth=1,
            gross_profit_populated=0, bank_fields_populated=3,
            bank_fields_total=3,
        ) == "low"

    def test_operational_low_depth(self):
        assert classify_quality(
            sector="operational", is_stub=False, annual_depth=1,
            gross_profit_populated=1, bank_fields_populated=0,
            bank_fields_total=0,
        ) == "low"


# ── count_cbe_provisional ──────────────────────────────────────────────────


class TestCBEProvisional:

    def test_counts_provisional_rows(self):
        rows = [
            {"effective_date": "2020-01-01", "verification_status": "PROVISIONAL"},
            {"effective_date": "2021-01-01", "verification_status": "VERIFIED"},
            {"effective_date": "2022-01-01", "verification_status": "PROVISIONAL - reconstructed"},
        ]
        count, dates = count_cbe_provisional(rows)
        assert count == 2
        assert dates == ["2020-01-01", "2022-01-01"]

    def test_no_provisional(self):
        rows = [
            {"effective_date": "2020-01-01", "verification_status": "VERIFIED"},
        ]
        count, dates = count_cbe_provisional(rows)
        assert count == 0
        assert dates == []

    def test_empty_rows(self):
        count, dates = count_cbe_provisional([])
        assert count == 0
        assert dates == []

    def test_case_insensitive(self):
        rows = [
            {"effective_date": "2020-01-01", "verification_status": "provisional"},
        ]
        count, _ = count_cbe_provisional(rows)
        assert count == 1


# ── count_quarterly_populated_rows ─────────────────────────────────────────


class TestQuarterlyPopulated:

    def test_all_empty_placeholders(self):
        """ESRS-style: rows exist but all fields are empty or MANUAL_ENTRY_REQUIRED."""
        income = [
            {"revenue": "", "net_income": "", "gross_profit": "", "operating_income": ""},
            {"revenue": "nan", "net_income": "NaN", "gross_profit": "", "operating_income": ""},
        ]
        balance = [
            {"total_assets": "", "total_liabilities": "", "total_equity": ""},
            {"total_assets": "", "total_liabilities": "", "total_equity": ""},
        ]
        ratios = [
            {"net_margin": "", "roe": "", "roa": "", "eps": ""},
            {"net_margin": "", "roe": "", "roa": "", "eps": ""},
        ]
        assert count_quarterly_populated_rows(income, balance, ratios) == 0

    def test_some_populated(self):
        income = [
            {"revenue": "1000000", "net_income": "500000", "gross_profit": "", "operating_income": ""},
            {"revenue": "", "net_income": "", "gross_profit": "", "operating_income": ""},
        ]
        balance = [
            {"total_assets": "", "total_liabilities": "", "total_equity": ""},
            {"total_assets": "5000000", "total_liabilities": "2000000", "total_equity": "3000000"},
        ]
        ratios = [[], []]
        assert count_quarterly_populated_rows(income, balance, ratios) == 2

    def test_empty_lists(self):
        assert count_quarterly_populated_rows([], [], []) == 0

    def test_mixed_lengths(self):
        """Lists can have different lengths — check all rows up to max."""
        income = [{"revenue": "100", "net_income": "", "gross_profit": "", "operating_income": ""}]
        balance = []
        ratios = [
            {"net_margin": "", "roe": "", "roa": "", "eps": ""},
            {"net_margin": "0.15", "roe": "", "roa": "", "eps": ""},
        ]
        assert count_quarterly_populated_rows(income, balance, ratios) == 2
