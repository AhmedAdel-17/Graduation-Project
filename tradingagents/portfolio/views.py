"""Deterministic multi-evidence view engine (roadmap P2) — the source of each
recommendation's *reason*.

The optimizer's Black-Litterman blend (``optimizer.py``) needs a per-ticker
**view**: a direction + magnitude (how much an asset should out/under-perform its
CAPM equilibrium) and a **confidence** (how sure we are). This module produces
those views from transparent, citeable evidence rather than an arbitrary scaling
of a BUY/HOLD/SELL label:

* **Value** — earnings yield E/P (= 1/PE), cross-sectional z-score. Cheap = positive
  view. (Basu 1977 "P/E effect"; Fama & French 1993 HML value factor.)
* **Quality** — return on equity, cross-sectional z-score. Profitable = positive.
  (Novy-Marx 2013 profitability/quality factor.)
* **Momentum** — 12-1 month total return (skip the most recent month), z-score.
  (Jegadeesh & Titman 1993.)
* **Sentiment** — optional EGX sector/index sentiment tilt (social_v2). Macro/sector
  level by design — per-stock social on EGX is too sparse to trust (CLAUDE.md §8).
* **Agent** — optional fresh ``TradingAgentsGraph`` BUY/SELL signal as one extra
  evidence source (folded in only when fresh; quant-prior HOLD contributes nothing).

Each factor is z-scored cross-sectionally across the universe and squashed to
``[-1, 1]``. A view's composite score ``s`` is the weight-normalised blend of the
sources that fired; its confidence rises with source *coverage* and *agreement*.
The optimizer turns ``s`` into the view return ``q_i = π_i + s·VIEW_SPREAD`` and
``confidence`` into the Idzorek-style view uncertainty ``Ω`` (see ``optimizer.py``).

Pure + deterministic: same inputs → identical views. No LLM. Network only via the
injected ratios/returns (the caller fetches them once and passes them in).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from tradingagents.portfolio.schemas import SignalLabel, SignalView

logger = logging.getLogger("tradingagents.portfolio.views")

#: Bump when any constant below changes (persisted in the proposal audit blob so a
#: proposal is reproducible against the evidence rules that produced it).
VIEWS_VERSION = "1.0"

#: Relative source weights in the composite score (inspectable, like the policy
#: compiler's tables). Fundamentals lead — they are this project's strongest,
#: most defensible subsystem. Re-normalised over whichever sources actually fire.
FACTOR_WEIGHTS: dict[str, float] = {
    "value": 0.35,
    "quality": 0.25,
    "momentum": 0.25,
    "sentiment": 0.10,
    "agent": 0.05,
}

#: 12-1 momentum window: total return over the last ~12 months, skipping the most
#: recent ~1 month (the short-term-reversal month, per Jegadeesh-Titman).
MOMENTUM_LOOKBACK = 252
MOMENTUM_SKIP = 21

_Z_CLIP = 3.0  # winsorise z-scores so one outlier name can't dominate a view


# ---------------------------------------------------------------------------
# View object (internal — provenance is surfaced via the proposal audit blob,
# kept out of schemas.py so the TS contract stays stable)
# ---------------------------------------------------------------------------

@dataclass
class Evidence:
    """One factor's contribution to a ticker's view (for the explainability chain)."""

    source: str          # value | quality | momentum | sentiment | agent
    metric: float        # the raw factor value (E/P, ROE, 12-1 return, …)
    z: float             # cross-sectional z-score
    score: float         # squashed contribution in [-1, 1]
    weight: float        # its weight in the composite


@dataclass
class TickerView:
    """A composite, evidence-backed view on one ticker."""

    ticker: str
    score: float                          # composite directional score in [-1, 1]
    confidence: float                     # in (0, 1]; feeds Idzorek Ω
    evidence: list[Evidence] = field(default_factory=list)

    def as_tuple(self) -> tuple[float, float]:
        """(score, confidence) — the compact form the optimizer consumes."""
        return self.score, self.confidence

    def provenance(self) -> dict:
        """JSON-able evidence record for the proposal audit / narrator."""
        return {
            "score": round(self.score, 4),
            "confidence": round(self.confidence, 4),
            "evidence": [
                {"source": e.source, "metric": round(e.metric, 4),
                 "z": round(e.z, 3), "score": round(e.score, 4), "weight": round(e.weight, 3)}
                for e in self.evidence
            ],
        }


# ---------------------------------------------------------------------------
# Cross-sectional helpers
# ---------------------------------------------------------------------------

def _zscores(values: Mapping[str, float]) -> dict[str, float]:
    """Cross-sectional, winsorised z-scores over the names that have a value.
    Returns {} when fewer than 2 names or zero dispersion (no relative signal)."""
    items = {t: float(v) for t, v in values.items() if v is not None and math.isfinite(float(v))}
    if len(items) < 2:
        return {}
    arr = np.array(list(items.values()), dtype=float)
    mu = float(arr.mean())
    sd = float(arr.std(ddof=0))
    if sd <= 1e-12:
        return {}
    return {t: float(np.clip((v - mu) / sd, -_Z_CLIP, _Z_CLIP)) for t, v in items.items()}


def _squash(z: float) -> float:
    """Map a z-score to a bounded directional score in (-1, 1)."""
    return math.tanh(z / 2.0)


# ---------------------------------------------------------------------------
# Per-factor scorers (each returns {ticker -> (metric, z, squashed_score)})
# ---------------------------------------------------------------------------

def _value_scores(ratios: Mapping[str, Mapping[str, float]]) -> dict[str, tuple[float, float, float]]:
    """Earnings yield E/P = 1/PE. Higher (cheaper) ⇒ positive view."""
    ey = {}
    for t, r in ratios.items():
        pe = r.get("pe_ratio")
        if pe is not None and pe > 0:
            ey[t] = 1.0 / pe
    zs = _zscores(ey)
    return {t: (ey[t], z, _squash(z)) for t, z in zs.items()}


def _quality_scores(ratios: Mapping[str, Mapping[str, float]]) -> dict[str, tuple[float, float, float]]:
    """Return on equity. Higher ⇒ positive view. (D/E is deliberately excluded —
    it is structurally high for banks and would penalise them spuriously.)"""
    roe = {t: r.get("roe") for t, r in ratios.items() if r.get("roe") is not None}
    zs = _zscores(roe)
    return {t: (roe[t], z, _squash(z)) for t, z in zs.items()}


def _momentum_scores(returns: Optional[pd.DataFrame]) -> dict[str, tuple[float, float, float]]:
    """12-1 month total return (skip the most recent month), cross-sectional z."""
    if returns is None or getattr(returns, "empty", True):
        return {}
    mom: dict[str, float] = {}
    for col in returns.columns:
        series = returns[col].dropna()
        if len(series) < MOMENTUM_SKIP + 60:  # need a meaningful window
            continue
        window = series.iloc[-MOMENTUM_LOOKBACK:-MOMENTUM_SKIP] if len(series) > MOMENTUM_LOOKBACK else series.iloc[:-MOMENTUM_SKIP]
        if len(window) < 2:
            continue
        mom[col] = float(np.prod(1.0 + window.to_numpy()) - 1.0)
    zs = _zscores(mom)
    return {t: (mom[t], z, _squash(z)) for t, z in zs.items()}


def _agent_scores(
    agent_signals: Optional[Mapping[str, SignalView]],
) -> dict[str, tuple[float, float, float]]:
    """Fresh agent BUY/SELL as a directional source (HOLD / stale / 0-conf abstain)."""
    if not agent_signals:
        return {}
    out: dict[str, tuple[float, float, float]] = {}
    for t, v in agent_signals.items():
        if v.is_stale or v.confidence <= 0 or v.label == SignalLabel.HOLD:
            continue
        sign = 1.0 if v.label == SignalLabel.BUY else -1.0
        score = sign * float(v.confidence)  # already in [-1, 1]
        out[t] = (score, score, score)  # metric == score (no z; it is pre-scaled)
    return out


def _sentiment_scores(
    sector_sentiment: Optional[Mapping[str, float]],
    sector_of,
    universe: Sequence[str],
) -> dict[str, tuple[float, float, float]]:
    """Apply a sector/index sentiment tilt to each name in that sector. Values are
    expected pre-normalised to [-1, 1] (the social_v2 aggregator's convention)."""
    if not sector_sentiment:
        return {}
    out: dict[str, tuple[float, float, float]] = {}
    for t in universe:
        sec = sector_of(t)
        if sec in sector_sentiment:
            val = float(np.clip(sector_sentiment[sec], -1.0, 1.0))
            out[t] = (val, val, val)
    return out


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def build_views(
    universe: Sequence[str],
    *,
    ratios: Optional[Mapping[str, Mapping[str, float]]] = None,
    returns: Optional[pd.DataFrame] = None,
    agent_signals: Optional[Mapping[str, SignalView]] = None,
    sector_sentiment: Optional[Mapping[str, float]] = None,
    sector_of=None,
) -> dict[str, TickerView]:
    """Build per-ticker composite views from the available evidence.

    All evidence inputs are optional and injected (the caller fetches them once):
    ``ratios`` (local fundamentals), ``returns`` (price history for momentum),
    ``agent_signals`` (fresh oracle decisions), ``sector_sentiment`` (social_v2).
    A ticker with no firing source gets **no view** (the optimizer leaves it at the
    equilibrium prior) — we never fabricate a reason.
    """
    ratios = ratios or {}
    if sector_of is None:
        from tradingagents.dataflows.social_v2.sectors import ticker_sector as sector_of

    per_source = {
        "value": _value_scores(ratios),
        "quality": _quality_scores(ratios),
        "momentum": _momentum_scores(returns),
        "agent": _agent_scores(agent_signals),
        "sentiment": _sentiment_scores(sector_sentiment, sector_of, universe),
    }

    views: dict[str, TickerView] = {}
    for t in universe:
        evidence: list[Evidence] = []
        for src, scores in per_source.items():
            if t in scores:
                metric, z, score = scores[t]
                evidence.append(Evidence(
                    source=src, metric=metric, z=z, score=score, weight=FACTOR_WEIGHTS[src]))
        if not evidence:
            continue
        wsum = sum(e.weight for e in evidence)
        composite = sum(e.score * e.weight for e in evidence) / wsum if wsum > 0 else 0.0
        views[t] = TickerView(
            ticker=t, score=float(np.clip(composite, -1.0, 1.0)),
            confidence=_confidence(evidence), evidence=evidence)
    return views


def _confidence(evidence: list[Evidence]) -> float:
    """Confidence in (0, 1] from source *coverage* and *agreement*.

    coverage  = covered weight / total possible weight (more evidence ⇒ surer).
    agreement = how aligned the firing sources are in sign/magnitude (a clean
                consensus ⇒ surer; conflicting evidence ⇒ less sure).
    """
    total_w = sum(FACTOR_WEIGHTS.values())
    coverage = sum(e.weight for e in evidence) / total_w if total_w > 0 else 0.0

    scores = [e.score for e in evidence]
    mean_abs = sum(abs(x) for x in scores) / len(scores)
    spread = float(np.std(scores)) if len(scores) > 1 else 0.0
    agreement = max(0.0, mean_abs - spread)  # strong + aligned ⇒ high

    conf = 0.15 + 0.55 * coverage + 0.30 * agreement
    return float(min(0.95, max(0.05, conf)))


__all__ = ["build_views", "TickerView", "Evidence", "VIEWS_VERSION", "FACTOR_WEIGHTS"]
