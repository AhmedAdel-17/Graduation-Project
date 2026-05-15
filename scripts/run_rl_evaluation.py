"""Compare baseline vs RL meta-policy vs classical on a walk-forward backtest.

This script is *consumer-only*: it reads JSON reports already produced by
``scripts/backtester.py`` (one with the RL flag off, one with it on) and
optionally ``scripts/bt_benchmark.py`` for the classical baseline, then
prints + writes the pre-registered comparison.

Run order (graduation workflow):

1. Generate the baseline arm::

       python scripts/backtester.py --ticker COMI.CA --start 2023-10-01 --end 2024-01-01

2. Generate the RL arm (same dates) with the flag on::

       RL_META_POLICY_ENABLED=1 RL_MODEL_PATH=models/rl_meta_v1.pt \\
           python scripts/backtester.py --ticker COMI.CA --start 2023-10-01 --end 2024-01-01

3. Optionally generate the classical arm::

       python scripts/bt_benchmark.py --ticker COMI.CA --start 2023-10-01 --end 2024-01-01

4. Compare::

       python scripts/run_rl_evaluation.py \\
           --baseline  backtest_results/report_COMI.CA_<ts_baseline>.json \\
           --rl-meta   backtest_results/report_COMI.CA_<ts_rl>.json \\
           --classical backtest_results/bt_report_COMI.CA_<ts>.json \\
           --output    eval_results/rl_vs_baseline_walkforward.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Project-root import path so this works from any CWD.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tradingagents.rl.walkforward import (
    compare_arms,
    default_risk_free_rate,
    write_comparison_report,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("rl.evaluation")


def _format_arm_summary(name: str, m) -> str:
    """Pretty one-liner for the console summary table."""
    if m is None:
        return f"  {name:<10}  (no report)"
    ci_lo = m.win_rate_ci_lo
    ci_hi = m.win_rate_ci_hi
    win_str = (
        f"{m.win_rate:.1%} (95% CI {ci_lo:.1%}-{ci_hi:.1%})"
        if m.win_rate is not None else "n/a"
    )
    return (
        f"  {name:<10}  ret={m.total_return_pct:+6.2f}%  ann={m.annualized_return_pct:+6.2f}%  "
        f"sharpe={m.sharpe_ratio:+6.3f}  calmar={m.calmar_ratio:+6.3f}  "
        f"maxdd={m.max_drawdown_pct:+6.2f}%  trades={m.n_trades_total:3d} "
        f"closed={m.n_trades_closed:3d}  win={win_str}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="Path to baseline report.json")
    parser.add_argument("--rl-meta", required=True, help="Path to RL-arm report.json")
    parser.add_argument("--classical", default=None, help="Optional classical bt_report.json")
    parser.add_argument("--output", required=True, help="Output comparison JSON path")
    parser.add_argument(
        "--risk-free-rate", type=float, default=None,
        help=f"Annual risk-free rate (default: {default_risk_free_rate():.3f}, "
             f"closes MEMORY §C3 on this eval path)",
    )
    parser.add_argument(
        "--sharpe-pass-threshold", type=float, default=0.20,
        help="Pre-registered Sharpe uplift threshold for PASS (default 0.20)",
    )
    parser.add_argument(
        "--n-bootstrap", type=int, default=5000,
        help="Bootstrap iterations for the 95%% CI on mean return difference",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="RNG seed for the bootstrap (default 42 for reproducibility)",
    )
    args = parser.parse_args(argv)

    for label, path in (("baseline", args.baseline), ("rl-meta", args.rl_meta)):
        if not Path(path).exists():
            logger.error("rl-eval: %s arm report missing: %s", label, path)
            return 2
    if args.classical and not Path(args.classical).exists():
        logger.error("rl-eval: classical report missing: %s", args.classical)
        return 2

    try:
        result = compare_arms(
            baseline_path=args.baseline,
            rl_meta_path=args.rl_meta,
            classical_path=args.classical,
            risk_free_rate=args.risk_free_rate,
            n_bootstrap=args.n_bootstrap,
            rng_seed=args.seed,
            sharpe_pass_threshold=args.sharpe_pass_threshold,
        )
    except FileNotFoundError as exc:
        logger.error("rl-eval: %s", exc)
        return 2

    # Console summary
    print()
    print("=" * 100)
    print(" RL META-POLICY WALK-FORWARD COMPARISON")
    print("=" * 100)
    print(_format_arm_summary("baseline", result.baseline))
    print(_format_arm_summary("rl_meta",  result.rl_meta))
    if result.classical is not None:
        print(_format_arm_summary("classical", result.classical))
    print("-" * 100)
    print(
        f"  sharpe uplift (rl - baseline) = {result.sharpe_uplift:+.3f} "
        f"(threshold: >= {result.sharpe_pass_threshold:+.2f})"
    )
    print(
        f"  return uplift (rl - baseline) = {result.return_uplift_pct:+.3f}%"
    )
    print(
        f"  bootstrap 95% CI on per-day mean return difference = "
        f"[{result.bootstrap_ci_95[0]:+.6f}, {result.bootstrap_ci_95[1]:+.6f}] "
        f"(n_bootstrap={result.n_bootstrap}, seed={result.rng_seed})"
    )
    print()
    if result.pre_registered_pass:
        print(f"  PRE-REGISTERED VERDICT: PASS -- {result.pre_registered_reason}")
    else:
        print(f"  PRE-REGISTERED VERDICT: FAIL -- {result.pre_registered_reason}")
        print("  -> flag remains default OFF; document negative result honestly.")
    print("=" * 100)

    out = write_comparison_report(result, args.output)
    logger.info("rl-eval: wrote comparison report to %s", out)
    return 0 if result.pre_registered_pass else 1


if __name__ == "__main__":
    sys.exit(main())
