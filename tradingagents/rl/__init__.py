"""Offline RL decision-calibration policy for the EGX multi-agent trader.

At a glance: a small Conservative Q-Learning policy that consumes the
structured outputs of the existing LangGraph and returns an independent
BUY/HOLD/SELL opinion learned from the realized outcomes of past decisions.
It is trained offline on a counterfactual per-action reward and runs as a
parallel decision arm — it never sizes a position and never overrides the
deterministic EGX risk veto. See ``docs/RL_METHODOLOGY.md``.
"""

from tradingagents.rl.config import (
    DECISION_ACTIONS,
    N_ACTIONS,
    TrainingConfig,
)
from tradingagents.rl.feature_extractor import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    extract_state_features,
    extract_state_features_dict,
    feature_vector_size,
)
from tradingagents.rl.policy import (
    PolicyPrediction,
    QNetwork,
    RLDecisionPolicy,
    RLSizingPolicy,
    identity_policy,
)
from tradingagents.rl.online import (
    DEFAULT_ONLINE_CONFIG,
    OnlineConfig,
    OnlineRLTrainer,
    OnlineUpdateResult,
)

__all__ = [
    "FEATURE_NAMES",
    "FEATURE_VERSION",
    "extract_state_features",
    "extract_state_features_dict",
    "feature_vector_size",
    # Action space / config
    "DECISION_ACTIONS",
    "N_ACTIONS",
    "TrainingConfig",
    # Policy
    "RLDecisionPolicy",
    "RLSizingPolicy",
    "PolicyPrediction",
    "QNetwork",
    "identity_policy",
    # Online / incremental RL
    "OnlineRLTrainer",
    "OnlineConfig",
    "OnlineUpdateResult",
    "DEFAULT_ONLINE_CONFIG",
]
