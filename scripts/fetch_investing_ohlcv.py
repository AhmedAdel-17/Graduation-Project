"""Backfill OHLCV from Investing.com's historical-price API (for yfinance gaps).

Some EGX names are broken/short on yfinance (e.g. ORAS.CA: only 45 bars). Investing
exposes full daily history via its internal API keyed by a numeric instrument id:

    https://api.investing.com/api/financialdata/historical/{instrument_id}
        ?start-date=YYYY-MM-DD&end-date=YYYY-MM-DD&time-frame=Daily

The instrument id is read from the equity overview page's __NEXT_DATA__
(`instrument_id`). We fetch via Scrapling, parse the `*Raw` numeric fields, write a
clean OHLCV+indicator CSV into data/egx_ohlcv/ (same schema as fetch_egx_ohlcv) so a
subsequent `split_egx30_datasets.py` folds it into the deliverable.

Usage:
    python scripts/fetch_investing_ohlcv.py --ticker ORAS.CA --slug orascom-construction-ltd
    python scripts/fetch_investing_ohlcv.py --ticker ORAS.CA --instrument 950025
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from scrapling.fetchers import Fetcher

from scripts.fetch_egx_ohlcv import compute_indicators, OHLCV_COLS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("tradingagents.fetch_investing_ohlcv")

NEXT_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
HIST_URL = ("https://api.investing.com/api/financialdata/historical/{iid}"
            "?start-date={start}&end-date={end}&time-frame=Daily&add-missing-rows=false")


def _get(url: str, tries: int = 4, **kw):
    for _ in range(tries):
        try:
            p = Fetcher.get(url, stealthy_headers=True, follow_redirects=True, timeout=35, **kw)
            return p.html_content if hasattr(p, "html_content") else str(p)
        except Exception:
            continue
    return None


def resolve_instrument(slug: str) -> Optional[str]:
    html = _get(f"https://www.investing.com/equities/{slug}")
    if not html:
        return None
    m = NEXT_RE.search(html)
    if not m:
        return None
    s = m.group(1)
    hit = re.search(r'"instrument_id":\s*"?(\d+)"?', s)
    return hit.group(1) if hit else None


def fetch_history(instrument_id: str, start: str, end: str) -> pd.DataFrame:
    raw = _get(HIST_URL.format(iid=instrument_id, start=start, end=end),
               headers={"domain-id": "www", "Accept": "application/json"})
    if not raw:
        return pd.DataFrame()
    inner = re.sub(r"^.*?<p>", "", raw, flags=re.S)
    inner = re.sub(r"</p>.*$", "", inner, flags=re.S).strip()
    try:
        payload = json.loads(inner)
    except Exception:
        return pd.DataFrame()
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not rows:
        return pd.DataFrame()

    recs = []
    for r in rows:
        # rowDateTimestamp is ISO ("2026-06-17T00:00:00Z"); rowDateRaw is epoch int.
        iso = r.get("rowDateTimestamp")
        try:
            if isinstance(iso, str) and "-" in iso:
                d = iso[:10]
            else:
                d = datetime.utcfromtimestamp(int(r["rowDateRaw"])).strftime("%Y-%m-%d")
        except Exception:
            continue
        try:
            recs.append({
                "date": d,
                "open": float(r["last_openRaw"]),
                "high": float(r["last_maxRaw"]),
                "low": float(r["last_minRaw"]),
                "close": float(r["last_closeRaw"]),
                "volume": int(float(r.get("volumeRaw") or 0)),
            })
        except Exception:
            continue

    if not recs:
        return pd.DataFrame()
    df = pd.DataFrame(recs).sort_values("date").reset_index(drop=True)
    # drop zero-price phantom rows (non-trading days Investing pads)
    df = df[df["close"] > 0].reset_index(drop=True)
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True, help="e.g. ORAS.CA")
    ap.add_argument("--slug", default=None, help="Investing equity slug (to resolve instrument id)")
    ap.add_argument("--instrument", default=None, help="Investing numeric instrument id (skips slug)")
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--out", default="data/egx_ohlcv")
    args = ap.parse_args()

    iid = args.instrument or (resolve_instrument(args.slug) if args.slug else None)
    if not iid:
        logger.error("Could not resolve instrument id (give --instrument or --slug)")
        return
    logger.info("%s instrument_id=%s", args.ticker, iid)

    df = fetch_history(iid, args.start, args.end)
    if df.empty:
        logger.error("No history returned for %s", args.ticker)
        return

    df = compute_indicators(df)[["date", *OHLCV_COLS[1:],
        "sma_20", "sma_50", "sma_200", "ema_12", "ema_26",
        "macd", "macd_signal", "macd_hist", "rsi_14", "bb_mid", "bb_upper", "bb_lower"]]
    out = Path(args.out) / f"{args.ticker.upper()}.csv"
    df.to_csv(out, index=False)
    logger.info("OK %s %d bars %s -> %s  (%s)", args.ticker, len(df),
                df["date"].iloc[0], df["date"].iloc[-1], out)


if __name__ == "__main__":
    main()
