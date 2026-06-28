"""EGX scenario backtest — dashboard + CLI entry point.

Runs the multi-agent pipeline **once** on the start date (as if it were a live
run in the past), then evaluates the outcome at the end date:

  1. **Prediction accuracy** — was BUY/SELL/HOLD right vs what the stock did?
  2. **Benchmark** — did following the AI beat buying the EGX30 index/ETF?

Backtest-only data discipline (live runs are untouched):

  * Analysts: **market + fundamentals** only (no news/social agents).
  * ``prefetch_data=False`` — no news/social fetch pipelines.
  * ``ohlcv_local_only=True`` — OHLCV from ``data/egx30_ohlcv`` CSVs only.
  * Fundamentals from the local normalized cache (no API refresh).

The old interval-based walk-forward engine (``scripts/backtester.py``) remains
for thesis batch runs; this script is what the dashboard **Run backtest** button
and ``python main.py backtest`` use.

Usage
-----
    python scripts/run_backtest.py --ticker COMI.CA --start 2024-01-01 --end 2024-06-30
    python scripts/run_backtest.py --ticker ETEL.CA --start 2024-01-01 --end 2024-04-01 \\
        --capital 100000 --rfr 0.0
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_backtest.py",
        description=(
            "Scenario backtest: one agent prediction on the start date, "
            "evaluated against reality + EGX30 at the end date."
        ),
    )
    p.add_argument("--ticker", required=True, help="EGX ticker, e.g. COMI.CA")
    p.add_argument("--start", required=True, help="Decision date YYYY-MM-DD")
    p.add_argument("--end", required=True, help="Evaluation / sell date YYYY-MM-DD")
    p.add_argument(
        "--capital",
        type=float,
        default=1_000_000.0,
        help="Starting budget in EGP (default: 1,000,000)",
    )
    p.add_argument(
        "--rfr",
        type=float,
        default=0.0,
        help="Disclosed decision required-return hurdle (default 0.0)",
    )
    return p


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    args = _build_parser().parse_args()

    from scripts.scenario_backtest import run_single_scenario

    log = logging.getLogger("run_backtest")
    log.info(
        "Scenario backtest: %s | decide %s → evaluate %s | capital=%.0f EGP",
        args.ticker, args.start, args.end, args.capital,
    )

    rec = run_single_scenario(
        args.ticker,
        start=args.start,
        end=args.end,
        rfr=float(args.rfr),
        initial_capital=float(args.capital),
    )
    if not rec:
        log.error("Backtest produced no result — check ticker OHLCV coverage and LLM keys.")
        return 1

    print("\n" + "=" * 70)
    print(f"Ticker:     {rec['ticker']}")
    print(f"Window:     {rec['start']} → {rec['end']}")
    print(f"Predicted:  {rec.get('predicted_direction') or rec['decision']}")
    print(f"Executed:   {rec['decision']} ({rec.get('action_taken', '')})")
    print(f"Stock move: {rec['stock_return_pct']:+.2f}%")
    print(f"Follow AI:  {rec['follow_return_pct']:+.2f}%")
    if rec.get("index_return_pct") is not None:
        print(f"EGX30:      {rec['index_return_pct']:+.2f}%")
        print(f"Beat index: {'yes' if rec.get('followed_beat_index') else 'no'}")
    print(f"Prediction correct: {'yes' if rec.get('prediction_correct') else 'no'}")
    print(f"Session:    {rec.get('session_id')}")
    print("=" * 70)
    print(json.dumps(rec, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
