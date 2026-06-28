"""Bridge: cross-sectional factor scores → Black-Litterman views → sized portfolio.

This is Track B Phase 5 — "portfolio construction in the live path". Instead of
deciding one name at a time, the factor engine ranks the whole universe and this
bridge turns those ranks into Black-Litterman *views* that drive the project's
EXISTING, tested optimizer (`portfolio/optimizer.py`: CAPM-equilibrium prior +
Idzorek confidence→Ω + long-only MVO). We deliberately reuse that optimizer rather
than build a second one — the review flagged duplicate sizing schemes as a smell.

Two layers:
  * ``factor_scores_to_views`` — pure mapping (composite → (score, confidence)),
    matching the optimizer's view contract (score ∈ [-1,1], confidence ∈ (0,1]).
  * ``build_factor_portfolio`` — orchestrator: factor scores → views → ``optimize``
    from an all-cash book, returning a sized, risk-aware, long-only proposal.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from tradingagents.factors.core import FactorInputs, FactorScore, rank_universe

logger = logging.getLogger("tradingagents.factors.portfolio_bridge")


def factor_scores_to_views(
    scores: Sequence[FactorScore],
    *,
    score_scale: float = 1.5,
    max_confidence: float = 0.6,
    min_abs_score: float = 0.05,
) -> Dict[str, Tuple[float, float]]:
    """Map composite factor scores onto the optimizer's view contract.

    The optimizer expects ``ticker → (score ∈ [-1,1], confidence ∈ (0,1])`` where
    score is the directional tilt (q_i = π_i + score·VIEW_SPREAD) and confidence
    sets the view's Ω (higher ⇒ the view binds harder).

    - ``score`` = composite / ``score_scale`` clipped to [-1, 1]. A composite of
      ``score_scale`` (default 1.5, ~1.5 z) expresses full conviction.
    - ``confidence`` = |score|, capped at ``max_confidence`` (factor views should
      tilt, not dictate — they're a statistical edge, not certainty).
    - Views weaker than ``min_abs_score`` are dropped (no meaningful tilt).
    """
    views: Dict[str, Tuple[float, float]] = {}
    for s in scores:
        score = max(-1.0, min(1.0, s.composite / score_scale)) if score_scale > 0 else 0.0
        if abs(score) < min_abs_score:
            continue
        confidence = min(max_confidence, abs(score))
        views[s.ticker] = (score, confidence)
    return views


def build_factor_portfolio(
    universe: Sequence[str],
    as_of: str,
    *,
    inputs: Optional[List[FactorInputs]] = None,
    closes_by_ticker: Optional[Mapping[str, Sequence[float]]] = None,
    prices: Optional[Mapping[str, float]] = None,
    covariance: Optional[Any] = None,           # pd.DataFrame; None ⇒ neutral diagonal
    market_weights: Optional[Mapping[str, float]] = None,
    capital_egp: float = 1_000_000.0,
    policy: Optional[Any] = None,               # InvestmentPolicy; None ⇒ default BALANCED
    score_scale: float = 1.5,
    max_confidence: float = 0.6,
):
    """Construct a sized, long-only factor portfolio from cash.

    Provide either ``inputs`` (pre-built FactorInputs — the test/offline path, no
    fundamentals fetch) or ``closes_by_ticker`` (the bridge builds inputs and pulls
    point-in-time fundamentals via the adapter). Returns the optimizer's
    ``OptimizationProposal`` (``.target_weights``, ``.actions``, ``.audit``).

    Sector-neutral factor z-scoring is applied so e.g. bank leverage is judged
    within sector. The optimizer enforces the long-only / per-name / per-sector /
    cash-floor constraints from the compiled policy.
    """
    # Lazy imports so importing this module (and the pure mapping above) stays light.
    from tradingagents.portfolio.schemas import PortfolioSnapshot, InvestmentPolicy
    from tradingagents.portfolio.analytics import compute_analytics
    from tradingagents.portfolio.policy_compiler import compile_policy
    from tradingagents.portfolio.optimizer import optimize
    from tradingagents.factors.fundamentals_adapter import build_factor_inputs, sector_map_for

    universe = list(dict.fromkeys(universe))
    if not universe:
        raise ValueError("build_factor_portfolio: empty universe")

    # --- factor inputs --------------------------------------------------------
    if inputs is None:
        if not closes_by_ticker:
            raise ValueError("provide either `inputs` or `closes_by_ticker`")
        inputs = [
            build_factor_inputs(t, list(closes_by_ticker[t]), as_of)
            for t in universe if t in closes_by_ticker and closes_by_ticker[t]
        ]
    inputs = [fi for fi in inputs if fi.ticker in universe]
    if len(inputs) < 2:
        raise ValueError("need >= 2 tickers with data to build a cross-sectional portfolio")

    present = [fi.ticker for fi in inputs]

    # --- prices (default: last close per ticker) ------------------------------
    if prices is None:
        prices = {fi.ticker: float(fi.closes[-1]) for fi in inputs if fi.closes}
    missing_px = [t for t in present if t not in prices]
    if missing_px:
        raise ValueError(f"missing prices for: {missing_px}")

    # --- factor scores → views (sector-neutral) -------------------------------
    sector_map = sector_map_for(present)
    scores = rank_universe(inputs, sector_map=sector_map)
    views = factor_scores_to_views(
        scores, score_scale=score_scale, max_confidence=max_confidence
    )

    # --- all-cash snapshot + compiled policy ----------------------------------
    snapshot = PortfolioSnapshot(cash_egp=float(capital_egp), holdings=[])
    analytics = compute_analytics(snapshot, prices)
    params, _flags = compile_policy(policy or InvestmentPolicy(), analytics)

    def _sector_of(t: str) -> str:
        return sector_map.get(t, "operational")

    proposal = optimize(
        snapshot,
        prices,
        params,
        views=views,
        covariance=covariance,
        market_weights=market_weights,
        candidate_tickers=present,
        sector_of=_sector_of,
    )
    logger.info(
        "build_factor_portfolio: %d names, %d views, top=%s",
        len(present), len(views), scores[0].ticker if scores else "n/a",
    )
    return proposal
