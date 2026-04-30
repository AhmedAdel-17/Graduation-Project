"""
Benchmark Comparison Report
============================
Side-by-side comparison of the LLM multi-agent system vs the Backtrader
classical technical strategy.

Can be used in two ways:
  1. As a module: call compare_results(llm_metrics, bt_metrics, ticker)
  2. As a CLI:    python benchmark_comparison.py <llm_report.json> <bt_report.json>

Metrics compared:
  Total Return, Alpha, Win Rate, Max Drawdown, Sharpe Ratio,
  Calmar Ratio, Total Trades, Total Commissions, Final Portfolio
"""

import json
import os
import sys
from typing import Dict, List, Optional, Tuple

# Ordered list of metrics to display (subset that both engines always produce)
_SHARED_METRICS: List[str] = [
    "Total Return",
    "Win Rate",
    "Max Drawdown",
    "Sharpe Ratio",
    "Calmar Ratio",
    "Total Trades",
    "Total Commissions",
    "Final Portfolio",
]

# Metrics where a larger value is better (for winner annotation)
_HIGHER_IS_BETTER = {"Total Return", "Alpha", "Win Rate", "Sharpe Ratio", "Calmar Ratio"}

# Metrics where a less-negative (larger) value is better
_LESS_NEGATIVE_IS_BETTER = {"Max Drawdown"}


def _to_float(value) -> Optional[float]:
    """Best-effort parse of a metric value string to float."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        clean = value.replace("%", "").replace(",", "").replace(" EGP", "").strip()
        try:
            return float(clean)
        except ValueError:
            return None
    return None


def _winner_tag(metric: str, llm_val, bt_val) -> str:
    """Return a short annotation indicating which engine won on this metric."""
    lf = _to_float(llm_val)
    bf = _to_float(bt_val)
    if lf is None or bf is None:
        return ""

    if metric in _HIGHER_IS_BETTER or metric in _LESS_NEGATIVE_IS_BETTER:
        if lf > bf:
            return "  LLM +"
        if bf > lf:
            return "  BT  +"
    return ""


def compare_results(
    llm_metrics: Dict,
    bt_metrics: Dict,
    ticker: str = "",
    extra_llm_metrics: Optional[Dict] = None,
) -> str:
    """
    Build a formatted side-by-side comparison table.

    Args:
        llm_metrics:       metrics dict from BacktestingEngine._calculate_metrics()
        bt_metrics:        metrics dict from run_bt_benchmark()["metrics"]
        ticker:            ticker label shown in the header
        extra_llm_metrics: optional extra keys from the LLM report (e.g. Alpha,
                           Benchmark Return) that the BT engine doesn't produce

    Returns:
        A multi-line string — print it or write it to a file.
    """
    col_w = 22  # column width for each value column
    label_w = 28

    lines: List[str] = []

    header = f"  COMPARISON REPORT — {ticker}" if ticker else "  COMPARISON REPORT"
    lines.append("=" * 76)
    lines.append(header)
    lines.append("=" * 76)
    lines.append(
        f"{'Metric':<{label_w}}  {'LLM Multi-Agent':>{col_w}}  {'Classical (BT)':>{col_w}}  Note"
    )
    lines.append("-" * 76)

    # Merge in any LLM-only extra metrics (Alpha, Benchmark Return)
    display_metrics = list(_SHARED_METRICS)
    if extra_llm_metrics:
        for k in ("Benchmark Return", "Alpha"):
            if k in extra_llm_metrics:
                llm_metrics = {**llm_metrics, k: extra_llm_metrics[k]}
                if k not in display_metrics:
                    display_metrics.insert(1, k)  # right after Total Return

    for metric in display_metrics:
        llm_val = llm_metrics.get(metric, "—")
        bt_val  = bt_metrics.get(metric, "—")
        tag     = _winner_tag(metric, llm_val, bt_val)
        lines.append(
            f"{metric:<{label_w}}  {str(llm_val):>{col_w}}  {str(bt_val):>{col_w}}{tag}"
        )

    lines.append("=" * 76)

    # ---- Overall verdict ----
    llm_wins = 0
    bt_wins  = 0
    for metric in ("Total Return", "Sharpe Ratio", "Win Rate"):
        lf = _to_float(llm_metrics.get(metric))
        bf = _to_float(bt_metrics.get(metric))
        if lf is not None and bf is not None:
            if lf > bf:
                llm_wins += 1
            elif bf > lf:
                bt_wins  += 1

    if llm_wins > bt_wins:
        verdict = "LLM Multi-Agent OUTPERFORMS classical technical strategy"
    elif bt_wins > llm_wins:
        verdict = "Classical Technical strategy OUTPERFORMS LLM multi-agent"
    else:
        verdict = "Results are MIXED — no clear overall winner"

    lines.append(f"\n  VERDICT: {verdict}")
    lines.append(
        f"  Scorecard (Return / Sharpe / Win-Rate): "
        f"LLM {llm_wins} — {bt_wins} Classical"
    )
    lines.append("=" * 76 + "\n")

    return "\n".join(lines)


def compare_from_files(llm_path: str, bt_path: str) -> str:
    """
    Load two saved JSON reports from disk and produce the comparison string.

    Args:
        llm_path: path to the LLM backtester JSON (from BacktestingEngine.save_results)
        bt_path:  path to the Backtrader JSON      (from run_bt_benchmark)

    Returns:
        Formatted comparison string.
    """
    with open(llm_path, encoding="utf-8") as f:
        llm_report = json.load(f)
    with open(bt_path, encoding="utf-8") as f:
        bt_report = json.load(f)

    llm_metrics = llm_report.get("metrics", {})
    bt_metrics  = bt_report.get("metrics", {})
    ticker      = llm_report.get("session", "")

    return compare_results(llm_metrics, bt_metrics, ticker)


def print_multi_ticker_summary(results: List[Tuple[str, Dict, Dict]]):
    """
    Print a condensed summary table across all tickers.

    Args:
        results: list of (ticker, llm_metrics, bt_metrics)
    """
    print("\n" + "=" * 76)
    print("  ABLATION SUMMARY — LLM Multi-Agent vs Classical Technical")
    print("=" * 76)
    print(
        f"  {'Ticker':<12} {'LLM Return':>12} {'BT Return':>12} "
        f"{'LLM Sharpe':>12} {'BT Sharpe':>12}  Winner"
    )
    print("-" * 76)

    for ticker, llm_m, bt_m in results:
        llm_ret    = llm_m.get("Total Return", "N/A")
        bt_ret     = bt_m.get("Total Return",  "N/A")
        llm_sharpe = llm_m.get("Sharpe Ratio", "N/A")
        bt_sharpe  = bt_m.get("Sharpe Ratio",  "N/A")

        lrf = _to_float(llm_ret)
        brf = _to_float(bt_ret)
        winner = "LLM" if (lrf is not None and brf is not None and lrf > brf) else "Classical"

        print(
            f"  {ticker:<12} {llm_ret:>12} {bt_ret:>12} "
            f"{llm_sharpe:>12} {bt_sharpe:>12}  {winner}"
        )

    print("=" * 76 + "\n")


# =============================================================================
# CLI Entry Point
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Compare LLM backtester vs Backtrader benchmark reports"
    )
    parser.add_argument("llm_report", type=str, help="Path to LLM backtester JSON report")
    parser.add_argument("bt_report",  type=str, help="Path to Backtrader JSON report")
    args = parser.parse_args()

    if not os.path.exists(args.llm_report):
        print(f"ERROR: LLM report not found: {args.llm_report}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(args.bt_report):
        print(f"ERROR: BT report not found: {args.bt_report}", file=sys.stderr)
        sys.exit(1)

    print(compare_from_files(args.llm_report, args.bt_report))
