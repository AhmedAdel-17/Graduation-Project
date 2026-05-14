"""Tests for tradingagents.rl.eval — off-policy evaluation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import torch

from tradingagents.rl.config import N_ACTIONS, SIZE_TIERS
from tradingagents.rl.eval import (
    OPEResult,
    compare_to_baseline,
    evaluate,
    write_report,
)
from tradingagents.rl.feature_extractor import FEATURE_NAMES, feature_vector_size
from tradingagents.rl.policy import QNetwork, RLSizingPolicy, identity_policy
from tradingagents.rl.train import TrainingTensors


def _make_tensors(n: int = 20) -> TrainingTensors:
    torch.manual_seed(0)
    return TrainingTensors(
        states=torch.randn(n, feature_vector_size()),
        actions=torch.randint(0, N_ACTIONS, (n,)),
        rewards=torch.rand(n) * 0.2 - 0.1,
        behavior_size_pct=torch.rand(n),
        tickers=["COMI.CA"] * n,
        dates=[f"2024-06-{(i % 28) + 1:02d}" for i in range(n)],
    )


def _make_policy(seed: int = 0) -> RLSizingPolicy:
    torch.manual_seed(seed)
    net = QNetwork(state_dim=feature_vector_size(), hidden_dims=(16, 16), dropout_p=0.0)
    return RLSizingPolicy(q_network=net)


def test_evaluate_empty_validation_returns_zero_result():
    empty = TrainingTensors(
        states=torch.empty(0, feature_vector_size()),
        actions=torch.empty(0, dtype=torch.int64),
        rewards=torch.empty(0),
        behavior_size_pct=torch.empty(0),
        tickers=[],
        dates=[],
    )
    p = _make_policy()
    result = evaluate(p, empty)
    assert result.n_samples == 0
    assert result.direct_value == 0.0
    assert "empty validation set" in result.notes


def test_evaluate_returns_well_formed_result():
    val = _make_tensors(30)
    p = _make_policy()
    result = evaluate(p, val)
    assert result.n_samples == 30
    assert isinstance(result.direct_value, float)
    assert isinstance(result.snips_value, float)
    assert isinstance(result.behavior_mean_reward, float)
    assert math.isfinite(result.direct_value)
    assert math.isfinite(result.snips_value)
    assert math.isfinite(result.snips_effective_sample_size)
    assert result.snips_effective_sample_size >= 0
    # Action distributions sum to 1 (up to floating tolerance)
    pi_total = sum(result.action_distribution.values())
    beta_total = sum(result.behavior_action_distribution.values())
    assert pytest.approx(pi_total, abs=1e-5) == 1.0
    assert pytest.approx(beta_total, abs=1e-5) == 1.0


def test_evaluate_warns_on_small_validation_set():
    val = _make_tensors(8)
    p = _make_policy()
    result = evaluate(p, val)
    assert any("only" in n for n in result.notes)


def test_evaluate_rejects_unloaded_policy():
    val = _make_tensors(5)
    with pytest.raises(RuntimeError, match="unloaded"):
        evaluate(identity_policy(), val)


def test_compare_to_baseline_flags_uplift():
    result = OPEResult(
        n_samples=50,
        direct_value=0.10,
        behavior_mean_reward=0.01,
        snips_value=0.08,
        snips_effective_sample_size=20.0,
        action_distribution={},
        behavior_action_distribution={},
    )
    summary = compare_to_baseline(result)
    assert summary["preliminary_ok"] is True
    assert summary["snips_ok"] is True
    assert summary["direct_uplift_vs_behavior"] == pytest.approx(0.09, abs=1e-9)


def test_compare_to_baseline_flags_no_uplift():
    result = OPEResult(
        n_samples=50,
        direct_value=-0.02,
        behavior_mean_reward=0.01,
        snips_value=-0.05,
        snips_effective_sample_size=15.0,
        action_distribution={},
        behavior_action_distribution={},
    )
    summary = compare_to_baseline(result)
    assert summary["preliminary_ok"] is False
    assert summary["snips_ok"] is False


def test_write_report_round_trip(tmp_path: Path):
    val = _make_tensors(25)
    p = _make_policy()
    result = evaluate(p, val)
    out = tmp_path / "ope.json"
    written = write_report(result, out)
    assert written.exists()
    import json
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert "ope_result" in payload and "summary" in payload
    assert payload["ope_result"]["n_samples"] == 25


# Move imports needed only by tests
import math
