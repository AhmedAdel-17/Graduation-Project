"""
Tests for the date-aware CBE policy rate lookup (rate_lookup.py).

Covers:
  1.  test_risk_free_rate_lookup_uses_latest_rate_before_trade_date
  2.  test_risk_free_rate_lookup_never_uses_future_rate
  3.  test_risk_free_rate_falls_back_to_static_config_when_csv_missing
  4.  test_risk_free_rate_none_when_not_configured
  5.  test_earnings_yield_spread_uses_date_aware_rate
  6.  test_structured_analysis_records_risk_free_rate_source
  7.  test_evidence_pack_marks_date_aware_rate

Edge-case tests:
  8.  test_lookup_exact_effective_date_match
  9.  test_lookup_date_before_all_csv_rows_returns_static_fallback
  10. test_lookup_returns_none_when_csv_is_empty

Verification-metadata tests (new column schema):
  11. test_lookup_accepts_new_column_schema_with_verification_status
  12. test_provisional_rows_accepted_by_lookup
  13. test_synthetic_anchor_row_accepted_by_lookup
  14. test_production_csv_all_rows_parseable

2023 H2 gap-awareness tests (documented gap behavior):
  15. test_2023_h1_returns_jan_2023_row_from_production_csv
  16. test_2023_h2_returns_jan_2023_row_from_production_csv
  17. test_2023_year_end_hold_consistent_with_csv
"""
from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path
from typing import Optional

import pytest

# Ensure project root is on path
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tradingagents.agents.analysts.fundamentals.rate_lookup import (
    get_egx_risk_free_rate_as_of,
    _lookup_rate_from_csv,
)
from tradingagents.agents.analysts.fundamentals.financial_calculator import FinancialCalculator
from tradingagents.agents.analysts.fundamentals.schemas import FundamentalAnalysisReport
from tradingagents.agents.analysts.fundamentals.sector_config import SectorConfig
from tradingagents.agents.analysts.fundamentals.data_cot import build_evidence_pack


# =============================================================================
# Helpers
# =============================================================================

def _write_csv(tmp_path: Path, rows: list[dict]) -> Path:
    """
    Write a cbe_policy_rates.csv to a temp dir and return its path.
    Uses the full new column schema (source_name, source_url,
    verification_status, note).  Rows may supply any subset —
    missing keys default to empty string.
    """
    csv_path = tmp_path / "cbe_policy_rates.csv"
    fieldnames = [
        "effective_date", "rate",
        "source_name", "source_url", "verification_status", "note",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            full_row = {k: "" for k in fieldnames}
            full_row.update(row)
            writer.writerow(full_row)
    return csv_path


def _minimal_report(**kwargs) -> FundamentalAnalysisReport:
    defaults = dict(
        ticker="COMI",
        analysis_date="2022-06-30",
        fiscal_period="FY2021",
        sector="banks",
        ratios={},
        preprocessing={},
        distress_flags=[],
        data_confidence=80,
        signal_coherence=100,
        financial_health="healthy",
        pipeline_mode="deterministic",
        stages_completed=[],
    )
    defaults.update(kwargs)
    return FundamentalAnalysisReport(**defaults)


# =============================================================================
# Test 1: Returns the latest rate on/before trade_date
# =============================================================================

def test_risk_free_rate_lookup_uses_latest_rate_before_trade_date(tmp_path):
    """
    CSV has three rates. trade_date falls between rows 2 and 3.
    Must return row 2's rate (the most recent that is not in the future).
    """
    csv_path = _write_csv(tmp_path, [
        {"effective_date": "2020-03-16", "rate": "0.0925", "source": "CBE", "note": "COVID cut"},
        {"effective_date": "2022-05-19", "rate": "0.1225", "source": "CBE", "note": "hike"},
        {"effective_date": "2022-10-27", "rate": "0.1625", "source": "CBE", "note": "hike"},
    ])

    rate, source, eff_date = get_egx_risk_free_rate_as_of(
        trade_date="2022-07-15",          # between 2022-05-19 and 2022-10-27
        config={},
        rates_csv_path=csv_path,
    )

    assert source == "date_aware_cbe_policy_rate"
    assert rate == pytest.approx(0.1225)
    assert eff_date == "2022-05-19"


# =============================================================================
# Test 2: Future rates are NEVER returned
# =============================================================================

def test_risk_free_rate_lookup_never_uses_future_rate(tmp_path):
    """
    All CSV rows are after trade_date. Must fall through to static config.
    """
    csv_path = _write_csv(tmp_path, [
        {"effective_date": "2023-01-05", "rate": "0.1925", "source": "CBE", "note": ""},
        {"effective_date": "2024-03-06", "rate": "0.2725", "source": "CBE", "note": ""},
    ])

    rate, source, eff_date = get_egx_risk_free_rate_as_of(
        trade_date="2022-01-01",          # before all rows
        config={"egx_risk_free_rate": 0.09},
        rates_csv_path=csv_path,
    )

    # Must NOT use any future rate — falls back to static config
    assert source == "static_config_fallback"
    assert rate == pytest.approx(0.09)
    assert eff_date is None


# =============================================================================
# Test 3: Falls back to static config when CSV file is missing
# =============================================================================

def test_risk_free_rate_falls_back_to_static_config_when_csv_missing(tmp_path):
    """CSV file does not exist — must use static config value."""
    nonexistent = tmp_path / "no_such_file.csv"

    rate, source, eff_date = get_egx_risk_free_rate_as_of(
        trade_date="2023-06-30",
        config={"egx_risk_free_rate": 0.1925},
        rates_csv_path=nonexistent,
    )

    assert source == "static_config_fallback"
    assert rate == pytest.approx(0.1925)
    assert eff_date is None


# =============================================================================
# Test 4: Returns None when not configured and CSV missing
# =============================================================================

def test_risk_free_rate_none_when_not_configured(tmp_path):
    """No CSV, no config value → rate is None, source is 'not_configured'."""
    nonexistent = tmp_path / "no_such_file.csv"

    rate, source, eff_date = get_egx_risk_free_rate_as_of(
        trade_date="2021-12-31",
        config={},                        # no egx_risk_free_rate key
        rates_csv_path=nonexistent,
    )

    assert rate is None
    assert source == "not_configured"
    assert eff_date is None


# =============================================================================
# Test 5: earnings_yield_spread uses the date-aware rate, not static 0.275
# =============================================================================

def test_earnings_yield_spread_uses_date_aware_rate(tmp_path):
    """
    With P/E=10 (EY=10%), a 2020 date-aware rate of 9.25% gives spread=+0.75%.
    If we had used the static 2024 rate (27.5%) instead, spread would be -17.5%.
    Confirms date-aware rate reaches compute_all().
    """
    csv_path = _write_csv(tmp_path, [
        {"effective_date": "2020-03-16", "rate": "0.0925", "source": "CBE", "note": "COVID cut"},
    ])

    date_aware_rate, _, _ = get_egx_risk_free_rate_as_of(
        trade_date="2020-12-31",
        config={"egx_risk_free_rate": 0.275},   # static fallback should NOT be used
        rates_csv_path=csv_path,
    )

    # Date-aware rate for 2020-12-31 should be 9.25%
    assert date_aware_rate == pytest.approx(0.0925)

    # Feed that rate into compute_all (P/E=10 → EY=10%)
    result = FinancialCalculator.compute_all(
        pe_ratio_csv=10.0,
        risk_free_rate=date_aware_rate,
    )
    spread = result["earnings_yield_spread"]
    assert spread is not None
    # EY = 1/10 = 0.10; spread = 0.10 - 0.0925 = 0.0075
    assert abs(spread - 0.0075) < 1e-6, (
        f"Spread should be +0.75% with date-aware 9.25% rate, got {spread:.4f}"
    )


# =============================================================================
# Test 6: structured_analysis records risk_free_rate_source
# =============================================================================

def test_structured_analysis_records_risk_free_rate_source():
    """
    FundamentalAnalysisReport can hold rfr metadata fields.
    Verifies the schema fields exist with correct defaults.
    """
    report = _minimal_report(
        risk_free_rate_value=0.0925,
        risk_free_rate_source="date_aware_cbe_policy_rate",
        risk_free_rate_effective_date="2020-03-16",
    )
    assert report.risk_free_rate_value == pytest.approx(0.0925)
    assert report.risk_free_rate_source == "date_aware_cbe_policy_rate"
    assert report.risk_free_rate_effective_date == "2020-03-16"


def test_structured_analysis_rfr_defaults_empty():
    """Without rfr fields, schema defaults to None/empty — backward compatible."""
    report = _minimal_report()
    assert report.risk_free_rate_value is None
    assert report.risk_free_rate_source == ""
    assert report.risk_free_rate_effective_date == ""


# =============================================================================
# Test 7: Evidence pack narrative marks date-aware rate correctly
# =============================================================================

def test_evidence_pack_marks_date_aware_rate():
    """
    When risk_free_rate_source='date_aware_cbe_policy_rate', the evidence pack
    narrative must include '[date-aware CBE:' annotation on the EY Spread line.
    When 'static_config_fallback', must include 'anachronistic'.
    """
    sector_cfg = SectorConfig("COMI")

    # Case A: date-aware
    report_da = _minimal_report(
        ratios={"pe_ratio": 10.0, "earnings_yield": 0.10, "earnings_yield_spread": 0.0075},
        risk_free_rate_source="date_aware_cbe_policy_rate",
        risk_free_rate_effective_date="2020-03-16",
    )
    pack_da = build_evidence_pack(report_da, sector_cfg)
    narrative_da = pack_da.get("narrative", "")
    assert "[date-aware CBE:" in narrative_da, (
        "Narrative must include '[date-aware CBE:' annotation for date-aware rate"
    )

    # Case B: static fallback
    report_sf = _minimal_report(
        ratios={"pe_ratio": 10.0, "earnings_yield": 0.10, "earnings_yield_spread": -0.175},
        risk_free_rate_source="static_config_fallback",
        risk_free_rate_effective_date="",
    )
    pack_sf = build_evidence_pack(report_sf, sector_cfg)
    narrative_sf = pack_sf.get("narrative", "")
    assert "anachronistic" in narrative_sf, (
        "Narrative must warn 'anachronistic' for static config fallback rate"
    )

    # Case C: not configured — shows "N/A (risk_free_rate not configured)"
    report_nc = _minimal_report(
        ratios={},
        risk_free_rate_source="not_configured",
    )
    pack_nc = build_evidence_pack(report_nc, sector_cfg)
    narrative_nc = pack_nc.get("narrative", "")
    assert "not configured" in narrative_nc.lower(), (
        "Narrative must note that risk_free_rate is not configured"
    )


# =============================================================================
# Edge cases
# =============================================================================

def test_lookup_exact_effective_date_match(tmp_path):
    """trade_date exactly equals effective_date — that row IS included."""
    csv_path = _write_csv(tmp_path, [
        {"effective_date": "2022-07-21", "rate": "0.1425", "source": "CBE", "note": "hike"},
    ])
    rate, source, eff_date = get_egx_risk_free_rate_as_of(
        trade_date="2022-07-21",
        config={},
        rates_csv_path=csv_path,
    )
    assert source == "date_aware_cbe_policy_rate"
    assert rate == pytest.approx(0.1425)
    assert eff_date == "2022-07-21"


def test_lookup_date_before_all_csv_rows_returns_static_fallback(tmp_path):
    """trade_date is before the earliest row → falls back to config."""
    csv_path = _write_csv(tmp_path, [
        {"effective_date": "2020-03-16", "rate": "0.0925", "source": "CBE", "note": ""},
    ])
    rate, source, eff_date = get_egx_risk_free_rate_as_of(
        trade_date="2018-01-01",          # before any CSV row
        config={"egx_risk_free_rate": 0.18},
        rates_csv_path=csv_path,
    )
    assert source == "static_config_fallback"
    assert rate == pytest.approx(0.18)


def test_lookup_returns_none_when_csv_is_empty(tmp_path):
    """Empty CSV (header only) → no rate found → falls back to config."""
    csv_path = tmp_path / "empty.csv"
    # Use the current full column schema header
    csv_path.write_text(
        "effective_date,rate,source_name,source_url,verification_status,note\n",
        encoding="utf-8",
    )

    rate, source, eff_date = get_egx_risk_free_rate_as_of(
        trade_date="2021-06-01",
        config={"egx_risk_free_rate": 0.0925},
        rates_csv_path=csv_path,
    )
    assert source == "static_config_fallback"
    assert rate == pytest.approx(0.0925)


# =============================================================================
# Verification-metadata tests (new column schema — Tests 11–14)
# =============================================================================

def test_lookup_accepts_new_column_schema_with_verification_status(tmp_path):
    """
    Lookup must work correctly when the CSV uses the full audited column schema
    (source_name, source_url, verification_status, note) — i.e., extra columns
    are silently ignored and rate/effective_date are still parsed correctly.
    """
    csv_path = _write_csv(tmp_path, [
        {
            "effective_date": "2022-10-27",
            "rate": "0.1625",
            "source_name": "CBE MPC decision",
            "source_url": "https://www.cbe.org.eg/en/monetary-policy/mpc-decisions",
            "verification_status": "high_confidence_provisional",
            "note": "Hike 200bps; overnight deposit rate 16.25%",
        },
    ])
    rate, source, eff_date = get_egx_risk_free_rate_as_of(
        trade_date="2022-12-31",
        config={},
        rates_csv_path=csv_path,
    )
    assert source == "date_aware_cbe_policy_rate"
    assert rate == pytest.approx(0.1625)
    assert eff_date == "2022-10-27"


def test_provisional_rows_accepted_by_lookup(tmp_path):
    """
    Rows with verification_status='provisional' must be accepted and returned
    by the lookup — provisional status is a documentation tag only and must NOT
    prevent the rate from being used.
    """
    csv_path = _write_csv(tmp_path, [
        {
            "effective_date": "2019-02-14",
            "rate": "0.1425",
            "source_name": "CBE MPC decision",
            "source_url": "https://www.cbe.org.eg/en/monetary-policy/mpc-decisions",
            "verification_status": "provisional",
            "note": "Cut ~100bps; overnight deposit rate 14.25%. PROVISIONAL.",
        },
    ])
    rate, source, eff_date = get_egx_risk_free_rate_as_of(
        trade_date="2019-06-30",
        config={"egx_risk_free_rate": 0.275},   # static fallback must NOT win
        rates_csv_path=csv_path,
    )
    # Provisional rows are accepted — source must be date_aware, not static
    assert source == "date_aware_cbe_policy_rate", (
        "Provisional rows must be accepted; source should be 'date_aware_cbe_policy_rate'"
    )
    assert rate == pytest.approx(0.1425)


def test_synthetic_anchor_row_accepted_by_lookup(tmp_path):
    """
    Rows with verification_status='synthetic_anchor' (e.g. 2019-01-01) are
    still valid for the lookup — the lookup uses effective_date and rate only
    and does not filter on verification_status.
    """
    csv_path = _write_csv(tmp_path, [
        {
            "effective_date": "2019-01-01",
            "rate": "0.1525",
            "source_name": "CBE MPC (series anchor)",
            "source_url": "https://www.cbe.org.eg/en/monetary-policy/mpc-decisions",
            "verification_status": "synthetic_anchor",
            "note": "NOT a real MPC decision date. Synthetic anchor.",
        },
    ])
    rate, source, eff_date = get_egx_risk_free_rate_as_of(
        trade_date="2019-01-15",
        config={},
        rates_csv_path=csv_path,
    )
    assert source == "date_aware_cbe_policy_rate"
    assert rate == pytest.approx(0.1525)
    assert eff_date == "2019-01-01"


def test_production_csv_all_rows_parseable():
    """
    Load the actual production cbe_policy_rates.csv and verify:
    - All rows parse without errors
    - All rate values are valid floats in a plausible range (0.05 – 0.40)
    - effective_dates are valid ISO strings (YYYY-MM-DD)
    - No future rates bleed through when querying 2020-12-31
    """
    from tradingagents.agents.analysts.fundamentals.rate_lookup import (
        _DEFAULT_RATES_CSV,
        _lookup_rate_from_csv,
    )
    assert _DEFAULT_RATES_CSV.exists(), (
        f"Production CSV not found at expected path: {_DEFAULT_RATES_CSV}"
    )

    # Parse every row and check validity
    import csv as csv_module
    with open(_DEFAULT_RATES_CSV, newline="", encoding="utf-8") as f:
        reader = csv_module.DictReader(f)
        rows = list(reader)

    assert len(rows) >= 10, f"Expected at least 10 rows, got {len(rows)}"

    for row in rows:
        date_str = row["effective_date"].strip()
        rate_str = row["rate"].strip()
        # Date must be YYYY-MM-DD parseable
        from datetime import datetime
        parsed = datetime.strptime(date_str, "%Y-%m-%d")
        assert parsed.year >= 2018, f"Unexpectedly old date: {date_str}"
        # Rate must be a float in a sane range for Egypt
        rate_val = float(rate_str)
        assert 0.05 <= rate_val <= 0.40, (
            f"Rate {rate_val:.4f} on {date_str} is outside plausible 5%–40% range"
        )

    # Temporal safety: 2020-12-31 must return 9.25% (COVID cut row), not 2024 rate
    # _lookup_rate_from_csv returns (rate, effective_date) — 2-tuple
    rate_2020, eff_2020 = _lookup_rate_from_csv(_DEFAULT_RATES_CSV, "2020-12-31")
    assert rate_2020 == pytest.approx(0.0925), (
        f"2020-12-31 should return 9.25% (post-COVID-cut), got {rate_2020}"
    )
    assert eff_2020 == "2020-03-16"

    # Temporal safety: 2024-12-31 must return 27.25% (latest row)
    rate_2024, eff_2024 = _lookup_rate_from_csv(_DEFAULT_RATES_CSV, "2024-12-31")
    assert rate_2024 == pytest.approx(0.2725), (
        f"2024-12-31 should return 27.25%, got {rate_2024}"
    )
    assert eff_2024 == "2024-03-06"


# =============================================================================
# 2023 H2 gap-awareness tests (Tests 15–17)
# =============================================================================

def test_2023_h1_returns_jan_2023_row_from_production_csv():
    """
    2023-06-30 is AFTER the 2023-01-05 row and BEFORE the 2024-03-06 row.
    With no intermediate 2023 rows in the production CSV, the lookup must
    return the 2023-01-05 rate (19.25%) — the last known rate before that date.

    This is the EXPECTED documented behavior for the known 2023 gap.
    The test does not assert that 19.25% is the true historical rate for
    2023-H1; it asserts that the lookup correctly uses the latest available
    row on/before the trade date, and does not bleed in the future 2024 row.
    """
    from tradingagents.agents.analysts.fundamentals.rate_lookup import (
        _DEFAULT_RATES_CSV,
        _lookup_rate_from_csv,
    )
    assert _DEFAULT_RATES_CSV.exists(), (
        f"Production CSV not found: {_DEFAULT_RATES_CSV}"
    )

    rate_h1, eff_h1 = _lookup_rate_from_csv(_DEFAULT_RATES_CSV, "2023-06-30")

    # Must return 2023-01-05 row, NOT the 2024-03-06 row
    assert rate_h1 == pytest.approx(0.1925), (
        f"2023-06-30 should return 19.25% (2023-01-05 row, last known before this date), "
        f"got {rate_h1}. If the 2024-03-06 row (27.25%) was returned, that is temporal leakage."
    )
    assert eff_h1 == "2023-01-05", (
        f"effective_date must be '2023-01-05', got '{eff_h1}'"
    )


def test_2023_h2_returns_jan_2023_row_from_production_csv():
    """
    2023-09-30 is in the known gap between the 2023-01-05 row and the
    2024-03-06 row. The lookup must return the 2023-01-05 rate (19.25%).

    A repo search (2026-05-02) confirmed no intermediate 2023 CBE rate
    levels exist in this codebase. Any intermediate hikes in 2023 must be
    sourced from official CBE MPC press releases before they can be added.
    """
    from tradingagents.agents.analysts.fundamentals.rate_lookup import (
        _DEFAULT_RATES_CSV,
        _lookup_rate_from_csv,
    )
    assert _DEFAULT_RATES_CSV.exists(), (
        f"Production CSV not found: {_DEFAULT_RATES_CSV}"
    )

    rate_h2, eff_h2 = _lookup_rate_from_csv(_DEFAULT_RATES_CSV, "2023-09-30")

    assert rate_h2 == pytest.approx(0.1925), (
        f"2023-09-30 should return 19.25% (2023-01-05 row — last known rate before this date). "
        f"No intermediate 2023 rows exist in the production CSV (documented gap). "
        f"If this fails with 27.25%, a future-dated 2024 row is leaking through."
    )
    assert eff_h2 == "2023-01-05"


def test_2023_year_end_hold_consistent_with_csv():
    """
    Internal fixture `scripts/test_bull_researcher.py:106` contains:
        "Central Bank of Egypt held rates steady (CBE press release, 2023-12-28)"
    This confirms CBE did NOT cut rates at year-end 2023.

    The production CSV has no 2023-12-28 row (CBE held, so no new effective_date).
    The lookup for 2023-12-28 must therefore return the 2023-01-05 row (19.25%),
    which is consistent with the "held steady" evidence — if rates were held,
    the most recent change is still the 2023-01-05 emergency hike.

    NOTE: This fixture does not establish the numeric rate for 2023-12-28 with
    certainty (CBE may have hiked earlier in 2023 H2 before holding at year-end).
    The test verifies lookup correctness only, not historical accuracy.
    """
    from tradingagents.agents.analysts.fundamentals.rate_lookup import (
        _DEFAULT_RATES_CSV,
        _lookup_rate_from_csv,
    )
    assert _DEFAULT_RATES_CSV.exists(), (
        f"Production CSV not found: {_DEFAULT_RATES_CSV}"
    )

    rate_ye, eff_ye = _lookup_rate_from_csv(_DEFAULT_RATES_CSV, "2023-12-28")

    # CBE held steady at year-end 2023 per internal fixture.
    # With no 2023-12-28 row, the last known rate is 2023-01-05 = 19.25%.
    assert rate_ye == pytest.approx(0.1925), (
        f"2023-12-28 should return 19.25% (2023-01-05 row). "
        f"Internal fixture confirms 'held rates steady' at this date — "
        f"consistent with no newer row in the CSV."
    )
    assert eff_ye == "2023-01-05"
