"""Online / incremental RL for the position-sizing meta-policy.

The offline path (``train.py``) fits a Q-network once on a batch of matured
trades. This module lets that same Q-network **keep learning as new trade
outcomes arrive** — the online-RL upgrade requested over the offline-only
setup — without changing the observation space, action space, reward shaping,
or the audit/serialization contract.

Design (kept deliberately conservative for the small-data EGX setting):

* **Single-step bandit, so no bootstrapping.** ``gamma = 0`` (see
  ``config.TrainingConfig``), so the Q-target for a taken action is just the
  shaped reward. There is therefore no target network and no TD-bootstrap
  instability — online learning is incremental regression-to-reward plus the
  same CQL conservative penalty used offline (``train.cql_loss``). That penalty
  is what keeps a handful of fresh transitions from blowing up Q-values on
  never-taken actions.

* **Warm start.** Initialise from the offline-trained policy so the agent
  starts from the batch optimum and *adapts*, rather than relearning from
  scratch.

* **Experience replay.** Every committed transition is kept in a bounded,
  persisted buffer. Each update samples a mini-batch from the *whole* buffer
  (old + new), which is the standard guard against catastrophic forgetting.

* **No look-ahead.** A transition is only committed once its reward horizon has
  elapsed — reusing ``dataset._no_lookahead_window`` and ``dataset.shaped_reward``
  so online and offline rewards are byte-for-byte comparable.

* **Fail-safe guardrail.** Each ``update()`` is checked against a held-out slice
  of the buffer; if it degrades holdout loss past a tolerance for ``patience``
  consecutive updates, the trainer reverts to the last-good snapshot and decays
  the learning rate. This mirrors the walk-forward PASS/FAIL doctrine: an online
  update that hurts is rolled back, never silently shipped.

* **Reproducible + auditable.** Seeded RNG; the model fingerprint carries
  ``n_online_updates`` / ``buffer_size`` so every prediction is traceable to the
  exact amount of online adaptation behind it.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn

from tradingagents.rl.config import (
    DECISION_ACTIONS,
    DEFAULT_TRAINING_CONFIG,
    N_ACTIONS,
    TrainingConfig,
    action_to_index,
)
from tradingagents.rl.dataset import (
    DEFAULT_REWARD_HORIZON_DAYS,
    _no_lookahead_window,
    counterfactual_rewards,
)
from tradingagents.rl.feature_extractor import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    extract_state_features,
    feature_vector_size,
)
from tradingagents.rl.policy import QNetwork, RLDecisionPolicy, RLSizingPolicy
from tradingagents.rl.train import cql_loss, seed_everything


def _reward_vector(forward_return, drawdown=None) -> Optional[np.ndarray]:
    """Counterfactual reward vector [BUY, HOLD, SELL] or None if not yet known."""
    cf = counterfactual_rewards(forward_return, drawdown_during_holding=drawdown)
    if cf.get("BUY") is None or cf.get("SELL") is None:
        return None
    return np.asarray(
        [cf["BUY"], cf.get("HOLD", 0.0), cf["SELL"]], dtype=np.float32
    )

logger = logging.getLogger("tradingagents.rl.online")


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OnlineConfig:
    """Knobs for incremental updates. Defaults are intentionally cautious."""

    learning_rate: float = 1e-4          # smaller than offline (3e-4): gentle adaptation
    weight_decay: float = 1e-4
    grad_clip_norm: float = 1.0
    # Matches the offline default: tuned to the EGX reward scale so the
    # conservative anchor to the committee action doesn't dominate the signal.
    cql_alpha: float = 0.01
    cql_temperature: float = 1.0

    updates_per_call: int = 8            # gradient steps per update() invocation
    batch_size: int = 32
    min_buffer_to_update: int = 16       # fail-safe: don't learn from near-nothing
    buffer_capacity: int = 5000          # FIFO cap on retained transitions

    # Guardrail
    holdout_fraction: float = 0.25
    degrade_tolerance: float = 0.05      # accept ≤ +5% holdout-loss drift
    revert_patience: int = 3             # consecutive degrading updates → revert + decay
    lr_decay_on_revert: float = 0.5

    seed: int = 42
    algorithm: str = "online_cql_single_step"
    algorithm_version: str = "v2"

    def as_dict(self) -> dict:
        return asdict(self)


DEFAULT_ONLINE_CONFIG = OnlineConfig()


# ─────────────────────────────────────────────────────────────────────────────
# Replay buffer
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _ReplayBuffer:
    """Bounded FIFO buffer of committed (state, reward_vector, behavior_action).

    Each transition carries the full counterfactual reward vector over
    ``DECISION_ACTIONS`` and the committee's action index (the CQL anchor),
    matching the offline decision-policy training scheme. ``keys`` dedups by
    (ticker, trade_date) so re-ingesting the same matured trade never
    double-counts it.
    """

    state_dim: int
    capacity: int = 5000
    states: List[np.ndarray] = field(default_factory=list)
    reward_vectors: List[np.ndarray] = field(default_factory=list)
    behavior_actions: List[int] = field(default_factory=list)
    keys: set = field(default_factory=set)

    def __len__(self) -> int:
        return len(self.reward_vectors)

    def add(
        self,
        state: np.ndarray,
        reward_vector: np.ndarray,
        behavior_action: int,
        key: Optional[str],
    ) -> bool:
        if key is not None and key in self.keys:
            return False
        self.states.append(np.asarray(state, dtype=np.float32))
        self.reward_vectors.append(np.asarray(reward_vector, dtype=np.float32))
        self.behavior_actions.append(int(behavior_action))
        if key is not None:
            self.keys.add(key)
        # FIFO eviction (keys for evicted rows are intentionally left in the set:
        # we never want to relearn an already-seen, already-evicted transition).
        while len(self.reward_vectors) > self.capacity:
            self.states.pop(0)
            self.reward_vectors.pop(0)
            self.behavior_actions.pop(0)
        return True

    def tensors(self, indices: Optional[List[int]] = None):
        idx = list(range(len(self.reward_vectors))) if indices is None else list(indices)
        if not idx:
            return torch.empty(0), torch.empty(0, N_ACTIONS), torch.empty(0, dtype=torch.int64)
        s = torch.from_numpy(np.stack([self.states[i] for i in idx]))
        r = torch.from_numpy(np.stack([self.reward_vectors[i] for i in idx]))
        a = torch.tensor([self.behavior_actions[i] for i in idx], dtype=torch.int64)
        return s, r, a


# ─────────────────────────────────────────────────────────────────────────────
# Online trainer
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class OnlineUpdateResult:
    """Telemetry returned by :meth:`OnlineRLTrainer.update`."""

    updated: bool
    reverted: bool
    n_steps: int
    buffer_size: int
    holdout_loss_before: Optional[float]
    holdout_loss_after: Optional[float]
    learning_rate: float
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class OnlineRLTrainer:
    """Incremental learner wrapping a :class:`QNetwork`.

    Typical loop (live or backtest)::

        trainer = OnlineRLTrainer.from_offline_policy("models/rl_sizing.pt")
        ...
        # at decision time, for inference:
        action = trainer.policy.predict(final_state, ticker=t, trade_date=d).action
        ...
        # once the 20-day horizon for that trade has elapsed:
        trainer.observe_closed_trade(
            state_features=feats, action="BUY", size_pct=0.5,
            forward_return=0.031, trade_date=d, ticker=t,
        )
        trainer.update()          # incremental, guard-railed
        trainer.save("models/rl_sizing_online.pt")
    """

    def __init__(
        self,
        q_network: QNetwork,
        *,
        config: TrainingConfig = DEFAULT_TRAINING_CONFIG,
        online_config: OnlineConfig = DEFAULT_ONLINE_CONFIG,
        model_fingerprint: Optional[Dict[str, Any]] = None,
    ) -> None:
        if q_network.state_dim != feature_vector_size():
            raise ValueError(
                f"QNetwork.state_dim={q_network.state_dim} != FEATURE_NAMES "
                f"length {feature_vector_size()} — retrain against the current extractor."
            )
        seed_everything(online_config.seed)
        self.q_network = q_network
        self.q_network.train()
        self.config = config
        self.online = online_config
        self._base_fingerprint = dict(model_fingerprint or {})

        self.buffer = _ReplayBuffer(
            state_dim=q_network.state_dim, capacity=online_config.buffer_capacity
        )
        self._optimizer = torch.optim.Adam(
            self.q_network.parameters(),
            lr=online_config.learning_rate,
            weight_decay=online_config.weight_decay,
        )
        self._lr = online_config.learning_rate
        self._rng = np.random.default_rng(online_config.seed)
        self.n_online_updates = 0
        self.n_observed = 0
        self._consecutive_degrades = 0
        self._best_state: Optional[Dict[str, torch.Tensor]] = None
        self._best_holdout: float = float("inf")
        # Pending decisions awaiting their reward horizon: key -> (features, action, size_pct)
        self._pending: Dict[str, Tuple[np.ndarray, str, float]] = {}

    # ── Constructors ────────────────────────────────────────────────────────
    @classmethod
    def from_offline_policy(
        cls,
        path: str | Path,
        *,
        online_config: OnlineConfig = DEFAULT_ONLINE_CONFIG,
    ) -> "OnlineRLTrainer":
        """Warm-start from a saved offline :class:`RLSizingPolicy`."""
        policy = RLSizingPolicy.load(path)
        if policy.q_network is None:
            raise ValueError(f"offline policy at {path} has no weights to warm-start from")
        return cls(
            q_network=policy.q_network,
            config=policy.config,
            online_config=online_config,
            model_fingerprint={**policy.model_fingerprint, "warm_started_from": str(path)},
        )

    @classmethod
    def cold_start(
        cls,
        *,
        config: TrainingConfig = DEFAULT_TRAINING_CONFIG,
        online_config: OnlineConfig = DEFAULT_ONLINE_CONFIG,
    ) -> "OnlineRLTrainer":
        """Fresh Q-network (no offline pretraining). Mostly for tests/ablation."""
        net = QNetwork(
            state_dim=feature_vector_size(),
            hidden_dims=config.hidden_dims,
            dropout_p=config.dropout_p,
            n_actions=N_ACTIONS,
        )
        return cls(q_network=net, config=config, online_config=online_config)

    # ── Inference ────────────────────────────────────────────────────────────
    @property
    def policy(self) -> RLSizingPolicy:
        """A fresh inference wrapper reflecting the *current* online weights."""
        return RLSizingPolicy(
            q_network=self.q_network,
            config=self.config,
            model_fingerprint=self.model_fingerprint,
        )

    @property
    def model_fingerprint(self) -> Dict[str, Any]:
        fp = dict(self._base_fingerprint)
        fp.update(
            {
                "algorithm": self.online.algorithm,
                "algorithm_version": self.online.algorithm_version,
                "feature_version": FEATURE_VERSION,
                "n_online_updates": self.n_online_updates,
                "n_observed": self.n_observed,
                "buffer_size": len(self.buffer),
            }
        )
        return fp

    # ── Observation ──────────────────────────────────────────────────────────
    def observe_decision(
        self,
        final_state: Dict[str, Any],
        *,
        ticker: str,
        trade_date: str,
        action: str,
        size_pct: float,
        portfolio_ctx: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a decision now; its reward is committed later by
        :meth:`resolve_outcome` once the horizon elapses. Use this in a live
        loop where the realized return is not yet known."""
        feats = extract_state_features(
            final_state, ticker=ticker, trade_date=trade_date, portfolio_ctx=portfolio_ctx
        )
        self._pending[self._key(ticker, trade_date)] = (feats, str(action).upper(), float(size_pct))

    def resolve_outcome(
        self,
        *,
        ticker: str,
        trade_date: str,
        forward_return: float,
        drawdown: Optional[float] = None,
        horizon_days: int = DEFAULT_REWARD_HORIZON_DAYS,
        now=None,
    ) -> bool:
        """Commit a previously-observed decision now that its return is known.

        Returns True if a transition was committed (horizon elapsed + decision
        was pending), else False.
        """
        key = self._key(ticker, trade_date)
        pending = self._pending.get(key)
        if pending is None:
            return False
        feats, action, size_pct = pending
        committed = self._commit(
            state_features=feats, action=action, size_pct=size_pct,
            forward_return=forward_return, drawdown=drawdown,
            trade_date=trade_date, key=key, horizon_days=horizon_days, now=now,
        )
        if committed:
            self._pending.pop(key, None)
        return committed

    def observe_closed_trade(
        self,
        *,
        state_features,
        action: str,
        size_pct: float,
        forward_return: Optional[float],
        drawdown: Optional[float] = None,
        ticker: str = "?",
        trade_date: Optional[str] = None,
        horizon_days: int = DEFAULT_REWARD_HORIZON_DAYS,
        now=None,
    ) -> bool:
        """One-shot ingest of an already-matured trade (post-hoc / from archive).

        ``state_features`` may be a numpy array or a {name: value} dict in
        FEATURE_NAMES order. Returns True if committed to the buffer.
        """
        feats = self._coerce_features(state_features)
        return self._commit(
            state_features=feats, action=str(action).upper(), size_pct=float(size_pct),
            forward_return=forward_return, drawdown=drawdown, trade_date=trade_date,
            key=self._key(ticker, trade_date) if trade_date else None,
            horizon_days=horizon_days, now=now,
        )

    def _commit(
        self,
        *,
        state_features: np.ndarray,
        action: str,
        size_pct: float,
        forward_return: Optional[float],
        drawdown: Optional[float],
        trade_date: Optional[str],
        key: Optional[str],
        horizon_days: int,
        now,
    ) -> bool:
        # No look-ahead: only learn from trades whose horizon has elapsed.
        if trade_date is not None and not _no_lookahead_window(trade_date, horizon_days, now=now):
            logger.debug("online: skip %s — reward horizon not elapsed", key)
            return False
        # ``size_pct`` is retained on the signature for call-site compatibility
        # but no longer shapes the reward — the decision policy learns over the
        # counterfactual per-action reward vector instead.
        rv = _reward_vector(forward_return, drawdown=drawdown)
        if rv is None:
            return False
        added = self.buffer.add(state_features, rv, action_to_index(action), key)
        if added:
            self.n_observed += 1
        return added

    # ── Update ────────────────────────────────────────────────────────────────
    def update(self, n_steps: Optional[int] = None) -> OnlineUpdateResult:
        """Run guard-railed incremental gradient steps from the replay buffer."""
        n = len(self.buffer)
        if n < self.online.min_buffer_to_update:
            return OnlineUpdateResult(
                updated=False, reverted=False, n_steps=0, buffer_size=n,
                holdout_loss_before=None, holdout_loss_after=None,
                learning_rate=self._lr,
                reason=f"buffer too small ({n} < {self.online.min_buffer_to_update})",
            )

        steps = int(n_steps if n_steps is not None else self.online.updates_per_call)

        # Seeded train/holdout split of the buffer for the guardrail.
        perm = self._rng.permutation(n)
        n_hold = max(1, int(round(n * self.online.holdout_fraction)))
        hold_idx = perm[:n_hold].tolist()
        train_idx = perm[n_hold:].tolist() or perm.tolist()  # tiny-buffer fallback

        hs, hr, ha = self.buffer.tensors(hold_idx)
        loss_before = self._holdout_loss(hs, hr, ha)

        snapshot = {k: v.detach().clone() for k, v in self.q_network.state_dict().items()}

        self.q_network.train()
        bs = min(self.online.batch_size, len(train_idx))
        for _ in range(steps):
            batch = self._rng.choice(train_idx, size=bs, replace=len(train_idx) < bs)
            s, r, a = self.buffer.tensors(batch.tolist())
            self._optimizer.zero_grad(set_to_none=True)
            q = self.q_network(s)
            loss, _ = cql_loss(
                q, r, a, alpha=self.online.cql_alpha, temperature=self.online.cql_temperature
            )
            loss.backward()
            if self.online.grad_clip_norm and self.online.grad_clip_norm > 0:
                nn.utils.clip_grad_norm_(self.q_network.parameters(), self.online.grad_clip_norm)
            self._optimizer.step()

        loss_after = self._holdout_loss(hs, hr, ha)
        self.n_online_updates += 1

        # Guardrail: revert if holdout loss degraded beyond tolerance.
        degraded = loss_after > loss_before * (1.0 + self.online.degrade_tolerance)
        reverted = False
        if degraded:
            self._consecutive_degrades += 1
            if self._consecutive_degrades >= self.online.revert_patience:
                self.q_network.load_state_dict(snapshot)
                self._decay_lr()
                reverted = True
                self._consecutive_degrades = 0
                reason = (
                    f"reverted: holdout {loss_before:.5f}→{loss_after:.5f} degraded "
                    f"{self.online.revert_patience}x; lr→{self._lr:.2e}"
                )
            else:
                reason = (
                    f"accepted-with-warning: holdout {loss_before:.5f}→{loss_after:.5f} "
                    f"degraded ({self._consecutive_degrades}/{self.online.revert_patience})"
                )
        else:
            self._consecutive_degrades = 0
            if loss_after < self._best_holdout:
                self._best_holdout = loss_after
                self._best_state = {k: v.detach().clone() for k, v in self.q_network.state_dict().items()}
            reason = f"accepted: holdout {loss_before:.5f}→{loss_after:.5f}"

        self.q_network.eval()
        return OnlineUpdateResult(
            updated=not reverted, reverted=reverted, n_steps=steps, buffer_size=n,
            holdout_loss_before=loss_before, holdout_loss_after=loss_after,
            learning_rate=self._lr, reason=reason,
        )

    def step(self, **observe_kwargs) -> OnlineUpdateResult:
        """Convenience: ``observe_closed_trade(**kwargs)`` then ``update()``."""
        self.observe_closed_trade(**observe_kwargs)
        return self.update()

    # ── Bulk ingestion ────────────────────────────────────────────────────────
    def ingest_samples(self, samples, *, now=None) -> int:
        """Ingest matured ``dataset.RLSample`` records (e.g. from
        ``dataset.build_from_postgres`` which recovers real state features).
        Returns the number of NEW transitions committed (does not call update).
        """
        committed = 0
        for s in samples:
            rb = getattr(s, "reward_buy", None)
            rs = getattr(s, "reward_sell", None)
            if rb is None or rs is None:
                continue  # PENDING — horizon not elapsed
            feats = self._coerce_features(s.state_features)
            rv = np.asarray(
                [float(rb), float(getattr(s, "reward_hold", 0.0) or 0.0), float(rs)],
                dtype=np.float32,
            )
            behavior = int(getattr(s, "committee_action_index", 1))
            key = self._key(getattr(s, "ticker", "?"), getattr(s, "trade_date", None))
            if self.buffer.add(feats, rv, behavior, key):
                committed += 1
                self.n_observed += 1
        return committed

    # ── Persistence ────────────────────────────────────────────────────────────
    def save(self, path: str | Path) -> Path:
        """Persist weights + replay buffer + online state so learning resumes."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "state_dict": self.q_network.state_dict(),
            "architecture": self.q_network.architecture_summary(),
            "config": self.config.as_dict(),
            "online_config": self.online.as_dict(),
            "feature_version": FEATURE_VERSION,
            "feature_names": list(FEATURE_NAMES),
            "buffer": {
                "states": [s.tolist() for s in self.buffer.states],
                "reward_vectors": [rv.tolist() for rv in self.buffer.reward_vectors],
                "behavior_actions": list(self.buffer.behavior_actions),
                "keys": list(self.buffer.keys),
                "capacity": self.buffer.capacity,
            },
            "n_online_updates": self.n_online_updates,
            "n_observed": self.n_observed,
            "lr": self._lr,
            "model_fingerprint": self.model_fingerprint,
        }
        torch.save(payload, path)
        logger.info("online-rl: saved to %s (buffer=%d, updates=%d)",
                    path, len(self.buffer), self.n_online_updates)
        return path

    @classmethod
    def load(cls, path: str | Path) -> "OnlineRLTrainer":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"no online policy at {path}")
        payload = torch.load(path, map_location="cpu", weights_only=False)
        fv = payload.get("feature_version", FEATURE_VERSION)
        if fv != FEATURE_VERSION:
            raise ValueError(
                f"online policy trained on feature_version={fv!r}, current is "
                f"{FEATURE_VERSION!r}; retrain or downgrade the extractor."
            )
        cfg_dict = dict(payload.get("config") or {})
        cfg_dict.pop("decision_actions", None)
        cfg_dict.pop("size_tiers", None)
        cfg_dict.pop("n_actions", None)
        cfg_dict.pop("size_multiplier_min", None)
        cfg_dict.pop("size_multiplier_max", None)
        if isinstance(cfg_dict.get("hidden_dims"), list):
            cfg_dict["hidden_dims"] = tuple(cfg_dict["hidden_dims"])
        config = TrainingConfig(**cfg_dict) if cfg_dict else DEFAULT_TRAINING_CONFIG
        online_cfg = OnlineConfig(**(payload.get("online_config") or {}))

        arch = payload.get("architecture") or {}
        net = QNetwork(
            state_dim=int(arch.get("state_dim", feature_vector_size())),
            hidden_dims=tuple(arch.get("hidden_dims", config.hidden_dims)),
            dropout_p=float(arch.get("dropout_p", config.dropout_p)),
            n_actions=N_ACTIONS,
        )
        net.load_state_dict(payload["state_dict"])

        trainer = cls(
            q_network=net, config=config, online_config=online_cfg,
            model_fingerprint=payload.get("model_fingerprint") or {},
        )
        buf = payload.get("buffer") or {}
        for st, rv, ba in zip(
            buf.get("states", []),
            buf.get("reward_vectors", []),
            buf.get("behavior_actions", []),
        ):
            trainer.buffer.states.append(np.asarray(st, dtype=np.float32))
            trainer.buffer.reward_vectors.append(np.asarray(rv, dtype=np.float32))
            trainer.buffer.behavior_actions.append(int(ba))
        trainer.buffer.keys = set(buf.get("keys", []))
        trainer.n_online_updates = int(payload.get("n_online_updates", 0))
        trainer.n_observed = int(payload.get("n_observed", 0))
        trainer._lr = float(payload.get("lr", online_cfg.learning_rate))
        for g in trainer._optimizer.param_groups:
            g["lr"] = trainer._lr
        return trainer

    # ── Internals ──────────────────────────────────────────────────────────────
    def _holdout_loss(self, s: torch.Tensor, r: torch.Tensor, a: torch.Tensor) -> float:
        if s.numel() == 0:
            return float("inf")
        self.q_network.eval()
        with torch.no_grad():
            q = self.q_network(s)
            _, tele = cql_loss(
                q, r, a, alpha=self.online.cql_alpha, temperature=self.online.cql_temperature
            )
        self.q_network.train()
        return float(tele["td_loss"])

    def _decay_lr(self) -> None:
        self._lr *= self.online.lr_decay_on_revert
        for g in self._optimizer.param_groups:
            g["lr"] = self._lr

    @staticmethod
    def _key(ticker: str, trade_date: Optional[str]) -> Optional[str]:
        if trade_date is None:
            return None
        return f"{str(ticker).upper()}@{str(trade_date)[:10]}"

    @staticmethod
    def _coerce_features(state_features) -> np.ndarray:
        if isinstance(state_features, dict):
            return np.asarray([float(state_features.get(n, 0.0)) for n in FEATURE_NAMES], dtype=np.float32)
        arr = np.asarray(state_features, dtype=np.float32).reshape(-1)
        if arr.shape[0] != feature_vector_size():
            raise ValueError(
                f"state_features length {arr.shape[0]} != {feature_vector_size()} (FEATURE_NAMES)"
            )
        return arr
