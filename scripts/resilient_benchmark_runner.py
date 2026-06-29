#!/usr/bin/env python3
"""
Resilient unattended benchmark runner
======================================
Runs each ticker one-by-one via subprocess, with:
  - --resume to continue from partial checkpoints
  - Watchdog: kills ticker if no log output for WATCHDOG_TIMEOUT_S
  - Up to MAX_RETRIES per ticker
  - caffeinate-compatible (launch with: caffeinate -dimsu python3 scripts/resilient_benchmark_runner.py)
  - Timestamped per-run log directory
  - Final summary of completed/failed/retries/report paths

Label: anti-churn + verified historical news, uneven coverage
"""

import glob
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────

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

MAX_RETRIES = 3
WATCHDOG_TIMEOUT_S = 25 * 60  # 25 minutes with no output = stuck
POLL_INTERVAL_S = 30          # check log file every 30 seconds

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_SCRIPT = PROJECT_ROOT / "scripts" / "backtester.py"
RESULTS_DIR = PROJECT_ROOT / "backtest_results"

# ── Helpers ──────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _find_latest_report(ticker: str) -> str | None:
    pattern = str(RESULTS_DIR / f"report_{ticker}_*.json")
    candidates = sorted(glob.glob(pattern))
    return candidates[-1] if candidates else None


def _log(log_f, msg: str) -> None:
    line = f"[{_ts()}] {msg}"
    print(line, flush=True)
    log_f.write(line + "\n")
    log_f.flush()


# ── Per-ticker runner ────────────────────────────────────────────────────

def run_ticker(
    ticker: str,
    log_dir: Path,
    summary_log,
    attempt: int,
) -> tuple[bool, str | None]:
    """Run one ticker backtest with watchdog. Returns (success, report_path)."""

    ticker_log = log_dir / f"{ticker}_attempt{attempt}.log"
    _log(summary_log, f"  Attempt {attempt}/{MAX_RETRIES} for {ticker}")
    _log(summary_log, f"  Log: {ticker_log}")

    cmd = [
        sys.executable, str(BACKTEST_SCRIPT),
        "--ticker", ticker,
        "--start", BACKTEST_ARGS["start"],
        "--end", BACKTEST_ARGS["end"],
        "--interval", BACKTEST_ARGS["interval"],
        "--capital", BACKTEST_ARGS["capital"],
        "--analysts", BACKTEST_ARGS["analysts"],
        "--resume",
    ]

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    with open(ticker_log, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            cmd,
            stdout=lf,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
            env=env,
            # New process group so we can kill cleanly
            preexec_fn=os.setsid,
        )

    _log(summary_log, f"  PID: {proc.pid}")

    # Watchdog loop: poll the log file size
    last_size = 0
    last_change = time.monotonic()

    while True:
        ret = proc.poll()
        if ret is not None:
            # Process finished
            if ret == 0:
                _log(summary_log, f"  {ticker} finished OK (exit 0)")
                report = _find_latest_report(ticker)
                return True, report
            else:
                _log(summary_log, f"  {ticker} exited with code {ret}")
                return False, None

        # Check log file growth
        try:
            current_size = os.path.getsize(ticker_log)
        except OSError:
            current_size = last_size

        now = time.monotonic()
        if current_size > last_size:
            last_size = current_size
            last_change = now
        else:
            stale_seconds = now - last_change
            if stale_seconds > WATCHDOG_TIMEOUT_S:
                _log(summary_log,
                     f"  WATCHDOG: {ticker} stale for {stale_seconds:.0f}s "
                     f"(>{WATCHDOG_TIMEOUT_S}s). Killing PID {proc.pid}.")
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
                _log(summary_log, f"  {ticker} killed by watchdog.")
                return False, None

        time.sleep(POLL_INTERVAL_S)


# ── Main ─────────────────────────────────────────────────────────────────

def main() -> int:
    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = RESULTS_DIR / "logs" / f"antichurn_news_{run_ts}"
    log_dir.mkdir(parents=True, exist_ok=True)

    summary_path = log_dir / "summary.log"
    summary_f = open(summary_path, "w", encoding="utf-8")

    _log(summary_f, "=" * 72)
    _log(summary_f, "RESILIENT BENCHMARK RUNNER")
    _log(summary_f, "Label: anti-churn + verified historical news, uneven coverage")
    _log(summary_f, f"Tickers: {', '.join(TICKERS)}")
    _log(summary_f, f"Window: {BACKTEST_ARGS['start']} -> {BACKTEST_ARGS['end']}")
    _log(summary_f, f"Interval: {BACKTEST_ARGS['interval']} days")
    _log(summary_f, f"Capital: {BACKTEST_ARGS['capital']} EGP")
    _log(summary_f, f"Analysts: {BACKTEST_ARGS['analysts']}")
    _log(summary_f, f"Max retries: {MAX_RETRIES}")
    _log(summary_f, f"Watchdog timeout: {WATCHDOG_TIMEOUT_S}s")
    _log(summary_f, f"Log dir: {log_dir}")
    _log(summary_f, "=" * 72)

    t0 = time.monotonic()

    completed: list[tuple[str, str, int]] = []   # (ticker, report_path, retries)
    failed: list[tuple[str, int]] = []            # (ticker, retries)

    for ticker in TICKERS:
        _log(summary_f, "")
        _log(summary_f, f"--- {ticker} ---")

        # Check if a report already exists from a completed run in THIS session
        # (only skip if we know it's from after our session started)
        success = False
        report_path = None
        retries = 0

        for attempt in range(1, MAX_RETRIES + 1):
            retries = attempt
            success, report_path = run_ticker(ticker, log_dir, summary_f, attempt)
            if success:
                break
            _log(summary_f, f"  Retry {attempt}/{MAX_RETRIES} failed for {ticker}")
            if attempt < MAX_RETRIES:
                _log(summary_f, f"  Waiting 10s before retry...")
                time.sleep(10)

        if success and report_path:
            _log(summary_f, f"  COMPLETED: {ticker} -> {report_path} (retries: {retries})")
            completed.append((ticker, report_path, retries))
        else:
            _log(summary_f, f"  FAILED: {ticker} after {retries} attempts")
            failed.append((ticker, retries))

    elapsed = time.monotonic() - t0
    elapsed_h = elapsed / 3600

    # ── Final summary ────────────────────────────────────────────────
    _log(summary_f, "")
    _log(summary_f, "=" * 72)
    _log(summary_f, "FINAL SUMMARY")
    _log(summary_f, "=" * 72)
    _log(summary_f, f"Total runtime: {elapsed_h:.1f}h ({elapsed:.0f}s)")
    _log(summary_f, f"Completed: {len(completed)}/{len(TICKERS)}")
    _log(summary_f, f"Failed: {len(failed)}/{len(TICKERS)}")
    _log(summary_f, "")

    _log(summary_f, "Completed tickers:")
    for ticker, rpath, retries in completed:
        _log(summary_f, f"  {ticker:12s} retries={retries}  report={rpath}")

    if failed:
        _log(summary_f, "")
        _log(summary_f, "Failed tickers:")
        for ticker, retries in failed:
            _log(summary_f, f"  {ticker:12s} retries={retries}")

    _log(summary_f, "")
    _log(summary_f, f"Log directory: {log_dir}")
    _log(summary_f, f"Summary log: {summary_path}")
    _log(summary_f, "=" * 72)

    # Also write a structured JSON summary
    json_summary = {
        "label": "anti-churn + verified historical news, uneven coverage",
        "run_timestamp": run_ts,
        "config": BACKTEST_ARGS,
        "tickers": TICKERS,
        "total_runtime_h": round(elapsed_h, 2),
        "completed": [
            {"ticker": t, "report": r, "retries": n}
            for t, r, n in completed
        ],
        "failed": [
            {"ticker": t, "retries": n}
            for t, n in failed
        ],
    }
    json_path = log_dir / "summary.json"
    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(json_summary, jf, indent=2)

    _log(summary_f, f"JSON summary: {json_path}")
    summary_f.close()

    # Print to stdout too
    print()
    print("=" * 72)
    print(f"DONE. {len(completed)}/{len(TICKERS)} completed, "
          f"{len(failed)} failed. Runtime: {elapsed_h:.1f}h")
    print(f"Logs: {log_dir}")
    print(f"Summary: {summary_path}")
    print("=" * 72)

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
