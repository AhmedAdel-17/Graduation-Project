"""Tests for the competing-hypotheses 3-call H&P thesis pipeline.

Covers:
  - Call 1: generates 2-4 competing hypotheses with direction diversity
  - Call 2: maps evidence and scores each hypothesis independently
  - Call 3: selects best-supported hypothesis and produces thesis
  - Full pipeline: all 3 calls succeed end-to-end
  - Fallback chain: each call failure handled gracefully
  - Output shape matches original single-call for _build_full_report compatibility
  - Config flag toggles between single and 3call modes

Pure-unit: no actual LLM calls, no network.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tradingagents.agents.analysts.fundamentals.thesis_cot_3call import (
    _run_call_1_hypotheses,
    _run_call_2_evidence_map,
    _run_call_3_select_thesis,
    run_thesis_cot_3call,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_llm(response_text: str):
    """Create a mock LLM that returns the given text."""
    mock = MagicMock()
    mock.invoke.return_value = MagicMock(content=response_text)
    return mock


def _make_failing_llm(error_msg: str = "Connection timeout"):
    """Create a mock LLM that raises on invoke."""
    mock = MagicMock()
    mock.invoke.side_effect = RuntimeError(error_msg)
    return mock


@pytest.fixture
def evidence_pack():
    return {
        "ticker": "COMI.CA",
        "sector": "banks",
        "narrative": "ROE: 25.3% (improving). D/E: 6.2 (normal for banks). NIM expanding.",
        "data_confidence": 85,
        "_valid": True,
    }


@pytest.fixture
def concept_output():
    return {
        "financial_health": "healthy",
        "financial_health_rationale": "Strong NIM and stable NPLs.",
        "key_metrics_discussion": "ROE above sector average.",
        "growth_signal": "positive",
        "growth_signal_rationale": "NIM expansion + loan growth.",
        "valuation_read": "fair",
        "valuation_rationale": "P/E in line with sector.",
        "risk_factors": ["FX volatility", "Rate reversal risk"],
        "standout_signals": ["NIM expansion"],
        "analyst_note": "none",
        "_valid": True,
    }


def _valid_hypotheses_response():
    """Standard valid Call 1 response with 3 competing hypotheses."""
    return json.dumps({
        "hypotheses": [
            {
                "id": "H1",
                "direction": "up",
                "statement": "COMI earnings will grow driven by NIM expansion and loan book growth.",
                "rationale": "ROE at 25.3% and improving trend supports continued profitability.",
            },
            {
                "id": "H2",
                "direction": "flat",
                "statement": "COMI earnings will stagnate as rate cycle peaks compress NIM.",
                "rationale": "D/E at 6.2 limits further leverage; rate environment may shift.",
            },
            {
                "id": "H3",
                "direction": "down",
                "statement": "COMI earnings will decline due to FX-driven NPL deterioration.",
                "rationale": "EGP devaluation risk could trigger asset quality problems.",
            },
        ]
    })


def _valid_scored_response():
    """Standard valid Call 2 response scoring all 3 hypotheses."""
    return json.dumps({
        "scored_hypotheses": [
            {
                "id": "H1",
                "evidence_for": ["ROE 25.3% improving", "NIM expanding"],
                "evidence_against": ["D/E at 6.2 limits balance sheet flexibility"],
                "evidence_support_score": 78,
                "score_rationale": "Strong profitability metrics directly support growth thesis.",
            },
            {
                "id": "H2",
                "evidence_for": ["D/E already elevated at 6.2"],
                "evidence_against": ["ROE improving contradicts stagnation", "NIM still expanding"],
                "evidence_support_score": 42,
                "score_rationale": "Limited evidence for peak thesis; NIM still growing.",
            },
            {
                "id": "H3",
                "evidence_for": ["FX volatility is a known risk"],
                "evidence_against": ["No NPL deterioration visible in data", "ROE healthy"],
                "evidence_support_score": 25,
                "score_rationale": "Speculative; no current data supports NPL spike.",
            },
        ]
    })


def _valid_thesis_response():
    """Standard valid Call 3 response selecting H1."""
    return json.dumps({
        "selected_hypothesis_id": "H1",
        "selection_rationale": "H1 scored 78 vs H2 at 42 and H3 at 25. NIM expansion and ROE improvement provide direct evidence for earnings growth.",
        "thesis_text": "COMI is well-positioned for continued earnings growth driven by NIM expansion. ROE at 25.3% and improving trend supports the bullish thesis.",
        "fundamental_outlook": "bullish",
        "downside_risk_level": "moderate",
        "earnings_direction": "up",
        "earnings_direction_confidence": 75,
        "earnings_direction_rationale": "Strong NIM expansion with improving ROE; H2 stagnation thesis scored only 42.",
        "valuation_assessment": "fair_value",
        "valuation_rationale": "P/E in line with sector.",
        "financial_health": "healthy",
        "key_risks": ["Rate cycle peak could compress NIM (H2)", "FX-driven NPL risk (H3)"],
        "egx_specific_risks": ["EGP devaluation"],
        "invalidation_conditions": ["NIM compression >50bps next quarter (would validate H2)"],
    })


# ---------------------------------------------------------------------------
# Call 1: Competing Hypotheses
# ---------------------------------------------------------------------------


class TestCall1Hypotheses:
    def test_valid_3_hypotheses(self, evidence_pack):
        llm = _make_llm(_valid_hypotheses_response())
        result = _run_call_1_hypotheses(llm, evidence_pack)
        assert result["_valid"] is True
        assert len(result["hypotheses"]) == 3
        # Check direction diversity
        directions = {h["direction"] for h in result["hypotheses"]}
        assert len(directions) >= 2

    def test_requires_minimum_2_hypotheses(self, evidence_pack):
        llm = _make_llm(json.dumps({
            "hypotheses": [
                {"id": "H1", "direction": "up", "statement": "Only one.", "rationale": "..."}
            ]
        }))
        result = _run_call_1_hypotheses(llm, evidence_pack)
        assert result["_valid"] is False
        assert any("2-4 hypotheses" in e or "fewer than 2" in e for e in result["_validation_errors"])

    def test_requires_direction_diversity(self, evidence_pack):
        llm = _make_llm(json.dumps({
            "hypotheses": [
                {"id": "H1", "direction": "up", "statement": "Growth A.", "rationale": "..."},
                {"id": "H2", "direction": "up", "statement": "Growth B.", "rationale": "..."},
            ]
        }))
        result = _run_call_1_hypotheses(llm, evidence_pack)
        assert result["_valid"] is False
        assert any("same direction" in e for e in result["_validation_errors"])

    def test_truncates_more_than_4(self, evidence_pack):
        llm = _make_llm(json.dumps({
            "hypotheses": [
                {"id": f"H{i}", "direction": "up" if i % 2 == 0 else "down",
                 "statement": f"Hyp {i}.", "rationale": "..."}
                for i in range(1, 7)
            ]
        }))
        result = _run_call_1_hypotheses(llm, evidence_pack)
        assert len(result["hypotheses"]) <= 4

    def test_json_parse_failure(self, evidence_pack):
        llm = _make_llm("This is not JSON")
        result = _run_call_1_hypotheses(llm, evidence_pack)
        assert result["_valid"] is False

    def test_llm_exception(self, evidence_pack):
        llm = _make_failing_llm()
        result = _run_call_1_hypotheses(llm, evidence_pack)
        assert result["_valid"] is False
        assert "LLM failed" in result["_validation_errors"][0]


# ---------------------------------------------------------------------------
# Call 2: Evidence Mapping & Scoring
# ---------------------------------------------------------------------------


class TestCall2EvidenceMap:
    def test_valid_scoring(self, evidence_pack, concept_output):
        hypotheses = [
            {"id": "H1", "direction": "up", "statement": "Growth.", "rationale": "..."},
            {"id": "H2", "direction": "down", "statement": "Decline.", "rationale": "..."},
        ]
        llm = _make_llm(json.dumps({
            "scored_hypotheses": [
                {"id": "H1", "evidence_for": ["ROE 25%"], "evidence_against": ["D/E high"],
                 "evidence_support_score": 72, "score_rationale": "Strong support."},
                {"id": "H2", "evidence_for": ["FX risk"], "evidence_against": ["ROE healthy"],
                 "evidence_support_score": 35, "score_rationale": "Weak support."},
            ]
        }))
        result = _run_call_2_evidence_map(llm, evidence_pack, concept_output, hypotheses)
        assert result["_valid"] is True
        assert len(result["scored_hypotheses"]) == 2
        scores = {s["id"]: s["evidence_support_score"] for s in result["scored_hypotheses"]}
        assert scores["H1"] == 72
        assert scores["H2"] == 35

    def test_scores_clamped_to_0_100(self, evidence_pack, concept_output):
        hypotheses = [
            {"id": "H1", "direction": "up", "statement": "A.", "rationale": "..."},
            {"id": "H2", "direction": "down", "statement": "B.", "rationale": "..."},
        ]
        llm = _make_llm(json.dumps({
            "scored_hypotheses": [
                {"id": "H1", "evidence_for": ["x"], "evidence_against": ["y"],
                 "evidence_support_score": 150, "score_rationale": "..."},
                {"id": "H2", "evidence_for": ["a"], "evidence_against": ["b"],
                 "evidence_support_score": -20, "score_rationale": "..."},
            ]
        }))
        result = _run_call_2_evidence_map(llm, evidence_pack, concept_output, hypotheses)
        scores = {s["id"]: s["evidence_support_score"] for s in result["scored_hypotheses"]}
        assert scores["H1"] == 100
        assert scores["H2"] == 0

    def test_missing_hypothesis_score_flagged(self, evidence_pack, concept_output):
        hypotheses = [
            {"id": "H1", "direction": "up", "statement": "A.", "rationale": "..."},
            {"id": "H2", "direction": "down", "statement": "B.", "rationale": "..."},
        ]
        llm = _make_llm(json.dumps({
            "scored_hypotheses": [
                {"id": "H1", "evidence_for": ["x"], "evidence_against": ["y"],
                 "evidence_support_score": 70, "score_rationale": "..."},
                # H2 missing
            ]
        }))
        result = _run_call_2_evidence_map(llm, evidence_pack, concept_output, hypotheses)
        assert result["_valid"] is False
        assert any("not scored" in e for e in result["_validation_errors"])

    def test_llm_exception(self, evidence_pack, concept_output):
        hypotheses = [{"id": "H1", "direction": "up", "statement": "A.", "rationale": "..."}]
        llm = _make_failing_llm()
        result = _run_call_2_evidence_map(llm, evidence_pack, concept_output, hypotheses)
        assert result["_valid"] is False


# ---------------------------------------------------------------------------
# Call 3: Selection & Thesis
# ---------------------------------------------------------------------------


class TestCall3SelectThesis:
    def test_valid_selection(self, concept_output):
        hypotheses = [
            {"id": "H1", "direction": "up", "statement": "Growth.", "rationale": "..."},
            {"id": "H2", "direction": "down", "statement": "Decline.", "rationale": "..."},
        ]
        scored = [
            {"id": "H1", "evidence_for": ["ROE"], "evidence_against": ["D/E"],
             "evidence_support_score": 75, "score_rationale": "Strong."},
            {"id": "H2", "evidence_for": ["FX"], "evidence_against": ["ROE"],
             "evidence_support_score": 30, "score_rationale": "Weak."},
        ]
        llm = _make_llm(_valid_thesis_response())
        result = _run_call_3_select_thesis(
            llm, hypotheses, scored, concept_output, "COMI.CA", "banks",
        )
        assert result["_valid"] is True
        assert result["selected_hypothesis_id"] == "H1"
        assert result["earnings_direction"] == "up"
        assert result["fundamental_outlook"] == "bullish"
        assert result["earnings_direction_confidence"] == 75

    def test_invalid_direction_defaults_to_flat(self, concept_output):
        llm = _make_llm(json.dumps({
            "selected_hypothesis_id": "H1",
            "selection_rationale": "Best supported.",
            "thesis_text": "Analysis complete.",
            "fundamental_outlook": "bullish",
            "downside_risk_level": "low",
            "earnings_direction": "sideways",  # invalid
            "earnings_direction_confidence": 60,
            "financial_health": "healthy",
            "key_risks": ["risk1"],
        }))
        result = _run_call_3_select_thesis(
            llm, [], [], concept_output, "COMI.CA", "banks",
        )
        assert result["earnings_direction"] == "flat"
        assert result["_valid"] is False

    def test_llm_exception(self, concept_output):
        llm = _make_failing_llm()
        result = _run_call_3_select_thesis(
            llm, [], [], concept_output, "COMI.CA", "banks",
        )
        assert result["_valid"] is False


# ---------------------------------------------------------------------------
# Full 3-call pipeline (run_thesis_cot_3call)
# ---------------------------------------------------------------------------


class TestFullPipeline3Call:
    def test_all_three_calls_succeed(self, evidence_pack, concept_output):
        """Happy path: all 3 calls succeed, output has all fields."""
        call_count = [0]

        def mock_invoke(messages):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(content=_valid_hypotheses_response())
            elif call_count[0] == 2:
                return MagicMock(content=_valid_scored_response())
            else:
                return MagicMock(content=_valid_thesis_response())

        llm = MagicMock()
        llm.invoke.side_effect = mock_invoke

        result = run_thesis_cot_3call(llm, evidence_pack, concept_output)

        assert result["_valid"] is True
        assert result["_thesis_mode"] == "3call"
        assert call_count[0] == 3
        # Selected hypothesis preserved
        assert "NIM expansion" in result["hypothesis"]
        # Evidence from winning hypothesis
        assert len(result["evidence_for"]) >= 1
        assert len(result["evidence_against"]) >= 1
        # Competing hypotheses preserved for audit
        assert len(result["competing_hypotheses"]) == 3
        assert len(result["scored_hypotheses"]) == 3
        # Thesis fields present
        assert result["earnings_direction"] == "up"
        assert result["thesis_text"] != ""
        assert result["fundamental_outlook"] == "bullish"

    def test_call1_failure_short_circuits(self, evidence_pack, concept_output):
        """If Call 1 fails, no further calls are made."""
        llm = _make_failing_llm()
        result = run_thesis_cot_3call(llm, evidence_pack, concept_output)

        assert result["_valid"] is False
        assert result["_thesis_mode"] == "3call"
        assert "hypotheses" not in result
        llm.invoke.assert_called_once()

    def test_call2_failure_preserves_hypotheses(self, evidence_pack, concept_output):
        """If Call 2 fails, hypotheses from Call 1 are still in the result."""
        call_count = [0]

        def mock_invoke(messages):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(content=_valid_hypotheses_response())
            else:
                raise RuntimeError("Call 2 network error")

        llm = MagicMock()
        llm.invoke.side_effect = mock_invoke

        result = run_thesis_cot_3call(llm, evidence_pack, concept_output)

        assert result["_valid"] is False
        assert len(result["hypotheses"]) == 3
        assert "Call 2 LLM failed" in result["_validation_errors"][0]

    def test_output_shape_compatible_with_build_full_report(self, evidence_pack, concept_output):
        """The output dict contains all keys that _build_full_report reads."""
        call_count = [0]

        def mock_invoke(messages):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(content=_valid_hypotheses_response())
            elif call_count[0] == 2:
                return MagicMock(content=_valid_scored_response())
            else:
                return MagicMock(content=_valid_thesis_response())

        llm = MagicMock()
        llm.invoke.side_effect = mock_invoke

        result = run_thesis_cot_3call(llm, evidence_pack, concept_output)

        # These are the keys _build_full_report reads from thesis_output
        required_keys = [
            "financial_health", "earnings_direction",
            "earnings_direction_confidence", "fundamental_outlook",
            "downside_risk_level", "valuation_assessment",
            "thesis_text", "key_risks", "_valid",
        ]
        for key in required_keys:
            assert key in result, f"Missing key '{key}' required by _build_full_report"

    def test_rejected_hypotheses_become_risks(self, evidence_pack, concept_output):
        """The rejected hypotheses should inform key_risks."""
        call_count = [0]

        def mock_invoke(messages):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(content=_valid_hypotheses_response())
            elif call_count[0] == 2:
                return MagicMock(content=_valid_scored_response())
            else:
                return MagicMock(content=_valid_thesis_response())

        llm = MagicMock()
        llm.invoke.side_effect = mock_invoke

        result = run_thesis_cot_3call(llm, evidence_pack, concept_output)

        # key_risks should reference rejected hypotheses
        risks_text = " ".join(result["key_risks"])
        assert "H2" in risks_text or "H3" in risks_text or "NIM" in risks_text


# ---------------------------------------------------------------------------
# Config flag dispatch
# ---------------------------------------------------------------------------


class TestConfigDispatch:
    """Verify pipeline.py dispatches to the correct thesis runner based on config."""

    def test_default_config_uses_3call(self):
        from tradingagents.default_config import DEFAULT_CONFIG
        assert DEFAULT_CONFIG.get("thesis_cot_mode") == "3call"

    def test_pipeline_imports_both_runners(self):
        """Both runners are importable from pipeline module."""
        from tradingagents.agents.analysts.fundamentals import pipeline
        assert hasattr(pipeline, "run_thesis_cot")
        assert hasattr(pipeline, "run_thesis_cot_3call")

    def test_3call_mode_dispatches_to_3call_runner(self, evidence_pack, concept_output):
        """When config says '3call', the 3-call runner is used."""
        from tradingagents.agents.analysts.fundamentals import pipeline
        from tradingagents.agents.analysts.fundamentals.schemas import FundamentalAnalysisReport

        # Stage 2 (concept_cot) uses quick_llm — needs concept-shaped output
        quick_llm = _make_llm(json.dumps({
            "financial_health": "healthy",
            "financial_health_rationale": "Strong margins.",
            "key_metrics_discussion": "ROE above sector.",
            "growth_signal": "positive",
            "growth_signal_rationale": "NIM growth.",
            "valuation_read": "fair",
            "valuation_rationale": "OK.",
            "risk_factors": ["FX risk"],
            "standout_signals": ["NIM"],
            "coherence_notes": "none",
            "analyst_note": "none",
        }))

        # Stage 3 (thesis_cot_3call) uses deep_llm — 3 sequential calls
        deep_call_count = [0]

        def deep_invoke(messages):
            deep_call_count[0] += 1
            if deep_call_count[0] == 1:
                return MagicMock(content=_valid_hypotheses_response())
            elif deep_call_count[0] == 2:
                return MagicMock(content=_valid_scored_response())
            else:
                return MagicMock(content=_valid_thesis_response())

        deep_llm = MagicMock()
        deep_llm.invoke.side_effect = deep_invoke

        report = FundamentalAnalysisReport(
            ticker="COMI.CA",
            analysis_date="2024-06-01",
            fiscal_period="FY2023",
            sector="banks",
            ratios={"roe": 0.25, "debt_to_equity": 6.2},
            preprocessing={},
            distress_flags=[],
            data_confidence=85,
            signal_coherence=90,
        )

        from tradingagents.agents.analysts.fundamentals.sector_config import SectorConfig
        sector_cfg = SectorConfig("COMI.CA")

        with patch.object(pipeline, "get_config", return_value={
            "thesis_cot_mode": "3call",
            "use_fundamental_memory": False,
        }):
            result = pipeline.run_cot_pipeline(
                quick_llm=quick_llm,
                deep_llm=deep_llm,
                report=report,
                sector_cfg=sector_cfg,
            )

        # deep_llm called 3 times (competing hypotheses H&P)
        assert deep_call_count[0] == 3
        assert result.pipeline_mode == "cot_full"
        assert result.earnings_direction == "up"

        # Competing-hypotheses audit trail persists in the final report
        assert len(result.competing_hypotheses) == 3
        assert len(result.scored_hypotheses) == 3
        assert result.selected_hypothesis_id == "H1"

        # Verify scores are preserved
        scores = {s["id"]: s["evidence_support_score"] for s in result.scored_hypotheses}
        assert scores["H1"] == 78
        assert scores["H2"] == 42
        assert scores["H3"] == 25

    def test_single_mode_leaves_audit_fields_empty(self, evidence_pack, concept_output):
        """When thesis_cot_mode='single', the competing-hypotheses fields are empty."""
        from tradingagents.agents.analysts.fundamentals import pipeline
        from tradingagents.agents.analysts.fundamentals.schemas import FundamentalAnalysisReport

        # Single-call thesis response (original format)
        single_llm = _make_llm(json.dumps({
            "hypothesis": "COMI grows.",
            "evidence_for": ["ROE high"],
            "evidence_against": ["D/E elevated"],
            "synthesis": "Net positive.",
            "thesis_text": "COMI is well-positioned.",
            "fundamental_outlook": "bullish",
            "downside_risk_level": "low",
            "earnings_direction": "up",
            "earnings_direction_confidence": 70,
            "earnings_direction_rationale": "Strong.",
            "valuation_assessment": "fair_value",
            "valuation_rationale": "In line.",
            "financial_health": "healthy",
            "key_risks": ["FX risk"],
            "invalidation_conditions": ["NIM drop"],
        }))

        # quick_llm for Stage 2
        quick_llm = _make_llm(json.dumps({
            "financial_health": "healthy",
            "financial_health_rationale": "Strong.",
            "key_metrics_discussion": "ROE above sector.",
            "growth_signal": "positive",
            "growth_signal_rationale": "NIM.",
            "valuation_read": "fair",
            "valuation_rationale": "OK.",
            "risk_factors": ["FX"],
            "standout_signals": ["NIM"],
            "coherence_notes": "none",
            "analyst_note": "none",
        }))

        report = FundamentalAnalysisReport(
            ticker="COMI.CA",
            analysis_date="2024-06-01",
            fiscal_period="FY2023",
            sector="banks",
            ratios={"roe": 0.25, "debt_to_equity": 6.2},
            preprocessing={},
            distress_flags=[],
            data_confidence=85,
            signal_coherence=90,
        )

        from tradingagents.agents.analysts.fundamentals.sector_config import SectorConfig
        sector_cfg = SectorConfig("COMI.CA")

        with patch.object(pipeline, "get_config", return_value={
            "thesis_cot_mode": "single",
            "use_fundamental_memory": False,
        }):
            result = pipeline.run_cot_pipeline(
                quick_llm=quick_llm,
                deep_llm=single_llm,
                report=report,
                sector_cfg=sector_cfg,
            )

        # In single mode, competing-hypotheses fields should be empty
        assert result.competing_hypotheses == []
        assert result.scored_hypotheses == []
        assert result.selected_hypothesis_id == ""
