"""Tests for tradingagents.rl.walkforward — Stage D arm comparison.

Covers:
- _parse_egp robustness on "1,017.11 EGP" / "%" / None / numeric inputs
- _daily_returns + _max_drawdown on hand-built daily_portfolio fixtures
- Wilson 95% CI math against a known reference
- _closed_trade_winrate counts SELLs with positive realized_pnl only
- compute_arm_metrics happy-path on a synthetic report
- compute_arm_metrics labels insufficient-data with notes
- compare_arms PASS when rl beats baseline by ≥ threshold AND CI excludes zero
- compare_arms FAIL when uplift below threshold
- compare_arms FAIL when bootstrap CI crosses zero
- write_comparison_report round-trips through JSON
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from tradingagents.rl.walkforward import (
    DEFAULT_CBE_POLICY_RATE,
    ArmMetrics,
    _closed_trade_winrate,
    _daily_returns,
    _max_drawdown,
    _parse_egp,
    _wilson_ci,
    compare_arms,
    compute_arm_metrics,
    default_risk_free_rate,
    write_comparison_report,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _report(
    *,
    daily_values: List[float],
    trades: List[Dict[str, Any]],
    benchmark_values: List[float] | None = None,
    metrics: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a minimally valid backtester JSON report dict."""
    return {
        "session": {"ticker": "COMI.CA", "start_date": "2024-01-01", "end_date": "2024-06-30"},
        "metrics": metrics or {},
        "trades": trades,
        "daily_portfolio": [
            {"date": f"2024-01-{i+1:02d}", "portfolio_value": v, "split": "full"}
            for i, v in enumerate(daily_values)
        ],
        "benchmark_history": [
            {"date": f"2024-01-{i+1:02d}", "price": v}
            for i, v in enumerate(benchmark_values or [])
        ],
        "audit_log": [],
    }


def _write_report(tmp_path: Path, name: str, **kwargs) -> Path:
    out = tmp_path / name
    out.write_text(json.dumps(_report(**kwargs)), encoding="utf-8")
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ──────────────────────────────────────────────────────────────────────────────


def test_parse_egp_handles_currency_strings():
    assert _parse_egp("1,017,197.11 EGP") == pytest.approx(1_017_197.11)
    assert _parse_egp("1.72%") == pytest.approx(1.72)
    assert _parse_egp(42.5) == 42.5
    assert _parse_egp(None) == 0.0
    assert _parse_egp("") == 0.0
    assert _parse_egp("garbage") == 0.0


def test_daily_returns_from_portfolio_values():
    rets = _daily_returns([
        {"portfolio_value": 100.0},
        {"portfolio_value": 110.0},
        {"portfolio_value": 121.0},
    ])
    assert rets.shape == (2,)
    assert rets[0] == pytest.approx(0.10, abs=1e-9)
    assert rets[1] == pytest.approx(0.10, abs=1e-9)


def test_daily_returns_filters_non_positive():
    rets = _daily_returns([
        {"portfolio_value": 100.0},
        {"portfolio_value": 0.0},      # dropped
        {"portfolio_value": -10.0},    # dropped
        {"portfolio_value": 110.0},
    ])
    assert rets.shape == (1,)
    assert rets[0] == pytest.approx(0.10, abs=1e-9)


def test_max_drawdown_on_monotone_up():
    dd = _max_drawdown([
        {"portfolio_value": 100.0},
        {"portfolio_value": 110.0},
        {"portfolio_value": 120.0},
    ])
    assert dd == pytest.approx(0.0)


def test_max_drawdown_peak_to_trough():
    dd = _max_drawdown([
        {"portfolio_value": 100.0},
        {"portfolio_value": 120.0},   # peak
        {"portfolio_value": 90.0},    # trough
        {"portfolio_value": 105.0},
    ])
    assert dd == pytest.approx(-0.25, abs=1e-9)   # (90 - 120) / 120


def test_wilson_ci_known_case():
    """5 wins out of 10 — Wilson 95% CI is roughly [0.237, 0.763]."""
    lo, hi = _wilson_ci(5, 10)
    assert lo == pytest.approx(0.237, abs=1e-2)
    assert hi == pytest.approx(0.763, abs=1e-2)


def test_wilson_ci_undefined_for_zero_trials():
    lo, hi = _wilson_ci(0, 0)
    assert lo is None and hi is None


def test_closed_trade_winrate_only_counts_sells():
    trades = [
        {"action": "BUY",  "realized_pnl": 0.0},      # ignored
        {"action": "SELL", "realized_pnl": 250.0},    # win
        {"action": "SELL", "realized_pnl": -10.0},    # loss
        {"action": "SELL", "realized_pnl": 0.0},      # loss (zero is not a win)
    ]
    rate, wins, n = _closed_trade_winrate(trades)
    assert wins == 1 and n == 3
    assert rate == pytest.approx(1/3, abs=1e-9)


def test_closed_trade_winrate_empty():
    rate, wins, n = _closed_trade_winrate([])
    assert rate is None and wins == 0 and n == 0


def test_default_risk_free_rate_is_cbe_policy_rate():
    assert default_risk_free_rate() == pytest.approx(DEFAULT_CBE_POLICY_RATE)


# ──────────────────────────────────────────────────────────────────────────────
# compute_arm_metrics
# ──────────────────────────────────────────────────────────────────────────────


def test_compute_arm_metrics_happy_path(tmp_path: Path):
    # 5 days of monotonic upside; 1 closed winning SELL.
    path = _write_report(
        tmp_path, "rl_report.json",
        daily_values=[1_000_000, 1_010_000, 1_020_000, 1_030_000, 1_050_000],
        trades=[
            {"date": "2024-01-02", "action": "BUY",  "realized_pnl": 0.0},
            {"date": "2024-01-05", "action": "SELL", "realized_pnl": 5_000.0},
        ],
        benchmark_values=[1_000.0, 1_005.0, 1_010.0, 1_015.0, 1_025.0],
    )
    m = compute_arm_metrics(path, arm="rl_meta")
    assert m.arm == "rl_meta"
    assert m.n_trading_days == 4
    assert m.n_trades_total == 2
    assert m.n_trades_closed == 1
    assert m.total_return_pct == pytest.approx(5.0, abs=1e-6)
    assert m.win_rate == 1.0
    assert m.win_rate_ci_lo is not None and m.win_rate_ci_hi is not None
    assert m.benchmark_return_pct == pytest.approx(2.5, abs=1e-6)
    assert m.benchmark_alpha_pct == pytest.approx(2.5, abs=1e-6)
    assert m.max_drawdown_pct == pytest.approx(0.0)
    assert m.risk_free_rate_used == DEFAULT_CBE_POLICY_RATE


def test_compute_arm_metrics_flags_small_sample(tmp_path: Path):
    path = _write_report(
        tmp_path, "tiny.json",
        daily_values=[1_000_000, 1_005_000, 1_010_000],
        trades=[],
    )
    m = compute_arm_metrics(path, arm="baseline")
    assert m.n_trading_days < 30
    assert any("illustrative" in n for n in m.notes)


def test_compute_arm_metrics_zero_closed_trades_marks_undefined(tmp_path: Path):
    path = _write_report(
        tmp_path, "open_only.json",
        daily_values=[1_000_000, 1_010_000, 1_020_000],
        trades=[{"date": "2024-01-02", "action": "BUY", "realized_pnl": 0.0}],
    )
    m = compute_arm_metrics(path, arm="baseline")
    assert m.win_rate is None
    assert m.win_rate_ci_lo is None and m.win_rate_ci_hi is None
    assert any("undefined" in n for n in m.notes)


def test_compute_arm_metrics_missing_report_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        compute_arm_metrics(tmp_path / "no_such.json", arm="baseline")


def test_compute_arm_metrics_custom_risk_free_rate(tmp_path: Path):
    path = _write_report(
        tmp_path, "r.json",
        daily_values=[1_000_000, 1_010_000, 1_020_000, 1_010_000, 1_030_000],
        trades=[],
    )
    m_default = compute_arm_metrics(path, arm="baseline")
    m_custom = compute_arm_metrics(path, arm="baseline", risk_free_rate=0.05)
    assert m_default.risk_free_rate_used == DEFAULT_CBE_POLICY_RATE
    assert m_custom.risk_free_rate_used == 0.05
    # Different risk-free rates ⇒ different Sharpes
    assert m_default.sharpe_ratio != m_custom.sharpe_ratio


# ──────────────────────────────────────────────────────────────────────────────
# compare_arms
# ──────────────────────────────────────────────────────────────────────────────


def _build_arm_pair(tmp_path: Path, *, rl_uplift_per_day: float, noise: float = 0.005, seed: int = 0) -> tuple[Path, Path]:
    """Build paired baseline / RL reports sharing dates so the bootstrap pairs cleanly.

    Adds Gaussian noise so daily-return std > 0 and Sharpe is well-defined.
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    n = 60
    base_pv = 1_000_000.0
    noise_draws = rng.normal(0.0, noise, size=n)
    base_returns = [0.001 + float(eps) for eps in noise_draws]
    rl_returns = [r + rl_uplift_per_day for r in base_returns]
    base_vals = [base_pv]
    rl_vals = [base_pv]
    for r in base_returns:
        base_vals.append(base_vals[-1] * (1 + r))
    for r in rl_returns:
        rl_vals.append(rl_vals[-1] * (1 + r))
    base_path = _write_report(
        tmp_path, "baseline.json",
        daily_values=base_vals,
        trades=[{"date": "2024-01-05", "action": "SELL", "realized_pnl": 100.0}],
    )
    rl_path = _write_report(
        tmp_path, "rl_meta.json",
        daily_values=rl_vals,
        trades=[{"date": "2024-01-05", "action": "SELL", "realized_pnl": 200.0}],
    )
    return base_path, rl_path


def test_compare_arms_pass_when_uplift_large_and_ci_excludes_zero(tmp_path: Path):
    # +0.5% per day uplift over 60 days ⇒ Sharpe uplift well past +0.20 and
    # the CI on per-day differences will exclude zero comfortably.
    base, rl = _build_arm_pair(tmp_path, rl_uplift_per_day=0.005)
    result = compare_arms(
        baseline_path=base, rl_meta_path=rl,
        n_bootstrap=2000, rng_seed=42,
    )
    assert result.sharpe_uplift >= result.sharpe_pass_threshold
    ci_lo, ci_hi = result.bootstrap_ci_95
    assert ci_lo > 0
    assert result.pre_registered_pass is True
    assert "PASS" in result.pre_registered_reason


def test_compare_arms_fail_when_no_uplift(tmp_path: Path):
    # Zero uplift ⇒ Sharpe uplift = 0, CI centred on 0
    base, rl = _build_arm_pair(tmp_path, rl_uplift_per_day=0.0)
    result = compare_arms(
        baseline_path=base, rl_meta_path=rl,
        n_bootstrap=2000, rng_seed=42,
    )
    assert result.sharpe_uplift == pytest.approx(0.0, abs=1e-3)
    assert result.pre_registered_pass is False
    assert "FAIL" in result.pre_registered_reason


def test_compare_arms_fail_when_ci_crosses_zero(tmp_path: Path):
    # Tiny uplift ⇒ Sharpe might or might not clear; CI will likely include 0.
    base, rl = _build_arm_pair(tmp_path, rl_uplift_per_day=0.00001)
    result = compare_arms(
        baseline_path=base, rl_meta_path=rl,
        n_bootstrap=2000, rng_seed=42,
    )
    assert result.pre_registered_pass is False


def test_compare_arms_includes_classical_when_provided(tmp_path: Path):
    base, rl = _build_arm_pair(tmp_path, rl_uplift_per_day=0.002)
    classical = _write_report(
        tmp_path, "classical.json",
        daily_values=[1_000_000, 1_001_000, 1_002_000, 1_003_000, 1_005_000],
        trades=[{"date": "2024-01-02", "action": "SELL", "realized_pnl": 50.0}],
    )
    result = compare_arms(
        baseline_path=base, rl_meta_path=rl, classical_path=classical,
        n_bootstrap=1000, rng_seed=42,
    )
    assert result.classical is not None
    assert result.classical.arm == "classical"


def test_compare_arms_bootstrap_seed_reproducible(tmp_path: Path):
    base, rl = _build_arm_pair(tmp_path, rl_uplift_per_day=0.001)
    r1 = compare_arms(baseline_path=base, rl_meta_path=rl, n_bootstrap=1000, rng_seed=7)
    r2 = compare_arms(baseline_path=base, rl_meta_path=rl, n_bootstrap=1000, rng_seed=7)
    assert r1.bootstrap_ci_95 == r2.bootstrap_ci_95


def test_compare_arms_handles_no_overlapping_dates(tmp_path: Path):
    """Baseline 2024-01-*; RL 2025-01-* ⇒ no paired days ⇒ CI defaults to (0,0)."""
    base = _write_report(
        tmp_path, "b.json",
        daily_values=[1_000_000, 1_010_000, 1_020_000],
        trades=[],
    )
    # Hand-edit dates so the second report doesn't overlap
    rl_data = _report(
        daily_values=[1_000_000, 1_005_000, 1_010_000],
        trades=[],
    )
    for i, row in enumerate(rl_data["daily_portfolio"]):
        row["date"] = f"2025-06-{i+1:02d}"
    rl_path = tmp_path / "rl.json"
    rl_path.write_text(json.dumps(rl_data), encoding="utf-8")
    result = compare_arms(
        baseline_path=base, rl_meta_path=rl_path,
        n_bootstrap=500, rng_seed=42,
    )
    # No paired data ⇒ CI is (0,0), excludes zero only trivially, so FAIL
    assert result.bootstrap_ci_95 == (0.0, 0.0)
    assert result.pre_registered_pass is False


def test_write_comparison_report_round_trip(tmp_path: Path):
    base, rl = _build_arm_pair(tmp_path, rl_uplift_per_day=0.001)
    result = compare_arms(baseline_path=base, rl_meta_path=rl, n_bootstrap=500, rng_seed=42)
    out = tmp_path / "compare.json"
    written = write_comparison_report(result, out)
    assert written.exists()
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert "baseline" in payload and "rl_meta" in payload
    assert payload["baseline"]["arm"] == "baseline"
    assert payload["rl_meta"]["arm"] == "rl_meta"
    assert "bootstrap_ci_95" in payload
    assert "pre_registered_pass" in payload
