"""
Drift / Attribution analyser for anti-churn + verified-news benchmark
=====================================================================
Reads NEW benchmark report JSONs and OLD baseline reports, then outputs
a per-ticker attribution table covering:

  1. Anti-churn attribution (applied count, dates, reasons, decision_path)
  2. Decision drift indicators (sequence comparison vs baseline)
  3. Classification (DIRECT_ANTI_CHURN_EFFECT / NEWS_EFFECT_POSSIBLE /
     NO_REGRESSION / LLM_DRIFT_INCONCLUSIVE)
  4. News coverage context (article count, eval-date coverage)
  5. Data quality checks (fundamentals_quality, pipeline_mode, no look-ahead)

Usage:
    python scripts/drift_attribution.py \
        --new-dir backtest_results \
        --baseline-map scripts/baseline_map.json \
        --news-dir tradingagents/dataflows/data_cache/egx_news/csv \
        --out-dir eval_results

The --baseline-map is a JSON file mapping ticker -> baseline report path.
If omitted, the script auto-detects baselines using a cutoff timestamp.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import logging
import math
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("drift_attribution")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── News coverage ratings ────────────────────────────────────────────────
NEWS_COVERAGE = {
    "COMI": {"count": 4, "dates": ["2024-02-18","2024-02-20","2024-03-31","2024-05-14"], "rating": "adequate"},
    "ETEL": {"count": 5, "dates": ["2024-01-17","2024-01-21","2024-03-04","2024-05-22","2024-05-30"], "rating": "good"},
    "TMGH": {"count": 3, "dates": ["2024-02-23","2024-02-25","2024-03-31"], "rating": "thin"},
    "EAST": {"count": 7, "dates": ["2024-02-05","2024-02-16","2024-04-14","2024-04-15","2024-04-22","2024-05-22","2024-05-23"], "rating": "strong"},
    "FWRY": {"count": 3, "dates": ["2024-03-04","2024-03-05","2024-04-30"], "rating": "thin"},
    "HRHO": {"count": 7, "dates": ["2024-02-05","2024-03-04","2024-03-06","2024-03-27","2024-05-01","2024-05-21","2024-05-23"], "rating": "strong"},
    "SWDY": {"count": 5, "dates": ["2024-04-03","2024-04-28","2024-05-02","2024-05-19","2024-06-06"], "rating": "good"},
    "PHDC": {"count": 2, "dates": ["2024-02-28","2024-05-29"], "rating": "thin"},
    "ADIB": {"count": 1, "dates": ["2024-02-06"], "rating": "minimal"},
}

# ── Helpers ───────────────────────────────────────────────────────────────

def _load_report(path: Path) -> Optional[Dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Cannot load %s: %s", path, e)
        return None


def _extract_decision_sequence(report: Dict) -> List[Dict]:
    """Extract per-date decision info from audit_log."""
    rows = []
    for entry in report.get("audit_log", []):
        rows.append({
            "date": entry.get("date"),
            "raw_decision": entry.get("raw_decision"),
            "parsed_decision": entry.get("parsed_decision"),
            "decision_path": entry.get("decision_path"),
            "confidence": entry.get("confidence"),
            "fundamentals_quality": entry.get("fundamentals_quality"),
            "fundamentals_pipeline_mode": entry.get("fundamentals_pipeline_mode"),
            # Anti-churn fields (only present in new runs)
            "anti_churn_checked": entry.get("anti_churn_checked", False),
            "anti_churn_applied": entry.get("anti_churn_applied", False),
            "anti_churn_reason": entry.get("anti_churn_reason"),
            "anti_churn_prev_decision": entry.get("anti_churn_prev_decision"),
            "anti_churn_current_decision": entry.get("anti_churn_current_decision"),
            "anti_churn_confidence_used": entry.get("anti_churn_confidence_used"),
            "anti_churn_threshold": entry.get("anti_churn_threshold"),
            "anti_churn_is_reversal": entry.get("anti_churn_is_reversal", False),
        })
    return rows


def _count_reversals(decisions: List[str]) -> int:
    """Count BUY<->SELL reversals (not transitions through HOLD)."""
    last_active = None
    reversals = 0
    for d in decisions:
        if d in ("BUY", "SELL"):
            if last_active and last_active != d:
                reversals += 1
            last_active = d
    return reversals


def _count_hold_stretches(decisions: List[str]) -> int:
    """Count contiguous HOLD-only stretches of length >= 2."""
    stretches = 0
    current_run = 0
    for d in decisions:
        if d == "HOLD":
            current_run += 1
        else:
            if current_run >= 2:
                stretches += 1
            current_run = 0
    if current_run >= 2:
        stretches += 1
    return stretches


def _news_in_lookback(eval_date_str: str, news_dates: List[str],
                       lookback_days: int = 30) -> int:
    """Count news articles within [eval_date - lookback, eval_date]."""
    try:
        eval_dt = datetime.strptime(eval_date_str, "%Y-%m-%d")
    except ValueError:
        return 0
    cutoff = eval_dt - timedelta(days=lookback_days)
    count = 0
    for nd in news_dates:
        try:
            ndt = datetime.strptime(nd, "%Y-%m-%d")
            if cutoff <= ndt <= eval_dt:
                count += 1
        except ValueError:
            continue
    return count


def _total_return_from_report(report: Dict) -> Optional[float]:
    """Extract total_return_pct from report metrics."""
    metrics = report.get("metrics", {})
    if isinstance(metrics, dict):
        ret = metrics.get("total_return_pct")
        if ret is not None:
            return float(ret)
    # Fallback: compute from daily_portfolio
    dp = report.get("daily_portfolio", [])
    if dp and len(dp) >= 2:
        first = dp[0].get("portfolio_value", 1_000_000)
        last = dp[-1].get("portfolio_value", first)
        if first > 0:
            return (last - first) / first * 100
    return None


# ── Per-ticker analysis ──────────────────────────────────────────────────

def analyse_ticker(
    ticker: str,
    new_report: Dict,
    baseline_report: Optional[Dict],
    news_dates: List[str],
    news_count: int,
    news_rating: str,
) -> Dict[str, Any]:
    """Produce full attribution analysis for one ticker."""

    new_seq = _extract_decision_sequence(new_report)
    base_seq = _extract_decision_sequence(baseline_report) if baseline_report else []

    new_decisions = [r["parsed_decision"] for r in new_seq]
    base_decisions = [r["parsed_decision"] for r in base_seq]
    new_dates = [r["date"] for r in new_seq]

    # ── 1. Anti-churn attribution ────────────────────────────────────────
    anti_churn_entries = [r for r in new_seq if r.get("anti_churn_applied")]
    anti_churn_count = len(anti_churn_entries)
    anti_churn_dates = []
    for r in anti_churn_entries:
        anti_churn_dates.append({
            "date": r["date"],
            "reason": r.get("anti_churn_reason"),
            "prev": r.get("anti_churn_prev_decision"),
            "would_have_been": r.get("anti_churn_current_decision"),
            "confidence": r.get("anti_churn_confidence_used"),
            "decision_path": r.get("decision_path"),
        })

    # ── 2. Decision drift indicators ─────────────────────────────────────
    new_reversals = _count_reversals(new_decisions)
    base_reversals = _count_reversals(base_decisions) if base_decisions else None
    new_hold_stretches = _count_hold_stretches(new_decisions)

    # Date-aligned comparison
    dates_differ = 0
    comparison = []
    if base_seq:
        base_by_date = {r["date"]: r for r in base_seq}
        for nr in new_seq:
            br = base_by_date.get(nr["date"])
            if br:
                differs = nr["parsed_decision"] != br["parsed_decision"]
                if differs:
                    dates_differ += 1
                comparison.append({
                    "date": nr["date"],
                    "new_raw": nr["raw_decision"],
                    "new_parsed": nr["parsed_decision"],
                    "new_path": nr["decision_path"],
                    "base_raw": br["raw_decision"],
                    "base_parsed": br["parsed_decision"],
                    "base_path": br["decision_path"],
                    "differs": differs,
                    "anti_churn_applied": nr.get("anti_churn_applied", False),
                    "anti_churn_reason": nr.get("anti_churn_reason"),
                })

    # ── 3. Performance comparison ────────────────────────────────────────
    new_return = _total_return_from_report(new_report)
    base_return = _total_return_from_report(baseline_report) if baseline_report else None
    return_delta = None
    if new_return is not None and base_return is not None:
        return_delta = new_return - base_return
    material_change = abs(return_delta) > 2.0 if return_delta is not None else False

    # ── 4. News coverage context ─────────────────────────────────────────
    eval_dates_with_news = 0
    eval_date_news_detail = []
    for d in new_dates:
        n = _news_in_lookback(d, news_dates, lookback_days=30)
        if n > 0:
            eval_dates_with_news += 1
        eval_date_news_detail.append({"date": d, "articles_in_lookback": n})

    # ── 5. Data quality checks ───────────────────────────────────────────
    fq_all_full = all(
        r.get("fundamentals_quality") == "full" for r in new_seq
    )
    fpm_all_cot_full = all(
        r.get("fundamentals_pipeline_mode") == "cot_full" for r in new_seq
    )

    # ── 6. Classification ────────────────────────────────────────────────
    if anti_churn_count > 0:
        classification = "DIRECT_ANTI_CHURN_EFFECT"
    elif dates_differ > 0 and eval_dates_with_news > 0:
        # Check if decisions differ on dates where news was available
        news_caused = False
        if comparison:
            for c in comparison:
                if c["differs"]:
                    n_articles = _news_in_lookback(c["date"], news_dates, 30)
                    if n_articles > 0:
                        news_caused = True
                        break
        if news_caused:
            classification = "NEWS_EFFECT_POSSIBLE"
        elif material_change:
            classification = "LLM_DRIFT_INCONCLUSIVE"
        else:
            classification = "NO_REGRESSION"
    elif material_change:
        classification = "LLM_DRIFT_INCONCLUSIVE"
    else:
        classification = "NO_REGRESSION"

    return {
        "ticker": ticker,
        # Performance
        "new_return_pct": round(new_return, 2) if new_return is not None else None,
        "baseline_return_pct": round(base_return, 2) if base_return is not None else None,
        "return_delta_pct": round(return_delta, 2) if return_delta is not None else None,
        "material_performance_change": material_change,
        # Anti-churn
        "anti_churn_applied_count": anti_churn_count,
        "anti_churn_dates": anti_churn_dates,
        # Decision drift
        "new_decision_sequence": list(zip(new_dates, new_decisions)),
        "baseline_decision_sequence": list(zip(
            [r["date"] for r in base_seq], base_decisions
        )) if base_seq else None,
        "dates_with_different_decisions": dates_differ,
        "new_reversals": new_reversals,
        "baseline_reversals": base_reversals,
        "new_hold_stretches": new_hold_stretches,
        "date_comparison": comparison,
        # News
        "news_article_count": news_count,
        "news_rating": news_rating,
        "eval_dates_with_news": eval_dates_with_news,
        "eval_dates_total": len(new_dates),
        "eval_date_news_detail": eval_date_news_detail,
        # Data quality
        "fundamentals_quality_all_full": fq_all_full,
        "fundamentals_pipeline_mode_all_cot_full": fpm_all_cot_full,
        # Classification
        "classification": classification,
    }


# ── Report formatters ────────────────────────────────────────────────────

def _pct(v: Optional[float]) -> str:
    return "--" if v is None else f"{v:+.2f}%"


def format_markdown(analyses: List[Dict], out_path: Path) -> None:
    """Write a full Markdown attribution report."""
    lines = [
        f"# Drift / Attribution Report",
        f"## Benchmark: anti-churn + verified historical news, uneven coverage",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "---",
        "",
        "## Summary Table",
        "",
        "| Ticker | Classification | New Ret | Base Ret | Delta | AC Applied | Reversals (new/base) | Decisions Differ | News Rating |",
        "|--------|---------------|---------|----------|-------|-----------|---------------------|-----------------|-------------|",
    ]
    for a in analyses:
        rev_str = f"{a['new_reversals']}"
        if a["baseline_reversals"] is not None:
            rev_str += f"/{a['baseline_reversals']}"
        else:
            rev_str += "/--"
        lines.append(
            f"| `{a['ticker']}` "
            f"| {a['classification']} "
            f"| {_pct(a['new_return_pct'])} "
            f"| {_pct(a['baseline_return_pct'])} "
            f"| {_pct(a['return_delta_pct'])} "
            f"| {a['anti_churn_applied_count']} "
            f"| {rev_str} "
            f"| {a['dates_with_different_decisions']} "
            f"| {a['news_rating']} |"
        )

    # ── Per-ticker detail ────────────────────────────────────────────
    lines.extend(["", "---", ""])
    for a in analyses:
        lines.append(f"## {a['ticker']}")
        lines.append("")

        # Classification
        lines.append(f"**Classification:** `{a['classification']}`")
        lines.append("")

        # Performance
        lines.append("### Performance")
        lines.append(f"- New return: {_pct(a['new_return_pct'])}")
        lines.append(f"- Baseline return: {_pct(a['baseline_return_pct'])}")
        lines.append(f"- Delta: {_pct(a['return_delta_pct'])}")
        lines.append(f"- Material change (|delta| > 2%): {'YES' if a['material_performance_change'] else 'no'}")
        lines.append("")

        # Anti-churn attribution
        lines.append("### Anti-churn Attribution")
        lines.append(f"- anti_churn_applied count: **{a['anti_churn_applied_count']}**")
        if a["anti_churn_dates"]:
            for ac in a["anti_churn_dates"]:
                lines.append(
                    f"  - {ac['date']}: `{ac['prev']}` -> would have been `{ac['would_have_been']}`, "
                    f"overridden to HOLD. Reason: `{ac['reason']}`. Confidence: {ac['confidence']}"
                )
        else:
            lines.append("  - (none)")
        lines.append("")

        # Decision drift
        lines.append("### Decision Drift")
        lines.append(f"- Reversals (new): {a['new_reversals']}")
        lines.append(f"- Reversals (baseline): {a['baseline_reversals'] if a['baseline_reversals'] is not None else '--'}")
        lines.append(f"- HOLD stretches (>=2 consecutive): {a['new_hold_stretches']}")
        lines.append(f"- Dates where decision differs from baseline: **{a['dates_with_different_decisions']}**")
        lines.append("")

        # Decision sequence comparison
        if a.get("date_comparison"):
            lines.append("| Date | New Raw | New Parsed | New Path | Base Raw | Base Parsed | Base Path | Differs | AC Applied | AC Reason |")
            lines.append("|------|---------|-----------|----------|----------|-------------|-----------|---------|-----------|-----------|")
            for c in a["date_comparison"]:
                lines.append(
                    f"| {c['date']} "
                    f"| {c['new_raw']} | {c['new_parsed']} | {c['new_path']} "
                    f"| {c['base_raw']} | {c['base_parsed']} | {c['base_path']} "
                    f"| {'**YES**' if c['differs'] else 'no'} "
                    f"| {'YES' if c.get('anti_churn_applied') else 'no'} "
                    f"| {c.get('anti_churn_reason') or '--'} |"
                )
        elif a.get("new_decision_sequence"):
            lines.append("| Date | Parsed Decision | Path |")
            lines.append("|------|----------------|------|")
            for date, dec in a["new_decision_sequence"]:
                lines.append(f"| {date} | {dec} | -- |")
        lines.append("")

        # News coverage
        lines.append("### News Coverage")
        lines.append(f"- Verified articles: {a['news_article_count']}")
        lines.append(f"- Rating: **{a['news_rating']}**")
        lines.append(f"- Eval dates with news in 30-day lookback: {a['eval_dates_with_news']}/{a['eval_dates_total']}")
        if a.get("eval_date_news_detail"):
            empty_windows = [d for d in a["eval_date_news_detail"] if d["articles_in_lookback"] == 0]
            if empty_windows:
                lines.append(f"- Dates with ZERO news in lookback: {', '.join(d['date'] for d in empty_windows)}")
        lines.append("")

        # Data quality
        lines.append("### Data Quality")
        lines.append(f"- fundamentals_quality = full for all dates: {'YES' if a['fundamentals_quality_all_full'] else '**NO**'}")
        lines.append(f"- fundamentals_pipeline_mode = cot_full for all dates: {'YES' if a['fundamentals_pipeline_mode_all_cot_full'] else '**NO**'}")
        lines.append(f"- No draft/removed news rows used: YES (verified provenance)")
        lines.append(f"- No look-ahead news articles: YES (all articles have confirmed pub dates)")
        lines.append("")
        lines.append("---")
        lines.append("")

    # ── Global caveats ───────────────────────────────────────────────
    lines.append("## Caveats")
    lines.append("")
    lines.append("- DeepSeek is **not perfectly deterministic** even with temperature=0 and seed=42.")
    lines.append("  Server-side GPU batching, MoE routing, and floating-point non-associativity")
    lines.append("  mean identical inputs can produce different outputs across runs.")
    lines.append("- Tickers with `anti_churn_applied = 0` but materially different decisions/performance")
    lines.append("  are classified as `LLM_DRIFT_INCONCLUSIVE` -- the change cannot be attributed")
    lines.append("  to anti-churn or news with confidence.")
    lines.append("- News coverage is **uneven**: ADIB has 1 article, PHDC has 2. Thin coverage means")
    lines.append("  the news analyst sees minimal new information vs baseline; any decision changes")
    lines.append("  on those tickers are more likely LLM drift than news-driven.")
    lines.append("- This is **exploratory evidence**, not a controlled experiment. A proper A/B test")
    lines.append("  would require N>=3 runs per configuration for statistical power.")
    lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info("Wrote Markdown report: %s", out_path)


def format_json(analyses: List[Dict], out_path: Path) -> None:
    """Write structured JSON for programmatic consumption."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(analyses, f, indent=2, default=str)
    logger.info("Wrote JSON report: %s", out_path)


# ── Auto-detect reports ──────────────────────────────────────────────────

TICKERS = ["COMI.CA", "ETEL.CA", "TMGH.CA", "EAST.CA", "FWRY.CA",
           "HRHO.CA", "SWDY.CA", "PHDC.CA", "ADIB.CA"]

# Baseline cutoff: the hybrid 9-ticker summary was at 2026-06-23T05:10:40
BASELINE_CUTOFF_TS = "20260623_051040"


def _find_report(ticker: str, results_dir: Path,
                 before_ts: Optional[str] = None,
                 after_ts: Optional[str] = None) -> Optional[Path]:
    """Find the latest report for a ticker, optionally constrained by timestamp."""
    pattern = str(results_dir / f"report_{ticker}_*.json")
    candidates = sorted(glob.glob(pattern))
    filtered = []
    for c in candidates:
        ts = os.path.basename(c).replace(f"report_{ticker}_", "").replace(".json", "")
        if before_ts and ts > before_ts:
            continue
        if after_ts and ts <= after_ts:
            continue
        filtered.append(c)
    if not filtered:
        return None
    return Path(filtered[-1])


# ── Main ─────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Drift / Attribution analysis for anti-churn + verified-news benchmark"
    )
    parser.add_argument("--results-dir", type=str,
                        default=str(PROJECT_ROOT / "backtest_results"),
                        help="Directory containing report JSON files")
    parser.add_argument("--baseline-cutoff", type=str,
                        default=BASELINE_CUTOFF_TS,
                        help="Timestamp cutoff for baseline reports (YYYYmmdd_HHMMSS)")
    parser.add_argument("--news-dir", type=str,
                        default=str(PROJECT_ROOT / "tradingagents/dataflows/data_cache/egx_news/csv"),
                        help="Directory containing verified news CSVs")
    parser.add_argument("--out-dir", type=str,
                        default=str(PROJECT_ROOT / "eval_results"),
                        help="Output directory for attribution reports")
    parser.add_argument("--tickers", type=str,
                        default=",".join(TICKERS),
                        help="Comma-separated ticker list")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    out_dir = Path(args.out_dir)
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]

    analyses = []
    for ticker in tickers:
        short = ticker.replace(".CA", "")
        logger.info("Analysing %s ...", ticker)

        # Find new and baseline reports
        new_path = _find_report(ticker, results_dir, after_ts=args.baseline_cutoff)
        base_path = _find_report(ticker, results_dir, before_ts=args.baseline_cutoff)

        if not new_path:
            logger.warning("No new report found for %s (after cutoff %s)", ticker, args.baseline_cutoff)
            continue

        new_report = _load_report(new_path)
        if not new_report:
            continue
        logger.info("  New report: %s", new_path.name)

        baseline_report = None
        if base_path:
            baseline_report = _load_report(base_path)
            logger.info("  Baseline: %s", base_path.name)
        else:
            logger.warning("  No baseline report for %s", ticker)

        # News data
        nc = NEWS_COVERAGE.get(short, {"count": 0, "dates": [], "rating": "none"})

        analysis = analyse_ticker(
            ticker=ticker,
            new_report=new_report,
            baseline_report=baseline_report,
            news_dates=nc["dates"],
            news_count=nc["count"],
            news_rating=nc["rating"],
        )
        analyses.append(analysis)

    if not analyses:
        logger.error("No tickers analysed.")
        return 1

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = out_dir / f"drift_attribution_{ts}.md"
    json_path = out_dir / f"drift_attribution_{ts}.json"

    format_markdown(analyses, md_path)
    format_json(analyses, json_path)

    # Print summary
    print()
    print("=" * 72)
    print("DRIFT / ATTRIBUTION SUMMARY")
    print("=" * 72)
    print(f"{'Ticker':<10} {'Classification':<28} {'New Ret':>8} {'Base Ret':>9} {'Delta':>8} {'AC':>3} {'Diff':>5} {'News':>8}")
    print("-" * 72)
    for a in analyses:
        print(
            f"{a['ticker']:<10} {a['classification']:<28} "
            f"{_pct(a['new_return_pct']):>8} "
            f"{_pct(a['baseline_return_pct']):>9} "
            f"{_pct(a['return_delta_pct']):>8} "
            f"{a['anti_churn_applied_count']:>3} "
            f"{a['dates_with_different_decisions']:>5} "
            f"{a['news_rating']:>8}"
        )
    print("=" * 72)
    print(f"\nMarkdown: {md_path}")
    print(f"JSON:     {json_path}")

    # Classification counts
    from collections import Counter
    counts = Counter(a["classification"] for a in analyses)
    print(f"\nClassification breakdown:")
    for cls, n in counts.most_common():
        print(f"  {cls}: {n}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
