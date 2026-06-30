"""
Thin controlled refresh command for EGX annual fundamentals.

Wraps the existing yfinance scraper in a manifest-aware workflow:
  1. Scan current manifest state
  2. Identify stale/missing/invalid tickers
  3. Optionally execute yfinance refresh for selected tickers
  4. Write a refresh report (always)

Usage:
    # Dry-run: show what would be refreshed (no scraper calls)
    python -m tradingagents.dataflows.refresh_fundamentals

    # Dry-run for specific tickers
    python -m tradingagents.dataflows.refresh_fundamentals --tickers COMI,EAST

    # Execute: actually call yfinance scraper
    python -m tradingagents.dataflows.refresh_fundamentals --execute --source yfinance

    # Execute for specific tickers
    python -m tradingagents.dataflows.refresh_fundamentals --execute --source yfinance --tickers COMI,EAST
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Lazy imports to avoid circular dependency and keep startup fast
_SCRAPER_AVAILABLE = None


def _ensure_scraper_importable() -> bool:
    """Check if the yfinance scraper module is importable."""
    global _SCRAPER_AVAILABLE
    if _SCRAPER_AVAILABLE is not None:
        return _SCRAPER_AVAILABLE

    # The scraper lives in scripts/ which is not on sys.path by default.
    # Add it temporarily for programmatic invocation.
    project_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    scripts_dir = os.path.join(project_root, "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    try:
        import egx30_full_scraper  # noqa: F401
        _SCRAPER_AVAILABLE = True
    except ImportError:
        _SCRAPER_AVAILABLE = False

    return _SCRAPER_AVAILABLE


def _get_default_tickers() -> List[str]:
    """Return the active EGX ticker universe (bare symbols, no .CA suffix)."""
    from tradingagents.default_config import EGX_TICKERS
    from tradingagents.dataflows.symbol_utils import normalize_egx_ticker
    return [normalize_egx_ticker(t) for t in EGX_TICKERS]


def _scan_current_state(data_dir: str) -> tuple[List[Dict[str, Any]], str]:
    """Scan CSVs and build an in-memory manifest (does NOT write to disk).

    Returns (entries_list, manifest_path) where manifest_path is the
    canonical location but the file is NOT overwritten.
    """
    from tradingagents.dataflows.manifest_scanner import scan_fundamentals
    from tradingagents.dataflows.local import EGX_FUNDAMENTALS_DIR

    entries = scan_fundamentals(data_dir=data_dir)
    manifest_path = os.path.join(data_dir or "", EGX_FUNDAMENTALS_DIR, "manifest.json")
    return entries, manifest_path


def _load_existing_manifest(manifest_path: str) -> Optional[Dict[str, Any]]:
    """Load the on-disk manifest.json as the 'before' snapshot, or None."""
    if not os.path.exists(manifest_path):
        return None
    try:
        with open(manifest_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _identify_refresh_candidates(
    tickers: List[str],
    as_of_date: str,
    data_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Scan current CSVs and identify which tickers need refresh.

    This writes a *temporary* in-memory manifest for freshness evaluation
    but does NOT overwrite the on-disk manifest.json — that file is
    preserved as the 'before' snapshot for the diff report.

    Returns a dict with:
        needs_refresh: list of ticker dicts with reason
        up_to_date: list of tickers that pass freshness
        manifest_path: canonical manifest path (not overwritten)
    """
    import tempfile

    from tradingagents.dataflows.manifest_scanner import (
        scan_fundamentals,
        write_manifest,
        check_manifest_freshness,
    )
    from tradingagents.dataflows.config import DATA_DIR
    from tradingagents.dataflows.local import EGX_FUNDAMENTALS_DIR

    base = data_dir or DATA_DIR
    entries = scan_fundamentals(data_dir=base)
    manifest_path = os.path.join(base or "", EGX_FUNDAMENTALS_DIR, "manifest.json")

    # Write to a temp file for freshness evaluation (don't clobber the real one)
    tmp_fd, tmp_manifest = tempfile.mkstemp(suffix=".json", prefix="manifest_tmp_")
    os.close(tmp_fd)
    try:
        write_manifest(entries, output_path=tmp_manifest)

        needs_refresh: List[Dict[str, Any]] = []
        up_to_date: List[str] = []

        for ticker in tickers:
            result = check_manifest_freshness(
                manifest_path=tmp_manifest,
                as_of_date=as_of_date,
                required_tickers=[ticker],
            )
            if not result.fresh:
                needs_refresh.append({
                    "ticker": ticker,
                    "reasons": result.reasons,
                })
            else:
                up_to_date.append(ticker)
    finally:
        os.unlink(tmp_manifest)

    return {
        "needs_refresh": needs_refresh,
        "up_to_date": up_to_date,
        "manifest_path": manifest_path,
    }


def _execute_yfinance_refresh(tickers: List[str]) -> Dict[str, str]:
    """Call the yfinance scraper for each ticker.

    Returns a dict of ticker -> "OK" | "FAILED" | error message.
    """
    if not _ensure_scraper_importable():
        return {t: "FAILED: scraper not importable" for t in tickers}

    import egx30_full_scraper

    results: Dict[str, str] = {}
    for ticker in tickers:
        try:
            ok = egx30_full_scraper.process_ticker(ticker, skip_existing=False)
            results[ticker] = "OK" if ok else "FAILED"
        except Exception as e:
            results[ticker] = f"FAILED: {e}"
            logger.error("refresh_fundamentals: %s failed: %s", ticker, e)

    return results


def run_refresh(
    tickers: Optional[List[str]] = None,
    source: str = "yfinance",
    execute: bool = False,
    as_of_date: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Main refresh workflow.

    Flow:
      1. Load existing manifest.json as the "before" snapshot.
      2. Identify stale/missing tickers (without overwriting the manifest).
      3. If execute, run the yfinance scraper for stale tickers.
      4. Scan again after execution to capture the "after" state.
      5. Generate a true before-vs-after diff report.
      6. Write the updated manifest only after execution (never in dry-run).

    Args:
        tickers: List of bare ticker symbols. If None, uses active EGX universe.
        source: Data source to use. Currently only "yfinance" is supported.
        execute: If False (default), dry-run only. If True, calls the scraper.
        as_of_date: Date for freshness evaluation. Defaults to today.
        output_dir: Where to write the report. Defaults to egx_fundamentals dir.

    Returns:
        Report dict with full details of what was done / would be done.
    """
    from tradingagents.dataflows.manifest_scanner import (
        scan_fundamentals,
        write_manifest,
    )
    from tradingagents.dataflows.refresh_report import (
        generate_refresh_report,
        write_refresh_report,
    )
    from tradingagents.dataflows.config import DATA_DIR
    from tradingagents.dataflows.local import EGX_FUNDAMENTALS_DIR

    if tickers is None:
        tickers = _get_default_tickers()

    if as_of_date is None:
        as_of_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if source != "yfinance":
        raise ValueError(f"Unsupported source: {source!r}. Only 'yfinance' is supported.")

    data_dir = DATA_DIR
    manifest_path = os.path.join(data_dir or "", EGX_FUNDAMENTALS_DIR, "manifest.json")

    # --- Phase 1: Capture "before" snapshot ---
    # Load the existing on-disk manifest (may be None on first run).
    before_manifest = _load_existing_manifest(manifest_path)

    # --- Phase 2: Identify what needs refresh (non-destructive) ---
    candidates = _identify_refresh_candidates(tickers, as_of_date, data_dir=data_dir)

    # --- Phase 3: Execute (or dry-run) ---
    scraper_results: Dict[str, str] = {}
    if execute:
        refresh_tickers = [c["ticker"] for c in candidates["needs_refresh"]]
        if refresh_tickers:
            scraper_results = _execute_yfinance_refresh(refresh_tickers)
        else:
            logger.info("refresh_fundamentals: all tickers up to date, nothing to refresh")

        # --- Phase 4: Scan again AFTER execution to capture "after" state ---
        after_entries = scan_fundamentals(data_dir=data_dir)
        # Write the updated manifest (only in execute mode)
        write_manifest(after_entries, output_path=manifest_path)
    else:
        # Dry-run: mark what would be refreshed, do NOT touch manifest.json
        for c in candidates["needs_refresh"]:
            scraper_results[c["ticker"]] = "DRY_RUN (would refresh)"

    # --- Phase 5: Generate before-vs-after diff report ---
    if execute and before_manifest is not None:
        # Execute mode with a real "before" snapshot: generate a true diff.
        # We already wrote the updated manifest in Phase 4, so pass the
        # captured before_manifest as the explicit previous state.
        report = _generate_report_with_before(
            data_dir=data_dir,
            before_manifest=before_manifest,
        )
    else:
        # Dry-run (or first-ever run with no prior manifest):
        # Don't write manifest — just generate a status report.
        report = generate_refresh_report(
            data_dir=data_dir,
            previous_manifest_path=None,
            write_manifest_file=False,
        )

    # Enrich report with refresh-specific metadata
    report["refresh_metadata"] = {
        "mode": "execute" if execute else "dry_run",
        "source": source,
        "as_of_date": as_of_date,
        "requested_tickers": tickers,
        "needs_refresh": candidates["needs_refresh"],
        "up_to_date": candidates["up_to_date"],
        "scraper_results": scraper_results,
    }

    # Write report
    base = output_dir or os.path.join(data_dir or ".", EGX_FUNDAMENTALS_DIR)
    report_path = write_refresh_report(report, output_dir=base)
    report["report_path"] = report_path

    return report


def _generate_report_with_before(
    data_dir: Optional[str],
    before_manifest: Dict[str, Any],
) -> Dict[str, Any]:
    """Generate a refresh report using an explicit 'before' manifest snapshot.

    This avoids the race where generate_refresh_report reads the already-
    updated manifest.json as "previous" (which would show zero changes).
    """
    import tempfile

    from tradingagents.dataflows.refresh_report import generate_refresh_report

    # Write the before snapshot to a temp file so generate_refresh_report
    # can diff against it.
    tmp_fd, tmp_before = tempfile.mkstemp(suffix=".json", prefix="manifest_before_")
    os.close(tmp_fd)
    try:
        with open(tmp_before, "w") as f:
            json.dump(before_manifest, f)

        report = generate_refresh_report(
            data_dir=data_dir,
            previous_manifest_path=tmp_before,
            write_manifest_file=False,  # already written by Phase 4
        )
    finally:
        os.unlink(tmp_before)

    return report


def _print_ticker_summary(report: Dict[str, Any]) -> None:
    """Print a concise ticker-scoped summary to the terminal."""
    meta = report["refresh_metadata"]
    mode_label = "EXECUTE" if meta["mode"] == "execute" else "DRY RUN"

    print(f"{'='*50}")
    print(f"  REFRESH {mode_label} — source: {meta['source']}")
    print(f"{'='*50}")
    print(f"  As-of date:  {meta['as_of_date']}")
    print(f"  Tickers:     {', '.join(meta['requested_tickers'])}")
    print()

    # Per-ticker freshness status
    up_set = set(meta["up_to_date"])
    for ticker in meta["requested_tickers"]:
        if ticker in up_set:
            print(f"  [OK] {ticker}: up to date")
        else:
            # Find reasons for this ticker
            reasons = []
            for c in meta["needs_refresh"]:
                if c["ticker"] == ticker:
                    reasons = c.get("reasons", [])
                    break
            scraper_status = meta["scraper_results"].get(ticker, "")
            print(f"  [!!] {ticker}: needs refresh")
            for r in reasons:
                print(f"       - {r}")
            if scraper_status:
                icon = "+" if scraper_status == "OK" else "~" if "DRY_RUN" in scraper_status else "X"
                print(f"       scraper: [{icon}] {scraper_status}")

    # Brief global stats (one line)
    s = report["summary"]
    print()
    print(f"  Global: {s['total_files']} files scanned, "
          f"{s['schema_valid']} valid, "
          f"{s['stale_files']} stale, "
          f"{s['schema_invalid_files']} invalid")

    print(f"\n  Report: {report.get('report_path', 'N/A')}")
    print(f"{'='*50}")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Controlled refresh for EGX annual fundamentals CSVs."
    )
    parser.add_argument(
        "--tickers",
        type=str,
        default=None,
        help="Comma-separated ticker list (bare symbols, e.g. COMI,EAST). "
             "If omitted, uses the active EGX_TICKERS universe.",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="yfinance",
        choices=["yfinance"],
        help="Data source to use (default: yfinance).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Actually call the scraper. Without this flag, dry-run only.",
    )
    parser.add_argument(
        "--as-of-date",
        type=str,
        default=None,
        help="Date for freshness evaluation (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Show full Markdown report with global manifest diff details.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress all terminal output (JSON report still written).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    tickers = None
    if args.tickers:
        from tradingagents.dataflows.symbol_utils import normalize_egx_ticker
        tickers = [normalize_egx_ticker(t) for t in args.tickers.split(",")]

    report = run_refresh(
        tickers=tickers,
        source=args.source,
        execute=args.execute,
        as_of_date=args.as_of_date,
    )

    if not args.quiet:
        if args.verbose:
            from tradingagents.dataflows.refresh_report import format_report_markdown
            print(format_report_markdown(report))
            print()
        _print_ticker_summary(report)


if __name__ == "__main__":
    main()
