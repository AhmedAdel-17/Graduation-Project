#!/usr/bin/env python
"""
calibrate_tier_thresholds.py — Layer C liquidity-tier threshold calibration.

PR 10: SESSION_BOOTSTRAP §5 marks MEGA/MID/SMALL strong-mention thresholds
as PROVISIONAL.  This script ingests a rolling window of past v2 pipeline
results and recommends updated thresholds for each tier.

Usage
-----
    # Use default 30-day lookback from the most recent results_*.json files:
    python scripts/calibrate_tier_thresholds.py

    # Point at a specific results directory:
    python scripts/calibrate_tier_thresholds.py --results-dir logs/archive

    # Emit a machine-readable config patch:
    python scripts/calibrate_tier_thresholds.py --format json

This script is deliberately offline-safe: it only reads local JSON/CSV files
produced by pipeline_v2.py.  It never calls any live API.

Algorithm
---------
For each ticker in each results file, we count:
  - n_strong_mentions  (posts with entity_conf >= 0.85 mentioning that ticker)
  - n_distinct_authors
  - n_distinct_sources
  - recent_72h_share   (fraction of strong-mention posts within 72 h)

We bucket tickers by LiquidityTier (MEGA/MID/SMALL) using the current tier
definition from `tradingagents/sentiment/liquidity_tiers.py`.

For each tier we compute:
  - 5th, 25th, 50th, 75th, 95th percentiles of each gate metric
  - Recommended threshold = 25th-percentile value (conservative: ~75% of real
    trading days would pass the gate)

Output shows current thresholds vs recommended, and prints the
`tradingagents/sentiment/liquidity_tiers.py` edit you'd need to apply.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── path setup ───────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tradingagents.sentiment.liquidity_tiers import LiquidityTier, tier_for

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("calibrate_tiers")

# ── current thresholds (from SESSION_BOOTSTRAP §6) ───────────────────────────
CURRENT_THRESHOLDS: Dict[str, Dict[str, int]] = {
    "MEGA":  {"n_strong_mentions": 8,  "n_distinct_authors": 5, "n_distinct_sources": 3},
    "MID":   {"n_strong_mentions": 5,  "n_distinct_authors": 3, "n_distinct_sources": 2},
    "SMALL": {"n_strong_mentions": 3,  "n_distinct_authors": 2, "n_distinct_sources": 2},
}

ENTITY_CONF_THRESHOLD = 0.85   # "strong mention" definition
RECENT_72H_MIN_SHARE  = 0.60   # recency gate (not threshold-calibrated here)


# ── data loading ─────────────────────────────────────────────────────────────

def _find_results_files(results_dir: Path, lookback_days: int) -> List[Path]:
    """Return results_*.json files from the last `lookback_days` days."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
    files: List[Path] = []
    for path in sorted(results_dir.glob("results_*.json")):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime >= cutoff:
                files.append(path)
        except OSError:
            continue
    return files


def _load_per_stock_observations(
    results_files: List[Path],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Parse results files and extract per-ticker observation dicts.

    Returns: { ticker: [ {n_strong_mentions, n_distinct_authors,
                           n_distinct_sources, recent_72h_share,
                           date, source_file}, ... ] }
    """
    observations: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for path in results_files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Skipping %s: %s", path.name, e)
            continue

        # Infer date from filename if not in payload
        file_date = _infer_date_from_filename(path.name)

        # per_stock_sentiment block
        per_stock = data.get("per_stock_sentiment") or {}
        for ticker, stock_data in per_stock.items():
            if not isinstance(stock_data, dict):
                continue
            posts = stock_data.get("posts") or stock_data.get("items") or []
            if not isinstance(posts, list):
                continue

            obs = _compute_observation(ticker, posts, file_date)
            if obs is not None:
                observations[ticker].append(obs)

    return dict(observations)


def _infer_date_from_filename(filename: str) -> Optional[str]:
    """Extract YYYY-MM-DD from 'results_20260502_143021.json'."""
    import re
    m = re.search(r"(\d{4})(\d{2})(\d{2})", filename)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def _compute_observation(
    ticker: str,
    posts: List[Dict[str, Any]],
    date: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Derive gate metric values from a list of raw post dicts."""
    if not posts:
        return None

    now_ts: Optional[float] = None
    if date:
        try:
            now_ts = datetime.fromisoformat(date).replace(
                tzinfo=timezone.utc
            ).timestamp()
        except ValueError:
            pass

    strong_posts: List[Dict[str, Any]] = []
    authors: set = set()
    sources: set = set()
    recent_strong = 0

    for p in posts:
        if not isinstance(p, dict):
            continue
        ec = p.get("entity_conf") or p.get("entity_confidence") or 0.0
        try:
            ec = float(ec)
        except (TypeError, ValueError):
            ec = 0.0

        author = p.get("author") or p.get("username") or "_anon"
        source = p.get("source") or p.get("platform") or "unknown"
        is_spam = bool(p.get("spam") or p.get("is_spam"))

        if is_spam:
            continue
        if ec < ENTITY_CONF_THRESHOLD:
            continue

        strong_posts.append(p)
        authors.add(str(author).strip().lower() or "_anon")
        sources.add(str(source).strip().lower())

        # Recency
        if now_ts:
            ts_raw = p.get("timestamp") or p.get("created_at") or ""
            ts = _parse_timestamp(ts_raw)
            if ts and (now_ts - ts) <= 72 * 3600:
                recent_strong += 1

    n = len(strong_posts)
    if n == 0:
        return None

    recent_72h_share = recent_strong / n if (n > 0 and now_ts) else None

    return {
        "ticker": ticker,
        "date": date,
        "n_strong_mentions": n,
        "n_distinct_authors": len(authors),
        "n_distinct_sources": len(sources),
        "recent_72h_share": recent_72h_share,
    }


def _parse_timestamp(ts: Any) -> Optional[float]:
    """Best-effort timestamp → epoch float."""
    if not ts:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(ts[:19], fmt[:len(fmt)])
                return dt.replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                continue
    return None


# ── statistics ───────────────────────────────────────────────────────────────

def _percentile(data: List[float], pct: float) -> float:
    """Return the `pct`-th percentile (0-100) of sorted data."""
    if not data:
        return 0.0
    sorted_d = sorted(data)
    n = len(sorted_d)
    idx = (pct / 100) * (n - 1)
    lo, hi = int(idx), min(int(idx) + 1, n - 1)
    frac = idx - lo
    return sorted_d[lo] * (1 - frac) + sorted_d[hi] * frac


def _summarise_metric(
    values: List[float],
) -> Dict[str, float]:
    """Return descriptive statistics for a list of metric values."""
    if not values:
        return {"n": 0, "p5": 0, "p25": 0, "p50": 0, "p75": 0, "p95": 0, "mean": 0}
    return {
        "n": len(values),
        "p5":   round(_percentile(values, 5),  2),
        "p25":  round(_percentile(values, 25), 2),
        "p50":  round(_percentile(values, 50), 2),
        "p75":  round(_percentile(values, 75), 2),
        "p95":  round(_percentile(values, 95), 2),
        "mean": round(statistics.mean(values),  2),
    }


# ── recommendation ───────────────────────────────────────────────────────────

def _recommend_threshold(p25: float, current: int) -> int:
    """
    Recommended threshold = ceil(p25).

    This means ~75% of past trading days would pass the gate.
    If fewer than 10 observations, keep the current threshold (not enough data).
    """
    return max(1, math.ceil(p25))


def compute_recommendations(
    observations: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    """
    Group observations by LiquidityTier and compute per-metric recommendations.

    Returns: { "MEGA": { metric: {stats, current, recommended}, ... }, ... }
    """
    tier_buckets: Dict[str, Dict[str, List[float]]] = {
        "MEGA":  defaultdict(list),
        "MID":   defaultdict(list),
        "SMALL": defaultdict(list),
    }

    for ticker, obs_list in observations.items():
        tier = tier_for(ticker)
        tier_name = tier.name  # "MEGA", "MID", "SMALL"
        for obs in obs_list:
            for metric in ("n_strong_mentions", "n_distinct_authors", "n_distinct_sources"):
                v = obs.get(metric)
                if v is not None:
                    tier_buckets[tier_name][metric].append(float(v))

    results: Dict[str, Dict[str, Any]] = {}
    for tier_name, buckets in tier_buckets.items():
        tier_result: Dict[str, Any] = {"n_tickers": 0, "n_observations": 0}
        total_obs = 0
        tickers_seen: set = set()

        for ticker, obs_list in observations.items():
            if tier_for(ticker).name == tier_name:
                tickers_seen.add(ticker)
                total_obs += len(obs_list)

        tier_result["n_tickers"] = len(tickers_seen)
        tier_result["n_observations"] = total_obs

        for metric in ("n_strong_mentions", "n_distinct_authors", "n_distinct_sources"):
            vals = buckets.get(metric, [])
            stats = _summarise_metric(vals)
            current = CURRENT_THRESHOLDS[tier_name][metric]

            if stats["n"] >= 10:
                recommended = _recommend_threshold(stats["p25"], current)
            else:
                recommended = current  # insufficient data — keep current
                stats["note"] = "insufficient_data (<10 obs) — keeping current"

            tier_result[metric] = {
                "stats": stats,
                "current": current,
                "recommended": recommended,
                "changed": recommended != current,
            }

        results[tier_name] = tier_result

    return results


# ── output formatters ─────────────────────────────────────────────────────────

def _print_text_report(
    recommendations: Dict[str, Dict[str, Any]],
    results_dir: Path,
    lookback_days: int,
    n_files: int,
) -> None:
    """Print a human-readable calibration report."""
    print("\n" + "=" * 70)
    print("LAYER C LIQUIDITY-TIER THRESHOLD CALIBRATION REPORT")
    print(f"Results dir  : {results_dir}")
    print(f"Lookback     : {lookback_days} days")
    print(f"Files scanned: {n_files}")
    print("=" * 70)

    for tier_name in ("MEGA", "MID", "SMALL"):
        tier = recommendations.get(tier_name, {})
        n_tick = tier.get("n_tickers", 0)
        n_obs  = tier.get("n_observations", 0)
        print(f"\n── {tier_name} tier  ({n_tick} tickers, {n_obs} observations) ──")

        for metric in ("n_strong_mentions", "n_distinct_authors", "n_distinct_sources"):
            m = tier.get(metric, {})
            if not m:
                print(f"  {metric:25s}  no data")
                continue
            s = m["stats"]
            flag = "  ← CHANGE RECOMMENDED" if m["changed"] else ""
            note = f"  [{s.get('note','')}]" if s.get("note") else ""
            print(
                f"  {metric:25s}  "
                f"cur={m['current']:2d}  rec={m['recommended']:2d}  "
                f"p25={s['p25']:5.1f}  p50={s['p50']:5.1f}  "
                f"p75={s['p75']:5.1f}  n={s['n']:4d}"
                f"{flag}{note}"
            )

    print("\n── Suggested edit to tradingagents/sentiment/liquidity_tiers.py ──")
    for tier_name in ("MEGA", "MID", "SMALL"):
        tier = recommendations.get(tier_name, {})
        ns  = tier.get("n_strong_mentions", {}).get("recommended", CURRENT_THRESHOLDS[tier_name]["n_strong_mentions"])
        na  = tier.get("n_distinct_authors",  {}).get("recommended", CURRENT_THRESHOLDS[tier_name]["n_distinct_authors"])
        nso = tier.get("n_distinct_sources",  {}).get("recommended", CURRENT_THRESHOLDS[tier_name]["n_distinct_sources"])
        print(f"  {tier_name:5s}: n_strong_mentions={ns}, n_distinct_authors={na}, n_distinct_sources={nso}")

    print()


def _print_json_report(recommendations: Dict[str, Dict[str, Any]]) -> None:
    """Emit machine-readable JSON patch for CI/scripting."""
    patch = {}
    for tier_name in ("MEGA", "MID", "SMALL"):
        tier = recommendations.get(tier_name, {})
        patch[tier_name] = {
            metric: tier.get(metric, {}).get("recommended",
                    CURRENT_THRESHOLDS[tier_name].get(metric, 0))
            for metric in ("n_strong_mentions", "n_distinct_authors", "n_distinct_sources")
        }
    print(json.dumps({"recommended_thresholds": patch}, indent=2))


# ── entrypoint ────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate Layer C liquidity-tier thresholds from pipeline results.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=_HERE / "twitter_pipeline" / "v2" / "logs",
        help="Directory containing results_*.json files (default: scripts/twitter_pipeline/v2/logs/)",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        help="Number of days of results to include (default: 30)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format: 'text' (human-readable) or 'json' (machine-readable patch)",
    )
    args = parser.parse_args()

    results_dir: Path = args.results_dir
    if not results_dir.is_dir():
        log.error("Results directory not found: %s", results_dir)
        log.info("Run pipeline_v2.py first to generate results files.")
        return 1

    log.info("Scanning %s for results files (last %d days)…", results_dir, args.lookback_days)
    files = _find_results_files(results_dir, args.lookback_days)
    log.info("Found %d results files.", len(files))

    if not files:
        log.warning("No results files found. Cannot calibrate. Run pipeline_v2.py first.")
        return 1

    observations = _load_per_stock_observations(files)
    total_obs = sum(len(v) for v in observations.values())
    log.info(
        "Loaded %d observations across %d tickers.", total_obs, len(observations)
    )

    if total_obs == 0:
        log.warning(
            "All results files were empty or had no per_stock_sentiment data. "
            "Cannot calibrate."
        )
        return 1

    recommendations = compute_recommendations(observations)

    if args.format == "json":
        _print_json_report(recommendations)
    else:
        _print_text_report(recommendations, results_dir, args.lookback_days, len(files))

    return 0


if __name__ == "__main__":
    sys.exit(main())
