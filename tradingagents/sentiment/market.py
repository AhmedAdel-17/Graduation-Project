"""Layer A: MarketSentiment aggregator with three binary hard gates.

Input
-----
A sequence of :class:`MarketDataPoint` (timestamp, platform, sentiment_score,
weight).  Convert from ``ScoredPost`` (scripts/twitter_pipeline/v2/aggregator)
before calling :func:`compute_market_sentiment`.  Keeping this module inside
the ``tradingagents`` package without importing the scripts tree is intentional.

Output
------
:class:`~tradingagents.sentiment.contracts.MarketSentiment`
  status=SIGNAL  — all three gates passed; score / regime / confidence set.
  status=NO_SIGNAL — first failed gate; reason propagated; score is None.

Hard gates (all must pass; first failure → NO_SIGNAL)
------------------------------------------------------
1. ``n_total_posts``      >= MARKET_THRESHOLDS["min_total_posts"]       (50)
2. ``n_distinct_sources`` >= MARKET_THRESHOLDS["min_distinct_sources"]  (2)
3. ``recent_24h_share``   >= MARKET_THRESHOLDS["min_recent_24h_share"]  (0.30)

Regime bands  (weighted-mean sentiment score → MarketRegime)
------------------------------------------------------------
  score >= +0.35  →  EUPHORIA
  score >= +0.15  →  GREED
  score >  -0.15  →  NEUTRAL   (catch-all centre)
  score <= -0.15  →  FEAR
  score <= -0.35  →  PANIC

Volatility mood  (standard deviation of per-post scores)
---------------------------------------------------------
  std >= 0.55  →  STRESSED
  std >= 0.35  →  ELEVATED
  else         →  CALM
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Sequence

from tradingagents.sentiment.config import MARKET_THRESHOLDS
from tradingagents.sentiment.contracts import (
    LayerStatus,
    MarketRegime,
    MarketSentiment,
    NoSignalReason,
    VolatilityMood,
)

log = logging.getLogger("tradingagents.sentiment.market")

# ---------------------------------------------------------------------------
# Public data contract for callers
# ---------------------------------------------------------------------------

class MarketDataPoint(NamedTuple):
    """Minimal per-post data required by the market aggregator.

    This is deliberately decoupled from ``ScoredPost`` (scripts tree) so that
    the ``tradingagents`` package stays importable without the script
    dependencies.  Callers convert before calling
    :func:`compute_market_sentiment`.

    Fields
    ------
    timestamp:
        ISO-8601 string.  Empty or unparseable values are treated as "old"
        (conservative: does not contribute to the recency gate numerator).
    platform:
        Source platform label, e.g. ``"facebook"``, ``"telegram"``,
        ``"reddit"``.  Used to count distinct sources.
    sentiment_score:
        Normalised sentiment in [-1, 1].  Positive = bullish, negative = bearish.
    weight:
        Aggregation weight: ``entity_conf × content_weight × intent_factor
        × log(1 + engagement)``.  Zero or negative weights are treated as 0.
    """

    timestamp: str
    platform: str
    sentiment_score: float
    weight: float


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

# Regime score thresholds (symmetric about zero)
_EUPHORIA_THRESH: float = 0.35
_GREED_THRESH: float = 0.15
_FEAR_THRESH: float = -0.15
_PANIC_THRESH: float = -0.35

# Volatility-mood standard-deviation thresholds
_VOLATILITY_STRESSED_THRESH: float = 0.55
_VOLATILITY_ELEVATED_THRESH: float = 0.35

# Confidence formula weights (must sum to 1.0)
_CONF_WEIGHT_SIZE: float = 0.40
_CONF_WEIGHT_RECENCY: float = 0.35
_CONF_WEIGHT_CLARITY: float = 0.25


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _parse_timestamp(ts: str) -> datetime | None:
    """Parse ISO-8601 timestamp string; return ``None`` on failure.

    Recognises the most common formats emitted by the v2 scraper sources.
    An unrecognised format is silently returned as ``None`` (treated as old).
    """
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(ts, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _regime_from_score(score: float) -> MarketRegime:
    """Map weighted-mean sentiment score to a :class:`MarketRegime`."""
    if score >= _EUPHORIA_THRESH:
        return MarketRegime.EUPHORIA
    if score >= _GREED_THRESH:
        return MarketRegime.GREED
    if score <= _PANIC_THRESH:
        return MarketRegime.PANIC
    if score <= _FEAR_THRESH:
        return MarketRegime.FEAR
    return MarketRegime.NEUTRAL


def _volatility_from_std(std: float) -> VolatilityMood:
    """Map score standard deviation to a :class:`VolatilityMood`."""
    if std >= _VOLATILITY_STRESSED_THRESH:
        return VolatilityMood.STRESSED
    if std >= _VOLATILITY_ELEVATED_THRESH:
        return VolatilityMood.ELEVATED
    return VolatilityMood.CALM


def _weighted_stats(
    posts: Sequence[MarketDataPoint],
) -> tuple[float, float]:
    """Return ``(weighted_mean_score, score_std)``.

    ``weighted_mean_score`` is the weight-normalised sentiment average.
    ``score_std`` is the unweighted standard deviation of per-post scores
    (used for volatility mood; simpler computation is adequate here).

    Both values are ``0.0`` when ``posts`` is empty or all weights are ≤ 0.
    """
    if not posts:
        return 0.0, 0.0

    weight_total = sum(max(0.0, p.weight) for p in posts)
    if weight_total <= 0.0:
        # Fall back to simple mean if weights are degenerate
        mean_score = sum(p.sentiment_score for p in posts) / len(posts)
    else:
        mean_score = (
            sum(p.sentiment_score * max(0.0, p.weight) for p in posts) / weight_total
        )

    variance = (
        sum((p.sentiment_score - mean_score) ** 2 for p in posts) / len(posts)
    )
    std = math.sqrt(variance)
    return mean_score, std


def _recent_share(
    posts: Sequence[MarketDataPoint],
    reference_time: datetime,
    window_hours: int = 24,
) -> float:
    """Fraction of posts whose timestamp falls within *window_hours* of
    *reference_time*.

    Posts with unparseable timestamps are conservatively treated as "old"
    (not recent) and do not contribute to the numerator.
    """
    if not posts:
        return 0.0
    cutoff = reference_time - timedelta(hours=window_hours)
    n_recent = sum(
        1
        for p in posts
        if (dt := _parse_timestamp(p.timestamp)) is not None and dt >= cutoff
    )
    return n_recent / len(posts)


def _signal_confidence(
    n_posts: int,
    recent_share: float,
    score_abs: float,
    min_posts: int,
) -> float:
    """Bounded [0, 1] confidence for a SIGNAL result.

    Three components:
    - **size** — saturates at 2× the minimum post threshold.
    - **recency** — proportion of posts within the 24 h window.
    - **clarity** — directional clarity; saturates at ±EUPHORIA_THRESH.

    Weights: size 40 %, recency 35 %, clarity 25 %.
    """
    size_conf = min(1.0, n_posts / max(1, 2 * min_posts))
    recency_conf = recent_share
    clarity_conf = min(1.0, score_abs / _EUPHORIA_THRESH)
    return round(
        _CONF_WEIGHT_SIZE * size_conf
        + _CONF_WEIGHT_RECENCY * recency_conf
        + _CONF_WEIGHT_CLARITY * clarity_conf,
        3,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_market_sentiment(
    posts: Sequence[MarketDataPoint],
    *,
    reference_time: datetime | None = None,
) -> MarketSentiment:
    """Apply Layer A hard gates and return a typed :class:`MarketSentiment`.

    Parameters
    ----------
    posts:
        Market-level posts (EGX_MARKET bucket), already filtered for spam and
        promo content by the quality gate.  The caller is responsible for
        passing only EGX_MARKET-routed posts, not per-stock posts.
    reference_time:
        Wall-clock reference for the recency gate.  Defaults to UTC now.
        Tests should pass an explicit value for determinism.

    Returns
    -------
    MarketSentiment
        ``status=SIGNAL``    — all three gates passed; ``score``, ``regime``,
                               ``volatility_mood``, and ``confidence`` are set.
        ``status=NO_SIGNAL`` — first failed gate; ``reason`` carries structured
                               gate metadata; ``score`` is ``None``.

    Notes
    -----
    Gate failures are logged at INFO level in the canonical format::

        NO_SIGNAL: <human_readable> (gate=<gate_id>, k=v, ...)

    so that a grep for ``NO_SIGNAL`` in pipeline logs surfaces every abstention
    with its context.
    """
    if reference_time is None:
        reference_time = datetime.now(timezone.utc)

    thresholds = MARKET_THRESHOLDS
    min_posts: int = int(thresholds["min_total_posts"])
    min_sources: int = int(thresholds["min_distinct_sources"])
    min_recent_share: float = float(thresholds["min_recent_24h_share"])

    n_posts = len(posts)

    # ------------------------------------------------------------------
    # Gate 1: minimum post volume
    # ------------------------------------------------------------------
    if n_posts < min_posts:
        reason = NoSignalReason(
            gate_failed="market.n_total_posts",
            human_readable=(
                f"insufficient posts for market signal "
                f"({n_posts} < required {min_posts})"
            ),
            metrics={"n_posts": n_posts, "required": min_posts},
        )
        log.info("[MarketSentiment] %s", reason.to_log_str())
        return MarketSentiment(
            status=LayerStatus.NO_SIGNAL,
            reason=reason,
            n_posts=n_posts,
        )

    # ------------------------------------------------------------------
    # Gate 2: distinct source platforms
    # ------------------------------------------------------------------
    distinct_sources = len({p.platform for p in posts})
    if distinct_sources < min_sources:
        reason = NoSignalReason(
            gate_failed="market.n_distinct_sources",
            human_readable=(
                f"insufficient source diversity "
                f"({distinct_sources} < required {min_sources})"
            ),
            metrics={
                "n_distinct_sources": distinct_sources,
                "required": min_sources,
                "n_posts": n_posts,
            },
        )
        log.info("[MarketSentiment] %s", reason.to_log_str())
        return MarketSentiment(
            status=LayerStatus.NO_SIGNAL,
            reason=reason,
            n_posts=n_posts,
            n_distinct_sources=distinct_sources,
        )

    # ------------------------------------------------------------------
    # Gate 3: recency — ≥30 % of posts within 24 h
    # ------------------------------------------------------------------
    recent_share = _recent_share(posts, reference_time)
    if recent_share < min_recent_share:
        reason = NoSignalReason(
            gate_failed="market.recent_24h_share",
            human_readable=(
                f"data too stale for market signal "
                f"({recent_share:.0%} within 24 h < required {min_recent_share:.0%})"
            ),
            metrics={
                "recent_24h_share": round(recent_share, 3),
                "required": min_recent_share,
                "n_posts": n_posts,
                "n_distinct_sources": distinct_sources,
            },
        )
        log.info("[MarketSentiment] %s", reason.to_log_str())
        return MarketSentiment(
            status=LayerStatus.NO_SIGNAL,
            reason=reason,
            n_posts=n_posts,
            n_distinct_sources=distinct_sources,
        )

    # ------------------------------------------------------------------
    # All gates passed — compute signal
    # ------------------------------------------------------------------
    weighted_score, score_std = _weighted_stats(posts)
    regime = _regime_from_score(weighted_score)
    volatility_mood = _volatility_from_std(score_std)
    confidence = _signal_confidence(
        n_posts, recent_share, abs(weighted_score), min_posts
    )

    log.info(
        "[MarketSentiment] SIGNAL  regime=%s  score=%+.3f  conf=%.3f  "
        "n=%d  sources=%d  recency=%.0f%%  volatility=%s",
        regime.value,
        weighted_score,
        confidence,
        n_posts,
        distinct_sources,
        recent_share * 100,
        volatility_mood.value,
    )

    return MarketSentiment(
        status=LayerStatus.SIGNAL,
        score=round(weighted_score, 4),
        confidence=confidence,
        regime=regime,
        volatility_mood=volatility_mood,
        n_posts=n_posts,
        n_distinct_sources=distinct_sources,
    )
