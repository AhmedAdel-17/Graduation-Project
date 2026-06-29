"""
Tests for data_loader.py diagnostics output.

Uses temporary CSV files to validate that load_multi_period() returns
correct diagnostics metadata alongside the existing data contract.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tradingagents.agents.analysts.fundamentals.data_loader import (
    load_multi_period,
    _read_egx_csv_multi,
    _LoadDiagnostics,
)
from tradingagents.dataflows.local import (
    EGX_INCOME_REQUIRED_FIELDS,
    EGX_INCOME_OPTIONAL_FIELDS,
)


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Create a temporary EGX fundamentals directory structure."""
    egx_dir = tmp_path / "egx_fundamentals"
    (egx_dir / "income_statements").mkdir(parents=True)
    (egx_dir / "balance_sheets").mkdir(parents=True)
    (egx_dir / "key_ratios").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def patch_data_dir(tmp_data_dir):
    """Patch DATA_DIR to point at temp directory."""
    with patch("tradingagents.agents.analysts.fundamentals.data_loader.DATA_DIR", str(tmp_data_dir)):
        yield tmp_data_dir


def _write_csv(path: Path, content: str):
    path.write_text(content.strip() + "\n")


# =============================================================================
# Test: File not found
# =============================================================================

class TestFileNotFound:
    def test_missing_file_returns_empty_with_diagnostics(self, patch_data_dir):
        result = load_multi_period("COMI.CA", curr_date="2024-06-01")
        assert result["income"] == []
        assert result["n_income"] == 0
        diag = result["diagnostics"]["income"]
        assert diag["file_exists"] is False
        assert "File not found" in diag["warnings"]
        assert diag["rows_read"] == 0

    def test_all_three_statements_report_missing(self, patch_data_dir):
        result = load_multi_period("XXXX.CA", curr_date="2024-06-01")
        for stmt in ("income", "balance", "ratios"):
            assert result["diagnostics"][stmt]["file_exists"] is False


# =============================================================================
# Test: CSV parse error
# =============================================================================

class TestParseError:
    def test_malformed_csv_reports_parse_error(self, patch_data_dir):
        bad_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "BAD_income_annual.csv"
        # Write binary garbage that pandas can't parse
        bad_path.write_bytes(b"\x00\x01\x02\x03\x80\x81\x82")

        result = load_multi_period("BAD.CA", curr_date="2024-06-01")
        diag = result["diagnostics"]["income"]
        assert diag["file_exists"] is True
        assert diag["parse_error"] is not None
        assert result["income"] == []


# =============================================================================
# Test: Missing period_end_date column
# =============================================================================

class TestMissingPeriodEndDate:
    def test_no_period_end_date_column(self, patch_data_dir):
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "TEST_income_annual.csv"
        _write_csv(csv_path, "revenue,net_income\n1000,200\n900,180")

        result = load_multi_period("TEST.CA", curr_date="2024-06-01")
        diag = result["diagnostics"]["income"]
        assert diag["file_exists"] is True
        assert diag["has_period_end_date"] is False
        assert "Missing 'period_end_date' column" in diag["warnings"]
        assert result["income"] == []


# =============================================================================
# Test: Successful load with full diagnostics
# =============================================================================

class TestSuccessfulLoad:
    def test_full_diagnostics_on_valid_csv(self, patch_data_dir):
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2023-12-31,5000,3000,2000,1500
2022-12-31,4200,2500,1700,1200
2021-12-31,3800,2200,1500,1000
2020-12-31,3500,2000,1300,900""")

        result = load_multi_period("COMI.CA", curr_date="2024-06-01", n_periods=3)
        diag = result["diagnostics"]["income"]

        assert diag["file_exists"] is True
        assert diag["parse_error"] is None
        assert diag["rows_read"] == 4
        assert diag["has_period_end_date"] is True
        assert diag["has_publish_date"] is False
        assert diag["date_filter_method"] == "filing_lag_120d"
        # 2024-06-01 - 120d = 2024-02-02; all 4 rows pass (all <= 2024-02-02)
        assert diag["rows_after_date_filter"] == 4
        assert diag["rows_after_empty_filter"] == 3  # limited by n_periods=3
        assert diag["selected_periods"] == ["2023-12-31", "2022-12-31", "2021-12-31"]
        assert diag["missing_required_fields"] == []
        assert diag["warnings"] == []

    def test_date_filter_excludes_future_periods(self, patch_data_dir):
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2024-12-31,6000,3500,2500,1800
2023-12-31,5000,3000,2000,1500
2022-12-31,4200,2500,1700,1200""")

        # curr_date=2024-06-01 - 120d = 2024-02-02
        # Only 2023-12-31 and 2022-12-31 pass
        result = load_multi_period("COMI.CA", curr_date="2024-06-01")
        diag = result["diagnostics"]["income"]
        assert diag["rows_read"] == 3
        assert diag["rows_after_date_filter"] == 2
        assert "2024-12-31" not in diag["selected_periods"]
        assert "2023-12-31" in diag["selected_periods"]

    def test_publish_date_filter_preferred(self, patch_data_dir):
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path, """\
period_end_date,publish_date,revenue,gross_profit,operating_income,net_income
2023-12-31,2024-04-15,5000,3000,2000,1500
2022-12-31,2023-04-10,4200,2500,1700,1200""")

        # curr_date=2024-03-01 — publish_date 2024-04-15 is AFTER → excluded
        result = load_multi_period("COMI.CA", curr_date="2024-03-01")
        diag = result["diagnostics"]["income"]
        assert diag["date_filter_method"] == "publish_date"
        assert diag["has_publish_date"] is True
        assert diag["rows_after_date_filter"] == 1
        assert diag["selected_periods"] == ["2022-12-31"]

    def test_all_null_publish_date_falls_back_to_filing_lag(self, patch_data_dir):
        """When publish_date column exists but all values are null/blank,
        the loader must fall back to the 120-day filing lag filter, NOT
        incorrectly filter out all rows because 'NaT <= curr_dt' is False.

        This is the exact scenario produced by egx30_full_scraper.py which
        sets publish_date=None (yfinance doesn't provide filing dates)."""
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path, """\
period_end_date,revenue,gross_profit,operating_income,net_income,data_source,scraped_at,publish_date
2023-12-31,5000,3000,2000,1500,yfinance,2024-06-01T10:00:00Z,
2022-12-31,4200,2500,1700,1200,yfinance,2024-06-01T10:00:00Z,""")

        # curr_date=2024-06-01, filing lag 120d -> cutoff 2024-02-02
        # Both 2023-12-31 and 2022-12-31 pass the 120d lag filter
        result = load_multi_period("COMI.CA", curr_date="2024-06-01")
        diag = result["diagnostics"]["income"]

        # Should NOT use publish_date filter (all values are NaT)
        # Should fall back to filing_lag_120d
        assert diag["date_filter_method"] == "filing_lag_120d"
        assert diag["has_publish_date"] is False  # all-null = effectively absent
        assert diag["rows_after_date_filter"] == 2
        assert len(result["income"]) == 2
        assert result["income"][0]["revenue"] == 5000.0

    def test_no_curr_date_skips_filter(self, patch_data_dir):
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2025-12-31,7000,4000,3000,2000
2024-12-31,6000,3500,2500,1800""")

        result = load_multi_period("COMI.CA", curr_date=None)
        diag = result["diagnostics"]["income"]
        assert diag["date_filter_method"] == "none"
        assert diag["rows_after_date_filter"] == 2
        assert len(result["income"]) == 2


# =============================================================================
# Test: Missing required fields
# =============================================================================

class TestMissingRequiredFields:
    def test_missing_required_columns_reported(self, patch_data_dir):
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        # Missing gross_profit and operating_income
        _write_csv(csv_path, """\
period_end_date,revenue,net_income
2023-12-31,5000,1500""")

        result = load_multi_period("COMI.CA", curr_date="2024-06-01")
        diag = result["diagnostics"]["income"]
        assert "gross_profit" in diag["missing_required_fields"]
        assert "operating_income" in diag["missing_required_fields"]
        # Data still loads — missing fields become None in the row
        assert len(result["income"]) == 1
        assert result["income"][0]["gross_profit"] is None
        assert result["income"][0]["revenue"] == 5000.0


# =============================================================================
# Test: Empty data rows filtered
# =============================================================================

class TestEmptyRowFilter:
    def test_all_none_rows_dropped_with_warning(self, patch_data_dir):
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2023-12-31,,,,
2022-12-31,4200,2500,1700,1200""")

        result = load_multi_period("COMI.CA", curr_date="2024-06-01")
        diag = result["diagnostics"]["income"]
        assert diag["rows_after_date_filter"] == 2
        assert diag["rows_after_empty_filter"] == 1
        assert result["income"][0]["revenue"] == 4200.0


# =============================================================================
# Test: Quarterly frequency
# =============================================================================

class TestQuarterlyFrequency:
    def test_quarterly_uses_45_day_lag(self, patch_data_dir):
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_quarterly.csv"
        _write_csv(csv_path, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2024-03-31,1500,900,600,400
2023-12-31,1400,850,580,390
2023-09-30,1300,800,550,370""")

        # curr_date=2024-06-01 - 45d = 2024-04-17; all rows pass
        result = load_multi_period("COMI.CA", curr_date="2024-06-01", freq="quarterly")
        diag = result["diagnostics"]["income"]
        assert diag["date_filter_method"] == "filing_lag_45d"
        assert diag["rows_after_date_filter"] == 3


# =============================================================================
# Test: Backward compatibility — existing keys unchanged
# =============================================================================

class TestBackwardCompat:
    def test_existing_keys_present(self, patch_data_dir):
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2023-12-31,5000,3000,2000,1500""")

        result = load_multi_period("COMI.CA", curr_date="2024-06-01")
        # All original keys still present
        assert "ticker" in result
        assert "income" in result
        assert "balance" in result
        assert "ratios" in result
        assert "n_income" in result
        assert "n_balance" in result
        assert "n_ratios" in result
        # New keys
        assert "diagnostics" in result
        assert "frequency" in result
        assert result["frequency"] == "annual"
        assert result["ticker"] == "COMI"

    def test_diagnostics_does_not_affect_data_values(self, patch_data_dir):
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2023-12-31,5000,3000,2000,1500""")

        result = load_multi_period("COMI.CA", curr_date="2024-06-01")
        row = result["income"][0]
        assert row["revenue"] == 5000.0
        assert row["net_income"] == 1500.0
        assert row["_period_end_date"] == "2023-12-31"


# =============================================================================
# Test: schema_valid field
# =============================================================================

class TestSchemaValid:
    def test_valid_csv_has_schema_valid_true(self, patch_data_dir):
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2023-12-31,5000,3000,2000,1500""")

        result = load_multi_period("COMI.CA", curr_date="2024-06-01")
        assert result["diagnostics"]["income"]["schema_valid"] is True

    def test_missing_file_has_schema_valid_false(self, patch_data_dir):
        result = load_multi_period("MISSING.CA", curr_date="2024-06-01")
        assert result["diagnostics"]["income"]["schema_valid"] is False

    def test_parse_error_has_schema_valid_false(self, patch_data_dir):
        bad_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "BAD_income_annual.csv"
        bad_path.write_bytes(b"\x00\x01\x02\x03\x80\x81\x82")

        result = load_multi_period("BAD.CA", curr_date="2024-06-01")
        assert result["diagnostics"]["income"]["schema_valid"] is False

    def test_missing_period_end_date_has_schema_valid_false(self, patch_data_dir):
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "TEST_income_annual.csv"
        _write_csv(csv_path, "revenue,net_income\n1000,200")

        result = load_multi_period("TEST.CA", curr_date="2024-06-01")
        assert result["diagnostics"]["income"]["schema_valid"] is False

    def test_missing_required_columns_has_schema_valid_false(self, patch_data_dir):
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        # Has period_end_date but missing gross_profit and operating_income
        _write_csv(csv_path, """\
period_end_date,revenue,net_income
2023-12-31,5000,1500""")

        result = load_multi_period("COMI.CA", curr_date="2024-06-01")
        diag = result["diagnostics"]["income"]
        assert diag["schema_valid"] is False
        assert "gross_profit" in diag["missing_required_fields"]

    def test_ratios_only_needs_period_end_date(self, patch_data_dir):
        """Ratios required fields = [period_end_date] only, so any ratio CSV with
        that column should be schema_valid=True."""
        csv_path = patch_data_dir / "egx_fundamentals" / "key_ratios" / "COMI_ratios.csv"
        _write_csv(csv_path, """\
period_end_date,pe_ratio,roe
2023-12-31,7.2,0.25""")

        result = load_multi_period("COMI.CA", curr_date="2024-06-01")
        assert result["diagnostics"]["ratios"]["schema_valid"] is True


# =============================================================================
# Test: Numeric parse warnings
# =============================================================================

class TestNumericParseWarnings:
    def test_malformed_numeric_becomes_none_with_warning(self, patch_data_dir):
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2023-12-31,INVALID_TEXT,3000,2000,1500""")

        result = load_multi_period("COMI.CA", curr_date="2024-06-01")
        row = result["income"][0]
        # The malformed value becomes None
        assert row["revenue"] is None
        # But a warning is recorded
        diag = result["diagnostics"]["income"]
        assert len(diag["numeric_parse_warnings"]) == 1
        assert "revenue" in diag["numeric_parse_warnings"][0]
        assert "INVALID_TEXT" in diag["numeric_parse_warnings"][0]
        assert "2023-12-31" in diag["numeric_parse_warnings"][0]

    def test_multiple_malformed_fields_produce_multiple_warnings(self, patch_data_dir):
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2023-12-31,abc,xyz,2000,1500""")

        result = load_multi_period("COMI.CA", curr_date="2024-06-01")
        diag = result["diagnostics"]["income"]
        assert len(diag["numeric_parse_warnings"]) == 2
        fields_warned = [w for w in diag["numeric_parse_warnings"]]
        assert any("revenue" in w for w in fields_warned)
        assert any("gross_profit" in w for w in fields_warned)

    def test_empty_and_dash_do_not_produce_warnings(self, patch_data_dir):
        """Empty strings, '-', and 'N/A' are expected missing indicators, not parse failures."""
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2023-12-31,5000,-,N/A,1500""")

        result = load_multi_period("COMI.CA", curr_date="2024-06-01")
        diag = result["diagnostics"]["income"]
        assert diag["numeric_parse_warnings"] == []

    def test_valid_numeric_formats_do_not_produce_warnings(self, patch_data_dir):
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2023-12-31,"5,000",3000.5,(200),1500""")

        result = load_multi_period("COMI.CA", curr_date="2024-06-01")
        diag = result["diagnostics"]["income"]
        assert diag["numeric_parse_warnings"] == []
        # Verify parsing worked correctly
        row = result["income"][0]
        assert row["revenue"] == 5000.0
        assert row["gross_profit"] == 3000.5
        assert row["operating_income"] == -200.0


# =============================================================================
# Test: Diagnostics surfaced in deterministic fundamentals output
# =============================================================================

class TestDiagnosticsPropagation:
    """Verify that data_loader_diagnostics flows through to fundamentals_analyst output."""

    def test_deterministic_analyst_includes_diagnostics(self, patch_data_dir):
        """structured_analysis must contain data_loader_diagnostics key."""
        # Write minimal valid CSVs so the analyst can run
        income_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        balance_path = patch_data_dir / "egx_fundamentals" / "balance_sheets" / "COMI_balance_annual.csv"
        ratios_path = patch_data_dir / "egx_fundamentals" / "key_ratios" / "COMI_ratios.csv"

        _write_csv(income_path, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2023-12-31,5000,3000,2000,1500
2022-12-31,4200,2500,1700,1200""")

        _write_csv(balance_path, """\
period_end_date,total_assets,total_liabilities,total_equity,current_assets,current_liabilities
2023-12-31,50000,30000,20000,15000,10000
2022-12-31,45000,27000,18000,14000,9000""")

        _write_csv(ratios_path, """\
period_end_date,pe_ratio,roe,eps
2023-12-31,7.2,0.25,13.2""")

        from tradingagents.agents.analysts.fundamentals_analyst import (
            create_deterministic_fundamentals_analyst,
        )

        analyst_node = create_deterministic_fundamentals_analyst()
        state = {
            "trade_date": "2024-06-01",
            "company_of_interest": "COMI.CA",
            "current_price": 0,
        }
        result = analyst_node(state)

        structured = result["fundamental_analysis"]
        assert "data_loader_diagnostics" in structured
        diag = structured["data_loader_diagnostics"]
        assert "income" in diag
        assert "balance" in diag
        assert "ratios" in diag
        # Income loaded successfully
        assert diag["income"]["file_exists"] is True
        assert diag["income"]["schema_valid"] is True
        assert diag["income"]["rows_read"] == 2
