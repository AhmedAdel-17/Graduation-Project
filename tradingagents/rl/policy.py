"""Q-network architecture + the inference-time decision policy wrapper.

The Q-network is intentionally tiny (~5k parameters): two hidden layers of
64 units each, ReLU, dropout 0.1, head emitting one Q-value per decision
action (BUY / HOLD / SELL).

Two classes here:

- :class:`QNetwork` — pure ``torch.nn.Module``. Used inside ``train.py``.
- :class:`RLDecisionPolicy` — inference wrapper. Loads weights, normalizes /
  validates state vectors, picks the argmax decision, and exposes the model
  fingerprint to ``tradingagents/db/audit_writer.py``. ``RLSizingPolicy`` is
  kept as a backwards-compatible alias for existing import sites.

The policy is a *parallel decision arm*: it returns an independent BUY/HOLD/
SELL opinion learned from the realized outcomes of past decisions. It never
sizes a position and never overrides the deterministic EGX risk veto — the
caller decides what to do with the recommendation (today: log it and compare
it against the committee; see ``scripts/backtester.py``).

Both paths use exactly the same feature extractor
(``tradingagents.rl.feature_extractor.extract_state_features``) so training
and inference observation spaces cannot drift.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn

from tradingagents.rl.config import (
    DECISION_ACTIONS,
    DEFAULT_TRAINING_CONFIG,
    N_ACTIONS,
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
    """MLP returning one Q-value per decision action.

    Input:  ``(batch, state_dim)`` float32
    Output: ``(batch, N_ACTIONS)`` float32 — Q(s, a) for a in DECISION_ACTIONS

    The architecture is fixed by ``TrainingConfig.hidden_dims``; we don't
    expose more knobs because the data scale doesn't justify them.
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

    ``action`` is the policy's recommended decision (one of
    ``DECISION_ACTIONS``). The recommendation is advisory: the backtester runs
    it as a parallel arm and never lets it override the deterministic risk
    veto. ``q_values`` are recorded for audit.
    """

    action: str                     # argmax decision: BUY / HOLD / SELL
    action_index: int               # index into DECISION_ACTIONS
    committee_action: str           # the decision the LLM committee proposed
    q_values: Tuple[float, ...]     # one Q-value per action (for audit)
    feature_version: str
    model_fingerprint: Dict[str, Any]

    @property
    def agrees_with_committee(self) -> bool:
        return self.action.strip().upper() == self.committee_action.strip().upper()


def _hash_state_dict(state_dict: Dict[str, torch.Tensor]) -> str:
    """Stable 16-char hex digest of a Q-network's parameters."""
    h = hashlib.sha256()
    for key in sorted(state_dict.keys()):
        tensor = state_dict[key].detach().cpu().to(torch.float32).contiguous()
        h.update(key.encode("utf-8"))
        h.update(tensor.numpy().tobytes())
    return h.hexdigest()[:16]


def _committee_action(final_state: Dict[str, Any]) -> str:
    """Extract the committee's BUY/HOLD/SELL via the project's regex extractor."""
    try:
        from tradingagents.graph.signal_processing import SignalProcessor

        signal = (
            final_state.get("final_trade_decision")
            or final_state.get("trader_investment_plan")
            or ""
        )
        return SignalProcessor().process_signal(signal)
    except Exception:
        return "HOLD"


class RLDecisionPolicy:
    """Inference wrapper around a trained :class:`QNetwork`.

    The backtester calls ``predict(final_state, ticker, trade_date,
    portfolio_ctx)``. The class is fail-closed: if no model is loaded
    ``predict`` returns the *committee's own* decision (identity / pass-through)
    with a warning, so the feature flag's "off" path is also the
    "model missing" path and the parallel arm degrades to "agree with the
    committee".
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
        committee = _committee_action(final_state if isinstance(final_state, dict) else {})

        if not self.is_loaded:
            logger.warning(
                "RLDecisionPolicy.predict called without a loaded model; "
                "passing through the committee decision (%s)", committee
            )
            try:
                idx = DECISION_ACTIONS.index(committee.strip().upper())
            except ValueError:
                idx = DECISION_ACTIONS.index("HOLD")
            return PolicyPrediction(
                action=DECISION_ACTIONS[idx],
                action_index=idx,
                committee_action=committee,
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
        return PolicyPrediction(
            action=DECISION_ACTIONS[action_idx],
            action_index=action_idx,
            committee_action=committee,
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
            "decision_actions": list(DECISION_ACTIONS),
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
    def load(cls, path: str | Path) -> "RLDecisionPolicy":
        """Load a previously :meth:`save`-d policy."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"No RL policy at {path}")

        # Trusted local file, weights-only would reject the metadata dict.
        payload = torch.load(path, map_location="cpu", weights_only=False)
        cfg_dict = dict(payload.get("config") or {})
        cfg_dict.pop("decision_actions", None)
        cfg_dict.pop("size_tiers", None)      # tolerate legacy v1 cards
        cfg_dict.pop("n_actions", None)
        cfg_dict.pop("size_multiplier_min", None)
        cfg_dict.pop("size_multiplier_max", None)
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
        n_actions = int(arch.get("n_actions", N_ACTIONS))
        if n_actions != N_ACTIONS:
            raise ValueError(
                f"Model has a {n_actions}-action head but the decision policy "
                f"expects {N_ACTIONS} actions ({', '.join(DECISION_ACTIONS)}). "
                f"This checkpoint predates the decision-policy action space — "
                f"retrain with scripts/train_rl_policy.py."
            )

        net = QNetwork(
            state_dim=state_dim,
            hidden_dims=hidden,
            dropout_p=dropout,
            n_actions=n_actions,
        )
        net.load_state_dict(payload["state_dict"])
        net.eval()
        return cls(
            q_network=net,
            config=config,
            model_fingerprint=payload.get("model_fingerprint") or {},
        )


# Backwards-compatible alias: existing import sites (api_server, backtester,
# audit_writer, tests) reference ``RLSizingPolicy``. The class is now a
# decision policy; the alias keeps those imports working.
RLSizingPolicy = RLDecisionPolicy


# ─────────────────────────────────────────────────────────────────────────────
# Utility: identity / fail-closed policy
# ─────────────────────────────────────────────────────────────────────────────


def identity_policy() -> RLDecisionPolicy:
    """The no-op policy: passes the committee's own decision through.

    The backtester uses this when the feature flag is off OR the model fails
    to load. Centralizing the construction here means there is one
    well-tested no-op path, not several.
    """
    return RLDecisionPolicy(q_network=None)
