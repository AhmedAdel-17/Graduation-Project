"""
Optional supplemental data loader for the EGX Fundamental Analyst.

The core Fundamental Analyst intentionally works without these files. This
module gives the project a local, auditable place to add forward-looking and
quality-of-earnings context before attempting another standalone direction
validation.

Expected local files under:
  tradingagents/dataflows/data_cache/egx_fundamentals/supplemental/

Files:
  valuation_context.csv
  quality_of_earnings.csv
  narrative_events.csv
  macro_sector_context.csv
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from tradingagents.dataflows.config import DATA_DIR
from tradingagents.dataflows.local import EGX_FUNDAMENTALS_DIR


SUPPLEMENTAL_DIR = Path(DATA_DIR) / EGX_FUNDAMENTALS_DIR / "supplemental"

VALUATION_FIELDS = [
    "ticker", "period_end_date", "frequency", "price_date", "close_price",
    "pe_ratio", "pb_ratio", "dividend_yield", "market_cap", "source", "notes",
]
QUALITY_FIELDS = [
    "ticker", "period_end_date", "frequency", "operating_cash_flow",
    "free_cash_flow", "interest_expense", "one_off_gains_losses",
    "one_off_description", "source", "notes",
]
NARRATIVE_FIELDS = [
    "ticker", "period_end_date", "frequency", "management_guidance",
    "event_flags", "one_off_event_notes", "source", "notes",
]
MACRO_FIELDS = [
    "sector", "period_end_date", "frequency", "inflation_yoy",
    "policy_rate", "egp_usd_change_yoy", "commodity_context",
    "sector_cycle_notes", "source", "notes",
]

TEMPLATE_SPECS = {
    "valuation_context.csv": VALUATION_FIELDS,
    "quality_of_earnings.csv": QUALITY_FIELDS,
    "narrative_events.csv": NARRATIVE_FIELDS,
    "macro_sector_context.csv": MACRO_FIELDS,
}

NUMERIC_FIELDS = {
    "close_price", "pe_ratio", "pb_ratio", "dividend_yield", "market_cap",
    "operating_cash_flow", "free_cash_flow", "interest_expense",
    "one_off_gains_losses", "inflation_yoy", "policy_rate",
    "egp_usd_change_yoy",
}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_ticker(ticker: str) -> str:
    return ticker.upper().replace(".CA", "").strip()


def _parse_float(value: Any) -> Optional[float]:
    text = _normalize_text(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _load_csv(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        out: Dict[str, Any] = {}
        for key, value in row.items():
            clean_key = _normalize_text(key).lower()
            if clean_key in NUMERIC_FIELDS:
                out[clean_key] = _parse_float(value)
            else:
                out[clean_key] = _normalize_text(value)
        normalized.append(out)
    return normalized


def _matches_common(row: Mapping[str, Any], frequency: str, period_end_date: str) -> bool:
    row_freq = _normalize_text(row.get("frequency")).lower()
    row_period = _normalize_text(row.get("period_end_date"))
    return row_freq == frequency.lower() and row_period == period_end_date


def _find_ticker_row(
    rows: Iterable[Mapping[str, Any]],
    ticker: str,
    period_end_date: str,
    frequency: str,
) -> Dict[str, Any]:
    normalized_ticker = _normalize_ticker(ticker)
    for row in rows:
        if (
            _normalize_ticker(_normalize_text(row.get("ticker"))) == normalized_ticker
            and _matches_common(row, frequency, period_end_date)
        ):
            return dict(row)
    return {}


def _find_sector_row(
    rows: Iterable[Mapping[str, Any]],
    sector: str,
    period_end_date: str,
    frequency: str,
) -> Dict[str, Any]:
    normalized_sector = _normalize_text(sector).lower()
    for row in rows:
        if (
            _normalize_text(row.get("sector")).lower() == normalized_sector
            and _matches_common(row, frequency, period_end_date)
        ):
            return dict(row)
    return {}


def load_supplemental_context(
    ticker: str,
    period_end_date: str,
    frequency: str,
    sector: str,
    supplemental_dir: str | os.PathLike[str] | None = None,
) -> Dict[str, Any]:
    """
    Load optional forward-looking supplemental context for one ticker/period.

    Missing files or rows are represented explicitly in `missing_categories`.
    This makes the data limitation visible without breaking existing behavior.
    """
    base = Path(supplemental_dir) if supplemental_dir is not None else SUPPLEMENTAL_DIR
    valuation = _find_ticker_row(
        _load_csv(base / "valuation_context.csv"), ticker, period_end_date, frequency
    )
    quality = _find_ticker_row(
        _load_csv(base / "quality_of_earnings.csv"), ticker, period_end_date, frequency
    )
    narrative = _find_ticker_row(
        _load_csv(base / "narrative_events.csv"), ticker, period_end_date, frequency
    )
    macro = _find_sector_row(
        _load_csv(base / "macro_sector_context.csv"), sector, period_end_date, frequency
    )

    categories = {
        "valuation_context": valuation,
        "quality_of_earnings": quality,
        "narrative_events": narrative,
        "macro_sector_context": macro,
    }
    missing = [name for name, value in categories.items() if not value]

    return {
        **categories,
        "missing_categories": missing,
        "source_directory": str(base),
    }


def ensure_supplemental_templates(
    supplemental_dir: str | os.PathLike[str] | None = None,
) -> List[str]:
    """Create empty local CSV templates if they do not already exist."""
    base = Path(supplemental_dir) if supplemental_dir is not None else SUPPLEMENTAL_DIR
    base.mkdir(parents=True, exist_ok=True)
    created: List[str] = []
    for filename, fields in TEMPLATE_SPECS.items():
        path = base / filename
        if path.exists():
            continue
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
        created.append(str(path))
    return created


def audit_supplemental_coverage(
    cases: Iterable[Mapping[str, Any]],
    supplemental_dir: str | os.PathLike[str] | None = None,
) -> List[Dict[str, Any]]:
    """Return per-case supplemental data coverage rows."""
    rows: List[Dict[str, Any]] = []
    for case in cases:
        ticker = _normalize_text(case.get("ticker"))
        period = _normalize_text(
            case.get("prediction_period") or case.get("period_end_date") or case.get("fiscal_period")
        )
        frequency = _normalize_text(case.get("frequency") or "annual")
        sector = _normalize_text(case.get("sector"))
        context = load_supplemental_context(
            ticker=ticker,
            period_end_date=period,
            frequency=frequency,
            sector=sector,
            supplemental_dir=supplemental_dir,
        )
        missing = context["missing_categories"]
        rows.append({
            "ticker": ticker,
            "sector": sector,
            "period_end_date": period,
            "frequency": frequency,
            "has_valuation_context": "valuation_context" not in missing,
            "has_quality_of_earnings": "quality_of_earnings" not in missing,
            "has_narrative_events": "narrative_events" not in missing,
            "has_macro_sector_context": "macro_sector_context" not in missing,
            "missing_categories": ";".join(missing),
        })
    return rows


def write_coverage_audit(rows: Iterable[Mapping[str, Any]], path: str | Path) -> None:
    """Write supplemental coverage audit rows to CSV."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "ticker", "sector", "period_end_date", "frequency",
        "has_valuation_context", "has_quality_of_earnings",
        "has_narrative_events", "has_macro_sector_context",
        "missing_categories",
    ]
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
