"""
Thesis report builder (Chapters 7 & 8)
======================================
Reads the backtest reports listed in a ``run_thesis_backtests.py`` manifest
(or auto-discovers the latest reports) and emits, into ``thesis_results/``:

  * ``thesis_summary_<ts>.csv``  — per-(ticker,profile) metrics + decision quality
  * ``thesis_summary_<ts>.md``   — the same as a Markdown table + pooled summary
  * ``charts/*.png``             — publication-quality figures:
        equity_curves_<profile>.png, returns_by_ticker.png, sharpe_by_ticker.png,
        confusion_<profile>.png, calibration_<profile>.png,
        hitrate_vs_random_<profile>.png, decision_distribution_<profile>.png

All numbers come straight from the engine's JSON reports — this script does no
new modelling. Decision-quality is pooled across tickers from the per-prediction
rows (each carries its realized forward return + correctness, computed leak-safe
post-hoc by ``tradingagents/backtest/decision_metrics.py``).

Usage
-----
    python scripts/thesis_report.py                       # latest manifest
    python scripts/thesis_report.py --manifest thesis_results/manifest_*.json
    python scripts/thesis_report.py --reports backtest_results/report_*.json
"""

from __future__ import annotations

import argparse
import csv
import glob
import io
import json
import math
import os
import sys
from collections import defaultdict

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from tradingagents.rl.walkforward import _wilson_ci, default_risk_free_rate  # noqa: E402

try:
    from scipy import stats as _stats  # noqa: E402
except Exception:
    _stats = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_RESULTS_DIR = PROJECT_ROOT / "backtest_results"
THESIS_RESULTS_DIR = PROJECT_ROOT / "thesis_results"
CHARTS_DIR = THESIS_RESULTS_DIR / "charts"

PRIMARY_H = "10"
_SIGNAL = {"BUY": 1.0, "HOLD": 0.0, "SELL": -1.0}
plt.rcParams.update({
    "figure.dpi": 130, "savefig.dpi": 150, "font.size": 10,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.alpha": 0.25,
})
C_STRAT, C_BENCH, C_OK, C_BAD = "#0f766e", "#94a3b8", "#059669", "#e11d48"


# ─────────────────────────────────────────────────────────────────────────────
# Parsing helpers
# ─────────────────────────────────────────────────────────────────────────────


def _pf(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).replace("%", "").replace(",", "").replace("EGP", "").strip())
    except (TypeError, ValueError):
        return None


@dataclass
class Run:
    ticker: str
    profile: str
    path: Path
    report: Dict[str, Any]

    @property
    def metrics(self) -> Dict[str, Any]:
        return self.report.get("metrics") or {}

    @property
    def dq(self) -> Dict[str, Any]:
        return (self.report.get("decision_quality") or {}).get("horizons", {}).get(PRIMARY_H, {})

    @property
    def predictions(self) -> List[Dict[str, Any]]:
        return self.report.get("predictions") or []

    def equity_norm(self) -> Tuple[List[str], List[float]]:
        daily = self.report.get("daily_portfolio") or []
        pts = [(r.get("date"), r.get("portfolio_value", r.get("equity", r.get("value"))))
               for r in daily if r.get("date")]
        pts = [(d, float(v)) for d, v in pts if v]
        if not pts:
            return [], []
        base = pts[0][1] or 1.0
        return [d for d, _ in pts], [100.0 * v / base for _, v in pts]

    def benchmark_norm(self) -> Tuple[List[str], List[float]]:
        bm = self.report.get("benchmark_history") or []
        pts = [(r.get("date"), r.get("value", r.get("equity"))) for r in bm if r.get("date")]
        pts = [(d, float(v)) for d, v in pts if v]
        if not pts:
            return [], []
        base = pts[0][1] or 1.0
        return [d for d, _ in pts], [100.0 * v / base for _, v in pts]


def load_runs(manifest: Optional[Path], report_globs: List[str]) -> List[Run]:
    runs: List[Run] = []
    paths_profiles: List[Tuple[Path, Optional[str]]] = []

    if manifest and manifest.exists():
        with open(manifest, "r", encoding="utf-8") as f:
            m = json.load(f)
        for r in m.get("runs", []):
            p = r.get("report")
            if p and Path(p).exists():
                paths_profiles.append((Path(p), r.get("profile")))
    else:
        globs = report_globs or [str(BACKTEST_RESULTS_DIR / "report_*.json")]
        for g in globs:
            for p in glob.glob(g):
                paths_profiles.append((Path(p), None))

    for path, profile in paths_profiles:
        try:
            with open(path, "r", encoding="utf-8") as f:
                rep = json.load(f)
        except Exception:
            continue
        rc = rep.get("run_config") or {}
        runs.append(Run(
            ticker=str(rep.get("session") or path.stem),
            profile=profile or rc.get("decision_profile") or "live_faithful",
            path=path,
            report=rep,
        ))
    return runs


# ─────────────────────────────────────────────────────────────────────────────
# Pooled decision quality (from per-prediction rows)
# ─────────────────────────────────────────────────────────────────────────────


def pool_decision_quality(runs: List[Run]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for r in runs:
        rows.extend(r.predictions)

    actionable = [r for r in rows if r.get("decision") in ("BUY", "SELL") and r.get("correct") is not None]
    a_correct = sum(1 for r in actionable if r.get("correct"))
    a_n = len(actionable)
    hit = a_correct / a_n if a_n else None
    ci = _wilson_ci(a_correct, a_n) if a_n else (None, None)
    binom_p = None
    if a_n and _stats is not None:
        binom_p = float(_stats.binomtest(a_correct, a_n, 0.5, alternative="greater").pvalue)

    sig, fwd = [], []
    for r in rows:
        f = r.get("forward_return_10d")
        if f is not None and r.get("decision") in _SIGNAL:
            sig.append(_SIGNAL[r["decision"]])
            fwd.append(float(f))
    ic = ic_p = None
    if len(sig) >= 3 and len(set(sig)) > 1 and _stats is not None:
        res = _stats.spearmanr(sig, fwd)
        ic, ic_p = float(res.statistic), float(res.pvalue)

    confusion = {a: {"UP": 0, "FLAT": 0, "DOWN": 0} for a in ("BUY", "HOLD", "SELL")}
    for r in rows:
        d, rd = r.get("decision"), r.get("realized_direction")
        if d in confusion and rd in ("UP", "FLAT", "DOWN"):
            confusion[d][rd] += 1

    dist = defaultdict(int)
    for r in rows:
        dist[r.get("decision")] += 1

    return {
        "n_decisions": len(rows),
        "actionable_n": a_n,
        "actionable_hit_rate": hit,
        "ci_lo": ci[0], "ci_hi": ci[1],
        "binomial_p_vs_50pct": binom_p,
        "information_coefficient": ic,
        "ic_p_value": ic_p,
        "confusion_matrix": confusion,
        "action_distribution": dict(dist),
    }


def pool_calibration(runs: List[Run], buckets=(0.0, 0.4, 0.55, 0.7, 0.85, 1.0)) -> List[Dict[str, Any]]:
    rows = [r for run in runs for r in run.predictions
            if r.get("decision") in ("BUY", "SELL") and r.get("correct") is not None
            and r.get("confidence") is not None]
    out = []
    for lo, hi in zip(buckets[:-1], buckets[1:]):
        sel = [r for r in rows if lo <= float(r["confidence"]) < hi
               or (hi == buckets[-1] and float(r["confidence"]) == hi)]
        n = len(sel)
        c = sum(1 for r in sel if r.get("correct"))
        out.append({"bucket": f"{lo:.2f}-{hi:.2f}", "mid": (lo + hi) / 2, "n": n,
                    "hit_rate": (c / n) if n else None})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Tables
# ─────────────────────────────────────────────────────────────────────────────

CSV_COLS = [
    "ticker", "profile", "total_return_pct", "benchmark_return_pct", "alpha_pct",
    "sharpe", "sortino", "max_drawdown_pct", "win_rate_pct", "closed_trades",
    "decisions", "actionable_hit_rate_10d", "info_coeff_10d", "final_equity_egp",
]


def run_row(r: Run) -> List[Any]:
    m, dq = r.metrics, r.dq
    return [
        r.ticker, r.profile,
        _pf(m.get("Total Return")), _pf(m.get("Benchmark Return")), _pf(m.get("Alpha")),
        _pf(m.get("Sharpe Ratio")), _pf(m.get("Sortino Ratio")),
        _pf(m.get("Max Drawdown")), _pf(m.get("Win Rate")), m.get("Closed Trades"),
        (r.report.get("decision_quality") or {}).get("n_decisions"),
        dq.get("actionable_hit_rate"), dq.get("information_coefficient"),
        _pf(m.get("Final Portfolio")),
    ]


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def write_tables(runs: List[Run], pooled: Dict[str, Dict[str, Any]], ts: str) -> Tuple[Path, Path]:
    THESIS_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = THESIS_RESULTS_DIR / f"thesis_summary_{ts}.csv"
    md_path = THESIS_RESULTS_DIR / f"thesis_summary_{ts}.md"

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLS)
        for r in sorted(runs, key=lambda x: (x.profile, x.ticker)):
            w.writerow([_fmt(v) for v in run_row(r)])

    lines: List[str] = []
    lines.append(f"# Thesis backtest results — {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append(f"- Risk-free rate (CBE proxy, Sharpe/metrics): {default_risk_free_rate():.4f}")
    lines.append("- Analysts: market + fundamentals (news/social have no historical archive).")
    lines.append("- Decision quality: 10-trading-day forward horizon, ±1% dead-band, leak-safe.")
    lines.append("")
    for profile in sorted({r.profile for r in runs}):
        prs = [r for r in runs if r.profile == profile]
        lines.append(f"## Profile: `{profile}`")
        lines.append("")
        lines.append("| Ticker | Total Ret | EGX30 | Alpha | Sharpe | Sortino | Max DD | Win | Decisions | Hit@10d | IC |")
        lines.append("|--------|----------:|------:|------:|-------:|--------:|-------:|----:|----------:|--------:|---:|")
        for r in sorted(prs, key=lambda x: x.ticker):
            m, dq = r.metrics, r.dq
            def pc(v):
                f = _pf(v)
                return "—" if f is None else f"{f:.2f}%"
            hit = dq.get("actionable_hit_rate")
            ic = dq.get("information_coefficient")
            ndec = (r.report.get("decision_quality") or {}).get("n_decisions") or 0
            lines.append(
                f"| `{r.ticker}` | {pc(m.get('Total Return'))} | {pc(m.get('Benchmark Return'))} "
                f"| {pc(m.get('Alpha'))} | {m.get('Sharpe Ratio','—')} | {m.get('Sortino Ratio','—')} "
                f"| {pc(m.get('Max Drawdown'))} | {pc(m.get('Win Rate'))} | {ndec} "
                f"| {f'{hit*100:.0f}%' if hit is not None else '—'} "
                f"| {f'{ic:.2f}' if ic is not None else '—'} |"
            )
        lines.append("")
        pq = pooled.get(profile, {})
        if pq:
            hit = pq.get("actionable_hit_rate")
            lo, hi = pq.get("ci_lo"), pq.get("ci_hi")
            p = pq.get("binomial_p_vs_50pct")
            ic = pq.get("information_coefficient")
            ci = f" [95% CI {lo*100:.0f}–{hi*100:.0f}%]" if (lo is not None and hi is not None) else ""
            lines.append(f"### Pooled decision quality ({profile})")
            lines.append("")
            lines.append(f"- Decisions scored: **{pq.get('n_decisions')}** "
                         f"(actionable BUY/SELL: {pq.get('actionable_n')})")
            lines.append(f"- Actionable hit-rate @10d: **{hit*100:.1f}%**{ci}" if hit is not None
                         else "- Actionable hit-rate @10d: — (no actionable calls)")
            if p is not None:
                lines.append(f"- One-sided binomial p vs coin-flip: **{('<0.001' if p<0.001 else f'{p:.3f}')}**"
                             + (" → statistically significant skill (p<0.05)" if p < 0.05 else ""))
            if ic is not None:
                lines.append(f"- Information Coefficient (Spearman): **{ic:.3f}** "
                             f"(p={pq.get('ic_p_value'):.3f})")
            lines.append(f"- Action mix: {pq.get('action_distribution')}")
            lines.append("")
    lines.append("_All figures are produced directly from the engine JSON reports. "
                 "No look-ahead — forward returns are scored post-hoc and never fed "
                 "to the agents (MEMORY.md §C1)._")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return csv_path, md_path


# ─────────────────────────────────────────────────────────────────────────────
# Charts
# ─────────────────────────────────────────────────────────────────────────────


def _save(fig, name: str) -> Path:
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    path = CHARTS_DIR / name
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def chart_equity_curves(runs: List[Run], profile: str) -> Optional[Path]:
    prs = [r for r in runs if r.profile == profile and r.equity_norm()[0]]
    if not prs:
        return None
    n = len(prs)
    cols = min(3, n)
    rows_n = math.ceil(n / cols)
    fig, axes = plt.subplots(rows_n, cols, figsize=(4.6 * cols, 3.0 * rows_n), squeeze=False)
    for i, r in enumerate(prs):
        ax = axes[i // cols][i % cols]
        ed, ev = r.equity_norm()
        bd, bv = r.benchmark_norm()
        x = list(range(len(ev)))
        ax.plot(x, ev, color=C_STRAT, lw=2, label="Strategy")
        if bv:
            ax.plot(range(len(bv)), bv, color=C_BENCH, lw=1.4, ls="--", label="EGX30")
        ax.axhline(100, color="#cbd5e1", lw=0.8, ls=":")
        ax.set_title(r.ticker, fontsize=11, fontweight="bold")
        ax.set_ylabel("Indexed = 100")
        ax.legend(fontsize=8, loc="best")
    for j in range(n, rows_n * cols):
        axes[j // cols][j % cols].axis("off")
    fig.suptitle(f"Equity curve vs EGX30 — {profile}", fontsize=13, fontweight="bold")
    return _save(fig, f"equity_curves_{profile}.png")


def chart_returns_by_ticker(runs: List[Run]) -> Optional[Path]:
    by = defaultdict(dict)
    for r in runs:
        by[r.ticker][r.profile] = _pf(r.metrics.get("Total Return"))
    bench = {}
    for r in runs:
        bench[r.ticker] = _pf(r.metrics.get("Benchmark Return"))
    if not by:
        return None
    tickers = sorted(by)
    profiles = sorted({r.profile for r in runs})
    x = np.arange(len(tickers))
    w = 0.8 / max(len(profiles), 1)
    fig, ax = plt.subplots(figsize=(max(7, 1.2 * len(tickers)), 4.2))
    for k, prof in enumerate(profiles):
        vals = [by[t].get(prof) or 0.0 for t in tickers]
        ax.bar(x + k * w, vals, w, label=f"{prof}")
    bvals = [bench.get(t) or 0.0 for t in tickers]
    ax.plot(x + 0.4 - w / 2, bvals, "D", color=C_BENCH, label="EGX30 B&H")
    ax.axhline(0, color="#475569", lw=0.8)
    ax.set_xticks(x + 0.4 - w / 2)
    ax.set_xticklabels(tickers, rotation=30, ha="right")
    ax.set_ylabel("Total return (%)")
    ax.set_title("Total return by ticker vs EGX30")
    ax.legend(fontsize=8)
    return _save(fig, "returns_by_ticker.png")


def chart_sharpe_by_ticker(runs: List[Run]) -> Optional[Path]:
    by = defaultdict(dict)
    for r in runs:
        by[r.ticker][r.profile] = _pf(r.metrics.get("Sharpe Ratio"))
    if not by:
        return None
    tickers = sorted(by)
    profiles = sorted({r.profile for r in runs})
    x = np.arange(len(tickers))
    w = 0.8 / max(len(profiles), 1)
    fig, ax = plt.subplots(figsize=(max(7, 1.2 * len(tickers)), 4.2))
    for k, prof in enumerate(profiles):
        ax.bar(x + k * w, [by[t].get(prof) or 0.0 for t in tickers], w, label=prof)
    ax.axhline(0, color="#475569", lw=0.8)
    ax.set_xticks(x + 0.4 - w / 2)
    ax.set_xticklabels(tickers, rotation=30, ha="right")
    ax.set_ylabel("Sharpe ratio")
    ax.set_title("Sharpe ratio by ticker")
    ax.legend(fontsize=8)
    return _save(fig, "sharpe_by_ticker.png")


def chart_confusion(pq: Dict[str, Any], profile: str) -> Optional[Path]:
    cm = pq.get("confusion_matrix")
    if not cm:
        return None
    rows = ["BUY", "HOLD", "SELL"]
    cols = ["UP", "FLAT", "DOWN"]
    M = np.array([[cm[r][c] for c in cols] for r in rows], dtype=float)
    fig, ax = plt.subplots(figsize=(4.6, 4.2))
    im = ax.imshow(M, cmap="Greens")
    ax.set_xticks(range(3)); ax.set_xticklabels(cols)
    ax.set_yticks(range(3)); ax.set_yticklabels(rows)
    ax.set_xlabel("Realized move"); ax.set_ylabel("Agent decision")
    for i in range(3):
        for j in range(3):
            ax.text(j, i, int(M[i, j]), ha="center", va="center",
                    color="#064e3b" if M[i, j] > M.max() / 2 else "#334155", fontsize=12)
    ax.set_title(f"Confusion matrix — {profile}")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    return _save(fig, f"confusion_{profile}.png")


def chart_calibration(cal: List[Dict[str, Any]], profile: str) -> Optional[Path]:
    pts = [(c["mid"], c["hit_rate"], c["n"]) for c in cal if c.get("hit_rate") is not None and c["n"] > 0]
    if not pts:
        return None
    fig, ax = plt.subplots(figsize=(5.0, 4.2))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    ns = [p[2] for p in pts]
    ax.plot([0, 1], [0, 1], ls=":", color="#94a3b8", label="perfect calibration")
    ax.scatter(xs, ys, s=[30 + 18 * n for n in ns], color=C_STRAT, zorder=3)
    ax.plot(xs, ys, color=C_STRAT, lw=1.4)
    for x, y, n in pts:
        ax.annotate(f"n={n}", (x, y), textcoords="offset points", xytext=(6, 6), fontsize=8)
    ax.set_xlabel("Agent confidence"); ax.set_ylabel("Realized hit-rate")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.set_title(f"Calibration (actionable, @10d) — {profile}")
    ax.legend(fontsize=8)
    return _save(fig, f"calibration_{profile}.png")


def chart_hitrate_vs_random(pq: Dict[str, Any], runs: List[Run], profile: str) -> Optional[Path]:
    hit = pq.get("actionable_hit_rate")
    if hit is None:
        return None
    lo, hi = pq.get("ci_lo"), pq.get("ci_hi")
    # Average the per-run Monte-Carlo random baseline if present.
    rnd = [r.dq.get("baseline_random", {}).get("mean") for r in runs if r.profile == profile]
    rnd = [x for x in rnd if x is not None]
    rnd_mean = sum(rnd) / len(rnd) if rnd else 0.5
    fig, ax = plt.subplots(figsize=(4.4, 4.4))
    # Clamp to >= 0: the Wilson interval is shifted toward 0.5, so on small
    # samples lo/hi can land marginally on the "wrong" side of the point estimate.
    yerr = [[max(0.0, hit - (lo or hit))], [max(0.0, (hi or hit) - hit)]] if lo is not None else None
    ax.bar([0], [hit], 0.5, color=C_STRAT, yerr=yerr, capsize=6, label="Agent (95% CI)")
    ax.bar([1], [rnd_mean], 0.5, color=C_BENCH, label="Random (same mix)")
    ax.axhline(0.5, color=C_BAD, lw=1.0, ls="--", label="coin-flip 50%")
    ax.set_xticks([0, 1]); ax.set_xticklabels(["Agent", "Random"])
    ax.set_ylim(0, 1); ax.set_ylabel("Actionable hit-rate")
    p = pq.get("binomial_p_vs_50pct")
    sub = f"  (binomial p={'<0.001' if (p is not None and p<0.001) else f'{p:.3f}' if p is not None else 'n/a'})"
    ax.set_title(f"Skill vs chance — {profile}{sub}", fontsize=10)
    ax.legend(fontsize=8)
    return _save(fig, f"hitrate_vs_random_{profile}.png")


def chart_decision_distribution(runs: List[Run], profile: str) -> Optional[Path]:
    prs = [r for r in runs if r.profile == profile]
    if not prs:
        return None
    tickers, buy, hold, sell = [], [], [], []
    for r in sorted(prs, key=lambda x: x.ticker):
        dist = (r.report.get("decision_quality") or {}).get("action_distribution") or {}
        tickers.append(r.ticker)
        buy.append(dist.get("BUY", 0)); hold.append(dist.get("HOLD", 0)); sell.append(dist.get("SELL", 0))
    if not tickers:
        return None
    x = np.arange(len(tickers))
    fig, ax = plt.subplots(figsize=(max(7, 1.2 * len(tickers)), 4.0))
    ax.bar(x, buy, label="BUY", color=C_OK)
    ax.bar(x, hold, bottom=buy, label="HOLD", color="#cbd5e1")
    ax.bar(x, sell, bottom=[b + h for b, h in zip(buy, hold)], label="SELL", color=C_BAD)
    ax.set_xticks(x); ax.set_xticklabels(tickers, rotation=30, ha="right")
    ax.set_ylabel("# decisions"); ax.set_title(f"Decision distribution — {profile}")
    ax.legend(fontsize=8)
    return _save(fig, f"decision_distribution_{profile}.png")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(description="Build thesis tables + charts from backtest reports.")
    p.add_argument("--manifest", type=str, default=None,
                   help="Path to a run_thesis_backtests manifest JSON (default: latest in thesis_results/).")
    p.add_argument("--reports", type=str, nargs="*", default=None,
                   help="Explicit report glob(s) instead of a manifest.")
    args = p.parse_args()

    manifest_path: Optional[Path] = None
    if args.manifest:
        manifest_path = Path(args.manifest)
    elif not args.reports:
        cands = sorted(glob.glob(str(THESIS_RESULTS_DIR / "manifest_*.json")))
        cands = [c for c in cands if "DRYRUN" not in c]
        if cands:
            manifest_path = Path(cands[-1])

    runs = load_runs(manifest_path, args.reports or [])
    if not runs:
        print("No backtest reports found. Run scripts/run_thesis_backtests.py first.")
        return 1

    profiles = sorted({r.profile for r in runs})
    pooled = {prof: pool_decision_quality([r for r in runs if r.profile == prof]) for prof in profiles}

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path, md_path = write_tables(runs, pooled, ts)

    charts: List[Path] = []
    charts += [c for c in [chart_returns_by_ticker(runs), chart_sharpe_by_ticker(runs)] if c]
    for prof in profiles:
        pruns = [r for r in runs if r.profile == prof]
        pq = pooled[prof]
        cal = pool_calibration(pruns)
        for c in [
            chart_equity_curves(runs, prof),
            chart_confusion(pq, prof),
            chart_calibration(cal, prof),
            chart_hitrate_vs_random(pq, pruns, prof),
            chart_decision_distribution(runs, prof),
        ]:
            if c:
                charts.append(c)

    print("=" * 70)
    print(f"Runs aggregated: {len(runs)} ({', '.join(profiles)})")
    print(f"CSV   → {csv_path}")
    print(f"MD    → {md_path}")
    print(f"Charts ({len(charts)}) → {CHARTS_DIR}")
    for c in charts:
        print(f"   - {c.name}")
    for prof in profiles:
        pq = pooled[prof]
        print(f"\n[{prof}] pooled actionable hit-rate @10d: "
              f"{(pq['actionable_hit_rate'] or 0)*100:.1f}% "
              f"(n={pq['actionable_n']}), IC={pq.get('information_coefficient')}, "
              f"binomial p={pq.get('binomial_p_vs_50pct')}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
