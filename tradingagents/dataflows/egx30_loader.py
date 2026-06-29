"""
Shared EGX30 index CSV loader.

Loads EGX30 historical closes from a local Investing.com CSV export.
Both the backtester benchmark and P3 relative-strength features consume
the same {YYYY-MM-DD: close_price} dict from this module.

The CSV is loaded once per process (lru_cache) and the full map is
immutable after load.  Callers truncate by trade_date at usage time.

No yfinance fallback — if the CSV is missing, returns empty dict.
"""
from __future__ import annotations

import csv
import logging
import os
from datetime import datetime
from functools import lru_cache
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Date formats supported by the Investing.com CSV (tried in order).
_CSV_DATE_FORMATS: List[str] = ["%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d"]

# Candidate filenames, searched in the project root.  First match wins.
_CSV_CANDIDATES: List[str] = [
    "EGX 30 Historical Data.csv",
    "EGX30ETF ETF Stock Price History.csv",
    "EGX30 ETF Stock Price History.csv",
    "egx30.csv",
]


def _parse_csv_date(raw: str) -> Optional[str]:
    """Try multiple date formats, return YYYY-MM-DD or None."""
    for fmt in _CSV_DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _find_project_root() -> str:
    """Return the project root (parent of tradingagents/)."""
    here = os.path.dirname(os.path.abspath(__file__))
    # here = tradingagents/dataflows/  →  project root is two levels up
    return os.path.normpath(os.path.join(here, "..", ".."))


def _locate_csv(project_root: Optional[str] = None) -> Optional[str]:
    """Search candidate filenames in the project root, return first match or None."""
    root = project_root or _find_project_root()
    for name in _CSV_CANDIDATES:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            return path
    return None


def _load_csv(csv_path: str) -> Dict[str, float]:
    """
    Parse an Investing.com-format CSV into {YYYY-MM-DD: close_price}.

    Handles:
      - UTF-8 BOM (encoding='utf-8-sig')
      - Comma-formatted prices ("51,994.63" → 51994.63)
      - Descending date order (output is unordered dict; callers sort)
      - Multiple date formats (MM/DD/YYYY, DD/MM/YYYY, YYYY-MM-DD)
    """
    data: Dict[str, float] = {}
    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                raw_date = row.get("Date", "").strip().strip('"')
                raw_price = row.get("Price", "").strip().strip('"').replace(",", "")
                if not raw_date or not raw_price:
                    continue
                try:
                    date_str = _parse_csv_date(raw_date)
                    if date_str is None:
                        continue
                    price = float(raw_price)
                    data[date_str] = price
                except (ValueError, TypeError):
                    continue
    except Exception as e:
        logger.warning("egx30_loader: CSV parse failed (%s): %s", csv_path, e)
        return {}

    if data:
        logger.info(
            "egx30_loader: loaded %d points, range %s → %s",
            len(data), min(data.keys()), max(data.keys()),
        )
    return data


@lru_cache(maxsize=1)
def load_egx30_csv(csv_path: Optional[str] = None) -> Dict[str, float]:
    """
    Load EGX30 index closes from a local Investing.com CSV.

    Args:
        csv_path: Explicit path.  If None, searches the standard candidate
                  list in the project root (same filenames as backtester).

    Returns:
        {YYYY-MM-DD: close_price} dict.  Empty dict on any failure.
        Prices have commas stripped.  Dict is cached per-process.
    """
    path = csv_path or _locate_csv()
    if path is None:
        logger.warning("egx30_loader: no EGX30 CSV found in project root")
        return {}
    return _load_csv(path)
