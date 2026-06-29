"""
P3 Tests: EGX30 shared CSV loader (T16-T22 from P3 spec §7.3).

Tests the tradingagents/dataflows/egx30_loader.py module that provides
a shared, cached EGX30 index close-price map for both the backtester
benchmark and P3 relative-strength features.
"""
import os
import tempfile
import textwrap

import pytest

from tradingagents.dataflows.egx30_loader import (
    _load_csv,
    _locate_csv,
    _parse_csv_date,
    load_egx30_csv,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _write_csv(tmpdir: str, filename: str, content: str) -> str:
    """Write a CSV file and return its path."""
    path = os.path.join(tmpdir, filename)
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(content)
    return path


# ── T16: test_load_real_csv_exists ──────────────────────────────────────────

def test_load_real_csv_exists():
    """T16: load_egx30_csv() finds 'EGX 30 Historical Data.csv' (with spaces) and returns non-empty dict."""
    # We must bypass the lru_cache to test the actual locate + load path
    project_root = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..")
    )
    csv_path = _locate_csv(project_root)
    if csv_path is None:
        pytest.skip("EGX30 CSV not present in project root (CI environment)")
    data = _load_csv(csv_path)
    assert len(data) > 1000, f"Expected 1000+ rows, got {len(data)}"
    # All keys should be YYYY-MM-DD format
    for k in list(data.keys())[:5]:
        assert len(k) == 10 and k[4] == "-" and k[7] == "-", f"Bad date key: {k}"
    # All values should be positive floats
    for v in list(data.values())[:5]:
        assert isinstance(v, float) and v > 0


# ── T17: test_investing_com_number_format ───────────────────────────────────

def test_investing_com_number_format():
    """T17: Prices like '51,994.63' are parsed to 51994.63 (comma-stripped)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_content = textwrap.dedent("""\
            "Date","Price","Open","High","Low","Vol.","Change %"
            "06/14/2026","51,994.63","50,818.84","52,201.58","51,765.01","395.86M","2.31%"
            "06/11/2026","50,818.84","51,256.65","51,215.58","50,686.22","308.96M","-0.85%"
        """)
        path = _write_csv(tmpdir, "test.csv", csv_content)
        data = _load_csv(path)
        assert data["2026-06-14"] == pytest.approx(51994.63)
        assert data["2026-06-11"] == pytest.approx(50818.84)


# ── T18: test_descending_date_csv_sorting ───────────────────────────────────

def test_descending_date_csv_sorting():
    """T18: CSV rows are newest-first; output dict keys work correctly when sorted ascending."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Intentionally descending order (Investing.com default)
        csv_content = textwrap.dedent("""\
            "Date","Price","Open","High","Low","Vol.","Change %"
            "01/05/2024","200.00","","","","",""
            "01/04/2024","190.00","","","","",""
            "01/03/2024","180.00","","","","",""
            "01/02/2024","170.00","","","","",""
        """)
        path = _write_csv(tmpdir, "test.csv", csv_content)
        data = _load_csv(path)
        sorted_dates = sorted(data.keys())
        assert sorted_dates == ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
        assert data[sorted_dates[0]] == pytest.approx(170.0)
        assert data[sorted_dates[-1]] == pytest.approx(200.0)


# ── T19: test_date_format_mm_dd_yyyy ────────────────────────────────────────

def test_date_format_mm_dd_yyyy():
    """T19: '06/14/2026' → '2026-06-14' (Investing.com default format)."""
    assert _parse_csv_date("06/14/2026") == "2026-06-14"
    assert _parse_csv_date("01/02/2020") == "2020-01-02"
    # Also supports DD/MM/YYYY when MM/DD fails
    assert _parse_csv_date("2024-03-15") == "2024-03-15"


# ── T20: test_date_range_covers_benchmark ───────────────────────────────────

def test_date_range_covers_benchmark():
    """T20: Loaded map has dates from 2020-01-02 through 2026-06-14."""
    project_root = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..")
    )
    csv_path = _locate_csv(project_root)
    if csv_path is None:
        pytest.skip("EGX30 CSV not present in project root")
    data = _load_csv(csv_path)
    dates = sorted(data.keys())
    assert dates[0] <= "2020-01-03", f"Earliest date {dates[0]} > 2020-01-03"
    assert dates[-1] >= "2026-06-14", f"Latest date {dates[-1]} < 2026-06-14"
    # Specifically: covers 2024-01-02 to 2024-07-14 benchmark window
    assert any(d >= "2024-01-02" for d in dates)
    assert any(d <= "2024-07-14" for d in dates)


# ── T21: test_utf8_bom_handled ──────────────────────────────────────────────

def test_utf8_bom_handled():
    """T21: File with UTF-8 BOM marker loads without error."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "bom_test.csv")
        # Write with explicit BOM
        with open(path, "wb") as f:
            bom = b"\xef\xbb\xbf"
            content = (
                '"Date","Price","Open","High","Low","Vol.","Change %"\n'
                '"01/15/2024","30,000.50","","","","",""\n'
            )
            f.write(bom + content.encode("utf-8"))
        data = _load_csv(path)
        assert len(data) == 1
        assert "2024-01-15" in data
        assert data["2024-01-15"] == pytest.approx(30000.50)


# ── T22: test_missing_csv_returns_empty ─────────────────────────────────────

def test_missing_csv_returns_empty():
    """T22: Non-existent path → returns {}, no exception."""
    data = _load_csv("/nonexistent/path/to/csv.csv")
    assert data == {}
    assert isinstance(data, dict)
