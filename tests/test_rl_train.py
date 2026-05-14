"""Tests for tradingagents.rl.train.

Coverage:
- seed_everything makes the loop deterministic
- materialize_dataset enforces schema (missing columns -> ValueError)
- materialize_dataset drops pending + hold rows by default
- time_aware_split is chronological per ticker (no future leakage)
- random split is reproducible for a fixed seed
- cql_loss: pure regression when alpha=0; conservative penalty pulls Q down when alpha>0
- train() runs end-to-end on a synthetic parquet and converges below initial loss
- train() honors early stopping (n_epochs reachable but not exceeded)
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd
import pytest
import torch

from tradingagents.rl.config import N_ACTIONS, SIZE_TIERS, TrainingConfig
from tradingagents.rl.feature_extractor import FEATURE_NAMES, feature_vector_size
from tradingagents.rl.train import (
    TrainingTensors,
    cql_loss,
    materialize_dataset,
    seed_everything,
    time_aware_split,
    train,
)


# ──────────────────────────────────────────────────────────────────────────────
# Determinism
# ──────────────────────────────────────────────────────────────────────────────


def test_seed_everything_pins_numpy_and_torch():
    seed_everything(42)
    a = np.random.rand(5)
    b = torch.randn(5).numpy()
    seed_everything(42)
    a2 = np.random.rand(5)
    b2 = torch.randn(5).numpy()
    np.testing.assert_array_equal(a, a2)
    np.testing.assert_array_equal(b, b2)


# ──────────────────────────────────────────────────────────────────────────────
# Synthetic dataset helpers
# ──────────────────────────────────────────────────────────────────────────────


def _synthetic_dataframe(
    *,
    n_per_ticker: int = 40,
    tickers: Tuple[str, ...] = ("COMI.CA", "EAST.CA"),
    seed: int = 7,
) -> pd.DataFrame:
    """Build a parquet-shaped dataset with a learnable signal.

    The signal: when the synthetic ``conf_overall`` feature is high we
    reward larger size tiers; when it is low we reward smaller tiers.
    A well-trained Q-network should learn this.
    """
    rng = np.random.RandomState(seed)
    rows = []
    base_date = pd.Timestamp("2023-01-01")
    for ticker in tickers:
        for i in range(n_per_ticker):
            conf = float(rng.rand())
            tier = int(rng.randint(0, N_ACTIONS))
            size_pct = SIZE_TIERS[tier]
            # Reward ≈ size × (conf - 0.5), so high-confidence + high-size pays off.
            reward = size_pct * (conf - 0.5) + rng.normal(scale=0.02)
            row = {f"feat__{name}": 0.0 for name in FEATURE_NAMES}
            row["feat__conf_overall"] = conf
            row["session_id"] = f"{ticker}-{i}"
            row["ticker"] = ticker
            row["trade_date"] = (base_date + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
            row["feature_version"] = "rl_state_v1"
            row["dataset_schema_version"] = "rl_dataset_v1"
            row["llm_action"] = "BUY"
            row["behavior_size_pct"] = float(size_pct)
            row["behavior_size_tier"] = int(tier)
            row["reward_horizon_days"] = 20
            row["forward_return_5d"] = None
            row["forward_return_20d"] = conf - 0.5
            row["drawdown_during_holding"] = None
            row["transaction_cost_pct"] = 0.00378
            row["reward"] = float(reward)
            row["trade_result"] = "WIN" if reward > 0 else "LOSS"
            row["source"] = "synthetic"
            row["notes"] = None
            rows.append(row)
    return pd.DataFrame(rows)


def _write_synthetic(tmp_path: Path, **kwargs) -> Path:
    df = _synthetic_dataframe(**kwargs)
    out = tmp_path / "synthetic.parquet"
    try:
        df.to_parquet(out, index=False)
    except Exception:
        out = tmp_path / "synthetic.csv"
        df.to_csv(out, index=False)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# materialize_dataset
# ──────────────────────────────────────────────────────────────────────────────


def test_materialize_dataset_round_trip(tmp_path: Path):
    path = _write_synthetic(tmp_path, n_per_ticker=10)
    tensors = materialize_dataset(path)
    assert tensors.states.shape == (20, feature_vector_size())
    assert tensors.actions.shape == (20,)
    assert tensors.rewards.shape == (20,)
    assert tensors.actions.dtype == torch.int64
    assert tensors.states.dtype == torch.float32


def test_materialize_dataset_drops_pending(tmp_path: Path):
    df = _synthetic_dataframe(n_per_ticker=5)
    df.loc[0, "reward"] = float("nan")
    out = tmp_path / "with_pending.parquet"
    try:
        df.to_parquet(out, index=False)
    except Exception:
        out = tmp_path / "with_pending.csv"
        df.to_csv(out, index=False)
    tensors = materialize_dataset(out, drop_pending=True)
    # 2 tickers × 5 - 1 dropped = 9
    assert tensors.states.shape[0] == 9


def test_materialize_dataset_drops_hold(tmp_path: Path):
    df = _synthetic_dataframe(n_per_ticker=5)
    df.loc[df.index[:3], "llm_action"] = "HOLD"
    out = tmp_path / "with_hold.parquet"
    try:
        df.to_parquet(out, index=False)
    except Exception:
        out = tmp_path / "with_hold.csv"
        df.to_csv(out, index=False)
    tensors = materialize_dataset(out, drop_hold=True)
    # 2 tickers × 5 - 3 HOLDs = 7
    assert tensors.states.shape[0] == 7


def test_materialize_dataset_missing_columns_raises(tmp_path: Path):
    df = _synthetic_dataframe(n_per_ticker=3)
    df = df.drop(columns=["feat__conf_overall"])  # break the schema
    out = tmp_path / "bad.csv"
    df.to_csv(out, index=False)
    with pytest.raises(ValueError, match="missing"):
        materialize_dataset(out)


def test_materialize_empty_after_filtering_raises(tmp_path: Path):
    df = _synthetic_dataframe(n_per_ticker=3)
    df["reward"] = float("nan")
    out = tmp_path / "all_pending.csv"
    df.to_csv(out, index=False)
    with pytest.raises(ValueError, match="zero usable"):
        materialize_dataset(out)


# ──────────────────────────────────────────────────────────────────────────────
# Splits
# ──────────────────────────────────────────────────────────────────────────────


def test_time_aware_split_is_chronological_per_ticker(tmp_path: Path):
    path = _write_synthetic(tmp_path, n_per_ticker=20, tickers=("COMI.CA",))
    full = materialize_dataset(path)
    train_t, val_t = time_aware_split(full, val_fraction=0.25, mode="time")
    # All train dates must precede all val dates for this single-ticker case
    assert max(train_t.dates) < min(val_t.dates)


def test_time_aware_split_keeps_per_ticker_chronology(tmp_path: Path):
    path = _write_synthetic(tmp_path, n_per_ticker=20)
    full = materialize_dataset(path)
    train_t, val_t = time_aware_split(full, val_fraction=0.25, mode="time")
    for ticker in set(full.tickers):
        train_dates = [d for d, t in zip(train_t.dates, train_t.tickers) if t == ticker]
        val_dates = [d for d, t in zip(val_t.dates, val_t.tickers) if t == ticker]
        if train_dates and val_dates:
            assert max(train_dates) < min(val_dates), f"leakage for {ticker}"


def test_random_split_reproducible(tmp_path: Path):
    path = _write_synthetic(tmp_path, n_per_ticker=15)
    full = materialize_dataset(path)
    a_train, a_val = time_aware_split(full, val_fraction=0.2, mode="random", seed=99)
    b_train, b_val = time_aware_split(full, val_fraction=0.2, mode="random", seed=99)
    assert a_train.dates == b_train.dates
    assert a_val.dates == b_val.dates


# ──────────────────────────────────────────────────────────────────────────────
# cql_loss
# ──────────────────────────────────────────────────────────────────────────────


def test_cql_loss_reduces_to_td_when_alpha_zero():
    q = torch.tensor([[0.1, 0.2, 0.3, 0.4, 0.5]], requires_grad=True)
    actions = torch.tensor([2])
    rewards = torch.tensor([0.6])
    loss, tele = cql_loss(q, actions, rewards, alpha=0.0, temperature=1.0)
    expected_td = (0.3 - 0.6) ** 2
    assert pytest.approx(tele["td_loss"], abs=1e-5) == expected_td
    assert pytest.approx(tele["total_loss"], abs=1e-5) == expected_td


def test_cql_loss_penalty_positive_when_alpha_positive():
    q = torch.tensor([[0.1, 0.2, 0.3, 0.4, 0.5]])
    actions = torch.tensor([0])  # behavior chose the WORST tier; lsm - q_taken is large
    rewards = torch.tensor([0.0])
    loss, tele = cql_loss(q, actions, rewards, alpha=1.0, temperature=1.0)
    assert tele["cql_penalty"] > 0
    assert tele["total_loss"] > tele["td_loss"]


# ──────────────────────────────────────────────────────────────────────────────
# End-to-end train()
# ──────────────────────────────────────────────────────────────────────────────


def test_train_runs_and_converges(tmp_path: Path):
    path = _write_synthetic(tmp_path, n_per_ticker=80)
    cfg = TrainingConfig(
        n_epochs=40,
        batch_size=32,
        early_stopping_patience=100,  # disable for this assertion
        cql_alpha=0.5,
        learning_rate=3e-3,
        dropout_p=0.0,                # less noise, easier convergence on a tiny test
        seed=42,
    )
    result = train(path, config=cfg)
    assert result.n_train > 0 and result.n_val > 0
    assert len(result.train_history) >= 1
    first = result.train_history[0]["td_loss"]
    last = result.train_history[-1]["td_loss"]
    # Convergence sanity: TD loss should drop appreciably on this synthetic
    assert last < first, f"td_loss did not decrease ({first:.4f} -> {last:.4f})"


def test_train_is_deterministic_for_same_seed(tmp_path: Path):
    path = _write_synthetic(tmp_path, n_per_ticker=30)
    cfg = TrainingConfig(
        n_epochs=10, batch_size=16, dropout_p=0.0, seed=123,
        early_stopping_patience=100,
    )
    r1 = train(path, config=cfg)
    r2 = train(path, config=cfg)
    # Compare the final greedy size on a fixed feature vector
    state = {}
    p1 = r1.policy.predict(state, ticker="COMI.CA", trade_date="2024-06-01")
    p2 = r2.policy.predict(state, ticker="COMI.CA", trade_date="2024-06-01")
    assert p1.action_index == p2.action_index
    assert p1.size_multiplier == p2.size_multiplier


def test_train_respects_early_stopping(tmp_path: Path):
    path = _write_synthetic(tmp_path, n_per_ticker=50)
    cfg = TrainingConfig(
        n_epochs=500,
        early_stopping_patience=3,
        dropout_p=0.0,
        seed=0,
    )
    result = train(path, config=cfg)
    # If early stopping fires, history is shorter than n_epochs
    assert len(result.train_history) < cfg.n_epochs
