"""Quarterly-mode tests for the fundamentals pipeline.

Locks the quarterly-specific code paths that are infrastructure-ready
but not yet invoked in production (fundamentals_analyst.py always passes
freq="annual"). These tests prevent silent regression.

Quarterly behavior under test:
  - Evidence narrative: "ANALYSIS MODE: QUARTERLY", QoQ labels,
    near-zero crossing warning, unavailable QoQ note
  - Pipeline: freq="quarterly" flows through to build_evidence_pack
    and calibrate_earnings_direction
  - Filing lag: quarterly uses 45d (configurable) vs annual 120d
  - Calibration: freq param accepted (currently unused, future-proofing)

Pure-unit: LLM calls are mocked. No network.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tradingagents.agents.analysts.fundamentals.data_cot import format_evidence_narrative
from tradingagents.agents.analysts.fundamentals.calibration import (
    calibrate_earnings_direction,
    CalibrationResult,
    POLICY_NAME,
)
from tradingagents.agents.analysts.fundamentals.pipeline import run_cot_pipeline
from tradingagents.agents.analysts.fundamentals.schemas import FundamentalAnalysisReport
from tradingagents.agents.analysts.fundamentals.data_loader import load_multi_period


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_evidence_pack(**overrides) -> dict:
    """Create a minimal evidence pack for narrative formatting tests."""
    defaults = {
        "ticker": "COMI.CA",
        "analysis_date": "2024-06-01",
        "fiscal_period": "Q1-2024",
        "sector": "banks",
        "sector_context": {},
        "ratios": {"roe": 0.15, "debt_to_equity": 2.0},
        "directions": {},
        "distress_flags": [],
        "data_confidence": 70,
        "signal_coherence": 85,
        "freq": "quarterly",
        "revenue_growth_yoy": 0.10,
        "net_income_growth_yoy": 0.08,
        "common_size_income": {},
        "common_size_balance": {},
        "piotroski_score": None,
        "financial_health_heuristic": "healthy",
        "pe_ratio_source": "",
        "risk_free_rate_source": "",
        "risk_free_rate_effective_date": "",
        "supplemental_context": {},
    }
    defaults.update(overrides)
    return defaults


def _make_report(**overrides) -> FundamentalAnalysisReport:
    """Create a minimal valid FundamentalAnalysisReport for testing."""
    defaults = {
        "ticker": "COMI.CA",
        "analysis_date": "2024-06-01",
        "fiscal_period": "Q1-2024",
        "sector": "banks",
        "ratios": {"roe": 0.15, "debt_to_equity": 2.0},
        "data_confidence": 70,
        "signal_coherence": 85,
        "financial_health": "healthy",
        "key_risks": ["EGX structural risk"],
    }
    defaults.update(overrides)
    return FundamentalAnalysisReport(**defaults)


# ─────────────────────────────────────────────────────────────────────────────
# Evidence narrative: quarterly vs annual
# ─────────────────────────────────────────────────────────────────────────────


class TestQuarterlyEvidenceNarrative:
    def test_quarterly_narrative_has_qoq_header(self):
        """Quarterly pack → narrative contains QUARTERLY header and QoQ labels."""
        pack = _make_evidence_pack(freq="quarterly")
        narrative = format_evidence_narrative(pack)
        assert "ANALYSIS MODE: QUARTERLY" in narrative
        assert "QoQ" in narrative

    def test_annual_narrative_has_yoy_header(self):
        """Annual pack → narrative contains ANNUAL header and YoY labels."""
        pack = _make_evidence_pack(freq="annual")
        narrative = format_evidence_narrative(pack)
        assert "ANALYSIS MODE: ANNUAL" in narrative
        assert "YoY" in narrative
        assert "QoQ" not in narrative

    def test_quarterly_near_zero_crossing_warning(self):
        """QoQ > 200% magnitude → near-zero crossing warning in narrative."""
        pack = _make_evidence_pack(freq="quarterly", net_income_growth_yoy=2.5)
        narrative = format_evidence_narrative(pack)
        assert "near-zero crossing" in narrative

    def test_quarterly_no_warning_moderate_qoq(self):
        """QoQ = 30% → no near-zero crossing warning."""
        pack = _make_evidence_pack(freq="quarterly", net_income_growth_yoy=0.3)
        narrative = format_evidence_narrative(pack)
        assert "near-zero crossing" not in narrative

    def test_quarterly_unavailable_qoq_note(self):
        """QoQ = None → unavailable note in narrative."""
        pack = _make_evidence_pack(freq="quarterly", net_income_growth_yoy=None)
        narrative = format_evidence_narrative(pack)
        assert "unavailable" in narrative.lower()

    def test_annual_no_near_zero_crossing_warning(self):
        """Annual mode with large growth does NOT trigger QoQ near-zero warning."""
        pack = _make_evidence_pack(freq="annual", net_income_growth_yoy=2.5)
        narrative = format_evidence_narrative(pack)
        assert "near-zero crossing" not in narrative


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline: freq passthrough
# ─────────────────────────────────────────────────────────────────────────────


class TestQuarterlyPipelinePassthrough:
    def test_quarterly_freq_passed_to_evidence_pack(self):
        """run_cot_pipeline(freq='quarterly') passes freq to build_evidence_pack."""
        report = _make_report()

        with patch(
            "tradingagents.agents.analysts.fundamentals.pipeline.build_evidence_pack",
            return_value={"_valid": False, "_validation_errors": ["test"]},
        ) as mock_build, patch(
            "tradingagents.agents.analysts.fundamentals.pipeline.get_config",
            return_value={"use_fundamental_memory": False},
        ):
            run_cot_pipeline(
                quick_llm=MagicMock(),
                deep_llm=MagicMock(),
                report=report,
                sector_cfg=MagicMock(),
                freq="quarterly",
            )

        # Verify freq="quarterly" was passed
        mock_build.assert_called_once()
        call_kwargs = mock_build.call_args
        assert call_kwargs.kwargs.get("freq") == "quarterly" or \
            (len(call_kwargs.args) >= 3 and call_kwargs.args[2] == "quarterly")

    def test_quarterly_freq_passed_to_calibration(self):
        """All stages succeed with freq='quarterly' → calibration called with freq='quarterly'."""
        report = _make_report(sector="operational", data_confidence=70)

        valid_concept = {
            "_valid": True,
            "financial_health": "healthy",
            "risk_factors": [],
        }
        valid_thesis = {
            "_valid": True,
            "financial_health": "healthy",
            "fundamental_outlook": "bullish",
            "downside_risk_level": "low",
            "earnings_direction": "up",
            "earnings_direction_confidence": 80,
            "thesis_text": "Strong quarterly momentum.",
            "key_risks": [],
            "egx_specific_risks": [],
            "valuation_assessment": "fairly valued",
        }

        with patch(
            "tradingagents.agents.analysts.fundamentals.pipeline.build_evidence_pack",
            return_value={"_valid": True, "narrative": "evidence text"},
        ), patch(
            "tradingagents.agents.analysts.fundamentals.pipeline.run_concept_cot",
            return_value=valid_concept,
        ), patch(
            "tradingagents.agents.analysts.fundamentals.pipeline.run_thesis_cot",
            return_value=valid_thesis,
        ), patch(
            "tradingagents.agents.analysts.fundamentals.pipeline.calibrate_earnings_direction",
            wraps=calibrate_earnings_direction,
        ) as mock_cal, patch(
            "tradingagents.agents.analysts.fundamentals.pipeline.get_config",
            return_value={"use_fundamental_memory": False},
        ):
            result = run_cot_pipeline(
                quick_llm=MagicMock(),
                deep_llm=MagicMock(),
                report=report,
                sector_cfg=MagicMock(),
                freq="quarterly",
            )

        mock_cal.assert_called_once()
        assert mock_cal.call_args.kwargs.get("freq") == "quarterly"
        assert result.pipeline_mode == "cot_full"


# ─────────────────────────────────────────────────────────────────────────────
# Filing lag: config override for quarterly
# ─────────────────────────────────────────────────────────────────────────────


def _write_csv(path: Path, content: str):
    path.write_text(content.strip() + "\n")


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


class TestQuarterlyFilingLag:
    def test_quarterly_filing_lag_config_override(self, patch_data_dir):
        """Config filing_lag_quarterly_days=30 → uses 30d lag instead of 45d."""
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_quarterly.csv"
        # Row at 2024-03-31: with 45d lag from 2024-05-01, cutoff = 2024-03-17 → passes
        # Row at 2024-03-31: with 30d lag from 2024-05-01, cutoff = 2024-04-01 → FAILS (2024-03-31 < 2024-04-01)
        # Row at 2024-03-15: with 30d lag, cutoff = 2024-04-01 → FAILS
        # Row at 2023-12-31: always passes both lags
        _write_csv(csv_path, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2024-03-31,1500,900,600,400
2023-12-31,1400,850,580,390""")

        # Default 45d lag: curr_date=2024-05-20, cutoff=2024-04-05 → 2024-03-31 fails, 2023-12-31 passes
        # With 30d lag: curr_date=2024-05-20, cutoff=2024-04-20 → 2024-03-31 fails, 2023-12-31 passes
        # Need a date where 45d and 30d give different results.
        # curr_date=2024-05-16: 45d cutoff=2024-04-01 → 2024-03-31 fails; 30d cutoff=2024-04-16 → 2024-03-31 fails
        # curr_date=2024-06-01: 45d cutoff=2024-04-17 → 2024-03-31 passes; 30d cutoff=2024-05-02 → 2024-03-31 passes
        # Need: curr_date where 45d passes 2024-03-31 but 30d doesn't.
        # 45d: curr_date - 45d <= 2024-03-31 → curr_date >= 2024-05-15
        # 30d: curr_date - 30d <= 2024-03-31 → curr_date >= 2024-04-30
        # So between 2024-04-30 and 2024-05-14, 30d passes but 45d fails. Wait, reversed:
        # Filter: period_end_date <= curr_date - lag_days
        # 45d: 2024-03-31 <= curr_date - 45 → need curr_date >= 2024-05-15
        # 30d: 2024-03-31 <= curr_date - 30 → need curr_date >= 2024-04-30
        # So at curr_date=2024-05-10: 30d passes (cutoff=2024-04-10, 2024-03-31 passes),
        #                              45d fails (cutoff=2024-03-26, 2024-03-31 fails)
        # Perfect: at curr_date=2024-05-10, default 45d gets only 2023-12-31 (1 row),
        # but 30d override gets both rows (2).

        # First verify default 45d behavior
        result_default = load_multi_period("COMI.CA", curr_date="2024-05-10", freq="quarterly")
        diag = result_default["diagnostics"]["income"]
        assert diag["date_filter_method"] == "filing_lag_45d"
        assert diag["rows_after_date_filter"] == 1  # only 2023-12-31 passes

        # Now override to 30d
        with patch(
            "tradingagents.agents.analysts.fundamentals.data_loader.get_config",
            return_value={"filing_lag_quarterly_days": 30},
        ):
            result_override = load_multi_period("COMI.CA", curr_date="2024-05-10", freq="quarterly")

        diag_override = result_override["diagnostics"]["income"]
        assert diag_override["date_filter_method"] == "filing_lag_30d"
        assert diag_override["rows_after_date_filter"] == 2  # both rows pass with 30d lag


# ─────────────────────────────────────────────────────────────────────────────
# Calibration: quarterly freq acceptance
# ─────────────────────────────────────────────────────────────────────────────


class TestQuarterlyCalibration:
    def test_calibration_accepts_quarterly_freq(self):
        """calibrate_earnings_direction(freq='quarterly') returns valid result."""
        r = calibrate_earnings_direction(
            fundamental_outlook="bullish",
            downside_risk_level="low",
            raw_earnings_direction="up",
            earnings_direction_confidence=80,
            freq="quarterly",
        )
        assert isinstance(r, CalibrationResult)
        assert r.calibrated_direction == "up"
        assert r.policy == POLICY_NAME

    def test_calibration_quarterly_down_gate_same_as_annual(self):
        """Quarterly freq does not change the down-gate behavior (same policy)."""
        r = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="high",
            raw_earnings_direction="down",
            earnings_direction_confidence=80,
            freq="quarterly",
            sector="operational",
            de_ratio=1.5,
        )
        assert r.calibrated_direction == "down"
