"""Wire the existing fundamentals subsystem into the factor engine.

The factor core (``core.FactorInputs``) accepts value/quality ratios but does not
know how to fetch them. This adapter bridges the gap by reusing the project's
deterministic fundamentals path — ``data_loader.load_multi_period`` (point-in-time
CSV loader, ``curr_date``-aware so it never returns periods after the as-of date) +
``FinancialCalculator.compute_all`` — and mapping the result onto the five factor
fields the engine consumes.

Point-in-time safety relies on ``load_multi_period(curr_date=as_of)`` not returning
future fiscal periods (the same guarantee the look-ahead-resolved backtester depends
on). The market price for value ratios (E/P, B/P) is the as-of close.

Everything fails open: any error or missing data yields all-None factor fields, so
the cross-sectional z-score treats the name as neutral on value/quality rather than
crashing the whole ranking.
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, Optional, Sequence

from tradingagents.factors.core import FactorInputs

logger = logging.getLogger("tradingagents.factors.fundamentals_adapter")

_EMPTY: Dict[str, Optional[float]] = {
    "earnings_yield": None,
    "book_to_price": None,
    "roe": None,
    "net_margin": None,
    "debt_to_equity": None,
}


def _coerce(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ratios_to_factor_fields(ratios: Dict) -> Dict[str, Optional[float]]:
    """Pure mapping: ``FundamentalAnalysisReport``-style ratios → factor fields.

    book_to_price is the reciprocal of pb_ratio (the value engine wants 'cheaper =
    higher'); the rest map straight through. Kept pure so it can be unit-tested
    without touching the CSV loader.
    """
    if not isinstance(ratios, dict):
        return dict(_EMPTY)
    pb = _coerce(ratios.get("pb_ratio"))
    book_to_price = (1.0 / pb) if (pb and pb > 0) else None
    return {
        "earnings_yield": _coerce(ratios.get("earnings_yield")),
        "book_to_price": book_to_price,
        "roe": _coerce(ratios.get("roe")),
        "net_margin": _coerce(ratios.get("net_margin")),
        "debt_to_equity": _coerce(ratios.get("debt_to_equity")),
    }


def fundamentals_for_factor(
    ticker: str,
    as_of: str,
    current_price: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    """Load point-in-time fundamentals for ``ticker`` and return factor fields.

    Returns all-None (neutral) on any failure or missing data.
    """
    try:
        from tradingagents.agents.analysts.fundamentals.data_loader import load_multi_period
        from tradingagents.agents.analysts.fundamentals.financial_calculator import (
            FinancialCalculator,
        )
    except Exception as exc:  # pragma: no cover — package always present in repo
        logger.warning("fundamentals_adapter: import failed: %s", exc)
        return dict(_EMPTY)

    try:
        multi = load_multi_period(ticker, curr_date=as_of, n_periods=2, freq="annual")
    except Exception as exc:
        logger.info("fundamentals_adapter: no fundamentals for %s @ %s: %s", ticker, as_of, exc)
        return dict(_EMPTY)

    income = multi.get("income") or []
    balance = multi.get("balance") or []
    ratios_periods = multi.get("ratios") or []
    cur_income = income[0] if income else {}
    cur_balance = balance[0] if balance else {}
    cur_ratios = ratios_periods[0] if ratios_periods else {}
    if not (cur_income or cur_balance or cur_ratios):
        return dict(_EMPTY)

    try:
        raw = FinancialCalculator.compute_all(
            revenue=cur_income.get("revenue"),
            gross_profit=cur_income.get("gross_profit"),
            operating_income=cur_income.get("operating_income"),
            net_income=cur_income.get("net_income"),
            total_assets=cur_balance.get("total_assets"),
            total_liabilities=cur_balance.get("total_liabilities"),
            total_equity=cur_balance.get("total_equity"),
            current_assets=cur_balance.get("current_assets"),
            current_liabilities=cur_balance.get("current_liabilities"),
            shares_outstanding=cur_balance.get("shares_outstanding"),
            eps_csv=cur_ratios.get("eps"),
            pe_ratio_csv=cur_ratios.get("pe_ratio"),
            pb_ratio_csv=cur_ratios.get("price_to_book"),
            roe_csv=cur_ratios.get("roe"),
            roa_csv=cur_ratios.get("roa"),
            net_margin_csv=cur_ratios.get("net_margin"),
            book_value_per_share=cur_ratios.get("book_value_per_share"),
            current_price=current_price,
        )
    except Exception as exc:
        logger.info("fundamentals_adapter: ratio compute failed for %s: %s", ticker, exc)
        return dict(_EMPTY)

    return ratios_to_factor_fields(raw)


def sector_map_for(tickers: Iterable[str]) -> Dict[str, str]:
    """Map each ticker to its EGX sector for sector-neutral factor z-scoring.

    Reuses the fundamentals subsystem's ``classify_sector`` (banks / real_estate /
    holdings / operational ...), which already encodes the sector taxonomy that
    makes e.g. a bank's high leverage 'normal'. Unknown tickers fall back to
    ``operational``.
    """
    try:
        from tradingagents.agents.analysts.fundamentals.sector_config import classify_sector
    except Exception as exc:  # pragma: no cover
        logger.warning("fundamentals_adapter: classify_sector import failed: %s", exc)
        return {t: "operational" for t in tickers}
    out: Dict[str, str] = {}
    for t in tickers:
        try:
            # SECTOR_MAP is keyed by bare symbol (COMI), not the .CA form (COMI.CA).
            bare = str(t).upper().split(".")[0]
            out[t] = classify_sector(bare)
        except Exception:
            out[t] = "operational"
    return out


def build_factor_inputs(
    ticker: str,
    closes: Sequence[float],
    as_of: str,
    current_price: Optional[float] = None,
) -> FactorInputs:
    """Build a fully-populated FactorInputs (price + fundamentals) for one ticker.

    ``current_price`` defaults to the last close (the as-of close), which is the
    correct point-in-time price for the value ratios.
    """
    px = current_price if current_price is not None else (closes[-1] if closes else None)
    fields = fundamentals_for_factor(ticker, as_of, current_price=px)
    return FactorInputs(ticker=ticker, closes=list(closes), **fields)
