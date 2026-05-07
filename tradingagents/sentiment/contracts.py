"""Typed contracts for the redesigned sentiment subsystem.

Design principles enforced here:
1. NO_SIGNAL is a first-class status value at every layer. There is no neutral
   default that silently substitutes for missing data.
2. Numeric fields are nullable. A `score` is `None` whenever `status != SIGNAL`.
3. Every NO_SIGNAL emission carries a `NoSignalReason` with the exact gate that
   failed and the relevant metrics, so logs / agent context / final reports /
   audit outputs all surface the same explanation. Honest abstention is product
   behavior, not an internal implementation detail.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

NO_SIGNAL: Literal["NO_SIGNAL"] = "NO_SIGNAL"


class LayerStatus(str, Enum):
    SIGNAL = "SIGNAL"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NO_SIGNAL = "NO_SIGNAL"


class MarketRegime(str, Enum):
    EUPHORIA = "EUPHORIA"
    GREED = "GREED"
    NEUTRAL = "NEUTRAL"
    FEAR = "FEAR"
    PANIC = "PANIC"
    NO_SIGNAL = "NO_SIGNAL"


class VolatilityMood(str, Enum):
    CALM = "CALM"
    ELEVATED = "ELEVATED"
    STRESSED = "STRESSED"
    NO_SIGNAL = "NO_SIGNAL"


class MacroCategory(str, Enum):
    RATE_DECISION = "RATE_DECISION"
    EGP_DEVALUATION = "EGP_DEVALUATION"
    IMF_PROGRAM = "IMF_PROGRAM"
    INFLATION_PRINT = "INFLATION_PRINT"
    TAX_REGULATION = "TAX_REGULATION"
    GEOPOLITICAL = "GEOPOLITICAL"
    COMMODITY_SHOCK = "COMMODITY_SHOCK"
    NONE = "NONE"


class MacroDirection(str, Enum):
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    NEUTRAL = "NEUTRAL"
    NO_SIGNAL = "NO_SIGNAL"


class MacroMagnitude(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class SourceCredibility(str, Enum):
    OFFICIAL = "OFFICIAL"
    TIER1_NEWS = "TIER1_NEWS"
    TIER2_NEWS = "TIER2_NEWS"
    RUMOR = "RUMOR"
    NO_SIGNAL = "NO_SIGNAL"


class NoSignalReason(BaseModel):
    """Structured explanation for why a layer abstained.

    Renders to the canonical log/report format:
        ``NO_SIGNAL: <human_readable> (gate=<gate_failed>, metrics=<metrics>)``

    The same instance is propagated into logs, agent prompt context, final
    reports, and the audit trail. Consumers must not invent their own reasons.
    """

    model_config = ConfigDict(frozen=True)

    gate_failed: str = Field(
        ...,
        description="Identifier of the gate that failed (e.g. 'stock.n_strong_mentions').",
    )
    human_readable: str = Field(
        ...,
        description="Single-sentence explanation in the canonical form, e.g. "
        "'insufficient strong mentions (2 < required 5 for COMI[MEGA])'.",
    )
    metrics: dict[str, Any] = Field(
        default_factory=dict,
        description="Concrete metric values relevant to the failed gate "
        "(observed, required, ticker, tier, n_sources, ...).",
    )

    def to_log_str(self) -> str:
        if self.metrics:
            metric_str = ", ".join(f"{k}={v}" for k, v in self.metrics.items())
            return (
                f"NO_SIGNAL: {self.human_readable} "
                f"(gate={self.gate_failed}, {metric_str})"
            )
        return f"NO_SIGNAL: {self.human_readable} (gate={self.gate_failed})"

    def __str__(self) -> str:
        return self.to_log_str()


class PostRef(BaseModel):
    """Citation pointer to a single source post used as evidence."""

    model_config = ConfigDict(frozen=True)

    source: str
    post_id: str
    url: Optional[str] = None
    timestamp: Optional[datetime] = None
    entity_confidence: float = Field(ge=0.0, le=1.0)


class _LayerBase(BaseModel):
    """Common invariants for sentiment layer outputs.

    Enforces: when status != SIGNAL the score must be None and the reason must
    be present. When status == SIGNAL the score must be present and the reason
    must be absent. This prevents silent neutral-as-missing-data leakage.
    """

    model_config = ConfigDict(frozen=True)

    status: LayerStatus
    score: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: Optional[NoSignalReason] = None
    evidence: list[PostRef] = Field(default_factory=list)

    @field_validator("score")
    @classmethod
    def _score_in_range(cls, v: Optional[float]) -> Optional[float]:
        return v

    def model_post_init(self, __context: Any) -> None:  # type: ignore[override]
        if self.status == LayerStatus.SIGNAL:
            if self.score is None:
                raise ValueError("score is required when status == SIGNAL")
            if self.reason is not None:
                raise ValueError("reason must be None when status == SIGNAL")
        else:
            if self.score is not None:
                raise ValueError(
                    f"score must be None when status == {self.status.value}"
                )
            if self.reason is None:
                raise ValueError(
                    f"reason is required when status == {self.status.value}"
                )


class MarketSentiment(_LayerBase):
    regime: MarketRegime = MarketRegime.NO_SIGNAL
    volatility_mood: VolatilityMood = VolatilityMood.NO_SIGNAL
    n_posts: int = Field(default=0, ge=0)
    n_distinct_sources: int = Field(default=0, ge=0)


class SectorSentiment(_LayerBase):
    sector: str
    n_posts: int = Field(default=0, ge=0)
    n_distinct_days: int = Field(default=0, ge=0)


class StockSentiment(_LayerBase):
    ticker: str
    tier: str
    n_strong_mentions: int = Field(default=0, ge=0)
    n_distinct_authors: int = Field(default=0, ge=0)
    n_distinct_sources: int = Field(default=0, ge=0)
    contradicts_market: bool = False


class MacroEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    category: MacroCategory
    direction: MacroDirection
    magnitude: MacroMagnitude
    source_credibility: SourceCredibility
    half_life_hours: int = Field(gt=0)
    confidence: float = Field(ge=0.0, le=1.0)
    headline: str
    detected_at: datetime
    evidence: list[PostRef] = Field(default_factory=list)


class MacroSentiment(BaseModel):
    """Composite of currently-active macro events.

    `composite_regime == NO_SIGNAL` whenever no event clears its credibility +
    corroboration gate. Active events may be empty even when the layer is
    technically ``operational``; that is the correct outcome on quiet days.
    """

    model_config = ConfigDict(frozen=True)

    composite_regime: MacroDirection = MacroDirection.NO_SIGNAL
    active_events: list[MacroEvent] = Field(default_factory=list)
    reason: Optional[NoSignalReason] = None

    def model_post_init(self, __context: Any) -> None:  # type: ignore[override]
        if self.composite_regime == MacroDirection.NO_SIGNAL and self.reason is None:
            raise ValueError(
                "reason is required when composite_regime == NO_SIGNAL"
            )
        if self.composite_regime != MacroDirection.NO_SIGNAL and self.reason is not None:
            raise ValueError(
                f"reason must be None when composite_regime == {self.composite_regime.value}"
            )


class SentimentContext(BaseModel):
    """Top-level object handed to research / risk / blender stages.

    Sector and stock are typed so a missing layer is `NO_SIGNAL`-statused, not
    `None`. The blender (PR 7) consumes these typed sentinels directly and
    refuses to fall back to neutral=0/conf=0.5 substitution.
    """

    model_config = ConfigDict(frozen=True)

    market: MarketSentiment
    sector: SectorSentiment
    stock: StockSentiment
    macro: MacroSentiment
    contradiction_flags: list[str] = Field(default_factory=list)
