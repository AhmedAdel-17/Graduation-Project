"""Hyperparameters + seeds for the RL meta-policy.

Single source of truth so training, evaluation, and inference quote the same
numbers. The values here were chosen for the small-data EGX setting
(~1.5k-2.5k usable samples after expansion); tune carefully on validation,
do not chase training loss on the train split.

All knobs are overridable via :class:`TrainingConfig` instantiation. The
``scripts/train_rl_policy.py`` CLI exposes the most-tuned ones as flags.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Tuple


# The discrete action space: tier index -> size multiplier.
# Bumping this list is a breaking change for any saved model card.
SIZE_TIERS: Tuple[float, ...] = (0.0, 0.25, 0.50, 0.75, 1.0)
N_ACTIONS: int = len(SIZE_TIERS)


@dataclass(frozen=True)
class TrainingConfig:
    """Hyperparameters for one training run.

    Frozen so it can be hashed into the model fingerprint. Bump
    ``algorithm_version`` whenever the loss formulation changes.
    """

    # ── Identity ───────────────────────────────────────────────────────────
    algorithm: str = "cql_single_step"
    algorithm_version: str = "v1"

    # ── Architecture ───────────────────────────────────────────────────────
    hidden_dims: Tuple[int, ...] = (64, 64)
    dropout_p: float = 0.1
    activation: str = "relu"   # only "relu" supported in v1; kept for fingerprint

    # ── Optimisation ───────────────────────────────────────────────────────
    learning_rate: float = 3e-4
    weight_decay: float = 1e-4
    batch_size: int = 64
    n_epochs: int = 200
    grad_clip_norm: float = 1.0
    early_stopping_patience: int = 30

    # ── CQL conservative regulariser ───────────────────────────────────────
    cql_alpha: float = 1.0          # weight on log-sum-exp penalty
    cql_temperature: float = 1.0    # softmax temperature inside the penalty

    # ── Reward / discount ──────────────────────────────────────────────────
    # We frame each decision as a one-step bandit, so gamma is unused. Kept
    # for future multi-step extensions (matters only when gamma > 0).
    gamma: float = 0.0

    # ── Validation split ───────────────────────────────────────────────────
    val_fraction: float = 0.20      # last 20% of dates per ticker -> validation
    split_mode: str = "time"        # "time" (chronological) | "random" (debug only)

    # ── Determinism ────────────────────────────────────────────────────────
    seed: int = 42
    device: str = "cpu"             # cpu is fine for ~5k-parameter MLP

    # ── Inference guardrails ──────────────────────────────────────────────
    # Plan §3.1: RL can only SHRINK size, never grow it. The clamp is also
    # enforced at inference time (defense in depth).
    size_multiplier_min: float = 0.0
    size_multiplier_max: float = 1.0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["size_tiers"] = list(SIZE_TIERS)
        d["n_actions"] = N_ACTIONS
        return d


DEFAULT_TRAINING_CONFIG = TrainingConfig()
