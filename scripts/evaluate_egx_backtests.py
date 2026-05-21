"""
EGX multi-ticker evaluation harness
====================================
Runs ``scripts/backtester.py`` sequentially across a configurable EGX ticker
set, then aggregates the per-ticker JSON reports into a single CSV + Markdown
summary with look-ahead-free metrics and EGX30 buy-and-hold benchmark
relative alpha.

Re-uses ``tradingagents.rl.walkforward.compute_arm_metrics`` for the metric
ingestion — that helper already handles:
  - the §C3 CBE-policy risk-free rate (default 0.24)
  - the §C1 closed-trade win-rate with Wilson 95% CI
  - the §C4-aligned benchmark return / alpha

Usage
-----
    python scripts/evaluate_egx_backtests.py \\
        --tickers ETEL.CA,TMGH.CA \\
        --start 2024-01-01 --end 2024-03-31 \\
        --interval 20 --capital 1000000

Outputs (timestamped) under ``eval_results/``:
  - per-ticker JSON reports come from backtester.save_results (the canonical
    on-disk source; the harness only reads them back).
  - ``multi_ticker_summary_<timestamp>.csv``
  - ``multi_ticker_summary_<timestamp>.md``

The script is resumable: a ticker whose latest matching JSON report already
exists for the same ``(start, end, interval)`` triple is skipped.
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
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add project root to path so `from scripts.backtester ...` works when this
# script is invoked directly (`python scripts/evaluate_egx_backtests.py`).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tradingagents.rl.walkforward import (
    ArmMetrics,
    _wilson_ci,
    compute_arm_metrics,
    default_risk_free_rate,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("evaluate_egx_backtests")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_RESULTS_DIR = PROJECT_ROOT / "backtest_results"
EVAL_RESULTS_DIR = PROJECT_ROOT / "eval_results"


# ─────────────────────────────────────────────────────────────────────────────
# Per-ticker run + report discovery
# ─────────────────────────────────────────────────────────────────────────────


def _latest_report_for_ticker(ticker: str) -> Optional[Path]:
    """Return the newest report_<ticker>_<ts>.json in backtest_results/."""
    pattern = str(BACKTEST_RESULTS_DIR / f"report_{ticker}_*.json")
    candidates = sorted(glob.glob(pattern))
    if not candidates:
        return None
    return Path(candidates[-1])


def _report_matches_window(report_path: Path, start: str, end: str) -> bool:
    """Best-effort check: does this JSON's first/last daily_portfolio date
    fall inside [start, end]? Used to skip re-running tickers whose previous
    report covers the same window."""
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return False
    daily = d.get("daily_portfolio") or []
    if not daily:
        return False
    first = str(daily[0].get("date", ""))
    last = str(daily[-1].get("date", ""))
    return first >= start and last <= end and first <= end and last >= start


def run_one_ticker(
    ticker: str,
    start: str,
    end: str,
    interval: int,
    capital: float,
    analysts: List[str],
    cooldown: int,
    benchmark: Optional[str],
    train_end: Optional[str],
    skip_if_recent: bool,
    resume: bool,
) -> Optional[Path]:
    """Run one backtest. Returns the path to the produced JSON report (or the
    existing one if skipped). Returns None if the run failed completely."""
    if skip_if_recent:
        existing = _latest_report_for_ticker(ticker)
        if existing and _report_matches_window(existing, start, end):
            logger.info(
                "[SKIP] %s — existing report %s covers the requested window.",
                ticker, existing.name,
            )
            return existing

    # Import inside the function so a syntax error in backtester.py doesn't
    # kill the whole harness at import time.
    from scripts.backtester import BacktestingEngine  # noqa: WPS433

    logger.info("[RUN] %s | %s → %s | capital=%s EGP", ticker, start, end, capital)
    engine = BacktestingEngine(
        initial_capital=float(capital),
        benchmark_ticker=benchmark,
    )
    try:
        engine.run_backtest(
            ticker, start, end,
            interval_days=interval,
            analysts=analysts,
            cooldown=cooldown,
            train_end_date=train_end,
            resume=resume,
        )
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        logger.exception("Backtest for %s failed: %s", ticker, exc)
        return None

    return _latest_report_for_ticker(ticker)


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation
# ─────────────────────────────────────────────────────────────────────────────


def _pooled_wilson_ci(per_ticker: List[ArmMetrics]) -> Tuple[Optional[float], Optional[float], int, int]:
    """Pool wins / closed across tickers and return Wilson 95% CI + counts."""
    wins = 0
    closed = 0
    for m in per_ticker:
        n_closed = int(m.n_trades_closed or 0)
        if m.win_rate is not None and n_closed > 0:
            wins += int(round(m.win_rate * n_closed))
            closed += n_closed
    if closed == 0:
        return None, None, 0, 0
    lo, hi = _wilson_ci(wins, closed)
    return lo, hi, wins, closed


def _safe_mean(xs: List[Optional[float]]) -> Optional[float]:
    vs = [float(x) for x in xs if x is not None and not math.isnan(float(x))]
    if not vs:
        return None
    return sum(vs) / len(vs)


def aggregate(per_ticker: List[ArmMetrics]) -> Dict[str, Any]:
    if not per_ticker:
        return {}
    total_returns = [m.total_return_pct for m in per_ticker]
    sharpes = [m.sharpe_ratio for m in per_ticker]
    drawdowns = [m.max_drawdown_pct for m in per_ticker]
    alphas = [m.benchmark_alpha_pct for m in per_ticker]
    n_beat_bench = sum(
        1 for a in alphas
        if a is not None and a > 0
    )
    n_positive_sharpe = sum(1 for s in sharpes if s > 0)
    lo, hi, wins, closed = _pooled_wilson_ci(per_ticker)
    return {
        "n_tickers": len(per_ticker),
        "mean_total_return_pct": _safe_mean(total_returns),
        "mean_sharpe": _safe_mean(sharpes),
        "mean_max_drawdown_pct": _safe_mean(drawdowns),
        "mean_alpha_pct": _safe_mean([a for a in alphas if a is not None]),
        "n_beat_egx30": n_beat_bench,
        "n_positive_sharpe": n_positive_sharpe,
        "pooled_wins": wins,
        "pooled_closed_trades": closed,
        "pooled_winrate_ci_lo": lo,
        "pooled_winrate_ci_hi": hi,
        "risk_free_rate_used": default_risk_free_rate(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Output writers
# ─────────────────────────────────────────────────────────────────────────────


CSV_COLUMNS = [
    "ticker",
    "n_trading_days",
    "n_trades_total",
    "n_trades_closed",
    "total_return_pct",
    "annualized_return_pct",
    "sharpe_ratio",
    "calmar_ratio",
    "max_drawdown_pct",
    "win_rate",
    "win_rate_ci_lo",
    "win_rate_ci_hi",
    "benchmark_return_pct",
    "benchmark_alpha_pct",
    "final_portfolio_egp",
    "risk_free_rate_used",
    "notes",
]


def write_csv(rows: List[ArmMetrics], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for m in rows:
            d = m.to_dict()
            w.writerow([
                d.get("arm"),
                d.get("n_trading_days"),
                d.get("n_trades_total"),
                d.get("n_trades_closed"),
                _fmt_num(d.get("total_return_pct")),
                _fmt_num(d.get("annualized_return_pct")),
                _fmt_num(d.get("sharpe_ratio")),
                _fmt_num(d.get("calmar_ratio")),
                _fmt_num(d.get("max_drawdown_pct")),
                _fmt_num(d.get("win_rate")),
                _fmt_num(d.get("win_rate_ci_lo")),
                _fmt_num(d.get("win_rate_ci_hi")),
                _fmt_num(d.get("benchmark_return_pct")),
                _fmt_num(d.get("benchmark_alpha_pct")),
                _fmt_num(d.get("final_portfolio_egp")),
                _fmt_num(d.get("risk_free_rate_used")),
                "; ".join(d.get("notes") or []),
            ])


def _fmt_num(v: Any) -> str:
    if v is None:
        return ""
    try:
        return f"{float(v):.4f}"
    except (TypeError, ValueError):
        return str(v)


def write_markdown(
    rows: List[ArmMetrics],
    summary: Dict[str, Any],
    args: argparse.Namespace,
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def _pct(v: Optional[float]) -> str:
        return "—" if v is None else f"{float(v):.2f}%"

    def _f2(v: Optional[float]) -> str:
        return "—" if v is None else f"{float(v):.2f}"

    lines: List[str] = []
    lines.append(f"# EGX multi-ticker evaluation — {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append(f"- **Tickers**: `{args.tickers}`")
    lines.append(f"- **Window**: {args.start} → {args.end}")
    lines.append(f"- **Interval**: {args.interval} calendar days between evaluations")
    lines.append(f"- **Capital**: {float(args.capital):,.2f} EGP")
    lines.append(f"- **Benchmark**: EGX30 buy-and-hold")
    lines.append(
        f"- **Risk-free rate (CBE proxy)**: {summary.get('risk_free_rate_used', default_risk_free_rate()):.4f}"
    )
    lines.append("")
    lines.append("## Per-ticker metrics")
    lines.append("")
    lines.append("| Ticker | Days | Trades | Closed | Total Ret. | Sharpe | Max DD | Win Rate | Bench Ret. | Alpha |")
    lines.append("|--------|-----:|-------:|-------:|-----------:|-------:|-------:|---------:|-----------:|------:|")
    for m in rows:
        wr_str = "—"
        if m.win_rate is not None:
            wr_str = f"{m.win_rate*100:.1f}%"
            if m.win_rate_ci_lo is not None and m.win_rate_ci_hi is not None:
                wr_str += f" [{m.win_rate_ci_lo*100:.1f}–{m.win_rate_ci_hi*100:.1f}]"
        lines.append(
            f"| `{m.arm}` "
            f"| {m.n_trading_days} "
            f"| {m.n_trades_total} "
            f"| {m.n_trades_closed} "
            f"| {_pct(m.total_return_pct)} "
            f"| {_f2(m.sharpe_ratio)} "
            f"| {_pct(m.max_drawdown_pct)} "
            f"| {wr_str} "
            f"| {_pct(m.benchmark_return_pct)} "
            f"| {_pct(m.benchmark_alpha_pct)} |"
        )

    lines.append("")
    lines.append("## Pooled summary")
    lines.append("")
    n = summary.get("n_tickers", 0)
    lines.append(f"- **Tickers evaluated**: {n}")
    lines.append(f"- **Mean total return**: {_pct(summary.get('mean_total_return_pct'))}")
    lines.append(f"- **Mean Sharpe**: {_f2(summary.get('mean_sharpe'))}")
    lines.append(f"- **Mean max drawdown**: {_pct(summary.get('mean_max_drawdown_pct'))}")
    lines.append(f"- **Mean alpha vs EGX30**: {_pct(summary.get('mean_alpha_pct'))}")
    lines.append(f"- **Tickers beating EGX30**: {summary.get('n_beat_egx30', 0)} / {n}")
    lines.append(f"- **Tickers with positive Sharpe**: {summary.get('n_positive_sharpe', 0)} / {n}")
    pooled_closed = summary.get("pooled_closed_trades", 0)
    if pooled_closed:
        wins = summary.get("pooled_wins", 0)
        lo = summary.get("pooled_winrate_ci_lo")
        hi = summary.get("pooled_winrate_ci_hi")
        ci = f" [95% CI {lo*100:.1f}–{hi*100:.1f}]" if (lo is not None and hi is not None) else ""
        lines.append(
            f"- **Pooled closed-trade win rate**: "
            f"{wins/pooled_closed*100:.1f}% ({wins}/{pooled_closed}){ci}"
        )
    else:
        lines.append("- **Pooled closed-trade win rate**: — (no closed trades)")

    lines.append("")
    lines.append(
        "_Honest metrics. No look-ahead — see MEMORY.md §C1/C3/C4 for the "
        "removed `_evaluate_trade_outcomes` look-ahead path, the CBE risk-free "
        "rate, and the strict benchmark window alignment._"
    )
    lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def _default_tickers() -> str:
    return "ETEL.CA,TMGH.CA"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run EGX backtests for multiple tickers and emit a "
                    "pooled CSV + Markdown summary.",
    )
    parser.add_argument("--tickers",  type=str, default=_default_tickers(),
                        help="Comma-separated ticker list (default: ETEL.CA,TMGH.CA)")
    parser.add_argument("--start",    type=str, default="2024-01-01")
    parser.add_argument("--end",      type=str, default="2024-03-31")
    parser.add_argument("--interval", type=int, default=20,
                        help="Calendar days between agent evaluations")
    parser.add_argument("--capital",  type=float, default=1_000_000.0)
    parser.add_argument("--analysts", type=str,
                        default="market,fundamentals,news,social")
    parser.add_argument("--benchmark", type=str, default="^EGX30")
    parser.add_argument("--cooldown",  type=int, default=5)
    parser.add_argument("--train-end", type=str, default="none")
    parser.add_argument("--out-dir",   type=str, default=str(EVAL_RESULTS_DIR))
    parser.add_argument("--force",     action="store_true",
                        help="Re-run every ticker even if a matching report exists.")
    parser.add_argument("--resume",    action="store_true",
                        help="Pass --resume through to the per-ticker backtester so "
                             "interrupted runs continue from their partial checkpoint.")
    args = parser.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    analysts = [a.strip() for a in args.analysts.split(",") if a.strip()]
    benchmark = None if args.benchmark.lower() == "none" else args.benchmark
    train_end = None if args.train_end.lower() == "none" else args.train_end

    if not tickers:
        logger.error("No tickers provided.")
        return 2

    logger.info(
        "Evaluating %d tickers (%s) over %s → %s",
        len(tickers), ",".join(tickers), args.start, args.end,
    )

    rows: List[ArmMetrics] = []
    failures: List[str] = []
    rfr = default_risk_free_rate()

    for ticker in tickers:
        report_path = run_one_ticker(
            ticker=ticker,
            start=args.start,
            end=args.end,
            interval=args.interval,
            capital=args.capital,
            analysts=analysts,
            cooldown=args.cooldown,
            benchmark=benchmark,
            train_end=train_end,
            skip_if_recent=not args.force,
            resume=args.resume,
        )
        if not report_path or not report_path.exists():
            logger.warning("No report for %s — skipping in aggregation.", ticker)
            failures.append(ticker)
            continue
        try:
            metrics = compute_arm_metrics(
                report_path,
                arm=ticker,
                risk_free_rate=rfr,
            )
        except Exception as exc:
            logger.exception("Failed to compute metrics for %s: %s", ticker, exc)
            failures.append(ticker)
            continue
        rows.append(metrics)

    if not rows:
        logger.error("All %d tickers failed to produce metrics.", len(tickers))
        return 3

    summary = aggregate(rows)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir)
    csv_path = out_dir / f"multi_ticker_summary_{ts}.csv"
    md_path = out_dir / f"multi_ticker_summary_{ts}.md"
    write_csv(rows, csv_path)
    write_markdown(rows, summary, args, md_path)

    print()
    print("=" * 70)
    print(f"Evaluated {len(rows)} ticker(s), failed {len(failures)}.")
    print(f"CSV  → {csv_path}")
    print(f"MD   → {md_path}")
    print("=" * 70)
    print()
    if failures:
        print(f"Failures: {', '.join(failures)}")
    print(f"Mean Sharpe: {summary.get('mean_sharpe')}")
    print(f"Mean alpha vs EGX30: {summary.get('mean_alpha_pct')}")
    print(f"Tickers beating EGX30: "
          f"{summary.get('n_beat_egx30', 0)}/{summary.get('n_tickers', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
