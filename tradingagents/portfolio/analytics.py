"""Deterministic portfolio analytics (roadmap P1).

``compute_analytics(snapshot, prices, ...) -> PortfolioAnalytics`` — pure
arithmetic over a confirmed portfolio at given prices. No LLM, no network: every
number here is ground truth that the Narrator (P2) injects verbatim into its
prompt. Same inputs → byte-identical output.

Absolute-anchor requirement
---------------------------
``PortfolioAnalytics`` reports EGP market values, P&L and cash drag, so analytics
needs an absolute anchor for every holding: either per-holding ``shares`` or a
``total_invested_egp`` to convert ``weight_pct`` into EGP. A weight-only holding
with no total cannot be valued in EGP, so this raises ``ValueError`` rather than
fabricate a number — obtaining the anchor is the extraction/confirmation layer's
job (P2). This keeps the contract honest.

Definitions (documented so the Narrator can explain them):
* weights are % of **total** portfolio value (holdings + cash).
* HHI = Σ (holding_weight_fraction_of_total)² over holdings only (cash excluded
  as a term), so more cash ⇒ lower concentration. Range [0, 1].
* sector / index exposure are % of total. Index exposure overlaps by design
  (EGX100 ⊇ EGX30 ∪ EGX70), so a blue chip counts toward EGX30 and EGX100.
* portfolio beta / vol weight by total (cash has beta 0 and no return), and use
  only tickers with ≥ ``min_history_days`` of returns; the rest are listed in
  ``min_history_excluded`` and dropped from the risk math (their composition
  weight still counts).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Mapping, Optional

import numpy as np
import pandas as pd

from tradingagents.dataflows.social_v2.sectors import ticker_sector
from tradingagents.portfolio.schemas import (
    EGXIndex,
    HoldingAnalytics,
    PortfolioAnalytics,
    PortfolioSnapshot,
    SignalView,
)

logger = logging.getLogger("tradingagents.portfolio.analytics")

_TRADING_DAYS = 252


def _indices_for(ticker: str) -> list[EGXIndex]:
    """Map taxonomy IndexEnum → our EGXIndex (kept decoupled from schemas.py)."""
    try:
        from tradingagents.sentiment.taxonomy import ticker_to_indices
    except Exception:  # pragma: no cover — taxonomy import edge
        return []
    out: list[EGXIndex] = []
    for idx in ticker_to_indices(ticker):
        try:
            out.append(EGXIndex(idx.value))
        except ValueError:  # pragma: no cover — unknown index value
            continue
    return out


def _resolve_market_value(
    holding, price: float, total_invested_egp: Optional[float]
) -> tuple[float, float]:
    """Return ``(shares, market_value_egp)`` for one holding, raising when the
    portfolio lacks an absolute anchor for it."""
    if holding.shares is not None:
        return float(holding.shares), float(holding.shares) * price
    if holding.weight_pct is not None and total_invested_egp is not None:
        mv = holding.weight_pct / 100.0 * total_invested_egp
        shares = mv / price if price > 0 else 0.0
        return shares, mv
    raise ValueError(
        f"holding {holding.ticker} has no absolute anchor: provide `shares` on the "
        f"holding or a `total_invested_egp` to convert weight_pct into EGP."
    )


def _beta(asset: pd.Series, bench: pd.Series) -> Optional[float]:
    """Single-asset beta vs benchmark over pairwise non-NaN dates."""
    joined = pd.concat([asset, bench], axis=1, join="inner").dropna()
    if len(joined) < 2:
        return None
    a = joined.iloc[:, 0].to_numpy()
    b = joined.iloc[:, 1].to_numpy()
    var_b = float(np.var(b, ddof=1))
    if var_b <= 0:
        return None
    cov_ab = float(np.cov(a, b, ddof=1)[0, 1])
    return cov_ab / var_b


def compute_analytics(
    snapshot: PortfolioSnapshot,
    prices: Mapping[str, float],
    *,
    total_invested_egp: Optional[float] = None,
    signals: Optional[Mapping[str, SignalView]] = None,
    returns: Optional[pd.DataFrame] = None,
    benchmark_returns: Optional[pd.Series] = None,
    beta_is_proxy: bool = False,
    min_history_days: int = 60,
    price_asof: Optional[datetime] = None,
) -> PortfolioAnalytics:
    """Compute analytics for ``snapshot`` at ``prices``.

    Parameters
    ----------
    prices : ticker -> live price (EGP/share). Must contain every holding.
    total_invested_egp : anchor for weight-only holdings (see module docstring).
    signals : optional ticker -> SignalView, attached to each HoldingAnalytics.
    returns : optional daily-return DataFrame (columns = tickers) for vol/beta.
    benchmark_returns : optional EGX30 (or proxy) daily-return Series for beta.
    beta_is_proxy : True when benchmark_returns came from a constituent proxy
        basket rather than the real index (roadmap finding #3).
    """
    signals = signals or {}
    holdings = snapshot.holdings
    cash = float(snapshot.cash_egp)

    # --- resolve each holding to (shares, market value) --------------------
    resolved: list[tuple] = []  # (holding, price, shares, mv)
    for h in holdings:
        if h.ticker not in prices:
            raise ValueError(f"no price for holding {h.ticker}")
        price = float(prices[h.ticker])
        if price <= 0:
            raise ValueError(f"non-positive price for {h.ticker}: {price}")
        shares, mv = _resolve_market_value(h, price, total_invested_egp)
        resolved.append((h, price, shares, mv))

    invested = sum(mv for *_, mv in resolved)
    total = invested + cash
    if total <= 0:
        raise ValueError("total portfolio value must be positive")

    # --- which tickers have enough history for risk math -------------------
    excluded: list[str] = []
    usable_returns: set[str] = set()
    if returns is not None:
        for h, *_ in resolved:
            col = h.ticker
            if col in returns.columns and int(returns[col].notna().sum()) >= min_history_days:
                usable_returns.add(col)
            else:
                excluded.append(col)

    # --- per-holding analytics + grouped exposures -------------------------
    holding_rows: list[HoldingAnalytics] = []
    weights: dict[str, float] = {}
    sector_exposure: dict[str, float] = {}
    index_exposure: dict[str, float] = {}

    for h, price, shares, mv in resolved:
        weight_pct = mv / total * 100.0
        weights[h.ticker] = weight_pct

        pnl_egp = pnl_pct = None
        if h.avg_cost is not None and h.avg_cost > 0:
            pnl_egp = (price - h.avg_cost) * shares
            pnl_pct = (price / h.avg_cost - 1.0) * 100.0

        sector = ticker_sector(h.ticker)
        sector_exposure[sector] = sector_exposure.get(sector, 0.0) + weight_pct

        indices = _indices_for(h.ticker)
        for idx in indices:
            index_exposure[idx.value] = index_exposure.get(idx.value, 0.0) + weight_pct

        holding_rows.append(HoldingAnalytics(
            ticker=h.ticker, shares=shares, price=price, market_value_egp=mv,
            weight_pct=weight_pct, avg_cost=h.avg_cost,
            unrealized_pnl_egp=pnl_egp, unrealized_pnl_pct=pnl_pct,
            sector=sector, indices=indices, signal=signals.get(h.ticker),
        ))

    # --- concentration -----------------------------------------------------
    hhi = sum((w / 100.0) ** 2 for w in weights.values())
    hhi = min(max(hhi, 0.0), 1.0)  # guard float drift at the bounds

    # --- risk metrics (optional) ------------------------------------------
    portfolio_beta: Optional[float] = None
    annual_vol: Optional[float] = None
    if returns is not None and usable_returns:
        # total-weight vector over usable tickers (cash contributes 0)
        wvec = {h.ticker: (mv / total) for h, _p, _s, mv in resolved if h.ticker in usable_returns}

        if benchmark_returns is not None:
            betas = {t: _beta(returns[t], benchmark_returns) for t in wvec}
            if any(b is not None for b in betas.values()):
                portfolio_beta = sum(w * betas[t] for t, w in wvec.items() if betas[t] is not None)

        sub = returns[list(wvec)].dropna(how="all")
        if not sub.empty:
            w_series = pd.Series(wvec)
            port_daily = sub.mul(w_series, axis=1).sum(axis=1).dropna()
            if len(port_daily) >= 2:
                annual_vol = float(port_daily.std(ddof=1) * math.sqrt(_TRADING_DAYS))

    return PortfolioAnalytics(
        total_value_egp=total,
        invested_egp=invested,
        cash_egp=cash,
        cash_drag_pct=cash / total * 100.0,
        holdings=holding_rows,
        weights=weights,
        hhi=hhi,
        sector_exposure=sector_exposure,
        index_exposure=index_exposure,
        portfolio_beta=portfolio_beta,
        beta_is_proxy=beta_is_proxy if portfolio_beta is not None else False,
        annual_vol=annual_vol,
        min_history_excluded=excluded,
        price_asof=price_asof,
    )


__all__ = ["compute_analytics"]
