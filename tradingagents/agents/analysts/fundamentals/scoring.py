"""
Data confidence scoring for EGX Fundamental Analyst.

Computes data_confidence (0–100): "How much usable data do we have?"
This score is ONLY about data availability and recency.
It is NOT affected by what the data shows (business distress does not lower it).

Formula:
  data_confidence = (
      field_coverage    × 0.45  +   # required fields populated
      optional_coverage × 0.15  +   # optional fields populated
      staleness         × 0.25  +   # how recent is the last filing
      period_depth      × 0.15      # how many annual periods available
  ) × 100

Evidence class: [EI] — principle supported by [P2] FinMem importance scoring
and [P5] MarketSenseAI completeness checks.

Required fields (7 total):
  Income: revenue, gross_profit, operating_income, net_income (4)
  Balance: total_assets, total_liabilities, total_equity (3)

Optional fields (any field in EGX CSV schema beyond the required 7).
"""
from __future__ import annotations

from typing import Dict, List, Optional


# Weights for data_confidence formula
_FIELD_COVERAGE_WEIGHT = 0.45
_OPTIONAL_COVERAGE_WEIGHT = 0.15
_STALENESS_WEIGHT = 0.25
_PERIOD_DEPTH_WEIGHT = 0.15

# Required fields for data_confidence
_REQUIRED_INCOME_FIELDS = {"revenue", "gross_profit", "operating_income", "net_income"}
_REQUIRED_BALANCE_FIELDS = {"total_assets", "total_liabilities", "total_equity"}
_REQUIRED_FIELDS = _REQUIRED_INCOME_FIELDS | _REQUIRED_BALANCE_FIELDS

# All optional fields (from EGX CSV schema)
_OPTIONAL_INCOME_FIELDS = {
    "cost_of_revenue", "operating_expenses", "interest_expense",
    "tax_expense", "ebitda", "eps_basic", "eps_diluted",
}
_OPTIONAL_BALANCE_FIELDS = {
    "cash_and_equivalents", "accounts_receivable", "inventory",
    "current_assets", "fixed_assets", "current_liabilities",
    "long_term_debt", "retained_earnings", "shares_outstanding",
}
_OPTIONAL_RATIOS_FIELDS = {
    "pe_ratio", "eps", "debt_to_equity", "current_ratio",
    "roe", "roa", "gross_margin", "operating_margin", "net_margin",
    "book_value_per_share", "dividend_yield", "price_to_book",
}
_ALL_OPTIONAL_FIELDS = _OPTIONAL_INCOME_FIELDS | _OPTIONAL_BALANCE_FIELDS | _OPTIONAL_RATIOS_FIELDS


def compute_data_confidence(
    income_row: Dict[str, object],
    balance_row: Dict[str, object],
    ratios_row: Dict[str, object],
    n_annual_periods: int,
    periods_since_last_filing: int,
) -> int:
    """
    Compute data_confidence score (0–100).

    Args:
      income_row: Current period income data (field → value | None)
      balance_row: Current period balance data
      ratios_row: Current period ratios data
      n_annual_periods: Number of annual periods available (for period_depth)
      periods_since_last_filing: How many annual periods have elapsed since
        the most recent filed period. 0 = current period just filed.
        1 = one period gap. 5+ = very stale. Counts reporting periods, not days.

    Returns:
      Integer 0–100.
    """
    # ── field_coverage: required fields populated ─────────────────────────────
    populated_required = 0
    all_data = {**income_row, **balance_row}
    for field in _REQUIRED_FIELDS:
        val = all_data.get(field)
        if val is not None:
            populated_required += 1
    field_coverage = populated_required / len(_REQUIRED_FIELDS)

    # ── optional_coverage: optional fields populated ──────────────────────────
    all_optional_data = {**income_row, **balance_row, **ratios_row}
    populated_optional = 0
    for field in _ALL_OPTIONAL_FIELDS:
        val = all_optional_data.get(field)
        if val is not None:
            populated_optional += 1
    optional_coverage = (
        populated_optional / len(_ALL_OPTIONAL_FIELDS)
        if _ALL_OPTIONAL_FIELDS
        else 0.0
    )

    # ── staleness: period-based decay ────────────────────────────────────────
    # Decay starts after 1 missed period, reaches 0 at 6 missed periods.
    # staleness = max(0, 1 - (periods_since - 1) / 5)
    if periods_since_last_filing <= 1:
        staleness = 1.0
    elif periods_since_last_filing >= 6:
        staleness = 0.0
    else:
        staleness = max(0.0, 1.0 - (periods_since_last_filing - 1) / 5.0)

    # ── period_depth: how many annual periods available ───────────────────────
    # Normalize to 8 periods (8 years ~ meaningful trend visibility).
    period_depth = min(n_annual_periods, 8) / 8.0

    # ── Weighted sum ─────────────────────────────────────────────────────────
    score = (
        field_coverage * _FIELD_COVERAGE_WEIGHT
        + optional_coverage * _OPTIONAL_COVERAGE_WEIGHT
        + staleness * _STALENESS_WEIGHT
        + period_depth * _PERIOD_DEPTH_WEIGHT
    ) * 100

    return min(100, max(0, round(score)))


def estimate_periods_since_filing(n_available: int, n_periods_requested: int) -> int:
    """
    Heuristic: estimate periods_since_last_filing from available data.

    If we requested 5 periods and got 5, the data is current → 0 or 1.
    If we requested 5 but only got 2, the company likely has sparse data → 0
    (the data we have is current; it just doesn't go back far).

    In the absence of explicit filing dates, we use a conservative proxy:
    assume the data is current (periods_since = 0) because the local.py
    filing-lag filter already ensures the most recent row is a filed period.
    """
    # The filing lag in local.py prevents future periods from appearing.
    # If any data is available, the most recent row is a valid filed period → 0.
    if n_available > 0:
        return 0
    # No data at all → treat as maximally stale
    return 6


def determine_financial_health_heuristic(directions: Dict[str, str]) -> str:
    """
    Deterministic fallback for financial_health when CoT is not available.

    Uses majority-of-directions approach:
    - Count improving/deteriorating/stable across key profitability and leverage signals
    - Returns 'healthy' | 'concerning' | 'critical' | 'insufficient_data'

    Evidence class: [EI] — labeled as heuristic, not sourced.
    """
    positive_signals = ["improving", "stable"]
    negative_signals = ["deteriorating"]

    # Key metrics for health assessment
    profitability_keys = ["net_margin", "operating_margin", "roe", "roa"]
    leverage_keys = ["debt_to_equity", "total_liabilities"]

    positive_count = 0
    negative_count = 0
    total_signals = 0

    for key in profitability_keys + leverage_keys:
        direction = directions.get(key, "insufficient_history")
        if direction in ("improving", "stable"):
            positive_count += 1
            total_signals += 1
        elif direction == "deteriorating":
            negative_count += 1
            total_signals += 1
        # "insufficient_history" skipped — not counted

    if total_signals < 2:
        return "insufficient_data"

    # Majority vote with bias toward negative (conservative)
    negative_ratio = negative_count / total_signals
    if negative_ratio >= 0.6:
        return "critical"
    elif negative_ratio >= 0.35:
        return "concerning"
    else:
        return "healthy"
