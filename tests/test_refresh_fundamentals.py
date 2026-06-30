"""
Tests for tradingagents.dataflows.refresh_fundamentals module.

Covers:
  - Dry-run does not call scraper
  - Selected tickers are respected
  - Default ticker list comes from config
  - Execute mode calls yfinance scraper with selected tickers
  - Report is always generated
  - Execute mode report captures before-vs-after changes (regression)
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest


# --- Fixtures ---

class FakeFreshnessResult:
    """Mimics the manifest_scanner.FreshnessResult namedtuple."""
    def __init__(self, fresh: bool, reasons: list = None):
        self.fresh = fresh
        self.reasons = reasons or []


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Create a minimal data directory structure for tests."""
    fund_dir = tmp_path / "egx_fundamentals"
    fund_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def mock_manifest_infra(tmp_data_dir):
    """Patch manifest scanner and config so refresh_fundamentals works in isolation."""
    patches = {}

    # Patch DATA_DIR in refresh_report (it imports from config)
    patches["report_data_dir"] = patch(
        "tradingagents.dataflows.refresh_report.DATA_DIR",
        str(tmp_data_dir),
    )

    # Patch DATA_DIR in config (used by refresh_fundamentals via config import)
    patches["config_data_dir"] = patch(
        "tradingagents.dataflows.config.DATA_DIR",
        str(tmp_data_dir),
    )

    # scan_fundamentals returns empty (no CSVs in tmp dir)
    # Must patch at BOTH the source module AND the refresh_report module
    # (refresh_report imports scan_fundamentals at module load time)
    patches["scan"] = patch(
        "tradingagents.dataflows.manifest_scanner.scan_fundamentals",
        return_value=[],
    )
    patches["scan_report"] = patch(
        "tradingagents.dataflows.refresh_report.scan_fundamentals",
        return_value=[],
    )

    # write_manifest is a no-op (patch at both locations)
    patches["write"] = patch(
        "tradingagents.dataflows.manifest_scanner.write_manifest",
    )
    patches["write_report"] = patch(
        "tradingagents.dataflows.refresh_report.write_manifest",
    )

    started = {k: p.start() for k, p in patches.items()}
    yield started
    for p in patches.values():
        p.stop()


# --- Tests ---


class TestDryRunDoesNotCallScraper:
    """Dry-run mode must never invoke the yfinance scraper."""

    def test_dry_run_skips_scraper(self, tmp_data_dir, mock_manifest_infra):
        """run_refresh(execute=False) should not call process_ticker."""
        with patch(
            "tradingagents.dataflows.refresh_fundamentals._identify_refresh_candidates",
            return_value={
                "needs_refresh": [{"ticker": "COMI", "reasons": ["stale"]}],
                "up_to_date": [],
                "manifest_path": str(tmp_data_dir / "egx_fundamentals" / "manifest.json"),
            },
        ), patch(
            "tradingagents.dataflows.refresh_fundamentals._execute_yfinance_refresh",
        ) as mock_exec:
            from tradingagents.dataflows.refresh_fundamentals import run_refresh

            report = run_refresh(
                tickers=["COMI"],
                execute=False,
                output_dir=str(tmp_data_dir / "egx_fundamentals"),
            )

            mock_exec.assert_not_called()
            # Verify dry-run markers in results
            meta = report["refresh_metadata"]
            assert meta["mode"] == "dry_run"
            assert meta["scraper_results"]["COMI"] == "DRY_RUN (would refresh)"

    def test_dry_run_does_not_overwrite_manifest(self, tmp_data_dir, mock_manifest_infra):
        """Dry-run must not write/overwrite manifest.json on disk."""
        manifest_path = tmp_data_dir / "egx_fundamentals" / "manifest.json"
        # Write a sentinel manifest to verify it's NOT overwritten
        sentinel = {"sentinel": True, "entries": []}
        manifest_path.write_text(json.dumps(sentinel))

        with patch(
            "tradingagents.dataflows.refresh_fundamentals._identify_refresh_candidates",
            return_value={
                "needs_refresh": [{"ticker": "COMI", "reasons": ["stale"]}],
                "up_to_date": [],
                "manifest_path": str(manifest_path),
            },
        ):
            from tradingagents.dataflows.refresh_fundamentals import run_refresh

            run_refresh(
                tickers=["COMI"],
                execute=False,
                output_dir=str(tmp_data_dir / "egx_fundamentals"),
            )

        # Sentinel should be intact (dry-run doesn't clobber)
        reloaded = json.loads(manifest_path.read_text())
        assert reloaded.get("sentinel") is True


class TestSelectedTickersRespected:
    """Only the tickers passed to run_refresh should be evaluated."""

    def test_only_selected_tickers_checked(self, tmp_data_dir, mock_manifest_infra):
        """Candidates identification receives only the requested tickers."""
        with patch(
            "tradingagents.dataflows.refresh_fundamentals._identify_refresh_candidates",
        ) as mock_identify:
            mock_identify.return_value = {
                "needs_refresh": [],
                "up_to_date": ["EAST"],
                "manifest_path": str(tmp_data_dir / "egx_fundamentals" / "manifest.json"),
            }

            from tradingagents.dataflows.refresh_fundamentals import run_refresh

            run_refresh(
                tickers=["EAST"],
                execute=False,
                output_dir=str(tmp_data_dir / "egx_fundamentals"),
            )

            # _identify_refresh_candidates should have been called with ["EAST"] as first positional arg
            call_args = mock_identify.call_args
            assert call_args[0][0] == ["EAST"]

    def test_multiple_tickers_all_passed(self, tmp_data_dir, mock_manifest_infra):
        """Multiple tickers are all forwarded to identification."""
        with patch(
            "tradingagents.dataflows.refresh_fundamentals._identify_refresh_candidates",
        ) as mock_identify:
            mock_identify.return_value = {
                "needs_refresh": [
                    {"ticker": "COMI", "reasons": ["missing"]},
                    {"ticker": "TMGH", "reasons": ["stale"]},
                ],
                "up_to_date": ["EAST"],
                "manifest_path": str(tmp_data_dir / "egx_fundamentals" / "manifest.json"),
            }

            from tradingagents.dataflows.refresh_fundamentals import run_refresh

            report = run_refresh(
                tickers=["COMI", "EAST", "TMGH"],
                execute=False,
                output_dir=str(tmp_data_dir / "egx_fundamentals"),
            )

            meta = report["refresh_metadata"]
            assert set(meta["requested_tickers"]) == {"COMI", "EAST", "TMGH"}


class TestDefaultTickersFromConfig:
    """When no tickers are specified, the EGX_TICKERS config should be used."""

    def test_default_tickers_loaded(self, tmp_data_dir, mock_manifest_infra):
        """run_refresh(tickers=None) uses _get_default_tickers() from config."""
        fake_tickers = ["ALPHA", "BETA", "GAMMA"]

        with patch(
            "tradingagents.dataflows.refresh_fundamentals._get_default_tickers",
            return_value=fake_tickers,
        ), patch(
            "tradingagents.dataflows.refresh_fundamentals._identify_refresh_candidates",
            return_value={
                "needs_refresh": [],
                "up_to_date": fake_tickers,
                "manifest_path": str(tmp_data_dir / "egx_fundamentals" / "manifest.json"),
            },
        ):
            from tradingagents.dataflows.refresh_fundamentals import run_refresh

            report = run_refresh(
                tickers=None,
                execute=False,
                output_dir=str(tmp_data_dir / "egx_fundamentals"),
            )

            meta = report["refresh_metadata"]
            assert meta["requested_tickers"] == fake_tickers

    def test_get_default_tickers_strips_suffix(self):
        """_get_default_tickers removes .CA suffix and uppercases."""
        with patch(
            "tradingagents.default_config.EGX_TICKERS",
            ["comi.CA", "East.ca", "TMGH"],
        ):
            from tradingagents.dataflows.refresh_fundamentals import _get_default_tickers

            result = _get_default_tickers()
            assert result == ["COMI", "EAST", "TMGH"]


class TestExecuteModeCallsScraper:
    """Execute mode must call the yfinance scraper for stale tickers only."""

    def test_execute_calls_scraper_for_stale(self, tmp_data_dir, mock_manifest_infra):
        """run_refresh(execute=True) calls _execute_yfinance_refresh with stale tickers."""
        with patch(
            "tradingagents.dataflows.refresh_fundamentals._identify_refresh_candidates",
            return_value={
                "needs_refresh": [
                    {"ticker": "COMI", "reasons": ["stale"]},
                    {"ticker": "EAST", "reasons": ["missing"]},
                ],
                "up_to_date": ["TMGH"],
                "manifest_path": str(tmp_data_dir / "egx_fundamentals" / "manifest.json"),
            },
        ), patch(
            "tradingagents.dataflows.refresh_fundamentals._execute_yfinance_refresh",
            return_value={"COMI": "OK", "EAST": "OK"},
        ) as mock_exec:
            from tradingagents.dataflows.refresh_fundamentals import run_refresh

            report = run_refresh(
                tickers=["COMI", "EAST", "TMGH"],
                execute=True,
                output_dir=str(tmp_data_dir / "egx_fundamentals"),
            )

            # Scraper called with exactly the stale tickers
            mock_exec.assert_called_once_with(["COMI", "EAST"])

            meta = report["refresh_metadata"]
            assert meta["mode"] == "execute"
            assert meta["scraper_results"] == {"COMI": "OK", "EAST": "OK"}

    def test_execute_skips_scraper_when_all_fresh(self, tmp_data_dir, mock_manifest_infra):
        """If all tickers are fresh, execute mode should not call scraper."""
        with patch(
            "tradingagents.dataflows.refresh_fundamentals._identify_refresh_candidates",
            return_value={
                "needs_refresh": [],
                "up_to_date": ["COMI", "EAST"],
                "manifest_path": str(tmp_data_dir / "egx_fundamentals" / "manifest.json"),
            },
        ), patch(
            "tradingagents.dataflows.refresh_fundamentals._execute_yfinance_refresh",
        ) as mock_exec:
            from tradingagents.dataflows.refresh_fundamentals import run_refresh

            report = run_refresh(
                tickers=["COMI", "EAST"],
                execute=True,
                output_dir=str(tmp_data_dir / "egx_fundamentals"),
            )

            mock_exec.assert_not_called()
            assert report["refresh_metadata"]["scraper_results"] == {}


class TestReportAlwaysGenerated:
    """A refresh report must always be written regardless of mode."""

    def test_report_written_on_dry_run(self, tmp_data_dir, mock_manifest_infra):
        """Dry-run still produces a JSON report file."""
        with patch(
            "tradingagents.dataflows.refresh_fundamentals._identify_refresh_candidates",
            return_value={
                "needs_refresh": [{"ticker": "COMI", "reasons": ["stale"]}],
                "up_to_date": [],
                "manifest_path": str(tmp_data_dir / "egx_fundamentals" / "manifest.json"),
            },
        ):
            from tradingagents.dataflows.refresh_fundamentals import run_refresh

            report = run_refresh(
                tickers=["COMI"],
                execute=False,
                output_dir=str(tmp_data_dir / "egx_fundamentals"),
            )

            # Report path should exist
            assert "report_path" in report
            assert os.path.exists(report["report_path"])

            # Verify it's valid JSON
            with open(report["report_path"]) as f:
                saved = json.load(f)
            assert "refresh_metadata" in saved

    def test_report_written_on_execute(self, tmp_data_dir, mock_manifest_infra):
        """Execute mode also produces a JSON report file."""
        with patch(
            "tradingagents.dataflows.refresh_fundamentals._identify_refresh_candidates",
            return_value={
                "needs_refresh": [{"ticker": "COMI", "reasons": ["stale"]}],
                "up_to_date": [],
                "manifest_path": str(tmp_data_dir / "egx_fundamentals" / "manifest.json"),
            },
        ), patch(
            "tradingagents.dataflows.refresh_fundamentals._execute_yfinance_refresh",
            return_value={"COMI": "OK"},
        ):
            from tradingagents.dataflows.refresh_fundamentals import run_refresh

            report = run_refresh(
                tickers=["COMI"],
                execute=True,
                output_dir=str(tmp_data_dir / "egx_fundamentals"),
            )

            assert "report_path" in report
            assert os.path.exists(report["report_path"])

    def test_report_contains_refresh_metadata(self, tmp_data_dir, mock_manifest_infra):
        """Report always includes the refresh_metadata block with all expected keys."""
        with patch(
            "tradingagents.dataflows.refresh_fundamentals._identify_refresh_candidates",
            return_value={
                "needs_refresh": [],
                "up_to_date": ["COMI"],
                "manifest_path": str(tmp_data_dir / "egx_fundamentals" / "manifest.json"),
            },
        ):
            from tradingagents.dataflows.refresh_fundamentals import run_refresh

            report = run_refresh(
                tickers=["COMI"],
                execute=False,
                output_dir=str(tmp_data_dir / "egx_fundamentals"),
            )

            meta = report["refresh_metadata"]
            expected_keys = {
                "mode", "source", "as_of_date", "requested_tickers",
                "needs_refresh", "up_to_date", "scraper_results",
            }
            assert expected_keys.issubset(set(meta.keys()))


class TestUnsupportedSource:
    """Requesting an unsupported source should raise ValueError."""

    def test_invalid_source_raises(self, tmp_data_dir, mock_manifest_infra):
        from tradingagents.dataflows.refresh_fundamentals import run_refresh

        with pytest.raises(ValueError, match="Unsupported source"):
            run_refresh(tickers=["COMI"], source="bloomberg", execute=False)


class TestExecuteReportCapturesBeforeAfterDiff:
    """Execute mode report must reflect true before-vs-after changes.

    Regression test: when the scraper adds rows or updates latest_period,
    the report must show those as changed_files, not an empty diff.
    """

    def test_row_count_change_detected_in_report(self, tmp_data_dir):
        """Scraper adds rows to COMI income → report shows row_count change."""
        from tradingagents.dataflows.refresh_fundamentals import run_refresh

        manifest_path = tmp_data_dir / "egx_fundamentals" / "manifest.json"

        # "Before" manifest: COMI income has 3 rows
        before_manifest = {
            "generated_at": "2026-05-01T00:00:00+00:00",
            "entries": [
                {
                    "ticker": "COMI",
                    "statement_type": "income",
                    "frequency": "annual",
                    "row_count": 3,
                    "latest_period_end_date": "2023-06-30",
                    "schema_valid": True,
                    "data_sources": ["yfinance"],
                    "latest_scraped_at": "2026-05-01T00:00:00Z",
                },
            ],
        }
        manifest_path.write_text(json.dumps(before_manifest))

        # "After" state: COMI income now has 5 rows and a newer period
        after_entries = [
            {
                "ticker": "COMI",
                "statement_type": "income",
                "frequency": "annual",
                "row_count": 5,
                "latest_period_end_date": "2024-06-30",
                "schema_valid": True,
                "data_sources": ["yfinance"],
                "latest_scraped_at": "2026-05-30T12:00:00Z",
            },
        ]

        # Track scan call count to return different results before/after
        scan_call_count = [0]

        def mock_scan(data_dir=None):
            scan_call_count[0] += 1
            # Every scan after the scraper runs returns the "after" state
            return after_entries

        with patch("tradingagents.dataflows.config.DATA_DIR", str(tmp_data_dir)), \
             patch("tradingagents.dataflows.refresh_report.DATA_DIR", str(tmp_data_dir)), \
             patch("tradingagents.dataflows.manifest_scanner.scan_fundamentals", side_effect=mock_scan), \
             patch("tradingagents.dataflows.refresh_report.scan_fundamentals", side_effect=mock_scan), \
             patch("tradingagents.dataflows.manifest_scanner.write_manifest"), \
             patch("tradingagents.dataflows.refresh_report.write_manifest"), \
             patch(
                 "tradingagents.dataflows.refresh_fundamentals._identify_refresh_candidates",
                 return_value={
                     "needs_refresh": [{"ticker": "COMI", "reasons": ["stale"]}],
                     "up_to_date": [],
                     "manifest_path": str(manifest_path),
                 },
             ), \
             patch(
                 "tradingagents.dataflows.refresh_fundamentals._execute_yfinance_refresh",
                 return_value={"COMI": "OK"},
             ):

            report = run_refresh(
                tickers=["COMI"],
                execute=True,
                output_dir=str(tmp_data_dir / "egx_fundamentals"),
            )

        # The report must detect the row_count change (3 → 5)
        # and the latest_period_end_date change (2023-06-30 → 2024-06-30)
        assert report["summary"]["changed_files"] > 0

        # Find the specific change entry
        changed = report.get("changed_files", [])
        assert len(changed) >= 1

        comi_change = next(
            (c for c in changed if "COMI" in c["key"]),
            None,
        )
        assert comi_change is not None, f"Expected COMI change in: {changed}"
        assert "row_count" in comi_change["diffs"]
        assert comi_change["diffs"]["row_count"]["old"] == 3
        assert comi_change["diffs"]["row_count"]["new"] == 5

    def test_latest_period_change_classified_as_financial(self, tmp_data_dir):
        """A change in latest_period_end_date is classified as a financial change."""
        from tradingagents.dataflows.refresh_fundamentals import run_refresh

        manifest_path = tmp_data_dir / "egx_fundamentals" / "manifest.json"

        before_manifest = {
            "generated_at": "2026-05-01T00:00:00+00:00",
            "entries": [
                {
                    "ticker": "EAST",
                    "statement_type": "balance",
                    "frequency": "annual",
                    "row_count": 4,
                    "latest_period_end_date": "2023-12-31",
                    "schema_valid": True,
                    "data_sources": ["yfinance"],
                    "latest_scraped_at": "2026-05-01T00:00:00Z",
                },
            ],
        }
        manifest_path.write_text(json.dumps(before_manifest))

        after_entries = [
            {
                "ticker": "EAST",
                "statement_type": "balance",
                "frequency": "annual",
                "row_count": 5,
                "latest_period_end_date": "2024-12-31",
                "schema_valid": True,
                "data_sources": ["yfinance"],
                "latest_scraped_at": "2026-05-30T12:00:00Z",
            },
        ]

        with patch("tradingagents.dataflows.config.DATA_DIR", str(tmp_data_dir)), \
             patch("tradingagents.dataflows.refresh_report.DATA_DIR", str(tmp_data_dir)), \
             patch("tradingagents.dataflows.manifest_scanner.scan_fundamentals", return_value=after_entries), \
             patch("tradingagents.dataflows.refresh_report.scan_fundamentals", return_value=after_entries), \
             patch("tradingagents.dataflows.manifest_scanner.write_manifest"), \
             patch("tradingagents.dataflows.refresh_report.write_manifest"), \
             patch(
                 "tradingagents.dataflows.refresh_fundamentals._identify_refresh_candidates",
                 return_value={
                     "needs_refresh": [{"ticker": "EAST", "reasons": ["stale"]}],
                     "up_to_date": [],
                     "manifest_path": str(manifest_path),
                 },
             ), \
             patch(
                 "tradingagents.dataflows.refresh_fundamentals._execute_yfinance_refresh",
                 return_value={"EAST": "OK"},
             ):

            report = run_refresh(
                tickers=["EAST"],
                execute=True,
                output_dir=str(tmp_data_dir / "egx_fundamentals"),
            )

        # Must be classified as a financial change (not provenance-only)
        assert report["summary"]["financial_changed_files"] > 0
        fin_changed = report.get("financial_changed_files", [])
        assert any("EAST" in c["key"] for c in fin_changed)
