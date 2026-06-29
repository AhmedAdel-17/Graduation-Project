#!/usr/bin/env python3
"""Jul-Dec 2024 out-of-sample validation runner.

Runs the R4 system (frozen code/prompts) on 9 tickers for the OOS window.
Always-BUY baseline is computed post-hoc from the same audit data.

Usage:
    python3 scripts/oos_juldec_run.py
"""

import subprocess
import sys
import time
import json
import glob
import os
from datetime import datetime
from pathlib import Path

TICKERS = [
    "COMI.CA", "ETEL.CA", "TMGH.CA", "EAST.CA", "FWRY.CA",
    "HRHO.CA", "SWDY.CA", "PHDC.CA", "ADIB.CA",
]

BASE_CMD = [
    sys.executable, "scripts/backtester.py",
    "--start", "2024-07-01",
    "--end", "2024-12-31",
    "--interval", "14",
    "--analysts", "market,fundamentals,news,social",
    "--benchmark", "EGX30",
]

TIMEOUT_SECONDS = 45 * 60
MAX_RETRIES = 1
RESULTS_DIR = Path("backtest_results")
LOG_FILE = RESULTS_DIR / "oos_juldec_run.log"


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def find_report(ticker: str, after_ts: float) -> str | None:
    pattern = str(RESULTS_DIR / f"report_{ticker}_*.json")
    candidates = [p for p in glob.glob(pattern) if os.path.getmtime(p) > after_ts]
    return max(candidates, key=os.path.getmtime) if candidates else None


def run_ticker(ticker: str) -> dict:
    cmd = BASE_CMD + ["--ticker", ticker]
    result = {
        "ticker": ticker, "status": "FAILED",
        "report_path": None, "attempts": 0,
        "error": None, "duration_min": 0,
    }

    for attempt in range(1, MAX_RETRIES + 2):
        result["attempts"] = attempt
        log(f"  Attempt {attempt}/{MAX_RETRIES + 1} for {ticker}")
        before_ts = time.time()

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=TIMEOUT_SECONDS,
                cwd=str(Path(__file__).resolve().parent.parent),
            )
            duration = (time.time() - before_ts) / 60
            result["duration_min"] = round(duration, 1)

            if proc.returncode == 0:
                report = find_report(ticker, before_ts)
                if report:
                    result["status"] = "OK"
                    result["report_path"] = report
                    log(f"  {ticker} completed in {duration:.1f} min -> {report}")
                    return result
                else:
                    result["error"] = "Process OK but no report file"
                    log(f"  {ticker} process OK but no report file")
            else:
                stderr_tail = (proc.stderr or "")[-500:]
                result["error"] = f"Exit code {proc.returncode}: {stderr_tail}"
                log(f"  {ticker} failed (exit {proc.returncode})")

        except subprocess.TimeoutExpired:
            duration = (time.time() - before_ts) / 60
            result["duration_min"] = round(duration, 1)
            result["error"] = f"Timeout after {TIMEOUT_SECONDS // 60} min"
            log(f"  {ticker} timed out after {duration:.1f} min")

        if attempt <= MAX_RETRIES:
            log(f"  Retrying {ticker} in 10 seconds...")
            time.sleep(10)

    log(f"  {ticker} FAILED after {result['attempts']} attempts: {result['error']}")
    return result


def main():
    log("=" * 60)
    log("OOS Jul-Dec 2024 validation run")
    log(f"Tickers: {', '.join(TICKERS)}")
    log(f"Config: interval=14, benchmark=EGX30, start=2024-07-01, end=2024-12-31")
    log(f"Timeout per ticker: {TIMEOUT_SECONDS // 60} min, max retries: {MAX_RETRIES}")
    log("=" * 60)

    results = []
    start_time = time.time()

    for i, ticker in enumerate(TICKERS, 1):
        log(f"\n[{i}/{len(TICKERS)}] Starting {ticker}")
        result = run_ticker(ticker)
        results.append(result)
        log(f"[{i}/{len(TICKERS)}] {ticker} -> {result['status']}")

    total_min = (time.time() - start_time) / 60
    log(f"\n{'=' * 60}")
    log(f"All tickers complete. Total time: {total_min:.1f} min")

    ok = sum(1 for r in results if r["status"] == "OK")
    failed = sum(1 for r in results if r["status"] != "OK")
    log(f"Succeeded: {ok}, Failed: {failed}")

    for r in results:
        log(f"  {r['ticker']:10s} {r['status']:6s} {r['duration_min']:5.1f} min  "
            f"attempts={r['attempts']}  report={r['report_path'] or 'NONE'}")

    manifest = {
        "run_started": datetime.now().isoformat(),
        "window": "2024-07-01 to 2024-12-31",
        "purpose": "OOS validation — does Jan-Jun finding generalize?",
        "total_duration_min": round(total_min, 1),
        "config": {
            "start": "2024-07-01", "end": "2024-12-31",
            "interval": 14, "benchmark": "EGX30",
            "analysts": "market,fundamentals,news,social",
        },
        "results": results,
    }
    manifest_path = RESULTS_DIR / "oos_juldec_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    log(f"Manifest saved to {manifest_path}")
    log("DONE")


if __name__ == "__main__":
    main()
