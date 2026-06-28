"""
Thesis backtest orchestrator (Chapters 7 & 8)
=============================================
Drives ``scripts/backtester.py`` across a ticker × decision-profile matrix and
writes a manifest that ``scripts/thesis_report.py`` consumes to build the pooled
tables and charts.

Two profiles (see CLAUDE.md / the engine's --profile flag):

  * ``live_faithful`` — untouched live decision logic. PRIMARY result.
    Run on the full ticker set → decision-quality evidence.
  * ``tuned`` — disclosed sensitivity config (lower required-return assumption
    in the decision context ONLY; Sharpe/metrics risk-free rate unchanged).
    Run on a few liquid names → P&L illustration.

Backtests run with ``market,fundamentals`` analysts only (news/social have no
historical archive and are auto-dropped by the engine). EGX30 is the benchmark.

Usage
-----
    # Recommended "small budget" thesis batch (~60 live decisions):
    python scripts/run_thesis_backtests.py \
        --tickers COMI.CA,ETEL.CA,TMGH.CA,ABUK.CA,SWDY.CA,EFID.CA \
        --tuned-tickers COMI.CA,ETEL.CA,TMGH.CA \
        --start 2024-01-01 --end 2024-06-30 --interval 14 --resume

    # See the plan + time estimate without running anything:
    python scripts/run_thesis_backtests.py --dry-run

Resumable: ``--resume`` is passed through to the per-ticker backtester (it skips
already-evaluated dates from its on-disk checkpoint), so an interrupted overnight
batch continues where it stopped.
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("run_thesis_backtests")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_RESULTS_DIR = PROJECT_ROOT / "backtest_results"
THESIS_RESULTS_DIR = PROJECT_ROOT / "thesis_results"

DEFAULT_TICKERS = "COMI.CA,ETEL.CA,TMGH.CA,ABUK.CA,SWDY.CA,EFID.CA"
DEFAULT_TUNED = "COMI.CA,ETEL.CA,TMGH.CA"


def _latest_report_for_ticker(ticker: str) -> Optional[Path]:
    cands = sorted(glob.glob(str(BACKTEST_RESULTS_DIR / f"report_{ticker}_*.json")))
    return Path(cands[-1]) if cands else None


def _report_profile(path: Path) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return (d.get("run_config") or {}).get("decision_profile")
    except Exception:
        return None


def _report_matches(path: Path, start: str, end: str, profile: str) -> bool:
    """A previous report satisfies this run only if BOTH its profile AND its
    REQUESTED window (run_config.start_date/end_date) match exactly. Using the
    requested window — not the equity-curve span — avoids a short report (e.g. a
    2-week smoke run) falsely "covering" a 6-month request just because its dates
    fall inside the range."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return False
    rc = d.get("run_config") or {}
    if rc.get("decision_profile") != profile:
        return False
    return str(rc.get("start_date") or "") == start and str(rc.get("end_date") or "") == end


def _n_eval_dates(start: str, end: str, interval: int) -> int:
    """Mirror the engine's EGX-trading-day stepping to estimate decision count."""
    from datetime import datetime as _dt, timedelta as _td

    def is_trading(d):
        return d.weekday() in (0, 1, 2, 3, 6)

    cur = _dt.strptime(start, "%Y-%m-%d")
    end_dt = _dt.strptime(end, "%Y-%m-%d")
    n = 0
    while cur <= end_dt:
        while not is_trading(cur):
            cur += _td(days=1)
        if cur <= end_dt:
            n += 1
        cur += _td(days=interval)
    return n


def run_matrix(args: argparse.Namespace) -> Path:
    THESIS_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    tuned_tickers = [t.strip() for t in args.tuned_tickers.split(",") if t.strip()] if args.tuned_tickers else []

    jobs: List[dict] = []
    for t in tickers:
        jobs.append({"ticker": t, "profile": "live_faithful"})
    for t in tuned_tickers:
        jobs.append({"ticker": t, "profile": "tuned"})

    per_run = _n_eval_dates(args.start, args.end, args.interval)
    est_min = len(jobs) * per_run * args.minutes_per_decision
    logger.info(
        "Matrix: %d runs (%d live_faithful + %d tuned) × ~%d eval dates each "
        "≈ %d decisions. Est. wall-clock ≈ %.1f h (@ %.1f min/decision).",
        len(jobs), len(tickers), len(tuned_tickers), per_run,
        len(jobs) * per_run, est_min / 60.0, args.minutes_per_decision,
    )
    for j in jobs:
        logger.info("  - %-10s  %s", j["profile"], j["ticker"])

    if args.dry_run:
        logger.info("--dry-run: not executing.")
        return THESIS_RESULTS_DIR / "manifest_DRYRUN.json"

    from scripts.backtester import BacktestingEngine

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "window": {"start": args.start, "end": args.end, "interval_days": args.interval},
        "capital": args.capital,
        "analysts": ["market", "fundamentals"],
        "benchmark": "EGX30",
        "runs": [],
    }

    for j in jobs:
        ticker, profile = j["ticker"], j["profile"]
        # Skip if a matching report already exists (unless --force).
        if not args.force:
            existing = _latest_report_for_ticker(ticker)
            if existing and _report_matches(existing, args.start, args.end, profile):
                logger.info("[SKIP] %s/%s — existing report %s covers it.",
                            ticker, profile, existing.name)
                manifest["runs"].append({
                    "ticker": ticker, "profile": profile,
                    "report": str(existing), "status": "skipped_existing",
                })
                continue

        logger.info("=" * 70)
        logger.info("[RUN] %s | profile=%s | %s → %s", ticker, profile, args.start, args.end)
        try:
            engine = BacktestingEngine(
                initial_capital=float(args.capital),
                benchmark_ticker="^EGX30",
                decision_profile=profile,
                decision_rfr_override=args.decision_rfr,
            )
            engine.run_backtest(
                ticker, args.start, args.end,
                interval_days=args.interval,
                analysts=["market", "fundamentals"],
                cooldown=args.cooldown,
                train_end_date=None,
                resume=args.resume,
            )
            report = _latest_report_for_ticker(ticker)
            manifest["runs"].append({
                "ticker": ticker, "profile": profile,
                "report": str(report) if report else None,
                "status": "ok" if report else "no_report",
            })
        except KeyboardInterrupt:
            logger.warning("Interrupted — writing partial manifest. Re-run with --resume.")
            break
        except Exception as exc:
            logger.exception("Run failed for %s/%s: %s", ticker, profile, exc)
            manifest["runs"].append({
                "ticker": ticker, "profile": profile,
                "report": None, "status": f"error:{type(exc).__name__}",
            })

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest_path = THESIS_RESULTS_DIR / f"manifest_{ts}.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    logger.info("Manifest → %s", manifest_path)
    logger.info(
        "Next: python scripts/thesis_report.py --manifest %s", manifest_path
    )
    return manifest_path


def main() -> int:
    p = argparse.ArgumentParser(description="Run the thesis backtest matrix.")
    p.add_argument("--tickers", type=str, default=DEFAULT_TICKERS,
                   help="Comma-separated tickers for the live_faithful (primary) runs.")
    p.add_argument("--tuned-tickers", type=str, default=DEFAULT_TUNED,
                   help="Comma-separated subset to ALSO run under the tuned profile "
                        "(P&L illustration). Pass '' to skip tuned runs.")
    p.add_argument("--start", type=str, default="2024-01-01")
    p.add_argument("--end", type=str, default="2024-06-30")
    p.add_argument("--interval", type=int, default=14,
                   help="Calendar days between agent evaluations.")
    p.add_argument("--capital", type=float, default=1_000_000.0)
    p.add_argument("--cooldown", type=int, default=5)
    p.add_argument("--decision-rfr", type=float, default=None,
                   help="Explicit decision-RFR override for the tuned profile "
                        "(default 0.12 / BACKTEST_DECISION_RFR).")
    p.add_argument("--resume", action="store_true",
                   help="Pass --resume to the per-ticker backtester.")
    p.add_argument("--force", action="store_true",
                   help="Re-run even if a matching report already exists.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the matrix + time estimate without running.")
    p.add_argument("--minutes-per-decision", type=float, default=5.0,
                   help="For the wall-clock estimate only.")
    args = p.parse_args()
    run_matrix(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
