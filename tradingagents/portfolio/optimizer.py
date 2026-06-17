"""Portfolio optimization engine (roadmap P1) — the deterministic core's centerpiece.

Turns agent signals (BUY/HOLD/SELL + confidence) into a constraint-respecting,
discrete rebalancing *proposal* for human review. Pure and reproducible: same
inputs → byte-identical proposal.

Method — full Black-Litterman (Black & Litterman 1992), kept robust for short/gappy
EGX history. See docs/PORTFOLIO_METHODOLOGY.md for the citations.
  1. Equilibrium prior  π = δ · Σ · w_market   (reverse optimization; ``w_market``
     is the CAPM market-cap weight vector when supplied — He & Litterman 1999 —
     else it degrades to today's book as a minimum-trade prior).
  2. Views — per-ticker evidence views (value/quality/momentum via ``views.py``),
     each an *absolute* view  q_i = π_i + score · VIEW_SPREAD, with uncertainty Ω
     set from the view's confidence by an Idzorek (2005) style mapping
     ω_i = (1/c − 1)·(τ·σ_ii), inflated by the policy's horizon ``view_shrinkage``.
     Posterior  μ = π + τΣ·Pᵀ·(P·τΣ·Pᵀ + Ω)⁻¹·(Q − P·π)  (``_bl_posterior``).
  3. Long-only mean-variance optimization (cvxpy) with the compiled constraints
     (per-name / sector caps, cash floor, exclusions, vol target/ceiling) plus a
     turnover penalty.
  4. Discretize μ-optimal weights into integer share trades at live prices
     (board lots), ordered sells-before-buys for T+2 cash, then the EGX validation
     pass enforces long-only / ADV / no-leverage. Reported ``target_weights`` and
     after-metrics reflect the REALIZED (discretized + clipped) book, not the
     continuous optimum (kept in the audit as ``target_weights_continuous``).

Agent signals are NOT calibrated expected returns; ``expected_return_view_annual``
is a model view and is labeled as such. When cvxpy is unavailable or the problem
is infeasible, a rule-based ``heuristic_fallback`` tier produces a valid long-only
plan (clearly flagged).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from tradingagents.dataflows.social_v2.sectors import ticker_sector
from tradingagents.portfolio import ENGINE_VERSION
from tradingagents.portfolio.analytics import compute_analytics
from tradingagents.portfolio.egx_validation import validate_actions
from tradingagents.portfolio.market_data import diagonal_covariance
from tradingagents.portfolio.schemas import (
    OptimizationProposal,
    OptimizerParams,
    PolicyFlag,
    PortfolioSnapshot,
    RebalanceAction,
    SignalLabel,
    SignalView,
    SolverStatus,
    TradeSide,
)

logger = logging.getLogger("tradingagents.portfolio.optimizer")

#: Annualized over/under-performance vs the CAPM equilibrium implied by a
#: maximal-strength (|score|=1) composite view. The Black-Litterman view return
#: is q_i = π_i + score·VIEW_SPREAD, so the *evidence* (views.py) sets the sign
#: and magnitude and this constant only sets the scale of a strongest-possible view.
VIEW_SPREAD = 0.10
#: Backward-compat alias. The old additive-tilt `VIEW_SCALE` is superseded by the
#: full Black-Litterman blend below; kept so external imports don't break.
VIEW_SCALE = VIEW_SPREAD
#: Prior-uncertainty scalar τ in the Black-Litterman master formula (Black &
#: Litterman 1992). Small ⇒ the equilibrium prior is tightly held.
BL_TAU = 0.05
_DEFAULT_ANNUAL_VOL = 0.30  # fallback per-name vol when no covariance is available


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sign(label: SignalLabel) -> float:
    return {SignalLabel.BUY: 1.0, SignalLabel.SELL: -1.0, SignalLabel.HOLD: 0.0}[label]


def _build_sigma(universe: list[str], covariance: Optional[pd.DataFrame]) -> np.ndarray:
    """Annualized covariance matrix over ``universe``; fills unknown names with a
    neutral diagonal so the MVO always has a usable Σ."""
    n = len(universe)
    default_var = _DEFAULT_ANNUAL_VOL ** 2
    sigma = np.eye(n) * default_var
    if covariance is not None:
        idx = set(covariance.index)
        for i, ti in enumerate(universe):
            for j, tj in enumerate(universe):
                if ti in idx and tj in idx:
                    sigma[i, j] = float(covariance.loc[ti, tj])
    # symmetrize + tiny ridge for numerical PSD safety
    sigma = (sigma + sigma.T) / 2.0
    sigma += np.eye(n) * 1e-8
    return sigma


def _equilibrium_prior(
    universe: list[str], w_current: np.ndarray, sigma: np.ndarray,
    params: OptimizerParams, market_weights: Optional[np.ndarray],
) -> np.ndarray:
    """Reverse-optimization (implied-equilibrium) excess returns π = δ·Σ·w_mkt.

    ``market_weights`` is the CAPM-equilibrium (market-cap) weight vector over
    ``universe`` (He & Litterman 1999). When supplied, π pulls the no-view optimum
    toward the *market*, so the optimizer can recommend names the user does not yet
    hold. When absent, it degrades to the current book as the prior — the
    historical "minimum-trade" behaviour."""
    if market_weights is not None and float(np.sum(market_weights)) > 1e-9:
        w_mkt = market_weights / float(np.sum(market_weights))
    else:
        invested = float(w_current.sum())
        w_mkt = (w_current / invested) if invested > 1e-9 else np.full(len(universe), 1.0 / len(universe))
    return params.risk_aversion * sigma @ w_mkt


def _bl_posterior(
    universe: list[str], sigma: np.ndarray, prior: np.ndarray,
    views: Mapping[str, tuple[float, float]], params: OptimizerParams,
) -> np.ndarray:
    """Black-Litterman posterior expected returns (Black & Litterman 1992).

    ``views`` maps ticker → (score ∈ [-1,1], confidence ∈ (0,1]). Each view is an
    *absolute* view on one asset: q_i = π_i + score·VIEW_SPREAD (the evidence sets
    direction + magnitude). The view uncertainty Ω is set from the confidence via
    the Idzorek (2005) style mapping ω_i = (1/c − 1)·(p_iᵀ·τΣ·p_i): full confidence
    (c→1) ⇒ ω→0 (the view binds); low confidence (c→0) ⇒ ω→∞ (the view is ignored)
    and ``view_shrinkage`` (the policy compiler's horizon shrinkage) widens Ω
    further so short-horizon profiles distrust the views.

    Master formula: E[R] = π + τΣ·Pᵀ·(P·τΣ·Pᵀ + Ω)⁻¹·(Q − P·π)."""
    idx = {t: i for i, t in enumerate(universe)}
    shrink = float(min(max(params.view_shrinkage, 0.0), 0.999))
    rows = [(idx[t], float(sc), float(cf)) for t, (sc, cf) in views.items()
            if t in idx and cf > 0 and abs(sc) > 1e-9]
    if not rows or shrink >= 0.999:
        return prior.copy()

    n, k = len(universe), len(rows)
    tau_sigma = BL_TAU * sigma
    P = np.zeros((k, n))
    Q = np.zeros(k)
    omega = np.zeros((k, k))
    for r, (i, sc, cf) in enumerate(rows):
        P[r, i] = 1.0
        Q[r] = prior[i] + sc * VIEW_SPREAD
        view_var = float(P[r] @ tau_sigma @ P[r])  # = τ·σ_ii
        # confidence → Ω; horizon shrinkage inflates Ω (1/(1-shrink) ≥ 1).
        conf = cf * (1.0 - shrink)
        omega[r, r] = max((1.0 / conf - 1.0) * view_var, 1e-12)

    A = P @ tau_sigma @ P.T + omega
    rhs = Q - P @ prior
    try:
        sol = np.linalg.solve(A, rhs)
    except np.linalg.LinAlgError:  # pragma: no cover — near-singular A
        sol = np.linalg.lstsq(A, rhs, rcond=None)[0]
    return prior + tau_sigma @ P.T @ sol


def _solve_mvo(
    universe: list[str], mu: np.ndarray, sigma: np.ndarray, w0: np.ndarray,
    params: OptimizerParams, sector_of: Callable[[str], str],
) -> tuple[Optional[np.ndarray], SolverStatus]:
    """Long-only mean-variance optimization. Returns (weights, status). Tries the
    full problem, then relaxes the vol constraint, then sector caps; if cvxpy is
    unavailable or all relaxations fail, returns (None, HEURISTIC_FALLBACK)."""
    try:
        import cvxpy as cp
    except Exception:  # pragma: no cover — cvxpy not installed
        logger.warning("cvxpy unavailable; using heuristic fallback")
        return None, SolverStatus.HEURISTIC_FALLBACK

    n = len(universe)
    cap = params.max_position_pct / 100.0
    sector_cap = params.max_sector_pct / 100.0
    max_invested = 1.0 - params.min_cash_pct / 100.0
    excl_tickers = set(params.excluded_tickers)
    excl_sectors = set(params.excluded_sectors)

    def build(with_vol: bool, with_sector: bool):
        w = cp.Variable(n)
        cons = [w >= 0, cp.sum(w) <= max_invested]
        for i, t in enumerate(universe):
            ub = 0.0 if (t in excl_tickers or sector_of(t) in excl_sectors) else cap
            cons.append(w[i] <= ub)
        if with_sector:
            sectors: dict[str, list[int]] = {}
            for i, t in enumerate(universe):
                sectors.setdefault(sector_of(t), []).append(i)
            for grp in sectors.values():
                cons.append(cp.sum(w[grp]) <= sector_cap)
        vol_cap = params.vol_target or params.vol_ceiling
        if with_vol and vol_cap:
            cons.append(cp.quad_form(w, cp.psd_wrap(sigma)) <= vol_cap ** 2)
        objective = cp.Maximize(
            mu @ w
            - 0.5 * params.risk_aversion * cp.quad_form(w, cp.psd_wrap(sigma))
            - params.turnover_penalty * cp.norm1(w - w0)
        )
        return cp.Problem(objective, cons), w

    for with_vol, with_sector, status in (
        (True, True, SolverStatus.OPTIMAL),
        (False, True, SolverStatus.INFEASIBLE_RELAXED),
        (False, False, SolverStatus.INFEASIBLE_RELAXED),
    ):
        prob, w = build(with_vol, with_sector)
        try:
            prob.solve(solver=cp.CLARABEL)  # deterministic conic solver
        except Exception as exc:  # pragma: no cover — solver edge
            logger.debug("solver error (%s); trying next relaxation", exc)
            continue
        if w.value is not None and prob.status in ("optimal", "optimal_inaccurate"):
            weights = np.clip(np.asarray(w.value).flatten(), 0.0, None)
            return weights, status
    return None, SolverStatus.HEURISTIC_FALLBACK


#: A composite evidence view at/below this score is treated as bearish enough to
#: exit the name in the heuristic fallback (mirrors the BL path's directionality).
_HEURISTIC_BEARISH = -0.30


def _heuristic(
    universe: list[str], w0: np.ndarray, signals: Mapping[str, SignalView],
    params: OptimizerParams, sector_of: Callable[[str], str],
    views: Optional[Mapping[str, tuple[float, float]]] = None,
) -> np.ndarray:
    """Rule-based fallback (used when cvxpy is unavailable / infeasible): clip
    over-cap names, exit excluded names, strongly-bearish evidence views, and SELL
    signals, then deploy the freed budget into the most convincing positive views
    (evidence-led, agent-BUY as backup) pro-rata by conviction, respecting the
    per-name cap and cash floor. Always long-only and within caps."""
    cap = params.max_position_pct / 100.0
    max_invested = 1.0 - params.min_cash_pct / 100.0
    excl_tickers = set(params.excluded_tickers)
    excl_sectors = set(params.excluded_sectors)
    views = views or {}

    def _excluded(t: str) -> bool:
        return t in excl_tickers or sector_of(t) in excl_sectors

    w = w0.copy()
    for i, t in enumerate(universe):
        sig = signals.get(t)
        score = views.get(t, (0.0, 0.0))[0]
        if _excluded(t):
            w[i] = 0.0
        elif (sig is not None and sig.label == SignalLabel.SELL) or score <= _HEURISTIC_BEARISH:
            w[i] = 0.0
        else:
            w[i] = min(w[i], cap)

    budget = max_invested - float(w.sum())
    if budget > 1e-9:
        # deploy conviction: positive evidence view (score·conf), else agent-BUY conf.
        cand: list[tuple[int, float]] = []
        for i, t in enumerate(universe):
            if _excluded(t):
                continue
            sc, cf = views.get(t, (0.0, 0.0))
            dw = max(0.0, sc) * cf
            if dw <= 0 and t in signals and signals[t].label == SignalLabel.BUY:
                dw = float(signals[t].confidence)
            if dw > 0:
                cand.append((i, dw))
        total = sum(d for _, d in cand)
        if total > 0:
            for i, dw in cand:
                add = min(budget * dw / total, cap - w[i])
                w[i] += max(0.0, add)
    # final safety: respect cap + cash floor
    w = np.clip(w, 0.0, cap)
    if w.sum() > max_invested:
        w *= max_invested / w.sum()
    return w


#: A signal must clear this confidence to be treated as the *reason* for a trade.
#: Below it (incl. the conf=0.00 neutral/quant-prior the SignalResolver degrades
#: to when no fresh agent run exists), the trade is portfolio-construction driven.
_ACTIONABLE_SIGNAL_CONF = 0.05


def _action_rationale(
    side: TradeSide, cur_w: float, tgt_w: float,
    sig: Optional[SignalView], params: OptimizerParams,
    view: Optional[tuple[float, float]] = None,
    prov: Optional[Mapping[str, Any]] = None,
) -> str:
    """One-line, honest explanation of WHY this trade exists, so the user is never
    shown a directional verb (BUY/SELL) justified only by a neutral "HOLD signal".

    Priority of attribution:
      1. an *evidence-based view* (value / quality / momentum, via views.py) whose
         direction matches the trade → name the factors + composite + confidence,
         framed as a Black-Litterman tilt vs the market equilibrium;
      2. an *actionable agent signal* (non-HOLD, confidence ≥ threshold);
      3. otherwise the trade is portfolio construction under the user's risk
         profile (concentration cap / diversification toward the equilibrium /
         cash deployment) — stated plainly, never blamed on a 0.00-conf HOLD."""
    cap = params.max_position_pct
    move = f"{cur_w:.0f}% → {tgt_w:.0f}%"

    # 1. evidence-based view that agrees with the trade direction
    if view is not None and prov is not None:
        score, conf = float(view[0]), float(view[1])
        agrees = (side == TradeSide.BUY and score > 0) or (side == TradeSide.SELL and score < 0)
        if agrees and abs(score) >= 0.15:
            direction = "bullish" if score > 0 else "bearish"
            factors = _factor_phrase(prov)
            verb = "raises" if score > 0 else "lowers"
            return (f"{direction.capitalize()} {factors} view (composite {score:+.2f}, "
                    f"confidence {conf:.0%}); Black-Litterman {verb} its expected return vs the "
                    f"market equilibrium, so the optimizer moves it {move}.")

    # 2. actionable agent signal
    if sig is not None and sig.label != SignalLabel.HOLD and sig.confidence >= _ACTIONABLE_SIGNAL_CONF:
        view_word = "bullish" if sig.label == SignalLabel.BUY else "bearish"
        return f"Acting on a {view_word} agent signal (confidence {sig.confidence:.0%}); {move}."

    # 3. construction-driven (no directional evidence)
    if side == TradeSide.SELL:
        at_cap = cur_w >= cap - 1e-6
        lead = f"Trimming an over-cap holding to the {cap:.0f}% limit" if at_cap else "Trimming overweight"
        return f"{lead} ({move}) to cut concentration risk — no directional signal, driven by your risk profile."
    return (f"Topping up toward the {cap:.0f}% cap ({move}) to diversify toward the market "
            f"equilibrium and deploy idle cash — no directional signal, driven by your risk profile.")


#: Human labels for the evidence sources, used in per-trade rationales.
_FACTOR_LABELS = {
    "value": "value (low P/E)", "quality": "quality (ROE)", "momentum": "momentum",
    "agent": "agent signal", "sentiment": "sector sentiment",
}


def _factor_phrase(prov: Mapping[str, Any]) -> str:
    """Name the 1–2 sources that contributed most to a view (for the rationale)."""
    evidence = sorted(prov.get("evidence", []),
                      key=lambda e: -abs(float(e.get("score", 0.0)) * float(e.get("weight", 0.0))))
    labels = [_FACTOR_LABELS.get(e.get("source", ""), e.get("source", "")) for e in evidence[:2]]
    return " + ".join(labels) if labels else "model"


def _discretize(
    universe: list[str], w_target: np.ndarray, prices: Mapping[str, float],
    current_shares: Mapping[str, float], total_value: float, signals: Mapping[str, SignalView],
    lot_size: int, params: OptimizerParams,
    views: Optional[Mapping[str, tuple[float, float]]] = None,
    view_provenance: Optional[Mapping[str, Any]] = None,
) -> tuple[list[RebalanceAction], float, float]:
    """Convert target weights to integer share trades (board lots), sells first
    for T+2 cash. Returns (actions_sorted, est_total_cost_egp, est_turnover_pct)."""
    actions: list[RebalanceAction] = []
    est_cost = 0.0
    turnover_egp = 0.0
    for i, t in enumerate(universe):
        price = float(prices[t])
        target_egp = w_target[i] * total_value
        target_shares = int((target_egp / price) // lot_size * lot_size) if price > 0 else 0
        cur = int(round(current_shares.get(t, 0.0)))
        delta = target_shares - cur
        if delta == 0:
            continue
        side = TradeSide.BUY if delta > 0 else TradeSide.SELL
        qty = abs(delta)
        value = qty * price
        est_cost += value * params.transaction_cost_pct
        turnover_egp += value
        sig = signals.get(t)
        cur_w = (cur * price / total_value * 100.0) if total_value > 0 else 0.0
        tgt_w = float(w_target[i] * 100.0)
        view = views.get(t) if views else None
        prov = view_provenance.get(t) if view_provenance else None
        actions.append(RebalanceAction(
            ticker=t, side=side, shares=qty, price_used=price, est_value_egp=value,
            current_weight_pct=cur_w, target_weight_pct=tgt_w,
            rationale=_action_rationale(side, cur_w, tgt_w, sig, params, view=view, prov=prov),
            signal_session_id=sig.session_id if sig else None,
        ))
    # sells before buys (T+2: free cash before spending it)
    actions.sort(key=lambda a: 0 if a.side == TradeSide.SELL else 1)
    turnover_pct = (turnover_egp / total_value * 100.0) if total_value > 0 else 0.0
    return actions, est_cost, turnover_pct


def _vol(w: np.ndarray, sigma: np.ndarray) -> float:
    return float(math.sqrt(max(0.0, float(w @ sigma @ w))))


def _hhi(weights: np.ndarray) -> float:
    return float(min(max(sum(float(x) ** 2 for x in weights), 0.0), 1.0))


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def optimize(
    snapshot: PortfolioSnapshot,
    prices: Mapping[str, float],
    params: OptimizerParams,
    *,
    signals: Optional[Mapping[str, SignalView]] = None,
    views: Optional[Mapping[str, tuple[float, float]]] = None,
    view_provenance: Optional[Mapping[str, Any]] = None,
    covariance: Optional[pd.DataFrame] = None,
    market_weights: Optional[Mapping[str, float]] = None,
    total_invested_egp: Optional[float] = None,
    candidate_tickers: Optional[Sequence[str]] = None,
    lot_size: int = 1,
    adv: Optional[Mapping[str, float]] = None,
    sector_of: Optional[Callable[[str], str]] = None,
    conversation_id: Optional[str] = None,
    snapshot_id: Optional[int] = None,
    scenario_id: Optional[int] = None,
    policy_flags: Optional[list[PolicyFlag]] = None,
) -> OptimizationProposal:
    """Produce a rebalancing proposal for ``snapshot`` under ``params``."""
    signals = dict(signals or {})
    sector_of = sector_of or ticker_sector
    policy_flags = list(policy_flags or [])

    # --- current state via the (tested) analytics engine -------------------
    analytics = compute_analytics(snapshot, prices, total_invested_egp=total_invested_egp, signals=signals)
    total_value = analytics.total_value_egp
    current_shares = {h.ticker: h.shares for h in analytics.holdings}

    # --- universe: holdings + candidate buys (all must have prices) --------
    universe = list(dict.fromkeys(list(snapshot.tickers) + list(candidate_tickers or [])))
    missing = [t for t in universe if t not in prices]
    if missing:
        raise ValueError(f"no price for candidate(s): {missing}")
    if not universe:
        raise ValueError("empty universe: nothing to optimize")

    w0 = np.array([analytics.weights.get(t, 0.0) / 100.0 for t in universe])
    sigma = _build_sigma(universe, covariance)
    mkt_vec = (np.array([float(market_weights.get(t, 0.0)) for t in universe])
               if market_weights else None)

    # Views: prefer the explicit evidence-based views (views.py); otherwise derive
    # a simple directional view per fresh non-HOLD agent signal (back-compat).
    if views is not None:
        views_map = {t: (float(sc), float(cf)) for t, (sc, cf) in views.items()}
        views_source = "evidence_engine"
    else:
        views_map = {t: (_sign(v.label), float(v.confidence)) for t, v in signals.items()
                     if v.label != SignalLabel.HOLD and v.confidence > 0}
        views_source = "agent_signal"

    prior = _equilibrium_prior(universe, w0, sigma, params, mkt_vec)
    mu = _bl_posterior(universe, sigma, prior, views_map, params)

    # --- optimize (MVO → relaxations → heuristic) --------------------------
    w_target, status = _solve_mvo(universe, mu, sigma, w0, params, sector_of)
    if w_target is None:
        w_target = _heuristic(universe, w0, signals, params, sector_of, views=views_map)
        status = SolverStatus.HEURISTIC_FALLBACK

    # --- discretize, then the EGX regulatory pass (clip + disclose) -------
    actions, _, _ = _discretize(
        universe, w_target, prices, current_shares, total_value, signals,
        lot_size, params, views=views_map, view_provenance=view_provenance)
    actions, egx_flags = validate_actions(
        actions, current_shares=current_shares, available_cash_egp=analytics.cash_egp, adv=adv)
    policy_flags = policy_flags + egx_flags
    # metrics recomputed from the validated (possibly clipped) action set
    turnover_egp = sum(a.est_value_egp for a in actions)
    est_cost = turnover_egp * params.transaction_cost_pct
    turnover_pct = (turnover_egp / total_value * 100.0) if total_value > 0 else 0.0

    # --- REALIZED book: reconstruct the actual post-trade weights from the final
    # (discretized + EGX-validated/clipped) actions so the reported "after" metrics
    # match the executable plan, not the pre-rounding continuous optimum (F2). ----
    realized_shares = {t: float(current_shares.get(t, 0.0)) for t in universe}
    for a in actions:
        realized_shares[a.ticker] = realized_shares.get(a.ticker, 0.0) + (
            a.shares if a.side == TradeSide.BUY else -a.shares)
    w_realized = np.array([
        (realized_shares.get(t, 0.0) * float(prices[t]) / total_value) if total_value > 0 else 0.0
        for t in universe])

    target_weights = {t: float(w_realized[i] * 100.0) for i, t in enumerate(universe)}
    current_weights = {t: float(w0[i] * 100.0) for i, t in enumerate(universe)}

    audit = {
        "prices": {t: float(prices[t]) for t in universe},
        "params": params.model_dump(mode="json"),
        "signals": {t: signals[t].model_dump(mode="json") for t in signals},
        "covariance_hash": hashlib.sha256(
            json.dumps(np.round(sigma, 8).tolist(), sort_keys=True).encode()).hexdigest()[:16],
        "covariance_source": "ledoit_wolf" if covariance is not None else "diagonal_fallback",
        "universe": universe,
        "prior_source": "market_cap_equilibrium" if mkt_vec is not None else "current_weights",
        "market_weights": {t: float(market_weights[t]) for t in market_weights} if market_weights else {},
        "views_source": views_source,
        "views": {t: {"score": round(sc, 4), "confidence": round(cf, 4)}
                  for t, (sc, cf) in views_map.items()},
        "view_provenance": dict(view_provenance) if view_provenance else {},
        "bl_tau": BL_TAU,
        "view_spread": VIEW_SPREAD,
        "prior_return_annual": {t: float(prior[i]) for i, t in enumerate(universe)},
        "posterior_return_annual": {t: float(mu[i]) for i, t in enumerate(universe)},
        # continuous MVO optimum (pre-discretization) kept for transparency; the
        # reported target_weights/metrics below are the REALIZED post-trade book.
        "target_weights_continuous": {t: float(w_target[i] * 100.0) for i, t in enumerate(universe)},
        "expected_return_view_annual": float(mu @ w_realized),
    }

    if snapshot_id is None and scenario_id is None:
        snapshot_id = snapshot.snapshot_id if snapshot.snapshot_id is not None else 0

    return OptimizationProposal(
        conversation_id=conversation_id,
        snapshot_id=snapshot_id,
        scenario_id=scenario_id,
        policy_version=params.policy_version,
        actions=actions,
        current_weights=current_weights,
        target_weights=target_weights,
        expected_return_view_annual=float(mu @ w_realized),
        expected_vol_before=_vol(w0, sigma),
        expected_vol_after=_vol(w_realized, sigma),
        hhi_before=_hhi(w0),
        hhi_after=_hhi(w_realized),
        est_total_cost_egp=est_cost,
        est_turnover_pct=turnover_pct,
        solver_status=status,
        policy_flags=policy_flags,
        inputs_audit=audit,
        engine_version=ENGINE_VERSION,
    )


__all__ = ["optimize", "VIEW_SCALE", "VIEW_SPREAD", "BL_TAU"]
