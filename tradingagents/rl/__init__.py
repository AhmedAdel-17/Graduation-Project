"""Offline RL meta-policy package for the EGX multi-agent trader.

See ``agent_docs/rl_meta_policy.md`` (Phase 4) for the architecture story.
At a glance: a small Conservative Q-Learning policy that consumes the
structured outputs of the existing LangGraph and returns a size multiplier
in ``[0.0, 1.0]``. The directional BUY/SELL/HOLD call and the deterministic
risk veto remain unchanged.

Stage A (this PR) ships only the feature-extraction + offline-dataset
building blocks. Training, the inference policy class, and the integration
into ``scripts/backtester.py`` arrive in Stages B and C.
"""

from tradingagents.rl.feature_extractor import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    extract_state_features,
    extract_state_features_dict,
    feature_vector_size,
)

__all__ = [
    "FEATURE_NAMES",
    "FEATURE_VERSION",
    "extract_state_features",
    "extract_state_features_dict",
    "feature_vector_size",
]
