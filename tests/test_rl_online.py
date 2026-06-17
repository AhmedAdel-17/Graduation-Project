"""Tests for the online / incremental RL learner (tradingagents/rl/online.py)."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from tradingagents.rl.config import SIZE_TIERS
from tradingagents.rl.feature_extractor import FEATURE_NAMES, feature_vector_size
from tradingagents.rl.online import OnlineConfig, OnlineRLTrainer


def _elapsed_date(days_ago: int = 120) -> str:
    return (datetime.utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def _future_date(days_ahead: int = 5) -> str:
    return (datetime.utcnow() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


def _feats(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(feature_vector_size()).astype(np.float32)


def test_cold_start_constructs_with_correct_state_dim():
    t = OnlineRLTrainer.cold_start()
    assert t.q_network.state_dim == feature_vector_size()
    assert len(t.buffer) == 0
    # Inference works immediately (warm policy wrapper).
    pred = t.policy.predict({}, ticker="COMI.CA", trade_date=_elapsed_date())
    assert 0.0 <= pred.size_multiplier <= 1.0


def test_no_lookahead_skips_unmatured_trades():
    t = OnlineRLTrainer.cold_start()
    committed = t.observe_closed_trade(
        state_features=_feats(1), action="BUY", size_pct=0.5,
        forward_return=0.04, ticker="COMI.CA", trade_date=_future_date(),
    )
    assert committed is False
    assert len(t.buffer) == 0


def test_hold_and_none_return_do_not_commit():
    t = OnlineRLTrainer.cold_start()
    # HOLD gets reward 0 → still a valid transition (teaches "no exposure").
    assert t.observe_closed_trade(
        state_features=_feats(2), action="HOLD", size_pct=0.0,
        forward_return=None, ticker="X", trade_date=_elapsed_date(),
    ) is True
    # BUY with unknown return → reward None → not committed.
    assert t.observe_closed_trade(
        state_features=_feats(3), action="BUY", size_pct=0.5,
        forward_return=None, ticker="Y", trade_date=_elapsed_date(),
    ) is False


def test_buffer_dedups_by_ticker_date():
    t = OnlineRLTrainer.cold_start()
    kw = dict(state_features=_feats(4), action="BUY", size_pct=0.5,
              forward_return=0.02, ticker="COMI.CA", trade_date=_elapsed_date())
    assert t.observe_closed_trade(**kw) is True
    assert t.observe_closed_trade(**kw) is False  # same key, ignored
    assert len(t.buffer) == 1


def test_update_noop_below_min_buffer():
    t = OnlineRLTrainer.cold_start(online_config=OnlineConfig(min_buffer_to_update=16))
    t.observe_closed_trade(
        state_features=_feats(5), action="BUY", size_pct=0.5,
        forward_return=0.02, ticker="A", trade_date=_elapsed_date(),
    )
    res = t.update()
    assert res.updated is False
    assert "buffer too small" in res.reason


def test_online_updates_learn_a_size_signal():
    """With a planted signal (big size good when feature>0, bad when <0), the
    Q-network should, after online updates, prefer a larger tier on the
    positive-context state than on the negative-context one."""
    cfg = OnlineConfig(min_buffer_to_update=8, updates_per_call=40, batch_size=16,
                       learning_rate=3e-3, holdout_fraction=0.25, revert_patience=99)
    t = OnlineRLTrainer.cold_start(online_config=cfg)
    pos = np.ones(feature_vector_size(), dtype=np.float32)
    neg = -np.ones(feature_vector_size(), dtype=np.float32)
    d = _elapsed_date()
    # positive context: full size earned +return; negative context: full size lost.
    for i in range(40):
        t.observe_closed_trade(state_features=pos, action="BUY", size_pct=1.0,
                               forward_return=0.05, ticker=f"P{i}", trade_date=d)
        t.observe_closed_trade(state_features=neg, action="BUY", size_pct=1.0,
                               forward_return=-0.05, ticker=f"N{i}", trade_date=d)
    for _ in range(15):
        t.update()
    pred_pos = t.policy.predict({"__feats__": None}, ticker="P", trade_date=d)
    # Build inference directly from planted vectors via the network:
    import torch
    with torch.no_grad():
        q_pos = t.q_network(torch.from_numpy(pos).unsqueeze(0)).squeeze(0).numpy()
        q_neg = t.q_network(torch.from_numpy(neg).unsqueeze(0)).squeeze(0).numpy()
    # argmax tier index should be higher (bigger size) for the positive context.
    assert int(np.argmax(q_pos)) >= int(np.argmax(q_neg))
    assert SIZE_TIERS[int(np.argmax(q_pos))] >= SIZE_TIERS[int(np.argmax(q_neg))]
    assert t.n_online_updates >= 15


def test_guardrail_reverts_on_persistent_degradation(monkeypatch):
    cfg = OnlineConfig(min_buffer_to_update=8, updates_per_call=2, revert_patience=2)
    t = OnlineRLTrainer.cold_start(online_config=cfg)
    d = _elapsed_date()
    for i in range(20):
        t.observe_closed_trade(state_features=_feats(i), action="BUY", size_pct=0.5,
                               forward_return=0.01, ticker=f"T{i}", trade_date=d)
    # Force every update to look like a degradation: a monotonically
    # increasing counter makes loss_after > loss_before on every update() call
    # (two _holdout_loss calls per update → before < after).
    counter = {"v": 0.0}

    def _rising(*_a, **_k):
        counter["v"] += 1.0
        return counter["v"]

    monkeypatch.setattr(t, "_holdout_loss", _rising)
    r1 = t.update()   # before=1, after=2 → degrade #1 (warning)
    r2 = t.update()   # before=3, after=4 → degrade #2 → revert (patience=2)
    assert r1.reverted is False
    assert r2.reverted is True
    assert t._lr < cfg.learning_rate  # lr decayed on revert


def test_save_load_round_trip(tmp_path):
    t = OnlineRLTrainer.cold_start(online_config=OnlineConfig(min_buffer_to_update=4))
    d = _elapsed_date()
    for i in range(10):
        t.observe_closed_trade(state_features=_feats(i), action="BUY", size_pct=0.75,
                               forward_return=0.02, ticker=f"T{i}", trade_date=d)
    t.update()
    p = t.save(tmp_path / "online.pt")
    t2 = OnlineRLTrainer.load(p)
    assert len(t2.buffer) == len(t.buffer)
    assert t2.n_online_updates == t.n_online_updates
    assert t2.n_observed == t.n_observed
    # dedup keys survive → re-observing a saved trade is a no-op.
    assert t2.observe_closed_trade(
        state_features=_feats(0), action="BUY", size_pct=0.75,
        forward_return=0.02, ticker="T0", trade_date=d,
    ) is False


def test_observe_decision_then_resolve_outcome():
    t = OnlineRLTrainer.cold_start()
    d = _elapsed_date()
    t.observe_decision({}, ticker="COMI.CA", trade_date=d, action="BUY", size_pct=0.5)
    assert len(t.buffer) == 0  # pending, not yet committed
    ok = t.resolve_outcome(ticker="COMI.CA", trade_date=d, forward_return=0.03)
    assert ok is True
    assert len(t.buffer) == 1


def test_online_checkpoint_loads_into_backtester_policy(tmp_path):
    """The backtester consumes RL policies via RLSizingPolicy.load(). An online
    checkpoint MUST be loadable by that same path so a freshly online-updated
    policy can be dropped straight into `backtester.py --rl-model`."""
    from tradingagents.rl.policy import RLSizingPolicy

    t = OnlineRLTrainer.cold_start(online_config=OnlineConfig(min_buffer_to_update=4))
    d = _elapsed_date()
    for i in range(8):
        t.observe_closed_trade(state_features=_feats(i), action="BUY", size_pct=0.5,
                               forward_return=0.02, ticker=f"T{i}", trade_date=d)
    t.update()
    ckpt = t.save(tmp_path / "rl_sizing_online.pt")

    policy = RLSizingPolicy.load(ckpt)
    assert policy.is_loaded
    pred = policy.predict({}, ticker="COMI.CA", trade_date=d)
    assert 0.0 <= pred.size_multiplier <= 1.0
    assert policy.model_fingerprint.get("algorithm") == "online_cql_single_step"


def test_fingerprint_tracks_online_progress():
    t = OnlineRLTrainer.cold_start(online_config=OnlineConfig(min_buffer_to_update=4))
    d = _elapsed_date()
    for i in range(8):
        t.observe_closed_trade(state_features=_feats(i), action="BUY", size_pct=0.5,
                               forward_return=0.02, ticker=f"T{i}", trade_date=d)
    t.update()
    fp = t.model_fingerprint
    assert fp["algorithm"] == "online_cql_single_step"
    assert fp["n_online_updates"] >= 1
    assert fp["buffer_size"] == 8
