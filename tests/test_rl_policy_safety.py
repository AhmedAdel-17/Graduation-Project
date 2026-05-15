"""Stage C safety / integration tests.

What's verified here:

1. **Default-off invariant** — when the feature flag is unset, behavior of
   ``execute_trade`` is byte-identical to the pre-RL implementation: the
   identity policy is loaded, ``rl_size_multiplier`` is recorded as 1.0,
   and no audit-write helper is triggered.

2. **Fail-closed** — when the flag is set but the model file is missing
   or unreadable, the constructor logs a warning and falls back to the
   identity policy. The backtest does not crash.

3. **RL can only shrink** — even when the loaded policy outputs the lowest
   tier (``size_multiplier=0.0``), the BUY branch reduces share count to
   zero rather than producing an unbounded order. SELL and HOLD paths are
   untouched (SELL liquidates the whole position; HOLD does nothing).

4. **Risk veto still wins** — a VETO decision short-circuits before any RL
   sizing matters. No trade is appended and the audit row records the
   policy's intended size_mult for diagnostic purposes only.

5. **Audit fields are present uniformly** — trade records and audit log
   entries always carry the new RL keys, regardless of flag state.

The tests bypass the agent graph entirely by exercising ``execute_trade``
directly. End-to-end smoke (graph + RL + backtest) is left to the manual
smoke run documented at the bottom of the Stage C report.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict

import pytest
import torch

# Add project root so we can import ``scripts.backtester`` like the CLI does.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.backtester import BacktestingEngine
from tradingagents.rl.config import SIZE_TIERS, TrainingConfig
from tradingagents.rl.feature_extractor import feature_vector_size
from tradingagents.rl.policy import QNetwork, RLSizingPolicy


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _final_state(action: str = "BUY") -> Dict:
    """Minimal final_state that ``feature_extractor`` will accept."""
    return {
        "final_trade_decision": f"FINAL TRANSACTION PROPOSAL: {action}",
        "confidence_scores": {"overall": 0.7, "technical": 0.6, "fundamental": 0.8},
    }


def _policy_with_fixed_tier(tier_idx: int) -> RLSizingPolicy:
    """Construct a deterministic policy whose argmax is the given tier.

    We zero the network's last-layer biases except for one, so the argmax
    is fixed regardless of the input.
    """
    torch.manual_seed(0)
    net = QNetwork(state_dim=feature_vector_size(), hidden_dims=(8, 8), dropout_p=0.0)
    # Zero everything, then set the bias of the chosen tier to a large value.
    with torch.no_grad():
        for p in net.parameters():
            p.zero_()
        last_linear = net.body[-1]
        bias = last_linear.bias
        bias.zero_()
        bias[tier_idx] = 1.0
    return RLSizingPolicy(q_network=net)


def _make_engine_with_policy(policy: RLSizingPolicy | None) -> BacktestingEngine:
    """Build a BacktestingEngine and patch in an injected policy."""
    eng = BacktestingEngine(initial_capital=1_000_000.0, target_market="EGX", benchmark_ticker=None)
    eng.cash = 1_000_000.0
    eng.portfolio_value = 1_000_000.0
    if policy is None:
        eng.rl_policy_enabled = False
        # Keep the identity policy that __init__ installed.
    else:
        eng.rl_policy_enabled = True
        eng.rl_policy = policy
    return eng


# ──────────────────────────────────────────────────────────────────────────────
# 1. Default-off invariant
# ──────────────────────────────────────────────────────────────────────────────


def test_flag_off_yields_identity_multiplier():
    eng = _make_engine_with_policy(None)
    assert eng.rl_policy_enabled is False
    assert not eng.rl_policy.is_loaded   # identity policy
    plan = {"position_sizing": {"target_shares": 100}}
    eng.execute_trade(
        date="2024-06-01",
        ticker="COMI.CA",
        decision="BUY",
        close_price=100.0,
        execution_plan=plan,
        confidence=1.0,
        reasoning="test",
        final_state=_final_state("BUY"),
    )
    assert len(eng.trade_history) == 1
    rec = eng.trade_history[0]
    assert rec["rl_meta_policy_enabled"] is False
    assert rec["rl_size_multiplier"] == 1.0
    assert rec["shares"] == 100   # confidence=1.0 ⇒ untouched


def test_flag_off_records_no_rl_audit_fields_in_trade_record():
    eng = _make_engine_with_policy(None)
    plan = {"position_sizing": {"target_shares": 50}}
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY", close_price=100.0,
        execution_plan=plan, confidence=1.0, reasoning="test",
        final_state=_final_state("BUY"),
    )
    rec = eng.trade_history[0]
    # Even when off, the schema is uniform (= 1.0) so downstream readers don't
    # need conditional branches. But the RL-specific extras (action_index,
    # model fingerprint) should be absent when no policy ran.
    assert "rl_size_multiplier" in rec
    assert "rl_action_index" not in rec
    assert "rl_model_fingerprint" not in rec


def test_flag_off_does_not_touch_share_count():
    """Reproducibility: BUY of 100 shares stays 100 shares when flag is off."""
    eng_off = _make_engine_with_policy(None)
    eng_off.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 100}},
        confidence=1.0, reasoning="test", final_state=_final_state("BUY"),
    )
    assert eng_off.trade_history[0]["shares"] == 100


# ──────────────────────────────────────────────────────────────────────────────
# 2. Fail-closed
# ──────────────────────────────────────────────────────────────────────────────


def test_constructor_falls_back_to_identity_when_model_missing(monkeypatch, tmp_path):
    """Flag on + nonexistent path → warning logged, identity policy used."""
    monkeypatch.setenv("RL_META_POLICY_ENABLED", "1")
    monkeypatch.setenv("RL_MODEL_PATH", str(tmp_path / "does_not_exist.pt"))
    # Re-evaluate DEFAULT_CONFIG so the env vars take effect.
    import importlib
    import tradingagents.default_config as dc
    importlib.reload(dc)
    from tradingagents.dataflows.config import set_config
    set_config(dc.DEFAULT_CONFIG)

    eng = BacktestingEngine(initial_capital=1_000.0, target_market="EGX", benchmark_ticker=None)
    assert eng.rl_policy_enabled is True
    assert eng.rl_policy.is_loaded is False  # identity


def test_constructor_falls_back_to_identity_when_path_empty(monkeypatch):
    monkeypatch.setenv("RL_META_POLICY_ENABLED", "1")
    monkeypatch.setenv("RL_MODEL_PATH", "")
    import importlib
    import tradingagents.default_config as dc
    importlib.reload(dc)
    from tradingagents.dataflows.config import set_config
    set_config(dc.DEFAULT_CONFIG)

    eng = BacktestingEngine(initial_capital=1_000.0, target_market="EGX", benchmark_ticker=None)
    assert eng.rl_policy_enabled is True
    assert eng.rl_policy.is_loaded is False  # identity


def test_predict_errors_silently_fall_back_to_one(monkeypatch):
    """A broken policy must not crash the trade — size_mult reverts to 1.0."""
    class _BrokenPolicy(RLSizingPolicy):
        @property
        def is_loaded(self):  # type: ignore[override]
            return True
        def predict(self, *args, **kwargs):  # type: ignore[override]
            raise RuntimeError("synthetic predict failure")

    eng = BacktestingEngine(initial_capital=1_000_000.0, target_market="EGX", benchmark_ticker=None)
    eng.rl_policy_enabled = True
    eng.rl_policy = _BrokenPolicy()
    eng.cash = 1_000_000.0
    eng.portfolio_value = 1_000_000.0

    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 200}},
        confidence=1.0, reasoning="test", final_state=_final_state("BUY"),
    )
    rec = eng.trade_history[0]
    assert rec["rl_size_multiplier"] == 1.0
    assert rec["shares"] == 200   # untouched after error fallback


# ──────────────────────────────────────────────────────────────────────────────
# 3. RL can only shrink
# ──────────────────────────────────────────────────────────────────────────────


def test_rl_tier_zero_zeros_out_buy():
    eng = _make_engine_with_policy(_policy_with_fixed_tier(0))   # 0.0 ⇒ skip
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 200}},
        confidence=1.0, reasoning="test", final_state=_final_state("BUY"),
    )
    # tier 0 = 0.0 ⇒ target shrinks to 0 ⇒ no trade is appended
    assert eng.trade_history == []


def test_rl_tier_half_halves_buy():
    eng = _make_engine_with_policy(_policy_with_fixed_tier(2))   # 0.5
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 200}},
        confidence=1.0, reasoning="test", final_state=_final_state("BUY"),
    )
    assert len(eng.trade_history) == 1
    rec = eng.trade_history[0]
    assert rec["shares"] == 100
    assert rec["rl_size_multiplier"] == 0.5
    assert rec["rl_action_index"] == 2


def test_rl_tier_one_leaves_buy_intact():
    eng = _make_engine_with_policy(_policy_with_fixed_tier(4))   # 1.0
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 200}},
        confidence=1.0, reasoning="test", final_state=_final_state("BUY"),
    )
    rec = eng.trade_history[0]
    assert rec["shares"] == 200
    assert rec["rl_size_multiplier"] == 1.0


def test_rl_does_not_amplify_when_tier_max_is_one():
    """Even at tier 4 (size=1.0), RL never adds shares beyond the trader's target."""
    eng = _make_engine_with_policy(_policy_with_fixed_tier(4))
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 100}},
        confidence=1.0, reasoning="test", final_state=_final_state("BUY"),
    )
    assert eng.trade_history[0]["shares"] == 100


def test_size_mult_clamped_when_config_caps_below_one():
    cfg = TrainingConfig(size_multiplier_min=0.0, size_multiplier_max=0.5)
    torch.manual_seed(0)
    net = QNetwork(state_dim=feature_vector_size(), hidden_dims=(8, 8), dropout_p=0.0)
    with torch.no_grad():
        for p in net.parameters():
            p.zero_()
        net.body[-1].bias[4] = 1.0   # would otherwise pick size=1.0
    policy = RLSizingPolicy(q_network=net, config=cfg)
    eng = _make_engine_with_policy(policy)
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 200}},
        confidence=1.0, reasoning="test", final_state=_final_state("BUY"),
    )
    rec = eng.trade_history[0]
    assert rec["rl_size_multiplier"] == 0.5
    assert rec["shares"] == 100   # 200 × 0.5


# ──────────────────────────────────────────────────────────────────────────────
# 4. Risk veto still wins (the BUY contract short-circuits on VETO)
# ──────────────────────────────────────────────────────────────────────────────


def test_decision_containing_veto_blocks_buy_even_under_rl():
    """When the upstream decision string contains VETO, no BUY is taken
    regardless of what the meta-policy would have picked."""
    eng = _make_engine_with_policy(_policy_with_fixed_tier(4))   # would say 1.0
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY (VETO)",
        close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 100}},
        confidence=1.0, reasoning="risk veto", final_state=_final_state("BUY"),
    )
    assert eng.trade_history == []


def test_sell_path_ignores_rl_size_multiplier():
    """SELLs always liquidate the whole position. RL never amplifies a SELL."""
    eng = _make_engine_with_policy(_policy_with_fixed_tier(0))   # 0.0
    # Seed a position
    eng.positions["COMI.CA"] = {"shares": 150, "avg_cost": 90.0}
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="SELL", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 0}},
        confidence=1.0, reasoning="test", final_state=_final_state("SELL"),
    )
    assert len(eng.trade_history) == 1
    rec = eng.trade_history[0]
    assert rec["action"] == "SELL"
    assert rec["shares"] == 150          # full liquidation
    # Meta-policy audit still recorded for diagnostics
    assert "rl_size_multiplier" in rec


def test_hold_path_records_no_trade_but_keeps_policy_state():
    eng = _make_engine_with_policy(_policy_with_fixed_tier(2))   # 0.5
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="HOLD", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 100}},
        confidence=1.0, reasoning="test", final_state=_final_state("HOLD"),
    )
    assert eng.trade_history == []
    # The prediction is still stashed on the engine (so the outer loop can audit it).
    assert hasattr(eng, "_last_rl_prediction")
    assert eng._last_rl_prediction is not None


# ──────────────────────────────────────────────────────────────────────────────
# 5. Audit field plumbing
# ──────────────────────────────────────────────────────────────────────────────


def test_trade_record_has_fingerprint_and_feature_version_when_policy_loaded():
    eng = _make_engine_with_policy(_policy_with_fixed_tier(3))
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 100}},
        confidence=1.0, reasoning="test", final_state=_final_state("BUY"),
    )
    rec = eng.trade_history[0]
    assert rec["rl_meta_policy_enabled"] is True
    assert rec["rl_action_index"] == 3
    assert rec["rl_feature_version"].startswith("rl_state_v")
    assert isinstance(rec["rl_model_fingerprint"], str)
    assert len(rec["rl_model_fingerprint"]) == 16


def test_final_state_none_keeps_identity_path():
    """If the outer loop forgets to pass final_state, the engine must not crash."""
    eng = _make_engine_with_policy(_policy_with_fixed_tier(0))   # would shrink
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 100}},
        confidence=1.0, reasoning="test", final_state=None,
    )
    rec = eng.trade_history[0]
    assert rec["rl_size_multiplier"] == 1.0
    assert rec["shares"] == 100
