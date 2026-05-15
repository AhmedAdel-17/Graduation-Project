"""Hand-rolled single-step CQL training loop.

Why hand-rolled? The plan (§3.5) allowed a ``d3rlpy``-backed implementation
with this loop as a fallback when d3rlpy doesn't install cleanly. On the
target Windows + Python 3.13 + uv environment d3rlpy is unavailable, so we
take the documented fallback. The benefit is also defensibility: every
line of the loss formulation is readable and reviewable here, which is
useful for a graduation defense.

Single-step framing recap (plan §3.4): each (ticker, trade_date) is a
one-step bandit. ``gamma = 0``, so the Bellman target collapses to the
shaped reward ``r``. The training objective becomes::

    L_td   = MSE( Q(s, a_behavior), r )
    L_cql  = α · ( log Σ_a exp( Q(s, a) / τ ) - Q(s, a_behavior) )
    L      = L_td + L_cql

The conservative term L_cql pulls Q-values down on actions that were never
observed in the dataset, mitigating offline-RL extrapolation error on a
small EGX sample. With α = 0 the loss reduces to plain regression to
rewards — useful baseline if you ever want to sanity-check.
"""

from __future__ import annotations

import json
import logging
import math
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

from tradingagents.rl.config import (
    DEFAULT_TRAINING_CONFIG,
    N_ACTIONS,
    SIZE_TIERS,
    TrainingConfig,
)
from tradingagents.rl.feature_extractor import FEATURE_NAMES, FEATURE_VERSION, feature_vector_size
from tradingagents.rl.policy import QNetwork, RLSizingPolicy

logger = logging.getLogger("tradingagents.rl.train")


# ─────────────────────────────────────────────────────────────────────────────
# Determinism helpers
# ─────────────────────────────────────────────────────────────────────────────


def seed_everything(seed: int) -> None:
    """Pin every RNG we care about. Idempotent."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    # Disable cuDNN benchmarking so identical inputs produce identical kernels.
    try:
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
    except AttributeError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Dataset materialization
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class TrainingTensors:
    """The parquet/CSV converted to dense tensors ready for the loop."""

    states: torch.Tensor          # (N, state_dim) float32
    actions: torch.Tensor         # (N,) int64        — discretized behavior tier
    rewards: torch.Tensor         # (N,) float32
    behavior_size_pct: torch.Tensor  # (N,) float32   — for off-policy eval
    tickers: List[str]
    dates: List[str]


def _load_parquet_or_csv(path: str | Path):
    import pandas as pd
    path = Path(path)
    if not path.exists():
        # Try the CSV fallback that ``dataset.write_parquet`` may emit.
        csv = path.with_suffix(".csv")
        if csv.exists():
            path = csv
        else:
            raise FileNotFoundError(f"No dataset file at {path} or {csv}")
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def materialize_dataset(
    dataset_path: str | Path,
    *,
    drop_pending: bool = True,
    drop_hold: bool = True,
) -> TrainingTensors:
    """Load the parquet/CSV and convert to training tensors.

    ``drop_pending=True`` removes rows whose 20-day horizon hasn't elapsed
    (reward is NaN). ``drop_hold=True`` removes HOLD decisions whose reward
    is 0 by construction — they don't teach the Q-network anything about
    sizing, and they would otherwise dominate the action histogram.
    """
    df = _load_parquet_or_csv(dataset_path)

    # Schema sanity
    feat_cols = [f"feat__{name}" for name in FEATURE_NAMES]
    missing = [c for c in feat_cols if c not in df.columns]
    if missing:
        raise ValueError(
            f"dataset {dataset_path} missing {len(missing)} feature columns: "
            f"{missing[:5]}{'…' if len(missing) > 5 else ''}"
        )
    if "reward" not in df.columns or "behavior_size_tier" not in df.columns:
        raise ValueError("dataset must include 'reward' and 'behavior_size_tier' columns")

    if drop_pending:
        df = df[df["reward"].notna()].reset_index(drop=True)
    if drop_hold and "llm_action" in df.columns:
        df = df[df["llm_action"] != "HOLD"].reset_index(drop=True)

    if len(df) == 0:
        raise ValueError(
            f"dataset {dataset_path} has zero usable rows after filtering "
            f"(pending dropped={drop_pending}, hold dropped={drop_hold})"
        )

    states = torch.from_numpy(df[feat_cols].to_numpy(dtype=np.float32))
    actions = torch.from_numpy(df["behavior_size_tier"].to_numpy(dtype=np.int64))
    rewards = torch.from_numpy(df["reward"].to_numpy(dtype=np.float32))
    behavior = torch.from_numpy(
        df["behavior_size_pct"].fillna(1.0).to_numpy(dtype=np.float32)
    )

    return TrainingTensors(
        states=states,
        actions=actions,
        rewards=rewards,
        behavior_size_pct=behavior,
        tickers=df.get("ticker", ["?"] * len(df)).tolist(),
        dates=df.get("trade_date", ["?"] * len(df)).tolist(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Train / validation split
# ─────────────────────────────────────────────────────────────────────────────


def time_aware_split(
    data: TrainingTensors,
    *,
    val_fraction: float = 0.20,
    mode: str = "time",
    seed: int = 42,
) -> Tuple[TrainingTensors, TrainingTensors]:
    """Split into train / validation.

    ``mode="time"`` (default and recommended): per-ticker chronological tail
    is the validation set. ``mode="random"`` uses a seeded random split —
    debug-only because it mixes future and past.
    """
    n = data.states.shape[0]
    if n < 2:
        raise ValueError("need at least 2 samples to split")
    val_fraction = max(0.05, min(0.5, val_fraction))

    if mode == "random":
        rng = np.random.RandomState(seed)
        idx = rng.permutation(n)
        n_val = max(1, int(round(n * val_fraction)))
        val_idx = sorted(idx[:n_val].tolist())
        train_idx = sorted(idx[n_val:].tolist())
    elif mode == "time":
        # Sort by (ticker, date) then peel the tail of each ticker.
        by_ticker: Dict[str, List[int]] = {}
        for i, (tk, dt) in enumerate(zip(data.tickers, data.dates)):
            by_ticker.setdefault(tk, []).append(i)
        train_idx: List[int] = []
        val_idx: List[int] = []
        for tk, idxs in by_ticker.items():
            ordered = sorted(idxs, key=lambda i: data.dates[i])
            n_val_tk = max(1, int(round(len(ordered) * val_fraction)))
            train_idx.extend(ordered[:-n_val_tk])
            val_idx.extend(ordered[-n_val_tk:])
        if not train_idx:
            # Degenerate single-ticker tiny dataset — fall back to global tail.
            cutoff = max(1, int(n * (1 - val_fraction)))
            train_idx = list(range(cutoff))
            val_idx = list(range(cutoff, n))
        train_idx.sort()
        val_idx.sort()
    else:
        raise ValueError(f"unknown split mode: {mode}")

    def _take(indices: List[int]) -> TrainingTensors:
        return TrainingTensors(
            states=data.states[indices],
            actions=data.actions[indices],
            rewards=data.rewards[indices],
            behavior_size_pct=data.behavior_size_pct[indices],
            tickers=[data.tickers[i] for i in indices],
            dates=[data.dates[i] for i in indices],
        )

    return _take(train_idx), _take(val_idx)


# ─────────────────────────────────────────────────────────────────────────────
# Loss
# ─────────────────────────────────────────────────────────────────────────────


def cql_loss(
    q_values: torch.Tensor,        # (B, N_ACTIONS)
    actions: torch.Tensor,         # (B,)
    rewards: torch.Tensor,         # (B,)
    *,
    alpha: float,
    temperature: float,
) -> Tuple[torch.Tensor, Dict[str, float]]:
    """Single-step CQL: TD loss (γ=0) + conservative log-sum-exp penalty.

    Returns ``(total_loss, telemetry_dict)``. Telemetry is detached.
    """
    batch = q_values.shape[0]
    q_taken = q_values.gather(1, actions.view(-1, 1)).squeeze(1)
    td_loss = F.mse_loss(q_taken, rewards)

    logsumexp = torch.logsumexp(q_values / temperature, dim=1) * temperature
    cql_penalty = (logsumexp - q_taken).mean()

    total = td_loss + alpha * cql_penalty
    telemetry = {
        "td_loss": float(td_loss.detach().cpu().item()),
        "cql_penalty": float(cql_penalty.detach().cpu().item()),
        "total_loss": float(total.detach().cpu().item()),
        "q_mean": float(q_values.detach().mean().cpu().item()),
        "q_max": float(q_values.detach().max().cpu().item()),
    }
    return total, telemetry


# ─────────────────────────────────────────────────────────────────────────────
# Training loop
# ─────────────────────────────────────────────────────────────────────────────


class _TensorDataset(Dataset):
    def __init__(self, t: TrainingTensors):
        self.t = t

    def __len__(self) -> int:
        return self.t.states.shape[0]

    def __getitem__(self, i: int):
        return self.t.states[i], self.t.actions[i], self.t.rewards[i]


@dataclass
class TrainingResult:
    """Returned by :func:`train` — caller writes this into the model card."""

    policy: RLSizingPolicy
    config: TrainingConfig
    train_history: List[Dict[str, float]] = field(default_factory=list)
    val_history: List[Dict[str, float]] = field(default_factory=list)
    n_train: int = 0
    n_val: int = 0
    best_epoch: int = 0
    best_val_td_loss: float = float("inf")
    wall_clock_seconds: float = 0.0

    def to_card_dict(self) -> Dict[str, Any]:
        return {
            "config": self.config.as_dict(),
            "feature_version": FEATURE_VERSION,
            "n_train": self.n_train,
            "n_val": self.n_val,
            "best_epoch": self.best_epoch,
            "best_val_td_loss": self.best_val_td_loss,
            "wall_clock_seconds": self.wall_clock_seconds,
            "train_history": self.train_history,
            "val_history": self.val_history,
        }


def _validate(network: QNetwork, val: TrainingTensors, config: TrainingConfig) -> Dict[str, float]:
    network.eval()
    with torch.no_grad():
        q = network(val.states)
        _, tele = cql_loss(
            q, val.actions, val.rewards,
            alpha=config.cql_alpha, temperature=config.cql_temperature,
        )
    network.train()
    return tele


def train(
    dataset_path: str | Path,
    *,
    config: TrainingConfig = DEFAULT_TRAINING_CONFIG,
    drop_pending: bool = True,
    drop_hold: bool = True,
) -> TrainingResult:
    """Run the full CQL training pipeline.

    Returns a :class:`TrainingResult`. Saves nothing on disk; the caller is
    responsible for ``result.policy.save(path)``. This keeps tests fast and
    side-effect-free.
    """
    seed_everything(config.seed)

    full = materialize_dataset(dataset_path, drop_pending=drop_pending, drop_hold=drop_hold)
    if full.states.shape[1] != feature_vector_size():
        raise ValueError(
            f"dataset state_dim={full.states.shape[1]} != FEATURE_NAMES "
            f"length {feature_vector_size()}; regenerate the training data"
        )

    train_t, val_t = time_aware_split(
        full,
        val_fraction=config.val_fraction,
        mode=config.split_mode,
        seed=config.seed,
    )
    logger.info(
        "rl-train: %d total samples → %d train / %d val (split=%s)",
        full.states.shape[0], train_t.states.shape[0], val_t.states.shape[0], config.split_mode,
    )

    network = QNetwork(
        state_dim=feature_vector_size(),
        hidden_dims=config.hidden_dims,
        dropout_p=config.dropout_p,
        n_actions=N_ACTIONS,
    )

    optimizer = torch.optim.Adam(
        network.parameters(),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )

    loader = DataLoader(
        _TensorDataset(train_t),
        batch_size=min(config.batch_size, max(1, train_t.states.shape[0])),
        shuffle=True,
        drop_last=False,
        generator=torch.Generator().manual_seed(config.seed),
    )

    history_train: List[Dict[str, float]] = []
    history_val: List[Dict[str, float]] = []
    best_val_td = float("inf")
    best_epoch = 0
    best_state: Optional[Dict[str, torch.Tensor]] = None
    epochs_without_improve = 0

    t0 = time.perf_counter()
    network.train()
    for epoch in range(1, config.n_epochs + 1):
        epoch_metrics: List[Dict[str, float]] = []
        for states, actions, rewards in loader:
            optimizer.zero_grad(set_to_none=True)
            q = network(states)
            loss, tele = cql_loss(
                q, actions, rewards,
                alpha=config.cql_alpha, temperature=config.cql_temperature,
            )
            loss.backward()
            if config.grad_clip_norm and config.grad_clip_norm > 0:
                nn.utils.clip_grad_norm_(network.parameters(), config.grad_clip_norm)
            optimizer.step()
            epoch_metrics.append(tele)

        # Aggregate by simple mean
        train_summary = {
            "epoch": epoch,
            "td_loss": float(np.mean([m["td_loss"] for m in epoch_metrics])),
            "cql_penalty": float(np.mean([m["cql_penalty"] for m in epoch_metrics])),
            "total_loss": float(np.mean([m["total_loss"] for m in epoch_metrics])),
            "q_mean": float(np.mean([m["q_mean"] for m in epoch_metrics])),
        }
        history_train.append(train_summary)

        if val_t.states.shape[0] > 0:
            val_tele = _validate(network, val_t, config)
            val_tele["epoch"] = epoch
            history_val.append(val_tele)
            current_val_td = val_tele["td_loss"]
        else:
            current_val_td = train_summary["td_loss"]

        if current_val_td + 1e-8 < best_val_td:
            best_val_td = current_val_td
            best_epoch = epoch
            best_state = {k: v.detach().clone() for k, v in network.state_dict().items()}
            epochs_without_improve = 0
        else:
            epochs_without_improve += 1
            if epochs_without_improve >= config.early_stopping_patience:
                logger.info("rl-train: early stop at epoch %d (no improvement for %d epochs)",
                            epoch, epochs_without_improve)
                break

        if epoch == 1 or epoch % 10 == 0:
            logger.info(
                "rl-train: epoch %3d  train_td=%.6f  cql=%.6f  val_td=%.6f  q_mean=%.4f",
                epoch, train_summary["td_loss"], train_summary["cql_penalty"],
                current_val_td, train_summary["q_mean"],
            )

    if best_state is not None:
        network.load_state_dict(best_state)
    network.eval()

    fingerprint = {
        "feature_version": FEATURE_VERSION,
        "algorithm": config.algorithm,
        "algorithm_version": config.algorithm_version,
        "trained_on_n_samples": int(train_t.states.shape[0]),
        "best_val_td_loss": float(best_val_td),
        "best_epoch": int(best_epoch),
    }
    policy = RLSizingPolicy(q_network=network, config=config, model_fingerprint=fingerprint)

    wall = time.perf_counter() - t0
    return TrainingResult(
        policy=policy,
        config=config,
        train_history=history_train,
        val_history=history_val,
        n_train=int(train_t.states.shape[0]),
        n_val=int(val_t.states.shape[0]),
        best_epoch=best_epoch,
        best_val_td_loss=float(best_val_td),
        wall_clock_seconds=float(wall),
    )
