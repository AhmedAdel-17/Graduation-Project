"""Tests for tradingagents.rl.policy.

Coverage:
- QNetwork: shape contract, deterministic forward at eval
- RLSizingPolicy: identity behavior when no model loaded, predict shape,
  size_multiplier always in [0, 1], clamping enforced
- save/load round-trip preserves the prediction
- Refuses to load a model trained against a different feature version
- model_fingerprint is stable across save/load
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import torch

from tradingagents.rl.config import N_ACTIONS, SIZE_TIERS, TrainingConfig
from tradingagents.rl.feature_extractor import FEATURE_VERSION, feature_vector_size
from tradingagents.rl.policy import (
    QNetwork,
    RLSizingPolicy,
    identity_policy,
)


# ──────────────────────────────────────────────────────────────────────────────
# QNetwork
# ──────────────────────────────────────────────────────────────────────────────


def test_qnetwork_output_shape():
    net = QNetwork(state_dim=feature_vector_size(), hidden_dims=(32, 32), n_actions=N_ACTIONS)
    net.eval()
    x = torch.randn(7, feature_vector_size())
    with torch.no_grad():
        q = net(x)
    assert q.shape == (7, N_ACTIONS)


def test_qnetwork_deterministic_at_eval():
    net = QNetwork(state_dim=feature_vector_size(), hidden_dims=(16, 16), dropout_p=0.5)
    net.eval()
    x = torch.randn(3, feature_vector_size())
    with torch.no_grad():
        a = net(x).clone()
        b = net(x).clone()
    torch.testing.assert_close(a, b)


def test_qnetwork_architecture_summary_has_state_dim():
    net = QNetwork(state_dim=feature_vector_size(), hidden_dims=(8, 8))
    summary = net.architecture_summary()
    assert summary["state_dim"] == feature_vector_size()
    assert summary["n_actions"] == N_ACTIONS
    assert summary["n_parameters"] > 0


# ──────────────────────────────────────────────────────────────────────────────
# Identity / fail-closed policy
# ──────────────────────────────────────────────────────────────────────────────


def test_identity_policy_returns_one():
    p = identity_policy()
    assert not p.is_loaded
    pred = p.predict({}, ticker="COMI.CA", trade_date="2024-06-01")
    assert pred.size_multiplier == 1.0
    assert pred.action_index == N_ACTIONS - 1
    assert pred.feature_version == FEATURE_VERSION
    # fingerprint exists even without a model — useful for audit
    assert "feature_version" in pred.model_fingerprint


def test_unloaded_policy_does_not_crash_on_malformed_state():
    p = identity_policy()
    p.predict("not a dict", ticker="COMI.CA", trade_date="2024-06-01")  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────────
# Loaded policy: shape + bounds
# ──────────────────────────────────────────────────────────────────────────────


def _make_loaded_policy(seed: int = 0) -> RLSizingPolicy:
    torch.manual_seed(seed)
    net = QNetwork(state_dim=feature_vector_size(), hidden_dims=(16, 16), dropout_p=0.0)
    return RLSizingPolicy(q_network=net)


def test_predict_returns_size_in_zero_to_one():
    p = _make_loaded_policy()
    pred = p.predict({}, ticker="COMI.CA", trade_date="2024-06-01")
    assert 0.0 <= pred.size_multiplier <= 1.0
    assert pred.action_index in range(N_ACTIONS)
    assert len(pred.q_values) == N_ACTIONS


def test_predict_size_always_in_tier_set():
    p = _make_loaded_policy()
    pred = p.predict({}, ticker="COMI.CA", trade_date="2024-06-01")
    assert pred.size_multiplier in SIZE_TIERS


def test_predict_action_index_matches_size_multiplier():
    p = _make_loaded_policy()
    pred = p.predict({}, ticker="COMI.CA", trade_date="2024-06-01")
    assert pred.size_multiplier == SIZE_TIERS[pred.action_index]


def test_policy_refuses_mismatched_state_dim():
    bad_net = QNetwork(state_dim=feature_vector_size() + 5, hidden_dims=(8, 8))
    with pytest.raises(ValueError, match="state_dim"):
        RLSizingPolicy(q_network=bad_net)


def test_clamp_overrides_size_tier_max():
    """Even if a tier > size_multiplier_max is selected, clamping wins.

    We can't construct a tier > 1.0 by default, but if a future config
    lifted size_multiplier_max < 1.0 the clamp must still bite.
    """
    torch.manual_seed(0)
    net = QNetwork(state_dim=feature_vector_size(), hidden_dims=(8, 8), dropout_p=0.0)
    cfg = TrainingConfig(size_multiplier_min=0.0, size_multiplier_max=0.5)
    p = RLSizingPolicy(q_network=net, config=cfg)
    pred = p.predict({}, ticker="COMI.CA", trade_date="2024-06-01")
    assert pred.size_multiplier <= 0.5


# ──────────────────────────────────────────────────────────────────────────────
# Save / load
# ──────────────────────────────────────────────────────────────────────────────


def test_save_then_load_preserves_prediction(tmp_path: Path):
    original = _make_loaded_policy(seed=7)
    state = {"final_trade_decision": "BUY"}
    before = original.predict(state, ticker="COMI.CA", trade_date="2024-06-01")

    out = tmp_path / "rl_meta.pt"
    original.save(out, extra_metadata={"smoke": True})

    loaded = RLSizingPolicy.load(out)
    after = loaded.predict(state, ticker="COMI.CA", trade_date="2024-06-01")

    assert before.size_multiplier == after.size_multiplier
    assert before.action_index == after.action_index
    assert before.feature_version == after.feature_version
    np.testing.assert_allclose(before.q_values, after.q_values, atol=1e-6)


def test_save_writes_card_sidecar(tmp_path: Path):
    p = _make_loaded_policy()
    out = tmp_path / "rl_meta.pt"
    p.save(out, extra_metadata={"author": "test"})
    card = out.with_suffix(out.suffix + ".card.json")
    assert card.exists()
    payload = json.loads(card.read_text(encoding="utf-8"))
    # Card carries metadata but never weights
    assert "config" in payload and "architecture" in payload
    assert "state_dict" not in payload
    assert payload["feature_version"] == FEATURE_VERSION
    assert payload["extra_metadata"]["author"] == "test"


def test_load_rejects_mismatched_feature_version(tmp_path: Path, monkeypatch):
    p = _make_loaded_policy()
    out = tmp_path / "rl_meta.pt"
    p.save(out)
    # Tamper with the saved feature_version to force a mismatch
    payload = torch.load(out, map_location="cpu", weights_only=False)
    payload["feature_version"] = "rl_state_v999"
    torch.save(payload, out)
    with pytest.raises(ValueError, match="feature_version"):
        RLSizingPolicy.load(out)


def test_load_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        RLSizingPolicy.load(tmp_path / "does_not_exist.pt")


def test_model_fingerprint_changes_when_weights_change(tmp_path: Path):
    p1 = _make_loaded_policy(seed=1)
    p2 = _make_loaded_policy(seed=2)
    fp1 = p1.model_fingerprint
    fp2 = p2.model_fingerprint
    assert fp1["weights_sha256_16"] != fp2["weights_sha256_16"]


def test_model_fingerprint_stable_across_save_load(tmp_path: Path):
    p = _make_loaded_policy(seed=11)
    before = p.model_fingerprint["weights_sha256_16"]
    out = tmp_path / "rl_meta.pt"
    p.save(out)
    loaded = RLSizingPolicy.load(out)
    after = loaded.model_fingerprint["weights_sha256_16"]
    assert before == after
