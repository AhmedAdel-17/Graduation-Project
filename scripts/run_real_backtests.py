"""
Run Real World Backtests
========================
Evaluates 3 large-cap EGX stocks across a structured historical timeline to
validate the multi-agent LLM system against actual market conditions.

Phase 2: Each ticker runs through both engines back-to-back:
  1. LLM Multi-Agent Backtest  (BacktestingEngine — scripts/backtester.py)
  2. Classical Technical Bench (EGXTechnicalStrategy — scripts/bt_benchmark.py)

A side-by-side comparison is printed after each ticker, and a summary table
is printed at the end across all tickers.
"""

import os
import sys
import time
import traceback
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.backtester import BacktestingEngine
from scripts.bt_benchmark import run_bt_benchmark
from scripts.benchmark_comparison import compare_results, print_multi_ticker_summary

logger = logging.getLogger("RunBacktests")


def main():
    tickers    = ["COMI.CA", "EAST.CA", "HRHO.CA"]
    start_date = "2023-10-01"
    end_date   = "2024-01-01"
    interval   = 20          # calendar days between LLM evaluations
    capital    = 1_000_000.0 # EGP

    print("=" * 76)
    print("  REAL WORLD EVALUATION — LLM Multi-Agent + Classical Benchmark")
    print(f"  Period : {start_date}  →  {end_date}")
    print(f"  Capital: {capital:,.0f} EGP   |   LLM interval: {interval} days")
    print("=" * 76)

    all_results = []  # [(ticker, llm_metrics, bt_metrics)]

    for ticker in tickers:
        print(f"\n{'─'*76}")
        print(f"  TICKER: {ticker}")
        print(f"{'─'*76}")

        # ------------------------------------------------------------------
        # 1 / 2  LLM Multi-Agent Backtest
        # ------------------------------------------------------------------
        print(f"\n  [1/2] LLM Multi-Agent Backtest — {ticker}")
        llm_metrics: dict = {}
        engine = BacktestingEngine(
            initial_capital=capital,
            benchmark_ticker="^EGX30",
        )
        try:
            # Using analysts=["market"] to stay within Groq free-tier TPM limits.
            # Switch to ["market","fundamentals","news","social"] for full ablation.
            engine.run_backtest(
                ticker,
                start_date,
                end_date,
                interval_days=interval,
                analysts=["market"],
            )
            llm_metrics = engine._calculate_metrics()
        except Exception as e:
            print(f"\n  [ERROR] LLM backtest failed for {ticker}: {e}")
            traceback.print_exc()

        print(f"\n  Cooling off 10 s before starting Backtrader run...")
        time.sleep(10)

        # ------------------------------------------------------------------
        # 2 / 2  Backtrader Classical Technical Benchmark
        # ------------------------------------------------------------------
        print(f"\n  [2/2] Backtrader Classical Benchmark — {ticker}")
        bt_metrics: dict = {}
        try:
            bt_result  = run_bt_benchmark(ticker, start_date, end_date, capital)
            bt_metrics = bt_result["metrics"]
        except Exception as e:
            print(f"\n  [ERROR] Backtrader benchmark failed for {ticker}: {e}")
            traceback.print_exc()

        # ------------------------------------------------------------------
        # Side-by-side comparison for this ticker
        # ------------------------------------------------------------------
        if llm_metrics and bt_metrics:
            # Pass any LLM-only metrics (Alpha, Benchmark Return) as extras
            extra = {k: llm_metrics[k] for k in ("Alpha", "Benchmark Return") if k in llm_metrics}
            print(compare_results(llm_metrics, bt_metrics, ticker, extra_llm_metrics=extra))
            all_results.append((ticker, llm_metrics, bt_metrics))
        else:
            print(f"\n  Skipping comparison for {ticker} — one or both runs failed.\n")

        if ticker != tickers[-1]:
            print(f"  Waiting 10 s before next ticker...")
            time.sleep(10)

    # ------------------------------------------------------------------
    # Final summary across all tickers
    # ------------------------------------------------------------------
    if all_results:
        print_multi_ticker_summary(all_results)


if __name__ == "__main__":
    main()
