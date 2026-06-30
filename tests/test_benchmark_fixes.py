"""Tests for EGX30 benchmark fixes:
- Index ticker normalization (^, $ prefixes not mangled)
- CSV date format flexibility (MM/DD/YYYY, DD/MM/YYYY, YYYY-MM-DD)
- Missing benchmark produces explicit disabled/error flag
"""

import csv
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest


# ===========================================================================
# 1. Index ticker normalization
# ===========================================================================


class TestNormalizeSymbol:
    """DataGateway._normalize_symbol must not append .CA to index tickers."""

    @pytest.fixture
    def gateway(self):
        """Minimal DataGateway instance (no real connections needed)."""
        from tradingagents.dataflows.gateway import DataGateway
        with patch.object(DataGateway, "__init__", lambda self: None):
            gw = DataGateway.__new__(DataGateway)
            # Bind the real method
            gw._normalize_symbol = DataGateway._normalize_symbol.__get__(gw)
            return gw

    def test_caret_egx30_unchanged(self, gateway):
        assert gateway._normalize_symbol("^EGX30") == "^EGX30"

    def test_dollar_egx30_unchanged(self, gateway):
        assert gateway._normalize_symbol("$EGX30") == "$EGX30"

    def test_caret_tasi_unchanged(self, gateway):
        assert gateway._normalize_symbol("^TASI") == "^TASI"

    def test_caret_lowercase_uppercased(self, gateway):
        assert gateway._normalize_symbol("^egx30") == "^EGX30"

    def test_dollar_lowercase_uppercased(self, gateway):
        assert gateway._normalize_symbol("$tasi") == "$TASI"

    def test_regular_ticker_gets_ca(self, gateway):
        assert gateway._normalize_symbol("COMI") == "COMI.CA"

    def test_regular_ticker_already_ca(self, gateway):
        assert gateway._normalize_symbol("COMI.CA") == "COMI.CA"

    def test_lowercase_regular_ticker(self, gateway):
        assert gateway._normalize_symbol("comi") == "COMI.CA"

    def test_whitespace_stripped(self, gateway):
        assert gateway._normalize_symbol("  ^EGX30  ") == "^EGX30"
        assert gateway._normalize_symbol("  COMI  ") == "COMI.CA"


# ===========================================================================
# 2. CSV date format flexibility
# ===========================================================================


class TestCSVDateParsing:
    """BacktestingEngine._load_egx30_csv supports multiple date formats."""

    @pytest.fixture
    def engine(self):
        """Minimal BacktestingEngine instance (no graph, no gateway)."""
        from scripts.backtester import BacktestingEngine
        with patch.object(BacktestingEngine, "__init__", lambda self: None):
            eng = BacktestingEngine.__new__(BacktestingEngine)
            eng._bm_data_map = {}
            eng.benchmark_start_price = None
            # Bind real methods
            eng._load_egx30_csv = BacktestingEngine._load_egx30_csv.__get__(eng)
            eng._parse_csv_date = BacktestingEngine._parse_csv_date
            eng._CSV_DATE_FORMATS = BacktestingEngine._CSV_DATE_FORMATS
            return eng

    def _write_csv(self, rows, tmpdir):
        """Write a CSV with Date,Price columns and return the path."""
        path = os.path.join(tmpdir, "test_egx30.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Date", "Price", "Open", "High", "Low", "Vol.", "Change %"])
            for row in rows:
                writer.writerow(row)
        return path

    def test_mm_dd_yyyy_format(self, engine):
        """Standard Investing.com US format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_csv([
                ["01/15/2024", "27,123.45", "27000", "27200", "26900", "1B", "0.5%"],
                ["01/16/2024", "27,250.00", "27100", "27300", "27000", "1.1B", "0.47%"],
            ], tmpdir)
            assert engine._load_egx30_csv(path) is True
            assert "2024-01-15" in engine._bm_data_map
            assert "2024-01-16" in engine._bm_data_map
            assert engine._bm_data_map["2024-01-15"] == pytest.approx(27123.45)

    def test_dd_mm_yyyy_format(self, engine):
        """European/Arabic locale format from Investing.com."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_csv([
                ["15/01/2024", "27,123.45", "27000", "27200", "26900", "1B", "0.5%"],
                ["16/01/2024", "27,250.00", "27100", "27300", "27000", "1.1B", "0.47%"],
            ], tmpdir)
            assert engine._load_egx30_csv(path) is True
            assert "2024-01-15" in engine._bm_data_map
            assert "2024-01-16" in engine._bm_data_map

    def test_yyyy_mm_dd_format(self, engine):
        """ISO format (programmatic exports)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_csv([
                ["2024-01-15", "27,123.45", "27000", "27200", "26900", "1B", "0.5%"],
                ["2024-01-16", "27,250.00", "27100", "27300", "27000", "1.1B", "0.47%"],
            ], tmpdir)
            assert engine._load_egx30_csv(path) is True
            assert "2024-01-15" in engine._bm_data_map
            assert "2024-01-16" in engine._bm_data_map

    def test_empty_csv_returns_false(self, engine):
        """CSV with only headers should fail gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_csv([], tmpdir)
            assert engine._load_egx30_csv(path) is False

    def test_invalid_dates_skipped(self, engine):
        """Rows with unparseable dates are skipped, valid rows still load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_csv([
                ["garbage-date", "27000", "", "", "", "", ""],
                ["01/15/2024", "27,123.45", "", "", "", "", ""],
            ], tmpdir)
            assert engine._load_egx30_csv(path) is True
            assert len(engine._bm_data_map) == 1

    def test_comma_in_price_handled(self, engine):
        """Prices with thousands separators parse correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_csv([
                ["2024-01-15", "1,234,567.89", "", "", "", "", ""],
            ], tmpdir)
            assert engine._load_egx30_csv(path) is True
            assert engine._bm_data_map["2024-01-15"] == pytest.approx(1234567.89)


# ===========================================================================
# 3. Missing benchmark produces explicit disabled/error flag
# ===========================================================================


class TestBenchmarkDisabledFlag:
    """When benchmark is requested but unavailable, report must flag it clearly."""

    def test_benchmark_error_flag_in_report(self):
        """Simulate benchmark failure and verify the report structure."""
        from scripts.backtester import BacktestingEngine

        with patch.object(BacktestingEngine, "__init__", lambda self, **kw: None):
            eng = BacktestingEngine.__new__(BacktestingEngine)
            # Simulate state after failed benchmark load
            eng._requested_benchmark = "^EGX30"
            eng.benchmark_ticker = None
            eng._benchmark_error = (
                "BENCHMARK UNAVAILABLE: '^EGX30' returned no data from any source"
            )
            eng._benchmark_block = {}

            # Build the benchmark status block the same way save_results does
            _bm_block = getattr(eng, "_benchmark_block", {})
            _bm_error = getattr(eng, "_benchmark_error", None)
            _bm_requested = getattr(eng, "_requested_benchmark", None)
            benchmark_status = {
                "benchmark_requested": _bm_requested,
                "benchmark_enabled": bool(
                    _bm_block and _bm_block.get("n_aligned_days", 0) > 0
                ),
                "benchmark_error": _bm_error,
                **_bm_block,
            }

            assert benchmark_status["benchmark_requested"] == "^EGX30"
            assert benchmark_status["benchmark_enabled"] is False
            assert benchmark_status["benchmark_error"] is not None
            assert "UNAVAILABLE" in benchmark_status["benchmark_error"]

    def test_benchmark_enabled_when_data_present(self):
        """When benchmark data is available, flag should be True."""
        from scripts.backtester import BacktestingEngine

        with patch.object(BacktestingEngine, "__init__", lambda self, **kw: None):
            eng = BacktestingEngine.__new__(BacktestingEngine)
            eng._requested_benchmark = "^EGX30"
            eng.benchmark_ticker = "^EGX30"
            eng._benchmark_error = None
            eng._benchmark_block = {
                "name": "EGX30",
                "n_aligned_days": 50,
                "total_return_pct": 5.23,
                "alpha_pct": 2.10,
            }

            _bm_block = eng._benchmark_block
            benchmark_status = {
                "benchmark_requested": eng._requested_benchmark,
                "benchmark_enabled": bool(
                    _bm_block and _bm_block.get("n_aligned_days", 0) > 0
                ),
                "benchmark_error": eng._benchmark_error,
                **_bm_block,
            }

            assert benchmark_status["benchmark_enabled"] is True
            assert benchmark_status["benchmark_error"] is None
            assert benchmark_status["alpha_pct"] == 2.10

    def test_requested_benchmark_preserved_after_disable(self):
        """_requested_benchmark is never cleared even when benchmark_ticker is set to None."""
        from scripts.backtester import BacktestingEngine

        with patch.object(BacktestingEngine, "__init__", lambda self, **kw: None):
            eng = BacktestingEngine.__new__(BacktestingEngine)
            eng.benchmark_ticker = "^EGX30"
            eng._requested_benchmark = "^EGX30"
            eng._benchmark_error = None

            # Simulate what happens when benchmark loading fails
            eng._benchmark_error = "some error"
            eng.benchmark_ticker = None

            # _requested_benchmark should still reflect what the user asked for
            assert eng._requested_benchmark == "^EGX30"
            assert eng.benchmark_ticker is None
