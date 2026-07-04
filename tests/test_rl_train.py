"""Tests for tradingagents.rl.train (decision policy, counterfactual reward).

Coverage:
- seed_everything makes the loop deterministic
- materialize_dataset enforces schema (missing columns -> ValueError)
- materialize_dataset drops pending rows by default; keeps HOLD rows
- time_aware_split is chronological per ticker (no future leakage)
- random split is reproducible for a fixed seed
- cql_loss: full-feedback regression when alpha=0; conservative penalty pulls Q
  toward the committee action when alpha>0
- train() runs end-to-end on a synthetic parquet and converges below initial loss
- train() honors early stopping and is deterministic for a fixed seed
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd
import pytest
import torch

from tradingagents.rl.config import DECISION_ACTIONS, N_ACTIONS, TrainingConfig
from tradingagents.rl.feature_extractor import FEATURE_NAMES, feature_vector_size
from tradingagents.rl.train import (
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
    """Build a parquet-shaped v2 dataset with a learnable signal.

    The signal: a high ``conf_overall`` feature means the forward return was
    positive, so BUY is rewarded and SELL is penalised. A well-trained
    Q-network should learn to BUY when conf is high and SELL when it is low.
    """
    rng = np.random.RandomState(seed)
    rows = []
    base_date = pd.Timestamp("2023-01-01")
    for ticker in tickers:
        for i in range(n_per_ticker):
            conf = float(rng.rand())
            fwd = (conf - 0.5) * 0.1 + rng.normal(scale=0.01)
            r_buy = fwd - 0.00378
            r_sell = -fwd - 0.00378
            row = {f"feat__{name}": 0.0 for name in FEATURE_NAMES}
            row["feat__conf_overall"] = conf
            row["session_id"] = f"{ticker}-{i}"
            row["ticker"] = ticker
            row["trade_date"] = (base_date + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
            row["feature_version"] = "rl_state_v1"
            row["dataset_schema_version"] = "rl_dataset_v2"
            row["llm_action"] = "HOLD"
            row["reward"] = 0.0
            row["trade_result"] = "WIN" if fwd > 0 else "LOSS"
            row["reward_buy"] = float(r_buy)
            row["reward_hold"] = 0.0
            row["reward_sell"] = float(r_sell)
            row["committee_action_index"] = 1  # HOLD
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
    assert tensors.reward_matrix.shape == (20, N_ACTIONS)
    assert tensors.behavior_action.shape == (20,)
    assert tensors.behavior_action.dtype == torch.int64
    assert tensors.states.dtype == torch.float32


def test_materialize_dataset_drops_pending(tmp_path: Path):
    df = _synthetic_dataframe(n_per_ticker=5)
    df.loc[0, "reward_buy"] = float("nan")  # one pending row
    out = tmp_path / "with_pending.parquet"
    try:
        df.to_parquet(out, index=False)
    except Exception:
        out = tmp_path / "with_pending.csv"
        df.to_csv(out, index=False)
    tensors = materialize_dataset(out, drop_pending=True)
    assert tensors.states.shape[0] == 9   # 2×5 - 1 pending


def test_materialize_dataset_keeps_hold_rows(tmp_path: Path):
    """Under the decision policy, HOLD rows carry a full reward vector and stay."""
    df = _synthetic_dataframe(n_per_ticker=5)
    df.loc[df.index[:3], "llm_action"] = "HOLD"
    out = tmp_path / "with_hold.parquet"
    try:
        df.to_parquet(out, index=False)
    except Exception:
        out = tmp_path / "with_hold.csv"
        df.to_csv(out, index=False)
    tensors = materialize_dataset(out)
    assert tensors.states.shape[0] == 10  # nothing dropped


def test_materialize_dataset_missing_feature_columns_raises(tmp_path: Path):
    df = _synthetic_dataframe(n_per_ticker=3)
    df = df.drop(columns=["feat__conf_overall"])
    out = tmp_path / "bad.csv"
    df.to_csv(out, index=False)
    with pytest.raises(ValueError, match="missing"):
        materialize_dataset(out)


def test_materialize_dataset_missing_reward_columns_raises(tmp_path: Path):
    df = _synthetic_dataframe(n_per_ticker=3)
    df = df.drop(columns=["reward_buy", "reward_sell"])
    out = tmp_path / "bad2.csv"
    df.to_csv(out, index=False)
    with pytest.raises(ValueError, match="counterfactual reward"):
        materialize_dataset(out)


def test_materialize_empty_after_filtering_raises(tmp_path: Path):
    df = _synthetic_dataframe(n_per_ticker=3)
    df["reward_buy"] = float("nan")
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
    assert max(train_t.dates) < min(val_t.dates)


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


def test_cql_loss_reduces_to_regression_when_alpha_zero():
    q = torch.tensor([[0.1, 0.2, 0.3]], requires_grad=True)
    reward_matrix = torch.tensor([[0.4, 0.0, -0.2]])
    behavior = torch.tensor([1])
    loss, tele = cql_loss(q, reward_matrix, behavior, alpha=0.0, temperature=1.0)
    expected = float(((q.detach() - reward_matrix) ** 2).mean())
    assert pytest.approx(tele["reg_loss"], abs=1e-6) == expected
    assert pytest.approx(tele["total_loss"], abs=1e-6) == expected


def test_cql_penalty_positive_when_committee_not_argmax():
    # Committee chose action 0 but action 2 has the highest Q ⇒ penalty > 0.
    q = torch.tensor([[0.1, 0.2, 0.9]])
    reward_matrix = torch.zeros((1, N_ACTIONS))
    behavior = torch.tensor([0])
    loss, tele = cql_loss(q, reward_matrix, behavior, alpha=1.0, temperature=1.0)
    assert tele["cql_penalty"] > 0
    assert tele["total_loss"] > tele["reg_loss"]


# ──────────────────────────────────────────────────────────────────────────────
# End-to-end train()
# ──────────────────────────────────────────────────────────────────────────────


def test_train_runs_and_converges(tmp_path: Path):
    path = _write_synthetic(tmp_path, n_per_ticker=80)
    cfg = TrainingConfig(
        n_epochs=40, batch_size=32, early_stopping_patience=100,
        cql_alpha=0.0, learning_rate=3e-3, dropout_p=0.0, seed=42,
    )
    result = train(path, config=cfg)
    assert result.n_train > 0 and result.n_val > 0
    first = result.train_history[0]["td_loss"]
    last = result.train_history[-1]["td_loss"]
    assert last < first, f"reg loss did not decrease ({first:.4f} -> {last:.4f})"


def test_trained_policy_learns_to_act_on_signal(tmp_path: Path):
    """With alpha=0 the policy should BUY on a high-confidence state."""
    path = _write_synthetic(tmp_path, n_per_ticker=120)
    cfg = TrainingConfig(
        n_epochs=120, batch_size=32, early_stopping_patience=80,
        cql_alpha=0.0, learning_rate=3e-3, dropout_p=0.0, seed=1,
    )
    result = train(path, config=cfg)
    state_high = {"confidence_scores": {"overall": 0.95}}
    pred = result.policy.predict(state_high, ticker="COMI.CA", trade_date="2024-06-01")
    assert pred.action == "BUY"


def test_train_is_deterministic_for_same_seed(tmp_path: Path):
    path = _write_synthetic(tmp_path, n_per_ticker=30)
    cfg = TrainingConfig(
        n_epochs=10, batch_size=16, dropout_p=0.0, seed=123,
        early_stopping_patience=100,
    )
    r1 = train(path, config=cfg)
    r2 = train(path, config=cfg)
    state = {"confidence_scores": {"overall": 0.8}}
    p1 = r1.policy.predict(state, ticker="COMI.CA", trade_date="2024-06-01")
    p2 = r2.policy.predict(state, ticker="COMI.CA", trade_date="2024-06-01")
    assert p1.action_index == p2.action_index
    assert p1.action == p2.action


def test_train_respects_early_stopping(tmp_path: Path):
    path = _write_synthetic(tmp_path, n_per_ticker=50)
    cfg = TrainingConfig(
        n_epochs=500, early_stopping_patience=3, dropout_p=0.0, seed=0,
    )
    result = train(path, config=cfg)
    assert len(result.train_history) < cfg.n_epochs
