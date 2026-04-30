"""
egx30_full_scraper.py
=====================
Fetches financial statements for all EGX30 tickers using Yahoo Finance (yfinance).
Writes the three CSV files expected by tradingagents/dataflows/local.py:

    data_cache/egx_fundamentals/income_statements/{TICKER}_income_annual.csv
    data_cache/egx_fundamentals/balance_sheets/{TICKER}_balance_annual.csv
    data_cache/egx_fundamentals/key_ratios/{TICKER}_ratios.csv

Usage:
    # Single ticker (fast test):
    python scripts/egx30_full_scraper.py --ticker COMI

    # All 31 tickers:
    python scripts/egx30_full_scraper.py --all

    # Skip tickers that already have data:
    python scripts/egx30_full_scraper.py --all --skip-existing
"""

import os
import sys
import logging
import argparse
from datetime import datetime

import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Path setup — must be done before any tradingagents imports
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ticker lists
# ---------------------------------------------------------------------------
ALL_TICKERS = [
    "COMI", "EAST", "FWRY", "TMGH", "HRHO", "ETEL",
    "ABUK", "ADIB", "EFIH", "EGAL", "MFPC", "CCAP",
    "SKPC", "AMOC", "ESRS", "ORWE", "HELI", "GBCO",
    "SWDY", "ORAS", "PHDC", "CIEB", "ISPH", "DSCW",
    "RMDA", "ARCC", "BTFH", "JUFO", "ORHD", "RAYA", "VLMR",
]

# Banking-sector tickers — use CAR instead of current_ratio
BANK_TICKERS = {"COMI", "ADIB", "CIEB", "ARCC", "BTFH", "ABUK", "EGAL"}

# ---------------------------------------------------------------------------
# Output directories — resolved relative to project root so the script
# works regardless of which directory it is run from
# ---------------------------------------------------------------------------
DATA_CACHE = os.path.join(PROJECT_ROOT, "tradingagents", "dataflows", "data_cache")
OUT_INCOME  = os.path.join(DATA_CACHE, "egx_fundamentals", "income_statements")
OUT_BALANCE = os.path.join(DATA_CACHE, "egx_fundamentals", "balance_sheets")
OUT_RATIOS  = os.path.join(DATA_CACHE, "egx_fundamentals", "key_ratios")

for _d in [OUT_INCOME, OUT_BALANCE, OUT_RATIOS]:
    os.makedirs(_d, exist_ok=True)


# ---------------------------------------------------------------------------
# Core fetching logic
# ---------------------------------------------------------------------------

def fetch_ticker_data(ticker: str) -> dict:
    """Fetch financials, balance sheet, and info from yfinance for an EGX ticker."""
    yf_ticker = f"{ticker}.CA"
    log.info(f"[{ticker}] Fetching data from Yahoo Finance ({yf_ticker})...")

    stock = yf.Ticker(yf_ticker)

    # Income Statement
    try:
        inc_df = stock.financials.T
        if inc_df.empty:
            log.warning(f"[{ticker}] No income statement data found.")
    except Exception as e:
        log.warning(f"[{ticker}] Error fetching income statement: {e}")
        inc_df = pd.DataFrame()

    # Balance Sheet
    try:
        bal_df = stock.balance_sheet.T
        if bal_df.empty:
            log.warning(f"[{ticker}] No balance sheet data found.")
    except Exception as e:
        log.warning(f"[{ticker}] Error fetching balance sheet: {e}")
        bal_df = pd.DataFrame()

    # Info dict (current snapshot ratios)
    try:
        info = stock.info
    except Exception as e:
        log.warning(f"[{ticker}] Error fetching info: {e}")
        info = {}

    # ---- Process Income Statement ----
    inc_records = []
    if not inc_df.empty:
        inc_df.index = inc_df.index.strftime('%Y-%m-%d')
        for date, row in inc_df.iterrows():
            rec = {
                "period_end_date":    date,
                "revenue":            row.get("Total Revenue") or row.get("Operating Revenue") or row.get("Net Interest Income"),
                "net_income":         row.get("Net Income") or row.get("Net Income Common Stockholders"),
                "gross_profit":       row.get("Gross Profit"),
                "operating_income":   row.get("Operating Income"),
                "operating_expenses": row.get("Operating Expense"),
                "eps_basic":          row.get("Basic EPS"),
                "interest_income":    row.get("Interest Income"),
                "interest_expense":   row.get("Interest Expense"),
            }
            # Replace NaN with None for clean CSV output
            rec = {k: (v if pd.notna(v) else None) for k, v in rec.items()}
            inc_records.append(rec)

    # ---- Process Balance Sheet ----
    bal_records = []
    if not bal_df.empty:
        bal_df.index = bal_df.index.strftime('%Y-%m-%d')
        for date, row in bal_df.iterrows():
            tl = row.get("Total Liabilities Net Minority Interest")
            if pd.isna(tl):
                tl = row.get("Total Debt")
            te = row.get("Total Equity Gross Minority Interest")
            if pd.isna(te):
                te = row.get("Stockholders Equity") or row.get("Common Stock Equity")

            rec = {
                "period_end_date":     date,
                "total_assets":        row.get("Total Assets"),
                "total_liabilities":   tl,
                "total_equity":        te,
                "cash_and_equivalents": (
                    row.get("Cash And Cash Equivalents")
                    or row.get("Cash Cash Equivalents And Federal Funds Sold")
                ),
                "customer_deposits":   None,  # Not available in standard yfinance
            }
            rec = {k: (v if pd.notna(v) else None) for k, v in rec.items()}
            bal_records.append(rec)

    return {"income": inc_records, "balance": bal_records, "info": info}


# ---------------------------------------------------------------------------
# Save CSVs
# ---------------------------------------------------------------------------

def save_csvs(ticker: str, data: dict) -> dict:
    """
    Compute derived ratios from income + balance data, then write 3 CSV files.
    Returns a dict with row counts for each file.
    """
    inc_records = data["income"]
    bal_records = data["balance"]
    info        = data["info"]

    # ---- Income CSV ----
    inc_path = os.path.join(OUT_INCOME, f"{ticker}_income_annual.csv")
    if inc_records:
        pd.DataFrame(inc_records).to_csv(inc_path, index=False)
        log.info(f"[{ticker}] Saved income  → {inc_path} ({len(inc_records)} rows)")
    else:
        pd.DataFrame(columns=["period_end_date", "revenue", "net_income"]).to_csv(inc_path, index=False)
        log.warning(f"[{ticker}] No income data — wrote empty CSV")

    # ---- Balance CSV ----
    bal_path = os.path.join(OUT_BALANCE, f"{ticker}_balance_annual.csv")
    if bal_records:
        pd.DataFrame(bal_records).to_csv(bal_path, index=False)
        log.info(f"[{ticker}] Saved balance → {bal_path} ({len(bal_records)} rows)")
    else:
        pd.DataFrame(columns=["period_end_date", "total_assets", "total_liabilities", "total_equity"]).to_csv(bal_path, index=False)
        log.warning(f"[{ticker}] No balance data — wrote empty CSV")

    # ---- Ratios CSV — join income + balance, then derive metrics ----
    inc_df = pd.DataFrame(inc_records).set_index("period_end_date") if inc_records else pd.DataFrame()
    bal_df = pd.DataFrame(bal_records).set_index("period_end_date") if bal_records else pd.DataFrame()

    if not inc_df.empty and not bal_df.empty:
        joined = inc_df.join(bal_df, how="outer", rsuffix="_bal")
    elif not inc_df.empty:
        joined = inc_df
    elif not bal_df.empty:
        joined = bal_df
    else:
        joined = pd.DataFrame()

    # Snapshot data from info dict (most recent values)
    current_pe   = info.get("trailingPE")   or info.get("forwardPE")
    current_eps  = info.get("trailingEps")  or info.get("forwardEps")
    current_mcap = info.get("marketCap")
    current_div  = info.get("dividendYield")

    ratio_rows = []
    for i, (date_str, row) in enumerate(joined.iterrows()):
        net_income   = row.get("net_income")   if pd.notna(row.get("net_income"))   else None
        revenue      = row.get("revenue")      if pd.notna(row.get("revenue"))      else None
        total_liab   = row.get("total_liabilities") if pd.notna(row.get("total_liabilities")) else None
        total_equity = row.get("total_equity") if pd.notna(row.get("total_equity")) else None
        total_assets = row.get("total_assets") if pd.notna(row.get("total_assets")) else None

        net_margin    = round(net_income / revenue, 4)       if net_income and revenue and revenue != 0    else None
        roe           = round(net_income / total_equity, 4)  if net_income and total_equity and total_equity != 0 else None
        roa           = round(net_income / total_assets, 4)  if net_income and total_assets and total_assets != 0 else None
        debt_to_equity = round(total_liab / total_equity, 2) if total_liab and total_equity and total_equity != 0 else None

        ratiorec = {
            "period_end_date": date_str,
            "net_margin":      net_margin,
            "roe":             roe,
            "roa":             roa,
            "debt_to_equity":  debt_to_equity,
        }

        # Attach snapshot metrics only to the most recent (first) row
        if i == 0:
            eps_val = row.get("eps_basic") if pd.notna(row.get("eps_basic")) else current_eps
            ratiorec["eps"]            = eps_val
            ratiorec["pe_ratio"]       = current_pe
            ratiorec["market_cap"]     = current_mcap
            ratiorec["dividend_yield"] = current_div
            # CAR must come from official CBE reports — left blank for manual entry
            ratiorec["car_ratio"]      = None
        else:
            ratiorec["eps"]            = row.get("eps_basic") if pd.notna(row.get("eps_basic")) else None
            ratiorec["pe_ratio"]       = None
            ratiorec["market_cap"]     = None
            ratiorec["dividend_yield"] = None
            ratiorec["car_ratio"]      = None

        ratio_rows.append(ratiorec)

    rat_path = os.path.join(OUT_RATIOS, f"{ticker}_ratios.csv")
    if ratio_rows:
        pd.DataFrame(ratio_rows).to_csv(rat_path, index=False)
        log.info(f"[{ticker}] Saved ratios  → {rat_path} ({len(ratio_rows)} rows)")
    else:
        pd.DataFrame(columns=["period_end_date", "pe_ratio", "eps", "debt_to_equity"]).to_csv(rat_path, index=False)
        log.warning(f"[{ticker}] No ratio data — wrote empty CSV")

    return {
        "income_rows":  len(inc_records),
        "balance_rows": len(bal_records),
        "ratio_rows":   len(ratio_rows),
    }


# ---------------------------------------------------------------------------
# Per-ticker pipeline
# ---------------------------------------------------------------------------

def already_has_data(ticker: str) -> bool:
    """Return True if all three CSV files already exist and are non-empty."""
    paths = [
        os.path.join(OUT_INCOME,  f"{ticker}_income_annual.csv"),
        os.path.join(OUT_BALANCE, f"{ticker}_balance_annual.csv"),
        os.path.join(OUT_RATIOS,  f"{ticker}_ratios.csv"),
    ]
    for p in paths:
        if not os.path.exists(p):
            return False
        try:
            df = pd.read_csv(p)
            if df.empty:
                return False
        except Exception:
            return False
    return True


def process_ticker(ticker: str, skip_existing: bool = False) -> bool:
    """Run the full scrape + save pipeline for one ticker. Returns True on success."""
    ticker = ticker.upper().replace(".CA", "").strip()
    log.info(f"\n{'='*60}\n  Processing: {ticker}\n{'='*60}")

    if skip_existing and already_has_data(ticker):
        log.info(f"[{ticker}] Skipping — data already exists.")
        return True

    try:
        data  = fetch_ticker_data(ticker)
        stats = save_csvs(ticker, data)
        log.info(f"[{ticker}] Done → {stats}")
        return True
    except Exception as e:
        log.error(f"[{ticker}] FAILED: {e}", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fetch EGX30 fundamental data using Yahoo Finance and write CSV files."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ticker", type=str, help="Single EGX ticker (e.g. COMI)")
    group.add_argument("--all",    action="store_true", help="Fetch all 31 EGX tickers")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=False,
        help="Skip tickers that already have non-empty CSV files (faster re-runs)",
    )
    args = parser.parse_args()

    tickers = ALL_TICKERS if args.all else [args.ticker]

    log.info(f"Target tickers  : {tickers}")
    log.info(f"Output directory: {DATA_CACHE}/egx_fundamentals/")
    log.info(f"Skip existing   : {args.skip_existing}")

    results = {}
    for ticker in tickers:
        ok = process_ticker(ticker, skip_existing=args.skip_existing)
        results[ticker] = "OK" if ok else "FAILED"

    # Summary
    print("\n" + "=" * 50)
    print("  FETCH SUMMARY")
    print("=" * 50)
    ok_count = sum(1 for v in results.values() if v == "OK")
    for t, status in results.items():
        icon = "+" if status == "OK" else "X"
        print(f"  [{icon}] {t}: {status}")
    print(f"\n  {ok_count}/{len(tickers)} succeeded.")
    print(f"  Files saved to: {DATA_CACHE}/egx_fundamentals/")
    print("=" * 50)


if __name__ == "__main__":
    main()
