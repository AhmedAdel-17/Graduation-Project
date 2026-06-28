"""Build the daily technical-signals dataset (Investing.com 'Technical' panel).

Thin driver over ``tradingagents/dataflows/technical_panel.py`` — the SAME module the
live path uses via ``get_live_panel`` — so the historical dataset and any live panel
are computed by identical code (no methodology drift).

For every ticker in data/egx30_ohlcv/ this writes, per trading day (2020-2026):
    data/egx30_signals/<TICKER>_signals.csv   (12 indicators + verdicts + MA grid + summaries)
    data/egx30_signals/<TICKER>_pivots.csv    (5 pivot systems)

Indicator/verdict/pivot definitions and rules: data/egx30_signals/README.md.

Usage:
    python scripts/build_technical_signals.py [--tickers COMI.CA,ETEL.CA]
                                              [--start 2020-01-01 --end 2026-06-20]
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from tradingagents.dataflows.technical_panel import compute_signals, compute_pivots

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("tradingagents.build_technical_signals")

SRC_DIR = Path("data/egx30_ohlcv")
OUT_DIR = Path("data/egx30_signals")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=None)
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default="2026-06-20")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = (sorted(SRC_DIR / f"{t.strip().upper()}.csv" for t in args.tickers.split(","))
             if args.tickers else sorted(p for p in SRC_DIR.glob("*.csv") if not p.name.startswith("_")))

    summary = {}
    for path in files:
        if not path.exists():
            logger.warning("MISSING %s", path.name); continue
        ticker = path.stem
        df = pd.read_csv(path)
        df = df[(df["date"] >= args.start) & (df["date"] <= args.end)].reset_index(drop=True)
        if len(df) < 30:
            summary[ticker] = {"status": "too_short", "rows": len(df)}; continue

        sig = compute_signals(df)
        piv = compute_pivots(df)
        sig.to_csv(OUT_DIR / f"{ticker}_signals.csv", index=False)
        piv.to_csv(OUT_DIR / f"{ticker}_pivots.csv", index=False)
        last = sig.iloc[-1]
        summary[ticker] = {"status": "ok", "rows": len(sig),
                           "last_overall": last["overall_summary"],
                           "last_ind": f"{int(last['ind_buy'])}B/{int(last['ind_sell'])}S/{int(last['ind_neutral'])}N",
                           "last_ma": f"{int(last['ma_buy'])}B/{int(last['ma_sell'])}S"}
        logger.info("OK %-9s %4d rows | overall=%s ind=%s ma=%s", ticker, len(sig),
                    last["overall_summary"], summary[ticker]["last_ind"], summary[ticker]["last_ma"])

    (OUT_DIR / "_manifest.json").write_text(
        json.dumps({"start": args.start, "end": args.end, "tickers": summary}, indent=2),
        encoding="utf-8")
    ok = sum(1 for s in summary.values() if s["status"] == "ok")
    print(f"\nBuilt signals for {ok}/{len(files)} tickers -> {OUT_DIR}/")


if __name__ == "__main__":
    main()
