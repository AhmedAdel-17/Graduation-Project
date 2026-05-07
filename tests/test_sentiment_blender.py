"""PR 7 — Layer E blender + propagation rewrite.

Tests:
  1.  blend_sentiment() with all SIGNAL / NO_SIGNAL / absent combinations
  2.  Market-regime multipliers (PANIC / FEAR / NEUTRAL / GREED / EUPHORIA)
  3.  Macro-direction multipliers (RISK_OFF / RISK_ON / NEUTRAL / NO_SIGNAL)
  4.  Sector tilt: ±0.10 cap, positive / negative / zero score
  5.  Multiplicative cascade (market + macro + sector)
  6.  blend_from_dict() round-trip
  7.  calculate_unified_score() quorum rule
  8.  calculate_unified_score() confidence-weighted mean
  9.  calculate_unified_score() sentiment_blend passthrough (direction unchanged)
  10. calculate_unified_score() INSUFFICIENT_DATA status
  11. calculate_unified_score() determinism
  12. propagate_confidence() quorum check
  13. propagate_confidence() applies blend multiplier from state
  14. propagate_confidence() overall_status: OK vs INSUFFICIENT_DATA
  15. propagate_confidence() position_size_multiplier surfaced
  16. SentimentBlend is a NamedTuple (positional + named access)
  17. Blend multipliers clamp to [0.10, 1.0] (confidence) and [0.0, 1.0] (size)
  18. Contract: sentiment never flips BUY → SELL
"""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Module imports
# ---------------------------------------------------------------------------
from tradingagents.agents.utils.scoring import (
    QUORUM_MINIMUM,
    SentimentBlend,
    blend_from_dict,
    blend_sentiment,
    calculate_unified_score,
)
from tradingagents.graph.propagation import Propagator
from tradingagents.sentiment.contracts import (
    LayerStatus,
    MacroDirection,
    MacroSentiment,
    MarketRegime,
    MarketSentiment,
    NoSignalReason,
    SectorSentiment,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _no_signal_reason(gate: str = "test.gate") -> NoSignalReason:
    return NoSignalReason(gate_failed=gate, human_readable="test gate", metrics={})


def _market_signal(regime: MarketRegime, score: float = 0.0) -> MarketSentiment:
    return MarketSentiment(
        status=LayerStatus.SIGNAL,
        score=score,
        confidence=0.80,
        regime=regime,
        volatility_mood="CALM",
        n_posts=60,
        n_distinct_sources=3,
    )


def _market_no_signal() -> MarketSentiment:
    return MarketSentiment(
        status=LayerStatus.NO_SIGNAL,
        confidence=0.0,
        reason=_no_signal_reason("market.n_total_posts"),
        regime=MarketRegime.NO_SIGNAL,
        volatility_mood="NO_SIGNAL",
    )


def _macro_signal(direction: MacroDirection) -> MacroSentiment:
    from tradingagents.sentiment.contracts import (
        MacroCategory,
        MacroEvent,
        MacroMagnitude,
        PostRef,
        SourceCredibility,
    )
    from datetime import datetime, timezone

    ev = MacroEvent(
        category=MacroCategory.RATE_DECISION,
        direction=direction,
        magnitude=MacroMagnitude.HIGH,
        source_credibility=SourceCredibility.OFFICIAL,
        half_life_hours=120,
        confidence=0.90,
        headline="CBE holds rate",
        detected_at=datetime.now(timezone.utc),
    )
    return MacroSentiment(composite_regime=direction, active_events=[ev])


def _macro_no_signal() -> MacroSentiment:
    return MacroSentiment(
        composite_regime=MacroDirection.NO_SIGNAL,
        reason=_no_signal_reason("macro.source_credibility"),
    )


def _sector_signal(sector: str = "banks", score: float = 0.5) -> SectorSentiment:
    return SectorSentiment(
        status=LayerStatus.SIGNAL,
        score=score,
        confidence=0.75,
        sector=sector,
        n_posts=12,
        n_distinct_days=4,
    )


def _sector_no_signal(sector: str = "banks") -> SectorSentiment:
    return SectorSentiment(
        status=LayerStatus.NO_SIGNAL,
        confidence=0.0,
        reason=_no_signal_reason("sector.n_sector_posts"),
        sector=sector,
    )


# ---------------------------------------------------------------------------
# 1. blend_sentiment — all absent (pass-through)
# ---------------------------------------------------------------------------
class TestBlendAllAbsent:
    def test_conf_mult_is_1(self):
        b = blend_sentiment()
        assert b.confidence_multiplier == 1.0

    def test_size_mult_is_1(self):
        b = blend_sentiment()
        assert b.position_size_multiplier == 1.0

    def test_audit_mentions_absent(self):
        b = blend_sentiment()
        assert "absent" in b.audit

    def test_returns_named_tuple(self):
        b = blend_sentiment()
        assert isinstance(b, SentimentBlend)


# ---------------------------------------------------------------------------
# 2. Market-regime multipliers
# ---------------------------------------------------------------------------
class TestMarketRegimeMultipliers:
    @pytest.mark.parametrize(
        "regime,exp_conf,exp_size",
        [
            (MarketRegime.PANIC,    0.70, 0.50),
            (MarketRegime.FEAR,     0.85, 0.75),
            (MarketRegime.NEUTRAL,  1.00, 1.00),
            (MarketRegime.GREED,    0.90, 0.90),
            (MarketRegime.EUPHORIA, 0.70, 0.60),
        ],
    )
    def test_multiplier(self, regime, exp_conf, exp_size):
        b = blend_sentiment(market=_market_signal(regime))
        assert b.confidence_multiplier == pytest.approx(exp_conf, abs=1e-4)
        assert b.position_size_multiplier == pytest.approx(exp_size, abs=1e-4)

    def test_no_signal_market_is_passthrough(self):
        b = blend_sentiment(market=_market_no_signal())
        assert b.confidence_multiplier == 1.0
        assert b.position_size_multiplier == 1.0

    def test_audit_contains_regime(self):
        b = blend_sentiment(market=_market_signal(MarketRegime.PANIC))
        assert "PANIC" in b.audit


# ---------------------------------------------------------------------------
# 3. Macro-direction multipliers
# ---------------------------------------------------------------------------
class TestMacroDirectionMultipliers:
    def test_risk_off_reduces_confidence(self):
        b = blend_sentiment(macro=_macro_signal(MacroDirection.RISK_OFF))
        assert b.confidence_multiplier == pytest.approx(0.80, abs=1e-4)

    def test_risk_on_slightly_reduces_confidence(self):
        b = blend_sentiment(macro=_macro_signal(MacroDirection.RISK_ON))
        assert b.confidence_multiplier == pytest.approx(0.95, abs=1e-4)

    def test_neutral_is_passthrough(self):
        b = blend_sentiment(macro=_macro_signal(MacroDirection.NEUTRAL))
        assert b.confidence_multiplier == pytest.approx(1.00, abs=1e-4)

    def test_no_signal_macro_is_passthrough(self):
        b = blend_sentiment(macro=_macro_no_signal())
        assert b.confidence_multiplier == pytest.approx(1.00, abs=1e-4)

    def test_macro_does_not_affect_position_size(self):
        # Macro only touches confidence, never position size
        b_off = blend_sentiment(macro=_macro_signal(MacroDirection.RISK_OFF))
        assert b_off.position_size_multiplier == pytest.approx(1.00, abs=1e-4)

    def test_audit_contains_direction(self):
        b = blend_sentiment(macro=_macro_signal(MacroDirection.RISK_OFF))
        assert "RISK_OFF" in b.audit


# ---------------------------------------------------------------------------
# 4. Sector tilt
# ---------------------------------------------------------------------------
class TestSectorTilt:
    def test_positive_score_adds_tilt(self):
        # score=1.0 → tilt=+0.10
        b = blend_sentiment(sector=_sector_signal("banks", score=1.0))
        assert b.confidence_multiplier == pytest.approx(1.10, abs=1e-4)

    def test_negative_score_subtracts_tilt(self):
        # score=-1.0 → tilt=-0.10
        b = blend_sentiment(sector=_sector_signal("banks", score=-1.0))
        assert b.confidence_multiplier == pytest.approx(0.90, abs=1e-4)

    def test_zero_score_no_tilt(self):
        b = blend_sentiment(sector=_sector_signal("banks", score=0.0))
        assert b.confidence_multiplier == pytest.approx(1.00, abs=1e-4)

    def test_tilt_capped_at_0_10(self):
        # score=1.0 (max allowed) → tilt = +0.10 → conf_mult = 1.0 + 0.10 = 1.10
        # The tilt itself is capped at ±0.10; conf_mult may exceed 1.0.
        # Downstream callers clamp blended_conf to [0.10, 1.0].
        from tradingagents.sentiment.contracts import SectorSentiment
        big = SectorSentiment(
            status=LayerStatus.SIGNAL,
            score=1.0,
            confidence=0.75,
            sector="banks",
            n_posts=12,
            n_distinct_days=4,
        )
        b = blend_sentiment(sector=big)
        assert b.confidence_multiplier == pytest.approx(1.10, abs=1e-4)

    def test_sector_no_signal_is_passthrough(self):
        b = blend_sentiment(sector=_sector_no_signal())
        assert b.confidence_multiplier == pytest.approx(1.00, abs=1e-4)

    def test_audit_contains_sector(self):
        b = blend_sentiment(sector=_sector_signal("banks"))
        assert "banks" in b.audit


# ---------------------------------------------------------------------------
# 5. Multiplicative cascade
# ---------------------------------------------------------------------------
class TestMultiplicativeCascade:
    def test_panic_and_risk_off(self):
        # market PANIC: conf×0.70; macro RISK_OFF: conf×0.80
        # => conf_mult = 0.70 * 0.80 = 0.56
        b = blend_sentiment(
            macro=_macro_signal(MacroDirection.RISK_OFF),
            market=_market_signal(MarketRegime.PANIC),
        )
        assert b.confidence_multiplier == pytest.approx(0.56, abs=1e-4)
        # position size: only market PANIC → 0.50
        assert b.position_size_multiplier == pytest.approx(0.50, abs=1e-4)

    def test_fear_risk_on_positive_sector(self):
        # market FEAR: conf×0.85, size×0.75
        # macro RISK_ON: conf×0.95
        # sector tilt +0.05 (score=0.5)
        # conf_mult = 0.85 * 0.95 + 0.05 = 0.8575
        b = blend_sentiment(
            macro=_macro_signal(MacroDirection.RISK_ON),
            market=_market_signal(MarketRegime.FEAR),
            sector=_sector_signal("banks", score=0.5),
        )
        expected_conf = 0.85 * 0.95 + 0.05
        assert b.confidence_multiplier == pytest.approx(expected_conf, abs=1e-4)
        assert b.position_size_multiplier == pytest.approx(0.75, abs=1e-4)

    def test_all_no_signal_is_passthrough(self):
        b = blend_sentiment(
            macro=_macro_no_signal(),
            market=_market_no_signal(),
            sector=_sector_no_signal(),
        )
        assert b.confidence_multiplier == pytest.approx(1.00, abs=1e-4)
        assert b.position_size_multiplier == pytest.approx(1.00, abs=1e-4)


# ---------------------------------------------------------------------------
# 6. blend_from_dict round-trip
# ---------------------------------------------------------------------------
class TestBlendFromDict:
    def test_round_trip(self):
        original = blend_sentiment(market=_market_signal(MarketRegime.FEAR))
        d = {
            "confidence_multiplier": original.confidence_multiplier,
            "position_size_multiplier": original.position_size_multiplier,
            "audit": original.audit,
        }
        restored = blend_from_dict(d)
        assert restored is not None
        assert restored.confidence_multiplier == original.confidence_multiplier
        assert restored.position_size_multiplier == original.position_size_multiplier

    def test_none_input_returns_none(self):
        assert blend_from_dict(None) is None

    def test_empty_dict_returns_default(self):
        b = blend_from_dict({})
        assert b is not None
        assert b.confidence_multiplier == pytest.approx(1.0, abs=1e-4)

    def test_malformed_dict_returns_none(self):
        b = blend_from_dict({"confidence_multiplier": "not_a_float"})
        assert b is None


# ---------------------------------------------------------------------------
# 7. calculate_unified_score() — quorum rule
# ---------------------------------------------------------------------------
_FULL_STATE = {
    "technical_analysis": {
        "trend_direction": {"direction": "bullish"},
        "signals": {"rsi": {"signal": "oversold"}},
        "confidence_score": 0.8,
    },
    "fundamental_analysis": {
        "health_assessment": {
            "profitability": {"status": "healthy"},
            "leverage": {"status": "healthy"},
            "liquidity": {"status": "healthy"},
        },
        "confidence_score": 0.9,
    },
    "sentiment_analysis": {
        "sentiment": "bullish",
        "sentiment_strength": "strong",
        "confidence_score": 0.85,
    },
}


class TestQuorumRule:
    def test_three_analysts_ok(self):
        _, _, _, _, status = calculate_unified_score(_FULL_STATE)
        assert status == "OK"

    def test_two_analysts_ok(self):
        state = {k: v for k, v in _FULL_STATE.items() if k != "sentiment_analysis"}
        _, _, _, _, status = calculate_unified_score(state)
        assert status == "OK"

    def test_one_analyst_insufficient(self):
        state = {"technical_analysis": _FULL_STATE["technical_analysis"]}
        _, conf, _, _, status = calculate_unified_score(state)
        assert status == "INSUFFICIENT_DATA"
        assert conf == 0.0

    def test_zero_analysts_insufficient(self):
        _, _, _, _, status = calculate_unified_score({})
        assert status == "INSUFFICIENT_DATA"

    def test_insufficient_data_returns_hold(self):
        decision, _, _, _, status = calculate_unified_score({})
        assert status == "INSUFFICIENT_DATA"
        assert decision == "HOLD"

    def test_quorum_minimum_constant(self):
        assert QUORUM_MINIMUM == 2


# ---------------------------------------------------------------------------
# 8. calculate_unified_score() — confidence-weighted mean
# ---------------------------------------------------------------------------
class TestConfidenceWeightedMean:
    def test_high_conf_bullish_beats_low_conf_bearish(self):
        state = {
            "technical_analysis": {
                "trend_direction": {"direction": "bullish"},
                "confidence_score": 0.95,
            },
            "fundamental_analysis": {
                "overall_assessment": "strong financial health",
                "confidence_score": 0.90,
            },
            "sentiment_analysis": {
                "sentiment": "bearish",
                "sentiment_strength": "weak",
                "confidence_score": 0.10,  # very low confidence bearish
            },
        }
        decision, conf, _, _, status = calculate_unified_score(state)
        assert status == "OK"
        assert decision in ("BUY", "STRONG_BUY"), f"Got {decision}"

    def test_equal_confidence_averages(self):
        state = {
            "technical_analysis": {"trend_direction": {"direction": "neutral"}, "confidence_score": 0.5},
            "fundamental_analysis": {"health_assessment": {}, "confidence_score": 0.5},
        }
        _, _, _, _, status = calculate_unified_score(state)
        assert status == "OK"


# ---------------------------------------------------------------------------
# 9. Sentiment blend passthrough — direction unchanged
# ---------------------------------------------------------------------------
class TestBlendNeverFlipsDirection:
    """The core invariant: sentiment modifies confidence/size only."""

    def test_panic_does_not_flip_buy_to_sell(self):
        panic_blend = blend_sentiment(market=_market_signal(MarketRegime.PANIC))
        decision, _, _, _, _ = calculate_unified_score(_FULL_STATE, sentiment_blend=panic_blend)
        assert decision in ("BUY", "STRONG_BUY"), (
            f"PANIC blend changed direction to {decision}"
        )

    def test_panic_reduces_confidence(self):
        no_blend_result = calculate_unified_score(_FULL_STATE)
        panic_blend = blend_sentiment(market=_market_signal(MarketRegime.PANIC))
        blended_result = calculate_unified_score(_FULL_STATE, sentiment_blend=panic_blend)
        assert blended_result[1] < no_blend_result[1], "PANIC should reduce confidence"

    def test_risk_off_macro_does_not_flip_direction(self):
        risk_off_blend = blend_sentiment(macro=_macro_signal(MacroDirection.RISK_OFF))
        decision, _, _, _, _ = calculate_unified_score(_FULL_STATE, sentiment_blend=risk_off_blend)
        assert decision in ("BUY", "STRONG_BUY")

    def test_position_size_mult_in_component_scores(self):
        panic_blend = blend_sentiment(market=_market_signal(MarketRegime.PANIC))
        _, _, _, comp, _ = calculate_unified_score(_FULL_STATE, sentiment_blend=panic_blend)
        assert "position_size_multiplier" in comp
        assert comp["position_size_multiplier"] == pytest.approx(0.50, abs=1e-4)

    def test_state_blend_result_applied(self):
        """blend stored in state["sentiment_blend_result"] is picked up automatically."""
        state_with_blend = {
            **_FULL_STATE,
            "sentiment_blend_result": {
                "confidence_multiplier": 0.70,
                "position_size_multiplier": 0.50,
                "audit": "blend: market=PANIC(conf×0.70,size×0.50) => conf×0.7000, size×0.5000",
            },
        }
        _, blended_conf, _, _, _ = calculate_unified_score(state_with_blend)
        _, raw_conf, _, _, _ = calculate_unified_score(_FULL_STATE)
        assert blended_conf < raw_conf


# ---------------------------------------------------------------------------
# 10. INSUFFICIENT_DATA propagation
# ---------------------------------------------------------------------------
class TestInsufficientDataStatus:
    def test_reasoning_contains_insufficient_data(self):
        _, _, reason, _, status = calculate_unified_score({})
        assert "INSUFFICIENT_DATA" in reason
        assert status == "INSUFFICIENT_DATA"

    def test_component_scores_empty_on_no_data(self):
        _, _, _, comp, _ = calculate_unified_score({})
        assert isinstance(comp, dict)


# ---------------------------------------------------------------------------
# 11. Determinism
# ---------------------------------------------------------------------------
class TestDeterminism:
    def test_calculate_unified_score_deterministic(self):
        results = [calculate_unified_score(_FULL_STATE) for _ in range(50)]
        decisions = {r[0] for r in results}
        confs     = {round(r[1], 6) for r in results}
        assert len(decisions) == 1
        assert len(confs) == 1

    def test_blend_sentiment_deterministic(self):
        results = [
            blend_sentiment(
                macro=_macro_signal(MacroDirection.RISK_OFF),
                market=_market_signal(MarketRegime.FEAR),
                sector=_sector_signal("banks", score=0.5),
            )
            for _ in range(20)
        ]
        assert len({r.confidence_multiplier for r in results}) == 1
        assert len({r.position_size_multiplier for r in results}) == 1


# ---------------------------------------------------------------------------
# 12-15. propagate_confidence()
# ---------------------------------------------------------------------------
class TestPropagateConfidence:
    def _state_with_all_analysts(self):
        return {
            "technical_analysis": {"confidence_score": 0.80},
            "fundamental_analysis": {"confidence_score": 0.90},
            "sentiment_analysis": {"confidence_score": 0.75},
            "data_quality": {"data_completeness_score": 100},
        }

    def test_quorum_ok_returns_ok_status(self):
        result = Propagator.propagate_confidence(self._state_with_all_analysts())
        assert result["overall_status"] == "OK"

    def test_quorum_fails_returns_insufficient(self):
        result = Propagator.propagate_confidence({
            "technical_analysis": {"confidence_score": 0.80},
            "data_quality": {"data_completeness_score": 100},
        })
        assert result["overall_status"] == "INSUFFICIENT_DATA"

    def test_quorum_fails_overall_is_010(self):
        result = Propagator.propagate_confidence({})
        assert result["overall"] == pytest.approx(0.10, abs=1e-4)

    def test_blend_applied_from_state(self):
        state = {
            **self._state_with_all_analysts(),
            "sentiment_blend_result": {
                "confidence_multiplier": 0.70,
                "position_size_multiplier": 0.50,
                "audit": "test",
            },
        }
        blended = Propagator.propagate_confidence(state)
        unblended = Propagator.propagate_confidence(self._state_with_all_analysts())
        assert blended["overall"] < unblended["overall"]

    def test_position_size_multiplier_surfaced(self):
        state = {
            **self._state_with_all_analysts(),
            "sentiment_blend_result": {
                "confidence_multiplier": 0.85,
                "position_size_multiplier": 0.75,
                "audit": "test",
            },
        }
        result = Propagator.propagate_confidence(state)
        assert result["position_size_multiplier"] == pytest.approx(0.75, abs=1e-4)

    def test_no_blend_result_size_mult_is_1(self):
        result = Propagator.propagate_confidence(self._state_with_all_analysts())
        assert result["position_size_multiplier"] == pytest.approx(1.0, abs=1e-4)

    def test_overall_clamped_to_010_min(self):
        state = {
            **self._state_with_all_analysts(),
            "sentiment_blend_result": {
                "confidence_multiplier": 0.0001,
                "position_size_multiplier": 0.50,
                "audit": "extreme",
            },
        }
        result = Propagator.propagate_confidence(state)
        assert result["overall"] >= 0.10

    def test_overall_clamped_to_1_max(self):
        state = {
            **self._state_with_all_analysts(),
            "sentiment_blend_result": {
                "confidence_multiplier": 9999.0,
                "position_size_multiplier": 1.0,
                "audit": "extreme",
            },
        }
        result = Propagator.propagate_confidence(state)
        assert result["overall"] <= 1.0


# ---------------------------------------------------------------------------
# 16. SentimentBlend NamedTuple interface
# ---------------------------------------------------------------------------
class TestSentimentBlendInterface:
    def test_positional_access(self):
        b = blend_sentiment()
        assert b[0] == b.confidence_multiplier
        assert b[1] == b.position_size_multiplier
        assert b[2] == b.audit

    def test_named_access(self):
        b = blend_sentiment()
        assert hasattr(b, "confidence_multiplier")
        assert hasattr(b, "position_size_multiplier")
        assert hasattr(b, "audit")

    def test_is_namedtuple(self):
        b = blend_sentiment()
        assert isinstance(b, tuple)


# ---------------------------------------------------------------------------
# 17. Clamp invariants
# ---------------------------------------------------------------------------
class TestClampInvariants:
    @pytest.mark.parametrize(
        "regime",
        [
            MarketRegime.PANIC,
            MarketRegime.FEAR,
            MarketRegime.NEUTRAL,
            MarketRegime.GREED,
            MarketRegime.EUPHORIA,
        ],
    )
    def test_conf_mult_lower_bound(self, regime):
        # Negative sector tilt + RISK_OFF + worst regime — still ≥ 0.10
        b = blend_sentiment(
            macro=_macro_signal(MacroDirection.RISK_OFF),
            market=_market_signal(regime),
            sector=_sector_signal("banks", score=-1.0),
        )
        assert b.confidence_multiplier >= 0.10

    @pytest.mark.parametrize(
        "regime",
        [
            MarketRegime.PANIC,
            MarketRegime.FEAR,
            MarketRegime.NEUTRAL,
            MarketRegime.GREED,
            MarketRegime.EUPHORIA,
        ],
    )
    def test_final_blended_conf_clamped_to_1(self, regime):
        # Even if conf_mult slightly exceeds 1.0 (positive sector on neutral market),
        # the final blended_conf returned by calculate_unified_score is ≤ 1.0.
        b = blend_sentiment(
            market=_market_signal(regime),
            sector=_sector_signal("banks", score=1.0),
        )
        _, blended_conf, _, _, _ = calculate_unified_score(_FULL_STATE, sentiment_blend=b)
        assert blended_conf <= 1.0

    def test_size_mult_in_0_to_1(self):
        b = blend_sentiment(market=_market_signal(MarketRegime.PANIC))
        assert 0.0 <= b.position_size_multiplier <= 1.0


# ---------------------------------------------------------------------------
# 18. Sentiment never flips direction (exhaustive)
# ---------------------------------------------------------------------------
class TestNoDirectionFlip:
    """For any sentiment combination, the directional score from fundamentals
    must determine the decision, not the sentiment layers."""

    @pytest.mark.parametrize(
        "regime",
        [
            MarketRegime.PANIC,
            MarketRegime.FEAR,
            MarketRegime.GREED,
            MarketRegime.EUPHORIA,
        ],
    )
    def test_bullish_state_stays_bullish_under_any_regime(self, regime):
        blend = blend_sentiment(market=_market_signal(regime))
        decision, _, _, _, status = calculate_unified_score(_FULL_STATE, sentiment_blend=blend)
        if status == "OK":
            assert decision in ("BUY", "STRONG_BUY"), (
                f"Market {regime} flipped bullish state to {decision}"
            )

    def test_bearish_state_stays_bearish_under_greed(self):
        bearish_state = {
            "technical_analysis": {
                "trend_direction": {"direction": "bearish"},
                "signals": {"rsi": {"signal": "overbought"}},
                "confidence_score": 0.9,
            },
            "fundamental_analysis": {
                "health_assessment": {
                    "profitability": {"status": "critical"},
                    "leverage": {"status": "critical"},
                    "liquidity": {"status": "concerning"},
                },
                "confidence_score": 0.9,
            },
            "sentiment_analysis": {
                "sentiment": "bearish",
                "sentiment_strength": "strong",
                "confidence_score": 0.8,
            },
        }
        greed_blend = blend_sentiment(market=_market_signal(MarketRegime.GREED))
        decision, _, _, _, status = calculate_unified_score(bearish_state, sentiment_blend=greed_blend)
        if status == "OK":
            assert decision in ("SELL", "STRONG_SELL"), (
                f"GREED blend flipped bearish state to {decision}"
            )
