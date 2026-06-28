"""Historical fundamentals dataset builder for the EGX universe (Investing.com).

Investing.com renders every equity's financial statements server-side and embeds
the full multi-period dataset in the page's ``__NEXT_DATA__`` JSON blob. We fetch
the income-statement / balance-sheet / cash-flow tabs per ticker (via Scrapling's
TLS-impersonating ``Fetcher`` — no headless browser, no API key), pull the embedded
``reports`` arrays, and flatten them into one clean **wide** CSV per ticker:

    rows    = reporting periods (period_end_date)
    columns = is_<field> / bs_<field> / cf_<field>  (income / balance / cashflow)

Two files are written per ticker:
    data/egx30_fundamentals/<TICKER>_annual.csv      (annual,    ~2016 -> latest FY)
    data/egx30_fundamentals/<TICKER>_quarterly.csv   (quarterly, recent ~10 quarters)

Annual history covers the full 2020-2026 window the project cares about; quarterly
is only published by Investing.com for the trailing ~2.5 years.

Ticker -> Investing.com slug mapping is resolved once via their search API and
cached in ``_slug_map.json`` (rebuild with --remap).

Usage
-----
    # Full EGX universe (uses cached slug map, builds it first run)
    python scripts/fetch_egx_fundamentals.py

    # A subset, force re-map of slugs
    python scripts/fetch_egx_fundamentals.py --tickers COMI.CA,ETEL.CA --remap

Known gaps (reported at the end):
  * QNBA.CA  — QNB Al Ahli: no Investing.com financials page (absent on yfinance too).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from scrapling.fetchers import Fetcher

from tradingagents.default_config import EGX_TICKERS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("tradingagents.fetch_egx_fundamentals")

OUT_DIR = Path("data/egx30_fundamentals")
SLUG_MAP_PATH = OUT_DIR / "_slug_map.json"
SEARCH_URL = "https://api.investing.com/api/search/v2/search?q={q}"
EQUITY_URL = "https://www.investing.com/equities/{slug}-{tab}"

# (store-key, annual-data-key, quarterly-data-key, column-prefix)
STATEMENTS = [
    ("incomeStatementStore", "incomeStatementDataAnnual", "incomeStatementDataQuarterly", "is", "income-statement"),
    ("balanceSheetStore",    "balanceSheetDataAnnual",    "balanceSheetDataQuarterly",    "bs", "balance-sheet"),
    ("cashFlowStore",        "cashFlowDataAnnual",         "cashFlowDataQuarterly",        "cf", "cash-flow"),
]

NEXT_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


def _fetch_next_state(url: str, tries: int = 4, pause: float = 1.5) -> Optional[dict]:
    """Fetch a page and return its parsed ``__NEXT_DATA__`` ``state`` dict.

    Investing.com intermittently serves an anti-bot challenge page (no
    ``__NEXT_DATA__``); a few retries clears it.
    """
    for attempt in range(tries):
        try:
            page = Fetcher.get(
                url, stealthy_headers=True, follow_redirects=True, timeout=30
            )
            html = page.html_content if hasattr(page, "html_content") else str(page)
            match = NEXT_RE.search(html)
            if match:
                data = json.loads(match.group(1))
                return data["props"]["pageProps"]["state"]
        except Exception as exc:  # network / parse hiccup -> retry
            logger.debug("fetch %s attempt %d failed: %s", url, attempt + 1, exc)
        time.sleep(pause)
    return None


# --------------------------------------------------------------------------- #
# Slug mapping
# --------------------------------------------------------------------------- #
def _search_quotes(query: str) -> List[dict]:
    """Hit Investing.com's search API and return the ``quotes`` array."""
    page = Fetcher.get(
        SEARCH_URL.format(q=query), stealthy_headers=True,
        follow_redirects=True, timeout=20,
    )
    html = page.html_content if hasattr(page, "html_content") else str(page)
    inner = re.sub(r"^.*?<p>", "", html, flags=re.S)
    inner = re.sub(r"</p>.*$", "", inner, flags=re.S).strip()
    try:
        return json.loads(inner).get("quotes", [])
    except Exception:
        return []


def build_slug_map(tickers: List[str]) -> Dict[str, Optional[dict]]:
    """Resolve each EGX ticker root to its Investing.com Egypt-equity slug."""
    mapping: Dict[str, Optional[dict]] = {}
    for ticker in tickers:
        root = ticker.replace(".CA", "").upper()
        quotes = _search_quotes(root)
        egypt = [
            q for q in quotes
            if str(q.get("flag", "")).lower() == "egypt"
            and str(q.get("url", "")).startswith("/equities/")
        ]
        exact = [q for q in egypt if str(q.get("symbol", "")).upper() == root]
        pick = exact or egypt
        if pick:
            q = pick[0]
            mapping[ticker] = {
                "slug": q["url"].split("?")[0].replace("/equities/", ""),
                "desc": q.get("description"),
                "id": q.get("id"),
            }
            logger.info("map %-9s -> %s (%s)", ticker, mapping[ticker]["slug"], q.get("description"))
        else:
            mapping[ticker] = None
            logger.warning("map %-9s -> NOT FOUND", ticker)
        time.sleep(0.6)
    return mapping


def load_or_build_slug_map(tickers: List[str], remap: bool) -> Dict[str, Optional[dict]]:
    if SLUG_MAP_PATH.exists() and not remap:
        cached = json.loads(SLUG_MAP_PATH.read_text(encoding="utf-8"))
        if all(t in cached for t in tickers):
            return cached
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mapping = build_slug_map(tickers)
    # merge with any existing cache so partial subset runs don't drop entries
    if SLUG_MAP_PATH.exists():
        cached = json.loads(SLUG_MAP_PATH.read_text(encoding="utf-8"))
        cached.update(mapping)
        mapping = cached
    SLUG_MAP_PATH.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return mapping


# --------------------------------------------------------------------------- #
# Statement flattening
# --------------------------------------------------------------------------- #
def _reports_to_frame(reports: List[dict], prefix: str) -> pd.DataFrame:
    """Flatten one statement's period reports into a period-indexed frame."""
    rows = []
    for rep in reports:
        row = {
            "period_end_date": (rep.get("period_end_date") or "")[:10],
            "year": rep.get("year"),
            "currency_id": rep.get("currency_id"),
        }
        for key, ind in (rep.get("indicators") or {}).items():
            row[f"{prefix}_{key}"] = ind.get("value")
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("period_end_date")
    return df


def _collect(state: dict, store: str, data_key: str, prefix: str) -> pd.DataFrame:
    try:
        reports = state[store][data_key]["reports"]
    except (KeyError, TypeError):
        return pd.DataFrame()
    return _reports_to_frame(reports, prefix)


def fetch_ticker_fundamentals(slug: str) -> Dict[str, pd.DataFrame]:
    """Return {'annual': df, 'quarterly': df} merged across all three statements."""
    annual_parts: List[pd.DataFrame] = []
    quarterly_parts: List[pd.DataFrame] = []

    for store, annual_key, quarterly_key, prefix, tab in STATEMENTS:
        state = _fetch_next_state(EQUITY_URL.format(slug=slug, tab=tab))
        if state is None:
            logger.warning("  %-14s tab failed (challenge/empty)", tab)
            continue
        a = _collect(state, store, annual_key, prefix)
        q = _collect(state, store, quarterly_key, prefix)
        if not a.empty:
            annual_parts.append(a)
        if not q.empty:
            quarterly_parts.append(q)
        time.sleep(0.8)

    def _merge(parts: List[pd.DataFrame]) -> pd.DataFrame:
        if not parts:
            return pd.DataFrame()
        merged = parts[0]
        for extra in parts[1:]:
            # keep meta cols from the first; only add new value cols
            new_cols = [c for c in extra.columns if c not in ("year", "currency_id")]
            merged = merged.join(extra[new_cols], how="outer")
        return merged.sort_index()

    return {"annual": _merge(annual_parts), "quarterly": _merge(quarterly_parts)}


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def build(tickers: List[str], remap: bool, start_year: int) -> Dict[str, dict]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    slug_map = load_or_build_slug_map(tickers, remap)
    summary: Dict[str, dict] = {}

    for ticker in tickers:
        entry = slug_map.get(ticker)
        if not entry:
            summary[ticker] = {"status": "no_slug", "annual_rows": 0, "quarterly_rows": 0}
            logger.warning("SKIP %-9s no Investing.com slug", ticker)
            continue

        slug = entry["slug"]
        try:
            frames = fetch_ticker_fundamentals(slug)
        except Exception as exc:
            summary[ticker] = {"status": "error", "error": str(exc),
                               "annual_rows": 0, "quarterly_rows": 0}
            logger.warning("FAILED %-9s %s", ticker, exc)
            continue

        wrote = {}
        for period, df in frames.items():
            if df.empty:
                continue
            # keep only periods >= start_year for the headline annual file;
            # quarterly is already recent-only.
            if period == "annual" and "year" in df.columns:
                df = df[df["year"].fillna(0) >= start_year]
            if df.empty:
                continue
            path = OUT_DIR / f"{ticker}_{period}.csv"
            df.to_csv(path)
            wrote[period] = {"rows": len(df), "cols": df.shape[1], "path": str(path)}

        summary[ticker] = {
            "status": "ok" if wrote else "empty",
            "slug": slug,
            "company": entry.get("desc"),
            "annual_rows": wrote.get("annual", {}).get("rows", 0),
            "quarterly_rows": wrote.get("quarterly", {}).get("rows", 0),
        }
        logger.info(
            "OK  %-9s annual=%d quarterly=%d  (%s)",
            ticker, summary[ticker]["annual_rows"],
            summary[ticker]["quarterly_rows"], slug,
        )
        time.sleep(1.0)

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="EGX fundamentals dataset builder (Investing.com).")
    parser.add_argument("--tickers", default=None,
                        help="Comma-separated tickers. Default: full EGX_TICKERS universe.")
    parser.add_argument("--remap", action="store_true",
                        help="Force rebuild of the ticker->slug map.")
    parser.add_argument("--start-year", type=int, default=2020,
                        help="Drop annual periods before this fiscal year (default 2020).")
    args = parser.parse_args()

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers else sorted(EGX_TICKERS)
    )

    logger.info("Building fundamentals for %d tickers (start_year=%d)", len(tickers), args.start_year)
    summary = build(tickers, args.remap, args.start_year)

    manifest = OUT_DIR / "_manifest.json"
    manifest.write_text(
        json.dumps({"source": "investing.com", "start_year": args.start_year,
                    "tickers": summary}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    ok = [t for t, s in summary.items() if s["status"] == "ok"]
    bad = [t for t, s in summary.items() if s["status"] != "ok"]
    print("\n" + "=" * 64)
    print(f"DONE: {len(ok)}/{len(tickers)} tickers written to {OUT_DIR}/")
    print(f"Manifest: {manifest}")
    if bad:
        print("\nNo fundamentals written:")
        for t in bad:
            print(f"  {t}: {summary[t]['status']} {summary[t].get('error','')}")
    print("=" * 64)


if __name__ == "__main__":
    main()
