"""Layer C: StockSentiment aggregator with per-tier liquidity gates.

Input
-----
A sequence of :class:`StockDataPoint` (timestamp, platform, author,
sentiment_score, weight, entity_confidence, is_spam_promo) for a single EGX
ticker.  Callers must pre-filter to posts that mention the target ticker.
Spam / promo posts must be flagged with ``is_spam_promo=True`` — they are
excluded before all gate counts.

Output
------
:class:`~tradingagents.sentiment.contracts.StockSentiment`
  status=SIGNAL    — all four gates passed; score / confidence set.
  status=NO_SIGNAL — first failed gate; reason propagated; score is None.

Hard gates (all must pass; first failure → NO_SIGNAL)
------------------------------------------------------
Applied to clean (non-spam) posts only.

1. ``n_strong_mentions``  >= STOCK_THRESHOLDS_BY_TIER[tier]["n_strong_mentions"]
   A "strong" mention has ``entity_confidence >= MIN_STRONG_ENTITY_CONFIDENCE`` (0.85).
2. ``n_distinct_authors`` >= STOCK_THRESHOLDS_BY_TIER[tier]["n_distinct_authors"]
   Empty author strings are collapsed to ``"_anonymous"`` (one shared bucket).
3. ``n_distinct_sources`` >= STOCK_THRESHOLDS_BY_TIER[tier]["n_distinct_sources"]
4. ``recent_72h_share``   >= MIN_RECENT_72H_SHARE (0.60)
   Fraction of clean posts whose timestamp is within 72 h of ``reference_time``.
   Unparseable timestamps are conservatively treated as "old".

Tier thresholds (from :data:`~tradingagents.sentiment.config.STOCK_THRESHOLDS_BY_TIER`)
----------------------------------------------------------------------------------------
  MEGA  (COMI, TMGH, FWRY, ETEL, HRHO):  8 strong / 5 authors / 3 sources
  MID   (EGX-30 / EGX-70 remainder):     5 strong / 3 authors / 2 sources
  SMALL (all other tickers):              3 strong / 2 authors / 2 sources

Confidence formula (components, weights summing to 1.0)
--------------------------------------------------------
- **size**      (40 %) — n_strong_mentions / (2 × tier threshold); saturates at 1.0
- **diversity** (35 %) — n_distinct_authors / (2 × tier threshold); saturates at 1.0
- **clarity**   (25 %) — |score| / EUPHORIA_THRESH (0.35); saturates at 1.0

contradicts_market flag
-----------------------
Set to ``True`` when ``market_score`` is provided, both it and the stock score
are >= 0.15 in absolute value (neither near-neutral), and they have opposite
signs — indicating a meaningful stock-vs-market sentiment divergence.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional, Sequence

from tradingagents.sentiment.config import (
    MIN_RECENT_72H_SHARE,
    MIN_STRONG_ENTITY_CONFIDENCE,
    STOCK_THRESHOLDS_BY_TIER,
)
from tradingagents.sentiment.contracts import (
    LayerStatus,
    NoSignalReason,
    StockSentiment,
)
from tradingagents.sentiment.liquidity_tiers import LiquidityTier, tier_for

log = logging.getLogger("tradingagents.sentiment.stock")

# ---------------------------------------------------------------------------
# Public data contract for callers
# ---------------------------------------------------------------------------


class StockDataPoint(NamedTuple):
    """Minimal per-post data required by the stock aggregator.

    Deliberately decoupled from ``ScoredPost`` (scripts tree) so that the
    ``tradingagents`` package stays importable without script dependencies.

    Fields
    ------
    timestamp:
        ISO-8601 string.  Unparseable or empty values are treated as "old"
        (conservative: do not contribute to the recency gate numerator).
    platform:
        Source platform label, e.g. ``"facebook"``, ``"telegram"``.
        Used to count distinct sources for Gate 3.
    author:
        Author / username (may be anonymised).  Empty strings are collapsed
        to ``"_anonymous"`` — one shared bucket, not a unique count per post —
        so sources that omit user IDs do not inflate the distinct-author count.
    sentiment_score:
        Normalised sentiment in [-1, 1].  Positive = bullish.
    weight:
        Aggregation weight: ``entity_conf × content_weight × intent_factor
        × log(1 + engagement)``.  Zero or negative treated as 0.
    entity_confidence:
        Model confidence that this post refers to the target ticker, in [0, 1].
        A post is a "strong mention" when this value >= MIN_STRONG_ENTITY_CONFIDENCE.
    is_spam_promo:
        When ``True`` the post is excluded before all gate counts.  Callers
        set this via the quality gate in the v2 pipeline.
    """

    timestamp: str
    platform: str
    author: str
    sentiment_score: float
    weight: float
    entity_confidence: float
    is_spam_promo: bool = False


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_EUPHORIA_THRESH: float = 0.35  # clarity confidence saturates here

_CONF_WEIGHT_SIZE: float = 0.40
_CONF_WEIGHT_DIVERSITY: float = 0.35
_CONF_WEIGHT_CLARITY: float = 0.25

_CONTRADICTS_MIN_ABS: float = 0.15  # both score and market_score must exceed this


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def _parse_dt(ts: str) -> datetime | None:
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


# ---------------------------------------------------------------------------
# Private computation helpers
# ---------------------------------------------------------------------------


def _weighted_mean(posts: Sequence[StockDataPoint]) -> float:
    """Weight-normalised mean sentiment score; falls back to simple mean."""
    if not posts:
        return 0.0
    weight_total = sum(max(0.0, p.weight) for p in posts)
    if weight_total <= 0.0:
        return sum(p.sentiment_score for p in posts) / len(posts)
    return sum(p.sentiment_score * max(0.0, p.weight) for p in posts) / weight_total


def _signal_confidence(
    n_strong: int,
    n_authors: int,
    score_abs: float,
    req_strong: int,
    req_authors: int,
) -> float:
    """Bounded [0, 1] confidence for a SIGNAL result.

    Components:
    - size      (40 %): saturates at 2× req_strong
    - diversity (35 %): saturates at 2× req_authors
    - clarity   (25 %): saturates at ±EUPHORIA_THRESH
    """
    size_conf = min(1.0, n_strong / max(1, 2 * req_strong))
    div_conf = min(1.0, n_authors / max(1, 2 * req_authors))
    clarity_conf = min(1.0, score_abs / _EUPHORIA_THRESH)
    return round(
        _CONF_WEIGHT_SIZE * size_conf
        + _CONF_WEIGHT_DIVERSITY * div_conf
        + _CONF_WEIGHT_CLARITY * clarity_conf,
        3,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_stock_sentiment(
    ticker: str,
    posts: Sequence[StockDataPoint],
    reference_time: datetime,
    market_score: Optional[float] = None,
) -> StockSentiment:
    """Apply Layer C hard gates and return a typed :class:`StockSentiment`.

    Parameters
    ----------
    ticker:
        EGX ticker, e.g. ``"COMI.CA"`` or ``"COMI"``.  Used to look up the
        liquidity tier and appears in log messages.
    posts:
        All posts mentioning the ticker, including spam (flagged via
        ``is_spam_promo``).  Spam is filtered out first; callers do not need
        to pre-filter.
    reference_time:
        The "as of" time for the recency gate.  Pass ``datetime.now(utc)``
        for live use; pass the trade_date (end of day) for backtest replay.
        Must be timezone-aware.
    market_score:
        Optional weighted-mean market sentiment score from Layer A, in [-1, 1].
        When provided and both the market score and stock score are significant
        (>= 0.15 in abs value) but opposite in sign, ``contradicts_market`` is
        set to ``True``.

    Returns
    -------
    StockSentiment
        ``status=SIGNAL``    — all four gates passed; ``score`` and
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
    tier = tier_for(ticker)
    thresholds = STOCK_THRESHOLDS_BY_TIER[tier]
    req_strong: int = int(thresholds["n_strong_mentions"])
    req_authors: int = int(thresholds["n_distinct_authors"])
    req_sources: int = int(thresholds["n_distinct_sources"])
    tier_label = tier.value

    # ------------------------------------------------------------------
    # Pre-processing: exclude spam / promo posts before all gate counts
    # ------------------------------------------------------------------
    clean = [p for p in posts if not p.is_spam_promo]

    # ------------------------------------------------------------------
    # Gate 1: minimum strong mentions (entity_confidence >= 0.85)
    # ------------------------------------------------------------------
    strong = [p for p in clean if p.entity_confidence >= MIN_STRONG_ENTITY_CONFIDENCE]
    n_strong = len(strong)
    if n_strong < req_strong:
        reason = NoSignalReason(
            gate_failed="stock.n_strong_mentions",
            human_readable=(
                f"insufficient strong mentions for {ticker}[{tier_label}] "
                f"({n_strong} < required {req_strong})"
            ),
            metrics={
                "ticker": ticker,
                "tier": tier_label,
                "n_strong_mentions": n_strong,
                "required": req_strong,
                "n_total_clean": len(clean),
            },
        )
        log.info("[StockSentiment][%s] %s", ticker, reason.to_log_str())
        return StockSentiment(
            status=LayerStatus.NO_SIGNAL,
            reason=reason,
            ticker=ticker,
            tier=tier_label,
            n_strong_mentions=n_strong,
        )

    # ------------------------------------------------------------------
    # Gate 2: minimum distinct authors
    # ------------------------------------------------------------------
    author_set = {p.author if p.author else "_anonymous" for p in clean}
    n_authors = len(author_set)
    if n_authors < req_authors:
        reason = NoSignalReason(
            gate_failed="stock.n_distinct_authors",
            human_readable=(
                f"insufficient distinct authors for {ticker}[{tier_label}] "
                f"({n_authors} < required {req_authors})"
            ),
            metrics={
                "ticker": ticker,
                "tier": tier_label,
                "n_distinct_authors": n_authors,
                "required": req_authors,
                "n_strong_mentions": n_strong,
            },
        )
        log.info("[StockSentiment][%s] %s", ticker, reason.to_log_str())
        return StockSentiment(
            status=LayerStatus.NO_SIGNAL,
            reason=reason,
            ticker=ticker,
            tier=tier_label,
            n_strong_mentions=n_strong,
            n_distinct_authors=n_authors,
        )

    # ------------------------------------------------------------------
    # Gate 3: minimum distinct sources (platforms)
    # ------------------------------------------------------------------
    source_set = {p.platform for p in clean}
    n_sources = len(source_set)
    if n_sources < req_sources:
        reason = NoSignalReason(
            gate_failed="stock.n_distinct_sources",
            human_readable=(
                f"insufficient distinct sources for {ticker}[{tier_label}] "
                f"({n_sources} < required {req_sources})"
            ),
            metrics={
                "ticker": ticker,
                "tier": tier_label,
                "n_distinct_sources": n_sources,
                "required": req_sources,
                "n_strong_mentions": n_strong,
                "n_distinct_authors": n_authors,
            },
        )
        log.info("[StockSentiment][%s] %s", ticker, reason.to_log_str())
        return StockSentiment(
            status=LayerStatus.NO_SIGNAL,
            reason=reason,
            ticker=ticker,
            tier=tier_label,
            n_strong_mentions=n_strong,
            n_distinct_authors=n_authors,
            n_distinct_sources=n_sources,
        )

    # ------------------------------------------------------------------
    # Gate 4: recency — >=60% of clean posts within 72 h of reference_time
    # ------------------------------------------------------------------
    cutoff = reference_time - timedelta(hours=72)
    n_recent = sum(
        1 for p in clean
        if (dt := _parse_dt(p.timestamp)) is not None and dt >= cutoff
    )
    n_total_clean = len(clean)
    recent_share = n_recent / n_total_clean if n_total_clean > 0 else 0.0
    if recent_share < MIN_RECENT_72H_SHARE:
        reason = NoSignalReason(
            gate_failed="stock.recent_72h_share",
            human_readable=(
                f"stale data for {ticker}[{tier_label}] "
                f"({recent_share:.0%} recent < required {MIN_RECENT_72H_SHARE:.0%})"
            ),
            metrics={
                "ticker": ticker,
                "tier": tier_label,
                "recent_72h_share": round(recent_share, 3),
                "required": MIN_RECENT_72H_SHARE,
                "n_recent": n_recent,
                "n_total_clean": n_total_clean,
            },
        )
        log.info("[StockSentiment][%s] %s", ticker, reason.to_log_str())
        return StockSentiment(
            status=LayerStatus.NO_SIGNAL,
            reason=reason,
            ticker=ticker,
            tier=tier_label,
            n_strong_mentions=n_strong,
            n_distinct_authors=n_authors,
            n_distinct_sources=n_sources,
        )

    # ------------------------------------------------------------------
    # All gates passed — compute signal
    # ------------------------------------------------------------------
    weighted_score = _weighted_mean(clean)
    confidence = _signal_confidence(n_strong, n_authors, abs(weighted_score), req_strong, req_authors)

    contradicts_market = (
        market_score is not None
        and abs(market_score) >= _CONTRADICTS_MIN_ABS
        and abs(weighted_score) >= _CONTRADICTS_MIN_ABS
        and (market_score * weighted_score) < 0
    )

    log.info(
        "[StockSentiment][%s] SIGNAL  tier=%s  score=%+.3f  conf=%.3f  "
        "strong=%d  authors=%d  sources=%d  recent_share=%.0f%%  contradicts_market=%s",
        ticker,
        tier_label,
        weighted_score,
        confidence,
        n_strong,
        n_authors,
        n_sources,
        recent_share * 100,
        contradicts_market,
    )

    return StockSentiment(
        status=LayerStatus.SIGNAL,
        score=round(weighted_score, 4),
        confidence=confidence,
        ticker=ticker,
        tier=tier_label,
        n_strong_mentions=n_strong,
        n_distinct_authors=n_authors,
        n_distinct_sources=n_sources,
        contradicts_market=contradicts_market,
    )
