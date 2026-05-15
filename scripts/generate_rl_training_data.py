"""Build the offline RL training parquet from existing backtests + audit rows.

Usage::

    python scripts/generate_rl_training_data.py \\
        --output data/rl/training_v1.parquet \\
        --json-reports backtest_results/report_*.json \\
        --universe COMI.CA,EAST.CA,HRHO.CA \\
        --horizon-days 20

By default the script reads from Postgres (if configured) AND from any JSON
report paths passed via ``--json-reports``, then merges them keyed on
``(ticker, trade_date)`` with Postgres preferred. If neither source yields
samples it exits with a clear error.

The script is intentionally read-only — it doesn't touch the agent graph,
LLMs, or any vendor data feeds. It exists to materialize an immutable
training-data snapshot you can hand to ``tradingagents/rl/train.py`` in
Stage B.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List

# Add project root so we can ``from tradingagents.rl ...`` regardless of CWD.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tradingagents.rl.dataset import (
    DATASET_SCHEMA_VERSION,
    DEFAULT_REWARD_HORIZON_DAYS,
    assert_no_lookahead,
    build_offline_dataset,
    summarize,
    write_parquet,
)
from tradingagents.rl.feature_extractor import FEATURE_VERSION

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("rl.gen_data")


def _parse_csv_list(value: str) -> List[str]:
    return [token.strip() for token in value.split(",") if token.strip()]


def _expand_globs(patterns: List[str]) -> List[Path]:
    out: List[Path] = []
    for pat in patterns:
        matches = glob.glob(pat)
        if not matches:
            logger.warning("rl-gen: no matches for --json-reports glob %r", pat)
        out.extend(Path(m) for m in matches)
    # De-dup while preserving order
    seen = set()
    deduped: List[Path] = []
    for p in out:
        rp = p.resolve()
        if rp not in seen:
            deduped.append(p)
            seen.add(rp)
    return deduped


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", required=True,
        help="Output parquet path (e.g. data/rl/training_v1.parquet)",
    )
    parser.add_argument(
        "--json-reports", nargs="*", default=[],
        help="Backtest report.json files (globs OK). Used in addition to Postgres.",
    )
    parser.add_argument(
        "--universe", type=_parse_csv_list, default=None,
        help="Comma-separated ticker list to filter Postgres queries (e.g. COMI.CA,EAST.CA).",
    )
    parser.add_argument(
        "--start-date", default=None,
        help="ISO start date (inclusive) for Postgres filter (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--end-date", default=None,
        help="ISO end date (inclusive) for Postgres filter (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--horizon-days", type=int, default=DEFAULT_REWARD_HORIZON_DAYS,
        help="Reward horizon (trading days). Default: 20.",
    )
    parser.add_argument(
        "--prefer", choices=("postgres", "json"), default="postgres",
        help="Primary data source. The other is used as backfill.",
    )
    parser.add_argument(
        "--strict-lookahead", action="store_true",
        help="Fail loudly if any sample's horizon hasn't elapsed yet.",
    )
    parser.add_argument(
        "--summary-only", action="store_true",
        help="Only print the summary; do not write parquet.",
    )
    args = parser.parse_args(argv)

    json_paths = _expand_globs(args.json_reports)
    if json_paths:
        logger.info("rl-gen: %d json report file(s) discovered", len(json_paths))

    date_range = None
    if args.start_date and args.end_date:
        date_range = (args.start_date, args.end_date)
    elif bool(args.start_date) != bool(args.end_date):
        logger.error("rl-gen: --start-date and --end-date must be passed together")
        return 2

    samples = build_offline_dataset(
        universe=args.universe,
        date_range=date_range,
        json_report_paths=json_paths or None,
        horizon_days=args.horizon_days,
        prefer=args.prefer,
    )

    if not samples:
        logger.error(
            "rl-gen: no samples collected from any source. "
            "Pass --json-reports to ingest existing backtest reports, "
            "or ensure POSTGRES_URL is set and analysis_sessions has rows."
        )
        return 1

    if args.strict_lookahead:
        assert_no_lookahead(samples, now=datetime.utcnow())

    summary = summarize(samples)
    summary["generated_at"] = datetime.utcnow().isoformat() + "Z"
    summary["feature_version"] = FEATURE_VERSION
    summary["dataset_schema_version"] = DATASET_SCHEMA_VERSION
    logger.info("rl-gen: %s", json.dumps(summary, indent=2, default=str))

    if args.summary_only:
        return 0

    output_path = write_parquet(samples, args.output)
    logger.info("rl-gen: wrote %d samples to %s", len(samples), output_path)

    # Write a sidecar JSON summary so the model card (Stage B) can quote it.
    sidecar = output_path.with_suffix(output_path.suffix + ".summary.json")
    with open(sidecar, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    logger.info("rl-gen: wrote summary sidecar to %s", sidecar)

    return 0


if __name__ == "__main__":
    sys.exit(main())
