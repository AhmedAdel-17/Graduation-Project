"""
fetch_quarterly_pe_pb.py
========================
Enriches quarterly ratios CSVs with historical pe_ratio and pb_ratio
for every yfinance-filled quarter.

For each quarter-end date:
  - Fetches closing price from yfinance on or just before that date
  - pe_ratio  = price / (eps * 4)           [annualised quarterly EPS]
  - pb_ratio  = price / book_value_per_share
              = price / (total_equity / shares_outstanding)

Reads:
  key_ratios/{TICKER}_ratios_quarterly.csv   (has eps)
  balance_sheets/{TICKER}_balance_quarterly.csv  (has total_equity)

Writes back to the same quarterly ratios CSV, adding / overwriting
pe_ratio and pb_ratio for all yfinance rows with enough data.

Usage:
    python scripts/fetch_quarterly_pe_pb.py              # all tickers
    python scripts/fetch_quarterly_pe_pb.py --ticker ORAS
"""

from __future__ import annotations

import os
import sys
import csv
import logging
import argparse
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

DATA_CACHE  = os.path.join(PROJECT_ROOT, "tradingagents", "dataflows", "data_cache")
OUT_INCOME  = os.path.join(DATA_CACHE, "egx_fundamentals", "income_statements")
OUT_BALANCE = os.path.join(DATA_CACHE, "egx_fundamentals", "balance_sheets")
OUT_RATIOS  = os.path.join(DATA_CACHE, "egx_fundamentals", "key_ratios")

ALL_TICKERS = [
    "COMI", "EAST", "FWRY", "TMGH", "HRHO", "ETEL",
    "ABUK", "ADIB", "EFIH", "EGAL", "MFPC", "CCAP",
    "SKPC", "AMOC", "ESRS", "ORWE", "HELI", "GBCO",
    "SWDY", "ORAS", "PHDC", "CIEB", "ISPH", "DSCW",
    "RMDA", "ARCC", "BTFH", "JUFO", "ORHD", "RAYA", "VLMR",
]


def _safe(val) -> Optional[float]:
    try:
        if pd.isna(val):
            return None
        return float(val)
    except Exception:
        return None


def get_price_on_or_before(hist_df: pd.DataFrame, date_str: str) -> Optional[float]:
    """
    Return the closing price on date_str or the nearest prior trading day.
    Looks back up to 7 calendar days to handle weekends and EGX holidays.
    """
    target = datetime.strptime(date_str, "%Y-%m-%d").date()
    for delta in range(8):
        d = target - timedelta(days=delta)
        d_str = d.strftime("%Y-%m-%d")
        if d_str in hist_df.index.strftime("%Y-%m-%d"):
            idx = hist_df.index.strftime("%Y-%m-%d").tolist().index(d_str)
            return float(hist_df["Close"].iloc[idx])
    return None


def fetch_and_enrich(ticker: str):
    yf_sym = f"{ticker}.CA"
    log.info("[%s] Fetching price history and quarterly balance...", ticker)

    stock = yf.Ticker(yf_sym)

    # ── Price history (full range covers all quarters) ──────────────────────
    try:
        hist = stock.history(start="2022-01-01", end="2026-01-01")
    except Exception as e:
        log.warning("[%s] Price history error: %s", ticker, e)
        hist = pd.DataFrame()

    if hist.empty:
        log.warning("[%s] No price history — skipping.", ticker)
        return

    # ── Quarterly balance sheet for shares_outstanding per period ────────────
    shares_by_date: dict[str, float] = {}
    equity_by_date: dict[str, float] = {}
    try:
        q_bal = stock.quarterly_balance_sheet
        for col in q_bal.columns:
            d = col.strftime("%Y-%m-%d")
            shares = _safe(q_bal[col].get("Ordinary Shares Number") or q_bal[col].get("Share Issued"))
            equity = _safe(q_bal[col].get("Total Equity Gross Minority Interest") or
                           q_bal[col].get("Stockholders Equity") or
                           q_bal[col].get("Common Stock Equity"))
            if shares:
                shares_by_date[d] = shares
            if equity:
                equity_by_date[d] = equity
    except Exception as e:
        log.warning("[%s] Quarterly balance sheet error: %s", ticker, e)

    # Fallback: use info dict sharesOutstanding for all periods
    fallback_shares: Optional[float] = None
    try:
        fallback_shares = _safe(stock.info.get("sharesOutstanding"))
    except Exception:
        pass

    # ── Read existing quarterly ratios CSV ───────────────────────────────────
    ratios_path = os.path.join(OUT_RATIOS, f"{ticker}_ratios_quarterly.csv")
    if not os.path.exists(ratios_path):
        log.warning("[%s] No quarterly ratios file found — skipping.", ticker)
        return

    with open(ratios_path, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    # Add pb_ratio column if missing
    if "pb_ratio" not in fieldnames:
        fieldnames = list(fieldnames)
        # Insert after pe_ratio
        pe_idx = fieldnames.index("pe_ratio") if "pe_ratio" in fieldnames else 0
        fieldnames.insert(pe_idx + 1, "pb_ratio")

    # ── Enrich each yfinance row ─────────────────────────────────────────────
    pe_filled = pb_filled = 0

    for row in rows:
        if row.get("data_source") != "yfinance":
            row.setdefault("pb_ratio", "")
            continue

        date_str = row["period_end_date"]

        # Closing price at quarter-end
        price = get_price_on_or_before(hist, date_str)

        # EPS (quarterly) — annualise × 4
        eps_q = None
        raw_eps = row.get("eps", "")
        if raw_eps and raw_eps not in ("", "None", "nan"):
            try:
                eps_q = float(raw_eps)
            except ValueError:
                pass

        # Shares outstanding — use period-specific then fallback
        shares = shares_by_date.get(date_str) or fallback_shares

        # Equity — use period-specific from balance dict
        equity = equity_by_date.get(date_str)

        # ── pe_ratio ─────────────────────────────────────────────────────────
        if price and eps_q and eps_q > 0:
            eps_annual = eps_q * 4
            row["pe_ratio"] = round(price / eps_annual, 4)
            pe_filled += 1
        else:
            # Keep existing snapshot pe_ratio if already set
            if not row.get("pe_ratio", "").strip():
                row["pe_ratio"] = ""

        # ── pb_ratio ─────────────────────────────────────────────────────────
        bvps: Optional[float] = None
        if equity and shares and shares > 0:
            bvps = equity / shares
        if price and bvps and bvps > 0:
            row["pb_ratio"] = round(price / bvps, 4)
            pb_filled += 1
        else:
            row.setdefault("pb_ratio", "")

    # ── Write back ───────────────────────────────────────────────────────────
    with open(ratios_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    log.info("[%s] Done — pe_ratio filled: %d, pb_ratio filled: %d", ticker, pe_filled, pb_filled)


def main():
    parser = argparse.ArgumentParser(
        description="Enrich quarterly ratios CSVs with historical pe_ratio and pb_ratio."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ticker", type=str, help="Single EGX ticker (e.g. ORAS)")
    group.add_argument("--all", action="store_true", default=True, help="All 31 tickers (default)")
    args = parser.parse_args()

    tickers = [args.ticker.upper()] if args.ticker else ALL_TICKERS

    log.info("Enriching quarterly pe_ratio + pb_ratio for %d tickers...", len(tickers))
    ok = failed = 0
    for t in tickers:
        try:
            fetch_and_enrich(t)
            ok += 1
        except Exception as e:
            log.error("[%s] FAILED: %s", t, e)
            failed += 1

    print(f"\n{'='*50}")
    print(f"  Done: {ok} OK, {failed} failed")
    print(f"  Files: {OUT_RATIOS}/{{TICKER}}_ratios_quarterly.csv")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
