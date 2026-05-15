"""PR 1 — typed contracts: schema invariants, NO_SIGNAL handling, audit format."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from tradingagents.sentiment.contracts import (
    LayerStatus,
    MacroCategory,
    MacroDirection,
    MacroEvent,
    MacroMagnitude,
    MacroSentiment,
    MarketRegime,
    MarketSentiment,
    NoSignalReason,
    PostRef,
    SectorSentiment,
    SentimentContext,
    SourceCredibility,
    StockSentiment,
    VolatilityMood,
)


def _no_signal_reason() -> NoSignalReason:
    return NoSignalReason(
        gate_failed="stock.n_strong_mentions",
        human_readable="insufficient strong mentions (2 < required 5 for COMI[MEGA])",
        metrics={"observed": 2, "required": 5, "ticker": "COMI", "tier": "MEGA"},
    )


def test_no_signal_reason_renders_canonical_audit_string() -> None:
    r = _no_signal_reason()
    s = r.to_log_str()
    assert s.startswith("NO_SIGNAL: ")
    assert "insufficient strong mentions (2 < required 5 for COMI[MEGA])" in s
    assert "gate=stock.n_strong_mentions" in s
    assert "observed=2" in s and "required=5" in s and "ticker=COMI" in s
    assert str(r) == s


def test_no_signal_reason_without_metrics_is_still_well_formed() -> None:
    r = NoSignalReason(
        gate_failed="market.min_total_posts",
        human_readable="only 8 posts in 24h window (need 50)",
    )
    s = r.to_log_str()
    assert s == (
        "NO_SIGNAL: only 8 posts in 24h window (need 50) "
        "(gate=market.min_total_posts)"
    )


def test_signal_layer_requires_score_and_forbids_reason() -> None:
    MarketSentiment(
        status=LayerStatus.SIGNAL,
        score=-0.4,
        confidence=0.8,
        regime=MarketRegime.FEAR,
        volatility_mood=VolatilityMood.ELEVATED,
        n_posts=80,
        n_distinct_sources=3,
    )

    with pytest.raises(ValueError, match="score is required"):
        MarketSentiment(
            status=LayerStatus.SIGNAL,
            score=None,
            confidence=0.8,
            regime=MarketRegime.FEAR,
        )

    with pytest.raises(ValueError, match="reason must be None"):
        MarketSentiment(
            status=LayerStatus.SIGNAL,
            score=-0.4,
            confidence=0.8,
            regime=MarketRegime.FEAR,
            reason=_no_signal_reason(),
        )


def test_no_signal_layer_forbids_score_and_requires_reason() -> None:
    MarketSentiment(
        status=LayerStatus.NO_SIGNAL,
        regime=MarketRegime.NO_SIGNAL,
        reason=_no_signal_reason(),
    )

    with pytest.raises(ValueError, match="score must be None"):
        MarketSentiment(
            status=LayerStatus.NO_SIGNAL,
            score=0.0,
            regime=MarketRegime.NO_SIGNAL,
            reason=_no_signal_reason(),
        )

    with pytest.raises(ValueError, match="reason is required"):
        MarketSentiment(
            status=LayerStatus.NO_SIGNAL,
            regime=MarketRegime.NO_SIGNAL,
        )


def test_insufficient_data_and_low_confidence_use_same_invariants() -> None:
    for st in (LayerStatus.INSUFFICIENT_DATA, LayerStatus.LOW_CONFIDENCE):
        StockSentiment(
            status=st,
            ticker="COMI",
            tier="MEGA",
            reason=_no_signal_reason(),
        )
        with pytest.raises(ValueError, match="score must be None"):
            StockSentiment(
                status=st,
                ticker="COMI",
                tier="MEGA",
                score=0.1,
                reason=_no_signal_reason(),
            )


def test_score_clamped_to_unit_interval() -> None:
    with pytest.raises(ValidationError):
        StockSentiment(
            status=LayerStatus.SIGNAL,
            ticker="COMI",
            tier="MEGA",
            score=1.5,
        )
    with pytest.raises(ValidationError):
        StockSentiment(
            status=LayerStatus.SIGNAL,
            ticker="COMI",
            tier="MEGA",
            score=-2.0,
        )


def test_post_ref_entity_confidence_is_bounded() -> None:
    PostRef(source="reddit", post_id="x1", entity_confidence=0.9)
    with pytest.raises(ValidationError):
        PostRef(source="reddit", post_id="x1", entity_confidence=1.4)


def test_macro_sentiment_requires_reason_when_no_signal() -> None:
    MacroSentiment(
        composite_regime=MacroDirection.NO_SIGNAL,
        active_events=[],
        reason=NoSignalReason(
            gate_failed="macro.no_corroborated_event",
            human_readable="no event cleared TIER2 + 2-source corroboration",
        ),
    )
    with pytest.raises(ValueError, match="reason is required"):
        MacroSentiment(composite_regime=MacroDirection.NO_SIGNAL, active_events=[])


def test_macro_event_round_trip() -> None:
    ev = MacroEvent(
        category=MacroCategory.RATE_DECISION,
        direction=MacroDirection.RISK_OFF,
        magnitude=MacroMagnitude.HIGH,
        source_credibility=SourceCredibility.OFFICIAL,
        half_life_hours=120,
        confidence=0.95,
        headline="CBE raises overnight rate by 200bps",
        detected_at=datetime.now(timezone.utc),
    )
    assert ev.model_dump()["category"] == "RATE_DECISION"


def test_sentiment_context_assembles_typed_layers() -> None:
    market = MarketSentiment(
        status=LayerStatus.NO_SIGNAL,
        regime=MarketRegime.NO_SIGNAL,
        reason=NoSignalReason(
            gate_failed="market.min_total_posts",
            human_readable="only 8 posts (need 50)",
        ),
    )
    sector = SectorSentiment(
        status=LayerStatus.NO_SIGNAL,
        sector="banks",
        reason=NoSignalReason(
            gate_failed="sector.min_sector_posts",
            human_readable="only 2 banks-sector posts (need 10)",
        ),
    )
    stock = StockSentiment(
        status=LayerStatus.NO_SIGNAL,
        ticker="COMI",
        tier="MEGA",
        reason=_no_signal_reason(),
    )
    macro = MacroSentiment(
        composite_regime=MacroDirection.NO_SIGNAL,
        active_events=[],
        reason=NoSignalReason(
            gate_failed="macro.no_corroborated_event",
            human_readable="no events cleared gate",
        ),
    )
    ctx = SentimentContext(market=market, sector=sector, stock=stock, macro=macro)
    assert ctx.market.status == LayerStatus.NO_SIGNAL
    assert ctx.stock.reason is not None
    assert "COMI[MEGA]" in ctx.stock.reason.to_log_str()


def test_models_are_frozen() -> None:
    r = _no_signal_reason()
    with pytest.raises(ValidationError):
        r.gate_failed = "other"  # type: ignore[misc]
