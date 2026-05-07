"""Layer B: SectorSentiment aggregator with three binary hard gates.

Input
-----
A sequence of :class:`SectorDataPoint` (timestamp, platform, sentiment_score,
weight, entity_confidence) for a single EGX sector.  The caller routes each
post to the correct sector bucket (via :func:`classify_post_to_sector` or the
v2 pipeline's entity layer) before calling :func:`compute_sector_sentiment`.

Output
------
:class:`~tradingagents.sentiment.contracts.SectorSentiment`
  status=SIGNAL    — all three gates passed; score / confidence set.
  status=NO_SIGNAL — first failed gate; reason propagated; score is None.

Hard gates (all must pass; first failure → NO_SIGNAL)
------------------------------------------------------
1. ``n_sector_posts``       >= SECTOR_THRESHOLDS["min_sector_posts"]                   (10)
2. ``n_distinct_days``      >= SECTOR_THRESHOLDS["min_distinct_days"]                  (3)
3. ``mean_entity_conf``     >= SECTOR_THRESHOLDS["min_aggregate_entity_confidence"]    (0.70)

Confidence formula (components, weights summing to 1.0)
--------------------------------------------------------
- **size**   (40 %) — saturates at 2× min_sector_posts
- **spread** (35 %) — distinct days observed, saturates at 2× min_distinct_days
- **clarity** (25 %) — |weighted_score| / EUPHORIA_THRESH (±0.35)

Sector classification helper
-----------------------------
:func:`classify_post_to_sector` performs bilingual (Arabic + English) keyword
matching using the canonical aliases from :mod:`tradingagents.sentiment.taxonomy`.
It is intentionally lightweight: exact-string containment on lowercased text.
The v2 entity layer performs the authoritative classification; this helper is
provided for ad-hoc routing and test fixtures.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from typing import NamedTuple, Sequence

from tradingagents.sentiment.config import SECTOR_THRESHOLDS
from tradingagents.sentiment.contracts import (
    LayerStatus,
    NoSignalReason,
    SectorSentiment,
)
from tradingagents.sentiment.taxonomy import (
    SectorEnum,
    all_sectors,
    sector_aliases_ar,
)

log = logging.getLogger("tradingagents.sentiment.sector")

# ---------------------------------------------------------------------------
# Public data contract for callers
# ---------------------------------------------------------------------------


class SectorDataPoint(NamedTuple):
    """Minimal per-post data required by the sector aggregator.

    Deliberately decoupled from ``ScoredPost`` (scripts tree) so that the
    ``tradingagents`` package stays importable without script dependencies.

    Fields
    ------
    timestamp:
        ISO-8601 string.  Unparseable values are conservatively treated as
        "no date" — they do not contribute to the distinct-days numerator.
    platform:
        Source platform label, e.g. ``"facebook"``, ``"telegram"``.
    sentiment_score:
        Normalised sentiment in [-1, 1].  Positive = bullish, negative = bearish.
    weight:
        Aggregation weight: ``entity_conf × content_weight × intent_factor
        × log(1 + engagement)``.  Zero or negative weights are treated as 0.
    entity_confidence:
        Model confidence that this post refers to the target sector, in [0, 1].
        Used to compute ``mean_entity_conf`` for Gate 3.
    """

    timestamp: str
    platform: str
    sentiment_score: float
    weight: float
    entity_confidence: float


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_EUPHORIA_THRESH: float = 0.35

_CONF_WEIGHT_SIZE: float = 0.40
_CONF_WEIGHT_SPREAD: float = 0.35
_CONF_WEIGHT_CLARITY: float = 0.25

# English sector keyword map (canonical; augments the Arabic taxonomy aliases)
_SECTOR_KEYWORDS_EN: dict[SectorEnum, tuple[str, ...]] = {
    SectorEnum.BANKS: (
        "bank", "banks", "banking", "cib", "ahli", "commercial",
        "credit", "deposit", "lending", "mortgage",
    ),
    SectorEnum.REAL_ESTATE: (
        "real estate", "realty", "property", "properties",
        "housing", "construction", "development", "developer",
        "residential", "commercial property",
    ),
    SectorEnum.INDUSTRY: (
        "industry", "industrial", "manufacturing", "fertilizer",
        "fertilisers", "chemicals", "iron", "steel", "cement",
        "plastics", "petrochemical",
    ),
    SectorEnum.TELECOM_TECH: (
        "telecom", "telecommunications", "technology", "tech",
        "payments", "digital", "fintech", "software",
    ),
    SectorEnum.FINANCIAL_SERVICES: (
        "financial services", "brokerage", "broker", "finance",
        "insurance", "investment", "asset management", "fund",
    ),
    SectorEnum.FOOD_BEV: (
        "food", "beverage", "beverages", "consumer staples",
        "dairy", "sugar", "juice", "drinks",
    ),
}


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def _parse_date(ts: str) -> datetime | None:
    """Parse an ISO-8601 timestamp; return ``None`` on failure."""
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


def _count_distinct_days(posts: Sequence[SectorDataPoint]) -> int:
    """Count distinct calendar dates (UTC) across all parseable post timestamps.

    Posts with unparseable timestamps do not contribute (conservative).
    """
    dates: set[tuple[int, int, int]] = set()
    for p in posts:
        dt = _parse_date(p.timestamp)
        if dt is not None:
            dates.add((dt.year, dt.month, dt.day))
    return len(dates)


# ---------------------------------------------------------------------------
# Private computation helpers
# ---------------------------------------------------------------------------


def _weighted_mean(posts: Sequence[SectorDataPoint]) -> float:
    """Return weight-normalised mean sentiment score.

    Falls back to simple mean when all weights are ≤ 0 or posts is empty.
    Returns 0.0 on empty input.
    """
    if not posts:
        return 0.0
    weight_total = sum(max(0.0, p.weight) for p in posts)
    if weight_total <= 0.0:
        return sum(p.sentiment_score for p in posts) / len(posts)
    return (
        sum(p.sentiment_score * max(0.0, p.weight) for p in posts) / weight_total
    )


def _mean_entity_conf(posts: Sequence[SectorDataPoint]) -> float:
    """Return unweighted mean entity confidence across all posts.

    Rounded to 6 decimal places to avoid IEEE-754 accumulation artifacts
    when all posts share the same entity_confidence value (e.g. 0.70).
    """
    if not posts:
        return 0.0
    return round(sum(p.entity_confidence for p in posts) / len(posts), 6)


def _signal_confidence(
    n_posts: int,
    n_distinct_days: int,
    score_abs: float,
    min_posts: int,
    min_days: int,
) -> float:
    """Bounded [0, 1] confidence for a SIGNAL result.

    Components:
    - size   (40 %): saturates at 2× min_posts
    - spread (35 %): saturates at 2× min_distinct_days
    - clarity (25 %): saturates at ±EUPHORIA_THRESH
    """
    size_conf = min(1.0, n_posts / max(1, 2 * min_posts))
    spread_conf = min(1.0, n_distinct_days / max(1, 2 * min_days))
    clarity_conf = min(1.0, score_abs / _EUPHORIA_THRESH)
    return round(
        _CONF_WEIGHT_SIZE * size_conf
        + _CONF_WEIGHT_SPREAD * spread_conf
        + _CONF_WEIGHT_CLARITY * clarity_conf,
        3,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_sector_sentiment(
    sector: SectorEnum,
    posts: Sequence[SectorDataPoint],
) -> SectorSentiment:
    """Apply Layer B hard gates and return a typed :class:`SectorSentiment`.

    Parameters
    ----------
    sector:
        The sector being evaluated (determines the ``sector`` field on the
        returned object; does not perform any filtering itself — callers must
        pre-filter posts to this sector).
    posts:
        Sector-attributed posts, already filtered for spam and promo content
        by the quality gate.  Caller is responsible for routing.

    Returns
    -------
    SectorSentiment
        ``status=SIGNAL``    — all three gates passed; ``score`` and
                               ``confidence`` are set.
        ``status=NO_SIGNAL`` — first failed gate; ``reason`` carries
                               structured gate metadata; ``score`` is ``None``.

    Notes
    -----
    Gate failures are logged at INFO level in the canonical format::

        NO_SIGNAL: <human_readable> (gate=<gate_id>, k=v, ...)

    so that a grep for ``NO_SIGNAL`` in pipeline logs surfaces every
    abstention with its context.
    """
    thresholds = SECTOR_THRESHOLDS
    min_posts: int = int(thresholds["min_sector_posts"])
    min_days: int = int(thresholds["min_distinct_days"])
    min_entity_conf: float = float(thresholds["min_aggregate_entity_confidence"])

    sector_label = sector.value
    n_posts = len(posts)

    # ------------------------------------------------------------------
    # Gate 1: minimum post volume
    # ------------------------------------------------------------------
    if n_posts < min_posts:
        reason = NoSignalReason(
            gate_failed="sector.n_sector_posts",
            human_readable=(
                f"insufficient posts for sector signal in '{sector_label}' "
                f"({n_posts} < required {min_posts})"
            ),
            metrics={"n_posts": n_posts, "required": min_posts, "sector": sector_label},
        )
        log.info("[SectorSentiment][%s] %s", sector_label, reason.to_log_str())
        return SectorSentiment(
            status=LayerStatus.NO_SIGNAL,
            reason=reason,
            sector=sector_label,
            n_posts=n_posts,
        )

    # ------------------------------------------------------------------
    # Gate 2: minimum distinct calendar days
    # ------------------------------------------------------------------
    n_distinct_days = _count_distinct_days(posts)
    if n_distinct_days < min_days:
        reason = NoSignalReason(
            gate_failed="sector.n_distinct_days",
            human_readable=(
                f"insufficient temporal spread for '{sector_label}' "
                f"({n_distinct_days} distinct day(s) < required {min_days})"
            ),
            metrics={
                "n_distinct_days": n_distinct_days,
                "required": min_days,
                "n_posts": n_posts,
                "sector": sector_label,
            },
        )
        log.info("[SectorSentiment][%s] %s", sector_label, reason.to_log_str())
        return SectorSentiment(
            status=LayerStatus.NO_SIGNAL,
            reason=reason,
            sector=sector_label,
            n_posts=n_posts,
            n_distinct_days=n_distinct_days,
        )

    # ------------------------------------------------------------------
    # Gate 3: aggregate entity confidence
    # ------------------------------------------------------------------
    agg_entity_conf = _mean_entity_conf(posts)
    if agg_entity_conf < min_entity_conf:
        reason = NoSignalReason(
            gate_failed="sector.agg_entity_confidence",
            human_readable=(
                f"low aggregate entity confidence for '{sector_label}' "
                f"({agg_entity_conf:.3f} < required {min_entity_conf:.2f})"
            ),
            metrics={
                "agg_entity_confidence": round(agg_entity_conf, 3),
                "required": min_entity_conf,
                "n_posts": n_posts,
                "sector": sector_label,
            },
        )
        log.info("[SectorSentiment][%s] %s", sector_label, reason.to_log_str())
        return SectorSentiment(
            status=LayerStatus.NO_SIGNAL,
            reason=reason,
            sector=sector_label,
            n_posts=n_posts,
            n_distinct_days=n_distinct_days,
        )

    # ------------------------------------------------------------------
    # All gates passed — compute signal
    # ------------------------------------------------------------------
    weighted_score = _weighted_mean(posts)
    confidence = _signal_confidence(
        n_posts, n_distinct_days, abs(weighted_score), min_posts, min_days
    )

    log.info(
        "[SectorSentiment][%s] SIGNAL  score=%+.3f  conf=%.3f  "
        "n=%d  days=%d  entity_conf=%.3f",
        sector_label,
        weighted_score,
        confidence,
        n_posts,
        n_distinct_days,
        agg_entity_conf,
    )

    return SectorSentiment(
        status=LayerStatus.SIGNAL,
        score=round(weighted_score, 4),
        confidence=confidence,
        sector=sector_label,
        n_posts=n_posts,
        n_distinct_days=n_distinct_days,
    )


def classify_post_to_sector(text: str) -> SectorEnum | None:
    """Lightweight bilingual sector classifier using taxonomy keyword lists.

    Matches Arabic aliases from :func:`~tradingagents.sentiment.taxonomy.sector_aliases_ar`
    and English keywords from the internal ``_SECTOR_KEYWORDS_EN`` map.  Uses
    whole-word boundary matching for English and exact phrase matching for
    Arabic (same boundary approach as the entity layer).

    Returns the **first** sector with a keyword match, checked in
    :func:`~tradingagents.sentiment.taxonomy.all_sectors` order.  Returns
    ``None`` when no sector keyword matches (treat as market-level or discard).

    This is a routing helper for pipelines and test fixtures.  For production
    classification the v2 entity layer is authoritative.
    """
    text_lower = text.lower()

    for sector in all_sectors():
        # Arabic phrase matching (exact containment; Arabic is space-delimited
        # so substring match is sufficient for multi-word phrases)
        for alias in sector_aliases_ar(sector):
            if alias in text:
                return sector

        # English word-boundary matching
        for keyword in _SECTOR_KEYWORDS_EN.get(sector, ()):
            pattern = r"(?<![a-z])" + re.escape(keyword.lower()) + r"(?![a-z])"
            if re.search(pattern, text_lower):
                return sector

    return None
