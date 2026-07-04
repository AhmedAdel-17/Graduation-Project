"""RL decision-policy safety / integration tests (parallel-arm semantics).

What's verified here:

1. **Default-off invariant** — when the feature flag is unset, behavior of
   ``execute_trade`` is byte-identical to the pre-RL implementation: the
   identity policy is loaded, ``rl_action`` is recorded as ``None`` and the
   executed share count is untouched.

2. **Fail-closed** — when the flag is set but the model file is missing or
   unreadable, the constructor logs a warning and falls back to the identity
   policy. The backtest does not crash.

3. **Advisory only (the core invariant)** — the RL decision policy runs as a
   parallel arm. Whatever it recommends (even SELL/HOLD on a BUY, or a BUY on
   a state where the committee abstained), it NEVER changes the executed share
   count. Only the committee + deterministic risk veto move capital.

4. **Risk veto still wins** — a VETO decision short-circuits the BUY before any
   RL recommendation matters. No trade is appended.

5. **Audit fields are present uniformly** — trade records and audit log entries
   always carry the RL keys (``rl_action``, ``rl_agrees_with_committee``,
   ``rl_action_index``, ``rl_committee_action``) when a policy ran.

The tests bypass the agent graph by exercising ``execute_trade`` directly.
"""

from __future__ import annotations

import os
import sys
from typing import Dict

import pytest
import torch

# Add project root so we can import ``scripts.backtester`` like the CLI does.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.backtester import BacktestingEngine
from tradingagents.rl.config import DECISION_ACTIONS, N_ACTIONS
from tradingagents.rl.feature_extractor import feature_vector_size
from tradingagents.rl.policy import QNetwork, RLDecisionPolicy


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _final_state(action: str = "BUY") -> Dict:
    """Minimal final_state that ``feature_extractor`` will accept."""
    return {
        "final_trade_decision": f"FINAL TRANSACTION PROPOSAL: {action}",
        "confidence_scores": {"overall": 0.7, "technical": 0.6, "fundamental": 0.8},
    }


def _policy_with_fixed_action(action_idx: int) -> RLDecisionPolicy:
    """Construct a deterministic policy whose argmax is the given action.

    We zero the network and set the bias of the chosen action to a large
    value, so the argmax is fixed regardless of the input.
    """
    torch.manual_seed(0)
    net = QNetwork(
        state_dim=feature_vector_size(), hidden_dims=(8, 8), dropout_p=0.0,
        n_actions=N_ACTIONS,
    )
    with torch.no_grad():
        for p in net.parameters():
            p.zero_()
        net.body[-1].bias[action_idx] = 1.0
    return RLDecisionPolicy(q_network=net)


def _make_engine_with_policy(policy: RLDecisionPolicy | None) -> BacktestingEngine:
    """Build a BacktestingEngine and patch in an injected policy."""
    eng = BacktestingEngine(initial_capital=1_000_000.0, target_market="EGX", benchmark_ticker=None)
    eng.cash = 1_000_000.0
    eng.portfolio_value = 1_000_000.0
    if policy is None:
        eng.rl_policy_enabled = False  # keep the identity policy __init__ installed
    else:
        eng.rl_policy_enabled = True
        eng.rl_policy = policy
    return eng


# ──────────────────────────────────────────────────────────────────────────────
# 1. Default-off invariant
# ──────────────────────────────────────────────────────────────────────────────


def test_flag_off_records_none_action_and_untouched_shares():
    eng = _make_engine_with_policy(None)
    assert eng.rl_policy_enabled is False
    assert not eng.rl_policy.is_loaded   # identity policy
    plan = {"position_sizing": {"target_shares": 100}}
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY", close_price=100.0,
        execution_plan=plan, confidence=1.0, reasoning="test",
        final_state=_final_state("BUY"),
    )
    assert len(eng.trade_history) == 1
    rec = eng.trade_history[0]
    assert rec["rl_meta_policy_enabled"] is False
    assert rec["rl_action"] is None
    assert rec["rl_agrees_with_committee"] is None
    assert rec["shares"] == 100   # confidence=1.0 ⇒ untouched


def test_flag_off_omits_loaded_only_audit_fields():
    eng = _make_engine_with_policy(None)
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 50}}, confidence=1.0,
        reasoning="test", final_state=_final_state("BUY"),
    )
    rec = eng.trade_history[0]
    assert "rl_action" in rec
    # The loaded-policy extras are absent when no policy ran.
    assert "rl_action_index" not in rec
    assert "rl_model_fingerprint" not in rec


# ──────────────────────────────────────────────────────────────────────────────
# 2. Fail-closed
# ──────────────────────────────────────────────────────────────────────────────


def test_constructor_falls_back_to_identity_when_model_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("RL_META_POLICY_ENABLED", "1")
    monkeypatch.setenv("RL_MODEL_PATH", str(tmp_path / "does_not_exist.pt"))
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


def test_predict_errors_are_non_fatal(monkeypatch):
    """A broken policy must not crash the trade; the arm is skipped."""
    class _BrokenPolicy(RLDecisionPolicy):
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
    assert rec["rl_action"] is None   # arm skipped on error
    assert rec["shares"] == 200       # untouched


# ──────────────────────────────────────────────────────────────────────────────
# 3. Advisory only — RL never alters the executed trade
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("action_idx", [0, 1, 2])  # BUY / HOLD / SELL
def test_rl_recommendation_never_changes_buy_shares(action_idx):
    """Whatever the policy recommends, the executed BUY size is unchanged."""
    eng = _make_engine_with_policy(_policy_with_fixed_action(action_idx))
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 200}},
        confidence=1.0, reasoning="test", final_state=_final_state("BUY"),
    )
    assert len(eng.trade_history) == 1
    rec = eng.trade_history[0]
    assert rec["shares"] == 200   # advisory arm never sizes
    assert rec["rl_action"] == DECISION_ACTIONS[action_idx]
    assert rec["rl_action_index"] == action_idx


def test_rl_agreement_flag_is_recorded():
    # Policy forced to BUY; committee says BUY ⇒ agree.
    eng = _make_engine_with_policy(_policy_with_fixed_action(0))
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 100}},
        confidence=1.0, reasoning="test", final_state=_final_state("BUY"),
    )
    rec = eng.trade_history[0]
    assert rec["rl_action"] == "BUY"
    assert rec["rl_committee_action"] == "BUY"
    assert rec["rl_agrees_with_committee"] is True


def test_rl_disagreement_flag_is_recorded():
    # Policy forced to SELL; committee says BUY ⇒ disagree, but BUY still executes.
    eng = _make_engine_with_policy(_policy_with_fixed_action(2))
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 100}},
        confidence=1.0, reasoning="test", final_state=_final_state("BUY"),
    )
    rec = eng.trade_history[0]
    assert rec["shares"] == 100
    assert rec["rl_action"] == "SELL"
    assert rec["rl_agrees_with_committee"] is False


# ──────────────────────────────────────────────────────────────────────────────
# 4. Risk veto still wins
# ──────────────────────────────────────────────────────────────────────────────


def test_decision_containing_veto_blocks_buy_even_under_rl():
    eng = _make_engine_with_policy(_policy_with_fixed_action(0))   # would say BUY
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY (VETO)", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 100}},
        confidence=1.0, reasoning="risk veto", final_state=_final_state("BUY"),
    )
    assert eng.trade_history == []


def test_sell_path_unaffected_by_rl():
    """SELLs always liquidate the whole position; the RL arm is advisory."""
    eng = _make_engine_with_policy(_policy_with_fixed_action(0))   # would say BUY
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
    assert "rl_action" in rec


def test_hold_path_records_no_trade_but_keeps_policy_state():
    eng = _make_engine_with_policy(_policy_with_fixed_action(1))   # HOLD
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="HOLD", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 100}},
        confidence=1.0, reasoning="test", final_state=_final_state("HOLD"),
    )
    assert eng.trade_history == []
    assert hasattr(eng, "_last_rl_prediction")
    assert eng._last_rl_prediction is not None


# ──────────────────────────────────────────────────────────────────────────────
# 5. Audit field plumbing
# ──────────────────────────────────────────────────────────────────────────────


def test_trade_record_has_fingerprint_and_feature_version_when_policy_loaded():
    eng = _make_engine_with_policy(_policy_with_fixed_action(0))
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 100}},
        confidence=1.0, reasoning="test", final_state=_final_state("BUY"),
    )
    rec = eng.trade_history[0]
    assert rec["rl_meta_policy_enabled"] is True
    assert rec["rl_action_index"] == 0
    assert rec["rl_feature_version"].startswith("rl_state_v")
    assert isinstance(rec["rl_model_fingerprint"], str)
    assert len(rec["rl_model_fingerprint"]) == 16


def test_final_state_none_keeps_arm_inactive():
    """If the outer loop forgets to pass final_state, the engine must not crash."""
    eng = _make_engine_with_policy(_policy_with_fixed_action(2))   # would say SELL
    eng.execute_trade(
        date="2024-06-01", ticker="COMI.CA", decision="BUY", close_price=100.0,
        execution_plan={"position_sizing": {"target_shares": 100}},
        confidence=1.0, reasoning="test", final_state=None,
    )
    rec = eng.trade_history[0]
    # final_state is not a dict ⇒ the arm is skipped; the BUY is untouched.
    assert rec["rl_action"] is None
    assert rec["shares"] == 100
