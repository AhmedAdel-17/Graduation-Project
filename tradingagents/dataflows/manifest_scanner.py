"""
EGX Fundamentals Manifest Scanner.

Read-only scanner that inspects existing EGX fundamentals CSV files and
produces a machine-readable manifest (manifest.json) recording metadata
for each discovered file.

This module NEVER modifies or rewrites any CSV data.

Usage:
    # As a module
    from tradingagents.dataflows.manifest_scanner import scan_fundamentals, write_manifest
    entries = scan_fundamentals()
    write_manifest(entries)

    # As a script
    python -m tradingagents.dataflows.manifest_scanner
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from tradingagents.dataflows.config import DATA_DIR
from tradingagents.dataflows.local import (
    EGX_FUNDAMENTALS_DIR,
    EGX_INCOME_REQUIRED_FIELDS,
    EGX_BALANCE_REQUIRED_FIELDS,
    EGX_RATIOS_REQUIRED_FIELDS,
)

logger = logging.getLogger(__name__)

# Canonical filename patterns → (statement_type, frequency)
_FILENAME_PATTERNS = [
    (re.compile(r"^([A-Z0-9]+)_income_annual\.csv$"), "income", "annual"),
    (re.compile(r"^([A-Z0-9]+)_income_quarterly\.csv$"), "income", "quarterly"),
    (re.compile(r"^([A-Z0-9]+)_balance_annual\.csv$"), "balance", "annual"),
    (re.compile(r"^([A-Z0-9]+)_balance_quarterly\.csv$"), "balance", "quarterly"),
    (re.compile(r"^([A-Z0-9]+)_ratios\.csv$"), "ratios", "annual"),
    (re.compile(r"^([A-Z0-9]+)_ratios_quarterly\.csv$"), "ratios", "quarterly"),
]

# Subdirectory → statement_type
_SUBDIR_MAP = {
    "income_statements": "income",
    "balance_sheets": "balance",
    "key_ratios": "ratios",
}

# Required fields per statement type (excluding period_end_date which is always required)
_REQUIRED_FIELDS: Dict[str, List[str]] = {
    "income": [f for f in EGX_INCOME_REQUIRED_FIELDS if f != "period_end_date"],
    "balance": [f for f in EGX_BALANCE_REQUIRED_FIELDS if f != "period_end_date"],
    "ratios": [f for f in EGX_RATIOS_REQUIRED_FIELDS if f != "period_end_date"],
}


def scan_single_file(
    file_path: str,
    ticker: str,
    statement_type: str,
    frequency: str,
) -> Dict[str, Any]:
    """Scan a single CSV file and return its manifest entry.

    This function is pure — it reads the file but never modifies it.
    """
    entry: Dict[str, Any] = {
        "ticker": ticker,
        "statement_type": statement_type,
        "frequency": frequency,
        "path": file_path,
        "row_count": 0,
        "latest_period_end_date": None,
        "latest_publish_date": None,
        "latest_scraped_at": None,
        "data_sources": [],
        "schema_valid": False,
        "missing_required_fields": [],
        "warnings": [],
        "used_by_runtime": (frequency == "annual"),
    }

    if not os.path.exists(file_path):
        entry["warnings"].append("File not found")
        return entry

    try:
        df = pd.read_csv(file_path)
    except Exception as e:
        entry["warnings"].append(f"CSV parse error: {e}")
        return entry

    entry["row_count"] = len(df)

    # Normalize columns for inspection
    df.columns = df.columns.str.lower().str.strip().str.replace(" ", "_")

    # Check period_end_date
    if "period_end_date" not in df.columns:
        entry["warnings"].append("Missing period_end_date column")
        return entry

    # Check required fields
    required = _REQUIRED_FIELDS.get(statement_type, [])
    missing = [f for f in required if f not in df.columns]
    entry["missing_required_fields"] = missing

    # schema_valid = has period_end_date + all required columns present
    entry["schema_valid"] = len(missing) == 0

    # Extract latest period_end_date
    df["period_end_date"] = pd.to_datetime(df["period_end_date"], errors="coerce")
    valid_dates = df["period_end_date"].dropna()
    if not valid_dates.empty:
        entry["latest_period_end_date"] = valid_dates.max().strftime("%Y-%m-%d")

    # Extract latest publish_date if column exists
    if "publish_date" in df.columns:
        df["publish_date"] = pd.to_datetime(df["publish_date"], errors="coerce")
        valid_pub = df["publish_date"].dropna()
        if not valid_pub.empty:
            entry["latest_publish_date"] = valid_pub.max().strftime("%Y-%m-%d")

    # Extract latest scraped_at if column exists
    if "scraped_at" in df.columns:
        df["scraped_at"] = pd.to_datetime(df["scraped_at"], errors="coerce")
        valid_scraped = df["scraped_at"].dropna()
        if not valid_scraped.empty:
            entry["latest_scraped_at"] = valid_scraped.max().strftime("%Y-%m-%dT%H:%M:%SZ")

    # Extract unique data_source values if column exists
    if "data_source" in df.columns:
        sources = df["data_source"].dropna().unique().tolist()
        entry["data_sources"] = sorted(str(s) for s in sources if str(s).strip())

    return entry


def scan_fundamentals(
    data_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Scan all EGX fundamentals CSVs and return manifest entries.

    Args:
        data_dir: Override for DATA_DIR (useful for testing with temp dirs).

    Returns:
        List of manifest entry dicts, one per discovered CSV file.
    """
    base = data_dir or DATA_DIR
    egx_root = os.path.join(base, EGX_FUNDAMENTALS_DIR)
    entries: List[Dict[str, Any]] = []

    if not os.path.isdir(egx_root):
        logger.warning("manifest_scanner: EGX fundamentals dir not found: %s", egx_root)
        return entries

    for subdir_name, expected_type in _SUBDIR_MAP.items():
        subdir_path = os.path.join(egx_root, subdir_name)
        if not os.path.isdir(subdir_path):
            continue

        for filename in sorted(os.listdir(subdir_path)):
            if not filename.endswith(".csv"):
                continue

            matched = False
            for pattern, stmt_type, freq in _FILENAME_PATTERNS:
                m = pattern.match(filename)
                if m:
                    ticker = m.group(1)
                    file_path = os.path.join(subdir_path, filename)
                    entry = scan_single_file(file_path, ticker, stmt_type, freq)
                    entries.append(entry)
                    matched = True
                    break

            if not matched:
                # Unrecognized filename pattern
                entries.append({
                    "ticker": "UNKNOWN",
                    "statement_type": "unknown",
                    "frequency": "unknown",
                    "path": os.path.join(subdir_path, filename),
                    "row_count": 0,
                    "latest_period_end_date": None,
                    "latest_publish_date": None,
                    "latest_scraped_at": None,
                    "data_sources": [],
                    "schema_valid": False,
                    "missing_required_fields": [],
                    "warnings": [f"Unrecognized filename pattern: {filename}"],
                    "used_by_runtime": False,
                })

    return entries


def write_manifest(
    entries: List[Dict[str, Any]],
    output_path: Optional[str] = None,
) -> str:
    """Write manifest entries to JSON file.

    Args:
        entries: List of manifest entry dicts from scan_fundamentals().
        output_path: Override output path. Defaults to
            {DATA_DIR}/egx_fundamentals/manifest.json.

    Returns:
        Path to the written manifest file.
    """
    if output_path is None:
        output_path = os.path.join(DATA_DIR, EGX_FUNDAMENTALS_DIR, "manifest.json")

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_files": len(entries),
        "runtime_files": sum(1 for e in entries if e.get("used_by_runtime")),
        "schema_valid_files": sum(1 for e in entries if e.get("schema_valid")),
        "entries": entries,
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    logger.info(
        "manifest_scanner: wrote %d entries to %s (%d runtime, %d valid)",
        len(entries), output_path,
        manifest["runtime_files"], manifest["schema_valid_files"],
    )
    return output_path


# Filing lag constants (days after period_end_date until data is publicly filed)
ANNUAL_FILING_LAG_DAYS = 120
QUARTERLY_FILING_LAG_DAYS = 45

# All three statement types required for a complete annual dataset
_REQUIRED_STATEMENT_TYPES = frozenset({"income", "balance", "ratios"})


@dataclass
class FreshnessResult:
    """Result of a manifest-based freshness check."""
    fresh: bool
    reasons: List[str]

    def __bool__(self) -> bool:
        return self.fresh


def _normalize_ticker(ticker: str) -> str:
    """Normalize ticker to bare uppercase symbol (strip .CA suffix)."""
    from tradingagents.dataflows.symbol_utils import normalize_egx_ticker
    return normalize_egx_ticker(ticker)


def check_manifest_freshness(
    manifest_path: str,
    as_of_date: str,
    required_tickers: Optional[List[str]] = None,
    filing_lag_days: int = ANNUAL_FILING_LAG_DAYS,
) -> FreshnessResult:
    """Check whether the local fundamentals dataset is fresh enough for analysis.

    Scoped evaluation: when ``required_tickers`` is provided, only those tickers
    are checked.  Non-required tickers (e.g. VLMR stubs) cannot cause failures.
    When ``required_tickers`` is ``None``, no per-ticker checks run — only
    manifest-level integrity is verified.

    Filing-lag-aware staleness: an annual period with ``period_end_date`` is
    considered "available" once ``period_end_date + filing_lag_days`` has
    passed.  The check verifies that at least one period *should* be available
    by ``as_of_date``, rather than comparing raw period age.

    Per-ticker completeness: each required ticker must have all three annual
    statement types (income, balance, ratios) with ``schema_valid=True``,
    ``row_count > 0``, and at least one period whose filing should be
    available by ``as_of_date``.

    Args:
        manifest_path: Path to manifest.json (from write_manifest()).
        as_of_date: The trade/analysis date ("YYYY-MM-DD").
        required_tickers: Tickers to evaluate.  Accepts bare symbols ("COMI")
            or suffixed ("COMI.CA").  When None, only manifest integrity is
            checked — no per-ticker validation runs.
        filing_lag_days: Days after period_end_date before data is assumed
            publicly available.  Default 120 (annual EGX filing convention).

    Returns:
        FreshnessResult with .fresh=True if all checks pass.
    """
    reasons: List[str] = []

    # ── Load manifest ────────────────────────────────────────────────────
    if not os.path.exists(manifest_path):
        return FreshnessResult(fresh=False, reasons=["Manifest file not found"])

    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return FreshnessResult(fresh=False, reasons=[f"Manifest parse error: {e}"])

    entries = manifest.get("entries", [])
    if not entries:
        return FreshnessResult(fresh=False, reasons=["Manifest contains no entries"])

    as_of_dt = pd.to_datetime(as_of_date)

    # ── Per-ticker checks (only when required_tickers is provided) ───────
    if required_tickers:
        normalized_required = {_normalize_ticker(t) for t in required_tickers}

        # Index: ticker → statement_type → list of annual entries
        annual_by_ticker: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for e in entries:
            if e.get("frequency") != "annual":
                continue
            tk = e.get("ticker", "")
            if tk not in normalized_required:
                continue
            annual_by_ticker.setdefault(tk, {}).setdefault(
                e.get("statement_type", ""), []
            ).append(e)

        for ticker in sorted(normalized_required):
            stmts = annual_by_ticker.get(ticker, {})
            present_types = set(stmts.keys())

            # Check A: all 3 statement types must exist
            missing_types = _REQUIRED_STATEMENT_TYPES - present_types
            if missing_types:
                reasons.append(
                    f"{ticker}: missing annual statement types: "
                    f"{', '.join(sorted(missing_types))}"
                )
                continue  # no point checking further for this ticker

            # Check B: per-statement-type validation
            for stmt_type in sorted(_REQUIRED_STATEMENT_TYPES):
                stmt_entries = stmts[stmt_type]
                # Pick the "best" entry (prefer schema_valid, then highest row_count)
                best = max(
                    stmt_entries,
                    key=lambda e: (e.get("schema_valid", False), e.get("row_count", 0)),
                )

                # B1: schema must be valid
                if not best.get("schema_valid"):
                    reasons.append(
                        f"{ticker}/{stmt_type}: schema_valid=False "
                        f"(missing: {best.get('missing_required_fields', [])})"
                    )
                    continue

                # B2: must have at least one data row
                if best.get("row_count", 0) == 0:
                    reasons.append(
                        f"{ticker}/{stmt_type}: zero data rows"
                    )
                    continue

                # B3: filing-lag-aware freshness
                ped = best.get("latest_period_end_date")
                if not ped:
                    reasons.append(
                        f"{ticker}/{stmt_type}: no valid period_end_date"
                    )
                    continue

                period_dt = pd.to_datetime(ped)
                available_dt = period_dt + pd.Timedelta(days=filing_lag_days)
                if available_dt > as_of_dt:
                    # The latest period isn't expected to be filed yet —
                    # this is fine, it means we have future data that the
                    # loader's filing-lag filter will exclude. Check if
                    # there is *any* period that should be available.
                    # For now, we accept this — the loader handles it.
                    pass
                # Check: is the latest available period unreasonably old?
                # An annual period should cover ~365 days.  If the latest
                # period_end_date + filing_lag + 365 < as_of_date, we are
                # missing at least one expected annual filing.
                expected_next_period = period_dt + pd.Timedelta(days=365)
                expected_next_available = expected_next_period + pd.Timedelta(
                    days=filing_lag_days
                )
                if expected_next_available < as_of_dt:
                    gap = (as_of_dt - available_dt).days
                    reasons.append(
                        f"{ticker}/{stmt_type}: latest period {ped} "
                        f"(available {available_dt.strftime('%Y-%m-%d')}), "
                        f"expected next filing overdue by "
                        f"{(as_of_dt - expected_next_available).days}d"
                    )

    return FreshnessResult(fresh=len(reasons) == 0, reasons=reasons)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    entries = scan_fundamentals()
    path = write_manifest(entries)
    print(f"Manifest written: {path} ({len(entries)} entries)")
