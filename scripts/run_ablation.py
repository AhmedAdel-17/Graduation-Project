#!/usr/bin/env python3
"""
Run the full ablation suite for one ticker across all experiments.

Usage:
    python scripts/run_ablation.py --ticker COMI.CA --dates 2024-01-15,2024-01-16,...

Or generate dates automatically:
    python scripts/run_ablation.py --ticker COMI.CA --start 2024-01-01 --end 2024-03-31

After all experiments complete, run the evaluator:
    python -m tradingagents.ablation.evaluate --input results/ablation.jsonl --ticker COMI.CA
"""

import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tradingagents.ablation.runner import EXPERIMENT_CONFIGS, run_ablation_experiment


def generate_trading_dates(start: str, end: str) -> list:
    """Generate EGX trading dates (Sun-Thu) between start and end."""
    dates = []
    current = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    while current <= end_dt:
        # EGX trades Sun-Thu (weekday: Sun=6, Mon=0, Tue=1, Wed=2, Thu=3)
        # Python weekday: Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
        if current.weekday() not in (4, 5):  # Skip Fri and Sat
            dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


def main():
    parser = argparse.ArgumentParser(description="Run full ablation suite")
    parser.add_argument("--ticker", required=True, help="EGX ticker (e.g., COMI.CA)")
    parser.add_argument("--dates", help="Comma-separated dates")
    parser.add_argument("--start", help="Start date for auto-generation (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date for auto-generation (YYYY-MM-DD)")
    parser.add_argument("--output", default="results/ablation.jsonl")
    parser.add_argument("--experiments", help="Comma-separated experiment IDs (default: all)")
    args = parser.parse_args()

    # Resolve dates
    if args.dates:
        dates = [d.strip() for d in args.dates.split(",")]
    elif args.start and args.end:
        dates = generate_trading_dates(args.start, args.end)
    else:
        print("ERROR: provide either --dates or --start/--end")
        sys.exit(1)

    print(f"Ticker: {args.ticker}")
    print(f"Dates: {len(dates)} trading days ({dates[0]} to {dates[-1]})")
    print(f"Output: {args.output}")
    print()

    # Resolve experiments
    if args.experiments:
        experiment_ids = [e.strip() for e in args.experiments.split(",")]
    else:
        experiment_ids = list(EXPERIMENT_CONFIGS.keys())

    print(f"Experiments to run: {experiment_ids}")
    print()

    for exp_id in experiment_ids:
        if exp_id not in EXPERIMENT_CONFIGS:
            print(f"WARNING: Unknown experiment '{exp_id}', skipping")
            continue
        try:
            run_ablation_experiment(exp_id, args.ticker, dates, args.output)
        except Exception as e:
            print(f"ERROR in experiment {exp_id}: {e}")
            import traceback
            traceback.print_exc()
        print()

    print("=" * 60)
    print("ALL EXPERIMENTS COMPLETE")
    print(f"Results: {args.output}")
    print(f"Evaluate with:")
    print(f"  python -m tradingagents.ablation.evaluate --input {args.output} --ticker {args.ticker}")


if __name__ == "__main__":
    main()
