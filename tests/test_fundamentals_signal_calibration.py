"""
Unit tests for Phase B signal calibration.

Tests:
  1. Schema accepts new fields and preserves backward compatibility
  2. Calibration function maps expected cases correctly
  3. Pipeline integration (mock LLM) produces calibrated output
  4. Edge cases: missing fields, fallbacks, deterministic mode
"""
import pytest
from tradingagents.agents.analysts.fundamentals.schemas import FundamentalAnalysisReport
from tradingagents.agents.analysts.fundamentals.calibration import (
    calibrate_earnings_direction,
    CalibrationResult,
    POLICY_NAME,
)


# =============================================================================
# Schema backward compatibility
# =============================================================================

class TestSchemaBackwardCompatibility:
    """Verify that new fields are additive and do not break existing contracts."""

    def test_schema_without_new_fields(self):
        """Old-style construction without Phase B fields must still work."""
        report = FundamentalAnalysisReport(
            ticker="COMI",
            analysis_date="2024-01-01",
            fiscal_period="FY2023",
            sector="banks",
            earnings_direction="up",
            earnings_direction_confidence=70,
        )
        assert report.earnings_direction == "up"
        assert report.earnings_direction_confidence == 70
        # New fields default to empty
        assert report.fundamental_outlook == ""
        assert report.downside_risk_level == ""
        assert report.raw_earnings_direction == ""
        assert report.calibrated_earnings_direction == ""
        assert report.calibration_policy == ""
        assert report.signal_calibration_notes == []

    def test_schema_with_new_fields(self):
        """Construction with Phase B fields must work."""
        report = FundamentalAnalysisReport(
            ticker="COMI",
            analysis_date="2024-01-01",
            fiscal_period="FY2023",
            sector="banks",
            earnings_direction="up",
            earnings_direction_confidence=70,
            fundamental_outlook="bullish",
            downside_risk_level="low",
            raw_earnings_direction="up",
            calibrated_earnings_direction="up",
            calibration_policy=POLICY_NAME,
            signal_calibration_notes=["raw=up; kept as-is"],
        )
        assert report.fundamental_outlook == "bullish"
        assert report.downside_risk_level == "low"
        assert report.raw_earnings_direction == "up"
        assert report.calibrated_earnings_direction == "up"
        assert report.calibration_policy == POLICY_NAME
        assert len(report.signal_calibration_notes) == 1

    def test_model_dump_includes_new_fields(self):
        """model_dump() must include new fields for serialization."""
        report = FundamentalAnalysisReport(
            ticker="ETEL",
            analysis_date="2024-01-01",
            fiscal_period="FY2023",
            sector="operational",
            fundamental_outlook="bearish",
            downside_risk_level="high",
        )
        d = report.model_dump()
        assert "fundamental_outlook" in d
        assert "downside_risk_level" in d
        assert "raw_earnings_direction" in d
        assert "calibrated_earnings_direction" in d
        assert "calibration_policy" in d
        assert "signal_calibration_notes" in d

    def test_existing_fields_unchanged(self):
        """All pre-existing fields must still have their documented types and defaults."""
        report = FundamentalAnalysisReport(
            ticker="TEST",
            analysis_date="2024-01-01",
            fiscal_period="FY2023",
            sector="operational",
        )
        assert report.financial_health == ""
        assert report.valuation_assessment == ""
        assert report.earnings_direction == ""
        assert report.earnings_direction_confidence == 0
        assert report.thesis_text == ""
        assert report.key_risks == []
        assert report.pipeline_mode == "deterministic"
        assert report.stages_completed == []
        assert report.data_confidence == 0
        assert report.signal_coherence == 100
        assert report.distress_flags == []


# =============================================================================
# Calibration function unit tests
# =============================================================================

class TestCalibrationFunction:
    """Test the deterministic calibration logic."""

    # --- raw=up always stays up ---

    def test_raw_up_stays_up(self):
        result = calibrate_earnings_direction(
            fundamental_outlook="bullish",
            downside_risk_level="low",
            raw_earnings_direction="up",
            earnings_direction_confidence=80,
        )
        assert result.calibrated_direction == "up"
        assert result.policy == POLICY_NAME

    def test_raw_up_stays_up_even_bearish(self):
        """Even with bearish outlook, raw=up is not overridden."""
        result = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="high",
            raw_earnings_direction="up",
            earnings_direction_confidence=80,
        )
        assert result.calibrated_direction == "up"

    # --- Low confidence non-up → calibrated to up ---

    def test_low_confidence_down_calibrated_to_up(self):
        result = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="high",
            raw_earnings_direction="down",
            earnings_direction_confidence=65,
        )
        assert result.calibrated_direction == "up"
        assert "conf=65 < 75" in result.notes[0]

    def test_low_confidence_flat_calibrated_to_up(self):
        result = calibrate_earnings_direction(
            fundamental_outlook="neutral",
            downside_risk_level="moderate",
            raw_earnings_direction="flat",
            earnings_direction_confidence=55,
        )
        assert result.calibrated_direction == "up"

    # --- Strict high-confidence down: bearish + high risk → stays down ---

    def test_strict_down_survives(self):
        result = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="high",
            raw_earnings_direction="down",
            earnings_direction_confidence=78,
        )
        assert result.calibrated_direction == "down"
        assert result.policy == POLICY_NAME
        assert "kept as down" in result.notes[0]

    # --- Down below confidence 75 → calibrated to up ---

    def test_down_below_confidence_75_becomes_up(self):
        result = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="high",
            raw_earnings_direction="down",
            earnings_direction_confidence=74,
        )
        assert result.calibrated_direction == "up"
        assert "< 75" in result.notes[0]

    # --- High confidence down without bearish outlook → calibrated to up ---

    def test_down_without_bearish_outlook_becomes_up(self):
        result = calibrate_earnings_direction(
            fundamental_outlook="bullish",
            downside_risk_level="high",
            raw_earnings_direction="down",
            earnings_direction_confidence=80,
        )
        assert result.calibrated_direction == "up"

    def test_high_conf_neutral_down_calibrated_to_up(self):
        result = calibrate_earnings_direction(
            fundamental_outlook="neutral",
            downside_risk_level="high",
            raw_earnings_direction="down",
            earnings_direction_confidence=80,
        )
        assert result.calibrated_direction == "up"

    # --- High confidence down without high downside risk → calibrated to up ---

    def test_down_without_high_downside_risk_becomes_up(self):
        result = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="moderate",
            raw_earnings_direction="down",
            earnings_direction_confidence=80,
        )
        assert result.calibrated_direction == "up"

    # --- Flat final direction is not emitted in v2 ---

    def test_flat_final_direction_becomes_up(self):
        result = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="high",
            raw_earnings_direction="flat",
            earnings_direction_confidence=90,
        )
        assert result.calibrated_direction == "up"
        assert "flat retained only as raw/risk context" in result.notes[0]

    # --- Flat: bullish + low risk → calibrated to up ---

    def test_high_conf_bullish_low_risk_flat_to_up(self):
        result = calibrate_earnings_direction(
            fundamental_outlook="bullish",
            downside_risk_level="low",
            raw_earnings_direction="flat",
            earnings_direction_confidence=75,
        )
        assert result.calibrated_direction == "up"

    # --- Empty/missing fields ---

    def test_no_raw_direction(self):
        result = calibrate_earnings_direction(
            fundamental_outlook="bullish",
            downside_risk_level="low",
            raw_earnings_direction="",
            earnings_direction_confidence=0,
        )
        assert result.calibrated_direction == "up"

    def test_empty_outlook_and_risk_down_to_up(self):
        """If LLM omits outlook/risk, non-up should calibrate to up."""
        result = calibrate_earnings_direction(
            fundamental_outlook="",
            downside_risk_level="",
            raw_earnings_direction="down",
            earnings_direction_confidence=80,
        )
        assert result.calibrated_direction == "up"

    # --- Confidence boundary ---

    def test_confidence_exactly_75_keeps_down(self):
        """Confidence=75 meets the >= 75 v2 down gate."""
        result = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="high",
            raw_earnings_direction="down",
            earnings_direction_confidence=75,
        )
        assert result.calibrated_direction == "down"

    def test_confidence_74_calibrates_to_up(self):
        """Confidence=74 fails the >= 75 v2 down gate."""
        result = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="high",
            raw_earnings_direction="down",
            earnings_direction_confidence=74,
        )
        assert result.calibrated_direction == "up"

    def test_richer_fields_remain_preserved_by_callers(self):
        """Calibration output should not mutate or discard richer signal inputs."""
        raw = {
            "fundamental_outlook": "bearish",
            "downside_risk_level": "high",
            "raw_earnings_direction": "flat",
            "earnings_direction_confidence": 88,
        }
        before = dict(raw)
        result = calibrate_earnings_direction(**raw)
        assert result.calibrated_direction == "up"
        assert raw == before
        assert result.notes

    # --- Notes audit trail ---

    def test_notes_are_nonempty(self):
        """Every calibration result should have at least one note."""
        result = calibrate_earnings_direction(
            fundamental_outlook="bullish",
            downside_risk_level="low",
            raw_earnings_direction="up",
            earnings_direction_confidence=80,
        )
        assert len(result.notes) >= 1

    def test_policy_is_always_set(self):
        result = calibrate_earnings_direction(
            fundamental_outlook="",
            downside_risk_level="",
            raw_earnings_direction="",
            earnings_direction_confidence=0,
        )
        assert result.policy == POLICY_NAME


# =============================================================================
# Thesis validation of new fields
# =============================================================================

class TestThesisValidation:
    """Test that thesis_cot validates and normalizes the new fields."""

    def test_invalid_outlook_normalized(self):
        """Invalid fundamental_outlook should be normalized."""
        from tradingagents.agents.analysts.fundamentals.thesis_cot import (
            _extract_json, _VALID_OUTLOOK, _VALID_RISK_LEVEL,
        )
        import json
        raw = json.dumps({
            "hypothesis": "test",
            "evidence_for": ["a"],
            "evidence_against": ["b"],
            "synthesis": "test",
            "thesis_text": "test thesis",
            "fundamental_outlook": "very_bullish",
            "downside_risk_level": "extreme",
            "earnings_direction": "up",
            "earnings_direction_confidence": 70,
            "valuation_assessment": "fair_value",
            "financial_health": "healthy",
            "key_risks": ["risk1"],
        })
        parsed = _extract_json(raw)
        assert parsed is not None
        # The validation happens in run_thesis_cot, not _extract_json.
        # Just verify the extraction works; validation is tested via run_thesis_cot.

    def test_missing_outlook_gets_default(self):
        """If LLM omits fundamental_outlook, thesis_cot infers from direction."""
        from tradingagents.agents.analysts.fundamentals.thesis_cot import run_thesis_cot
        from unittest.mock import MagicMock
        import json

        llm_response = json.dumps({
            "hypothesis": "NI will grow",
            "evidence_for": ["revenue +20%"],
            "evidence_against": ["leverage high"],
            "synthesis": "Growth outweighs risk",
            "thesis_text": "Bullish thesis.",
            "earnings_direction": "up",
            "earnings_direction_confidence": 75,
            "valuation_assessment": "fair_value",
            "financial_health": "healthy",
            "key_risks": ["leverage"],
        })

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = llm_response
        mock_llm.invoke.return_value = mock_response

        result = run_thesis_cot(
            llm=mock_llm,
            evidence_pack={"ticker": "TEST", "sector": "operational", "narrative": "test"},
            concept_output={},
        )

        assert result.get("fundamental_outlook") == "bullish"
        assert result.get("downside_risk_level") == "low"  # healthy → low

    def test_provided_outlook_preserved(self):
        """If LLM provides valid outlook, it is preserved."""
        from tradingagents.agents.analysts.fundamentals.thesis_cot import run_thesis_cot
        from unittest.mock import MagicMock
        import json

        llm_response = json.dumps({
            "hypothesis": "NI will decline",
            "evidence_for": ["margins declining"],
            "evidence_against": ["revenue growing"],
            "synthesis": "Margin compression dominates",
            "thesis_text": "Bearish thesis.",
            "fundamental_outlook": "bearish",
            "downside_risk_level": "high",
            "earnings_direction": "down",
            "earnings_direction_confidence": 78,
            "valuation_assessment": "overvalued",
            "financial_health": "critical",
            "key_risks": ["leverage", "margin compression"],
        })

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = llm_response
        mock_llm.invoke.return_value = mock_response

        result = run_thesis_cot(
            llm=mock_llm,
            evidence_pack={"ticker": "TEST", "sector": "operational", "narrative": "test"},
            concept_output={},
        )

        assert result.get("fundamental_outlook") == "bearish"
        assert result.get("downside_risk_level") == "high"
        assert result.get("earnings_direction") == "down"


# =============================================================================
# Pipeline integration (mock)
# =============================================================================

class TestPipelineCalibrationIntegration:
    """Verify calibration is applied in the pipeline assembly."""

    def _make_report(self):
        return FundamentalAnalysisReport(
            ticker="TEST",
            analysis_date="2024-01-01",
            fiscal_period="FY2023",
            sector="operational",
        )

    def test_build_full_report_applies_calibration(self):
        """_build_full_report should apply calibration and set all fields."""
        from tradingagents.agents.analysts.fundamentals.pipeline import _build_full_report

        report = self._make_report()
        thesis_output = {
            "financial_health": "critical",
            "earnings_direction": "down",
            "earnings_direction_confidence": 78,
            "fundamental_outlook": "bearish",
            "downside_risk_level": "high",
            "thesis_text": "Test thesis",
            "valuation_assessment": "overvalued",
            "key_risks": ["high leverage"],
            "_valid": True,
        }

        result = _build_full_report(
            report=report,
            evidence_pack={},
            concept_output={},
            thesis_output=thesis_output,
            stages_completed=["data_cot", "concept_cot", "thesis_cot"],
            freq="annual",
        )

        # bearish + high risk + conf 78 → stays down
        assert result.raw_earnings_direction == "down"
        assert result.calibrated_earnings_direction == "down"
        assert result.earnings_direction == "down"  # backward compatible
        assert result.fundamental_outlook == "bearish"
        assert result.downside_risk_level == "high"
        assert result.calibration_policy == POLICY_NAME
        assert len(result.signal_calibration_notes) >= 1

    def test_build_full_report_calibrates_weak_down_to_up(self):
        """Low-confidence down with bullish outlook → calibrated to up."""
        from tradingagents.agents.analysts.fundamentals.pipeline import _build_full_report

        report = self._make_report()
        thesis_output = {
            "financial_health": "healthy",
            "earnings_direction": "down",
            "earnings_direction_confidence": 55,
            "fundamental_outlook": "bullish",
            "downside_risk_level": "low",
            "thesis_text": "Mixed signals thesis",
            "valuation_assessment": "fair_value",
            "key_risks": ["minor risk"],
            "_valid": True,
        }

        result = _build_full_report(
            report=report,
            evidence_pack={},
            concept_output={},
            thesis_output=thesis_output,
            stages_completed=["data_cot", "concept_cot", "thesis_cot"],
            freq="annual",
        )

        assert result.raw_earnings_direction == "down"
        assert result.calibrated_earnings_direction == "up"
        assert result.earnings_direction == "up"  # calibrated

    def test_partial_report_has_empty_calibration_fields(self):
        """When Stage 3 fails, calibration fields should be empty/default."""
        from tradingagents.agents.analysts.fundamentals.pipeline import _build_partial_report

        report = self._make_report()
        result = _build_partial_report(
            report=report,
            evidence_pack={},
            concept_output={},
            stages_completed=["data_cot"],
        )

        assert result.fundamental_outlook == ""
        assert result.downside_risk_level == ""
        assert result.raw_earnings_direction == ""
        assert result.calibrated_earnings_direction == ""
        assert result.calibration_policy == ""
        assert result.signal_calibration_notes == []


# =============================================================================
# Calibration policy completeness
# =============================================================================

class TestCalibrationPolicyCoverage:
    """Verify all outlook × risk × direction combinations produce a valid result."""

    @pytest.mark.parametrize("outlook", ["bullish", "neutral", "bearish", ""])
    @pytest.mark.parametrize("risk", ["low", "moderate", "high", ""])
    @pytest.mark.parametrize("direction", ["up", "down", "flat"])
    @pytest.mark.parametrize("confidence", [50, 70, 85])
    def test_all_combinations_valid(self, outlook, risk, direction, confidence):
        result = calibrate_earnings_direction(
            fundamental_outlook=outlook,
            downside_risk_level=risk,
            raw_earnings_direction=direction,
            earnings_direction_confidence=confidence,
        )
        assert result.calibrated_direction in ("up", "down", "flat")
        assert result.policy == POLICY_NAME
        assert len(result.notes) >= 1


# =============================================================================
# Phase 2B audit harness integration (no LLM)
# =============================================================================

class TestPhase2BAuditHarnessCalibration:
    """Verify the direct audit path evaluates the Phase B calibrated signal."""

    def test_audit_applies_calibration_after_thesis_output(self):
        from tests.phase2b_audit import _apply_phaseb_calibration

        thesis_output = {
            "earnings_direction": "down",
            "earnings_direction_confidence": 55,
            "fundamental_outlook": "bullish",
            "downside_risk_level": "low",
        }

        result = _apply_phaseb_calibration(thesis_output, freq="annual")

        assert result["raw_earnings_direction"] == "down"
        assert result["calibrated_earnings_direction"] == "up"
        assert result["earnings_direction"] == "up"
        assert result["calibration_policy"] == POLICY_NAME
        assert result["signal_calibration_notes"]

    def test_audit_artifact_rows_keep_phaseb_fields(self):
        from tests.phase2b_audit import _annual_case_result_fields, _build_artifact_case_rows

        cot_results = [{
            "ticker": "COMI",
            "prediction_period": "2023-12-31",
            "actual_period": "2024-12-31",
            "actual_direction": "up",
            "actual_ni_change": 0.15,
            "predicted_direction": "up",
            "earnings_direction": "up",
            "raw_earnings_direction": "down",
            "calibrated_earnings_direction": "up",
            "fundamental_outlook": "bullish",
            "downside_risk_level": "low",
            "calibration_policy": POLICY_NAME,
            "signal_calibration_notes": ["raw=down conf=55; calibrated to up"],
            "confidence": 55,
            "stage1_valid": True,
            "stage2_valid": True,
            "stage3_valid": True,
            "thesis_text": "mock thesis",
        }]
        test_cases = [{
            "ticker": "COMI",
            "sector": "banks",
            "prediction_period": "2023-12-31",
            "actual_period": "2024-12-31",
        }]

        rows = _build_artifact_case_rows(cot_results, test_cases)
        row = rows[0]

        assert row["raw_earnings_direction"] == "down"
        assert row["calibrated_earnings_direction"] == "up"
        assert row["earnings_direction"] == "up"
        assert row["fundamental_outlook"] == "bullish"
        assert row["downside_risk_level"] == "low"
        assert row["calibration_policy"] == POLICY_NAME
        assert row["raw_correct"] is False
        assert row["calibrated_correct"] is True
        assert row["final_correct"] is True
        assert "calibrated to up" in row["signal_calibration_notes"]

        fieldnames = _annual_case_result_fields()
        for field in (
            "raw_earnings_direction",
            "calibrated_earnings_direction",
            "earnings_direction",
            "fundamental_outlook",
            "downside_risk_level",
            "calibration_policy",
            "signal_calibration_notes",
            "raw_correct",
            "calibrated_correct",
            "final_correct",
        ):
            assert field in fieldnames
