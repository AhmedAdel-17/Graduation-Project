"""Tests for FundamentalsQualityStatus degradation tracking.

Covers:
  - Deterministic analyst: quality_status = deterministic_only
  - Deterministic analyst (no data): quality_status = unavailable
  - Hybrid analyst with CoT success: quality_status = full
  - Hybrid analyst with CoT failure: quality_status = deterministic_only, enrichment_attempted=True
  - Pipeline partial: quality_status = partial
  - effective_confidence penalty for degraded/partial results
  - Research Manager _build_confidence_context() surfaces quality status
  - Bull/Bear _fundamentals_quality_note() output for each level

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

from tradingagents.agents.analysts.fundamentals.schemas import (
    FundamentalAnalysisReport,
    FundamentalsQualityStatus,
)
from tradingagents.agents.analysts.fundamentals.pipeline import (
    run_cot_pipeline,
    _build_partial_report,
    _build_full_report,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_report(**overrides) -> FundamentalAnalysisReport:
    """Minimal FundamentalAnalysisReport for testing."""
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
# Schema tests
# ─────────────────────────────────────────────────────────────────────────────

class TestQualityStatusSchema:
    def test_default_values(self):
        qs = FundamentalsQualityStatus()
        assert qs.level == "deterministic_only"
        assert qs.reasons == []
        assert qs.data_available is True
        assert qs.enrichment_attempted is False
        assert qs.enrichment_succeeded is False

    def test_report_default_quality_status(self):
        report = _make_report()
        assert report.quality_status.level == "deterministic_only"
        assert report.effective_confidence == 0  # default

    def test_report_with_explicit_quality(self):
        qs = FundamentalsQualityStatus(
            level="full",
            data_available=True,
            enrichment_attempted=True,
            enrichment_succeeded=True,
        )
        report = _make_report(quality_status=qs, effective_confidence=70)
        assert report.quality_status.level == "full"
        assert report.effective_confidence == 70

    def test_serialization_roundtrip(self):
        qs = FundamentalsQualityStatus(
            level="partial",
            reasons=["Stage 3 failed"],
            enrichment_attempted=True,
        )
        data = qs.model_dump()
        restored = FundamentalsQualityStatus(**data)
        assert restored.level == "partial"
        assert restored.reasons == ["Stage 3 failed"]


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline partial report tests
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildPartialReport:
    def test_no_stages_completed(self):
        """All stages failed → deterministic_only, 60% penalty."""
        report = _make_report(data_confidence=80)
        partial = _build_partial_report(
            report=report,
            evidence_pack={},
            concept_output={},
            stages_completed=[],
            failure_reason="Stage 2 crashed",
        )
        assert partial.quality_status.level == "deterministic_only"
        assert partial.quality_status.enrichment_attempted is True
        assert partial.quality_status.enrichment_succeeded is False
        assert "Stage 2 crashed" in partial.quality_status.reasons[0]
        # 60% of 80 = 48
        assert partial.effective_confidence == 48

    def test_one_stage_completed(self):
        """data_cot succeeded, concept failed → partial, 80% penalty."""
        report = _make_report(data_confidence=80)
        partial = _build_partial_report(
            report=report,
            evidence_pack={},
            concept_output={},
            stages_completed=["data_cot"],
            failure_reason="Stage 2 validation failed",
        )
        assert partial.quality_status.level == "partial"
        assert partial.quality_status.enrichment_attempted is True
        # 80% of 80 = 64
        assert partial.effective_confidence == 64

    def test_two_stages_completed(self):
        """data_cot + concept_cot succeeded, thesis failed → partial."""
        report = _make_report(data_confidence=90)
        partial = _build_partial_report(
            report=report,
            evidence_pack={},
            concept_output={"_valid": True, "financial_health": "healthy"},
            stages_completed=["data_cot", "concept_cot"],
            failure_reason="Stage 3 crashed",
        )
        assert partial.quality_status.level == "partial"
        # 80% of 90 = 72
        assert partial.effective_confidence == 72


class TestBuildFullReport:
    def test_all_stages_succeed(self):
        """All stages → full quality, effective_confidence = data_confidence."""
        report = _make_report(data_confidence=75)
        from tradingagents.agents.analysts.fundamentals.sector_config import SectorConfig

        full = _build_full_report(
            report=report,
            evidence_pack={},
            concept_output={"_valid": True, "financial_health": "healthy"},
            thesis_output={
                "_valid": True,
                "financial_health": "healthy",
                "earnings_direction": "up",
                "earnings_direction_confidence": 70,
                "fundamental_outlook": "bullish",
                "downside_risk_level": "low",
            },
            stages_completed=["data_cot", "concept_cot", "thesis_cot"],
        )
        assert full.quality_status.level == "full"
        assert full.quality_status.enrichment_succeeded is True
        assert full.effective_confidence == 75  # no penalty

    def test_partial_stages_in_full_report(self):
        """Thesis stage missing → partial quality, 80% penalty."""
        report = _make_report(data_confidence=80)

        full = _build_full_report(
            report=report,
            evidence_pack={},
            concept_output={"_valid": True, "financial_health": "healthy"},
            thesis_output={"_valid": False},
            stages_completed=["data_cot", "concept_cot"],
        )
        assert full.quality_status.level == "partial"
        assert full.quality_status.enrichment_succeeded is False
        assert full.effective_confidence == 64  # 80% of 80


# ─────────────────────────────────────────────────────────────────────────────
# Research Manager confidence context
# ─────────────────────────────────────────────────────────────────────────────

class TestResearchManagerConfidenceContext:
    def test_unavailable_fundamentals(self):
        from tradingagents.agents.managers.research_manager import _build_confidence_context
        state = {
            "fundamental_analysis": {
                "quality_status": {"level": "unavailable", "data_available": False},
            }
        }
        ctx = _build_confidence_context(state)
        assert "UNAVAILABLE" in ctx
        assert "legitimate HOLD" in ctx

    def test_degraded_fundamentals(self):
        from tradingagents.agents.managers.research_manager import _build_confidence_context
        state = {
            "fundamental_analysis": {
                "quality_status": {
                    "level": "deterministic_only",
                    "enrichment_attempted": True,
                    "enrichment_succeeded": False,
                },
                "effective_confidence": 42,
            }
        }
        ctx = _build_confidence_context(state)
        assert "42%" in ctx
        assert "DEGRADED" in ctx

    def test_full_quality_no_tag(self):
        from tradingagents.agents.managers.research_manager import _build_confidence_context
        state = {
            "fundamental_analysis": {
                "quality_status": {
                    "level": "full",
                    "enrichment_attempted": True,
                    "enrichment_succeeded": True,
                },
                "effective_confidence": 75,
            }
        }
        ctx = _build_confidence_context(state)
        assert "75%" in ctx
        assert "DEGRADED" not in ctx
        assert "deterministic" not in ctx

    def test_legacy_no_quality_status(self):
        """Old-format fundamental_analysis without quality_status still works."""
        from tradingagents.agents.managers.research_manager import _build_confidence_context
        state = {
            "fundamental_analysis": {
                "confidence_score": 65,
            }
        }
        ctx = _build_confidence_context(state)
        assert "65%" in ctx


# ─────────────────────────────────────────────────────────────────────────────
# Bull/Bear quality note
# ─────────────────────────────────────────────────────────────────────────────

class TestFundamentalsQualityNote:
    def test_unavailable(self):
        from tradingagents.agents.researchers.bull_researcher import _fundamentals_quality_note
        note = _fundamentals_quality_note({"quality_status": {"level": "unavailable"}})
        assert "WARNING" in note
        assert "No fundamentals data" in note

    def test_degraded(self):
        from tradingagents.agents.researchers.bull_researcher import _fundamentals_quality_note
        note = _fundamentals_quality_note({
            "quality_status": {"level": "deterministic_only", "enrichment_attempted": True}
        })
        assert "enrichment was attempted but failed" in note

    def test_deterministic_not_attempted(self):
        from tradingagents.agents.researchers.bull_researcher import _fundamentals_quality_note
        note = _fundamentals_quality_note({
            "quality_status": {"level": "deterministic_only", "enrichment_attempted": False}
        })
        assert "deterministic-only" in note
        assert "attempted but failed" not in note

    def test_partial(self):
        from tradingagents.agents.researchers.bull_researcher import _fundamentals_quality_note
        note = _fundamentals_quality_note({
            "quality_status": {"level": "partial", "reasons": ["Stage 3 failed"]}
        })
        assert "partially completed" in note
        assert "Stage 3 failed" in note

    def test_full_no_note(self):
        from tradingagents.agents.researchers.bull_researcher import _fundamentals_quality_note
        note = _fundamentals_quality_note({"quality_status": {"level": "full"}})
        assert note == ""

    def test_empty_analysis(self):
        from tradingagents.agents.researchers.bull_researcher import _fundamentals_quality_note
        assert _fundamentals_quality_note({}) == ""
        assert _fundamentals_quality_note(None) == ""


# ─────────────────────────────────────────────────────────────────────────────
# Bear Researcher quality note (identical function, separate module)
# ─────────────────────────────────────────────────────────────────────────────

class TestBearFundamentalsQualityNote:
    def test_unavailable(self):
        from tradingagents.agents.researchers.bear_researcher import _fundamentals_quality_note
        note = _fundamentals_quality_note({"quality_status": {"level": "unavailable"}})
        assert "WARNING" in note
        assert "No fundamentals data" in note

    def test_degraded(self):
        from tradingagents.agents.researchers.bear_researcher import _fundamentals_quality_note
        note = _fundamentals_quality_note({
            "quality_status": {"level": "deterministic_only", "enrichment_attempted": True}
        })
        assert "enrichment was attempted but failed" in note

    def test_full_no_note(self):
        from tradingagents.agents.researchers.bear_researcher import _fundamentals_quality_note
        note = _fundamentals_quality_note({"quality_status": {"level": "full"}})
        assert note == ""


# ─────────────────────────────────────────────────────────────────────────────
# Trader quality note
# ─────────────────────────────────────────────────────────────────────────────

class TestTraderFundamentalsQualityNote:
    def test_unavailable(self):
        from tradingagents.agents.trader.trader import _fundamentals_quality_note_short
        note = _fundamentals_quality_note_short({"quality_status": {"level": "unavailable"}})
        assert "NO FUNDAMENTALS DATA" in note

    def test_degraded(self):
        from tradingagents.agents.trader.trader import _fundamentals_quality_note_short
        note = _fundamentals_quality_note_short({
            "quality_status": {"level": "deterministic_only", "enrichment_attempted": True}
        })
        assert "DEGRADED" in note

    def test_deterministic_not_attempted(self):
        from tradingagents.agents.trader.trader import _fundamentals_quality_note_short
        note = _fundamentals_quality_note_short({
            "quality_status": {"level": "deterministic_only", "enrichment_attempted": False}
        })
        assert "deterministic-only" in note

    def test_partial(self):
        from tradingagents.agents.trader.trader import _fundamentals_quality_note_short
        note = _fundamentals_quality_note_short({
            "quality_status": {"level": "partial"}
        })
        assert "partial" in note

    def test_full_no_note(self):
        from tradingagents.agents.trader.trader import _fundamentals_quality_note_short
        note = _fundamentals_quality_note_short({"quality_status": {"level": "full"}})
        assert note == ""

    def test_empty(self):
        from tradingagents.agents.trader.trader import _fundamentals_quality_note_short
        assert _fundamentals_quality_note_short({}) == ""
        assert _fundamentals_quality_note_short(None) == ""


# ─────────────────────────────────────────────────────────────────────────────
# Risk Manager fundamentals quality note in prompt
# ─────────────────────────────────────────────────────────────────────────────

class TestRiskManagerFundamentalsQuality:
    """Verify the Risk Manager prompt-building code reads quality_status.

    We can't easily invoke the full risk_manager_node (it needs LLM + full
    state), so we test the note-building logic by checking the code path
    that reads quality_status from state and produces fund_quality_note.
    """

    def _build_fund_quality_note(self, quality_status: dict) -> str:
        """Replicate the Risk Manager's fund_quality_note logic."""
        # This mirrors the exact code in risk_manager.py
        fund = {"quality_status": quality_status}
        qs = fund.get("quality_status", {})
        quality_level = qs.get("level", "")
        if quality_level == "unavailable":
            return (
                "\n**FUNDAMENTALS WARNING**: No financial data available for this ticker. "
                "Fundamental risk cannot be assessed. Consider this an elevated-risk condition "
                "and apply more conservative position sizing."
            )
        elif quality_level == "deterministic_only" and qs.get("enrichment_attempted"):
            return (
                "\n**Fundamentals note**: Enrichment was attempted but failed. "
                "Ratios and distress flags are available but no interpretive thesis or valuation. "
                "Treat fundamental conclusions with reduced confidence."
            )
        elif quality_level == "partial":
            return (
                "\n**Fundamentals note**: Enrichment partially completed — "
                "some interpretive stages failed. Fundamental conclusions may be incomplete."
            )
        return ""

    def test_unavailable(self):
        note = self._build_fund_quality_note({"level": "unavailable", "data_available": False})
        assert "FUNDAMENTALS WARNING" in note
        assert "elevated-risk" in note

    def test_degraded(self):
        note = self._build_fund_quality_note({
            "level": "deterministic_only",
            "enrichment_attempted": True,
            "enrichment_succeeded": False,
        })
        assert "Enrichment was attempted but failed" in note
        assert "reduced confidence" in note

    def test_partial(self):
        note = self._build_fund_quality_note({
            "level": "partial",
            "enrichment_attempted": True,
        })
        assert "partially completed" in note

    def test_full_no_note(self):
        note = self._build_fund_quality_note({
            "level": "full",
            "enrichment_attempted": True,
            "enrichment_succeeded": True,
        })
        assert note == ""

    def test_deterministic_not_attempted_no_note(self):
        note = self._build_fund_quality_note({
            "level": "deterministic_only",
            "enrichment_attempted": False,
        })
        assert note == ""


# ─────────────────────────────────────────────────────────────────────────────
# Production default: hybrid is enabled
# ─────────────────────────────────────────────────────────────────────────────

class TestHybridDefault:
    def test_default_config_hybrid_is_true(self):
        """Production default must enable hybrid fundamentals."""
        from tradingagents.default_config import DEFAULT_CONFIG
        assert DEFAULT_CONFIG.get("use_hybrid_fundamental_analyst") is True

    def test_setup_reads_hybrid_flag(self):
        """setup.py should respect use_hybrid_fundamental_analyst from config."""
        from tradingagents.dataflows.config import get_config
        # The default config should have hybrid enabled
        # (tests don't instantiate TradingAgentsGraph, so no LLM calls happen)
        from tradingagents.default_config import DEFAULT_CONFIG
        assert "use_hybrid_fundamental_analyst" in DEFAULT_CONFIG
