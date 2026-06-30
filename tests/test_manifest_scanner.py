"""
Tests for the EGX Fundamentals Manifest Scanner.

Uses temporary CSV files — no network calls, no live data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from tradingagents.dataflows.manifest_scanner import (
    scan_single_file,
    scan_fundamentals,
    write_manifest,
    check_manifest_freshness,
    FreshnessResult,
    ANNUAL_FILING_LAG_DAYS,
)


@pytest.fixture
def tmp_egx(tmp_path):
    """Create a minimal EGX fundamentals directory with test CSVs."""
    egx = tmp_path / "egx_fundamentals"
    (egx / "income_statements").mkdir(parents=True)
    (egx / "balance_sheets").mkdir(parents=True)
    (egx / "key_ratios").mkdir(parents=True)
    return tmp_path


def _write(path: Path, content: str):
    path.write_text(content.strip() + "\n")


# =============================================================================
# scan_single_file
# =============================================================================


class TestScanSingleFile:
    def test_valid_annual_income(self, tmp_egx):
        p = tmp_egx / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write(p, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2023-12-31,5000,3000,2000,1500
2022-12-31,4200,2500,1700,1200""")

        entry = scan_single_file(str(p), "COMI", "income", "annual")
        assert entry["schema_valid"] is True
        assert entry["row_count"] == 2
        assert entry["latest_period_end_date"] == "2023-12-31"
        assert entry["missing_required_fields"] == []
        assert entry["used_by_runtime"] is True

    def test_quarterly_not_runtime_used(self, tmp_egx):
        p = tmp_egx / "egx_fundamentals" / "income_statements" / "COMI_income_quarterly.csv"
        _write(p, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2024-03-31,1500,900,600,400""")

        entry = scan_single_file(str(p), "COMI", "income", "quarterly")
        assert entry["used_by_runtime"] is False

    def test_missing_required_fields_recorded(self, tmp_egx):
        p = tmp_egx / "egx_fundamentals" / "income_statements" / "BAD_income_annual.csv"
        _write(p, """\
period_end_date,revenue,net_income
2023-12-31,5000,1500""")

        entry = scan_single_file(str(p), "BAD", "income", "annual")
        assert entry["schema_valid"] is False
        assert "gross_profit" in entry["missing_required_fields"]
        assert "operating_income" in entry["missing_required_fields"]

    def test_missing_file(self, tmp_egx):
        entry = scan_single_file("/nonexistent/path.csv", "X", "income", "annual")
        assert entry["schema_valid"] is False
        assert entry["row_count"] == 0
        assert "File not found" in entry["warnings"]

    def test_publish_date_extracted(self, tmp_egx):
        p = tmp_egx / "egx_fundamentals" / "income_statements" / "PUB_income_annual.csv"
        _write(p, """\
period_end_date,publish_date,revenue,gross_profit,operating_income,net_income
2023-12-31,2024-04-15,5000,3000,2000,1500""")

        entry = scan_single_file(str(p), "PUB", "income", "annual")
        assert entry["latest_publish_date"] == "2024-04-15"

    def test_scraped_at_extracted(self, tmp_egx):
        p = tmp_egx / "egx_fundamentals" / "income_statements" / "PROV_income_annual.csv"
        _write(p, """\
period_end_date,revenue,gross_profit,operating_income,net_income,data_source,scraped_at,publish_date
2023-12-31,5000,3000,2000,1500,yfinance,2024-06-01T10:00:00Z,""")

        entry = scan_single_file(str(p), "PROV", "income", "annual")
        assert entry["latest_scraped_at"] == "2024-06-01T10:00:00Z"
        assert entry["data_sources"] == ["yfinance"]
        # publish_date is empty/null — should remain None
        assert entry["latest_publish_date"] is None

    def test_multiple_data_sources(self, tmp_egx):
        p = tmp_egx / "egx_fundamentals" / "income_statements" / "MIX_income_annual.csv"
        _write(p, """\
period_end_date,revenue,gross_profit,operating_income,net_income,data_source,scraped_at
2023-12-31,5000,3000,2000,1500,yfinance,2024-06-01T10:00:00Z
2022-12-31,4200,2500,1700,1200,mubasher,2024-06-01T10:00:00Z""")

        entry = scan_single_file(str(p), "MIX", "income", "annual")
        assert sorted(entry["data_sources"]) == ["mubasher", "yfinance"]

    def test_no_provenance_columns_returns_defaults(self, tmp_egx):
        """Files without provenance columns should get empty defaults."""
        p = tmp_egx / "egx_fundamentals" / "income_statements" / "OLD_income_annual.csv"
        _write(p, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2023-12-31,5000,3000,2000,1500""")

        entry = scan_single_file(str(p), "OLD", "income", "annual")
        assert entry["latest_scraped_at"] is None
        assert entry["data_sources"] == []

    def test_ratios_schema_valid_with_only_period_end_date(self, tmp_egx):
        p = tmp_egx / "egx_fundamentals" / "key_ratios" / "COMI_ratios.csv"
        _write(p, """\
period_end_date,pe_ratio,roe
2023-12-31,7.2,0.25""")

        entry = scan_single_file(str(p), "COMI", "ratios", "annual")
        assert entry["schema_valid"] is True
        assert entry["missing_required_fields"] == []


# =============================================================================
# scan_fundamentals (full directory scan)
# =============================================================================


class TestScanFundamentals:
    def test_discovers_all_files(self, tmp_egx):
        _write(tmp_egx / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv",
               "period_end_date,revenue,gross_profit,operating_income,net_income\n2023-12-31,5000,3000,2000,1500")
        _write(tmp_egx / "egx_fundamentals" / "income_statements" / "COMI_income_quarterly.csv",
               "period_end_date,revenue,gross_profit,operating_income,net_income\n2024-03-31,1500,900,600,400")
        _write(tmp_egx / "egx_fundamentals" / "key_ratios" / "COMI_ratios.csv",
               "period_end_date,pe_ratio\n2023-12-31,7.2")

        entries = scan_fundamentals(data_dir=str(tmp_egx))
        assert len(entries) == 3

        tickers = {e["ticker"] for e in entries}
        assert tickers == {"COMI"}

        freqs = {e["frequency"] for e in entries}
        assert "annual" in freqs
        assert "quarterly" in freqs

    def test_annual_is_runtime_quarterly_is_not(self, tmp_egx):
        _write(tmp_egx / "egx_fundamentals" / "income_statements" / "EAST_income_annual.csv",
               "period_end_date,revenue,gross_profit,operating_income,net_income\n2023-12-31,1000,500,300,200")
        _write(tmp_egx / "egx_fundamentals" / "income_statements" / "EAST_income_quarterly.csv",
               "period_end_date,revenue,gross_profit,operating_income,net_income\n2024-03-31,300,150,90,60")

        entries = scan_fundamentals(data_dir=str(tmp_egx))
        annual = [e for e in entries if e["frequency"] == "annual"]
        quarterly = [e for e in entries if e["frequency"] == "quarterly"]

        assert all(e["used_by_runtime"] for e in annual)
        assert all(not e["used_by_runtime"] for e in quarterly)

    def test_empty_dir_returns_empty_list(self, tmp_egx):
        entries = scan_fundamentals(data_dir=str(tmp_egx))
        assert entries == []

    def test_nonexistent_dir_returns_empty_list(self, tmp_path):
        entries = scan_fundamentals(data_dir=str(tmp_path / "does_not_exist"))
        assert entries == []


# =============================================================================
# write_manifest
# =============================================================================


class TestWriteManifest:
    def test_writes_valid_json(self, tmp_egx):
        _write(tmp_egx / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv",
               "period_end_date,revenue,gross_profit,operating_income,net_income\n2023-12-31,5000,3000,2000,1500")

        entries = scan_fundamentals(data_dir=str(tmp_egx))
        out_path = str(tmp_egx / "egx_fundamentals" / "manifest.json")
        result_path = write_manifest(entries, output_path=out_path)

        assert os.path.exists(result_path)
        with open(result_path) as f:
            manifest = json.load(f)

        assert manifest["total_files"] == 1
        assert manifest["runtime_files"] == 1
        assert manifest["schema_valid_files"] == 1
        assert "generated_at" in manifest
        assert len(manifest["entries"]) == 1
        assert manifest["entries"][0]["ticker"] == "COMI"

    def test_manifest_counts_correct(self, tmp_egx):
        # 1 valid annual + 1 invalid (missing fields) + 1 quarterly
        _write(tmp_egx / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv",
               "period_end_date,revenue,gross_profit,operating_income,net_income\n2023-12-31,5000,3000,2000,1500")
        _write(tmp_egx / "egx_fundamentals" / "income_statements" / "BAD_income_annual.csv",
               "period_end_date,revenue\n2023-12-31,5000")
        _write(tmp_egx / "egx_fundamentals" / "income_statements" / "COMI_income_quarterly.csv",
               "period_end_date,revenue,gross_profit,operating_income,net_income\n2024-03-31,1500,900,600,400")

        entries = scan_fundamentals(data_dir=str(tmp_egx))
        out_path = str(tmp_egx / "manifest.json")
        write_manifest(entries, output_path=out_path)

        with open(out_path) as f:
            manifest = json.load(f)

        assert manifest["total_files"] == 3
        assert manifest["runtime_files"] == 2  # both annual files
        assert manifest["schema_valid_files"] == 2  # COMI annual + COMI quarterly (both have all req fields)


# =============================================================================
# check_manifest_freshness
# =============================================================================


def _write_manifest(path: Path, entries: list):
    """Helper to write a manifest JSON directly from entry dicts."""
    manifest = {
        "generated_at": "2024-06-01T00:00:00+00:00",
        "total_files": len(entries),
        "runtime_files": sum(1 for e in entries if e.get("used_by_runtime")),
        "schema_valid_files": sum(1 for e in entries if e.get("schema_valid")),
        "entries": entries,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2))


def _make_entry(ticker="COMI", stmt="income", freq="annual",
                period="2023-12-31", publish=None, row_count=5,
                schema_valid=True, runtime=True, **extra):
    """Build a minimal manifest entry dict for testing."""
    return {
        "ticker": ticker,
        "statement_type": stmt,
        "frequency": freq,
        "path": f"/fake/{ticker}_{stmt}_{freq}.csv",
        "row_count": row_count,
        "latest_period_end_date": period,
        "latest_publish_date": publish,
        "schema_valid": schema_valid,
        "missing_required_fields": extra.get("missing_required_fields", []),
        "warnings": extra.get("warnings", []),
        "used_by_runtime": runtime,
    }


def _make_complete_ticker(ticker, period="2023-12-31", **overrides):
    """Create all 3 annual statement entries for a ticker."""
    entries = []
    for stmt in ("income", "balance", "ratios"):
        kw = dict(ticker=ticker, stmt=stmt, period=period)
        kw.update(overrides)
        entries.append(_make_entry(**kw))
    return entries


# =============================================================================
# check_manifest_freshness — manifest integrity
# =============================================================================

class TestFreshnessManifestIntegrity:
    """Tests for manifest-level integrity (no per-ticker checks)."""

    def test_missing_manifest_file_fails_safely(self, tmp_path):
        result = check_manifest_freshness(str(tmp_path / "nonexistent.json"), "2024-06-01")
        assert result.fresh is False
        assert any("not found" in r for r in result.reasons)

    def test_corrupt_manifest_file_fails_safely(self, tmp_path):
        mf = tmp_path / "manifest.json"
        mf.write_text("not valid json {{{")
        result = check_manifest_freshness(str(mf), "2024-06-01")
        assert result.fresh is False
        assert any("parse error" in r.lower() for r in result.reasons)

    def test_empty_manifest_entries_fails(self, tmp_path):
        mf = tmp_path / "manifest.json"
        mf.write_text(json.dumps({"entries": [], "total_files": 0}))
        result = check_manifest_freshness(str(mf), "2024-06-01")
        assert result.fresh is False

    def test_no_required_tickers_passes_with_valid_manifest(self, tmp_path):
        """Without required_tickers, only manifest integrity is checked."""
        mf = tmp_path / "manifest.json"
        _write_manifest(mf, _make_complete_ticker("COMI"))
        result = check_manifest_freshness(str(mf), "2024-06-01")
        assert result.fresh is True


# =============================================================================
# check_manifest_freshness — scoping to required_tickers
# =============================================================================

class TestFreshnessScoping:
    """Verify that freshness only evaluates required_tickers."""

    def test_invalid_non_required_ticker_does_not_fail(self, tmp_path):
        """VLMR has schema_valid=False and zero rows, but is NOT required.
        Freshness should pass because only COMI is required."""
        mf = tmp_path / "manifest.json"
        entries = _make_complete_ticker("COMI", period="2023-12-31")
        entries.append(_make_entry("VLMR", stmt="income", schema_valid=False, row_count=0))
        _write_manifest(mf, entries)

        result = check_manifest_freshness(
            str(mf), "2024-06-01", required_tickers=["COMI"]
        )
        assert result.fresh is True
        assert result.reasons == []

    def test_invalid_required_ticker_does_fail(self, tmp_path):
        """ESRS is required but has schema_valid=False — freshness must fail."""
        mf = tmp_path / "manifest.json"
        entries = _make_complete_ticker("COMI", period="2023-12-31")
        entries += [
            _make_entry("ESRS", stmt="income", schema_valid=False, row_count=0),
            _make_entry("ESRS", stmt="balance", schema_valid=True, row_count=3),
            _make_entry("ESRS", stmt="ratios", schema_valid=True, row_count=3),
        ]
        _write_manifest(mf, entries)

        result = check_manifest_freshness(
            str(mf), "2024-06-01", required_tickers=["COMI", "ESRS"]
        )
        assert result.fresh is False
        assert any("ESRS" in r and "schema_valid" in r for r in result.reasons)

    def test_missing_required_ticker_fails(self, tmp_path):
        """EAST is required but has no entries at all."""
        mf = tmp_path / "manifest.json"
        _write_manifest(mf, _make_complete_ticker("COMI"))
        result = check_manifest_freshness(
            str(mf), "2024-06-01", required_tickers=["COMI", "EAST"]
        )
        assert result.fresh is False
        assert any("EAST" in r for r in result.reasons)

    def test_required_ticker_normalizes_ca_suffix(self, tmp_path):
        """COMI.CA in required list matches COMI in manifest entries."""
        mf = tmp_path / "manifest.json"
        _write_manifest(mf, _make_complete_ticker("COMI"))
        result = check_manifest_freshness(
            str(mf), "2024-06-01", required_tickers=["COMI.CA"]
        )
        assert result.fresh is True


# =============================================================================
# check_manifest_freshness — statement type completeness
# =============================================================================

class TestFreshnessStatementTypes:
    """Required ticker must have all 3 annual statement types."""

    def test_missing_balance_fails(self, tmp_path):
        """COMI has income + ratios but no balance."""
        mf = tmp_path / "manifest.json"
        entries = [
            _make_entry("COMI", stmt="income", period="2023-12-31"),
            _make_entry("COMI", stmt="ratios", period="2023-12-31"),
        ]
        _write_manifest(mf, entries)
        result = check_manifest_freshness(
            str(mf), "2024-06-01", required_tickers=["COMI"]
        )
        assert result.fresh is False
        assert any("balance" in r and "COMI" in r for r in result.reasons)

    def test_quarterly_file_does_not_count(self, tmp_path):
        """Quarterly balance does not satisfy the annual requirement."""
        mf = tmp_path / "manifest.json"
        entries = [
            _make_entry("COMI", stmt="income", period="2023-12-31"),
            _make_entry("COMI", stmt="balance", freq="quarterly",
                        period="2024-03-31", runtime=False),
            _make_entry("COMI", stmt="ratios", period="2023-12-31"),
        ]
        _write_manifest(mf, entries)
        result = check_manifest_freshness(
            str(mf), "2024-06-01", required_tickers=["COMI"]
        )
        assert result.fresh is False
        assert any("balance" in r for r in result.reasons)

    def test_all_three_present_passes(self, tmp_path):
        mf = tmp_path / "manifest.json"
        _write_manifest(mf, _make_complete_ticker("COMI"))
        result = check_manifest_freshness(
            str(mf), "2024-06-01", required_tickers=["COMI"]
        )
        assert result.fresh is True


# =============================================================================
# check_manifest_freshness — zero-row detection
# =============================================================================

class TestFreshnessZeroRows:
    """Active-universe ticker with zero data rows must fail clearly."""

    def test_zero_row_income_fails(self, tmp_path):
        """ESRS-like case: file exists, schema looks OK, but row_count=0."""
        mf = tmp_path / "manifest.json"
        entries = [
            _make_entry("ESRS", stmt="income", row_count=0, period=None),
            _make_entry("ESRS", stmt="balance", row_count=3, period="2023-12-31"),
            _make_entry("ESRS", stmt="ratios", row_count=3, period="2023-12-31"),
        ]
        _write_manifest(mf, entries)
        result = check_manifest_freshness(
            str(mf), "2024-06-01", required_tickers=["ESRS"]
        )
        assert result.fresh is False
        assert any("ESRS" in r and "zero" in r.lower() for r in result.reasons)

    def test_all_zero_rows_fails_all_statements(self, tmp_path):
        """All 3 files have zero rows."""
        mf = tmp_path / "manifest.json"
        entries = [
            _make_entry("ESRS", stmt="income", row_count=0, period=None),
            _make_entry("ESRS", stmt="balance", row_count=0, period=None),
            _make_entry("ESRS", stmt="ratios", row_count=0, period=None),
        ]
        _write_manifest(mf, entries)
        result = check_manifest_freshness(
            str(mf), "2024-06-01", required_tickers=["ESRS"]
        )
        assert result.fresh is False
        # Should have a failure reason for each zero-row statement
        esrs_reasons = [r for r in result.reasons if "ESRS" in r]
        assert len(esrs_reasons) >= 3


# =============================================================================
# check_manifest_freshness — filing-lag-aware staleness
# =============================================================================

class TestFreshnessFilingLag:
    """Annual period freshness uses filing-lag logic, not raw age."""

    def test_recent_period_within_lag_passes(self, tmp_path):
        """FY2023 (period_end=2023-12-31) checked as_of 2024-06-01.
        Filing expected available: 2023-12-31 + 120d = 2024-04-29.
        Next expected period: 2024-12-31, available 2025-04-30.
        2025-04-30 > 2024-06-01, so next filing is NOT overdue. Pass."""
        mf = tmp_path / "manifest.json"
        _write_manifest(mf, _make_complete_ticker("COMI", period="2023-12-31"))
        result = check_manifest_freshness(
            str(mf), "2024-06-01", required_tickers=["COMI"]
        )
        assert result.fresh is True

    def test_old_period_with_overdue_next_filing_fails(self, tmp_path):
        """FY2022 (period_end=2022-12-31) checked as_of 2024-06-01.
        Filing available: 2022-12-31 + 120d = 2023-04-30.
        Next expected: 2023-12-31, available 2024-04-30.
        2024-04-30 < 2024-06-01 → next filing is overdue. Fail."""
        mf = tmp_path / "manifest.json"
        _write_manifest(mf, _make_complete_ticker("COMI", period="2022-12-31"))
        result = check_manifest_freshness(
            str(mf), "2024-06-01", required_tickers=["COMI"]
        )
        assert result.fresh is False
        assert any("overdue" in r.lower() for r in result.reasons)

    def test_very_old_period_clearly_fails(self, tmp_path):
        """FY2020 data checked as_of 2024-06-01 — multiple filings overdue."""
        mf = tmp_path / "manifest.json"
        _write_manifest(mf, _make_complete_ticker("COMI", period="2020-12-31"))
        result = check_manifest_freshness(
            str(mf), "2024-06-01", required_tickers=["COMI"]
        )
        assert result.fresh is False

    def test_just_filed_period_passes(self, tmp_path):
        """Period ended recently but filing lag hasn't expired.
        FY2024 (period_end=2024-03-31) checked as_of 2024-06-01.
        Available: 2024-03-31 + 120d = 2024-07-29 (future).
        Next expected: 2025-03-31, available 2025-07-28.
        Both in the future — pass."""
        mf = tmp_path / "manifest.json"
        _write_manifest(mf, _make_complete_ticker("COMI", period="2024-03-31"))
        result = check_manifest_freshness(
            str(mf), "2024-06-01", required_tickers=["COMI"]
        )
        assert result.fresh is True

    def test_custom_filing_lag(self, tmp_path):
        """With a shorter filing lag (45d for quarterly), staleness changes."""
        mf = tmp_path / "manifest.json"
        _write_manifest(mf, _make_complete_ticker("COMI", period="2023-09-30"))
        # With 45d lag: available 2023-11-14, next period 2024-09-30,
        # next available 2024-11-14. as_of=2024-06-01 < 2024-11-14 → pass
        result = check_manifest_freshness(
            str(mf), "2024-06-01",
            required_tickers=["COMI"],
            filing_lag_days=45,
        )
        assert result.fresh is True

    def test_june_fiscal_year_fresh(self, tmp_path):
        """Non-December FY: EAST uses June 30 fiscal year end.
        FY2024 (period_end=2024-06-30) checked as_of 2025-03-01.
        Filing available: 2024-06-30 + 120d = 2024-10-28.
        Next expected period: 2025-06-30, available 2025-10-28.
        2025-10-28 > 2025-03-01 → next filing is NOT overdue. Pass."""
        mf = tmp_path / "manifest.json"
        _write_manifest(mf, _make_complete_ticker("EAST", period="2024-06-30"))
        result = check_manifest_freshness(
            str(mf), "2025-03-01", required_tickers=["EAST"]
        )
        assert result.fresh is True

    def test_june_fiscal_year_stale(self, tmp_path):
        """Non-December FY: EAST with old June period.
        FY2023 (period_end=2023-06-30) checked as_of 2025-03-01.
        Filing available: 2023-06-30 + 120d = 2023-10-28.
        Next expected: 2024-06-30, available 2024-10-28.
        2024-10-28 < 2025-03-01 → next filing overdue. Fail."""
        mf = tmp_path / "manifest.json"
        _write_manifest(mf, _make_complete_ticker("EAST", period="2023-06-30"))
        result = check_manifest_freshness(
            str(mf), "2025-03-01", required_tickers=["EAST"]
        )
        assert result.fresh is False
        assert any("overdue" in r.lower() for r in result.reasons)
