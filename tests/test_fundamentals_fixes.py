"""
Priority 5: Proof tests for the five Fund Agent fixes.

Tests:
  1. earnings_yield_spread is non-None when risk_free_rate is wired
  2. earnings_yield_spread is None when risk_free_rate is None
  3. P/E source is "trade_date_price" when current_price provided
  4. P/E source is "csv_fallback" when only CSV value available
  5. P/E source is "unavailable" when neither is available
  6. calibrated_confidence = min(raw_conf, data_confidence + 20)
  7. CalibrationResult has calibrated_confidence field
  8. raw_earnings_direction_confidence preserved, earnings_direction_confidence calibrated
  9. base rate 76% claim — empirical rate within 5pp of 0.76
  10. pe_ratio_source appears in evidence pack narrative
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

import pytest

# Ensure project root is importable
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tradingagents.agents.analysts.fundamentals.financial_calculator import FinancialCalculator
from tradingagents.agents.analysts.fundamentals.calibration import (
    calibrate_earnings_direction,
    CalibrationResult,
)
from tradingagents.agents.analysts.fundamentals.schemas import FundamentalAnalysisReport
from tradingagents.agents.analysts.fundamentals.sector_config import SectorConfig
from tradingagents.agents.analysts.fundamentals.data_cot import (
    build_evidence_pack,
    format_evidence_narrative,
)


# =============================================================================
# Helpers
# =============================================================================

def _minimal_report(**kwargs) -> FundamentalAnalysisReport:
    """Build a minimal valid FundamentalAnalysisReport for testing."""
    defaults = dict(
        ticker="COMI",
        analysis_date="2023-12-31",
        fiscal_period="FY2023",
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
# Test 1: earnings_yield_spread is non-None when risk_free_rate is wired
# =============================================================================

def test_earnings_yield_spread_nonzero_with_risk_free_rate():
    """P/E=10 → EY=10%, risk_free=5% → spread=5% (non-None)."""
    result = FinancialCalculator.compute_all(
        net_income=1_000_000,
        pe_ratio_csv=10.0,
        risk_free_rate=0.05,
    )
    assert result["earnings_yield_spread"] is not None, (
        "earnings_yield_spread should be non-None when risk_free_rate is provided"
    )
    # EY = 1/10 = 0.10, spread = 0.10 - 0.05 = 0.05
    assert abs(result["earnings_yield_spread"] - 0.05) < 1e-6


# =============================================================================
# Test 2: earnings_yield_spread is None when risk_free_rate is None
# =============================================================================

def test_earnings_yield_spread_none_without_risk_free_rate():
    """Without risk_free_rate, spread must be None."""
    result = FinancialCalculator.compute_all(
        net_income=1_000_000,
        pe_ratio_csv=10.0,
        risk_free_rate=None,
    )
    assert result["earnings_yield_spread"] is None, (
        "earnings_yield_spread must be None when risk_free_rate is not provided"
    )


# =============================================================================
# Test 3: _pe_ratio_source = "trade_date_price" when current_price provided
# =============================================================================

def test_pe_source_trade_date_price():
    """When current_price and EPS are provided, P/E is computed live."""
    result = FinancialCalculator.compute_all(
        net_income=1_000_000,
        shares_outstanding=100_000,
        current_price=100.0,       # EPS = 10.0, P/E = 100/10 = 10
    )
    assert result["_pe_ratio_source"] == "trade_date_price", (
        f"Expected 'trade_date_price', got '{result['_pe_ratio_source']}'"
    )
    assert result["pe_ratio"] == pytest.approx(10.0)


# =============================================================================
# Test 4: _pe_ratio_source = "csv_fallback" when only CSV P/E available
# =============================================================================

def test_pe_source_csv_fallback():
    """Without current_price, P/E falls back to CSV value."""
    result = FinancialCalculator.compute_all(
        pe_ratio_csv=15.0,
        current_price=None,
    )
    assert result["_pe_ratio_source"] == "csv_fallback", (
        f"Expected 'csv_fallback', got '{result['_pe_ratio_source']}'"
    )
    assert result["pe_ratio"] == 15.0


# =============================================================================
# Test 5: _pe_ratio_source = "unavailable" when neither price nor CSV
# =============================================================================

def test_pe_source_unavailable():
    """Without current_price and no CSV P/E, source is unavailable."""
    result = FinancialCalculator.compute_all(
        current_price=None,
        pe_ratio_csv=None,
    )
    assert result["_pe_ratio_source"] == "unavailable", (
        f"Expected 'unavailable', got '{result['_pe_ratio_source']}'"
    )
    assert result["pe_ratio"] is None


# =============================================================================
# Test 6: calibrated_confidence = min(raw_conf, data_confidence + 20)
# =============================================================================

def test_calibrated_confidence_formula():
    """
    When raw_conf=90 and data_confidence=60, cap = 60+20 = 80.
    calibrated_confidence should be min(90, 80) = 80.
    """
    cal = calibrate_earnings_direction(
        fundamental_outlook="bullish",
        downside_risk_level="low",
        raw_earnings_direction="up",
        earnings_direction_confidence=90,
        data_confidence=60,
    )
    assert cal.calibrated_confidence == 80, (
        f"Expected calibrated_confidence=80 (min(90, 60+20)), got {cal.calibrated_confidence}"
    )


def test_calibrated_confidence_no_cap_when_data_high():
    """When data_confidence=100, cap = 120; raw_conf=75 is not capped."""
    cal = calibrate_earnings_direction(
        fundamental_outlook="bullish",
        downside_risk_level="low",
        raw_earnings_direction="up",
        earnings_direction_confidence=75,
        data_confidence=100,
    )
    assert cal.calibrated_confidence == 75, (
        f"Expected calibrated_confidence=75 (not capped), got {cal.calibrated_confidence}"
    )


# =============================================================================
# Test 7: CalibrationResult has calibrated_confidence field
# =============================================================================

def test_calibration_result_has_calibrated_confidence_field():
    """CalibrationResult NamedTuple must expose calibrated_confidence."""
    cal = calibrate_earnings_direction(
        fundamental_outlook="bearish",
        downside_risk_level="high",
        raw_earnings_direction="down",
        earnings_direction_confidence=80,
        data_confidence=70,
    )
    assert hasattr(cal, "calibrated_confidence"), (
        "CalibrationResult must have calibrated_confidence field"
    )
    # min(80, 70+20) = min(80, 90) = 80
    assert cal.calibrated_confidence == 80


# =============================================================================
# Test 8: raw confidence preserved, public confidence is calibrated
# =============================================================================

def test_pipeline_raw_vs_calibrated_confidence_in_schema():
    """
    FundamentalAnalysisReport schema must have raw_earnings_direction_confidence field.
    Schema default should be 0; can be set independently of earnings_direction_confidence.
    """
    report = _minimal_report(
        earnings_direction_confidence=70,
        raw_earnings_direction_confidence=90,
    )
    assert report.earnings_direction_confidence == 70
    assert report.raw_earnings_direction_confidence == 90


# =============================================================================
# Test 9: empirical base rate 76% claim verified from CSVs
# =============================================================================

def test_base_rate_76_percent_claim_verified():
    """
    Loads PROOF_OF_WORK_base_rate.json (generated by compute_egx_base_rate.py)
    and asserts the overall up rate is within 5pp of 0.76.
    """
    proof_path = _PROJECT_ROOT / "PROOF_OF_WORK_base_rate.json"
    if not proof_path.exists():
        pytest.skip("PROOF_OF_WORK_base_rate.json not found — run scripts/compute_egx_base_rate.py first")

    with open(proof_path, encoding="utf-8") as f:
        data = json.load(f)

    rate = data.get("overall_up_rate")
    assert rate is not None, "overall_up_rate missing from proof file"
    assert data.get("total_pairs", 0) >= 50, (
        f"Too few pairs ({data.get('total_pairs')}) — data may be incomplete"
    )
    assert abs(rate - 0.76) <= 0.05, (
        f"EGX up base rate {rate:.1%} is outside 5pp of the claimed 76% "
        f"({data['total_up']}/{data['total_pairs']} pairs)"
    )


# =============================================================================
# Test 10: pe_ratio_source appears in evidence pack narrative
# =============================================================================

def test_pe_ratio_source_in_evidence_narrative():
    """
    When a report has pe_ratio_source set, the evidence pack narrative
    must include a P/E annotation ([live price], [stale CSV], or [N/A]).
    """
    sector_cfg = SectorConfig("COMI")

    # Case A: csv_fallback
    report_csv = _minimal_report(
        ratios={"pe_ratio": 12.5},
        pe_ratio_source="csv_fallback",
    )
    pack_csv = build_evidence_pack(report_csv, sector_cfg)
    narrative_csv = pack_csv.get("narrative", "")
    assert "[stale CSV]" in narrative_csv, (
        "Narrative must annotate P/E as '[stale CSV]' when pe_ratio_source='csv_fallback'"
    )

    # Case B: trade_date_price
    report_live = _minimal_report(
        ratios={"pe_ratio": 14.0},
        pe_ratio_source="trade_date_price",
    )
    pack_live = build_evidence_pack(report_live, sector_cfg)
    narrative_live = pack_live.get("narrative", "")
    assert "[live price]" in narrative_live, (
        "Narrative must annotate P/E as '[live price]' when pe_ratio_source='trade_date_price'"
    )

    # Case C: unavailable
    report_na = _minimal_report(
        ratios={},
        pe_ratio_source="unavailable",
    )
    pack_na = build_evidence_pack(report_na, sector_cfg)
    narrative_na = pack_na.get("narrative", "")
    assert "[N/A]" in narrative_na, (
        "Narrative must annotate P/E as '[N/A]' when pe_ratio_source='unavailable'"
    )
