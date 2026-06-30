"""Unified CLI entry point for the EGX multi-agent system.

One command, three modes — all routed through :func:`tradingagents.runner.run_analysis`,
the same engine the dashboard and API use:

    # Full multi-agent pipeline (all analysts -> debate -> risk), as of today
    python main.py full COMI.CA

    # Quick analysis (reduced real graph: market + fundamentals only)
    python main.py quick COMI.CA

    # Point-in-time scenario backtest: decide on start, evaluate at end
    python main.py backtest COMI.CA --start 2024-01-01 --end 2024-06-30 --capital 100000

Run ``python main.py --help`` for all options.
"""

from dotenv import load_dotenv

load_dotenv()

import argparse
import json
import logging

from tradingagents.runner import (
    MODE_BACKTEST,
    MODE_FULL,
    MODE_QUICK,
    VALID_MODES,
    run_analysis,
)
from tradingagents.observability import setup_logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Run the EGX multi-agent system (quick / full / backtest).",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default=MODE_FULL,
        choices=VALID_MODES,
        help="Which run to perform (default: full).",
    )
    parser.add_argument(
        "ticker",
        nargs="?",
        default="COMI.CA",
        help="EGX ticker, e.g. COMI.CA (default: COMI.CA).",
    )
    parser.add_argument(
        "--as-of",
        dest="as_of_date",
        default=None,
        help="Point-in-time date YYYY-MM-DD for quick/full (default: last trading day).",
    )
    parser.add_argument("--start", dest="start_date", default=None,
                        help="Backtest window start (YYYY-MM-DD).")
    parser.add_argument("--end", dest="end_date", default=None,
                        help="Backtest window end (YYYY-MM-DD).")
    parser.add_argument("--interval", dest="interval_days", type=int, default=20,
                        help="Backtest: calendar days between evaluations (default: 20).")
    parser.add_argument("--capital", dest="initial_capital", type=float, default=1_000_000.0,
                        help="Backtest: initial capital in EGP (default: 1,000,000).")
    parser.add_argument("--analysts", default=None,
                        help="Comma-separated analyst subset, e.g. market,fundamentals.")
    parser.add_argument("--resume", action="store_true",
                        help="Backtest: resume from a partial checkpoint.")
    parser.add_argument("--debug", action="store_true", help="Verbose graph logging.")
    return parser


def main() -> None:
    setup_logging()
    args = _build_parser().parse_args()

    analysts = (
        [a.strip() for a in args.analysts.split(",") if a.strip()]
        if args.analysts
        else None
    )

    result = run_analysis(
        args.ticker,
        mode=args.mode,
        as_of_date=args.as_of_date,
        start_date=args.start_date,
        end_date=args.end_date,
        analysts=analysts,
        initial_capital=args.initial_capital,
        interval_days=args.interval_days,
        resume=args.resume,
        debug=args.debug,
    )

    if args.mode == MODE_BACKTEST:
        print(f"\nBacktest complete for {result['ticker']} "
              f"({result['start_date']} -> {result['end_date']}).")
        print(f"Prediction: {result.get('prediction')} "
              f"(executed: {result.get('executed_action')})")
        if result.get("stock_return_pct") is not None:
            print(f"Stock move: {result['stock_return_pct']:+.2f}%")
        if result.get("follow_return_pct") is not None:
            print(f"Follow AI:  {result['follow_return_pct']:+.2f}%")
        if result.get("index_return_pct") is not None:
            print(f"EGX30:      {result['index_return_pct']:+.2f}%")
        ok = result.get("prediction_correct")
        if ok is not None:
            print(f"Prediction correct: {'yes' if ok else 'no'}")
        print(f"Report: {result.get('report_path') or '(no report written)'}")
    else:
        print(f"\n{result['ticker']} [{result['mode']}] as of {result['as_of_date']}")
        print(f"Signal: {result['signal']}")
        if result.get("confidence") is not None:
            print(f"Confidence: {result['confidence']}")
        print("-" * 60)
        print(result["decision"])


if __name__ == "__main__":
    main()
