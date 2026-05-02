"""
Compute the empirical EGX annual net-income "up" base rate from local CSVs.

For each ticker, for each consecutive pair of annual periods, classify the
direction of net_income as:
  "up"   — net_income[t] > net_income[t-1]
  "down" — net_income[t] < net_income[t-1]
  "flat" — net_income[t] == net_income[t-1] (rare; excluded from rate)

Saves results to PROOF_OF_WORK_base_rate.json in the project root.

Usage:
    python scripts/compute_egx_base_rate.py
"""
from __future__ import annotations

import csv
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Paths ─────────────────────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_INCOME_DIR = _PROJECT_ROOT / "tradingagents" / "dataflows" / "data_cache" / "egx_fundamentals" / "income_statements"
_OUTPUT_FILE = _PROJECT_ROOT / "PROOF_OF_WORK_base_rate.json"


def _load_annual_income(ticker: str) -> List[Dict]:
    """
    Load annual income rows for a ticker, sorted oldest-first.
    Returns list of dicts with at least 'period_end_date' and 'net_income'.
    """
    path = _INCOME_DIR / f"{ticker}_income_annual.csv"
    if not path.exists():
        return []
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ni_raw = row.get("net_income", "").strip()
            period = row.get("period_end_date", "").strip()
            if not ni_raw or not period or ni_raw in ("", "None", "nan"):
                continue
            try:
                net_income = float(ni_raw)
            except ValueError:
                continue
            rows.append({"period_end_date": period, "net_income": net_income})
    # Sort oldest-first (ascending by date string; ISO format sorts correctly)
    rows.sort(key=lambda r: r["period_end_date"])
    return rows


def _classify_direction(current: float, prior: float) -> Optional[str]:
    """Return 'up', 'down', or None (flat/indeterminate)."""
    if current > prior:
        return "up"
    elif current < prior:
        return "down"
    return None  # exactly equal — excluded


def compute_base_rate() -> Dict:
    """
    Iterate over all *_income_annual.csv files, compute YoY net_income
    direction for each consecutive pair, and aggregate counts.
    """
    annual_files = sorted(_INCOME_DIR.glob("*_income_annual.csv"))
    tickers = [f.stem.replace("_income_annual", "") for f in annual_files]

    total_up = 0
    total_down = 0
    ticker_stats: Dict[str, Dict] = {}
    per_year_up: Dict[str, int] = defaultdict(int)
    per_year_down: Dict[str, int] = defaultdict(int)

    for ticker in tickers:
        rows = _load_annual_income(ticker)
        if len(rows) < 2:
            ticker_stats[ticker] = {"up": 0, "down": 0, "pairs": 0, "pairs_data": []}
            continue

        ticker_up = 0
        ticker_down = 0
        pairs_data: List[Dict] = []

        for i in range(1, len(rows)):
            prior = rows[i - 1]
            current = rows[i]
            direction = _classify_direction(current["net_income"], prior["net_income"])
            if direction is None:
                continue
            year_label = current["period_end_date"][:4]
            if direction == "up":
                ticker_up += 1
                per_year_up[year_label] += 1
            else:
                ticker_down += 1
                per_year_down[year_label] += 1
            pairs_data.append({
                "from_period": prior["period_end_date"],
                "to_period": current["period_end_date"],
                "prior_net_income": prior["net_income"],
                "current_net_income": current["net_income"],
                "direction": direction,
            })

        total_up += ticker_up
        total_down += ticker_down
        n_pairs = ticker_up + ticker_down
        ticker_stats[ticker] = {
            "up": ticker_up,
            "down": ticker_down,
            "pairs": n_pairs,
            "up_rate": round(ticker_up / n_pairs, 4) if n_pairs else None,
            "pairs_data": pairs_data,
        }

    total_pairs = total_up + total_down
    overall_up_rate = round(total_up / total_pairs, 4) if total_pairs else None

    # Build per-year summary
    all_years = sorted(set(list(per_year_up.keys()) + list(per_year_down.keys())))
    per_year_summary = {}
    for yr in all_years:
        up = per_year_up.get(yr, 0)
        down = per_year_down.get(yr, 0)
        n = up + down
        per_year_summary[yr] = {
            "up": up,
            "down": down,
            "pairs": n,
            "up_rate": round(up / n, 4) if n else None,
        }

    result = {
        "description": (
            "Empirical EGX annual net-income 'up' base rate computed from local income CSVs. "
            "Each pair is one consecutive year-over-year net_income comparison. "
            "Flat (equal) pairs excluded."
        ),
        "n_tickers": len(tickers),
        "total_pairs": total_pairs,
        "total_up": total_up,
        "total_down": total_down,
        "overall_up_rate": overall_up_rate,
        "hardcoded_claim": 0.76,
        "claim_verified": (
            abs((overall_up_rate or 0) - 0.76) < 0.05
            if overall_up_rate is not None else False
        ),
        "per_year": per_year_summary,
        "per_ticker": ticker_stats,
    }
    return result


if __name__ == "__main__":
    print(f"Scanning: {_INCOME_DIR}")
    data = compute_base_rate()
    with open(_OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Results saved to: {_OUTPUT_FILE}")
    rate = data["overall_up_rate"]
    claim = data["hardcoded_claim"]
    verified = data["claim_verified"]
    print(f"\nOverall up rate:    {rate:.1%} ({data['total_up']}/{data['total_pairs']} pairs)")
    print(f"Hardcoded claim:    {claim:.1%}")
    print(f"Claim verified:     {'YES (within 5pp)' if verified else 'NO (outside 5pp)'}")
