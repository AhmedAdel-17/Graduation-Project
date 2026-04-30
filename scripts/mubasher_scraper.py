"""
mubasher_scraper.py
===================
Scrapes quarterly financial data from Mubasher for EGX tickers
and fills in the MANUAL_ENTRY_REQUIRED gaps in the quarterly CSV files.

Mubasher URL pattern:
  https://english.mubasher.info/markets/EGX/stocks/{TICKER}/financials/

Data scraped:
  - Income statement (Quarterly tab): revenue, net income, EPS, operating income
  - Balance sheet (Quarterly tab): total assets, liabilities, equity, cash

Output: fills rows marked MANUAL_ENTRY_REQUIRED in:
  data_cache/egx_fundamentals/income_statements/{TICKER}_income_quarterly.csv
  data_cache/egx_fundamentals/balance_sheets/{TICKER}_balance_quarterly.csv
  data_cache/egx_fundamentals/key_ratios/{TICKER}_ratios_quarterly.csv

Usage:
    python scripts/mubasher_scraper.py --ticker COMI
    python scripts/mubasher_scraper.py --ticker COMI EAST HRHO
    python scripts/mubasher_scraper.py --all
    python scripts/mubasher_scraper.py --ticker COMI --headful   # see the browser
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime
from typing import Optional

import pandas as pd
from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

# ---------------------------------------------------------------------------
# Setup
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

BASE_URL = "https://english.mubasher.info/markets/EGX/stocks/{ticker}/financials/"

ALL_TICKERS = [
    "COMI", "EAST", "HRHO", "TMGH", "SWDY",
    "FWRY", "ETEL", "ABUK", "ADIB", "EFIH", "EGAL", "MFPC", "CCAP",
    "SKPC", "AMOC", "ESRS", "ORWE", "HELI", "GBCO",
    "ORAS", "PHDC", "CIEB", "ISPH", "DSCW",
    "RMDA", "ARCC", "BTFH", "JUFO", "ORHD", "RAYA", "VLMR",
]

PRIORITY_TICKERS = ["COMI", "EAST", "HRHO", "TMGH", "SWDY"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(text: str) -> Optional[float]:
    """Parse Arabic/English number string → float or None."""
    if not text or text.strip() in ("-", "--", "N/A", ""):
        return None
    # Remove commas, spaces, currency symbols
    cleaned = text.replace(",", "").replace(" ", "").replace("EGP", "").strip()
    # Handle parentheses as negative (e.g. "(1,234)" → -1234)
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        val = float(cleaned)
        # Mubasher shows values in thousands on some pages; we want raw EGP
        # Detect if value looks like thousands (< 100B but > 0): scale up
        # We'll NOT auto-scale — keep raw value as shown and note units
        return val
    except ValueError:
        return None


def _normalize_date(date_str: str) -> Optional[str]:
    """
    Normalize Mubasher date headers to YYYY-MM-DD quarter-end format.
    Handles: 'Q1 2024', 'Mar 2024', '03/2024', '2024-03-31', etc.
    """
    s = date_str.strip()

    QUARTER_MAP = {
        "Q1": "03-31", "Q2": "06-30", "Q3": "09-30", "Q4": "12-31",
        "Mar": "03-31", "Jun": "06-30", "Sep": "09-30", "Dec": "12-31",
        "March": "03-31", "June": "06-30", "September": "09-30", "December": "12-31",
        "January": "01-31", "February": "02-28", "April": "04-30",
        "May": "05-31", "July": "07-31", "August": "08-31",
        "October": "10-31", "November": "11-30",
    }

    # Already ISO format
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass

    # "Q1 2024" or "Q1/2024"
    for q, suffix in QUARTER_MAP.items():
        for sep in [" ", "/", "-"]:
            if s.startswith(q + sep):
                year_part = s[len(q) + 1:].strip()
                if year_part.isdigit() and len(year_part) == 4:
                    return f"{year_part}-{suffix}"

    # "Mar 2024" or "March 2024"
    for month_name, day_suffix in QUARTER_MAP.items():
        if s.startswith(month_name + " "):
            year_part = s[len(month_name) + 1:].strip()
            if year_part.isdigit() and len(year_part) == 4:
                return f"{year_part}-{day_suffix}"

    # "03/2024"
    if "/" in s:
        parts = s.split("/")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            month, year = int(parts[0]), parts[1]
            import calendar
            last_day = calendar.monthrange(int(year), month)[1]
            return f"{year}-{month:02d}-{last_day:02d}"

    return None


def _snap_to_quarter_end(date_str: str) -> Optional[str]:
    """Snap any date to the nearest quarter-end: Mar-31, Jun-30, Sep-30, Dec-31."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        month = dt.month
        year  = dt.year
        if 1 <= month <= 3:
            return f"{year}-03-31"
        elif 4 <= month <= 6:
            return f"{year}-06-30"
        elif 7 <= month <= 9:
            return f"{year}-09-30"
        else:
            return f"{year}-12-31"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Mubasher page parser
# ---------------------------------------------------------------------------

def _click_quarterly_tab(page: Page) -> bool:
    """Click the Quarterly tab. Returns True if successful."""
    # Try various selectors Mubasher uses for the quarterly tab
    selectors = [
        "text=Quarterly",
        "[data-period='quarterly']",
        "button:has-text('Quarterly')",
        "a:has-text('Quarterly')",
        ".tabs a:nth-child(2)",
        "li:has-text('Quarterly')",
    ]
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el:
                el.click()
                page.wait_for_load_state("networkidle", timeout=8000)
                time.sleep(1.5)
                log.info("  Clicked quarterly tab via: %s", sel)
                return True
        except Exception:
            pass
    log.warning("  Could not find quarterly tab — will try to parse current view")
    return False


def _extract_table(page: Page, section_hint: str = "") -> dict:
    """
    Extract a financial table from the current page view.
    Returns: {date_str: {metric_name: value, ...}, ...}
    """
    result = {}

    tables = page.query_selector_all("table")
    if not tables:
        # Try structured divs (some Mubasher views use div-based tables)
        return _extract_div_table(page)

    for table in tables:
        rows = table.query_selector_all("tr")
        if not rows:
            continue

        # First row is header with dates
        header_row = rows[0]
        header_cells = header_row.query_selector_all("th, td")
        if len(header_cells) < 2:
            continue

        dates = []
        for i, cell in enumerate(header_cells):
            if i == 0:
                dates.append(None)  # label column
                continue
            raw_date = cell.inner_text().strip()
            normalized = _normalize_date(raw_date) or _snap_to_quarter_end(raw_date) if raw_date else None
            dates.append(normalized)

        # Data rows
        for row in rows[1:]:
            cells = row.query_selector_all("td, th")
            if not cells:
                continue
            label = cells[0].inner_text().strip().lower().replace("\n", " ")
            for i, cell in enumerate(cells[1:], 1):
                if i >= len(dates) or not dates[i]:
                    continue
                date_key = dates[i]
                val_text  = cell.inner_text().strip()
                val       = _safe_float(val_text)
                if date_key not in result:
                    result[date_key] = {}
                result[date_key][label] = val

    return result


def _extract_div_table(page: Page) -> dict:
    """Fallback: extract data from div-based layout."""
    result = {}
    # Look for common Mubasher div patterns
    # This is a best-effort fallback
    rows = page.query_selector_all(".financial-row, .data-row, [class*='row']")
    log.warning("  Using div-table fallback — found %d rows", len(rows))
    return result


# ---------------------------------------------------------------------------
# Map Mubasher labels → our CSV column names
# ---------------------------------------------------------------------------

INCOME_LABEL_MAP = {
    # English labels from Mubasher
    "revenue":                          "revenue",
    "net revenue":                      "revenue",
    "total revenue":                    "revenue",
    "net interest income":              "revenue",       # banks
    "total net revenue":                "revenue",
    "net income":                       "net_income",
    "net profit":                       "net_income",
    "net profit attributable":          "net_income",
    "profit for the period":            "net_income",
    "net income attributable to shareholders": "net_income",
    "gross profit":                     "gross_profit",
    "operating income":                 "operating_income",
    "operating profit":                 "operating_income",
    "operating expenses":               "operating_expenses",
    "total operating expenses":         "operating_expenses",
    "eps":                              "eps_basic",
    "earnings per share":               "eps_basic",
    "basic eps":                        "eps_basic",
    "basic earnings per share":         "eps_basic",
    "interest income":                  "interest_income",
    "interest expense":                 "interest_expense",
    "interest expenses":                "interest_expense",
}

BALANCE_LABEL_MAP = {
    "total assets":                     "total_assets",
    "assets":                           "total_assets",
    "total liabilities":                "total_liabilities",
    "liabilities":                      "total_liabilities",
    "total equity":                     "total_equity",
    "shareholders equity":              "total_equity",
    "stockholders equity":              "total_equity",
    "equity":                           "total_equity",
    "cash and cash equivalents":        "cash_and_equivalents",
    "cash":                             "cash_and_equivalents",
    "cash & cash equivalents":          "cash_and_equivalents",
    "customer deposits":                "customer_deposits",
    "deposits from customers":          "customer_deposits",
}


def _map_row(raw_data: dict, label_map: dict) -> dict:
    """Map raw {label: value} using label_map → {csv_col: value}."""
    mapped = {}
    for raw_label, value in raw_data.items():
        for pattern, col in label_map.items():
            if pattern in raw_label:
                if col not in mapped or (value is not None and mapped[col] is None):
                    mapped[col] = value
                break
    return mapped


# ---------------------------------------------------------------------------
# Navigate and scrape both statement types
# ---------------------------------------------------------------------------

def scrape_ticker(page: Page, ticker: str) -> dict:
    """
    Navigate to Mubasher financials for ticker, click Quarterly,
    and extract both income and balance sheet data.

    Returns:
        {
            "income":  {date: {col: value}, ...},
            "balance": {date: {col: value}, ...},
        }
    """
    url = BASE_URL.format(ticker=ticker)
    log.info("[%s] Loading: %s", ticker, url)

    try:
        page.goto(url, timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=20000)
        time.sleep(2)
    except PWTimeout:
        log.error("[%s] Page load timed out", ticker)
        return {"income": {}, "balance": {}}

    title = page.title()
    log.info("[%s] Page title: %s", ticker, title)

    body_text = page.inner_text("body")
    if (
        "error" in title.lower()
        or "404" in title
        or "updating the website" in body_text.lower()
        or "نحن نقوم بتحديث" in body_text  # Arabic "we are updating"
    ):
        log.error("[%s] Mubasher is down or page not found. Try again later.", ticker)
        return {"income": {}, "balance": {}}

    income_data  = {}
    balance_data = {}

    # --- Try to find Income Statement section ---
    # Mubasher typically has tabs: Income Statement | Balance Sheet
    # Or dropdowns to switch between them

    income_selectors = [
        "text=Income Statement",
        "text=Profit & Loss",
        "text=P&L",
        "a:has-text('Income')",
        "button:has-text('Income')",
    ]

    # Click quarterly first for income
    _click_quarterly_tab(page)

    # Extract income table from current view
    log.info("[%s] Extracting income table...", ticker)
    raw_income = _extract_table(page)
    for date_key, row_data in raw_income.items():
        mapped = _map_row(row_data, INCOME_LABEL_MAP)
        if mapped:
            income_data[date_key] = mapped
    log.info("[%s] Income: found %d date periods", ticker, len(income_data))

    # --- Switch to Balance Sheet ---
    balance_selectors = [
        "text=Balance Sheet",
        "text=Financial Position",
        "a:has-text('Balance')",
        "button:has-text('Balance')",
        "li:has-text('Balance')",
        "[data-type='balance']",
    ]

    clicked_balance = False
    for sel in balance_selectors:
        try:
            el = page.query_selector(sel)
            if el:
                el.click()
                page.wait_for_load_state("networkidle", timeout=8000)
                time.sleep(1.5)
                log.info("[%s] Clicked balance sheet via: %s", ticker, sel)
                clicked_balance = True
                break
        except Exception:
            pass

    if not clicked_balance:
        log.warning("[%s] Could not navigate to balance sheet separately", ticker)
    else:
        # Click quarterly again for balance sheet (tab may have reset)
        _click_quarterly_tab(page)

    log.info("[%s] Extracting balance table...", ticker)
    raw_balance = _extract_table(page)
    for date_key, row_data in raw_balance.items():
        mapped = _map_row(row_data, BALANCE_LABEL_MAP)
        if mapped:
            balance_data[date_key] = mapped
    log.info("[%s] Balance: found %d date periods", ticker, len(balance_data))

    return {"income": income_data, "balance": balance_data}


# ---------------------------------------------------------------------------
# Update CSVs with scraped data
# ---------------------------------------------------------------------------

def _fill_csv(path: str, scraped: dict, col_map: dict, source_tag: str = "mubasher") -> int:
    """
    Fill MANUAL_ENTRY_REQUIRED rows in CSV with scraped data.
    Returns number of rows updated.
    """
    if not os.path.exists(path):
        log.warning("CSV not found: %s", path)
        return 0

    df = pd.read_csv(path)
    updated = 0

    for idx, row in df.iterrows():
        date_str = str(row.get("period_end_date", "")).strip()
        if not date_str:
            continue

        # Only fill rows that are missing
        if row.get("data_source") not in ("MANUAL_ENTRY_REQUIRED", ""):
            continue

        # Try exact match first, then quarter-snapped match
        matched_data = scraped.get(date_str)
        if not matched_data:
            snapped = _snap_to_quarter_end(date_str)
            matched_data = scraped.get(snapped)

        if not matched_data:
            continue

        # Fill columns
        changed = False
        for csv_col in col_map:
            if csv_col in ("period_end_date", "data_source"):
                continue
            scraped_val = matched_data.get(csv_col)
            if scraped_val is not None:
                df.at[idx, csv_col] = scraped_val
                changed = True

        if changed:
            df.at[idx, "data_source"] = source_tag
            updated += 1

    if updated > 0:
        df.to_csv(path, index=False)
        log.info("  Updated %d rows in %s", updated, os.path.basename(path))

    return updated


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


def _derive_and_fill_ratios(ticker: str, income_scraped: dict, balance_scraped: dict):
    """
    Compute derived ratios from scraped income + balance and fill ratios CSV.
    """
    ratios_path = os.path.join(OUT_RATIOS, f"{ticker}_ratios_quarterly.csv")
    if not os.path.exists(ratios_path):
        log.warning("Ratios CSV not found: %s", ratios_path)
        return 0

    df = pd.read_csv(ratios_path)
    updated = 0

    for idx, row in df.iterrows():
        date_str = str(row.get("period_end_date", "")).strip()
        if row.get("data_source") not in ("MANUAL_ENTRY_REQUIRED", ""):
            continue

        inc = income_scraped.get(date_str) or income_scraped.get(_snap_to_quarter_end(date_str) or "")
        bal = balance_scraped.get(date_str) or balance_scraped.get(_snap_to_quarter_end(date_str) or "")

        if not inc and not bal:
            continue

        inc = inc or {}
        bal = bal or {}

        net_income   = inc.get("net_income")
        revenue      = inc.get("revenue")
        total_liab   = bal.get("total_liabilities")
        total_equity = bal.get("total_equity")
        total_assets = bal.get("total_assets")
        eps          = inc.get("eps_basic")

        changed = False
        if net_income and revenue and revenue != 0:
            df.at[idx, "net_margin"] = round(net_income / revenue, 4)
            changed = True
        if net_income and total_equity and total_equity != 0:
            df.at[idx, "roe"] = round(net_income / total_equity, 4)
            changed = True
        if net_income and total_assets and total_assets != 0:
            df.at[idx, "roa"] = round(net_income / total_assets, 4)
            changed = True
        if total_liab and total_equity and total_equity != 0:
            df.at[idx, "debt_to_equity"] = round(total_liab / total_equity, 2)
            changed = True
        if eps is not None:
            df.at[idx, "eps"] = eps
            changed = True

        if changed:
            df.at[idx, "data_source"] = "mubasher_derived"
            updated += 1

    if updated > 0:
        df.to_csv(ratios_path, index=False)
        log.info("  Derived %d ratio rows for %s", updated, ticker)

    return updated


def update_csvs(ticker: str, scraped: dict) -> dict:
    """Update all 3 CSV files with scraped data. Returns counts."""
    income_path  = os.path.join(OUT_INCOME,  f"{ticker}_income_quarterly.csv")
    balance_path = os.path.join(OUT_BALANCE, f"{ticker}_balance_quarterly.csv")

    inc_updated = _fill_csv(income_path,  scraped["income"],  {c: c for c in INCOME_COLS})
    bal_updated = _fill_csv(balance_path, scraped["balance"], {c: c for c in BALANCE_COLS})
    rat_updated = _derive_and_fill_ratios(ticker, scraped["income"], scraped["balance"])

    return {
        "income_rows_updated":  inc_updated,
        "balance_rows_updated": bal_updated,
        "ratios_rows_updated":  rat_updated,
    }


# ---------------------------------------------------------------------------
# Status report
# ---------------------------------------------------------------------------

def print_status(ticker: str):
    """Print how many quarters are filled vs still missing."""
    inc_path = os.path.join(OUT_INCOME, f"{ticker}_income_quarterly.csv")
    if not os.path.exists(inc_path):
        print(f"  {ticker}: no file found")
        return

    df = pd.read_csv(inc_path)
    total   = len(df)
    manual  = len(df[df["data_source"] == "MANUAL_ENTRY_REQUIRED"])
    yf      = len(df[df["data_source"] == "yfinance"])
    mub     = len(df[df["data_source"].str.startswith("mubasher", na=False)])
    filled  = yf + mub

    bar = "#" * filled + "." * manual
    print(f"  {ticker:6s} [{bar:16s}]  {filled:2d}/{total} filled  ({manual} still missing)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Mubasher quarterly scraper for EGX tickers")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ticker", nargs="+", help="One or more tickers (e.g. COMI EAST HRHO)")
    group.add_argument("--all",    action="store_true", help="Scrape all 31 tickers")
    group.add_argument("--priority", action="store_true", help="Scrape top 5 priority tickers only")
    parser.add_argument("--headful", action="store_true", help="Show browser window (debug)")
    parser.add_argument("--status",  action="store_true", help="Just show fill status, no scraping")
    parser.add_argument("--slow-mo", type=int, default=0, help="Slow-mo delay in ms (debug)")
    args = parser.parse_args()

    # Determine ticker list
    if args.all:
        tickers = ALL_TICKERS
    elif args.priority:
        tickers = PRIORITY_TICKERS
    elif args.ticker:
        tickers = [t.upper().replace(".CA", "") for t in args.ticker]
    else:
        tickers = PRIORITY_TICKERS  # default

    # Status-only mode
    if args.status:
        print("\nQuarterly data fill status:")
        for t in (tickers if args.ticker or args.all else ALL_TICKERS):
            print_status(t)
        return

    log.info("="*60)
    log.info("  MUBASHER QUARTERLY SCRAPER")
    log.info("  Tickers: %s", tickers)
    log.info("  Headful: %s", args.headful)
    log.info("="*60)

    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not args.headful,
            slow_mo=args.slow_mo,
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = context.new_page()

        for i, ticker in enumerate(tickers, 1):
            log.info("\n[%d/%d] Processing %s", i, len(tickers), ticker)
            try:
                scraped = scrape_ticker(page, ticker)
                counts  = update_csvs(ticker, scraped)
                results[ticker] = counts
                log.info(
                    "[%s] Done — income:%d balance:%d ratios:%d rows updated",
                    ticker,
                    counts["income_rows_updated"],
                    counts["balance_rows_updated"],
                    counts["ratios_rows_updated"],
                )
            except Exception as e:
                import traceback
                log.error("[%s] FAILED: %s\n%s", ticker, e, traceback.format_exc())
                results[ticker] = {"error": str(e)}

            # Polite delay between tickers
            if i < len(tickers):
                time.sleep(2)

        browser.close()

    # Summary
    print("\n" + "="*60)
    print("  SCRAPE SUMMARY")
    print("="*60)
    for ticker, r in results.items():
        if "error" in r:
            print(f"  {ticker:6s}  FAILED: {r['error']}")
        else:
            print(
                f"  {ticker:6s}  income:{r['income_rows_updated']} "
                f"balance:{r['balance_rows_updated']} "
                f"ratios:{r['ratios_rows_updated']} rows updated"
            )

    print("\nFill status after scrape:")
    for ticker in tickers:
        print_status(ticker)

    print("\n" + "="*60)
    print("  Remaining MANUAL_ENTRY_REQUIRED rows must be filled from:")
    print("  https://english.mubasher.info/markets/EGX/stocks/{TICKER}/financials/")
    print("="*60)


if __name__ == "__main__":
    main()
