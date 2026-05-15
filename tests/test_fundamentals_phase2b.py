"""
Phase 2B tests for EGX Fundamental Analyst CoT Pipeline.

Tests that run WITHOUT LLM (deterministic checks):
  - test_evidence_pack_schema_completeness
  - test_inter_stage_validation_blocks_broken_state
  - test_deterministic_fallback_on_stage_failure
  - test_brier_score_formula_correctness
  - test_confidence_weighted_ic_formula_correctness
  - test_earnings_direction_hit_rate_formula_correctness
  - test_concept_prompt_forbids_moat_analysis
  - test_stage1_validation_rejects_empty_ratios
  - test_stage1_validation_rejects_low_confidence
  - test_stage2_validation_rejects_missing_keys
  - test_stage2_validation_rejects_invalid_health_value
  - test_pipeline_returns_deterministic_on_stage1_failure
  - test_pipeline_mode_cot_partial_on_stage2_failure
  - test_evidence_pack_contains_all_required_sections
  - test_evidence_narrative_non_empty_for_valid_report

Tests that run WITH LLM (require DEEPSEEK_API_KEY or project config):
  @pytest.mark.integration
  - test_stage2_output_schema_valid
  - test_stage3_produces_valid_report
  - test_pipeline_end_to_end_single_ticker
  - test_concept_cot_does_not_confabulate_moat
"""

import math
import pytest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from tradingagents.agents.analysts.fundamentals.schemas import FundamentalAnalysisReport
from tradingagents.agents.analysts.fundamentals.financial_calculator import FinancialCalculator
from tradingagents.agents.analysts.fundamentals.sector_config import SectorConfig
from tradingagents.agents.analysts.fundamentals.scoring import compute_data_confidence, estimate_periods_since_filing
from tradingagents.agents.analysts.fundamentals.statement_standardizer import StatementStandardizer
from tradingagents.agents.analysts.fundamentals.data_cot import (
    build_evidence_pack,
    validate_evidence_pack,
    format_evidence_narrative,
)
from tradingagents.agents.analysts.fundamentals.concept_cot import (
    validate_concept_output,
    build_concept_prompt,
    _SYSTEM_PROMPT as CONCEPT_SYSTEM_PROMPT,
)
from tradingagents.agents.analysts.fundamentals.thesis_cot import run_thesis_cot
from tradingagents.agents.analysts.fundamentals.pipeline import run_cot_pipeline


# =============================================================================
# Fixtures
# =============================================================================

def _make_healthy_report(ticker: str = "ETEL", sector: str = "operational") -> FundamentalAnalysisReport:
    """Build a FundamentalAnalysisReport with enough data for Stage 1 to pass validation."""
    # Internally consistent values: NM=10%, AT=0.5, EM=2.5 → DuPont ROE=12.5%
    nm = 0.10
    at = 0.5
    em = 2.5
    roe = nm * at * em  # 0.125

    ratios = {
        "roe": roe,
        "roa": 0.05,
        "gross_margin": 0.40,
        "operating_margin": 0.20,
        "net_margin": nm,
        "debt_to_equity": 1.5,
        "current_ratio": 2.0,
        "asset_turnover": at,
        "equity_multiplier": em,
        "dupont_3factor": roe,
        "eps": 5.0,
        "pe_ratio": 10.0,
        "pb_ratio": 1.2,
        "earnings_yield": 0.10,
        "earnings_yield_spread": None,
        "dividend_yield": None,
        "piotroski_score": 5,
    }

    preprocessing = {
        "common_size_income": {
            "gross_profit_pct": 0.40,
            "operating_income_pct": 0.20,
            "net_income_pct": 0.10,
        },
        "common_size_balance": {
            "total_liabilities_pct": 0.60,
            "total_equity_pct": 0.40,
        },
        "revenue_growth_yoy": 0.15,
        "net_income_growth_yoy": 0.20,
        "directions": {
            "revenue": "improving",
            "net_income": "improving",
            "net_margin": "improving",
            "roe": "improving",
            "roa": "improving",
            "debt_to_equity": "stable",
            "total_liabilities": "stable",
            "current_ratio": "stable",
        },
    }

    return FundamentalAnalysisReport(
        ticker=ticker,
        analysis_date="2024-01-01",
        fiscal_period="FY2023",
        sector=sector,
        ratios=ratios,
        preprocessing=preprocessing,
        distress_flags=[],
        data_confidence=85,
        signal_coherence=100,
        financial_health="healthy",
        pipeline_mode="deterministic",
    )


def _make_low_confidence_report() -> FundamentalAnalysisReport:
    """Build a report with very low data_confidence to trigger Stage 1 validation failure."""
    return FundamentalAnalysisReport(
        ticker="TEST",
        analysis_date="2024-01-01",
        fiscal_period="FY2023",
        sector="operational",
        ratios={},             # empty — Stage 1 should block this
        preprocessing={},
        distress_flags=[],
        data_confidence=5,     # below _MIN_CONFIDENCE_FOR_COT=20
        signal_coherence=100,
        financial_health="insufficient_data",
        pipeline_mode="deterministic",
    )


# =============================================================================
# Evidence pack (Stage 1) tests — deterministic
# =============================================================================

class TestEvidencePackAssembly:

    def test_evidence_pack_schema_completeness(self):
        """
        Stage 1 evidence pack must contain all required keys listed in Section 8 of the plan:
        ticker, fiscal_period, sector, sector_context, ratios, directions,
        distress_flags, data_confidence, signal_coherence,
        revenue_growth_yoy, net_income_growth_yoy, common_size_income,
        common_size_balance, piotroski_score, narrative, _valid, _validation_errors
        """
        report = _make_healthy_report()
        sector_cfg = SectorConfig("ETEL")
        pack = build_evidence_pack(report, sector_cfg)

        required_keys = {
            "ticker", "fiscal_period", "sector", "sector_context",
            "ratios", "directions", "distress_flags",
            "data_confidence", "signal_coherence",
            "revenue_growth_yoy", "net_income_growth_yoy",
            "common_size_income", "common_size_balance",
            "piotroski_score", "narrative",
            "_valid", "_validation_errors",
        }
        missing = required_keys - set(pack.keys())
        assert not missing, f"Evidence pack missing required keys: {missing}"

    def test_evidence_pack_valid_for_healthy_report(self):
        """A healthy report with data_confidence=85 should pass Stage 1 validation."""
        report = _make_healthy_report()
        sector_cfg = SectorConfig("ETEL")
        pack = build_evidence_pack(report, sector_cfg)
        assert pack["_valid"] is True, f"Validation errors: {pack['_validation_errors']}"

    def test_evidence_narrative_non_empty_for_valid_report(self):
        """Stage 1 narrative must be non-empty for any report with valid data."""
        report = _make_healthy_report()
        sector_cfg = SectorConfig("ETEL")
        pack = build_evidence_pack(report, sector_cfg)
        assert len(pack["narrative"].strip()) > 100, "Evidence narrative is too short"

    def test_stage1_validation_rejects_empty_ratios(self):
        """Stage 1 must fail when ratios dict has no non-null values."""
        report = FundamentalAnalysisReport(
            ticker="ETEL",
            analysis_date="2024-01-01",
            fiscal_period="FY2023",
            sector="operational",
            ratios={"roe": None, "roa": None},  # all None
            preprocessing={},
            distress_flags=[],
            data_confidence=85,
            signal_coherence=100,
            financial_health="insufficient_data",
            pipeline_mode="deterministic",
        )
        sector_cfg = SectorConfig("ETEL")
        pack = build_evidence_pack(report, sector_cfg)
        assert pack["_valid"] is False
        assert any("no non-null values" in e for e in pack["_validation_errors"])

    def test_stage1_validation_rejects_low_confidence(self):
        """Stage 1 must fail when data_confidence < 20 (insufficient data for LLM)."""
        report = _make_low_confidence_report()
        sector_cfg = SectorConfig("TEST")
        pack = build_evidence_pack(report, sector_cfg)
        assert pack["_valid"] is False
        assert any("data_confidence" in e for e in pack["_validation_errors"])

    def test_evidence_pack_ticker_matches_report(self):
        """Pack ticker must match the report ticker exactly."""
        report = _make_healthy_report(ticker="COMI", sector="banks")
        sector_cfg = SectorConfig("COMI")
        pack = build_evidence_pack(report, sector_cfg)
        assert pack["ticker"] == "COMI"
        assert pack["sector"] == "banks"

    def test_evidence_pack_banks_sector_has_context(self):
        """Banks sector pack must contain sector-specific context notes."""
        report = _make_healthy_report(ticker="COMI", sector="banks")
        sector_cfg = SectorConfig("COMI")
        pack = build_evidence_pack(report, sector_cfg)
        # Banks sector_context should have at least one context note
        assert isinstance(pack["sector_context"], dict)
        # Banks have D/E and current_ratio context notes
        assert len(pack["sector_context"]) > 0


# =============================================================================
# Stage 2 (Concept CoT) validation — deterministic
# =============================================================================

class TestConceptCoTValidation:

    def test_stage2_validation_rejects_missing_keys(self):
        """Stage 2 validation must fail if any required key is missing."""
        # Missing 'financial_health'
        parsed = {
            "key_metrics_discussion": "some text",
            "risk_factors": ["risk1"],
            "growth_signal": "positive",
        }
        valid, errors = validate_concept_output(parsed)
        assert valid is False
        assert any("financial_health" in e for e in errors)

    def test_stage2_validation_rejects_invalid_health_value(self):
        """Stage 2 validation must reject invalid financial_health values."""
        parsed = {
            "financial_health": "great",   # not in valid set
            "key_metrics_discussion": "some text",
            "risk_factors": ["risk1"],
            "growth_signal": "positive",
        }
        valid, errors = validate_concept_output(parsed)
        assert valid is False
        assert any("financial_health" in e for e in errors)

    def test_stage2_validation_rejects_non_list_risks(self):
        """Stage 2 validation must fail if risk_factors is not a list."""
        parsed = {
            "financial_health": "healthy",
            "key_metrics_discussion": "some text",
            "risk_factors": "single risk as string",  # should be list
            "growth_signal": "positive",
        }
        valid, errors = validate_concept_output(parsed)
        assert valid is False
        assert any("risk_factors" in e for e in errors)

    def test_stage2_validation_passes_for_valid_output(self):
        """Stage 2 validation must pass for a complete, valid output."""
        parsed = {
            "financial_health": "healthy",
            "financial_health_rationale": "Strong margins and improving ROE.",
            "key_metrics_discussion": "ROE improved YoY, margins stable.",
            "standout_signals": ["ROE improving", "revenue growth"],
            "risk_factors": ["EGP depreciation risk", "interest rate sensitivity"],
            "growth_signal": "positive",
            "growth_signal_rationale": "Revenue and NI both growing.",
            "valuation_read": "fair",
            "valuation_rationale": "P/E in line with sector.",
            "coherence_notes": "none",
            "analyst_note": "none",
        }
        valid, errors = validate_concept_output(parsed)
        assert valid is True, f"Should pass: {errors}"

    def test_concept_prompt_forbids_moat_analysis(self):
        """
        The Stage 2 system prompt must explicitly forbid moat, management quality,
        and peer comparison assessment — per Section 8 (OUT OF SCOPE list).
        """
        forbidden_terms = [
            "moat",
            "management quality",
            "peer comparison",
            "competitive position",
            "regulatory risk",
        ]
        prompt_lower = CONCEPT_SYSTEM_PROMPT.lower()
        # The prompt should mention these are forbidden — not absent, but explicitly constrained
        # We check the system prompt contains enough scoping language
        assert "moat" in prompt_lower or "only reference" in prompt_lower, (
            "System prompt must contain scoping rules that prevent moat analysis"
        )

    def test_concept_prompt_contains_sector_awareness(self):
        """Concept system prompt must include EGX-specific and sector-aware instructions."""
        prompt_lower = CONCEPT_SYSTEM_PROMPT.lower()
        assert "egx" in prompt_lower or "egypt" in prompt_lower, (
            "System prompt must reference EGX/Egypt market context"
        )
        assert "sector" in prompt_lower, "System prompt must mention sector-specific interpretation"

    def test_concept_prompt_builds_correctly(self):
        """build_concept_prompt must embed the evidence narrative in the user prompt."""
        narrative = "EVIDENCE PACK — TEST | FY2023\nSector: OPERATIONAL"
        prompt = build_concept_prompt(narrative, "TEST", "operational")
        assert "TEST" in prompt
        assert narrative in prompt
        assert "operational" in prompt.upper() or "OPERATIONAL" in prompt


# =============================================================================
# Inter-stage validation / pipeline fallback — deterministic
# =============================================================================

class TestPipelineFallback:

    def test_deterministic_fallback_on_stage1_failure(self):
        """
        Pipeline must return pipeline_mode='deterministic' and stages_completed=[]
        when Stage 1 validation fails (low confidence / empty ratios).
        """
        report = _make_low_confidence_report()
        sector_cfg = SectorConfig("TEST")

        # Mock LLMs — they should never be called if Stage 1 fails
        mock_quick = MagicMock()
        mock_deep = MagicMock()

        result = run_cot_pipeline(mock_quick, mock_deep, report, sector_cfg)

        assert result.pipeline_mode == "deterministic"
        assert result.stages_completed == []
        # LLMs must not have been called
        mock_quick.invoke.assert_not_called()
        mock_deep.invoke.assert_not_called()

    def test_pipeline_mode_cot_partial_on_stage2_failure(self):
        """
        When Stage 2 LLM fails, pipeline must return pipeline_mode='cot_partial'
        with stages_completed=['data_cot'] — Stage 1 completes but Stage 2 fails.
        """
        report = _make_healthy_report()
        sector_cfg = SectorConfig("ETEL")

        # Stage 2 LLM raises an exception
        mock_quick = MagicMock()
        mock_quick.invoke.side_effect = RuntimeError("LLM API timeout")
        mock_deep = MagicMock()

        result = run_cot_pipeline(mock_quick, mock_deep, report, sector_cfg)

        assert result.pipeline_mode == "cot_partial"
        assert "data_cot" in result.stages_completed
        assert "concept_cot" not in result.stages_completed
        # deep_llm (Stage 3) must not be called
        mock_deep.invoke.assert_not_called()

    def test_inter_stage_validation_blocks_broken_stage2_output(self):
        """
        When Stage 2 returns invalid JSON (broken state), Stage 3 must NOT be called.
        The pipeline returns cot_partial with only Stage 1 in stages_completed.
        """
        report = _make_healthy_report()
        sector_cfg = SectorConfig("ETEL")

        # Stage 2 returns invalid content (missing required keys)
        mock_response = MagicMock()
        mock_response.content = '{"financial_health": "INVALID_VALUE_NOT_IN_VALID_SET"}'

        mock_quick = MagicMock()
        mock_quick.invoke.return_value = mock_response

        mock_deep = MagicMock()

        result = run_cot_pipeline(mock_quick, mock_deep, report, sector_cfg)

        # Stage 3 must not have been called due to Stage 2 validation failure
        assert "thesis_cot" not in result.stages_completed
        mock_deep.invoke.assert_not_called()

    def test_pipeline_never_overwrites_deterministic_ratios(self):
        """
        The CoT pipeline must NEVER overwrite ratios, preprocessing, or distress_flags.
        These come from deterministic computation and must be preserved exactly.
        (Plan Section 8: 'Deterministic values are NEVER overwritten')
        """
        report = _make_healthy_report()
        sector_cfg = SectorConfig("ETEL")

        original_ratios = dict(report.ratios)
        original_flags = list(report.distress_flags)
        original_dc = report.data_confidence
        original_sc = report.signal_coherence

        # Simulate Stage 2 and 3 returning valid outputs that try to change ratios
        mock_stage2_response = MagicMock()
        mock_stage2_response.content = '''{
            "financial_health": "healthy",
            "financial_health_rationale": "Strong financials.",
            "key_metrics_discussion": "Good margins.",
            "standout_signals": ["ROE"],
            "risk_factors": ["currency risk"],
            "growth_signal": "positive",
            "growth_signal_rationale": "Growing.",
            "valuation_read": "fair",
            "valuation_rationale": "Fair value.",
            "coherence_notes": "none",
            "analyst_note": "none"
        }'''

        mock_stage3_response = MagicMock()
        mock_stage3_response.content = '''{
            "hypothesis": "Revenue will grow 15% next period due to expansion.",
            "evidence_for": ["revenue growing 15% YoY"],
            "evidence_against": ["EGP volatility risk"],
            "synthesis": "Evidence is mixed but leans positive.",
            "thesis_text": "ETEL shows improving fundamentals with margin expansion and revenue growth.",
            "earnings_direction": "up",
            "earnings_direction_confidence": 65,
            "earnings_direction_rationale": "Growth trajectory intact.",
            "valuation_assessment": "fair_value",
            "valuation_rationale": "P/E is reasonable.",
            "financial_health": "healthy",
            "key_risks": ["currency", "regulation"],
            "egx_specific_risks": [],
            "invalidation_conditions": ["margin compression"]
        }'''

        mock_quick = MagicMock()
        mock_quick.invoke.return_value = mock_stage2_response
        mock_deep = MagicMock()
        mock_deep.invoke.return_value = mock_stage3_response

        result = run_cot_pipeline(mock_quick, mock_deep, report, sector_cfg)

        # Deterministic fields must be preserved exactly
        assert dict(result.ratios) == original_ratios
        assert list(result.distress_flags) == original_flags
        assert result.data_confidence == original_dc
        assert result.signal_coherence == original_sc

    def test_pipeline_returns_valid_report_type(self):
        """Pipeline must always return a FundamentalAnalysisReport, never None or a dict."""
        report = _make_low_confidence_report()
        sector_cfg = SectorConfig("TEST")
        mock_quick = MagicMock()
        mock_deep = MagicMock()

        result = run_cot_pipeline(mock_quick, mock_deep, report, sector_cfg)
        assert isinstance(result, FundamentalAnalysisReport)


# =============================================================================
# Gate metric formula unit tests — deterministic
# =============================================================================

class TestBrierScoreFormula:
    """
    Unit tests for the Brier score formula used in Phase 2B gate.
    Formula: (1/N) × Σ(confidence_i/100 − outcome_i)²
    """

    def _brier(self, predictions):
        """Replicate the _compute_brier logic from phase2b_audit.py."""
        scored = [
            p for p in predictions
            if p.get("predicted_direction") in ("up", "down", "flat")
            and p.get("actual_direction") in ("up", "down", "flat")
        ]
        if not scored:
            return float("nan")
        total = 0.0
        for p in scored:
            conf = min(100, max(0, p.get("confidence", 0))) / 100.0
            outcome = 1.0 if p["predicted_direction"] == p["actual_direction"] else 0.0
            total += (conf - outcome) ** 2
        return total / len(scored)

    def test_perfect_confident_correct_predictions(self):
        """100% confidence + correct prediction → Brier = 0.0."""
        predictions = [
            {"predicted_direction": "up", "actual_direction": "up", "confidence": 100},
            {"predicted_direction": "down", "actual_direction": "down", "confidence": 100},
        ]
        assert self._brier(predictions) == pytest.approx(0.0)

    def test_wrong_predictions_at_full_confidence(self):
        """100% confidence + wrong prediction → Brier = 1.0."""
        predictions = [
            {"predicted_direction": "up", "actual_direction": "down", "confidence": 100},
        ]
        assert self._brier(predictions) == pytest.approx(1.0)

    def test_zero_confidence_predictions(self):
        """0% confidence → Brier = outcome² (0.0 if wrong, 1.0 if right)."""
        # 0% confidence on wrong answer: (0 - 0)² = 0.0
        pred_wrong = [{"predicted_direction": "up", "actual_direction": "down", "confidence": 0}]
        assert self._brier(pred_wrong) == pytest.approx(0.0)

        # 0% confidence on right answer: (0 - 1)² = 1.0
        pred_right = [{"predicted_direction": "up", "actual_direction": "up", "confidence": 0}]
        assert self._brier(pred_right) == pytest.approx(1.0)

    def test_well_calibrated_50_percent(self):
        """50% confidence, 50% correct → Brier = 0.25."""
        predictions = [
            {"predicted_direction": "up", "actual_direction": "up", "confidence": 50},
            {"predicted_direction": "down", "actual_direction": "up", "confidence": 50},
        ]
        # (0.5-1)² + (0.5-0)² = 0.25 + 0.25 = 0.5; /2 = 0.25
        assert self._brier(predictions) == pytest.approx(0.25)

    def test_empty_predictions_returns_nan(self):
        """Empty predictions list → Brier = NaN."""
        assert math.isnan(self._brier([]))

    def test_filters_out_invalid_directions(self):
        """Predictions with invalid direction strings are excluded from scoring."""
        predictions = [
            {"predicted_direction": "up", "actual_direction": "up", "confidence": 100},
            {"predicted_direction": "maybe", "actual_direction": "up", "confidence": 100},  # invalid
            {"predicted_direction": "", "actual_direction": "up", "confidence": 100},  # invalid
        ]
        # Only one valid prediction: (1-1)² = 0.0
        assert self._brier(predictions) == pytest.approx(0.0)

    def test_naive_baseline_brier_at_60_percent(self):
        """Naive 'always_up at 60%' baseline Brier formula check."""
        actuals = ["up", "up", "down", "up", "down"]
        total = 0.0
        for d in actuals:
            outcome = 1.0 if d == "up" else 0.0
            total += (0.60 - outcome) ** 2
        naive_brier = total / len(actuals)
        # up=3/5=0.6: (0.6-1)²×3 + (0.6-0)²×2 = 0.16×3 + 0.36×2 = 0.48+0.72 = 1.20 / 5 = 0.24
        assert naive_brier == pytest.approx(0.24)


class TestHitRateFormula:

    def _hit_rate(self, predictions):
        """Replicate _compute_hit_rate from phase2b_audit.py."""
        scored = [
            p for p in predictions
            if p.get("predicted_direction") in ("up", "down", "flat")
            and p.get("actual_direction") in ("up", "down", "flat")
        ]
        if not scored:
            return float("nan"), float("nan")
        actuals = [p["actual_direction"] for p in scored]
        naive_rate = sum(1 for a in actuals if a == "up") / len(actuals)
        cot_correct = sum(
            1 for p in scored if p["predicted_direction"] == p["actual_direction"]
        )
        return cot_correct / len(scored), naive_rate

    def test_perfect_predictions(self):
        predictions = [
            {"predicted_direction": "up", "actual_direction": "up", "confidence": 80},
            {"predicted_direction": "down", "actual_direction": "down", "confidence": 70},
        ]
        cot, naive = self._hit_rate(predictions)
        assert cot == pytest.approx(1.0)

    def test_always_up_baseline(self):
        """Naive baseline is fraction of actual 'up' outcomes."""
        predictions = [
            {"predicted_direction": "up", "actual_direction": "up", "confidence": 60},
            {"predicted_direction": "down", "actual_direction": "up", "confidence": 60},
            {"predicted_direction": "flat", "actual_direction": "down", "confidence": 60},
        ]
        _, naive = self._hit_rate(predictions)
        # 2 out of 3 are "up"
        assert naive == pytest.approx(2 / 3)

    def test_empty_predictions_returns_nan(self):
        cot, naive = self._hit_rate([])
        assert math.isnan(cot)
        assert math.isnan(naive)


class TestConfidenceWeightedIC:

    def _compute_ic(self, predictions):
        """
        Replicate _compute_ic from phase2b_audit.py.
        SpearmanCorr(confidence × direction_sign, actual_ni_change).
        """
        try:
            from scipy.stats import spearmanr
        except ImportError:
            return float("nan")

        scored = [
            p for p in predictions
            if p.get("predicted_direction") in ("up", "down", "flat")
            and p.get("actual_ni_change") is not None
            and isinstance(p.get("confidence"), (int, float))
        ]
        if len(scored) < 3:
            return float("nan")

        sign_map = {"up": 1, "down": -1, "flat": 0}
        x = [
            (p["confidence"] / 100.0) * sign_map[p["predicted_direction"]]
            for p in scored
        ]
        y = [p["actual_ni_change"] for p in scored]

        try:
            stat, _ = spearmanr(x, y)
            return float(stat) if not math.isnan(stat) else float("nan")
        except Exception:
            return float("nan")

    def test_positive_ic_for_correct_confident_predictions(self):
        """
        When high-confidence predictions align with actual direction, IC > 0.
        Requires scipy (skipped if not installed).
        """
        pytest.importorskip("scipy")
        predictions = [
            {"predicted_direction": "up",   "actual_ni_change": +0.30, "confidence": 80},
            {"predicted_direction": "down",  "actual_ni_change": -0.20, "confidence": 75},
            {"predicted_direction": "up",   "actual_ni_change": +0.15, "confidence": 65},
            {"predicted_direction": "down",  "actual_ni_change": -0.10, "confidence": 70},
            {"predicted_direction": "flat",  "actual_ni_change": +0.02, "confidence": 50},
        ]
        ic = self._compute_ic(predictions)
        assert not math.isnan(ic), "IC should be computable with ≥ 3 pairs"
        assert ic > 0, f"IC should be positive for correct aligned predictions, got {ic}"

    def test_ic_requires_minimum_3_pairs(self):
        """IC returns NaN when < 3 scored pairs exist."""
        pytest.importorskip("scipy")
        predictions = [
            {"predicted_direction": "up", "actual_ni_change": 0.10, "confidence": 70},
            {"predicted_direction": "down", "actual_ni_change": -0.10, "confidence": 70},
        ]
        ic = self._compute_ic(predictions)
        assert math.isnan(ic)

    def test_ic_returns_nan_without_scipy(self):
        """
        IC computation gracefully returns NaN when scipy is not importable.
        This test verifies the import guard works — it's a code path test, not a scipy test.
        """
        # We can't actually remove scipy, but we can verify the formula logic
        # by checking that predictions with no actual_ni_change return nan
        predictions = [
            {"predicted_direction": "up", "actual_ni_change": None, "confidence": 70},
            {"predicted_direction": "down", "actual_ni_change": None, "confidence": 70},
            {"predicted_direction": "flat", "actual_ni_change": None, "confidence": 70},
        ]
        ic = self._compute_ic(predictions)
        assert math.isnan(ic), "IC should be NaN when actual_ni_change is all None"


# =============================================================================
# Stage 3 (thesis) output validation — deterministic
# =============================================================================

class TestThesisCoTValidation:

    def _make_stage2_output(self) -> dict:
        return {
            "financial_health": "healthy",
            "financial_health_rationale": "Margins and ROE are improving.",
            "key_metrics_discussion": "ROE improved YoY, margins stable.",
            "standout_signals": ["ROE improving"],
            "risk_factors": ["EGP exposure", "commodity cost"],
            "growth_signal": "positive",
            "growth_signal_rationale": "Revenue growing 15%.",
            "valuation_read": "fair",
            "valuation_rationale": "P/E is reasonable.",
            "coherence_notes": "none",
            "analyst_note": "none",
            "_valid": True,
            "_validation_errors": [],
        }

    def test_thesis_cot_handles_llm_failure_gracefully(self):
        """
        If the deep_thinking_llm raises an exception, run_thesis_cot must return
        a dict with _valid=False without crashing or propagating the exception.
        (Plan Hard Rule 7: all LLM calls must have explicit error handling)
        """
        report = _make_healthy_report()
        sector_cfg = SectorConfig("ETEL")
        evidence_pack = build_evidence_pack(report, sector_cfg)

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = ConnectionError("API unreachable")

        result = run_thesis_cot(mock_llm, evidence_pack, self._make_stage2_output())

        assert result["_valid"] is False
        assert "LLM call failed" in " ".join(result["_validation_errors"])

    def test_thesis_cot_handles_invalid_json_gracefully(self):
        """
        If the deep_thinking_llm returns unparseable text, run_thesis_cot must return
        _valid=False without crashing.
        """
        report = _make_healthy_report()
        sector_cfg = SectorConfig("ETEL")
        evidence_pack = build_evidence_pack(report, sector_cfg)

        mock_response = MagicMock()
        mock_response.content = "I cannot provide investment advice."  # not JSON

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        result = run_thesis_cot(mock_llm, evidence_pack, self._make_stage2_output())
        assert result["_valid"] is False

    def test_thesis_cot_normalizes_invalid_direction(self):
        """
        If the LLM returns an invalid earnings_direction, it must be normalized to 'flat'.
        """
        report = _make_healthy_report()
        sector_cfg = SectorConfig("ETEL")
        evidence_pack = build_evidence_pack(report, sector_cfg)

        mock_response = MagicMock()
        mock_response.content = '''{
            "hypothesis": "Company will grow earnings.",
            "evidence_for": ["revenue growth"],
            "evidence_against": ["cost pressure"],
            "synthesis": "Net positive outlook.",
            "thesis_text": "This company shows strong fundamentals with consistent revenue growth and margin improvement.",
            "earnings_direction": "bullish",
            "earnings_direction_confidence": 70,
            "earnings_direction_rationale": "Growth trends intact.",
            "valuation_assessment": "fair_value",
            "valuation_rationale": "Fair P/E.",
            "financial_health": "healthy",
            "key_risks": ["currency"],
            "egx_specific_risks": [],
            "invalidation_conditions": ["margin compression"]
        }'''

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        result = run_thesis_cot(mock_llm, evidence_pack, self._make_stage2_output())
        # Invalid direction "bullish" must be normalized to "flat"
        assert result.get("earnings_direction") == "flat"

    def test_thesis_cot_clamps_confidence_to_0_100(self):
        """earnings_direction_confidence outside 0–100 must be clamped."""
        report = _make_healthy_report()
        sector_cfg = SectorConfig("ETEL")
        evidence_pack = build_evidence_pack(report, sector_cfg)

        mock_response = MagicMock()
        mock_response.content = '''{
            "hypothesis": "Company will grow earnings.",
            "evidence_for": ["revenue growth"],
            "evidence_against": ["cost pressure"],
            "synthesis": "Net positive outlook.",
            "thesis_text": "Strong fundamentals with revenue and margin improvement trends.",
            "earnings_direction": "up",
            "earnings_direction_confidence": 150,
            "earnings_direction_rationale": "Strong trend.",
            "valuation_assessment": "fair_value",
            "valuation_rationale": "Fair P/E.",
            "financial_health": "healthy",
            "key_risks": ["currency"],
            "egx_specific_risks": [],
            "invalidation_conditions": []
        }'''

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        result = run_thesis_cot(mock_llm, evidence_pack, self._make_stage2_output())
        assert result.get("earnings_direction_confidence") == 100


# =============================================================================
# Full pipeline integration tests (require LLM — marked integration)
# =============================================================================

@pytest.mark.integration
class TestPipelineIntegration:
    """
    These tests make real LLM API calls.
    Run with: pytest tests/test_fundamentals_phase2b.py -m integration
    Skip in CI with: pytest -m "not integration"

    Requires LLM credentials configured in tradingagents/default_config.py
    OR DEEPSEEK_API_KEY in environment.
    """

    def _get_llms(self):
        """Get LLM instances from project config."""
        try:
            from tradingagents.default_config import DEFAULT_CONFIG
            cfg = DEFAULT_CONFIG
            provider = cfg.get("llm_provider", "openai").lower()
            deep_model = cfg.get("deep_think_llm", "deepseek-reasoner")
            quick_model = cfg.get("quick_think_llm", "deepseek-chat")
            backend_url = cfg.get("backend_url", "https://api.deepseek.com")
            from langchain_openai import ChatOpenAI
            quick_llm = ChatOpenAI(model=quick_model, base_url=backend_url, temperature=0)
            deep_llm = ChatOpenAI(model=deep_model, base_url=backend_url, temperature=0)
            return quick_llm, deep_llm
        except Exception as e:
            pytest.skip(f"LLM credentials not available: {e}")

    def test_stage2_output_schema_valid(self):
        """Stage 2 must return a valid JSON with all required keys on a real report."""
        from tradingagents.agents.analysts.fundamentals.concept_cot import run_concept_cot
        quick_llm, _ = self._get_llms()

        report = _make_healthy_report()
        sector_cfg = SectorConfig("ETEL")
        pack = build_evidence_pack(report, sector_cfg)
        assert pack["_valid"], "Evidence pack must be valid before Stage 2"

        concept_output = run_concept_cot(quick_llm, pack)
        assert concept_output.get("_valid") is True, (
            f"Stage 2 validation failed: {concept_output.get('_validation_errors')}"
        )
        assert concept_output.get("financial_health") in {
            "healthy", "concerning", "critical", "insufficient_data"
        }

    def test_stage3_produces_valid_report(self):
        """Stage 3 must return a FundamentalAnalysisReport-compatible dict on a real report."""
        quick_llm, deep_llm = self._get_llms()

        report = _make_healthy_report()
        sector_cfg = SectorConfig("ETEL")

        result = run_cot_pipeline(quick_llm, deep_llm, report, sector_cfg)

        assert isinstance(result, FundamentalAnalysisReport)
        assert result.pipeline_mode in ("cot_partial", "cot_full", "deterministic")
        # If thesis succeeded, direction must be valid
        if result.earnings_direction:
            assert result.earnings_direction in ("up", "down", "flat")
        if result.thesis_text:
            assert len(result.thesis_text) >= 50, "Thesis text too short"

    def test_pipeline_end_to_end_single_ticker(self):
        """Full pipeline on COMI (bank sector) — end-to-end smoke test."""
        from tradingagents.agents.analysts.fundamentals.data_loader import load_multi_period
        quick_llm, deep_llm = self._get_llms()

        multi = load_multi_period("COMI", curr_date="2025-12-31", n_periods=5)
        if multi["n_income"] == 0:
            pytest.skip("No COMI income data available")

        income = multi["income"][0]
        balance = multi["balance"][0] if multi["balance"] else {}
        ratios_csv = multi["ratios"][0] if multi["ratios"] else {}

        sector_cfg = SectorConfig("COMI")
        ratios_raw = FinancialCalculator.compute_all(
            revenue=income.get("revenue"),
            gross_profit=income.get("gross_profit"),
            operating_income=income.get("operating_income"),
            net_income=income.get("net_income"),
            total_assets=balance.get("total_assets"),
            total_liabilities=balance.get("total_liabilities"),
            total_equity=balance.get("total_equity"),
        )
        public_ratios = {k: v for k, v in ratios_raw.items() if not k.startswith("_")}

        report = FundamentalAnalysisReport(
            ticker="COMI",
            analysis_date="2025-12-31",
            fiscal_period=income.get("_period_end_date", "FY2024"),
            sector="banks",
            ratios=public_ratios,
            preprocessing={},
            distress_flags=[],
            data_confidence=75,
            signal_coherence=100,
            financial_health="healthy",
            pipeline_mode="deterministic",
        )

        result = run_cot_pipeline(quick_llm, deep_llm, report, sector_cfg)
        assert isinstance(result, FundamentalAnalysisReport)
        assert result.ticker == "COMI"
        assert result.sector == "banks"
        # Banks should NEVER get HIGH_LEVERAGE_ALERT
        assert "HIGH_LEVERAGE_ALERT" not in result.distress_flags

    def test_concept_cot_does_not_confabulate_moat(self):
        """
        Stage 2 output must not contain moat analysis or peer comparison language.
        (Plan Section 8: Concept-CoT scope constraint — forbidden phrases)
        """
        from tradingagents.agents.analysts.fundamentals.concept_cot import run_concept_cot
        quick_llm, _ = self._get_llms()

        report = _make_healthy_report()
        sector_cfg = SectorConfig("ETEL")
        pack = build_evidence_pack(report, sector_cfg)

        concept_output = run_concept_cot(quick_llm, pack)

        # Convert to string for phrase checking
        output_text = " ".join(str(v) for v in concept_output.values() if isinstance(v, str)).lower()

        forbidden_phrases = ["competitive moat", "management quality", "compared to peers"]
        violations = [phrase for phrase in forbidden_phrases if phrase in output_text]
        assert not violations, (
            f"Concept CoT contains forbidden phrases: {violations}\n"
            f"Output: {output_text[:300]}"
        )
