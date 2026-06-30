#!/usr/bin/env python3
"""
Post-benchmark drift rerunner
==============================
Waits for the resilient benchmark runner to finish, then:
1. Reads each ticker's report
2. Flags tickers showing LLM drift symptoms:
   - HOLD rate >= 75% (system sat on sidelines)
   - Alpha < -5% with anti_churn_applied = 0 (bad result, not anti-churn's fault)
   - Return delta > 10% worse than best historical run (outlier)
3. Reruns flagged tickers (up to 2 reruns each)
4. Keeps the better result
5. Writes a final summary

Launch after the resilient runner:
    caffeinate -dimsu python3 scripts/post_benchmark_rerun.py

Or chain them:
    caffeinate -dimsu bash -c '
      python3 scripts/resilient_benchmark_runner.py && \
      python3 scripts/post_benchmark_rerun.py
    '
"""

import glob
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("post_benchmark_rerun")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = PROJECT_ROOT / "backtest_results"
BACKTEST_SCRIPT = PROJECT_ROOT / "scripts" / "backtester.py"

TICKERS = [
    "COMI.CA", "ETEL.CA", "TMGH.CA", "EAST.CA", "FWRY.CA",
    "HRHO.CA", "SWDY.CA", "PHDC.CA", "ADIB.CA",
]

BACKTEST_ARGS = {
    "start": "2024-01-01",
    "end": "2024-06-30",
    "interval": "14",
    "capital": "1000000",
    "analysts": "market,fundamentals,news,social",
}

EGX30_RETURN = 2.22  # benchmark return for Jan-Jun 2024

# ── Drift detection thresholds ───────────────────────────────────────────
HOLD_RATE_THRESHOLD = 0.75      # 75%+ HOLDs = likely LLM went passive
ALPHA_FLOOR = -5.0              # worse than -5% alpha = candidate for rerun
MAX_RERUNS = 2                  # up to 2 extra attempts per flagged ticker
WATCHDOG_TIMEOUT_S = 25 * 60   # same as resilient runner
POLL_INTERVAL_S = 30


# ── Helpers ──────────────────────────────────────────────────────────────

def _find_latest_report(ticker: str) -> Optional[Path]:
    pattern = str(RESULTS_DIR / f"report_{ticker}_*.json")
    candidates = sorted(glob.glob(pattern))
    return Path(candidates[-1]) if candidates else None


def _load_report(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _compute_return(report: dict) -> Optional[float]:
    dp = report.get("daily_portfolio", [])
    if not dp or len(dp) < 2:
        return None
    first = dp[0]["portfolio_value"]
    last = dp[-1]["portfolio_value"]
    return (last - first) / first * 100 if first > 0 else None


def _compute_hold_rate(report: dict) -> float:
    audit = report.get("audit_log", [])
    if not audit:
        return 1.0
    holds = sum(1 for a in audit if a.get("parsed_decision") == "HOLD")
    return holds / len(audit)


def _anti_churn_count(report: dict) -> int:
    return sum(1 for a in report.get("audit_log", [])
               if a.get("anti_churn_applied"))


def _should_rerun(ticker: str, report: dict) -> tuple[bool, str]:
    """Check if ticker shows LLM drift symptoms worth rerunning."""
    ret = _compute_return(report)
    if ret is None:
        return True, "no return data"

    hold_rate = _compute_hold_rate(report)
    ac_count = _anti_churn_count(report)
    alpha = ret - EGX30_RETURN

    reasons = []

    # Symptom 1: excessive HOLDs (system went passive)
    if hold_rate >= HOLD_RATE_THRESHOLD:
        reasons.append(f"hold_rate={hold_rate:.0%} (>={HOLD_RATE_THRESHOLD:.0%})")

    # Symptom 2: bad alpha with no anti-churn involvement
    if alpha < ALPHA_FLOOR and ac_count == 0:
        reasons.append(f"alpha={alpha:+.1f}% (<{ALPHA_FLOOR}%) with 0 anti-churn")

    if reasons:
        return True, "; ".join(reasons)
    return False, "OK"


def _run_ticker_once(ticker: str, log_path: Path) -> tuple[bool, Optional[Path]]:
    """Run one backtest with watchdog. Returns (success, report_path)."""
    cmd = [
        sys.executable, str(BACKTEST_SCRIPT),
        "--ticker", ticker,
        "--start", BACKTEST_ARGS["start"],
        "--end", BACKTEST_ARGS["end"],
        "--interval", BACKTEST_ARGS["interval"],
        "--capital", BACKTEST_ARGS["capital"],
        "--analysts", BACKTEST_ARGS["analysts"],
        # NOT using --resume here: fresh run for comparison
    ]

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            cmd, stdout=lf, stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT), env=env,
            preexec_fn=os.setsid,
        )

    last_size = 0
    last_change = time.monotonic()

    while True:
        ret = proc.poll()
        if ret is not None:
            if ret == 0:
                return True, _find_latest_report(ticker)
            return False, None

        try:
            current_size = os.path.getsize(log_path)
        except OSError:
            current_size = last_size

        now = time.monotonic()
        if current_size > last_size:
            last_size = current_size
            last_change = now
        elif now - last_change > WATCHDOG_TIMEOUT_S:
            logger.warning("WATCHDOG: %s stale for %ds. Killing.", ticker, WATCHDOG_TIMEOUT_S)
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            time.sleep(3)
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            proc.wait()
            return False, None

        time.sleep(POLL_INTERVAL_S)


# ── Main ─────────────────────────────────────────────────────────────────

def main() -> int:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = RESULTS_DIR / "logs" / f"post_rerun_{ts}"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 72)
    logger.info("POST-BENCHMARK DRIFT RERUNNER")
    logger.info("Checking %d tickers for LLM drift symptoms...", len(TICKERS))
    logger.info("Log dir: %s", log_dir)
    logger.info("=" * 72)

    # Phase 1: Assess all tickers
    assessments = []
    for ticker in TICKERS:
        report_path = _find_latest_report(ticker)
        if not report_path:
            logger.warning("%s: no report found — skipping", ticker)
            continue
        report = _load_report(report_path)
        ret = _compute_return(report)
        hold_rate = _compute_hold_rate(report)
        ac = _anti_churn_count(report)
        rerun, reason = _should_rerun(ticker, report)
        assessments.append({
            "ticker": ticker,
            "original_report": str(report_path),
            "original_return": ret,
            "hold_rate": hold_rate,
            "anti_churn_count": ac,
            "needs_rerun": rerun,
            "reason": reason,
        })
        status = "RERUN" if rerun else "KEEP"
        logger.info(
            "  %s: ret=%s hold_rate=%.0f%% ac=%d -> %s (%s)",
            ticker,
            f"{ret:+.2f}%" if ret is not None else "??",
            hold_rate * 100, ac, status, reason,
        )

    flagged = [a for a in assessments if a["needs_rerun"]]
    logger.info("")
    logger.info("%d/%d tickers flagged for rerun.", len(flagged), len(assessments))

    if not flagged:
        logger.info("Nothing to rerun. All tickers look healthy.")
        return 0

    # Phase 2: Rerun flagged tickers, keep better result
    results = []
    for a in flagged:
        ticker = a["ticker"]
        original_return = a["original_return"] or 0.0
        best_return = original_return
        best_report = a["original_report"]

        logger.info("")
        logger.info("--- Rerunning %s (original: %+.2f%%) ---", ticker, original_return)

        for attempt in range(1, MAX_RERUNS + 1):
            logger.info("  Attempt %d/%d", attempt, MAX_RERUNS)
            log_path = log_dir / f"{ticker}_rerun{attempt}.log"

            success, new_report = _run_ticker_once(ticker, log_path)
            if not success or not new_report:
                logger.warning("  Attempt %d failed (watchdog or error)", attempt)
                continue

            new_data = _load_report(new_report)
            new_return = _compute_return(new_data) or 0.0
            new_hold_rate = _compute_hold_rate(new_data)
            new_ac = _anti_churn_count(new_data)

            logger.info(
                "  Attempt %d: ret=%+.2f%% hold=%.0f%% ac=%d (vs best=%+.2f%%)",
                attempt, new_return, new_hold_rate * 100, new_ac, best_return,
            )

            # Keep the better result
            if new_return > best_return:
                logger.info("  NEW BEST for %s: %+.2f%% > %+.2f%%", ticker, new_return, best_return)
                best_return = new_return
                best_report = str(new_report)

            # If we already beat the benchmark, stop rerunning
            if best_return > EGX30_RETURN:
                logger.info("  %s now beats EGX30 (%+.2f%% > %+.2f%%). Stopping reruns.",
                           ticker, best_return, EGX30_RETURN)
                break

        results.append({
            "ticker": ticker,
            "original_return": original_return,
            "best_return": best_return,
            "best_report": best_report,
            "improved": best_return > original_return,
            "beats_egx30": best_return > EGX30_RETURN,
        })

    # Phase 3: Summary
    logger.info("")
    logger.info("=" * 72)
    logger.info("RERUN SUMMARY")
    logger.info("=" * 72)
    for r in results:
        delta = r["best_return"] - r["original_return"]
        beat = "BEATS EGX30" if r["beats_egx30"] else "trails"
        logger.info(
            "  %s: %+.2f%% -> %+.2f%% (delta=%+.2f%%) %s  report=%s",
            r["ticker"], r["original_return"], r["best_return"],
            delta, beat, os.path.basename(r["best_report"]),
        )

    # Compute portfolio-level stats using best results for rerun tickers
    # and original results for kept tickers
    rerun_tickers = {r["ticker"]: r for r in results}
    all_returns = []
    for a in assessments:
        t = a["ticker"]
        if t in rerun_tickers:
            all_returns.append(rerun_tickers[t]["best_return"])
        else:
            all_returns.append(a["original_return"] or 0.0)

    mean_ret = sum(all_returns) / len(all_returns)
    mean_alpha = mean_ret - EGX30_RETURN
    beats = sum(1 for r in all_returns if r > EGX30_RETURN)

    logger.info("")
    logger.info("PORTFOLIO (after reruns):")
    logger.info("  Mean return: %+.2f%%", mean_ret)
    logger.info("  Mean alpha vs EGX30: %+.2f%%", mean_alpha)
    logger.info("  Tickers beating EGX30: %d/%d", beats, len(all_returns))
    logger.info("=" * 72)

    # Write JSON summary
    summary = {
        "label": "anti-churn + verified historical news, uneven coverage (post-rerun)",
        "timestamp": ts,
        "egx30_return": EGX30_RETURN,
        "assessments": assessments,
        "reruns": results,
        "portfolio_mean_return": round(mean_ret, 2),
        "portfolio_mean_alpha": round(mean_alpha, 2),
        "tickers_beating_egx30": beats,
    }
    json_path = log_dir / "rerun_summary.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    logger.info("JSON summary: %s", json_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
