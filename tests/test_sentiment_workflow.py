"""PR 8 — LLM-as-explainer demotion + bull/bear NO_SIGNAL guard.

Tests:
  1.  social_media_analyst: Layer C NO_SIGNAL → returns template + blend pass-through
  2.  social_media_analyst: blend_result dict written to state
  3.  social_media_analyst: blend_result dict has required keys
  4.  social_media_analyst: _extract_narrative strips directional fields
  5.  social_media_analyst: _extract_narrative with JSON block
  6.  social_media_analyst: _extract_narrative fallback to plain text
  7.  social_media_analyst: _build_sentiment_report with real narrative
  8.  social_media_analyst: _build_sentiment_report with NO_SIGNAL narrative
  9.  social_media_analyst: _compute_blend_result all-None falls back to pass-through
  10. social_media_analyst: _compute_blend_result parses JSON market_sentiment
  11. social_media_analyst: _try_build_market_sentiment SIGNAL dict
  12. social_media_analyst: _try_build_market_sentiment NO_SIGNAL dict
  13. social_media_analyst: _try_build_market_sentiment invalid dict → None
  14. social_media_analyst: _try_build_macro_sentiment RISK_OFF
  15. social_media_analyst: _try_build_macro_sentiment NO_SIGNAL
  16. social_media_analyst: _try_build_sector_sentiment SIGNAL
  17. bull_researcher: _format_sentiment_section NO_SIGNAL → exclusion notice
  18. bull_researcher: _format_sentiment_section with real report + blend
  19. bull_researcher: _format_sentiment_section with None blend
  20. bear_researcher: _format_sentiment_section NO_SIGNAL → exclusion notice
  21. bear_researcher: _format_sentiment_section real report surfaces multipliers
  22. NO_SIGNAL detection: all known template phrases trigger exclusion
  23. Blend pass-through: conf×1.0, size×1.0 when all layers absent
  24. LLM_ROLE_INSTRUCTION present in module constant
  25. _NO_SIGNAL_TEMPLATE unchanged (contract with pre-LLM gate)
"""
from __future__ import annotations

import json

import pytest

from tradingagents.agents.analysts.social_media_analyst import (
    _NO_SIGNAL_TEMPLATE,
    _LLM_ROLE_INSTRUCTION,
    _build_sentiment_report,
    _compute_blend_result,
    _extract_narrative,
    _try_build_macro_sentiment,
    _try_build_market_sentiment,
    _try_build_sector_sentiment,
    _try_layer_c_gate,
)
from tradingagents.agents.researchers.bull_researcher import (
    _format_sentiment_section as bull_format,
)
from tradingagents.agents.researchers.bear_researcher import (
    _format_sentiment_section as bear_format,
)


# ---------------------------------------------------------------------------
# 1. Layer C NO_SIGNAL gate return value
# ---------------------------------------------------------------------------
class TestLayerCGate:
    def test_empty_datapoints_returns_no_signal(self):
        # 0 datapoints → all gates fail → NO_SIGNAL → True
        result = _try_layer_c_gate("COMI.CA", [], "2026-01-01")
        assert result is True

    def test_none_datapoints_returns_false(self):
        # Passing a non-list should not crash; returns False (safe fallback)
        result = _try_layer_c_gate("COMI.CA", None, "2026-01-01")  # type: ignore[arg-type]
        assert result is False

    def test_insufficient_datapoints_no_signal(self):
        # 2 low-confidence points → MEGA tier gates fail → NO_SIGNAL
        points = [
            {"timestamp": "2026-01-01T10:00:00Z", "platform": "facebook",
             "author": "user1", "sentiment_score": 0.5, "weight": 1.0,
             "entity_confidence": 0.50, "is_spam_promo": False},
            {"timestamp": "2026-01-01T10:05:00Z", "platform": "facebook",
             "author": "user2", "sentiment_score": 0.4, "weight": 1.0,
             "entity_confidence": 0.50, "is_spam_promo": False},
        ]
        result = _try_layer_c_gate("COMI.CA", points, "2026-01-01")
        assert result is True  # <8 strong mentions required for MEGA


# ---------------------------------------------------------------------------
# 2-3. blend_result written with required keys
# ---------------------------------------------------------------------------
class TestBlendResultDict:
    def test_compute_blend_result_has_required_keys(self):
        state = {}
        result = _compute_blend_result(state, "COMI.CA")
        assert "confidence_multiplier" in result
        assert "position_size_multiplier" in result
        assert "audit" in result

    def test_compute_blend_result_defaults_to_passthrough(self):
        state = {}
        result = _compute_blend_result(state, "COMI.CA")
        assert result["confidence_multiplier"] == pytest.approx(1.0, abs=1e-4)
        assert result["position_size_multiplier"] == pytest.approx(1.0, abs=1e-4)

    def test_compute_blend_result_non_json_prefetch_is_safe(self):
        state = {"prefetched_social_sentiment": "This is plain text, not JSON."}
        result = _compute_blend_result(state, "COMI.CA")
        # Falls back gracefully
        assert result["confidence_multiplier"] == pytest.approx(1.0, abs=1e-4)


# ---------------------------------------------------------------------------
# 4-6. _extract_narrative
# ---------------------------------------------------------------------------
class TestExtractNarrative:
    def test_json_block_no_directional_fields(self):
        text = (
            '```json\n'
            '{"narrative": "Retail investors bullish on COMI.", "cited_post_ids": ["p1"]}\n'
            '```'
        )
        result = _extract_narrative(text)
        assert "narrative" in result
        assert "cited_post_ids" in result
        assert "sentiment_score" not in result
        assert "direction" not in result
        assert "confidence" not in result

    def test_json_block_extracts_narrative(self):
        text = '```json\n{"narrative": "Strong banking sector discussion.", "cited_post_ids": []}\n```'
        result = _extract_narrative(text)
        assert result["narrative"] == "Strong banking sector discussion."

    def test_json_block_extracts_post_ids(self):
        text = '```json\n{"narrative": "...", "cited_post_ids": ["abc", "xyz"]}\n```'
        result = _extract_narrative(text)
        assert result["cited_post_ids"] == ["abc", "xyz"]

    def test_fallback_to_plain_text(self):
        text = "Retail investors are optimistic about COMI's Q3 results."
        result = _extract_narrative(text)
        assert result["narrative"] == text
        assert result["cited_post_ids"] == []

    def test_directional_fields_stripped_from_llm_noncompliance(self):
        # LLM included forbidden fields — they should be stripped
        text = (
            '```json\n'
            '{"narrative": "Bullish tone.", "sentiment_score": 0.8, '
            '"direction": "bullish", "cited_post_ids": []}\n'
            '```'
        )
        result = _extract_narrative(text)
        assert "sentiment_score" not in result
        assert "direction" not in result

    def test_empty_input_returns_empty_narrative(self):
        result = _extract_narrative("")
        assert isinstance(result["narrative"], str)


# ---------------------------------------------------------------------------
# 7-8. _build_sentiment_report
# ---------------------------------------------------------------------------
class TestBuildSentimentReport:
    def test_real_narrative_included(self):
        blend = {"confidence_multiplier": 0.85, "position_size_multiplier": 0.75, "audit": "x"}
        report = _build_sentiment_report("COMI.CA", "Investors positive.", blend)
        assert "Investors positive." in report

    def test_no_signal_narrative_returns_excluded(self):
        blend = {"confidence_multiplier": 1.0, "position_size_multiplier": 1.0, "audit": "pass"}
        report = _build_sentiment_report("COMI.CA", _NO_SIGNAL_TEMPLATE, blend)
        assert "EXCLUDED" in report

    def test_blend_multipliers_surfaced(self):
        blend = {"confidence_multiplier": 0.70, "position_size_multiplier": 0.50, "audit": "x"}
        report = _build_sentiment_report("COMI.CA", "Some narrative.", blend)
        assert "0.70" in report
        assert "0.50" in report

    def test_direction_invariant_note_present(self):
        blend = {"confidence_multiplier": 1.0, "position_size_multiplier": 1.0, "audit": "x"}
        report = _build_sentiment_report("COMI.CA", "Narrative.", blend)
        assert "directional" in report.lower()


# ---------------------------------------------------------------------------
# 9-10. _compute_blend_result with structured prefetch
# ---------------------------------------------------------------------------
class TestComputeBlendResultStructured:
    def test_panic_market_in_prefetch_reduces_size_mult(self):
        structured = {
            "market_sentiment": {
                "status": "SIGNAL",
                "score": -0.4,
                "confidence": 0.8,
                "regime": "PANIC",
                "n_posts": 60,
                "n_distinct_sources": 3,
            }
        }
        state = {"prefetched_social_sentiment": json.dumps(structured)}
        result = _compute_blend_result(state, "COMI.CA")
        assert result["position_size_multiplier"] == pytest.approx(0.50, abs=1e-4)

    def test_risk_off_macro_reduces_conf_mult(self):
        structured = {
            "macro_sentiment": {
                "composite_regime": "RISK_OFF",
            }
        }
        state = {"prefetched_social_sentiment": json.dumps(structured)}
        result = _compute_blend_result(state, "COMI.CA")
        assert result["confidence_multiplier"] == pytest.approx(0.80, abs=1e-4)

    def test_no_signal_market_is_passthrough(self):
        structured = {
            "market_sentiment": {
                "status": "NO_SIGNAL",
            }
        }
        state = {"prefetched_social_sentiment": json.dumps(structured)}
        result = _compute_blend_result(state, "COMI.CA")
        assert result["confidence_multiplier"] == pytest.approx(1.0, abs=1e-4)
        assert result["position_size_multiplier"] == pytest.approx(1.0, abs=1e-4)


# ---------------------------------------------------------------------------
# 11-13. _try_build_market_sentiment
# ---------------------------------------------------------------------------
class TestTryBuildMarketSentiment:
    def test_signal_dict_returns_market_sentiment(self):
        raw = {
            "status": "SIGNAL",
            "score": 0.3,
            "confidence": 0.75,
            "regime": "GREED",
            "n_posts": 80,
            "n_distinct_sources": 4,
        }
        result = _try_build_market_sentiment(raw)
        assert result is not None
        from tradingagents.sentiment.contracts import LayerStatus, MarketRegime
        assert result.status == LayerStatus.SIGNAL
        assert result.regime == MarketRegime.GREED

    def test_no_signal_dict_returns_no_signal_object(self):
        raw = {"status": "NO_SIGNAL"}
        result = _try_build_market_sentiment(raw)
        assert result is not None
        from tradingagents.sentiment.contracts import LayerStatus
        assert result.status == LayerStatus.NO_SIGNAL

    def test_invalid_input_returns_none(self):
        assert _try_build_market_sentiment(None) is None
        assert _try_build_market_sentiment("bad") is None
        assert _try_build_market_sentiment(42) is None

    def test_missing_score_in_signal_returns_none(self):
        raw = {"status": "SIGNAL", "confidence": 0.75}
        result = _try_build_market_sentiment(raw)
        assert result is None


# ---------------------------------------------------------------------------
# 14-15. _try_build_macro_sentiment
# ---------------------------------------------------------------------------
class TestTryBuildMacroSentiment:
    def test_risk_off_returns_macro_sentiment(self):
        raw = {"composite_regime": "RISK_OFF"}
        result = _try_build_macro_sentiment(raw)
        assert result is not None
        from tradingagents.sentiment.contracts import MacroDirection
        assert result.composite_regime == MacroDirection.RISK_OFF

    def test_no_signal_direction(self):
        raw = {"composite_regime": "NO_SIGNAL"}
        result = _try_build_macro_sentiment(raw)
        assert result is not None
        from tradingagents.sentiment.contracts import MacroDirection
        assert result.composite_regime == MacroDirection.NO_SIGNAL

    def test_none_input_returns_none(self):
        assert _try_build_macro_sentiment(None) is None


# ---------------------------------------------------------------------------
# 16. _try_build_sector_sentiment
# ---------------------------------------------------------------------------
class TestTryBuildSectorSentiment:
    def test_signal_dict_returns_sector_sentiment(self):
        raw = {
            "status": "SIGNAL",
            "score": 0.4,
            "confidence": 0.70,
            "sector": "banks",
            "n_posts": 15,
            "n_distinct_days": 4,
        }
        result = _try_build_sector_sentiment(raw, "COMI.CA")
        assert result is not None
        from tradingagents.sentiment.contracts import LayerStatus
        assert result.status == LayerStatus.SIGNAL
        assert result.sector == "banks"

    def test_none_returns_none(self):
        assert _try_build_sector_sentiment(None, "COMI.CA") is None


# ---------------------------------------------------------------------------
# 17-19. bull_researcher._format_sentiment_section
# ---------------------------------------------------------------------------
class TestBullFormatSentimentSection:
    def test_no_signal_returns_exclusion_notice(self):
        result = bull_format(_NO_SIGNAL_TEMPLATE, None)
        assert "EXCLUDED" in result
        assert "Do NOT" in result

    def test_no_signal_phrase_detection(self):
        for phrase in [
            "Social sentiment: insufficient data — excluded.",
            "social sentiment: insufficient data — excluded.",
            "LAYER_C_STATUS: NO_SIGNAL",
        ]:
            result = bull_format(phrase, None)
            assert "EXCLUDED" in result, f"Failed for phrase: {phrase!r}"

    def test_real_report_includes_narrative(self):
        report = "[Social sentiment for COMI.CA]\nRetail investors are optimistic."
        blend = {"confidence_multiplier": 0.85, "position_size_multiplier": 0.75, "audit": "x"}
        result = bull_format(report, blend)
        assert "optimistic" in result

    def test_real_report_surfaces_multipliers(self):
        report = "Good report."
        blend = {"confidence_multiplier": 0.70, "position_size_multiplier": 0.50, "audit": "x"}
        result = bull_format(report, blend)
        assert "0.70" in result
        assert "0.50" in result

    def test_none_blend_shows_default_multipliers(self):
        result = bull_format("Some report.", None)
        assert "1.00" in result

    def test_execution_only_instruction_present(self):
        result = bull_format("Narrative.", {"confidence_multiplier": 0.9, "position_size_multiplier": 0.9, "audit": "x"})
        assert "execution" in result.lower()


# ---------------------------------------------------------------------------
# 20-21. bear_researcher._format_sentiment_section
# ---------------------------------------------------------------------------
class TestBearFormatSentimentSection:
    def test_no_signal_exclusion_notice(self):
        result = bear_format(_NO_SIGNAL_TEMPLATE, None)
        assert "EXCLUDED" in result

    def test_real_report_surfaces_multipliers(self):
        report = "Market in FEAR mode."
        blend = {"confidence_multiplier": 0.85, "position_size_multiplier": 0.75, "audit": "x"}
        result = bear_format(report, blend)
        assert "0.85" in result
        assert "0.75" in result


# ---------------------------------------------------------------------------
# 22. All NO_SIGNAL trigger phrases
# ---------------------------------------------------------------------------
class TestNoSignalPhraseDetection:
    @pytest.mark.parametrize(
        "phrase",
        [
            "Social sentiment: insufficient data — excluded.",
            "social sentiment: insufficient data",
            "Social sentiment: INSUFFICIENT DATA — EXCLUDED.",
            "layer_c_status: no_signal",
        ],
    )
    def test_bull_detects_no_signal(self, phrase):
        result = bull_format(phrase, None)
        assert "EXCLUDED" in result

    @pytest.mark.parametrize(
        "phrase",
        [
            "Social sentiment: insufficient data — excluded.",
            "layer_c_status: no_signal",
        ],
    )
    def test_bear_detects_no_signal(self, phrase):
        result = bear_format(phrase, None)
        assert "EXCLUDED" in result


# ---------------------------------------------------------------------------
# 23. Blend pass-through invariant
# ---------------------------------------------------------------------------
class TestBlendPassThrough:
    def test_all_absent_gives_passthrough(self):
        state = {}
        result = _compute_blend_result(state, "TMGH.CA")
        assert result["confidence_multiplier"] == pytest.approx(1.0, abs=1e-4)
        assert result["position_size_multiplier"] == pytest.approx(1.0, abs=1e-4)

    def test_audit_mentions_pass_through(self):
        state = {}
        result = _compute_blend_result(state, "TMGH.CA")
        assert "pass-through" in result["audit"]


# ---------------------------------------------------------------------------
# 24-25. Module-level constants
# ---------------------------------------------------------------------------
class TestModuleConstants:
    def test_llm_role_instruction_present(self):
        assert "explainer only" in _LLM_ROLE_INSTRUCTION.lower()
        assert "narrative" in _LLM_ROLE_INSTRUCTION.lower()
        assert "cited_post_ids" in _LLM_ROLE_INSTRUCTION

    def test_no_signal_template_unchanged(self):
        # Contract: PR 5 set this exact string; changing it breaks the guard
        assert _NO_SIGNAL_TEMPLATE == "Social sentiment: insufficient data — excluded."

    def test_llm_role_instruction_forbids_directional_fields(self):
        assert "sentiment_score" in _LLM_ROLE_INSTRUCTION
        assert "direction" in _LLM_ROLE_INSTRUCTION
        assert "confidence" in _LLM_ROLE_INSTRUCTION
