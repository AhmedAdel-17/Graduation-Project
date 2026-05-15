"""Walk-forward arm comparison for the offline RL meta-policy.

Stage D consumes the JSON reports already produced by ``scripts/backtester.py``
(one per arm: baseline LLM graph, baseline + RL meta-policy, classical Backtrader
benchmark) and computes the *pre-registered* comparison metrics from the plan:

- Sharpe ratio (with a configurable risk-free rate; defaults to the CBE policy
  rate in :func:`tradingagents.rl.walkforward.default_risk_free_rate`, closing
  MEMORY §C3 for the RL path even though the rest of the codebase still uses 0.05)
- Calmar ratio
- Max drawdown
- Closed-trade win rate with a Wilson 95% confidence interval (the legacy
  ``"Hit Rate (fwd)"`` metric is intentionally NOT consumed — it suffers from
  look-ahead per MEMORY §C1)
- Total return + alpha vs the same JSON's reported benchmark window

The module is intentionally *consumer-only*: it does not drive the LLM graph or
launch new backtests. To get usable numbers a user runs ``scripts/backtester.py``
three times (with the RL flag toggled and with the classical benchmark script
for the third arm) and feeds the resulting JSONs into ``scripts/run_rl_evaluation.py``.
This keeps Stage D testable on synthetic fixtures and avoids re-implementing the
backtest engine.

PASS / FAIL criteria from the plan §3.7:

- **PASS**: ``rl_meta`` arm Sharpe ≥ baseline Sharpe + 0.20 *and* the 95%
  bootstrap CIs on per-day returns do not overlap zero.
- **FAIL-SAFE**: any other outcome → flag stays default off. Decision is
  recorded honestly in the JSON output, mirroring the ``PROOF_OF_WORK.md``
  pattern.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger("tradingagents.rl.walkforward")

# Configurable risk-free rate. The codebase still uses 0.05 in scripts/backtester.py
# (MEMORY §C3 open); we apply the C3 correction here on the RL eval path so
# Sharpe numbers in the comparison report are at least correct for this evaluation.
DEFAULT_CBE_POLICY_RATE = 0.24   # ≈ CBE policy rate in 2024-2026; conservative
TRADING_DAYS_PER_YEAR = 252
WILSON_Z_95 = 1.959964            # two-sided 95% normal quantile


def default_risk_free_rate() -> float:
    """Return the risk-free rate the RL eval pipeline should use.

    Centralized so model-card text and unit tests can quote the same number.
    """
    return DEFAULT_CBE_POLICY_RATE


# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ArmMetrics:
    """Per-arm metrics computed from a single backtest JSON."""

    arm: str
    n_trading_days: int
    n_trades_total: int
    n_trades_closed: int
    total_return_pct: float
    annualized_return_pct: float
    sharpe_ratio: float
    calmar_ratio: float
    max_drawdown_pct: float
    win_rate: Optional[float]
    win_rate_ci_lo: Optional[float]
    win_rate_ci_hi: Optional[float]
    benchmark_return_pct: Optional[float]
    benchmark_alpha_pct: Optional[float]
    final_portfolio_egp: float
    risk_free_rate_used: float
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ComparisonResult:
    """Pre-registered head-to-head ``rl_meta`` vs ``baseline`` summary."""

    baseline: ArmMetrics
    rl_meta: ArmMetrics
    classical: Optional[ArmMetrics]
    sharpe_uplift: float
    sharpe_pass_threshold: float
    return_uplift_pct: float
    bootstrap_ci_95: Tuple[float, float]
    n_bootstrap: int
    pre_registered_pass: bool
    pre_registered_reason: str
    rng_seed: int

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["bootstrap_ci_95"] = list(self.bootstrap_ci_95)
        return d


# ─────────────────────────────────────────────────────────────────────────────
# JSON-report ingestion
# ─────────────────────────────────────────────────────────────────────────────


def _parse_egp(value: Any) -> float:
    """Parse '"1,017,197.11 EGP"' style strings into floats. Robust to None."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return 0.0
    # Strip currency / percent / commas
    for tok in ("EGP", "%", ","):
        s = s.replace(tok, "")
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _daily_returns(daily_portfolio: List[Dict[str, Any]]) -> np.ndarray:
    """Per-day fractional returns from the daily_portfolio array."""
    if not daily_portfolio:
        return np.array([])
    vals = [float(row.get("portfolio_value", 0.0)) for row in daily_portfolio]
    arr = np.asarray(vals, dtype=np.float64)
    arr = arr[arr > 0]
    if arr.shape[0] < 2:
        return np.array([])
    return arr[1:] / arr[:-1] - 1.0


def _max_drawdown(daily_portfolio: List[Dict[str, Any]]) -> float:
    """Peak-to-trough drawdown over the daily series. Returns a non-positive value."""
    vals = [float(row.get("portfolio_value", 0.0)) for row in daily_portfolio if row.get("portfolio_value")]
    if not vals:
        return 0.0
    arr = np.asarray(vals, dtype=np.float64)
    peak = np.maximum.accumulate(arr)
    dd = (arr - peak) / np.where(peak > 0, peak, 1.0)
    return float(dd.min())   # most negative


def _wilson_ci(k: int, n: int, z: float = WILSON_Z_95) -> Tuple[Optional[float], Optional[float]]:
    """Two-sided Wilson confidence interval on a proportion k/n."""
    if n <= 0:
        return None, None
    p_hat = k / n
    denom = 1.0 + (z * z) / n
    centre = (p_hat + (z * z) / (2.0 * n)) / denom
    spread = z * math.sqrt((p_hat * (1.0 - p_hat) + (z * z) / (4.0 * n)) / n) / denom
    lo = max(0.0, centre - spread)
    hi = min(1.0, centre + spread)
    return float(lo), float(hi)


def _closed_trade_winrate(trades: List[Dict[str, Any]]) -> Tuple[Optional[float], int, int]:
    """Win rate on CLOSED (SELL) trades using realized PnL — no look-ahead."""
    closed = [t for t in trades if str(t.get("action", "")).upper() == "SELL"]
    if not closed:
        return None, 0, 0
    wins = sum(1 for t in closed if float(t.get("realized_pnl", 0.0)) > 0.0)
    return wins / len(closed), wins, len(closed)


def _benchmark_return_pct(benchmark_history: List[Dict[str, Any]]) -> Optional[float]:
    if not benchmark_history:
        return None
    vals = [r.get("price") or r.get("value") for r in benchmark_history if (r.get("price") or r.get("value"))]
    vals = [float(v) for v in vals if v is not None]
    if len(vals) < 2 or vals[0] <= 0:
        return None
    return float((vals[-1] / vals[0] - 1.0) * 100.0)


def compute_arm_metrics(
    report_path: str | Path,
    *,
    arm: str,
    risk_free_rate: Optional[float] = None,
) -> ArmMetrics:
    """Load a backtester JSON and compute the canonical arm metrics.

    ``risk_free_rate`` defaults to :func:`default_risk_free_rate` so the
    comparison uses a defensible EGP rate instead of the legacy 0.05 hard-code.
    """
    rf = float(risk_free_rate) if risk_free_rate is not None else default_risk_free_rate()
    path = Path(report_path)
    if not path.exists():
        raise FileNotFoundError(f"backtest report not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    daily_portfolio = data.get("daily_portfolio") or []
    trades = data.get("trades") or data.get("trade_history") or []
    benchmark_history = data.get("benchmark_history") or []

    rets = _daily_returns(daily_portfolio)
    n_days = int(rets.shape[0])
    notes: List[str] = []
    if n_days < 30:
        notes.append(
            f"only {n_days} daily-return samples; Sharpe / Calmar are illustrative, "
            f"not statistical evidence"
        )

    if n_days > 1 and rets.std(ddof=1) > 0:
        ann_mean = float(rets.mean()) * TRADING_DAYS_PER_YEAR
        ann_std = float(rets.std(ddof=1)) * math.sqrt(TRADING_DAYS_PER_YEAR)
        sharpe = (ann_mean - rf) / ann_std
        annualized_return_pct = ann_mean * 100.0
    elif n_days > 0:
        ann_mean = float(rets.mean()) * TRADING_DAYS_PER_YEAR
        sharpe = 0.0
        annualized_return_pct = ann_mean * 100.0
    else:
        sharpe = 0.0
        annualized_return_pct = 0.0

    max_dd = _max_drawdown(daily_portfolio)
    max_dd_pct = max_dd * 100.0

    # total_return = (last / first) - 1
    pv = [float(row.get("portfolio_value", 0.0)) for row in daily_portfolio if row.get("portfolio_value")]
    if len(pv) >= 2 and pv[0] > 0:
        total_return_pct = (pv[-1] / pv[0] - 1.0) * 100.0
    else:
        total_return_pct = _parse_egp((data.get("metrics") or {}).get("Total Return")) or 0.0

    calmar = (annualized_return_pct / 100.0) / abs(max_dd) if max_dd < 0 else 0.0

    win_rate, wins, n_closed = _closed_trade_winrate(trades)
    if n_closed > 0:
        ci_lo, ci_hi = _wilson_ci(wins, n_closed)
    else:
        ci_lo = ci_hi = None
        notes.append("zero closed (SELL) trades; win-rate is undefined")

    benchmark_return_pct = _benchmark_return_pct(benchmark_history)
    benchmark_alpha_pct: Optional[float] = None
    if benchmark_return_pct is not None:
        benchmark_alpha_pct = float(total_return_pct - benchmark_return_pct)

    final_portfolio_egp = (
        float(pv[-1]) if pv
        else _parse_egp((data.get("metrics") or {}).get("Final Portfolio"))
    )

    return ArmMetrics(
        arm=arm,
        n_trading_days=n_days,
        n_trades_total=len(trades),
        n_trades_closed=n_closed,
        total_return_pct=float(total_return_pct),
        annualized_return_pct=float(annualized_return_pct),
        sharpe_ratio=float(sharpe),
        calmar_ratio=float(calmar),
        max_drawdown_pct=float(max_dd_pct),
        win_rate=float(win_rate) if win_rate is not None else None,
        win_rate_ci_lo=ci_lo,
        win_rate_ci_hi=ci_hi,
        benchmark_return_pct=benchmark_return_pct,
        benchmark_alpha_pct=benchmark_alpha_pct,
        final_portfolio_egp=float(final_portfolio_egp),
        risk_free_rate_used=float(rf),
        notes=notes,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap CI on Sharpe / mean-return uplift
# ─────────────────────────────────────────────────────────────────────────────


def _bootstrap_mean_return_ci(
    baseline_path: str | Path,
    rl_path: str | Path,
    *,
    n_bootstrap: int = 5000,
    rng_seed: int = 42,
) -> Tuple[Tuple[float, float], int]:
    """Bootstrap 95% CI on (mean(rl_returns) − mean(baseline_returns)).

    Daily returns come from the same date span (intersection); when the two
    series have different lengths we resample the intersection length to keep
    samples paired. Returns ``((lo, hi), n_paired_days)``.
    """
    with open(baseline_path, "r", encoding="utf-8") as f:
        baseline_data = json.load(f)
    with open(rl_path, "r", encoding="utf-8") as f:
        rl_data = json.load(f)

    base_by_date = {
        row["date"]: float(row.get("portfolio_value", 0.0))
        for row in baseline_data.get("daily_portfolio", [])
        if row.get("date") and row.get("portfolio_value")
    }
    rl_by_date = {
        row["date"]: float(row.get("portfolio_value", 0.0))
        for row in rl_data.get("daily_portfolio", [])
        if row.get("date") and row.get("portfolio_value")
    }
    common_dates = sorted(set(base_by_date) & set(rl_by_date))
    if len(common_dates) < 3:
        return (0.0, 0.0), 0

    base_pv = np.array([base_by_date[d] for d in common_dates], dtype=np.float64)
    rl_pv = np.array([rl_by_date[d] for d in common_dates], dtype=np.float64)
    base_rets = base_pv[1:] / base_pv[:-1] - 1.0
    rl_rets = rl_pv[1:] / rl_pv[:-1] - 1.0
    diff = rl_rets - base_rets
    n = diff.shape[0]
    if n < 2:
        return (0.0, 0.0), int(n)

    rng = np.random.default_rng(rng_seed)
    samples = rng.choice(diff, size=(n_bootstrap, n), replace=True)
    means = samples.mean(axis=1)
    lo, hi = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    return (lo, hi), int(n)


# ─────────────────────────────────────────────────────────────────────────────
# Top-level comparison
# ─────────────────────────────────────────────────────────────────────────────


def compare_arms(
    *,
    baseline_path: str | Path,
    rl_meta_path: str | Path,
    classical_path: Optional[str | Path] = None,
    risk_free_rate: Optional[float] = None,
    n_bootstrap: int = 5000,
    rng_seed: int = 42,
    sharpe_pass_threshold: float = 0.20,
) -> ComparisonResult:
    """Compute the pre-registered head-to-head comparison.

    Returns a :class:`ComparisonResult` with the pre-registered PASS / FAIL
    flag and the bootstrap CI on the mean per-day return difference.
    """
    baseline = compute_arm_metrics(baseline_path, arm="baseline", risk_free_rate=risk_free_rate)
    rl_meta = compute_arm_metrics(rl_meta_path, arm="rl_meta", risk_free_rate=risk_free_rate)
    classical = (
        compute_arm_metrics(classical_path, arm="classical", risk_free_rate=risk_free_rate)
        if classical_path else None
    )

    ci, n_paired = _bootstrap_mean_return_ci(
        baseline_path, rl_meta_path,
        n_bootstrap=n_bootstrap, rng_seed=rng_seed,
    )

    sharpe_uplift = rl_meta.sharpe_ratio - baseline.sharpe_ratio
    return_uplift_pct = rl_meta.total_return_pct - baseline.total_return_pct

    # Pre-registered PASS criterion (plan §3.7): Sharpe uplift > +0.20 AND
    # the bootstrap CI on per-day return difference does not include zero.
    sharpe_ok = sharpe_uplift >= sharpe_pass_threshold
    ci_lo, ci_hi = ci
    ci_excludes_zero = (ci_lo > 0.0) or (ci_hi < 0.0)
    pass_overall = sharpe_ok and ci_excludes_zero

    if pass_overall:
        reason = (
            f"PASS: Sharpe uplift {sharpe_uplift:+.3f} ≥ {sharpe_pass_threshold:+.2f} "
            f"and 95% bootstrap CI [{ci_lo:.5f}, {ci_hi:.5f}] excludes zero"
        )
    elif not sharpe_ok and not ci_excludes_zero:
        reason = (
            f"FAIL: Sharpe uplift {sharpe_uplift:+.3f} < {sharpe_pass_threshold:+.2f} "
            f"AND 95% bootstrap CI [{ci_lo:.5f}, {ci_hi:.5f}] crosses zero "
            f"(n_paired_days={n_paired})"
        )
    elif not sharpe_ok:
        reason = (
            f"FAIL: Sharpe uplift {sharpe_uplift:+.3f} < threshold {sharpe_pass_threshold:+.2f}"
        )
    else:
        reason = (
            f"FAIL: 95% bootstrap CI [{ci_lo:.5f}, {ci_hi:.5f}] crosses zero "
            f"(n_paired_days={n_paired})"
        )

    return ComparisonResult(
        baseline=baseline,
        rl_meta=rl_meta,
        classical=classical,
        sharpe_uplift=float(sharpe_uplift),
        sharpe_pass_threshold=float(sharpe_pass_threshold),
        return_uplift_pct=float(return_uplift_pct),
        bootstrap_ci_95=ci,
        n_bootstrap=int(n_bootstrap),
        pre_registered_pass=bool(pass_overall),
        pre_registered_reason=reason,
        rng_seed=int(rng_seed),
    )


def write_comparison_report(result: ComparisonResult, output_path: str | Path) -> Path:
    """Dump the comparison result as JSON."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2, default=str)
    return output_path
