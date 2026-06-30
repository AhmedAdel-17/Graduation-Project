"""
Fundamentals Refresh Report Generator.

Compares the current manifest against a previous snapshot and produces a
human-readable + machine-readable report of what changed.

Usage:
    from tradingagents.dataflows.refresh_report import generate_refresh_report
    report = generate_refresh_report()

    # Or as a script:
    python -m tradingagents.dataflows.refresh_report
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tradingagents.dataflows.config import DATA_DIR
from tradingagents.dataflows.local import EGX_FUNDAMENTALS_DIR
from tradingagents.dataflows.manifest_scanner import (
    scan_fundamentals,
    write_manifest,
)

logger = logging.getLogger(__name__)


def _load_manifest(path: str) -> Optional[Dict[str, Any]]:
    """Load and return a manifest dict, or None on failure."""
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _entry_key(entry: Dict[str, Any]) -> str:
    """Unique key for an entry: ticker/statement_type/frequency."""
    return f"{entry['ticker']}/{entry['statement_type']}/{entry['frequency']}"


def generate_refresh_report(
    data_dir: Optional[str] = None,
    previous_manifest_path: Optional[str] = None,
    write_manifest_file: bool = True,
) -> Dict[str, Any]:
    """Scan current state, compare to previous manifest, produce a report.

    Args:
        data_dir: Override for DATA_DIR.
        previous_manifest_path: Path to previous manifest.json for diffing.
            If None, looks for manifest.json in the default location (which
            becomes the "previous" before we regenerate).
        write_manifest_file: If True (default), regenerates and writes
            manifest.json as a side effect.  Set False for dry-run / read-only
            mode (manifest is built in memory only).
            TODO: A future ``dry_run`` parameter on a higher-level refresh
            orchestrator should set this to False and also skip any CSV writes.

    Returns:
        Report dict with keys: generated_at, summary, new_files, removed_files,
        changed_files, financial_changed_files, provenance_changed_files,
        stale_files, schema_invalid_files, source_coverage.
    """
    base = data_dir or DATA_DIR
    manifest_path = os.path.join(base, EGX_FUNDAMENTALS_DIR, "manifest.json")

    # Load previous manifest before regenerating
    prev_path = previous_manifest_path or manifest_path
    prev_manifest = _load_manifest(prev_path)
    prev_entries: Dict[str, Dict[str, Any]] = {}
    if prev_manifest:
        for e in prev_manifest.get("entries", []):
            prev_entries[_entry_key(e)] = e

    # Regenerate current manifest
    current_entries_list = scan_fundamentals(data_dir=base)
    if write_manifest_file:
        write_manifest(current_entries_list, output_path=manifest_path)

    current_entries: Dict[str, Dict[str, Any]] = {}
    for e in current_entries_list:
        current_entries[_entry_key(e)] = e

    # Diff
    prev_keys = set(prev_entries.keys())
    curr_keys = set(current_entries.keys())

    new_keys = curr_keys - prev_keys
    removed_keys = prev_keys - curr_keys
    common_keys = curr_keys & prev_keys

    # Detect changes in common files.
    # Financial fields = changes to actual data (row_count, latest_period, schema).
    # Provenance fields = metadata about collection (scraped_at, data_sources).
    _FINANCIAL_FIELDS = {"row_count", "latest_period_end_date", "schema_valid"}
    _PROVENANCE_FIELDS = {"data_sources", "latest_scraped_at"}

    changed: List[Dict[str, Any]] = []
    financial_changed: List[Dict[str, Any]] = []
    provenance_changed: List[Dict[str, Any]] = []

    for key in sorted(common_keys):
        prev = prev_entries[key]
        curr = current_entries[key]

        diffs = {}
        if prev.get("row_count") != curr.get("row_count"):
            diffs["row_count"] = {"old": prev.get("row_count"), "new": curr.get("row_count")}
        if prev.get("latest_period_end_date") != curr.get("latest_period_end_date"):
            diffs["latest_period_end_date"] = {
                "old": prev.get("latest_period_end_date"),
                "new": curr.get("latest_period_end_date"),
            }
        if prev.get("schema_valid") != curr.get("schema_valid"):
            diffs["schema_valid"] = {"old": prev.get("schema_valid"), "new": curr.get("schema_valid")}
        if prev.get("data_sources", []) != curr.get("data_sources", []):
            diffs["data_sources"] = {"old": prev.get("data_sources", []), "new": curr.get("data_sources", [])}
        if prev.get("latest_scraped_at") != curr.get("latest_scraped_at"):
            diffs["latest_scraped_at"] = {
                "old": prev.get("latest_scraped_at"),
                "new": curr.get("latest_scraped_at"),
            }

        if diffs:
            entry = {"key": key, "diffs": diffs}
            changed.append(entry)

            diff_fields = set(diffs.keys())
            has_financial = bool(diff_fields & _FINANCIAL_FIELDS)
            has_provenance_only = diff_fields.issubset(_PROVENANCE_FIELDS)

            if has_financial:
                financial_changed.append(entry)
            if has_provenance_only:
                provenance_changed.append(entry)

    # Identify stale and schema-invalid files
    stale: List[Dict[str, Any]] = []
    schema_invalid: List[Dict[str, Any]] = []
    for key in sorted(curr_keys):
        entry = current_entries[key]
        if not entry.get("schema_valid"):
            schema_invalid.append({
                "key": key,
                "missing_required_fields": entry.get("missing_required_fields", []),
                "warnings": entry.get("warnings", []),
            })
        if entry.get("row_count", 0) == 0 and entry.get("used_by_runtime"):
            stale.append({
                "key": key,
                "reason": "zero data rows (runtime-used file)",
            })

    # Source coverage summary
    source_counts: Dict[str, int] = {}
    for entry in current_entries_list:
        for src in entry.get("data_sources", []):
            source_counts[src] = source_counts.get(src, 0) + 1
    no_source_count = sum(
        1 for e in current_entries_list if not e.get("data_sources")
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "had_previous_manifest": prev_manifest is not None,
        "summary": {
            "total_files": len(current_entries_list),
            "runtime_files": sum(1 for e in current_entries_list if e.get("used_by_runtime")),
            "schema_valid": sum(1 for e in current_entries_list if e.get("schema_valid")),
            "new_files": len(new_keys),
            "removed_files": len(removed_keys),
            "changed_files": len(changed),
            "financial_changed_files": len(financial_changed),
            "provenance_changed_files": len(provenance_changed),
            "stale_files": len(stale),
            "schema_invalid_files": len(schema_invalid),
        },
        "source_coverage": {
            "by_source": source_counts,
            "no_source_column": no_source_count,
        },
        "new_files": sorted(new_keys),
        "removed_files": sorted(removed_keys),
        "changed_files": changed,
        "financial_changed_files": financial_changed,
        "provenance_changed_files": provenance_changed,
        "stale_files": stale,
        "schema_invalid_files": schema_invalid,
    }

    return report


def write_refresh_report(
    report: Dict[str, Any],
    output_dir: Optional[str] = None,
) -> str:
    """Write a refresh report to JSON file.

    Returns the path to the written report.
    """
    base = output_dir or os.path.join(DATA_DIR or ".", EGX_FUNDAMENTALS_DIR)
    os.makedirs(base, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(base, f"refresh_report_{timestamp}.json")

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info("refresh_report: wrote %s", report_path)
    return report_path


def format_report_markdown(report: Dict[str, Any]) -> str:
    """Format a refresh report as Markdown for human reading."""
    lines = []
    lines.append("# Fundamentals Refresh Report")
    lines.append(f"Generated: {report['generated_at']}")
    lines.append(f"Previous manifest available: {report['had_previous_manifest']}")
    lines.append("")

    s = report["summary"]
    lines.append("## Summary")
    lines.append(f"- Total files: {s['total_files']}")
    lines.append(f"- Runtime-used: {s['runtime_files']}")
    lines.append(f"- Schema valid: {s['schema_valid']}")
    lines.append(f"- New: {s['new_files']}")
    lines.append(f"- Removed: {s['removed_files']}")
    lines.append(f"- Changed (all): {s['changed_files']}")
    lines.append(f"  - Financial (data/schema): {s.get('financial_changed_files', 0)}")
    lines.append(f"  - Provenance only (scraped_at/source): {s.get('provenance_changed_files', 0)}")
    lines.append(f"- Stale (zero rows, runtime): {s['stale_files']}")
    lines.append(f"- Schema invalid: {s['schema_invalid_files']}")
    lines.append("")

    sc = report["source_coverage"]
    lines.append("## Source Coverage")
    if sc["by_source"]:
        for src, count in sorted(sc["by_source"].items()):
            lines.append(f"- {src}: {count} files")
    lines.append(f"- No data_source column: {sc['no_source_column']} files")
    lines.append("")

    if report["new_files"]:
        lines.append("## New Files")
        for f in report["new_files"]:
            lines.append(f"- {f}")
        lines.append("")

    if report["removed_files"]:
        lines.append("## Removed Files")
        for f in report["removed_files"]:
            lines.append(f"- {f}")
        lines.append("")

    if report["changed_files"]:
        lines.append("## Changed Files")
        for c in report["changed_files"]:
            lines.append(f"- **{c['key']}**")
            for field, diff in c["diffs"].items():
                lines.append(f"  - {field}: {diff['old']} -> {diff['new']}")
        lines.append("")

    if report["stale_files"]:
        lines.append("## Stale Files (Action Required)")
        for st in report["stale_files"]:
            lines.append(f"- {st['key']}: {st['reason']}")
        lines.append("")

    if report["schema_invalid_files"]:
        lines.append("## Schema Invalid Files")
        for si in report["schema_invalid_files"]:
            lines.append(f"- {si['key']}: missing={si['missing_required_fields']}")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    report = generate_refresh_report()
    path = write_refresh_report(report)
    print(format_report_markdown(report))
    print(f"\nJSON report written to: {path}")
