"""Layer A0: MacroSentiment aggregator with source-credibility and corroboration gates.

Input
-----
A sequence of :class:`MacroDataPoint` (timestamp, source_domain, headline,
category, direction, sentiment_score, weight).  Callers must pre-classify each
post's ``category`` and ``direction`` before calling
:func:`compute_macro_sentiment`.  The aggregator does **not** infer categories
from raw text.

Output
------
:class:`~tradingagents.sentiment.contracts.MacroSentiment`
  composite_regime != NO_SIGNAL — at least one event cleared all three gates
      and is within its half-life window; ``active_events`` is non-empty.
  composite_regime == NO_SIGNAL  — first failed gate; ``reason`` is set.

Hard gates (three in sequence; first failure → NO_SIGNAL)
---------------------------------------------------------
1. **macro.source_credibility** — at least one post from a TIER1 or TIER2
   (or OFFICIAL) credible source.  Unrecognised domains (RUMOR) are excluded
   before all downstream checks.
2. **macro.corroborating_sources** — at least one ``(category, direction)``
   event group has ≥ 2 distinct ``source_domain`` values within a 48-hour
   window.  A single outlet publishing the same story twice does not count.
3. **macro.half_life_expired** — at least one corroborated event is still
   within its category-specific half-life window relative to
   ``reference_time``.

Composite regime (RISK_OFF dominates)
--------------------------------------
  Any active RISK_OFF event  →  RISK_OFF
  Any active RISK_ON event   →  RISK_ON  (only when no RISK_OFF active)
  All active NEUTRAL events  →  NEUTRAL
  No active events           →  NO_SIGNAL

Half-lives by MacroCategory (hours)
------------------------------------
  RATE_DECISION    120 h
  EGP_DEVALUATION  240 h
  IMF_PROGRAM      168 h
  INFLATION_PRINT   72 h
  TAX_REGULATION   168 h
  GEOPOLITICAL      48 h
  COMMODITY_SHOCK   72 h

Source credibility ladder (domains normalised to lowercase, no scheme/www)
---------------------------------------------------------------------------
  OFFICIAL   : cbe.org.eg, mof.gov.eg, fra.gov.eg, egx.com.eg
  TIER1_NEWS : reuters.com, bloomberg.com, mubasher.info (+ the rest of
               config.MACRO_SOURCES_TIER1 not listed above)
  TIER2_NEWS : enterprise.press, almalnews.com, dailynewsegypt.com,
               alborsaanews.com  (config.MACRO_SOURCES_TIER2)
  RUMOR      : any unrecognised domain → excluded before Gate 1
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional, Sequence

from tradingagents.sentiment.config import (
    MACRO_GATES,
    MACRO_HALF_LIVES_HOURS,
    MACRO_SOURCES_TIER1,
    MACRO_SOURCES_TIER2,
)
from tradingagents.sentiment.contracts import (
    MacroCategory,
    MacroDirection,
    MacroEvent,
    MacroMagnitude,
    MacroSentiment,
    NoSignalReason,
    PostRef,
    SourceCredibility,
)

log = logging.getLogger("tradingagents.sentiment.macro")

# ---------------------------------------------------------------------------
# Official (government) domains — ranked above TIER1_NEWS in credibility
# ---------------------------------------------------------------------------
_OFFICIAL_DOMAINS: frozenset[str] = frozenset(
    {
        "cbe.org.eg",    # Central Bank of Egypt
        "mof.gov.eg",    # Ministry of Finance
        "fra.gov.eg",    # Financial Regulatory Authority
        "egx.com.eg",    # Egyptian Exchange official announcements
    }
)

# ---------------------------------------------------------------------------
# Public data contract for callers
# ---------------------------------------------------------------------------


class MacroDataPoint(NamedTuple):
    """Minimal per-post data required by the macro aggregator.

    Callers must pre-classify ``category`` and ``direction`` before calling
    :func:`compute_macro_sentiment`.  The aggregator does not infer these from
    raw text.

    Fields
    ------
    timestamp:
        ISO-8601 string.  Unparseable values are treated conservatively as
        "old": the post is excluded from corroboration-window and half-life
        checks.
    source_domain:
        Normalised domain without scheme or ``www.`` prefix, e.g.
        ``"cbe.org.eg"``, ``"reuters.com"``.  Matched against the credibility
        ladder.  An empty string is treated as RUMOR.
    headline:
        Raw headline or article title for the MacroEvent record.
    category:
        :class:`~tradingagents.sentiment.contracts.MacroCategory` enum value
        as a string.  Posts with category ``"NONE"`` are skipped.
    direction:
        :class:`~tradingagents.sentiment.contracts.MacroDirection` enum value
        as a string — ``"RISK_ON"``, ``"RISK_OFF"``, or ``"NEUTRAL"``.
        Posts with direction ``"NO_SIGNAL"`` are skipped.
    sentiment_score:
        Normalised sentiment in [-1, 1].  Positive = bullish / risk-on.
    weight:
        Aggregation weight for confidence calculation (entity_conf ×
        content_weight × intent_factor × log(1 + engagement)).
    post_id:
        Optional stable identifier used for audit trail / PostRef building.
        When empty, a ``"<domain>:<iso_timestamp>"`` fallback is used.
    url:
        Optional canonical URL for the source article.
    """

    timestamp: str
    source_domain: str
    headline: str
    category: str
    direction: str
    sentiment_score: float
    weight: float
    post_id: str = ""
    url: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_domain(domain: str) -> str:
    """Strip scheme, ``www.`` prefix, path, and port; lowercase."""
    d = domain.lower().strip()
    for scheme in ("https://", "http://"):
        if d.startswith(scheme):
            d = d[len(scheme):]
            break
    if d.startswith("www."):
        d = d[4:]
    # Strip path and port
    d = d.split("/")[0].split(":")[0]
    return d


def _source_credibility(domain: str) -> SourceCredibility:
    """Map a source domain to its :class:`SourceCredibility` tier.

    Hierarchy: OFFICIAL > TIER1_NEWS > TIER2_NEWS > RUMOR.

    An empty ``domain`` is always RUMOR.
    """
    if not domain.strip():
        return SourceCredibility.RUMOR
    norm = _normalize_domain(domain)
    if norm in _OFFICIAL_DOMAINS:
        return SourceCredibility.OFFICIAL
    if norm in MACRO_SOURCES_TIER1:
        return SourceCredibility.TIER1_NEWS
    if norm in MACRO_SOURCES_TIER2:
        return SourceCredibility.TIER2_NEWS
    return SourceCredibility.RUMOR


def _credibility_weight(cred: SourceCredibility) -> float:
    """Numeric confidence weight for a credibility tier.

    OFFICIAL/TIER1 sources are weighted ~30 % higher than TIER2.
    """
    return {
        SourceCredibility.OFFICIAL: 1.0,
        SourceCredibility.TIER1_NEWS: 0.9,
        SourceCredibility.TIER2_NEWS: 0.7,
        SourceCredibility.RUMOR: 0.0,
        SourceCredibility.NO_SIGNAL: 0.0,
    }.get(cred, 0.0)


def _is_credible(cred: SourceCredibility) -> bool:
    """Return True when the source passes the minimum TIER2 credibility gate."""
    return cred in (
        SourceCredibility.OFFICIAL,
        SourceCredibility.TIER1_NEWS,
        SourceCredibility.TIER2_NEWS,
    )


def _parse_timestamp(ts: str) -> datetime | None:
    """Parse ISO-8601 timestamp; return ``None`` on failure.

    Recognises the most common formats emitted by the v2 scraper sources.
    Unrecognised formats are silently returned as ``None`` (treated as old).
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


def _magnitude_from_score(mean_score: float) -> MacroMagnitude:
    """Derive event magnitude from the absolute mean sentiment score.

    Bands: |score| >= 0.50 → HIGH, >= 0.25 → MEDIUM, else → LOW.
    """
    abs_score = abs(mean_score)
    if abs_score >= 0.50:
        return MacroMagnitude.HIGH
    if abs_score >= 0.25:
        return MacroMagnitude.MEDIUM
    return MacroMagnitude.LOW


def _event_confidence(
    n_corroborating_sources: int,
    best_cred: SourceCredibility,
    min_corroborating: int,
) -> float:
    """Bounded [0, 1] confidence for an active :class:`MacroEvent`.

    Two components:

    - **credibility** (60 %) — weight of the most credible corroborating source.
    - **corroboration** (40 %) — n_sources / (2 × min_corroborating); saturates at 1.

    A gate-minimum of 2 TIER2 sources yields ``0.6 × 0.7 + 0.4 × 0.5 = 0.62``.
    Two OFFICIAL sources yield ``0.6 × 1.0 + 0.4 × 0.5 = 0.80``.
    """
    cred_conf = _credibility_weight(best_cred)
    corroboration_conf = min(1.0, n_corroborating_sources / max(1, 2 * min_corroborating))
    return round(0.60 * cred_conf + 0.40 * corroboration_conf, 3)


def _find_corroboration(
    parsed_posts: list[tuple[datetime, MacroDataPoint]],
    min_sources: int,
    window_hours: int,
) -> tuple[datetime | None, set[str]]:
    """Find the earliest ``window_hours``-wide window with *min_sources* distinct domains.

    Parameters
    ----------
    parsed_posts:
        Timestamp-sorted list of ``(datetime, MacroDataPoint)`` pairs for a
        single ``(category, direction)`` group.  All posts have already passed
        the credibility filter.
    min_sources:
        Minimum number of distinct ``source_domain`` values required.
    window_hours:
        Width of the sliding window in hours.

    Returns
    -------
    tuple[datetime | None, set[str]]
        ``(detected_at, corroborating_sources)`` where ``detected_at`` is the
        timestamp of the window-anchor post and ``corroborating_sources`` is the
        set of distinct domains within that window.
        Returns ``(None, set())`` when no window satisfies the gate.
    """
    if not parsed_posts:
        return None, set()

    window = timedelta(hours=window_hours)
    for dt_i, _p in parsed_posts:
        window_end = dt_i + window
        sources_in_window = {
            p.source_domain
            for dt_j, p in parsed_posts
            if dt_i <= dt_j <= window_end
        }
        if len(sources_in_window) >= min_sources:
            return dt_i, sources_in_window

    return None, set()


def _build_active_event(
    category_str: str,
    direction_str: str,
    group_posts: list[tuple[datetime, MacroDataPoint]],
    corroborating_sources: set[str],
    detected_at: datetime,
    min_corroborating: int,
) -> MacroEvent | None:
    """Build a :class:`MacroEvent` from a validated corroborated group.

    Returns ``None`` if ``category`` or ``direction`` cannot be parsed as a
    valid enum value (defensive; callers should only pass known enum strings).
    """
    try:
        category = MacroCategory(category_str)
        direction = MacroDirection(direction_str)
    except ValueError:
        return None

    half_life_hours: int = MACRO_HALF_LIVES_HOURS.get(category_str, 72)

    all_scores = [p.sentiment_score for _, p in group_posts]
    mean_score = sum(all_scores) / len(all_scores) if all_scores else 0.0
    magnitude = _magnitude_from_score(mean_score)

    # Best credibility across corroborating sources
    best_cred: SourceCredibility = SourceCredibility.TIER2_NEWS
    for _, p in group_posts:
        if p.source_domain not in corroborating_sources:
            continue
        cred = _source_credibility(p.source_domain)
        if cred == SourceCredibility.OFFICIAL:
            best_cred = SourceCredibility.OFFICIAL
            break
        if cred == SourceCredibility.TIER1_NEWS:
            best_cred = SourceCredibility.TIER1_NEWS

    confidence = _event_confidence(
        len(corroborating_sources), best_cred, min_corroborating
    )

    # Headline: from the most credible corroborating post
    headline = ""
    for _, p in sorted(
        group_posts,
        key=lambda x: -_credibility_weight(_source_credibility(x[1].source_domain)),
    ):
        if p.source_domain in corroborating_sources:
            headline = p.headline
            break

    # Evidence: one PostRef per corroborating domain (most recent post per domain)
    per_domain: dict[str, tuple[datetime, MacroDataPoint]] = {}
    for dt, p in group_posts:
        if p.source_domain not in corroborating_sources:
            continue
        if p.source_domain not in per_domain or dt > per_domain[p.source_domain][0]:
            per_domain[p.source_domain] = (dt, p)

    evidence: list[PostRef] = [
        PostRef(
            source=p.source_domain,
            post_id=p.post_id or f"{p.source_domain}:{dt.isoformat()}",
            url=p.url or None,
            timestamp=dt,
            entity_confidence=_credibility_weight(_source_credibility(p.source_domain)),
        )
        for dt, p in sorted(per_domain.values(), key=lambda x: x[0])
    ]

    return MacroEvent(
        category=category,
        direction=direction,
        magnitude=magnitude,
        source_credibility=best_cred,
        half_life_hours=half_life_hours,
        confidence=confidence,
        headline=headline,
        detected_at=detected_at,
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_macro_sentiment(
    posts: Sequence[MacroDataPoint],
    *,
    reference_time: datetime | None = None,
) -> MacroSentiment:
    """Apply Layer A0 hard gates and return a typed :class:`MacroSentiment`.

    Parameters
    ----------
    posts:
        Macro-classified posts.  Each post must carry a pre-classified
        ``category`` and ``direction``.  Posts from unrecognised domains
        (RUMOR) are silently excluded at Gate 1.
    reference_time:
        Wall-clock reference for half-life expiry checks.  Defaults to
        ``datetime.now(timezone.utc)``.  Tests should pass an explicit value
        for determinism.

    Returns
    -------
    MacroSentiment
        ``composite_regime != NO_SIGNAL`` — at least one event cleared all
            gates; ``active_events`` is non-empty; ``reason`` is ``None``.
        ``composite_regime == NO_SIGNAL`` — first failed gate; ``reason`` is
            set; ``active_events`` is empty.

    Gate order
    ----------
    1. **macro.source_credibility** — no posts from OFFICIAL / TIER1 / TIER2
       sources.
    2. **macro.corroborating_sources** — no ``(category, direction)`` event
       group reached ≥ 2 distinct source_domains within 48 h.
    3. **macro.half_life_expired** — all corroborated events are older than
       their category half-life.

    Notes
    -----
    Gate failures are logged at INFO level in the canonical format::

        NO_SIGNAL: <human_readable> (gate=<gate_id>, k=v, ...)

    so that a grep for ``NO_SIGNAL`` in pipeline logs surfaces every abstention
    with its context.

    Composite regime rule: RISK_OFF dominates RISK_ON dominates NEUTRAL.
    """
    if reference_time is None:
        reference_time = datetime.now(timezone.utc)

    min_corroborating: int = MACRO_GATES["min_corroborating_sources"]
    corroboration_window_hours: int = MACRO_GATES["corroboration_window_hours"]

    # ------------------------------------------------------------------
    # Gate 1: at least one credible (OFFICIAL / TIER1 / TIER2) post
    # ------------------------------------------------------------------
    credible = [
        p
        for p in posts
        if _is_credible(_source_credibility(p.source_domain))
    ]
    if not credible:
        reason = NoSignalReason(
            gate_failed="macro.source_credibility",
            human_readable=(
                f"no posts from credible sources (OFFICIAL/TIER1/TIER2); "
                f"all {len(posts)} post(s) are from unrecognised domains"
            ),
            metrics={
                "n_total_posts": len(posts),
                "n_credible_posts": 0,
                "min_credibility": "TIER2",
            },
        )
        log.info("[MacroSentiment] %s", reason.to_log_str())
        return MacroSentiment(composite_regime=MacroDirection.NO_SIGNAL, reason=reason)

    # ------------------------------------------------------------------
    # Gate 2: corroboration — ≥2 distinct sources within 48 h per event group
    # ------------------------------------------------------------------
    # Group credible posts by (category, direction), skipping NONE/NO_SIGNAL.
    groups: dict[
        tuple[str, str], list[tuple[datetime, MacroDataPoint]]
    ] = defaultdict(list)

    for p in credible:
        if p.direction == MacroDirection.NO_SIGNAL.value:
            continue
        if p.category == MacroCategory.NONE.value:
            continue
        dt = _parse_timestamp(p.timestamp)
        if dt is None:
            continue  # Conservative: skip unresolvable timestamps
        groups[(p.category, p.direction)].append((dt, p))

    # Sort each group ascending by timestamp
    for key in groups:
        groups[key].sort(key=lambda x: x[0])

    # Find corroboration for each group
    corroborated: list[
        tuple[str, str, datetime, set[str], list[tuple[datetime, MacroDataPoint]]]
    ] = []
    for (cat, direction), parsed in groups.items():
        detected_at, sources = _find_corroboration(
            parsed, min_corroborating, corroboration_window_hours
        )
        if detected_at is not None:
            corroborated.append((cat, direction, detected_at, sources, parsed))

    if not corroborated:
        reason = NoSignalReason(
            gate_failed="macro.corroborating_sources",
            human_readable=(
                f"no macro event had ≥{min_corroborating} distinct corroborating "
                f"sources within {corroboration_window_hours} h"
            ),
            metrics={
                "n_credible_posts": len(credible),
                "min_corroborating_sources": min_corroborating,
                "corroboration_window_hours": corroboration_window_hours,
            },
        )
        log.info("[MacroSentiment] %s", reason.to_log_str())
        return MacroSentiment(composite_regime=MacroDirection.NO_SIGNAL, reason=reason)

    # ------------------------------------------------------------------
    # Gate 3: half-life — at least one corroborated event is still fresh
    # ------------------------------------------------------------------
    active_events: list[MacroEvent] = []
    for cat, direction, detected_at, sources, parsed in corroborated:
        half_life_hours: int = MACRO_HALF_LIVES_HOURS.get(cat, 72)
        age_hours = (reference_time - detected_at).total_seconds() / 3600
        if age_hours > half_life_hours:
            log.debug(
                "[MacroSentiment] expired event category=%s direction=%s "
                "age_hours=%.1f half_life=%d",
                cat,
                direction,
                age_hours,
                half_life_hours,
            )
            continue  # Event expired — not active

        event = _build_active_event(
            cat, direction, parsed, sources, detected_at, min_corroborating
        )
        if event is not None:
            active_events.append(event)

    if not active_events:
        reason = NoSignalReason(
            gate_failed="macro.half_life_expired",
            human_readable=(
                f"all {len(corroborated)} corroborated macro event(s) have expired "
                "past their category half-life window"
            ),
            metrics={
                "n_corroborated_events": len(corroborated),
                "n_active_events": 0,
            },
        )
        log.info("[MacroSentiment] %s", reason.to_log_str())
        return MacroSentiment(composite_regime=MacroDirection.NO_SIGNAL, reason=reason)

    # ------------------------------------------------------------------
    # All gates passed — compute composite regime
    # ------------------------------------------------------------------
    directions = {e.direction for e in active_events}
    if MacroDirection.RISK_OFF in directions:
        composite = MacroDirection.RISK_OFF
    elif MacroDirection.RISK_ON in directions:
        composite = MacroDirection.RISK_ON
    else:
        composite = MacroDirection.NEUTRAL

    log.info(
        "[MacroSentiment] SIGNAL  composite=%s  n_events=%d  categories=%s",
        composite.value,
        len(active_events),
        ", ".join(sorted({e.category.value for e in active_events})),
    )

    return MacroSentiment(composite_regime=composite, active_events=active_events)
