"""Free OHLCV (+ optional technical-indicator) dataset builder for the EGX universe.

Pulls daily OHLCV for every EGX ticker from **yfinance** (free, no API key),
runs it through the project's ``get_YFin_data_online`` so the phantom
forward-filled bars are dropped, and writes one clean CSV per ticker to
``data/egx_ohlcv/``. Optionally computes the standard indicator set the market
analyst uses (SMA/EMA/MACD/RSI/Bollinger) — note these are *derived* from the
OHLCV, not a separate dataset.

Why pre-fetch to CSV?
  * a durable, inspectable dataset you own (yfinance can silently change);
  * reproducible backtests;
  * one place to also drop the manually-sourced gaps (QNBA, the EGX30 ETF/index
    from Investing.com) in the same schema.

Usage
-----
    # Full EGX universe, 2020 -> today, with indicators
    python scripts/fetch_egx_ohlcv.py --start 2020-01-01 --indicators

    # A subset
    python scripts/fetch_egx_ohlcv.py --tickers COMI.CA,ETEL.CA --start 2020-01-01

Known free-source gaps (handled gracefully, reported at the end):
  * QNBA.CA  — absent on yfinance; export from Investing.com (QNB Al Ahli).
  * EFIH.CA / ORAS.CA — genuinely listed only from 2021 (not a data gap).
  * EGX30 index + ETF — no yfinance ticker; export daily CSV from Investing.com.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

import pandas as pd

from tradingagents.default_config import EGX_TICKERS
from tradingagents.dataflows.y_finance import get_YFin_data_online

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("tradingagents.fetch_egx_ohlcv")

OHLCV_COLS = ["date", "open", "high", "low", "close", "volume"]


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Append the standard indicator set used by the market analyst.

    Deterministic, pandas-only (no extra deps). Wilder's RSI(14), SMA 20/50/200,
    EMA 12/26, MACD(12,26,9), Bollinger(20, 2σ).
    """
    out = df.copy()
    close = out["close"]
    out["sma_20"] = close.rolling(20).mean()
    out["sma_50"] = close.rolling(50).mean()
    out["sma_200"] = close.rolling(200).mean()
    out["ema_12"] = close.ewm(span=12, adjust=False).mean()
    out["ema_26"] = close.ewm(span=26, adjust=False).mean()
    out["macd"] = out["ema_12"] - out["ema_26"]
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]

    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, pd.NA)
    out["rsi_14"] = 100.0 - 100.0 / (1.0 + rs)

    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    out["bb_mid"] = mid
    out["bb_upper"] = mid + 2.0 * std
    out["bb_lower"] = mid - 2.0 * std
    return out


def fetch_ticker(ticker: str, start: str, end: str) -> Dict:
    """Fetch one ticker's full-history OHLCV (phantom bars already dropped)."""
    return get_YFin_data_online(ticker, start, end, max_records=None)


def build(
    tickers: List[str],
    start: str,
    end: str,
    out_dir: Path,
    with_indicators: bool,
) -> Dict[str, Dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: Dict[str, Dict] = {}

    for ticker in tickers:
        try:
            result = fetch_ticker(ticker, start, end)
        except Exception as exc:  # network / vendor failure
            logger.warning("FAILED %s: %s", ticker, exc)
            summary[ticker] = {"status": "error", "error": str(exc), "rows": 0}
            continue

        rows = result.get("data") or []
        if not rows:
            logger.warning("EMPTY %s: %s", ticker, result.get("error"))
            summary[ticker] = {
                "status": "empty",
                "error": result.get("error"),
                "rows": 0,
            }
            continue

        df = pd.DataFrame(rows)[OHLCV_COLS]
        if with_indicators:
            df = compute_indicators(df)

        csv_path = out_dir / f"{ticker}.csv"
        df.to_csv(csv_path, index=False)

        summary[ticker] = {
            "status": "ok",
            "rows": len(df),
            "first": rows[0]["date"],
            "last": rows[-1]["date"],
            "phantom_dropped": result.get("phantom_dropped", 0),
            "low_liquidity": result.get("low_liquidity"),
            "path": str(csv_path),
        }
        logger.info(
            "OK  %-9s %4d bars  %s -> %s  (phantom dropped: %s)",
            ticker, len(df), rows[0]["date"], rows[-1]["date"],
            result.get("phantom_dropped", 0),
        )

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Free EGX OHLCV dataset builder (yfinance).")
    parser.add_argument("--tickers", default=None,
                        help="Comma-separated tickers. Default: full EGX_TICKERS universe.")
    parser.add_argument("--start", default="2020-01-01", help="Start date YYYY-MM-DD.")
    parser.add_argument("--end", default=date.today().isoformat(), help="End date YYYY-MM-DD.")
    parser.add_argument("--out", default="data/egx_ohlcv", help="Output directory.")
    parser.add_argument("--indicators", action="store_true",
                        help="Also compute SMA/EMA/MACD/RSI/Bollinger columns.")
    args = parser.parse_args()

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers else sorted(EGX_TICKERS)
    )
    out_dir = Path(args.out)

    logger.info("Fetching %d tickers | %s -> %s | indicators=%s | out=%s",
                len(tickers), args.start, args.end, args.indicators, out_dir)

    summary = build(tickers, args.start, args.end, out_dir, args.indicators)

    # Manifest for the user's records.
    manifest_path = out_dir / "_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {"start": args.start, "end": args.end, "indicators": args.indicators,
             "generated_at": date.today().isoformat(), "tickers": summary},
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    ok = [t for t, s in summary.items() if s["status"] == "ok"]
    bad = [t for t, s in summary.items() if s["status"] != "ok"]
    late = [(t, s["first"]) for t, s in summary.items()
            if s["status"] == "ok" and s["first"] > f"{args.start[:4]}-02-01"]

    print("\n" + "=" * 64)
    print(f"DONE: {len(ok)}/{len(tickers)} tickers written to {out_dir}/")
    print(f"Manifest: {manifest_path}")
    if late:
        print("\nStarts later than requested (likely a real later listing):")
        for t, f in late:
            print(f"  {t}: first bar {f}")
    if bad:
        print("\nNOT available on yfinance — source manually (Investing.com export):")
        for t in bad:
            print(f"  {t}: {summary[t].get('error')}")
    print("\nReminder: the EGX30 index + ETF have no yfinance ticker — export")
    print("them daily from Investing.com and drop them in the project root.")
    print("=" * 64)


if __name__ == "__main__":
    main()
