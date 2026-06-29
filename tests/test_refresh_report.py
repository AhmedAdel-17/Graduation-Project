"""
Tests for the fundamentals refresh report generator.

Uses temporary CSV/manifest files — no network calls, no live data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tradingagents.dataflows.refresh_report import (
    generate_refresh_report,
    write_refresh_report,
    format_report_markdown,
)


@pytest.fixture
def egx_dir(tmp_path):
    """Create a minimal EGX fundamentals directory structure."""
    egx = tmp_path / "egx_fundamentals"
    (egx / "income_statements").mkdir(parents=True)
    (egx / "balance_sheets").mkdir(parents=True)
    (egx / "key_ratios").mkdir(parents=True)
    return tmp_path


def _write_csv(path: Path, content: str):
    path.write_text(content.strip() + "\n")


def _write_manifest(egx_dir: Path, entries: list):
    """Write a manifest.json that the report can diff against."""
    manifest = {
        "generated_at": "2024-01-01T00:00:00+00:00",
        "total_files": len(entries),
        "runtime_files": sum(1 for e in entries if e.get("used_by_runtime")),
        "schema_valid_files": sum(1 for e in entries if e.get("schema_valid")),
        "entries": entries,
    }
    mf = egx_dir / "egx_fundamentals" / "manifest.json"
    mf.write_text(json.dumps(manifest, indent=2))


class TestGenerateRefreshReport:
    """Core report generation tests."""

    def test_no_previous_manifest(self, egx_dir):
        """First run with no existing manifest — all files are 'new'."""
        _write_csv(
            egx_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv",
            "period_end_date,revenue,gross_profit,operating_income,net_income,data_source,scraped_at,publish_date\n"
            "2023-12-31,5000,3000,2000,1500,yfinance,2024-06-01T10:00:00Z,",
        )
        report = generate_refresh_report(data_dir=str(egx_dir))

        assert report["had_previous_manifest"] is False
        assert report["summary"]["total_files"] >= 1
        assert report["summary"]["new_files"] >= 1

    def test_no_changes_detected(self, egx_dir):
        """When manifest exists and nothing changed — zero diffs."""
        csv_path = egx_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path,
            "period_end_date,revenue,gross_profit,operating_income,net_income\n"
            "2023-12-31,5000,3000,2000,1500",
        )
        # Generate manifest first time
        report1 = generate_refresh_report(data_dir=str(egx_dir))
        # Generate again — nothing changed
        report2 = generate_refresh_report(data_dir=str(egx_dir))

        assert report2["had_previous_manifest"] is True
        assert report2["summary"]["changed_files"] == 0
        assert report2["summary"]["new_files"] == 0
        assert report2["summary"]["removed_files"] == 0

    def test_detects_row_count_change(self, egx_dir):
        """Adding rows to a CSV produces a 'changed' entry."""
        csv_path = egx_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path,
            "period_end_date,revenue,gross_profit,operating_income,net_income\n"
            "2023-12-31,5000,3000,2000,1500",
        )
        # First scan
        generate_refresh_report(data_dir=str(egx_dir))

        # Add a row
        _write_csv(csv_path,
            "period_end_date,revenue,gross_profit,operating_income,net_income\n"
            "2023-12-31,5000,3000,2000,1500\n"
            "2022-12-31,4000,2500,1700,1200",
        )
        report = generate_refresh_report(data_dir=str(egx_dir))

        assert report["summary"]["changed_files"] >= 1
        changed_keys = [c["key"] for c in report["changed_files"]]
        assert any("COMI" in k for k in changed_keys)
        # Check specific diff
        comi_change = next(c for c in report["changed_files"] if "COMI" in c["key"])
        assert "row_count" in comi_change["diffs"]
        assert comi_change["diffs"]["row_count"]["old"] == 1
        assert comi_change["diffs"]["row_count"]["new"] == 2

    def test_detects_new_file(self, egx_dir):
        """A new CSV file that wasn't in previous manifest shows as new."""
        _write_csv(
            egx_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv",
            "period_end_date,revenue,gross_profit,operating_income,net_income\n"
            "2023-12-31,5000,3000,2000,1500",
        )
        # First scan (only COMI income)
        generate_refresh_report(data_dir=str(egx_dir))

        # Add balance sheet
        _write_csv(
            egx_dir / "egx_fundamentals" / "balance_sheets" / "COMI_balance_annual.csv",
            "period_end_date,total_assets,total_liabilities,total_equity\n"
            "2023-12-31,100000,60000,40000",
        )
        report = generate_refresh_report(data_dir=str(egx_dir))

        assert report["summary"]["new_files"] >= 1
        assert any("COMI/balance" in k for k in report["new_files"])

    def test_detects_removed_file(self, egx_dir):
        """A file removed since last scan shows as removed."""
        csv_path = egx_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path,
            "period_end_date,revenue,gross_profit,operating_income,net_income\n"
            "2023-12-31,5000,3000,2000,1500",
        )
        generate_refresh_report(data_dir=str(egx_dir))

        # Remove the file
        csv_path.unlink()
        report = generate_refresh_report(data_dir=str(egx_dir))

        assert report["summary"]["removed_files"] >= 1


class TestSourceCoverage:
    """Test that data_source column values are surfaced."""

    def test_source_counted(self, egx_dir):
        _write_csv(
            egx_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv",
            "period_end_date,revenue,gross_profit,operating_income,net_income,data_source,scraped_at\n"
            "2023-12-31,5000,3000,2000,1500,yfinance,2024-06-01T10:00:00Z",
        )
        report = generate_refresh_report(data_dir=str(egx_dir))
        assert "yfinance" in report["source_coverage"]["by_source"]

    def test_no_source_column_counted(self, egx_dir):
        """Files without data_source column are counted separately."""
        _write_csv(
            egx_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv",
            "period_end_date,revenue,gross_profit,operating_income,net_income\n"
            "2023-12-31,5000,3000,2000,1500",
        )
        report = generate_refresh_report(data_dir=str(egx_dir))
        assert report["source_coverage"]["no_source_column"] >= 1


class TestScrapedAtSurfacing:
    """Test that scraped_at is surfaced in manifest and report."""

    def test_scraped_at_change_detected(self, egx_dir):
        csv_path = egx_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path,
            "period_end_date,revenue,gross_profit,operating_income,net_income,data_source,scraped_at\n"
            "2023-12-31,5000,3000,2000,1500,yfinance,2024-01-01T10:00:00Z",
        )
        generate_refresh_report(data_dir=str(egx_dir))

        # Re-scrape with new timestamp
        _write_csv(csv_path,
            "period_end_date,revenue,gross_profit,operating_income,net_income,data_source,scraped_at\n"
            "2023-12-31,5000,3000,2000,1500,yfinance,2024-06-15T14:30:00Z",
        )
        report = generate_refresh_report(data_dir=str(egx_dir))

        assert report["summary"]["changed_files"] >= 1
        comi_change = next(c for c in report["changed_files"] if "COMI" in c["key"])
        assert "latest_scraped_at" in comi_change["diffs"]


class TestChangeClassification:
    """Financial vs provenance-only change separation."""

    def test_provenance_only_change_classified(self, egx_dir):
        """Changing only scraped_at → provenance_changed, NOT financial_changed."""
        csv_path = egx_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path,
            "period_end_date,revenue,gross_profit,operating_income,net_income,data_source,scraped_at\n"
            "2023-12-31,5000,3000,2000,1500,yfinance,2024-01-01T10:00:00Z",
        )
        generate_refresh_report(data_dir=str(egx_dir))

        # Re-scrape: same data, new timestamp
        _write_csv(csv_path,
            "period_end_date,revenue,gross_profit,operating_income,net_income,data_source,scraped_at\n"
            "2023-12-31,5000,3000,2000,1500,yfinance,2024-06-15T14:30:00Z",
        )
        report = generate_refresh_report(data_dir=str(egx_dir))

        assert report["summary"]["provenance_changed_files"] >= 1
        assert report["summary"]["financial_changed_files"] == 0

    def test_financial_change_classified(self, egx_dir):
        """Adding a row → financial_changed, NOT provenance-only."""
        csv_path = egx_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path,
            "period_end_date,revenue,gross_profit,operating_income,net_income\n"
            "2023-12-31,5000,3000,2000,1500",
        )
        generate_refresh_report(data_dir=str(egx_dir))

        _write_csv(csv_path,
            "period_end_date,revenue,gross_profit,operating_income,net_income\n"
            "2023-12-31,5000,3000,2000,1500\n"
            "2022-12-31,4000,2500,1700,1200",
        )
        report = generate_refresh_report(data_dir=str(egx_dir))

        assert report["summary"]["financial_changed_files"] >= 1
        assert report["summary"]["provenance_changed_files"] == 0

    def test_mixed_change_is_financial(self, egx_dir):
        """Row count + scraped_at both change → financial (not provenance-only)."""
        csv_path = egx_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path,
            "period_end_date,revenue,gross_profit,operating_income,net_income,scraped_at\n"
            "2023-12-31,5000,3000,2000,1500,2024-01-01T10:00:00Z",
        )
        generate_refresh_report(data_dir=str(egx_dir))

        _write_csv(csv_path,
            "period_end_date,revenue,gross_profit,operating_income,net_income,scraped_at\n"
            "2023-12-31,5000,3000,2000,1500,2024-06-15T14:30:00Z\n"
            "2022-12-31,4000,2500,1700,1200,2024-06-15T14:30:00Z",
        )
        report = generate_refresh_report(data_dir=str(egx_dir))

        assert report["summary"]["financial_changed_files"] >= 1
        # Mixed changes are NOT provenance-only
        assert report["summary"]["provenance_changed_files"] == 0


class TestDryRunMode:
    """Test write_manifest_file=False (dry-run)."""

    def test_dry_run_does_not_write_manifest(self, egx_dir):
        _write_csv(
            egx_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv",
            "period_end_date,revenue,gross_profit,operating_income,net_income\n"
            "2023-12-31,5000,3000,2000,1500",
        )
        manifest_path = egx_dir / "egx_fundamentals" / "manifest.json"
        assert not manifest_path.exists()

        report = generate_refresh_report(
            data_dir=str(egx_dir), write_manifest_file=False
        )
        # Report should still work
        assert report["summary"]["total_files"] >= 1
        # But manifest.json should NOT have been written
        assert not manifest_path.exists()

    def test_default_writes_manifest(self, egx_dir):
        _write_csv(
            egx_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv",
            "period_end_date,revenue,gross_profit,operating_income,net_income\n"
            "2023-12-31,5000,3000,2000,1500",
        )
        manifest_path = egx_dir / "egx_fundamentals" / "manifest.json"

        generate_refresh_report(data_dir=str(egx_dir))
        assert manifest_path.exists()


class TestStaleAndInvalid:
    """Test stale/schema-invalid detection."""

    def test_zero_row_runtime_file_flagged_stale(self, egx_dir):
        """A runtime-used file with zero data rows should be flagged."""
        _write_csv(
            egx_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv",
            "period_end_date,revenue,gross_profit,operating_income,net_income",
        )
        report = generate_refresh_report(data_dir=str(egx_dir))

        assert report["summary"]["stale_files"] >= 1
        assert any("COMI" in s["key"] for s in report["stale_files"])

    def test_schema_invalid_file_flagged(self, egx_dir):
        """A file missing required columns is flagged schema-invalid."""
        _write_csv(
            egx_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv",
            "period_end_date,revenue\n"
            "2023-12-31,5000",
        )
        report = generate_refresh_report(data_dir=str(egx_dir))

        assert report["summary"]["schema_invalid_files"] >= 1


class TestWriteAndFormat:
    """Test output writing and markdown formatting."""

    def test_write_produces_json_file(self, egx_dir):
        _write_csv(
            egx_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv",
            "period_end_date,revenue,gross_profit,operating_income,net_income\n"
            "2023-12-31,5000,3000,2000,1500",
        )
        report = generate_refresh_report(data_dir=str(egx_dir))
        path = write_refresh_report(report, output_dir=str(egx_dir))

        assert os.path.exists(path)
        with open(path) as f:
            loaded = json.load(f)
        assert loaded["summary"]["total_files"] >= 1

    def test_markdown_format_contains_sections(self, egx_dir):
        _write_csv(
            egx_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv",
            "period_end_date,revenue,gross_profit,operating_income,net_income\n"
            "2023-12-31,5000,3000,2000,1500",
        )
        report = generate_refresh_report(data_dir=str(egx_dir))
        md = format_report_markdown(report)

        assert "# Fundamentals Refresh Report" in md
        assert "## Summary" in md
        assert "## Source Coverage" in md


class TestProvenanceColumns:
    """Test that provenance columns don't break existing loaders."""

    def test_data_loader_ignores_provenance_columns(self, egx_dir):
        """The data_loader should gracefully ignore data_source/scraped_at/publish_date."""
        from tradingagents.agents.analysts.fundamentals.data_loader import (
            _read_egx_csv_multi,
        )
        from tradingagents.dataflows.local import (
            EGX_INCOME_REQUIRED_FIELDS,
            EGX_INCOME_OPTIONAL_FIELDS,
        )
        from unittest.mock import patch

        csv_path = egx_dir / "egx_fundamentals" / "income_statements" / "TEST_income_annual.csv"
        _write_csv(csv_path,
            "period_end_date,revenue,gross_profit,operating_income,net_income,data_source,scraped_at,publish_date\n"
            "2023-12-31,5000,3000,2000,1500,yfinance,2024-06-01T10:00:00Z,",
        )

        with patch("tradingagents.agents.analysts.fundamentals.data_loader.DATA_DIR", str(egx_dir)):
            rows, diag = _read_egx_csv_multi(
                ticker="TEST",
                statement_type="income_statements",
                filename_suffix="_income_annual.csv",
                required_fields=EGX_INCOME_REQUIRED_FIELDS,
                optional_fields=EGX_INCOME_OPTIONAL_FIELDS,
                curr_date=None,
                freq="annual",
                n_periods=5,
            )

        assert len(rows) == 1
        assert rows[0].get("revenue") == 5000.0
        assert rows[0].get("net_income") == 1500.0
        # Provenance columns should NOT appear in the parsed financial data
        assert "data_source" not in rows[0]
        assert "scraped_at" not in rows[0]
