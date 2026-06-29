"""P2 regression tests: regime-aware EY interpretation.

Locks:
  - inflation_regime flag derived from date-aware CBE rate (high / normal)
  - No temporal leakage in regime derivation
  - EARNINGS_YIELD_HIGH_RATE_CONTEXT note appears alongside (not replacing)
    EARNINGS_YIELD_COMPRESSED when CBE > 15%
  - thesis_cot system prompt contains high-rate regime guidance
  - concept_cot system prompt contains high-rate regime guidance
  - Calibration preserves "flat" in high-rate non-bank contexts
  - Calibration backward compatibility: banks always "up"
  - Calibration backward compatibility: normal-rate flat→up preserved

Pure-unit: no LLM, no network, no graph.
"""

from __future__ import annotations

import os
import sys

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tradingagents.agents.analysts.fundamentals.calibration import (
    CalibrationResult,
    POLICY_NAME,
    calibrate_earnings_direction,
    _DEFAULT_CALIBRATED_UP_CONFIDENCE,
    _DEFAULT_HIGH_RATE_THRESHOLD,
)
from tradingagents.agents.analysts.fundamentals.data_cot import (
    build_evidence_pack,
    _HIGH_RATE_REGIME_THRESHOLD,
)
from tradingagents.agents.analysts.fundamentals.sector_config import (
    EARNINGS_YIELD_HIGH_RATE_CONTEXT,
    SectorConfig,
    _HIGH_RATE_REGIME_THRESHOLD as SC_HIGH_RATE_THRESHOLD,
)
from tradingagents.agents.analysts.fundamentals.schemas import FundamentalAnalysisReport
from tradingagents.agents.analysts.fundamentals import thesis_cot, concept_cot


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_report(**overrides) -> FundamentalAnalysisReport:
    """Minimal valid FundamentalAnalysisReport."""
    defaults = {
        "ticker": "TMGH.CA",
        "analysis_date": "2024-04-01",
        "fiscal_period": "FY2023",
        "sector": "real_estate",
        "ratios": {
            "roe": 0.12,
            "debt_to_equity": 1.5,
            "pe_ratio": 15.0,
            "pb_ratio": 2.1,
            "earnings_yield": 0.0164,
            "earnings_yield_spread": -0.256,
            "net_margin": 0.15,
            "eps": 1.50,
        },
        "data_confidence": 70,
        "signal_coherence": 85,
        "financial_health": "healthy",
        "key_risks": ["EGX structural risk"],
        "risk_free_rate_value": 0.2725,
        "risk_free_rate_source": "date_aware_cbe_policy_rate",
        "risk_free_rate_effective_date": "2024-03-06",
    }
    defaults.update(overrides)
    return FundamentalAnalysisReport(**defaults)


def _make_sector_cfg(ticker: str = "TMGH") -> SectorConfig:
    return SectorConfig(ticker)


# ─────────────────────────────────────────────────────────────────────────────
# T1-T3: inflation_regime flag from date-aware CBE
# ─────────────────────────────────────────────────────────────────────────────

class TestInflationRegimeFlag:
    """T1-T3: inflation_regime derived from risk_free_rate_value on the report."""

    def test_high_regime_when_cbe_above_threshold(self):
        """T1: CBE = 27.25% → inflation_regime = 'high'."""
        report = _make_report(risk_free_rate_value=0.2725)
        pack = build_evidence_pack(report, _make_sector_cfg())
        assert pack["inflation_regime"] == "high"

    def test_normal_regime_when_cbe_below_threshold(self):
        """T2: CBE = 10.0% → inflation_regime = 'normal'."""
        report = _make_report(risk_free_rate_value=0.10)
        pack = build_evidence_pack(report, _make_sector_cfg())
        assert pack["inflation_regime"] == "normal"

    def test_normal_regime_at_boundary(self):
        """T3: CBE = 15.0% (boundary, ≤ threshold) → 'normal'."""
        report = _make_report(risk_free_rate_value=0.15)
        pack = build_evidence_pack(report, _make_sector_cfg())
        assert pack["inflation_regime"] == "normal"

    def test_normal_regime_when_rate_is_none(self):
        """Missing CBE rate → default to 'normal' (safe fallback)."""
        report = _make_report(risk_free_rate_value=None)
        pack = build_evidence_pack(report, _make_sector_cfg())
        assert pack["inflation_regime"] == "normal"

    def test_regime_in_narrative(self):
        """Regime appears in the narrative text."""
        report = _make_report(risk_free_rate_value=0.2725)
        pack = build_evidence_pack(report, _make_sector_cfg())
        assert "Rate Regime: HIGH" in pack["narrative"]

    def test_normal_regime_in_narrative(self):
        """Normal regime appears in the narrative text."""
        report = _make_report(risk_free_rate_value=0.10)
        pack = build_evidence_pack(report, _make_sector_cfg())
        assert "Rate Regime: NORMAL" in pack["narrative"]


# ─────────────────────────────────────────────────────────────────────────────
# T4: No temporal leakage in regime derivation
# ─────────────────────────────────────────────────────────────────────────────

class TestNoTemporalLeakage:
    """T10: inflation_regime uses the same date-aware CBE as existing macro_provider."""

    def test_regime_derived_from_report_rfr_not_hardcoded(self):
        """Verify inflation_regime changes with the report's risk_free_rate_value,
        proving it is derived from the report (date-aware) and not hardcoded."""
        report_high = _make_report(risk_free_rate_value=0.2725)
        report_low = _make_report(risk_free_rate_value=0.10)

        pack_high = build_evidence_pack(report_high, _make_sector_cfg())
        pack_low = build_evidence_pack(report_low, _make_sector_cfg())

        assert pack_high["inflation_regime"] == "high"
        assert pack_low["inflation_regime"] == "normal"

    def test_threshold_constants_consistent(self):
        """All high-rate thresholds across modules must be identical."""
        assert _HIGH_RATE_REGIME_THRESHOLD == _DEFAULT_HIGH_RATE_THRESHOLD
        assert _HIGH_RATE_REGIME_THRESHOLD == SC_HIGH_RATE_THRESHOLD
        assert _HIGH_RATE_REGIME_THRESHOLD == 0.15


# ─────────────────────────────────────────────────────────────────────────────
# T4-T5: EARNINGS_YIELD_HIGH_RATE_CONTEXT note
# ─────────────────────────────────────────────────────────────────────────────

class TestEYHighRateContextNote:
    """T4-T5: context note appears alongside (not replacing) EARNINGS_YIELD_COMPRESSED."""

    def test_high_rate_note_present_when_cbe_above_threshold(self):
        """T4: When CBE > 15% and EY spread < -2%, both flags appear."""
        cfg = SectorConfig("TMGH")
        flags = cfg.generate_distress_flags(
            net_margin=0.15,
            debt_to_equity=1.5,
            current_ratio=1.2,
            total_equity=1e9,
            revenue=5e9,
            eps=1.5,
            pe_ratio=15.0,
            roe=0.12,
            earnings_yield_spread=-0.256,
            risk_free_rate=0.2725,
        )
        assert "EARNINGS_YIELD_COMPRESSED" in flags
        assert EARNINGS_YIELD_HIGH_RATE_CONTEXT in flags

    def test_no_high_rate_note_when_cbe_below_threshold(self):
        """T5: When CBE ≤ 15%, only EARNINGS_YIELD_COMPRESSED appears, no context note."""
        cfg = SectorConfig("TMGH")
        flags = cfg.generate_distress_flags(
            net_margin=0.15,
            debt_to_equity=1.5,
            current_ratio=1.2,
            total_equity=1e9,
            revenue=5e9,
            eps=1.5,
            pe_ratio=15.0,
            roe=0.12,
            earnings_yield_spread=-0.05,
            risk_free_rate=0.10,
        )
        assert "EARNINGS_YIELD_COMPRESSED" in flags
        assert EARNINGS_YIELD_HIGH_RATE_CONTEXT not in flags

    def test_no_note_when_ey_spread_not_compressed(self):
        """No context note when EY spread is above the compressed threshold."""
        cfg = SectorConfig("TMGH")
        flags = cfg.generate_distress_flags(
            net_margin=0.15,
            debt_to_equity=1.5,
            current_ratio=1.2,
            total_equity=1e9,
            revenue=5e9,
            eps=1.5,
            pe_ratio=15.0,
            roe=0.12,
            earnings_yield_spread=0.10,  # attractive, not compressed
            risk_free_rate=0.2725,
        )
        assert "EARNINGS_YIELD_COMPRESSED" not in flags
        assert EARNINGS_YIELD_HIGH_RATE_CONTEXT not in flags

    def test_no_note_when_risk_free_rate_is_none(self):
        """When risk_free_rate not available, no high-rate context note."""
        cfg = SectorConfig("TMGH")
        flags = cfg.generate_distress_flags(
            net_margin=0.15,
            debt_to_equity=1.5,
            current_ratio=1.2,
            total_equity=1e9,
            revenue=5e9,
            eps=1.5,
            pe_ratio=15.0,
            roe=0.12,
            earnings_yield_spread=-0.256,
            risk_free_rate=None,
        )
        assert "EARNINGS_YIELD_COMPRESSED" in flags
        assert EARNINGS_YIELD_HIGH_RATE_CONTEXT not in flags

    def test_compressed_flag_preserved_not_suppressed(self):
        """The original EARNINGS_YIELD_COMPRESSED flag is NEVER removed by the context note."""
        cfg = SectorConfig("TMGH")
        flags = cfg.generate_distress_flags(
            net_margin=0.15,
            debt_to_equity=1.5,
            current_ratio=1.2,
            total_equity=1e9,
            revenue=5e9,
            eps=1.5,
            pe_ratio=15.0,
            roe=0.12,
            earnings_yield_spread=-0.256,
            risk_free_rate=0.2725,
        )
        # Both must be present — the note is additive, not a replacement
        compressed_idx = flags.index("EARNINGS_YIELD_COMPRESSED")
        context_idx = flags.index(EARNINGS_YIELD_HIGH_RATE_CONTEXT)
        assert compressed_idx < context_idx  # context note follows the flag


# ─────────────────────────────────────────────────────────────────────────────
# T6: thesis_cot prompt contains regime guidance
# ─────────────────────────────────────────────────────────────────────────────

class TestThesisPromptRegimeGuidance:
    """T6: thesis_cot system prompt contains the high-rate regime paragraph."""

    def test_system_prompt_contains_high_rate_guidance(self):
        assert "HIGH-RATE REGIME GUIDANCE" in thesis_cot._SYSTEM_PROMPT

    def test_system_prompt_references_inflation_regime(self):
        assert 'inflation_regime="high"' in thesis_cot._SYSTEM_PROMPT

    def test_system_prompt_mentions_not_standalone_veto(self):
        assert "standalone veto" in thesis_cot._SYSTEM_PROMPT

    def test_system_prompt_mentions_real_estate_and_holdings(self):
        assert "REAL ESTATE" in thesis_cot._SYSTEM_PROMPT
        assert "HOLDINGS" in thesis_cot._SYSTEM_PROMPT

    def test_system_prompt_mentions_pricing_power(self):
        assert "pricing power" in thesis_cot._SYSTEM_PROMPT


# ─────────────────────────────────────────────────────────────────────────────
# T7: concept_cot prompt contains regime guidance
# ─────────────────────────────────────────────────────────────────────────────

class TestConceptPromptRegimeGuidance:
    """T7: concept_cot system prompt contains the high-rate regime note."""

    def test_system_prompt_contains_high_rate_note(self):
        assert "HIGH-RATE REGIME" in concept_cot._SYSTEM_PROMPT

    def test_system_prompt_mentions_valuation_read(self):
        assert "valuation_read" in concept_cot._SYSTEM_PROMPT

    def test_system_prompt_mentions_not_company_specific(self):
        assert "not company-specific" in concept_cot._SYSTEM_PROMPT


# ─────────────────────────────────────────────────────────────────────────────
# T8-T10: Calibration regime-aware flat gate
# ─────────────────────────────────────────────────────────────────────────────

class TestCalibrationHighRateFlatPreservation:
    """T8: flat preserved in high-rate non-bank contexts."""

    def test_flat_preserved_high_rate_real_estate(self):
        """High rate + real_estate → flat survives."""
        r = calibrate_earnings_direction(
            fundamental_outlook="neutral",
            downside_risk_level="moderate",
            raw_earnings_direction="flat",
            earnings_direction_confidence=65,
            sector="real_estate",
            risk_free_rate=0.2725,
        )
        assert r.calibrated_direction == "flat"
        assert "P2" in " ".join(r.notes)

    def test_flat_preserved_high_rate_holdings(self):
        """High rate + holdings → flat survives."""
        r = calibrate_earnings_direction(
            fundamental_outlook="neutral",
            downside_risk_level="moderate",
            raw_earnings_direction="flat",
            earnings_direction_confidence=65,
            sector="holdings",
            risk_free_rate=0.20,
        )
        assert r.calibrated_direction == "flat"

    def test_flat_preserved_high_rate_operational(self):
        """High rate + operational → flat survives."""
        r = calibrate_earnings_direction(
            fundamental_outlook="neutral",
            downside_risk_level="moderate",
            raw_earnings_direction="flat",
            earnings_direction_confidence=65,
            sector="operational",
            risk_free_rate=0.2725,
        )
        assert r.calibrated_direction == "flat"


class TestCalibrationBackwardCompatBanks:
    """T9: Banks always calibrated to 'up' regardless of rate regime."""

    def test_flat_to_up_for_banks_high_rate(self):
        """Banks: flat → up even in high-rate regime."""
        r = calibrate_earnings_direction(
            fundamental_outlook="neutral",
            downside_risk_level="moderate",
            raw_earnings_direction="flat",
            earnings_direction_confidence=65,
            sector="banks",
            risk_free_rate=0.2725,
        )
        assert r.calibrated_direction == "up"
        assert r.calibrated_confidence == _DEFAULT_CALIBRATED_UP_CONFIDENCE

    def test_down_to_up_for_banks_high_rate(self):
        """Banks: down → up even in high-rate regime (unchanged from pre-P2)."""
        r = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="high",
            raw_earnings_direction="down",
            earnings_direction_confidence=90,
            sector="banks",
            risk_free_rate=0.2725,
        )
        assert r.calibrated_direction == "up"


class TestCalibrationBackwardCompatNormalRate:
    """T10: Normal-rate regimes: flat→up conversion preserved."""

    def test_flat_to_up_normal_rate(self):
        """Normal rate: flat → up (pre-P2 behavior preserved)."""
        r = calibrate_earnings_direction(
            fundamental_outlook="neutral",
            downside_risk_level="moderate",
            raw_earnings_direction="flat",
            earnings_direction_confidence=65,
            sector="real_estate",
            risk_free_rate=0.10,
        )
        assert r.calibrated_direction == "up"
        assert r.calibrated_confidence == _DEFAULT_CALIBRATED_UP_CONFIDENCE

    def test_flat_to_up_when_rate_is_none(self):
        """Missing rate → flat → up (safe fallback, backward compat)."""
        r = calibrate_earnings_direction(
            fundamental_outlook="neutral",
            downside_risk_level="moderate",
            raw_earnings_direction="flat",
            earnings_direction_confidence=65,
            sector="real_estate",
            risk_free_rate=None,
        )
        assert r.calibrated_direction == "up"

    def test_flat_to_up_at_rate_boundary(self):
        """Rate exactly at threshold (15%) → normal regime → flat → up."""
        r = calibrate_earnings_direction(
            fundamental_outlook="neutral",
            downside_risk_level="moderate",
            raw_earnings_direction="flat",
            earnings_direction_confidence=65,
            sector="real_estate",
            risk_free_rate=0.15,
        )
        assert r.calibrated_direction == "up"

    def test_down_gate_unchanged_by_rate(self):
        """Down gate is not affected by risk_free_rate — existing behavior preserved."""
        # This should still be calibrated to "down" (full evidence)
        r = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="high",
            raw_earnings_direction="down",
            earnings_direction_confidence=80,
            sector="operational",
            de_ratio=1.5,
            risk_free_rate=0.2725,
        )
        assert r.calibrated_direction == "down"

    def test_up_stays_up_regardless_of_rate(self):
        """raw=up stays up regardless of rate (no change from pre-P2)."""
        r = calibrate_earnings_direction(
            fundamental_outlook="bullish",
            downside_risk_level="low",
            raw_earnings_direction="up",
            earnings_direction_confidence=80,
            risk_free_rate=0.2725,
        )
        assert r.calibrated_direction == "up"
