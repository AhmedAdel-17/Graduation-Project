"""
Multi-period data loader for EGX Fundamental Analyst.

The existing local.py functions return only the most recent period.
This module reads the same CSV files and returns the last N periods,
enabling YoY/QoQ calculations in the standardizer.

This is a thin wrapper — all CSV parsing reuses the same column normalization
and numeric parsing logic from local.py.

Data source: the normalized CSVs under
``tradingagents/dataflows/data_cache/egx_fundamentals/`` are regenerated from the
richer Investing.com dump (``data/egx30_fundamentals/``) by
``scripts/normalize_egx30_fundamentals.py``. Re-run that converter after refreshing
the dump with ``scripts/fetch_egx_fundamentals.py``. See MEMORY.md §LL.
"""
from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional

import pandas as pd

from tradingagents.dataflows.config import DATA_DIR
from tradingagents.dataflows.local import (
    EGX_FUNDAMENTALS_DIR,
    EGX_INCOME_REQUIRED_FIELDS,
    EGX_INCOME_OPTIONAL_FIELDS,
    EGX_BALANCE_REQUIRED_FIELDS,
    EGX_BALANCE_OPTIONAL_FIELDS,
    EGX_RATIOS_REQUIRED_FIELDS,
    EGX_RATIOS_OPTIONAL_FIELDS,
    _parse_numeric_value,
)


def _sanitize_float(val: Optional[float]) -> Optional[float]:
    """Convert NaN/inf to None for JSON safety.

    _parse_numeric_value() in local.py converts the CSV strings 'nan' and 'inf'
    to float('nan') and float('inf') instead of None. These values:
      - Pass 'is not None' guards in financial_calculator.py (producing NaN ratios)
      - Produce non-compliant JSON when json.dumps() is called on fundamental_analysis
        (bull_researcher.py uses json.dumps() directly on this dict)

    This boundary sanitizer ensures all values returned by the loader are either
    a finite float or None — never NaN or infinity.
    """
    if val is None:
        return None
    if isinstance(val, float) and not math.isfinite(val):
        return None
    return val


def _read_egx_csv_multi(
    ticker: str,
    statement_type: str,  # "income_statements", "balance_sheets", "key_ratios"
    filename_suffix: str,  # e.g. "_income_annual.csv"
    required_fields: List[str],
    optional_fields: List[str],
    curr_date: Optional[str],
    freq: str = "annual",
    n_periods: int = 5,
) -> List[Dict[str, Optional[float]]]:
    """
    Read an EGX CSV file and return up to n_periods rows as dicts.

    Returns list of dicts (most recent first) where each dict maps
    field_name → float|None. Returns empty list on any failure.
    """
    ticker = ticker.upper().replace(".CA", "").strip()
    data_path = os.path.join(
        DATA_DIR, EGX_FUNDAMENTALS_DIR, statement_type, f"{ticker}{filename_suffix}"
    )

    if not os.path.exists(data_path):
        return []

    try:
        df = pd.read_csv(data_path)
    except Exception:
        return []

    # Normalize column names
    df.columns = df.columns.str.lower().str.strip().str.replace(" ", "_")

    if "period_end_date" not in df.columns:
        return []

    df["period_end_date"] = pd.to_datetime(df["period_end_date"], errors="coerce")

    # Apply filing lag to prevent look-ahead bias
    if curr_date:
        curr_dt = pd.to_datetime(curr_date)
        if "publish_date" in df.columns:
            df["publish_date"] = pd.to_datetime(df["publish_date"], errors="coerce")
            df = df[df["publish_date"] <= curr_dt]
        else:
            lag_days = 120 if freq == "annual" else 45
            df = df[df["period_end_date"] <= (curr_dt - pd.Timedelta(days=lag_days))]

    if df.empty:
        return []

    # Sort most-recent first, take up to n_periods
    df = df.sort_values("period_end_date", ascending=False).head(n_periods)

    all_fields = required_fields + optional_fields
    rows: List[Dict[str, Optional[float]]] = []

    for _, row in df.iterrows():
        period_dict: Dict[str, Optional[float]] = {}
        # Store period_end_date as a string metadata key
        if pd.notna(row["period_end_date"]):
            period_dict["_period_end_date"] = row["period_end_date"].strftime("%Y-%m-%d")
        else:
            period_dict["_period_end_date"] = None

        for field in all_fields:
            if field == "period_end_date":
                continue
            if field in row.index:
                period_dict[field] = _sanitize_float(_parse_numeric_value(row[field]))
            else:
                period_dict[field] = None

        rows.append(period_dict)

    # Drop rows where all financial values are None (e.g., EGAL June-30 FY:
    # yfinance returns an empty 2025-06-30 row that the filing-lag filter
    # doesn't catch because its period_end_date <= curr_date - 120d passes,
    # but the row has no actual data. Using it as [0] (most-recent) causes
    # the loader to hand an all-None income dict to the calculator.
    rows = [
        r for r in rows
        if any(
            v is not None
            for k, v in r.items()
            if not k.startswith("_")
        )
    ]

    return rows


def load_multi_period(
    ticker: str,
    curr_date: Optional[str],
    n_periods: int = 5,
    freq: str = "annual",
) -> Dict[str, Any]:
    """
    Load up to n_periods of income, balance, and ratios data for a ticker.

    Returns:
      {
        "income": [dict, ...],    # most recent first
        "balance": [dict, ...],
        "ratios": [dict, ...],
        "ticker": str,
        "n_income": int,          # actual periods available
        "n_balance": int,
        "n_ratios": int,
      }

    Each inner dict has field_name → float|None, plus "_period_end_date".
    """
    income_rows = _read_egx_csv_multi(
        ticker=ticker,
        statement_type="income_statements",
        filename_suffix=f"_income_{freq}.csv",
        required_fields=EGX_INCOME_REQUIRED_FIELDS,
        optional_fields=EGX_INCOME_OPTIONAL_FIELDS,
        curr_date=curr_date,
        freq=freq,
        n_periods=n_periods,
    )

    balance_rows = _read_egx_csv_multi(
        ticker=ticker,
        statement_type="balance_sheets",
        filename_suffix=f"_balance_{freq}.csv",
        required_fields=EGX_BALANCE_REQUIRED_FIELDS,
        optional_fields=EGX_BALANCE_OPTIONAL_FIELDS,
        curr_date=curr_date,
        freq=freq,
        n_periods=n_periods,
    )

    ratios_rows = _read_egx_csv_multi(
        ticker=ticker,
        statement_type="key_ratios",
        filename_suffix=f"_ratios_{freq}.csv" if freq == "quarterly" else "_ratios.csv",
        required_fields=EGX_RATIOS_REQUIRED_FIELDS,
        optional_fields=EGX_RATIOS_OPTIONAL_FIELDS,
        curr_date=curr_date,
        freq=freq,
        n_periods=n_periods,
    )

    return {
        "ticker": ticker.upper().replace(".CA", "").strip(),
        "income": income_rows,
        "balance": balance_rows,
        "ratios": ratios_rows,
        "n_income": len(income_rows),
        "n_balance": len(balance_rows),
        "n_ratios": len(ratios_rows),
    }
