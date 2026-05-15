"""
Post-hoc evaluator for ablation experiment results.

Reads the JSONL output from runner.py, computes forward returns,
and produces a comparison table across experiments.

Usage:
    python -m tradingagents.ablation.evaluate \
        --input results/ablation.jsonl \
        --ticker COMI.CA \
        --output results/ablation_report.json
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from tradingagents.ablation.schemas import AblationRecord


def load_records(path: str) -> List[dict]:
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def fetch_forward_returns(ticker: str, dates: List[str]) -> Dict[str, Dict[str, float]]:
    """
    Fetch actual forward returns for each trade_date.

    Returns: {date_str: {"1d": float, "5d": float, "10d": float}}
    """
    try:
        import yfinance as yf
        from datetime import datetime, timedelta

        all_dates = sorted(set(dates))
        start = min(all_dates)
        # Fetch enough extra days for 10-day forward return
        end_dt = datetime.strptime(max(all_dates), "%Y-%m-%d") + timedelta(days=20)

        df = yf.download(ticker, start=start, end=end_dt.strftime("%Y-%m-%d"),
                         progress=False)
        if df.empty:
            return {}

        df.index = pd.to_datetime(df.index)
        closes = df["Close"].dropna()

        result = {}
        for d in all_dates:
            dt = pd.Timestamp(d)
            # Find the closest trading day on or after this date
            future = closes[closes.index >= dt]
            if len(future) < 11:
                continue
            base_price = future.iloc[0]
            result[d] = {
                "1d": float((future.iloc[1] / base_price - 1)),
                "5d": float((future.iloc[5] / base_price - 1)) if len(future) > 5 else None,
                "10d": float((future.iloc[10] / base_price - 1)) if len(future) > 10 else None,
            }
        return result
    except Exception as e:
        print(f"Warning: could not fetch forward returns: {e}")
        return {}


def is_decision_correct(decision: str, forward_return: float) -> Optional[bool]:
    """Check if a decision was directionally correct."""
    if decision == "BUY":
        return forward_return > 0
    elif decision == "SELL":
        return forward_return < 0
    elif decision == "HOLD":
        return abs(forward_return) < 0.02  # HOLD is "correct" if price didn't move much
    return None


def compute_metrics(records: List[dict], returns: Dict[str, Dict]) -> dict:
    """Compute aggregate metrics for a set of records from one experiment."""
    n = len(records)
    if n == 0:
        return {}

    decisions = [r["final_decision"] for r in records]
    wall_times = [r["total_wall_time_ms"] for r in records]
    llm_calls = [r["total_llm_calls"] for r in records]
    input_tokens = [r["total_input_tokens"] for r in records]
    output_tokens = [r["total_output_tokens"] for r in records]
    costs = [r["estimated_api_cost_usd"] for r in records]

    # Decision distribution
    decision_counts = defaultdict(int)
    for d in decisions:
        decision_counts[d] += 1

    # Accuracy (directional correctness vs 5d return)
    correct_count = 0
    evaluated_count = 0
    for r in records:
        fwd = returns.get(r["trade_date"], {})
        ret_5d = fwd.get("5d")
        if ret_5d is not None:
            evaluated_count += 1
            if is_decision_correct(r["final_decision"], ret_5d):
                correct_count += 1

    accuracy = correct_count / evaluated_count if evaluated_count > 0 else None

    # Agreement with BASELINE
    # (computed externally by caller)

    return {
        "n_runs": n,
        "decision_distribution": dict(decision_counts),
        "accuracy_5d": accuracy,
        "accuracy_n_evaluated": evaluated_count,
        "latency_mean_s": np.mean(wall_times) / 1000,
        "latency_median_s": np.median(wall_times) / 1000,
        "latency_p95_s": np.percentile(wall_times, 95) / 1000,
        "llm_calls_mean": np.mean(llm_calls),
        "total_input_tokens_mean": np.mean(input_tokens),
        "total_output_tokens_mean": np.mean(output_tokens),
        "cost_per_run_mean_usd": np.mean(costs),
        "cost_total_usd": sum(costs),
    }


def compute_agreement(records_a: List[dict], records_b: List[dict]) -> float:
    """Fraction of dates where two experiments made the same decision."""
    map_a = {r["trade_date"]: r["final_decision"] for r in records_a}
    map_b = {r["trade_date"]: r["final_decision"] for r in records_b}
    common_dates = set(map_a.keys()) & set(map_b.keys())
    if not common_dates:
        return 0.0
    agreed = sum(1 for d in common_dates if map_a[d] == map_b[d])
    return agreed / len(common_dates)


def evaluate(input_path: str, ticker: str, output_path: str = None):
    all_records = load_records(input_path)

    # Filter to ticker
    records = [r for r in all_records if r["ticker"] == ticker]
    if not records:
        print(f"No records found for ticker {ticker}")
        return

    # Group by experiment
    by_exp: Dict[str, List[dict]] = defaultdict(list)
    for r in records:
        by_exp[r["experiment_id"]].append(r)

    # Fetch forward returns
    all_dates = list(set(r["trade_date"] for r in records))
    print(f"Fetching forward returns for {len(all_dates)} dates...")
    returns = fetch_forward_returns(ticker, all_dates)
    print(f"Got returns for {len(returns)} dates")

    # Compute per-experiment metrics
    report = {}
    for exp_id, exp_records in sorted(by_exp.items()):
        metrics = compute_metrics(exp_records, returns)

        # Agreement with BASELINE
        if exp_id != "BASELINE" and "BASELINE" in by_exp:
            metrics["agreement_with_baseline"] = compute_agreement(
                by_exp["BASELINE"], exp_records
            )

        report[exp_id] = metrics

    # Print comparison table
    print("\n" + "=" * 100)
    print(f"ABLATION RESULTS: {ticker}")
    print("=" * 100)
    header = f"{'Experiment':<12} {'N':>4} {'Acc-5d':>7} {'Agree%':>7} {'Latency':>8} {'LLM#':>5} {'Cost$':>7} {'BUY':>4} {'HOLD':>4} {'SELL':>4}"
    print(header)
    print("-" * 100)

    for exp_id in ["BASELINE", "EXP-S1", "EXP-RF", "EXP-R1", "EXP-R2",
                    "EXP-A1", "EXP-A2", "EXP-D", "EXP-T1", "TARGET"]:
        m = report.get(exp_id)
        if not m:
            continue
        acc = f"{m['accuracy_5d']:.0%}" if m.get("accuracy_5d") is not None else "N/A"
        agree = f"{m.get('agreement_with_baseline', 0):.0%}" if exp_id != "BASELINE" else "---"
        dd = m.get("decision_distribution", {})
        print(
            f"{exp_id:<12} {m['n_runs']:>4} {acc:>7} {agree:>7} "
            f"{m['latency_median_s']:>7.1f}s {m['llm_calls_mean']:>5.1f} "
            f"${m['cost_per_run_mean_usd']:>6.4f} "
            f"{dd.get('BUY', 0):>4} {dd.get('HOLD', 0):>4} {dd.get('SELL', 0):>4}"
        )

    print("=" * 100)

    # Key decision criteria
    print("\n### DECISION CRITERIA ###")
    print("Keep LLM if: removing it drops accuracy_5d by >5% AND agreement <80%")
    print("Remove LLM if: accuracy holds (±3%) AND latency drops meaningfully")
    print("Hybrid if: accuracy drops slightly but latency gain is >50%")

    if output_path:
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nFull report saved to {output_path}")

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    evaluate(args.input, args.ticker, args.output)
