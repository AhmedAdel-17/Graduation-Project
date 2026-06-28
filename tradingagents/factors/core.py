"""Cross-sectional factor model — pure, point-in-time, no look-ahead.

Pipeline:
  1. Per ticker, compute ATOMIC factor values from point-in-time data
     (momentum & realized vol from the close series; value & quality ratios from
     fundamentals when provided).
  2. Z-score each atomic factor ACROSS the universe (sign-adjusted so higher is
     always better; missing values get a neutral 0).
  3. Average atomic z-scores within their bucket (value / quality / momentum /
     low_vol).
  4. Composite = weighted sum of bucket scores; rank the universe descending.

This is a relative (cross-sectional) signal: a score only means something versus the
rest of the universe scored on the same date. Everything here is a pure function of
its inputs — feed it only data available as of the decision date.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# Atomic factor → (bucket, direction). direction +1 = higher raw is better,
# -1 = lower raw is better (leverage and volatility are "less is more").
ATOMIC_FACTORS: Dict[str, Tuple[str, int]] = {
    "earnings_yield": ("value", +1),
    "book_to_price": ("value", +1),
    "roe": ("quality", +1),
    "net_margin": ("quality", +1),
    "debt_to_equity": ("quality", -1),
    "momentum": ("momentum", +1),
    "realized_vol": ("low_vol", -1),
}

DEFAULT_FACTOR_WEIGHTS: Dict[str, float] = {
    "value": 0.30,
    "quality": 0.30,
    "momentum": 0.25,
    "low_vol": 0.15,
}

_Z_CLIP = 3.0  # winsorize z-scores to ±3 so one outlier can't dominate


# ─────────────────────────────────────────────────────────────────────────────
# Price-derived atomic factors
# ─────────────────────────────────────────────────────────────────────────────


def momentum_score(
    closes: Sequence[float],
    lookback: int = 252,
    skip: int = 21,
) -> Optional[float]:
    """12-1 momentum: return from ~12 months ago to ~1 month ago.

    The most recent month is skipped because short-term returns tend to reverse
    (Jegadeesh 1990); 12-1 momentum is the standard, robust construction.
    Returns None when there is not enough history.
    """
    need = lookback + 1
    if not closes or len(closes) < need:
        return None
    p_old = closes[-lookback]
    p_recent = closes[-1 - skip] if skip > 0 else closes[-1]
    if p_old is None or p_old <= 0 or p_recent is None:
        return None
    return p_recent / p_old - 1.0


def realized_vol(closes: Sequence[float], window: int = 63) -> Optional[float]:
    """Annualized realized volatility of daily returns over the last ``window`` bars.

    Used as the low-volatility factor (sign -1: lower vol scores higher). Returns
    None when history is insufficient.
    """
    if not closes or len(closes) < window + 1:
        return None
    rets: List[float] = []
    for prev, cur in zip(closes[-window - 1:-1], closes[-window:]):
        if prev and prev > 0 and cur is not None:
            rets.append(cur / prev - 1.0)
    if len(rets) < 2:
        return None
    return statistics.pstdev(rets) * (252 ** 0.5)


# ─────────────────────────────────────────────────────────────────────────────
# Inputs / outputs
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class FactorInputs:
    """Point-in-time data for one ticker. Fundamentals are optional; when absent,
    the value/quality buckets simply contribute neutral (0) z-scores and the
    composite reduces to a price-based (momentum + low-vol) model."""

    ticker: str
    closes: Sequence[float]
    earnings_yield: Optional[float] = None
    book_to_price: Optional[float] = None
    roe: Optional[float] = None
    net_margin: Optional[float] = None
    debt_to_equity: Optional[float] = None

    def atomic_values(self) -> Dict[str, Optional[float]]:
        return {
            "earnings_yield": self.earnings_yield,
            "book_to_price": self.book_to_price,
            "roe": self.roe,
            "net_margin": self.net_margin,
            "debt_to_equity": self.debt_to_equity,
            "momentum": momentum_score(self.closes),
            "realized_vol": realized_vol(self.closes),
        }


@dataclass
class FactorScore:
    ticker: str
    composite: float
    rank: int                       # 1 = best
    percentile: float               # 1.0 = top of universe, 0.0 = bottom
    bucket_scores: Dict[str, float] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Cross-sectional standardization + ranking
# ─────────────────────────────────────────────────────────────────────────────


def _sector_demean(
    values: Dict[str, Optional[float]],
    sector_map: Dict[str, str],
) -> Dict[str, Optional[float]]:
    """Subtract each ticker's SECTOR mean from its value (None preserved).

    This removes structural sector level-differences BEFORE standardization — e.g.
    a bank's D/E of 8 is normal among banks but extreme among industrials, so
    comparing it to its sector peers (not the whole universe) avoids penalizing the
    entire financials sector. A sector with a single present value demeans to 0
    (neutral): you cannot rank a sector of one.
    """
    from collections import defaultdict

    groups: Dict[str, list] = defaultdict(list)
    for t, v in values.items():
        if v is not None:
            groups[sector_map.get(t, "UNKNOWN")].append(v)
    means = {s: (sum(vs) / len(vs)) for s, vs in groups.items() if vs}

    out: Dict[str, Optional[float]] = {}
    for t, v in values.items():
        if v is None:
            out[t] = None
        else:
            sec = sector_map.get(t, "UNKNOWN")
            out[t] = v - means.get(sec, v)
    return out


def _zscore_cross_section(
    values: Dict[str, Optional[float]],
    sign: int,
) -> Dict[str, float]:
    """Sign-adjusted z-score across tickers. Missing → 0 (neutral). Degenerate
    (<2 present, or zero spread) → all 0."""
    present = [v for v in values.values() if v is not None]
    if len(present) < 2:
        return {t: 0.0 for t in values}
    mean = statistics.mean(present)
    sd = statistics.pstdev(present)
    if sd == 0:
        return {t: 0.0 for t in values}
    out: Dict[str, float] = {}
    for t, v in values.items():
        if v is None:
            out[t] = 0.0
        else:
            z = sign * (v - mean) / sd
            out[t] = max(-_Z_CLIP, min(_Z_CLIP, z))
    return out


def rank_universe(
    inputs: List[FactorInputs],
    weights: Optional[Dict[str, float]] = None,
    sector_map: Optional[Dict[str, str]] = None,
) -> List[FactorScore]:
    """Rank a universe by composite factor score (best first).

    Pure cross-sectional computation — scores are only meaningful relative to this
    exact set of tickers on this date.

    When ``sector_map`` ({ticker: sector}) is provided, every atomic factor is
    sector-demeaned before standardization (sector-neutral scoring). This stops
    structural sector differences — bank leverage, real-estate margins — from
    dominating the ranking. Pass ``None`` for raw universe-wide scoring.
    """
    weights = weights or DEFAULT_FACTOR_WEIGHTS
    if not inputs:
        return []

    atomics = {fi.ticker: fi.atomic_values() for fi in inputs}

    # Z-score each atomic factor across the universe (sector-neutral if a map given).
    atomic_z: Dict[str, Dict[str, float]] = {}
    for name, (_bucket, sign) in ATOMIC_FACTORS.items():
        vals = {t: atomics[t].get(name) for t in atomics}
        if sector_map:
            vals = _sector_demean(vals, sector_map)
        atomic_z[name] = _zscore_cross_section(vals, sign)

    # Map bucket → its atomic factor names.
    bucket_members: Dict[str, List[str]] = {}
    for name, (bucket, _sign) in ATOMIC_FACTORS.items():
        bucket_members.setdefault(bucket, []).append(name)

    scored: List[FactorScore] = []
    for fi in inputs:
        t = fi.ticker
        bucket_scores: Dict[str, float] = {}
        for bucket, names in bucket_members.items():
            zs = [atomic_z[n][t] for n in names]
            bucket_scores[bucket] = sum(zs) / len(zs) if zs else 0.0
        composite = sum(
            weights.get(b, 0.0) * s for b, s in bucket_scores.items()
        )
        scored.append(FactorScore(
            ticker=t, composite=composite, rank=0, percentile=0.0,
            bucket_scores=bucket_scores,
        ))

    # Sort best-first and assign rank + percentile.
    scored.sort(key=lambda s: s.composite, reverse=True)
    n = len(scored)
    for i, s in enumerate(scored):
        s.rank = i + 1
        # percentile: 1.0 for the top name, → ~0 for the bottom.
        s.percentile = 1.0 - (i / (n - 1)) if n > 1 else 1.0
    return scored


def rank_to_decisions(
    scores: List[FactorScore],
    top_quantile: float = 0.30,
) -> Dict[str, str]:
    """Long-only mapping: BUY the top ``top_quantile`` of the ranked universe, HOLD
    the rest. Returns ``{ticker: "BUY"|"HOLD"}``."""
    if not scores:
        return {}
    n = len(scores)
    k = max(1, round(n * top_quantile))
    buy = {s.ticker for s in scores[:k]}
    return {s.ticker: ("BUY" if s.ticker in buy else "HOLD") for s in scores}
