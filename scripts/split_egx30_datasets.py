"""Split the combined EGX OHLCV+indicator CSVs into two clean per-ticker datasets.

Source: ``data/egx_ohlcv/<TICKER>.csv`` (built by ``fetch_egx_ohlcv.py`` with
``--indicators``; one file per ticker, OHLCV + derived indicators together).

Produces, clipped to the requested window [--start, --end]:
    data/egx30_ohlcv/<TICKER>.csv      -> date, open, high, low, close, volume
    data/egx30_technical/<TICKER>.csv  -> date + SMA/EMA/MACD/RSI/Bollinger columns

Technical indicators are DERIVED from the OHLCV (Wilder RSI, SMA/EMA, MACD,
Bollinger) — not a separately sourced dataset. They are split out here purely so
the deliverable has three distinct datasets (OHLCV / technical / fundamentals).

Usage:
    python scripts/split_egx30_datasets.py --start 2020-01-01 --end 2026-06-20
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("tradingagents.split_egx30")

SRC_DIR = Path("data/egx_ohlcv")
OHLCV_DIR = Path("data/egx30_ohlcv")
TECH_DIR = Path("data/egx30_technical")

OHLCV_COLS = ["date", "open", "high", "low", "close", "volume"]
TECH_COLS = [
    "date", "sma_20", "sma_50", "sma_200", "ema_12", "ema_26",
    "macd", "macd_signal", "macd_hist", "rsi_14",
    "bb_mid", "bb_upper", "bb_lower",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default="2026-06-20")
    args = ap.parse_args()

    OHLCV_DIR.mkdir(parents=True, exist_ok=True)
    TECH_DIR.mkdir(parents=True, exist_ok=True)

    files = sorted(p for p in SRC_DIR.glob("*.csv") if not p.name.startswith("_"))
    summary = {}
    for path in files:
        ticker = path.stem
        df = pd.read_csv(path)
        df = df[(df["date"] >= args.start) & (df["date"] <= args.end)]
        if df.empty:
            summary[ticker] = {"status": "empty_after_clip", "rows": 0}
            logger.warning("EMPTY %-9s after window clip", ticker)
            continue

        ohlcv = df[[c for c in OHLCV_COLS if c in df.columns]]
        tech = df[[c for c in TECH_COLS if c in df.columns]]
        ohlcv.to_csv(OHLCV_DIR / f"{ticker}.csv", index=False)
        tech.to_csv(TECH_DIR / f"{ticker}.csv", index=False)

        summary[ticker] = {
            "status": "ok",
            "rows": len(df),
            "first": df["date"].iloc[0],
            "last": df["date"].iloc[-1],
        }
        logger.info("OK  %-9s %4d bars  %s -> %s",
                    ticker, len(df), df["date"].iloc[0], df["date"].iloc[-1])

    for out_dir in (OHLCV_DIR, TECH_DIR):
        (out_dir / "_manifest.json").write_text(
            json.dumps({"start": args.start, "end": args.end,
                        "source": "yfinance (via data/egx_ohlcv)",
                        "tickers": summary}, indent=2),
            encoding="utf-8",
        )

    ok = sum(1 for s in summary.values() if s["status"] == "ok")
    print(f"\nSplit {ok}/{len(files)} tickers -> {OHLCV_DIR}/ and {TECH_DIR}/")


if __name__ == "__main__":
    main()
