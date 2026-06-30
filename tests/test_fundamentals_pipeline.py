"""Regression tests for the 3-stage CoT pipeline and calibration.

Locks:
  - Stage 1 below threshold → deterministic output, 0 LLM calls
  - Stage 2 failure → Stage 1 + heuristic health (partial)
  - Stage 3 failure → Stage 1 + Stage 2 concept (partial)
  - All stages succeed → full output with cot_full mode
  - Calibration: base-rate default to "up"
  - Calibration: banks always "up"
  - Calibration: high D/E always "up"
  - Calibration: "down" kept only when bearish + high risk + conf >= 75
  - Calibration: confidence cap = min(raw, data_confidence + 20)
  - Calibration: flat → up

Pure-unit: LLM calls are mocked. No network.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tradingagents.agents.analysts.fundamentals.calibration import (
    calibrate_earnings_direction,
    CalibrationResult,
    POLICY_NAME,
    _DEFAULT_CALIBRATED_UP_CONFIDENCE,
)
from tradingagents.agents.analysts.fundamentals.schemas import FundamentalAnalysisReport
from tradingagents.agents.analysts.fundamentals.pipeline import run_cot_pipeline


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_report(**overrides) -> FundamentalAnalysisReport:
    """Create a minimal valid FundamentalAnalysisReport for testing."""
    defaults = {
        "ticker": "COMI.CA",
        "analysis_date": "2024-06-01",
        "fiscal_period": "FY2023",
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
# Calibration: Base-rate rules
# ─────────────────────────────────────────────────────────────────────────────


class TestCalibrationBaseRate:
    def test_default_up_when_no_raw_direction(self):
        """No valid raw direction → default to 'up'."""
        r = calibrate_earnings_direction(
            fundamental_outlook="neutral",
            downside_risk_level="moderate",
            raw_earnings_direction="",
            earnings_direction_confidence=50,
        )
        assert r.calibrated_direction == "up"
        assert r.policy == POLICY_NAME

    def test_up_stays_up(self):
        """raw=up is kept as-is."""
        r = calibrate_earnings_direction(
            fundamental_outlook="bullish",
            downside_risk_level="low",
            raw_earnings_direction="up",
            earnings_direction_confidence=80,
        )
        assert r.calibrated_direction == "up"

    def test_flat_calibrated_to_up(self):
        """raw=flat → calibrated to 'up', confidence set to _DEFAULT_CALIBRATED_UP_CONFIDENCE."""
        r = calibrate_earnings_direction(
            fundamental_outlook="neutral",
            downside_risk_level="moderate",
            raw_earnings_direction="flat",
            earnings_direction_confidence=65,
        )
        assert r.calibrated_direction == "up"
        assert r.calibrated_confidence == _DEFAULT_CALIBRATED_UP_CONFIDENCE


class TestCalibrationSectorGates:
    def test_banks_always_up(self):
        """Banks sector: down → up regardless of other signals."""
        r = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="high",
            raw_earnings_direction="down",
            earnings_direction_confidence=90,
            sector="banks",
        )
        assert r.calibrated_direction == "up"
        assert r.calibrated_confidence == _DEFAULT_CALIBRATED_UP_CONFIDENCE

    def test_high_de_ratio_always_up(self):
        """D/E > 4 → up regardless of other signals."""
        r = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="high",
            raw_earnings_direction="down",
            earnings_direction_confidence=90,
            sector="operational",
            de_ratio=5.0,
        )
        assert r.calibrated_direction == "up"
        assert r.calibrated_confidence == _DEFAULT_CALIBRATED_UP_CONFIDENCE

    def test_de_at_boundary_not_excluded(self):
        """D/E exactly 4.0 is NOT excluded (gate is > 4, not >=)."""
        r = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="high",
            raw_earnings_direction="down",
            earnings_direction_confidence=90,
            sector="operational",
            de_ratio=4.0,
        )
        # D/E = 4.0 passes the gate, and with bearish+high+conf>=75 → down
        assert r.calibrated_direction == "down"


class TestCalibrationDownGate:
    def test_down_kept_with_full_evidence(self):
        """raw=down + bearish + high risk + conf>=75 + non-bank + low D/E → kept."""
        r = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="high",
            raw_earnings_direction="down",
            earnings_direction_confidence=80,
            sector="operational",
            de_ratio=1.5,
        )
        assert r.calibrated_direction == "down"

    def test_down_rejected_low_confidence(self):
        """raw=down but conf < 75 → calibrated to up."""
        r = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="high",
            raw_earnings_direction="down",
            earnings_direction_confidence=70,
            sector="operational",
        )
        assert r.calibrated_direction == "up"

    def test_down_rejected_neutral_outlook(self):
        """raw=down but outlook=neutral → calibrated to up."""
        r = calibrate_earnings_direction(
            fundamental_outlook="neutral",
            downside_risk_level="high",
            raw_earnings_direction="down",
            earnings_direction_confidence=85,
            sector="operational",
        )
        assert r.calibrated_direction == "up"

    def test_down_rejected_moderate_risk(self):
        """raw=down but risk=moderate → calibrated to up."""
        r = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="moderate",
            raw_earnings_direction="down",
            earnings_direction_confidence=85,
            sector="operational",
        )
        assert r.calibrated_direction == "up"


class TestCalibrationConfidenceCap:
    def test_confidence_capped_by_data_confidence(self):
        """Calibrated confidence = min(raw_conf, data_confidence + 20)."""
        r = calibrate_earnings_direction(
            fundamental_outlook="bullish",
            downside_risk_level="low",
            raw_earnings_direction="up",
            earnings_direction_confidence=95,
            data_confidence=60,
        )
        # Cap = 60 + 20 = 80. min(95, 80) = 80.
        assert r.calibrated_confidence == 80

    def test_confidence_not_capped_when_below(self):
        """If raw conf < data_confidence + 20, no cap applied."""
        r = calibrate_earnings_direction(
            fundamental_outlook="bullish",
            downside_risk_level="low",
            raw_earnings_direction="up",
            earnings_direction_confidence=50,
            data_confidence=60,
        )
        # Cap = 80. min(50, 80) = 50. No change.
        assert r.calibrated_confidence == 50


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline: Fallback chain
# ─────────────────────────────────────────────────────────────────────────────


class TestPipelineFallback:
    """Test fallback behavior when stages fail.

    Mocks the stage functions to simulate failures without needing real LLMs.
    """

    def test_stage1_below_threshold_returns_deterministic(self):
        """Stage 1 validation failure → deterministic output, 0 LLM calls."""
        report = _make_report(data_confidence=10)  # Below typical threshold

        with patch(
            "tradingagents.agents.analysts.fundamentals.pipeline.build_evidence_pack",
            return_value={"_valid": False, "_validation_errors": ["data_confidence too low"]},
        ), patch(
            "tradingagents.agents.analysts.fundamentals.pipeline.get_config",
            return_value={"use_fundamental_memory": False},
        ):
            result = run_cot_pipeline(
                quick_llm=MagicMock(),
                deep_llm=MagicMock(),
                report=report,
                sector_cfg=MagicMock(),
            )

        assert result.pipeline_mode == "deterministic"
        assert result.stages_completed == []

    def test_stage2_crash_returns_partial(self):
        """Stage 2 exception → partial report with only Stage 1 data."""
        report = _make_report()

        with patch(
            "tradingagents.agents.analysts.fundamentals.pipeline.build_evidence_pack",
            return_value={"_valid": True, "narrative": "evidence text"},
        ), patch(
            "tradingagents.agents.analysts.fundamentals.pipeline.run_concept_cot",
            side_effect=RuntimeError("LLM timeout"),
        ), patch(
            "tradingagents.agents.analysts.fundamentals.pipeline.get_config",
            return_value={"use_fundamental_memory": False},
        ):
            result = run_cot_pipeline(
                quick_llm=MagicMock(),
                deep_llm=MagicMock(),
                report=report,
                sector_cfg=MagicMock(),
            )

        assert result.pipeline_mode == "cot_partial"
        assert result.stages_completed == ["data_cot"]

    def test_stage3_crash_returns_partial_with_concept(self):
        """Stage 3 exception → partial report with Stage 1 + Stage 2 data."""
        report = _make_report()

        valid_concept = {
            "_valid": True,
            "financial_health": "concerning",
            "risk_factors": ["high leverage"],
        }

        with patch(
            "tradingagents.agents.analysts.fundamentals.pipeline.build_evidence_pack",
            return_value={"_valid": True, "narrative": "evidence text"},
        ), patch(
            "tradingagents.agents.analysts.fundamentals.pipeline.run_concept_cot",
            return_value=valid_concept,
        ), patch(
            "tradingagents.agents.analysts.fundamentals.pipeline.run_thesis_cot",
            side_effect=RuntimeError("LLM error"),
        ), patch(
            "tradingagents.agents.analysts.fundamentals.pipeline.get_config",
            return_value={"use_fundamental_memory": False},
        ):
            result = run_cot_pipeline(
                quick_llm=MagicMock(),
                deep_llm=MagicMock(),
                report=report,
                sector_cfg=MagicMock(),
            )

        assert result.pipeline_mode == "cot_partial"
        assert result.stages_completed == ["data_cot", "concept_cot"]
        assert result.financial_health == "concerning"

    def test_all_stages_succeed_returns_full(self):
        """All stages succeed → cot_full with calibrated direction."""
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
            "thesis_text": "Strong fundamentals support continued growth.",
            "key_risks": ["FX risk"],
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
            "tradingagents.agents.analysts.fundamentals.pipeline.get_config",
            return_value={"use_fundamental_memory": False},
        ):
            result = run_cot_pipeline(
                quick_llm=MagicMock(),
                deep_llm=MagicMock(),
                report=report,
                sector_cfg=MagicMock(),
            )

        assert result.pipeline_mode == "cot_full"
        assert "thesis_cot" in result.stages_completed
        assert result.earnings_direction == "up"
        assert result.thesis_text == "Strong fundamentals support continued growth."
        assert result.calibration_policy == POLICY_NAME
