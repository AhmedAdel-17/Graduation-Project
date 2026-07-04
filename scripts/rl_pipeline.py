"""End-to-end driver for the RL decision policy: backtests → dataset → train → eval.

This is the orchestrator we use to turn the multi-agent committee's historical
decisions into a trained decision-calibration policy. It runs three phases:

  1. BACKTESTS  — run ``scripts/backtester.py`` across a ticker universe to
                  produce ``backtest_results/report_<TICKER>_*.json`` files.
                  Each report records, per decision, the committee's final
                  state + the realized forward price (the raw material for the
                  counterfactual reward). The RL policy itself stays OFF here —
                  we only need the committee's decisions and the prices.
  2. DATASET    — call ``scripts/generate_rl_training_data.py`` to ingest those
                  reports into a counterfactual (state, reward_buy/hold/sell)
                  parquet (schema ``rl_dataset_v2``).
  3. TRAIN+EVAL — call ``scripts/train_rl_policy.py`` to fit the Conservative
                  Q-Learning decision policy and write the model + card +
                  off-policy / counterfactual eval report.

Phase 1 makes real LLM calls (DeepSeek) and is the slow/expensive part. It is
crash-isolated per ticker and each ticker backtest is ``--resume``-able, so you
can stop and restart. Phases 2–3 are fast, offline, and deterministic.

Examples
--------
Plan only (no work), to estimate scope::

    python scripts/rl_pipeline.py --dry-run \\
        --tickers COMI.CA,ETEL.CA,TMGH.CA,ABUK.CA,SWDY.CA \\
        --start 2023-01-01 --end 2024-01-01 --interval 14

Full run (backtests + dataset + train)::

    python scripts/rl_pipeline.py \\
        --tickers COMI.CA,ETEL.CA,TMGH.CA,ABUK.CA,SWDY.CA,EFID.CA \\
        --start 2023-01-01 --end 2024-01-01 --interval 14 \\
        --analysts market,fundamentals --resume

Reuse existing reports (skip the slow phase) and just (re)build + train::

    python scripts/rl_pipeline.py --tickers COMI.CA,ETEL.CA --skip-backtests \\
        --cql-alpha 0.01 --epochs 200
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Add project root so we can import sibling scripts regardless of CWD.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("rl.pipeline")

_RESULTS_DIR = Path(_ROOT) / "backtest_results"


def _parse_csv_list(value: str) -> List[str]:
    return [t.strip() for t in value.split(",") if t.strip()]


def _reports_for_ticker(ticker: str, *, since: Optional[float] = None) -> List[Path]:
    """All report JSONs for a ticker, optionally only those modified after ``since``."""
    matches = sorted(_RESULTS_DIR.glob(f"report_{ticker}_*.json"))
    if since is not None:
        matches = [p for p in matches if p.stat().st_mtime >= since - 1.0]
    return matches


def _newest_report_for_ticker(ticker: str, *, since: Optional[float] = None) -> Optional[Path]:
    reports = _reports_for_ticker(ticker, since=since)
    if not reports:
        return None
    return max(reports, key=lambda p: p.stat().st_mtime)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — backtests
# ─────────────────────────────────────────────────────────────────────────────


def run_backtests(
    tickers: List[str],
    *,
    start: str,
    end: str,
    interval: int,
    analysts: str,
    benchmark: str,
    cooldown: int,
    resume: bool,
    dry_run: bool,
) -> List[Path]:
    """Run one backtest per ticker (subprocess). Returns the produced report paths."""
    produced: List[Path] = []
    for i, ticker in enumerate(tickers, 1):
        cmd = [
            sys.executable, os.path.join(_ROOT, "scripts", "backtester.py"),
            "--ticker", ticker,
            "--start", start,
            "--end", end,
            "--interval", str(interval),
            "--analysts", analysts,
            "--benchmark", benchmark,
            "--cooldown", str(cooldown),
        ]
        if resume:
            cmd.append("--resume")

        logger.info("[%d/%d] backtest %s  (%s → %s, every %dd)",
                    i, len(tickers), ticker, start, end, interval)
        if dry_run:
            logger.info("       DRY-RUN cmd: %s", " ".join(cmd))
            continue

        t0 = time.time()
        try:
            # Stream the child's output so the operator sees progress live.
            ret = subprocess.call(cmd, cwd=_ROOT)
        except KeyboardInterrupt:
            logger.warning("       interrupted; partial report is --resume-able. Stopping phase 1.")
            raise
        if ret != 0:
            logger.warning("       backtest for %s exited %d — continuing with the rest", ticker, ret)
            # A crash mid-run may still have written a partial report; pick it up if present.
        report = _newest_report_for_ticker(ticker, since=t0)
        if report is not None:
            produced.append(report)
            logger.info("       report: %s", report.name)
        else:
            logger.warning("       no report produced for %s", ticker)
    return produced


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — dataset
# ─────────────────────────────────────────────────────────────────────────────


def build_dataset(
    report_paths: List[Path],
    *,
    universe: List[str],
    output_parquet: Path,
    horizon_days: int,
    dry_run: bool,
) -> bool:
    """Build the counterfactual parquet from the report JSONs. Returns success."""
    from scripts.generate_rl_training_data import main as gen_main

    globs = [str(p) for p in report_paths]
    argv = [
        "--output", str(output_parquet),
        "--json-reports", *globs,
        "--universe", ",".join(universe),
        "--horizon-days", str(horizon_days),
        "--prefer", "json",
    ]
    logger.info("dataset: generate_rl_training_data %s", " ".join(argv[:6]) + " …")
    if dry_run:
        logger.info("       DRY-RUN (would ingest %d report file(s))", len(globs))
        return True
    rc = gen_main(argv)
    if rc != 0:
        logger.error("dataset: generation failed (rc=%d)", rc)
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — train + eval
# ─────────────────────────────────────────────────────────────────────────────


def train_policy(
    *,
    dataset_parquet: Path,
    output_model: Path,
    cql_alpha: Optional[float],
    epochs: Optional[int],
    seed: Optional[int],
    val_fraction: Optional[float],
    dry_run: bool,
) -> bool:
    from scripts.train_rl_policy import main as train_main

    argv = ["--dataset", str(dataset_parquet), "--output", str(output_model), "--force"]
    if cql_alpha is not None:
        argv += ["--cql-alpha", str(cql_alpha)]
    if epochs is not None:
        argv += ["--epochs", str(epochs)]
    if seed is not None:
        argv += ["--seed", str(seed)]
    if val_fraction is not None:
        argv += ["--val-fraction", str(val_fraction)]
    logger.info("train: train_rl_policy %s", " ".join(argv))
    if dry_run:
        logger.info("       DRY-RUN (would train + write %s + .card.json + .eval.json)", output_model)
        return True
    rc = train_main(argv)
    if rc != 0:
        logger.error("train: training failed (rc=%d)", rc)
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tickers", type=_parse_csv_list, required=True,
                        help="Comma-separated EGX ticker universe (e.g. COMI.CA,ETEL.CA,TMGH.CA).")
    parser.add_argument("--start", default="2023-01-01", help="Backtest start (YYYY-MM-DD).")
    parser.add_argument("--end", default="2024-01-01", help="Backtest end (YYYY-MM-DD).")
    parser.add_argument("--interval", type=int, default=14,
                        help="Trading-day cadence between decisions. Default 14.")
    parser.add_argument("--analysts", default="market,fundamentals",
                        help="Analyst fan-out for the backtests. Fewer analysts = faster/cheaper. "
                             "Default: market,fundamentals.")
    parser.add_argument("--benchmark", default="none",
                        help="Benchmark ticker, or 'none' to skip benchmark alignment. Default none.")
    parser.add_argument("--cooldown", type=int, default=5, help="Days between trades. Default 5.")
    parser.add_argument("--horizon-days", type=int, default=20,
                        help="Reward horizon in trading days (forward-return window). Default 20.")
    parser.add_argument("--resume", action="store_true",
                        help="Pass --resume to each backtest so partial runs continue.")
    parser.add_argument("--skip-backtests", action="store_true",
                        help="Skip phase 1; reuse existing backtest_results/report_<ticker>_*.json.")
    parser.add_argument("--output-dir", default="data/rl",
                        help="Where to write the dataset parquet. Default data/rl.")
    parser.add_argument("--model-out", default="models/rl_decision.pt",
                        help="Output path for the trained policy. Default models/rl_decision.pt.")
    parser.add_argument("--cql-alpha", type=float, default=None,
                        help="Override CQL conservatism (default 0.01 from config; higher = more "
                             "deference to the committee).")
    parser.add_argument("--epochs", type=int, default=None, help="Training epochs override.")
    parser.add_argument("--seed", type=int, default=None, help="Training seed override.")
    parser.add_argument("--val-fraction", type=float, default=None, help="Validation fraction override.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan (commands + estimated work) and exit without running.")
    parser.add_argument("--stop-after", choices=("backtests", "dataset", "train"), default="train",
                        help="Stop after this phase. Default: run everything.")
    args = parser.parse_args(argv)

    tickers = args.tickers
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    dataset_parquet = out_dir / f"training_{stamp}.parquet"
    model_out = Path(args.model_out)

    logger.info("=" * 72)
    logger.info("RL pipeline: %d ticker(s) | %s → %s | interval=%dd | analysts=%s",
                len(tickers), args.start, args.end, args.interval, args.analysts)
    logger.info("dataset → %s | model → %s | stop-after=%s%s",
                dataset_parquet, model_out, args.stop_after,
                " | DRY-RUN" if args.dry_run else "")
    logger.info("=" * 72)

    # ── Phase 1: backtests ────────────────────────────────────────────────────
    report_paths: List[Path] = []
    if args.skip_backtests:
        for t in tickers:
            r = _newest_report_for_ticker(t)
            if r is not None:
                report_paths.append(r)
            else:
                logger.warning("skip-backtests: no existing report for %s", t)
        logger.info("phase 1 skipped; reusing %d existing report(s)", len(report_paths))
    else:
        report_paths = run_backtests(
            tickers, start=args.start, end=args.end, interval=args.interval,
            analysts=args.analysts, benchmark=args.benchmark, cooldown=args.cooldown,
            resume=args.resume, dry_run=args.dry_run,
        )

    if args.stop_after == "backtests":
        logger.info("stopping after phase 1 (%d report(s))", len(report_paths))
        return 0

    if not args.dry_run and not report_paths:
        logger.error("no backtest reports available; cannot build a dataset. "
                     "Run phase 1 first, or pass --skip-backtests with existing reports.")
        return 1

    # ── Phase 2: dataset ──────────────────────────────────────────────────────
    ok = build_dataset(
        report_paths, universe=tickers, output_parquet=dataset_parquet,
        horizon_days=args.horizon_days, dry_run=args.dry_run,
    )
    if not ok:
        return 2
    if args.stop_after == "dataset":
        logger.info("stopping after phase 2; dataset at %s", dataset_parquet)
        return 0

    # ── Phase 3: train + eval ─────────────────────────────────────────────────
    ok = train_policy(
        dataset_parquet=dataset_parquet, output_model=model_out,
        cql_alpha=args.cql_alpha, epochs=args.epochs, seed=args.seed,
        val_fraction=args.val_fraction, dry_run=args.dry_run,
    )
    if not ok:
        return 3

    if not args.dry_run:
        logger.info("=" * 72)
        logger.info("DONE. To use the policy as a parallel arm in a backtest:")
        logger.info("  python scripts/backtester.py --ticker COMI.CA --start ... --end ... \\")
        logger.info("      --rl-model %s", model_out)
        logger.info("Eval report: %s.eval.json | model card: %s.card.json", model_out, model_out)
        logger.info("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
