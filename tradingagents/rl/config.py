"""Hyperparameters + seeds for the RL decision-calibration policy.

Single source of truth so training, evaluation, and inference quote the same
numbers. The values here were chosen for the small-data EGX setting; tune
carefully on validation, do not chase training loss on the train split.

All knobs are overridable via :class:`TrainingConfig` instantiation. The
``scripts/train_rl_policy.py`` CLI exposes the most-tuned ones as flags.

Action space
------------
The policy is a *decision* policy: given the same evidence the committee saw,
it picks a trade decision. The action vocabulary is the three EGX-admissible
decisions. The index order below is part of the saved-model contract — bumping
it is a breaking change for any saved model card.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Tuple


# The discrete action space: index -> decision. Order is part of the contract.
DECISION_ACTIONS: Tuple[str, ...] = ("BUY", "HOLD", "SELL")
N_ACTIONS: int = len(DECISION_ACTIONS)


def action_to_index(action: str) -> int:
    """Map a BUY/HOLD/SELL string to its action index (defaults to HOLD)."""
    a = (action or "").strip().upper()
    try:
        return DECISION_ACTIONS.index(a)
    except ValueError:
        return DECISION_ACTIONS.index("HOLD")


@dataclass(frozen=True)
class TrainingConfig:
    """Hyperparameters for one training run.

    Frozen so it can be hashed into the model fingerprint. Bump
    ``algorithm_version`` whenever the loss formulation changes.
    """

    # ── Identity ───────────────────────────────────────────────────────────
    algorithm: str = "cql_single_step"
    # v2: action space is {BUY, HOLD, SELL} with a counterfactual per-action
    # reward (full-feedback bandit) and a CQL anchor to the committee action.
    algorithm_version: str = "v2"

    # ── Architecture ───────────────────────────────────────────────────────
    hidden_dims: Tuple[int, ...] = (64, 64)
    dropout_p: float = 0.1
    activation: str = "relu"   # only "relu" supported; kept for fingerprint

    # ── Optimisation ───────────────────────────────────────────────────────
    learning_rate: float = 3e-4
    weight_decay: float = 1e-4
    batch_size: int = 64
    n_epochs: int = 200
    grad_clip_norm: float = 1.0
    early_stopping_patience: int = 30

    # ── CQL conservative regulariser ───────────────────────────────────────
    # The penalty pulls Q down on actions away from the committee's historical
    # action, so the policy only deviates from the committee when the
    # counterfactual reward gap is decisive. Tuned to the EGX reward scale
    # (~0.01-0.05): higher = more deference to the committee, alpha=0 = plain
    # full-feedback regression. ~0.01 keeps a genuine conservative anchor while
    # still letting strong evidence flip an over-cautious HOLD.
    cql_alpha: float = 0.01         # weight on log-sum-exp penalty
    cql_temperature: float = 1.0    # softmax temperature inside the penalty

    # ── Reward / discount ──────────────────────────────────────────────────
    # Each decision is a one-step bandit, so gamma is unused. Kept for a future
    # multi-step extension (matters only when gamma > 0).
    gamma: float = 0.0

    # ── Validation split ───────────────────────────────────────────────────
    val_fraction: float = 0.20      # last 20% of dates per ticker -> validation
    split_mode: str = "time"        # "time" (chronological) | "random" (debug only)

    # ── Determinism ────────────────────────────────────────────────────────
    seed: int = 42
    device: str = "cpu"             # cpu is fine for ~5k-parameter MLP

    def as_dict(self) -> dict:
        d = asdict(self)
        d["decision_actions"] = list(DECISION_ACTIONS)
        d["n_actions"] = N_ACTIONS
        return d


DEFAULT_TRAINING_CONFIG = TrainingConfig()
