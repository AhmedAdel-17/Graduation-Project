"""
egx_quarterly_scraper.py
========================
Builds quarterly fundamental CSV files for all 31 EGX tickers.

Two-phase approach:
  Phase 1 — yfinance: fills the last 6 quarters (Q3-2024 → Q4-2025) with real data.
  Phase 2 — templates: inserts EMPTY rows for Q1-2022 → Q2-2024 so the manual
             Mubasher entries have the correct structure ready to fill in.

Output files (same directories as annual files):
    data_cache/egx_fundamentals/income_statements/{TICKER}_income_quarterly.csv
    data_cache/egx_fundamentals/balance_sheets/{TICKER}_balance_quarterly.csv
    data_cache/egx_fundamentals/key_ratios/{TICKER}_ratios_quarterly.csv

NOTE: local.py reads:
    income  → {TICKER}_income_{freq}.csv   (freq = "quarterly")
    balance → {TICKER}_balance_{freq}.csv  (freq = "quarterly")
    ratios  → {TICKER}_ratios.csv          (no freq suffix — shared with annual)

So quarterly ratios are written to {TICKER}_ratios_quarterly.csv and NOT used
by local.py directly — they are reference files for manual QA only.
The annual ratios file is kept as-is (most recent snapshot values).

Usage:
    python scripts/egx_quarterly_scraper.py            # all 31 tickers
    python scripts/egx_quarterly_scraper.py --ticker COMI
    python scripts/egx_quarterly_scraper.py --all --force   # overwrite existing
"""

import os
import sys
import logging
import argparse
from datetime import datetime, date
from typing import Optional

import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
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

for d in [OUT_INCOME, OUT_BALANCE, OUT_RATIOS]:
    os.makedirs(d, exist_ok=True)

# ---------------------------------------------------------------------------
# Ticker list
# ---------------------------------------------------------------------------
ALL_TICKERS = [
    "COMI", "EAST", "FWRY", "TMGH", "HRHO", "ETEL",
    "ABUK", "ADIB", "EFIH", "EGAL", "MFPC", "CCAP",
    "SKPC", "AMOC", "ESRS", "ORWE", "HELI", "GBCO",
    "SWDY", "ORAS", "PHDC", "CIEB", "ISPH", "DSCW",
    "RMDA", "ARCC", "BTFH", "JUFO", "ORHD", "RAYA", "VLMR",
]

BANK_TICKERS = {"COMI", "ADIB", "CIEB", "ARCC", "BTFH", "ABUK", "EGAL"}

# ---------------------------------------------------------------------------
# All quarters we want: Q1-2022 → Q4-2025
# Format: period_end_date strings matching company fiscal calendars
# We generate calendar-year quarters; fiscal-year variants handled below.
# ---------------------------------------------------------------------------

def _all_quarter_ends() -> list[str]:
    """
    Return period_end_date strings for every quarter Q1-2022 → Q4-2025.
    Uses calendar year-end convention: Mar-31, Jun-30, Sep-30, Dec-31.
    """
    quarters = []
    for year in range(2022, 2026):
        quarters += [
            f"{year}-03-31",
            f"{year}-06-30",
            f"{year}-09-30",
            f"{year}-12-31",
        ]
    return quarters


# ---------------------------------------------------------------------------
# yfinance fetch
# ---------------------------------------------------------------------------

def _safe(val) -> Optional[float]:
    """Return float or None — no NaN in output."""
    try:
        if pd.isna(val):
            return None
        return float(val)
    except Exception:
        return None


def fetch_quarterly_yfinance(ticker: str) -> dict:
    """
    Pull quarterly financials and balance sheet from yfinance.
    Returns dict with keys: income (list of dicts), balance (list of dicts), info (dict).
    """
    yf_sym = f"{ticker}.CA"
    log.info("[%s] Fetching quarterly data from yfinance (%s)...", ticker, yf_sym)
    stock = yf.Ticker(yf_sym)

    # Income
    inc_records = []
    try:
        q_fin = stock.quarterly_financials
        if not q_fin.empty:
            for col in q_fin.columns:
                row  = q_fin[col]
                date_str = col.strftime("%Y-%m-%d")
                rec = {
                    "period_end_date":    date_str,
                    "revenue":            _safe(row.get("Total Revenue") or row.get("Operating Revenue") or row.get("Net Interest Income")),
                    "net_income":         _safe(row.get("Net Income") or row.get("Net Income Common Stockholders")),
                    "gross_profit":       _safe(row.get("Gross Profit")),
                    "operating_income":   _safe(row.get("Operating Income")),
                    "operating_expenses": _safe(row.get("Operating Expense")),
                    "eps_basic":          _safe(row.get("Basic EPS")),
                    "interest_income":    _safe(row.get("Interest Income")),
                    "interest_expense":   _safe(row.get("Interest Expense")),
                    "data_source":        "yfinance",
                }
                inc_records.append(rec)
    except Exception as e:
        log.warning("[%s] Quarterly income error: %s", ticker, e)

    # Balance sheet
    bal_records = []
    try:
        q_bal = stock.quarterly_balance_sheet
        if not q_bal.empty:
            for col in q_bal.columns:
                row      = q_bal[col]
                date_str = col.strftime("%Y-%m-%d")
                tl = _safe(row.get("Total Liabilities Net Minority Interest")) or _safe(row.get("Total Debt"))
                te = _safe(row.get("Total Equity Gross Minority Interest")) or _safe(row.get("Stockholders Equity")) or _safe(row.get("Common Stock Equity"))
                rec = {
                    "period_end_date":     date_str,
                    "total_assets":        _safe(row.get("Total Assets")),
                    "total_liabilities":   tl,
                    "total_equity":        te,
                    "cash_and_equivalents": _safe(row.get("Cash And Cash Equivalents") or row.get("Cash Cash Equivalents And Federal Funds Sold")),
                    "customer_deposits":   None,
                    "data_source":         "yfinance",
                }
                bal_records.append(rec)
    except Exception as e:
        log.warning("[%s] Quarterly balance error: %s", ticker, e)

    # Info snapshot
    info = {}
    try:
        info = stock.info
    except Exception as e:
        log.warning("[%s] Info error: %s", ticker, e)

    return {"income": inc_records, "balance": bal_records, "info": info}


# ---------------------------------------------------------------------------
# Build full template: merge yfinance data + empty rows for missing quarters
# ---------------------------------------------------------------------------

INCOME_COLS = [
    "period_end_date", "revenue", "net_income", "gross_profit",
    "operating_income", "operating_expenses", "eps_basic",
    "interest_income", "interest_expense", "data_source",
]

BALANCE_COLS = [
    "period_end_date", "total_assets", "total_liabilities",
    "total_equity", "cash_and_equivalents", "customer_deposits", "data_source",
]

RATIOS_COLS = [
    "period_end_date", "pe_ratio", "eps", "net_margin", "roe", "roa",
    "debt_to_equity", "current_ratio", "gross_margin", "operating_margin",
    "dividend_yield", "market_cap", "car_ratio", "data_source",
]


def _empty_income_row(date_str: str) -> dict:
    return {c: ("" if c not in ("period_end_date", "data_source") else
                (date_str if c == "period_end_date" else "MANUAL_ENTRY_REQUIRED"))
            for c in INCOME_COLS}


def _empty_balance_row(date_str: str) -> dict:
    return {c: ("" if c not in ("period_end_date", "data_source") else
                (date_str if c == "period_end_date" else "MANUAL_ENTRY_REQUIRED"))
            for c in BALANCE_COLS}


def _empty_ratios_row(date_str: str) -> dict:
    return {c: ("" if c not in ("period_end_date", "data_source") else
                (date_str if c == "period_end_date" else "MANUAL_ENTRY_REQUIRED"))
            for c in RATIOS_COLS}


def _derive_ratios(income_row: dict, balance_row: dict, info: dict, is_first: bool) -> dict:
    """Derive ratio row from income + balance data."""
    def fv(d, k):
        v = d.get(k)
        return float(v) if v not in (None, "", "MANUAL_ENTRY_REQUIRED") else None

    net_income   = fv(income_row, "net_income")
    revenue      = fv(income_row, "revenue")
    total_liab   = fv(balance_row, "total_liabilities")
    total_equity = fv(balance_row, "total_equity")
    total_assets = fv(balance_row, "total_assets")

    net_margin     = round(net_income / revenue, 4)       if net_income and revenue and revenue != 0   else None
    roe            = round(net_income / total_equity, 4)  if net_income and total_equity and total_equity != 0 else None
    roa            = round(net_income / total_assets, 4)  if net_income and total_assets and total_assets != 0 else None
    debt_to_equity = round(total_liab / total_equity, 2)  if total_liab and total_equity and total_equity != 0 else None

    rec = {
        "period_end_date": income_row["period_end_date"],
        "pe_ratio":        info.get("trailingPE") if is_first else None,
        "eps":             fv(income_row, "eps_basic"),
        "net_margin":      net_margin,
        "roe":             roe,
        "roa":             roa,
        "debt_to_equity":  debt_to_equity,
        "current_ratio":   None,
        "gross_margin":    None,
        "operating_margin": None,
        "dividend_yield":  info.get("dividendYield") if is_first else None,
        "market_cap":      info.get("marketCap") if is_first else None,
        "car_ratio":       None,
        "data_source":     income_row.get("data_source", "yfinance"),
    }
    return rec


def build_and_save(ticker: str, yf_data: dict, force: bool = False):
    """
    Merge yfinance quarterly data with empty template rows for missing quarters.
    Saves income_quarterly.csv, balance_quarterly.csv, ratios_quarterly.csv.
    """
    inc_path    = os.path.join(OUT_INCOME,  f"{ticker}_income_quarterly.csv")
    bal_path    = os.path.join(OUT_BALANCE, f"{ticker}_balance_quarterly.csv")
    ratios_path = os.path.join(OUT_RATIOS,  f"{ticker}_ratios_quarterly.csv")

    if not force and all(os.path.exists(p) for p in [inc_path, bal_path, ratios_path]):
        log.info("[%s] Quarterly files already exist — skipping (use --force to overwrite).", ticker)
        return

    all_quarters = _all_quarter_ends()  # Q1-2022 → Q4-2025

    # Index yfinance data by period_end_date
    yf_inc = {r["period_end_date"]: r for r in yf_data["income"]}
    yf_bal = {r["period_end_date"]: r for r in yf_data["balance"]}
    info   = yf_data["info"]

    inc_rows    = []
    bal_rows    = []
    ratios_rows = []

    for i, q_date in enumerate(all_quarters):
        # --- Income ---
        if q_date in yf_inc:
            inc_row = yf_inc[q_date]
        else:
            inc_row = _empty_income_row(q_date)
        inc_rows.append(inc_row)

        # --- Balance ---
        if q_date in yf_bal:
            bal_row = yf_bal[q_date]
        else:
            bal_row = _empty_balance_row(q_date)
        bal_rows.append(bal_row)

        # --- Ratios ---
        if q_date in yf_inc and q_date in yf_bal:
            ratios_row = _derive_ratios(inc_row, bal_row, info, is_first=(i == len(all_quarters) - 1))
        else:
            ratios_row = _empty_ratios_row(q_date)
        ratios_rows.append(ratios_row)

    # Sort oldest → newest so manual entry reads top-to-bottom chronologically
    inc_rows    = sorted(inc_rows,    key=lambda r: r["period_end_date"])
    bal_rows    = sorted(bal_rows,    key=lambda r: r["period_end_date"])
    ratios_rows = sorted(ratios_rows, key=lambda r: r["period_end_date"])

    pd.DataFrame(inc_rows)[INCOME_COLS].to_csv(inc_path,    index=False)
    pd.DataFrame(bal_rows)[BALANCE_COLS].to_csv(bal_path,   index=False)
    pd.DataFrame(ratios_rows)[RATIOS_COLS].to_csv(ratios_path, index=False)

    # Count filled vs empty
    filled_inc = sum(1 for r in inc_rows if r.get("data_source") == "yfinance")
    filled_bal = sum(1 for r in bal_rows if r.get("data_source") == "yfinance")
    empty_inc  = len(inc_rows) - filled_inc

    log.info(
        "[%s] Saved quarterly files — %d quarters total: %d filled by yfinance, %d need manual entry",
        ticker, len(all_quarters), filled_inc, empty_inc,
    )
    log.info("  Income:  %s", inc_path)
    log.info("  Balance: %s", bal_path)
    log.info("  Ratios:  %s", ratios_path)


# ---------------------------------------------------------------------------
# Print Mubasher manual-entry guide
# ---------------------------------------------------------------------------

def print_manual_entry_guide(ticker: str, missing_quarters: list[str]):
    """Print the exact Mubasher URL and column mapping for manual entry."""
    print(f"""
{'='*65}
  MANUAL ENTRY GUIDE — {ticker}
{'='*65}
  Open this URL in your browser:
  https://english.mubasher.info/markets/EGX/stocks/{ticker}/financials/

  Click "Quarterly" tab, then fill in these rows in the CSV files:
  Missing quarters: {missing_quarters}

  INCOME CSV columns to fill:
    revenue            → "Revenue" or "Net Interest Income" (banks)
    net_income         → "Net Income" / "Net Profit"
    gross_profit       → "Gross Profit" (leave blank for banks)
    operating_income   → "Operating Income" / "Operating Profit"
    operating_expenses → "Operating Expenses"
    eps_basic          → "EPS" / "Earnings Per Share"
    interest_income    → "Interest Income" (banks only)
    interest_expense   → "Interest Expense"

  BALANCE CSV columns to fill:
    total_assets       → "Total Assets"
    total_liabilities  → "Total Liabilities"
    total_equity       → "Total Equity" / "Shareholders Equity"
    cash_and_equivalents → "Cash & Cash Equivalents"
    customer_deposits  → "Customer Deposits" (banks only)

  After filling, change data_source from MANUAL_ENTRY_REQUIRED → mubasher
  All values should be in EGP (Egyptian Pounds), raw numbers (no commas).
{'='*65}""")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_ticker(ticker: str, force: bool = False):
    ticker = ticker.upper().replace(".CA", "").strip()
    log.info("\n%s\n  Processing: %s\n%s", "="*60, ticker, "="*60)

    yf_data = fetch_quarterly_yfinance(ticker)
    build_and_save(ticker, yf_data, force=force)

    # Show which quarters still need manual entry
    inc_path = os.path.join(OUT_INCOME, f"{ticker}_income_quarterly.csv")
    try:
        df = pd.read_csv(inc_path)
        missing = df[df["data_source"] == "MANUAL_ENTRY_REQUIRED"]["period_end_date"].tolist()
        if missing:
            print_manual_entry_guide(ticker, missing)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(
        description="Build quarterly fundamental CSV files for all 31 EGX tickers."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ticker", type=str, help="Single EGX ticker (e.g. COMI)")
    group.add_argument("--all",    action="store_true", default=True,
                       help="Process all 31 tickers (default)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing quarterly CSV files")
    parser.add_argument("--guide-only", action="store_true",
                        help="Just print the manual entry guide for a ticker")
    args = parser.parse_args()

    tickers = [args.ticker.upper()] if args.ticker else ALL_TICKERS

    log.info("="*60)
    log.info("  EGX QUARTERLY FUNDAMENTAL BUILDER")
    log.info("  Tickers: %d | Force: %s", len(tickers), args.force)
    log.info("="*60)

    results = {}
    for i, ticker in enumerate(tickers, 1):
        log.info("\n[%d/%d]", i, len(tickers))
        try:
            process_ticker(ticker, force=args.force)
            results[ticker] = "OK"
        except Exception as e:
            log.error("[%s] FAILED: %s", ticker, e)
            results[ticker] = f"FAILED: {e}"

    # Final summary
    print("\n" + "="*60)
    print("  SUMMARY")
    print("="*60)

    filled_total  = 0
    missing_total = 0

    for ticker, status in results.items():
        inc_path = os.path.join(OUT_INCOME, f"{ticker}_income_quarterly.csv")
        try:
            df      = pd.read_csv(inc_path)
            filled  = len(df[df["data_source"] == "yfinance"])
            missing = len(df[df["data_source"] == "MANUAL_ENTRY_REQUIRED"])
            filled_total  += filled
            missing_total += missing
            bar = "#" * filled + "." * missing
            print(f"  {ticker:6s} [{bar}]  {filled} yfinance / {missing} need manual entry")
        except Exception:
            print(f"  {ticker:6s}  {status}")

    print(f"\n  Total quarters filled by yfinance : {filled_total}")
    print(f"  Total quarters needing manual entry: {missing_total}")
    print(f"\n  Files are in: {DATA_CACHE}/egx_fundamentals/")
    print("="*60)
    print()
    print("  NEXT STEP — Manual entry:")
    print("  For each ticker, open the file and fill in rows where")
    print("  data_source = MANUAL_ENTRY_REQUIRED using Mubasher:")
    print("  https://english.mubasher.info/markets/EGX/stocks/{TICKER}/financials/")
    print()
    print("  Use the format: raw EGP numbers, no commas, no currency symbols.")
    print("  After filling a row, set data_source = mubasher")
    print("="*60)


if __name__ == "__main__":
    main()
