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

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from tradingagents.dataflows.config import DATA_DIR, get_config
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
from tradingagents.dataflows.symbol_utils import normalize_egx_ticker

logger = logging.getLogger(__name__)


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


@dataclass
class _LoadDiagnostics:
    """Diagnostics for a single CSV load operation."""
    path: str = ""
    file_exists: bool = False
    schema_valid: bool = False
    parse_error: Optional[str] = None
    rows_read: int = 0
    has_period_end_date: bool = False
    has_publish_date: bool = False
    date_filter_method: str = ""  # "publish_date", "filing_lag_120d", "filing_lag_45d", "none"
    rows_after_date_filter: int = 0
    rows_after_empty_filter: int = 0
    selected_periods: List[str] = field(default_factory=list)
    missing_required_fields: List[str] = field(default_factory=list)
    numeric_parse_warnings: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "file_exists": self.file_exists,
            "schema_valid": self.schema_valid,
            "parse_error": self.parse_error,
            "rows_read": self.rows_read,
            "has_period_end_date": self.has_period_end_date,
            "has_publish_date": self.has_publish_date,
            "date_filter_method": self.date_filter_method,
            "rows_after_date_filter": self.rows_after_date_filter,
            "rows_after_empty_filter": self.rows_after_empty_filter,
            "selected_periods": self.selected_periods,
            "missing_required_fields": self.missing_required_fields,
            "numeric_parse_warnings": self.numeric_parse_warnings,
            "warnings": self.warnings,
        }


def _read_egx_csv_multi(
    ticker: str,
    statement_type: str,  # "income_statements", "balance_sheets", "key_ratios"
    filename_suffix: str,  # e.g. "_income_annual.csv"
    required_fields: List[str],
    optional_fields: List[str],
    curr_date: Optional[str],
    freq: str = "annual",
    n_periods: int = 5,
) -> tuple[List[Dict[str, Optional[float]]], _LoadDiagnostics]:
    """
    Read an EGX CSV file and return up to n_periods rows as dicts.

    Returns:
      (rows, diagnostics) tuple where rows is a list of dicts (most recent
      first), each mapping field_name → float|None. Returns ([], diag) on
      any failure.
    """
    ticker = normalize_egx_ticker(ticker)
    data_path = os.path.join(
        DATA_DIR, EGX_FUNDAMENTALS_DIR, statement_type, f"{ticker}{filename_suffix}"
    )

    diag = _LoadDiagnostics(path=data_path)

    if not os.path.exists(data_path):
        diag.warnings.append("File not found")
        logger.warning("data_loader[%s]: file not found: %s", ticker, data_path)
        return [], diag

    diag.file_exists = True

    try:
        df = pd.read_csv(data_path)
    except Exception as e:
        diag.parse_error = str(e)
        diag.warnings.append(f"CSV parse error: {e}")
        logger.warning("data_loader[%s]: CSV parse error in %s: %s", ticker, data_path, e)
        return [], diag

    diag.rows_read = len(df)

    # Normalize column names
    df.columns = df.columns.str.lower().str.strip().str.replace(" ", "_")

    if "period_end_date" not in df.columns:
        diag.has_period_end_date = False
        diag.warnings.append("Missing 'period_end_date' column")
        logger.warning("data_loader[%s]: missing period_end_date in %s", ticker, data_path)
        return [], diag

    diag.has_period_end_date = True
    # has_publish_date is set later — True only when publish_date column
    # exists AND has at least one non-null value (see filing lag section).

    # Check for missing required fields (excluding period_end_date which we already checked)
    required_non_date = [f for f in required_fields if f != "period_end_date"]
    missing = [f for f in required_non_date if f not in df.columns]
    if missing:
        diag.missing_required_fields = missing
        diag.warnings.append(f"Missing required columns: {missing}")
        logger.info("data_loader[%s]: missing required columns in %s: %s", ticker, data_path, missing)

    # schema_valid = file exists + parses + has period_end_date + all required columns present
    diag.schema_valid = (not missing)

    df["period_end_date"] = pd.to_datetime(df["period_end_date"], errors="coerce")

    # Apply filing lag to prevent look-ahead bias
    if curr_date:
        curr_dt = pd.to_datetime(curr_date)
        # Use publish_date filter only when the column exists AND has at least
        # one non-null value. An all-null publish_date column (e.g. from yfinance
        # which doesn't provide filing dates) must fall back to the filing lag
        # heuristic — otherwise NaT <= curr_dt evaluates False and drops all rows.
        has_usable_publish_date = False
        if "publish_date" in df.columns:
            df["publish_date"] = pd.to_datetime(df["publish_date"], errors="coerce")
            has_usable_publish_date = df["publish_date"].notna().any()

        if has_usable_publish_date:
            df = df[df["publish_date"] <= curr_dt]
            diag.date_filter_method = "publish_date"
            diag.has_publish_date = True
        else:
            cfg = get_config()
            if freq == "annual":
                lag_days = cfg.get("filing_lag_annual_days", 120)
            else:
                lag_days = cfg.get("filing_lag_quarterly_days", 45)
            df = df[df["period_end_date"] <= (curr_dt - pd.Timedelta(days=lag_days))]
            diag.date_filter_method = f"filing_lag_{lag_days}d"
    else:
        diag.date_filter_method = "none"

    diag.rows_after_date_filter = len(df)

    if df.empty:
        diag.warnings.append("No rows pass date filter")
        return [], diag

    # Sort most-recent first, take up to n_periods
    df = df.sort_values("period_end_date", ascending=False).head(n_periods)

    all_fields = required_fields + optional_fields
    rows: List[Dict[str, Optional[float]]] = []

    for _, row in df.iterrows():
        period_dict: Dict[str, Optional[float]] = {}
        # Store period_end_date as a string metadata key
        if pd.notna(row["period_end_date"]):
            period_date_str = row["period_end_date"].strftime("%Y-%m-%d")
            period_dict["_period_end_date"] = period_date_str
        else:
            period_date_str = "unknown"
            period_dict["_period_end_date"] = None

        for field_name in all_fields:
            if field_name == "period_end_date":
                continue
            if field_name in row.index:
                raw_val = row[field_name]
                parsed = _sanitize_float(_parse_numeric_value(raw_val))
                period_dict[field_name] = parsed
                # Track when a non-empty cell fails to parse as numeric
                if parsed is None and raw_val is not None and not pd.isna(raw_val):
                    raw_str = str(raw_val).strip()
                    if raw_str and raw_str not in ("", "-", "N/A", "nan", "None"):
                        diag.numeric_parse_warnings.append(
                            f"Could not parse numeric field '{field_name}' "
                            f"at period {period_date_str}: '{raw_str}'"
                        )
            else:
                period_dict[field_name] = None

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

    diag.rows_after_empty_filter = len(rows)
    diag.selected_periods = [r["_period_end_date"] for r in rows if r.get("_period_end_date")]

    if diag.rows_after_date_filter > 0 and diag.rows_after_empty_filter == 0:
        diag.warnings.append("All rows had empty financial data (filtered as all-None)")

    return rows, diag


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
        "frequency": str,
        "n_income": int,          # actual periods available
        "n_balance": int,
        "n_ratios": int,
        "diagnostics": {
          "income": { path, file_exists, rows_read, ... },
          "balance": { ... },
          "ratios": { ... },
        }
      }

    Each inner dict has field_name → float|None, plus "_period_end_date".
    The diagnostics key provides full observability into the load process
    without changing the existing data contract.
    """
    income_rows, income_diag = _read_egx_csv_multi(
        ticker=ticker,
        statement_type="income_statements",
        filename_suffix=f"_income_{freq}.csv",
        required_fields=EGX_INCOME_REQUIRED_FIELDS,
        optional_fields=EGX_INCOME_OPTIONAL_FIELDS,
        curr_date=curr_date,
        freq=freq,
        n_periods=n_periods,
    )

    balance_rows, balance_diag = _read_egx_csv_multi(
        ticker=ticker,
        statement_type="balance_sheets",
        filename_suffix=f"_balance_{freq}.csv",
        required_fields=EGX_BALANCE_REQUIRED_FIELDS,
        optional_fields=EGX_BALANCE_OPTIONAL_FIELDS,
        curr_date=curr_date,
        freq=freq,
        n_periods=n_periods,
    )

    ratios_rows, ratios_diag = _read_egx_csv_multi(
        ticker=ticker,
        statement_type="key_ratios",
        filename_suffix=f"_ratios_{freq}.csv" if freq == "quarterly" else "_ratios.csv",
        required_fields=EGX_RATIOS_REQUIRED_FIELDS,
        optional_fields=EGX_RATIOS_OPTIONAL_FIELDS,
        curr_date=curr_date,
        freq=freq,
        n_periods=n_periods,
    )

    normalized_ticker = normalize_egx_ticker(ticker)

    # Log summary at debug level for operational visibility
    total_warnings = (
        len(income_diag.warnings) + len(balance_diag.warnings) + len(ratios_diag.warnings)
    )
    if total_warnings > 0:
        logger.info(
            "data_loader[%s/%s]: loaded %d/%d/%d periods (income/balance/ratios), %d warnings",
            normalized_ticker, freq,
            len(income_rows), len(balance_rows), len(ratios_rows),
            total_warnings,
        )

    return {
        "ticker": normalized_ticker,
        "frequency": freq,
        "income": income_rows,
        "balance": balance_rows,
        "ratios": ratios_rows,
        "n_income": len(income_rows),
        "n_balance": len(balance_rows),
        "n_ratios": len(ratios_rows),
        "diagnostics": {
            "income": income_diag.to_dict(),
            "balance": balance_diag.to_dict(),
            "ratios": ratios_diag.to_dict(),
        },
    }
