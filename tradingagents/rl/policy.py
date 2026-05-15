"""Q-network architecture + the inference-time policy wrapper.

The Q-network is intentionally tiny (~5k parameters): two hidden layers of
64 units each, ReLU, dropout 0.1, head emitting one Q-value per size tier.

Two classes here:

- :class:`QNetwork` — pure ``torch.nn.Module``. Used inside ``train.py``.
- :class:`RLSizingPolicy` — inference wrapper. Loads weights, normalizes /
  validates state vectors, applies the inference-time guardrail clamp
  (``size_mult ∈ [0.0, 1.0]``), and exposes the model fingerprint to
  ``tradingagents/db/audit_writer.py``.

Both paths use exactly the same feature extractor
(``tradingagents.rl.feature_extractor.extract_state_features``) so training
and inference observation spaces cannot drift.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn

from tradingagents.rl.config import (
    DEFAULT_TRAINING_CONFIG,
    N_ACTIONS,
    SIZE_TIERS,
    TrainingConfig,
)
from tradingagents.rl.feature_extractor import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    extract_state_features,
    feature_vector_size,
)

logger = logging.getLogger("tradingagents.rl.policy")


# ─────────────────────────────────────────────────────────────────────────────
# Q-network
# ─────────────────────────────────────────────────────────────────────────────


class QNetwork(nn.Module):
    """MLP returning one Q-value per size tier.

    Input:  ``(batch, state_dim)`` float32
    Output: ``(batch, N_ACTIONS)`` float32 — Q(s, a) for a in SIZE_TIERS

    The architecture is fixed by ``TrainingConfig.hidden_dims``; we don't
    expose more knobs in v1 because the data scale doesn't justify them.
    """

    def __init__(
        self,
        state_dim: int,
        hidden_dims: Tuple[int, ...] = (64, 64),
        dropout_p: float = 0.1,
        n_actions: int = N_ACTIONS,
    ) -> None:
        super().__init__()
        layers = []
        prev = state_dim
        for h in hidden_dims:
            layers.append(nn.Linear(prev, h))
            layers.append(nn.ReLU())
            if dropout_p > 0.0:
                layers.append(nn.Dropout(p=dropout_p))
            prev = h
        layers.append(nn.Linear(prev, n_actions))
        self.body = nn.Sequential(*layers)
        # Record metadata so saved checkpoints can be loaded without the
        # caller having to remember the architecture.
        self.state_dim = int(state_dim)
        self.n_actions = int(n_actions)
        self.hidden_dims = tuple(int(h) for h in hidden_dims)
        self.dropout_p = float(dropout_p)

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # type: ignore[override]
        return self.body(x)

    # ── Architectural fingerprint (hashable into model card) ───────────────
    def architecture_summary(self) -> Dict[str, Any]:
        return {
            "state_dim": self.state_dim,
            "n_actions": self.n_actions,
            "hidden_dims": list(self.hidden_dims),
            "dropout_p": self.dropout_p,
            "n_parameters": sum(p.numel() for p in self.parameters()),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Inference policy
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PolicyPrediction:
    """The single object the backtester / API server consumes at inference.

    ``size_multiplier`` is clamped to ``[size_multiplier_min, size_multiplier_max]``
    (see :class:`TrainingConfig`). Stage A and the plan promise this is the
    final knob; the backtester multiplies ``trader_target_shares * confidence
    * size_multiplier`` and then re-applies the deterministic risk veto.
    """

    size_multiplier: float          # the actual fraction to apply
    action_index: int               # argmax tier index
    q_values: Tuple[float, ...]     # one Q-value per tier (for audit)
    feature_version: str
    model_fingerprint: Dict[str, Any]


def _hash_state_dict(state_dict: Dict[str, torch.Tensor]) -> str:
    """Stable 16-char hex digest of a Q-network's parameters.

    Used so the audit trail can record *which* weights produced a prediction
    without dumping the full tensor.
    """
    h = hashlib.sha256()
    for key in sorted(state_dict.keys()):
        tensor = state_dict[key].detach().cpu().to(torch.float32).contiguous()
        h.update(key.encode("utf-8"))
        h.update(tensor.numpy().tobytes())
    return h.hexdigest()[:16]


class RLSizingPolicy:
    """Inference wrapper around a trained :class:`QNetwork`.

    Stage C will call ``predict(final_state, ticker, trade_date,
    portfolio_ctx)`` from inside ``scripts/backtester.py``. The class is
    fail-closed: if no model is loaded ``predict`` returns the identity
    multiplier (``1.0``) with a warning, so the feature flag's "off" path
    is also the "model missing" path.
    """

    def __init__(
        self,
        q_network: Optional[QNetwork] = None,
        config: TrainingConfig = DEFAULT_TRAINING_CONFIG,
        model_fingerprint: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.q_network = q_network
        self.config = config
        self._model_fingerprint = model_fingerprint or {}

        if q_network is not None:
            q_network.eval()
            for p in q_network.parameters():
                p.requires_grad_(False)
            expected = feature_vector_size()
            if q_network.state_dim != expected:
                raise ValueError(
                    f"QNetwork.state_dim={q_network.state_dim} does not match "
                    f"FEATURE_NAMES length {expected}. Re-train against the "
                    f"current feature extractor (FEATURE_VERSION={FEATURE_VERSION})."
                )

    @property
    def is_loaded(self) -> bool:
        return self.q_network is not None

    @property
    def model_fingerprint(self) -> Dict[str, Any]:
        # Compose the fingerprint each call so it always reflects current
        # config (caller cannot tamper with the underlying dict).
        fp = dict(self._model_fingerprint)
        fp.setdefault("feature_version", FEATURE_VERSION)
        fp.setdefault("algorithm", self.config.algorithm)
        fp.setdefault("algorithm_version", self.config.algorithm_version)
        if self.q_network is not None:
            fp.setdefault("weights_sha256_16", _hash_state_dict(self.q_network.state_dict()))
            fp.setdefault("architecture", self.q_network.architecture_summary())
        return fp

    # ── Inference ──────────────────────────────────────────────────────────
    def predict(
        self,
        final_state: Dict[str, Any],
        *,
        ticker: str,
        trade_date: str,
        portfolio_ctx: Optional[Dict[str, Any]] = None,
    ) -> PolicyPrediction:
        """Return a :class:`PolicyPrediction` for one decision.

        ``final_state`` is the LangGraph output. ``portfolio_ctx`` carries
        out-of-state context (drawdown, settled cash) from the backtester.
        """
        if not self.is_loaded:
            logger.warning(
                "RLSizingPolicy.predict called without a loaded model; "
                "returning identity multiplier=1.0"
            )
            return PolicyPrediction(
                size_multiplier=1.0,
                action_index=N_ACTIONS - 1,  # = 1.0 in SIZE_TIERS
                q_values=tuple(0.0 for _ in range(N_ACTIONS)),
                feature_version=FEATURE_VERSION,
                model_fingerprint=self.model_fingerprint,
            )

        feats = extract_state_features(
            final_state,
            ticker=ticker,
            trade_date=trade_date,
            portfolio_ctx=portfolio_ctx,
        )
        x = torch.from_numpy(feats).unsqueeze(0)  # (1, state_dim)
        with torch.no_grad():
            q_vals = self.q_network(x).squeeze(0).cpu().numpy()

        action_idx = int(np.argmax(q_vals))
        raw_size = float(SIZE_TIERS[action_idx])
        size = float(
            np.clip(raw_size, self.config.size_multiplier_min, self.config.size_multiplier_max)
        )
        return PolicyPrediction(
            size_multiplier=size,
            action_index=action_idx,
            q_values=tuple(float(v) for v in q_vals),
            feature_version=FEATURE_VERSION,
            model_fingerprint=self.model_fingerprint,
        )

    # ── Serialization ──────────────────────────────────────────────────────
    def save(self, path: str | Path, extra_metadata: Optional[Dict[str, Any]] = None) -> Path:
        """Save weights + config + model card sidecar.

        Writes ``<path>`` (torch state_dict + metadata) and ``<path>.card.json``
        (human-readable model card). Returns the primary path.
        """
        if self.q_network is None:
            raise RuntimeError("Cannot save an unloaded policy")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        payload: Dict[str, Any] = {
            "state_dict": self.q_network.state_dict(),
            "config": self.config.as_dict(),
            "feature_version": FEATURE_VERSION,
            "feature_names": list(FEATURE_NAMES),
            "size_tiers": list(SIZE_TIERS),
            "architecture": self.q_network.architecture_summary(),
            "model_fingerprint": self.model_fingerprint,
            "extra_metadata": extra_metadata or {},
        }
        torch.save(payload, path)

        # Plain-text model card so reviewers can read it without torch.
        card_path = path.with_suffix(path.suffix + ".card.json")
        card = dict(payload)
        card.pop("state_dict", None)
        with open(card_path, "w", encoding="utf-8") as f:
            json.dump(card, f, indent=2, default=str)

        logger.info("rl-policy: saved weights to %s and card to %s", path, card_path)
        return path

    @classmethod
    def load(cls, path: str | Path) -> "RLSizingPolicy":
        """Load a previously :meth:`save`-d policy."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"No RL policy at {path}")

        # Trusted local file, weights-only would reject the metadata dict.
        payload = torch.load(path, map_location="cpu", weights_only=False)
        cfg_dict = dict(payload.get("config") or {})
        cfg_dict.pop("size_tiers", None)
        cfg_dict.pop("n_actions", None)
        if "hidden_dims" in cfg_dict and isinstance(cfg_dict["hidden_dims"], list):
            cfg_dict["hidden_dims"] = tuple(cfg_dict["hidden_dims"])
        config = TrainingConfig(**cfg_dict) if cfg_dict else DEFAULT_TRAINING_CONFIG

        feature_version = payload.get("feature_version", FEATURE_VERSION)
        if feature_version != FEATURE_VERSION:
            raise ValueError(
                f"Model was trained against feature_version={feature_version!r} "
                f"but the current extractor exports {FEATURE_VERSION!r}. "
                f"Either downgrade the extractor or retrain."
            )

        arch = payload.get("architecture") or {}
        state_dim = int(arch.get("state_dim", feature_vector_size()))
        hidden = tuple(int(h) for h in arch.get("hidden_dims", config.hidden_dims))
        dropout = float(arch.get("dropout_p", config.dropout_p))

        net = QNetwork(
            state_dim=state_dim,
            hidden_dims=hidden,
            dropout_p=dropout,
            n_actions=N_ACTIONS,
        )
        net.load_state_dict(payload["state_dict"])
        net.eval()
        return cls(
            q_network=net,
            config=config,
            model_fingerprint=payload.get("model_fingerprint") or {},
        )


# ─────────────────────────────────────────────────────────────────────────────
# Utility: identity / fail-closed policy
# ─────────────────────────────────────────────────────────────────────────────


def identity_policy() -> RLSizingPolicy:
    """The no-op policy: returns ``size_multiplier=1.0`` always.

    The backtester uses this when the feature flag is off OR the model
    fails to load. Centralizing the construction here means there is one
    well-tested no-op path, not several.
    """
    return RLSizingPolicy(q_network=None)
