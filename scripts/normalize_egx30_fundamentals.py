"""Normalize the wide Investing.com fundamentals dump into the analyst's cache layout.

`scripts/fetch_egx_fundamentals.py` writes one **wide** CSV per ticker under
`data/egx30_fundamentals/<TICKER>.CA_{annual,quarterly}.csv` (raw Investing.com
`is_`/`bs_`/`cf_` columns, values in **millions** of EGP). The Fundamental Analyst,
however, reads a **normalized** 3-folder layout under
`tradingagents/dataflows/data_cache/egx_fundamentals/` produced historically by the
yfinance-based `egx30_full_scraper.py`:

    income_statements/<TICKER>_income_{annual,quarterly}.csv   (revenue, net_income, ...)
    balance_sheets/<TICKER>_balance_{annual,quarterly}.csv     (total_assets, ...)
    key_ratios/<TICKER>_ratios{,_quarterly}.csv                (roe, roa, net_margin, ...)

This converter regenerates that normalized layout **from the richer Investing.com
dump** so the analyst depends on a single, more complete source:

  * +13 tickers the yfinance cache never had (HDBK, QNBA, SAUD, DOMT, EFID, OCDI, ...).
  * +1 fiscal year of history (2020) per ticker.
  * Identical normalized schema → the tested loader / calculator / pipeline are
    UNCHANGED (zero regression risk).

Per the product decision, Investing.com is the PRIMARY source; the only fields it
cannot supply are the price-derived ratios (`pe_ratio`, `market_cap`,
`dividend_yield`) — for those we FALL BACK to whatever the existing yfinance cache
already had (sparse, but better than nothing). `car_ratio` stays blank (bank CAR is
not in either source). Everything else (`roe`, `roa`, `net_margin`, `debt_to_equity`,
`current_ratio`, `eps`) is recomputed from the raw statements exactly as
`egx30_full_scraper.save_csvs` did.

Tickers present only in the old cache (DSCW, SKPC — not in the Investing.com dump)
are LEFT UNTOUCHED.

Usage
-----
    python scripts/normalize_egx30_fundamentals.py                 # all dump tickers
    python scripts/normalize_egx30_fundamentals.py --tickers COMI.CA,QNBA.CA
    python scripts/normalize_egx30_fundamentals.py --dry-run       # report, write nothing
"""

from __future__ import annotations

import argparse
import glob
import logging
import math
import os
from typing import Dict, List, Optional

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("tradingagents.normalize_egx30_fundamentals")

# Investing.com monetary values are in MILLIONS of EGP; the normalized cache stores
# absolute units (verified: data_cache net_income 29,634,542,000 == egx30 29634.542 * 1e6).
MONEY_SCALE = 1_000_000.0

SRC_DIR = os.path.join("data", "egx30_fundamentals")
DST_ROOT = os.path.join(
    "tradingagents", "dataflows", "data_cache", "egx_fundamentals"
)
DST_INCOME = os.path.join(DST_ROOT, "income_statements")
DST_BALANCE = os.path.join(DST_ROOT, "balance_sheets")
DST_RATIOS = os.path.join(DST_ROOT, "key_ratios")

# A ticker is a "bank-template" filing when the income statement carries the
# net-interest-income line (banks do not report revenue/gross profit/operating income).
BANK_MARKER = "is_net_interest_income_bank_template"


def _first(row: pd.Series, *candidates: str) -> Optional[float]:
    """Return the first candidate column present with a finite value, else None."""
    for col in candidates:
        if col in row.index:
            val = row[col]
            if val is not None and not (isinstance(val, float) and not math.isfinite(val)):
                try:
                    f = float(val)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(f):
                    return f
    return None


def _money(val: Optional[float]) -> Optional[float]:
    """Scale an Investing.com millions figure to absolute EGP (None passes through)."""
    return None if val is None else val * MONEY_SCALE


def _build_income(row: pd.Series, is_bank: bool) -> Dict[str, Optional[float]]:
    """Map one wide period row to the normalized income-statement record."""
    if is_bank:
        revenue = _first(row, "is_net_interest_income_bank_template")
        operating_expenses = _first(row, "is_non_interest_expense_total_bank_template")
        interest_income = _first(row, "is_interest_income_total_bank_template")
        interest_expense = _first(row, "is_interest_expense_total_bank_template")
        gross_profit = None
        operating_income = None
    else:
        revenue = _first(row, "is_total_revenues_standard", "is_total_revenue")
        operating_expenses = _first(
            row, "is_other_operating_expenses_total", "is_operating_expenses_total"
        )
        interest_income = _first(row, "is_interest_and_investment_income")
        interest_expense = _first(
            row, "is_interest_expense_total", "is_net_interest_expenses"
        )
        gross_profit = _first(row, "is_gross_profit")
        operating_income = _first(row, "is_operating_income")

    return {
        "period_end_date": str(row.name)[:10],
        "revenue": _money(revenue),
        "net_income": _money(_first(row, "is_net_income", "is_net_income_to_company")),
        "gross_profit": _money(gross_profit),
        "operating_income": _money(operating_income),
        "operating_expenses": _money(operating_expenses),
        # Per-share value — NOT scaled.
        "eps_basic": _first(row, "is_basic_eps_continuing_operations"),
        "interest_income": _money(interest_income),
        "interest_expense": _money(interest_expense),
    }


def _build_balance(row: pd.Series, is_bank: bool) -> Dict[str, Optional[float]]:
    """Map one wide period row to the normalized balance-sheet record."""
    total_liabilities = _first(
        row,
        "bs_total_liabilities_template_specific",   # bank
        "bs_total_liabilities_standard_utility_template",  # operational / utility
        "bs_total_liabilities_standard",
        "bs_total_liabilities",
    )
    total_equity = _first(
        row,
        "bs_total_equity",          # bank
        "bs_total_equity_standard",  # non-bank
    )
    if is_bank:
        current_assets = None
        current_liabilities = None
        customer_deposits = _first(row, "bs_total_deposits")
    else:
        current_assets = _first(row, "bs_total_current_assets")
        current_liabilities = _first(row, "bs_total_current_liabilities")
        customer_deposits = None

    return {
        "period_end_date": str(row.name)[:10],
        "total_assets": _money(_first(row, "bs_total_assets")),
        "total_liabilities": _money(total_liabilities),
        "total_equity": _money(total_equity),
        "cash_and_equivalents": _money(_first(row, "bs_cash_and_equivalents")),
        "current_assets": _money(current_assets),
        "current_liabilities": _money(current_liabilities),
        "customer_deposits": _money(customer_deposits),
    }


def _compute_ratios(
    inc_records: List[dict],
    bal_records: List[dict],
    price_fallback: Optional[pd.DataFrame],
) -> List[dict]:
    """Recompute the derived ratios from normalized statements (matches egx30_full_scraper).

    Price-derived columns (pe_ratio, market_cap, dividend_yield) are not in the
    Investing.com dump; pull them from the existing yfinance cache when available.
    """
    inc_df = pd.DataFrame(inc_records).set_index("period_end_date") if inc_records else pd.DataFrame()
    bal_df = pd.DataFrame(bal_records).set_index("period_end_date") if bal_records else pd.DataFrame()

    if not inc_df.empty and not bal_df.empty:
        joined = inc_df.join(bal_df, how="outer", rsuffix="_bal")
    elif not inc_df.empty:
        joined = inc_df
    elif not bal_df.empty:
        joined = bal_df
    else:
        return []

    # Index price-derived fallbacks by period for a safe per-row lookup.
    fb = None
    if price_fallback is not None and "period_end_date" in price_fallback.columns:
        fb = price_fallback.set_index(price_fallback["period_end_date"].astype(str).str[:10])

    def _fb(date_str: str, col: str):
        if fb is None or col not in fb.columns or date_str not in fb.index:
            return None
        val = fb.loc[date_str, col]
        if isinstance(val, pd.Series):  # duplicate index guard
            val = val.iloc[0]
        return None if (val is None or (isinstance(val, float) and not math.isfinite(val))) else val

    rows = []
    for date_str, row in joined.sort_index(ascending=False).iterrows():
        ni = row.get("net_income")
        rev = row.get("revenue")
        tl = row.get("total_liabilities")
        te = row.get("total_equity")
        ta = row.get("total_assets")
        ca = row.get("current_assets")
        cl = row.get("current_liabilities")

        def ok(x):
            return x is not None and pd.notna(x)

        rows.append({
            "period_end_date": date_str,
            "net_margin": round(ni / rev, 4) if ok(ni) and ok(rev) and rev else None,
            "roe": round(ni / te, 4) if ok(ni) and ok(te) and te else None,
            "roa": round(ni / ta, 4) if ok(ni) and ok(ta) and ta else None,
            "debt_to_equity": round(tl / te, 2) if ok(tl) and ok(te) and te else None,
            "current_ratio": round(ca / cl, 4) if ok(ca) and ok(cl) and cl else None,
            "eps": row.get("eps_basic") if ok(row.get("eps_basic")) else _fb(date_str, "eps"),
            # Price-derived — Investing.com dump can't supply; fall back to yfinance cache.
            "pe_ratio": _fb(date_str, "pe_ratio"),
            "market_cap": _fb(date_str, "market_cap"),
            "dividend_yield": _fb(date_str, "dividend_yield"),
            "car_ratio": _fb(date_str, "car_ratio"),  # always blank today; preserved if present
        })
    return rows


def _load_wide(path: str) -> Optional[pd.DataFrame]:
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        logger.warning("read failed %s: %s", path, exc)
        return None
    df.columns = df.columns.str.lower().str.strip()
    if "period_end_date" not in df.columns:
        return None
    df = df.set_index("period_end_date")
    return df


def _read_old_ratios(ticker_root: str, freq: str) -> Optional[pd.DataFrame]:
    suffix = "_ratios.csv" if freq == "annual" else "_ratios_quarterly.csv"
    path = os.path.join(DST_RATIOS, f"{ticker_root}{suffix}")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
        df.columns = df.columns.str.lower().str.strip()
        return df
    except Exception:
        return None


def convert_ticker(src_path: str, dry_run: bool) -> dict:
    """Convert one wide ticker file (annual or quarterly) into the 3 normalized files."""
    base = os.path.basename(src_path)
    # e.g. "COMI.CA_annual.csv" -> root "COMI", freq "annual"
    name = base[:-4]  # strip .csv
    root_with_ca, _, freq = name.rpartition("_")
    ticker_root = root_with_ca.upper().replace(".CA", "").strip()

    wide = _load_wide(src_path)
    if wide is None or wide.empty:
        return {"ticker": ticker_root, "freq": freq, "status": "empty"}

    is_bank = BANK_MARKER in wide.columns
    inc_records = [_build_income(row, is_bank) for _, row in wide.iterrows()]
    bal_records = [_build_balance(row, is_bank) for _, row in wide.iterrows()]

    price_fb = _read_old_ratios(ticker_root, freq)
    ratio_records = _compute_ratios(inc_records, bal_records, price_fb)

    inc_suffix = f"_income_{freq}.csv"
    bal_suffix = f"_balance_{freq}.csv"
    rat_suffix = "_ratios.csv" if freq == "annual" else "_ratios_quarterly.csv"

    if not dry_run:
        os.makedirs(DST_INCOME, exist_ok=True)
        os.makedirs(DST_BALANCE, exist_ok=True)
        os.makedirs(DST_RATIOS, exist_ok=True)
        pd.DataFrame(inc_records).to_csv(
            os.path.join(DST_INCOME, f"{ticker_root}{inc_suffix}"), index=False
        )
        pd.DataFrame(bal_records).to_csv(
            os.path.join(DST_BALANCE, f"{ticker_root}{bal_suffix}"), index=False
        )
        pd.DataFrame(ratio_records).to_csv(
            os.path.join(DST_RATIOS, f"{ticker_root}{rat_suffix}"), index=False
        )

    return {
        "ticker": ticker_root,
        "freq": freq,
        "template": "bank" if is_bank else "non-bank",
        "rows": len(inc_records),
        "status": "ok",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Normalize egx30_fundamentals → analyst cache.")
    ap.add_argument("--tickers", default=None,
                    help="Comma-separated tickers (with or without .CA). Default: all in the dump.")
    ap.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
    args = ap.parse_args()

    paths = sorted(glob.glob(os.path.join(SRC_DIR, "*_annual.csv")) +
                   glob.glob(os.path.join(SRC_DIR, "*_quarterly.csv")))
    if args.tickers:
        wanted = {t.strip().upper().replace(".CA", "") for t in args.tickers.split(",") if t.strip()}
        paths = [p for p in paths
                 if os.path.basename(p).split(".CA")[0].upper() in wanted]

    if not paths:
        logger.error("No source files matched under %s", SRC_DIR)
        return

    results = [convert_ticker(p, args.dry_run) for p in paths]
    ok = [r for r in results if r["status"] == "ok"]
    bad = [r for r in results if r["status"] != "ok"]

    print("\n" + "=" * 64)
    print(f"{'DRY-RUN ' if args.dry_run else ''}Normalized {len(ok)}/{len(results)} files "
          f"→ {DST_ROOT}/")
    banks = sorted({r["ticker"] for r in ok if r.get("template") == "bank"})
    print(f"Bank-template tickers: {', '.join(banks)}")
    if bad:
        print("Empty/failed:", ", ".join(f"{r['ticker']}_{r['freq']}" for r in bad))
    print("=" * 64)


if __name__ == "__main__":
    main()
