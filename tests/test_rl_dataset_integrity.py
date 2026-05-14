"""Tests for tradingagents.rl.dataset.

Covers:
- shaped_reward: HOLD=0, BUY/SELL sign convention, transaction-cost & dd penalties,
  clipping, None-handling for unknown forward returns
- Look-ahead guard: refuses to compute reward when the horizon hasn't elapsed
- JSON-report ingestion: round-trips through trade rows, fills PENDING when stale
- Parquet/CSV serialization round-trip
- assert_no_lookahead enforces invariant
- summarize counts are correct
"""

from __future__ import annotations

import json
import math
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from tradingagents.rl.dataset import (
    DATASET_SCHEMA_VERSION,
    DEFAULT_REWARD_HORIZON_DAYS,
    DEFAULT_TX_COST_PCT,
    SIZE_TIERS,
    RLSample,
    assert_no_lookahead,
    build_from_json_reports,
    samples_to_dataframe,
    shaped_reward,
    summarize,
    write_parquet,
    _size_pct_to_tier,
)
from tradingagents.rl.feature_extractor import FEATURE_NAMES, FEATURE_VERSION


# ──────────────────────────────────────────────────────────────────────────────
# shaped_reward
# ──────────────────────────────────────────────────────────────────────────────


def test_hold_reward_always_zero():
    assert shaped_reward(forward_return=0.10, action="HOLD", size_pct=1.0) == 0.0
    assert shaped_reward(forward_return=-0.10, action="HOLD", size_pct=1.0) == 0.0
    # Even when forward_return is unknown
    assert shaped_reward(forward_return=None, action="HOLD", size_pct=1.0) == 0.0


def test_pending_horizon_returns_none_for_non_hold():
    assert shaped_reward(forward_return=None, action="BUY", size_pct=1.0) is None
    assert shaped_reward(forward_return=None, action="SELL", size_pct=1.0) is None


def test_buy_positive_return_positive_reward():
    r = shaped_reward(
        forward_return=0.05, action="BUY", size_pct=1.0,
        transaction_cost_pct=0.0,
    )
    assert r is not None and r > 0


def test_sell_positive_return_negative_reward():
    """A SELL when price went UP should be penalized (we missed the move)."""
    r = shaped_reward(
        forward_return=0.05, action="SELL", size_pct=1.0,
        transaction_cost_pct=0.0,
    )
    assert r is not None and r < 0


def test_sell_negative_return_positive_reward():
    """A SELL when price went DOWN avoided a loss — positive reward."""
    r = shaped_reward(
        forward_return=-0.05, action="SELL", size_pct=1.0,
        transaction_cost_pct=0.0,
    )
    assert r is not None and r > 0


def test_transaction_cost_subtracts_for_non_hold():
    r_no_cost = shaped_reward(
        forward_return=0.05, action="BUY", size_pct=1.0, transaction_cost_pct=0.0
    )
    r_with_cost = shaped_reward(
        forward_return=0.05, action="BUY", size_pct=1.0, transaction_cost_pct=0.005
    )
    assert r_no_cost is not None and r_with_cost is not None
    assert pytest.approx(r_no_cost - r_with_cost, abs=1e-6) == 0.005


def test_drawdown_penalty_applied_when_threshold_exceeded():
    r_low_dd = shaped_reward(
        forward_return=0.05, action="BUY", size_pct=1.0,
        transaction_cost_pct=0.0, drawdown_during_holding=0.02, lambda_dd=0.5,
    )
    r_high_dd = shaped_reward(
        forward_return=0.05, action="BUY", size_pct=1.0,
        transaction_cost_pct=0.0, drawdown_during_holding=0.10, lambda_dd=0.5,
        dd_threshold=0.05,
    )
    assert r_low_dd is not None and r_high_dd is not None
    assert r_low_dd > r_high_dd
    expected_delta = 0.5 * (0.10 - 0.05)
    assert pytest.approx(r_low_dd - r_high_dd, abs=1e-6) == expected_delta


def test_reward_clipped_both_sides():
    r_huge = shaped_reward(
        forward_return=2.0, action="BUY", size_pct=1.0,
        transaction_cost_pct=0.0, clip=0.5,
    )
    assert r_huge == 0.5
    r_tiny = shaped_reward(
        forward_return=-0.5, action="BUY", size_pct=1.0,
        transaction_cost_pct=0.0, clip=0.5,
    )
    assert r_tiny == -0.5


def test_size_zero_returns_only_cost():
    """When the meta-policy chooses 0 size on a non-HOLD direction, we still
    pay transaction cost (the LLM said BUY, we sized down to nothing — that
    is itself a missed-opportunity decision; cost models reality)."""
    r = shaped_reward(
        forward_return=0.05, action="BUY", size_pct=0.0,
        transaction_cost_pct=0.005,
    )
    # log(1 + 0) - 0 - 0.005 = -0.005
    assert pytest.approx(r, abs=1e-6) == -0.005


def test_size_tier_quantization():
    assert _size_pct_to_tier(0.0) == 0
    assert _size_pct_to_tier(0.12) == 0    # closer to 0 than 0.25
    assert _size_pct_to_tier(0.20) == 1    # nearest is 0.25
    assert _size_pct_to_tier(0.50) == 2
    assert _size_pct_to_tier(0.99) == 4
    assert _size_pct_to_tier(1.5) == 4     # clipped


def test_size_tiers_contract():
    assert SIZE_TIERS == (0.0, 0.25, 0.5, 0.75, 1.0)


# ──────────────────────────────────────────────────────────────────────────────
# Look-ahead guard
# ──────────────────────────────────────────────────────────────────────────────


def _sample(
    *,
    ticker="COMI.CA",
    trade_date="2024-06-01",
    action="BUY",
    horizon=20,
    reward=0.01,
    trade_result="WIN",
    fwd_20d=0.05,
    source="test",
):
    feats = {name: 0.0 for name in FEATURE_NAMES}
    return RLSample(
        session_id=None,
        ticker=ticker,
        trade_date=trade_date,
        feature_version=FEATURE_VERSION,
        state_features=feats,
        llm_action=action,
        behavior_size_pct=0.75,
        behavior_size_tier=3,
        reward_horizon_days=horizon,
        forward_return_5d=None,
        forward_return_20d=fwd_20d,
        drawdown_during_holding=None,
        transaction_cost_pct=DEFAULT_TX_COST_PCT,
        reward=reward,
        trade_result=trade_result,
        source=source,
    )


def test_assert_no_lookahead_passes_for_elapsed_horizon():
    # 60 days ago + 20d horizon -> well elapsed
    td = (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d")
    s = _sample(trade_date=td, horizon=20, reward=0.01)
    assert_no_lookahead([s])  # must not raise


def test_assert_no_lookahead_raises_for_pending_horizon():
    # Today + 20d horizon is in the future
    td = datetime.utcnow().strftime("%Y-%m-%d")
    s = _sample(trade_date=td, horizon=20, reward=0.01)
    with pytest.raises(ValueError, match="look-ahead"):
        assert_no_lookahead([s])


def test_assert_no_lookahead_ignores_pending_rows():
    """Rows with reward=None are PENDING by definition and exempt."""
    td = datetime.utcnow().strftime("%Y-%m-%d")
    s = _sample(trade_date=td, horizon=20, reward=None, trade_result="PENDING")
    assert_no_lookahead([s])  # must not raise


# ──────────────────────────────────────────────────────────────────────────────
# JSON-report ingestion
# ──────────────────────────────────────────────────────────────────────────────


def _fake_report(ticker: str, trade_date: str, action: str, fwd_20d: float | None) -> dict:
    return {
        "session": {"ticker": ticker, "start_date": "2023-01-01", "end_date": "2024-01-01"},
        "trades": [{
            "date": trade_date,
            "ticker": ticker,
            "action": action,
            "shares": 100,
            "close_price": 100.0,
            "exec_price": 100.1,
            "value": 10010.0,
            "commission": 18.9,
            "realized_pnl": 0.0,
            "confidence": 0.65,
            "reasoning": "test",
            "price_at_+5d": None,
            "forward_return_5d": None,
            "price_at_+20d": None if fwd_20d is None else (100.0 * (1 + fwd_20d)),
            "forward_return_20d": fwd_20d,
            "trade_result": "WIN" if (fwd_20d or 0) > 0.01 else ("LOSS" if (fwd_20d or 0) < -0.01 else "PENDING"),
        }],
        "audit_log": [],
        "metrics": {},
    }


def test_build_from_json_reports_parses_trade_rows(tmp_path: Path):
    # Use a trade_date well in the past so horizon is elapsed.
    td = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")
    report = _fake_report("COMI.CA", td, "BUY", fwd_20d=0.08)
    report_path = tmp_path / "report_COMI.CA_20240101_000000.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    samples = build_from_json_reports([report_path])
    assert len(samples) == 1
    s = samples[0]
    assert s.ticker == "COMI.CA"
    assert s.trade_date == td
    assert s.llm_action == "BUY"
    assert s.forward_return_20d == pytest.approx(0.08, abs=1e-6)
    assert s.trade_result == "WIN"
    assert s.reward is not None and s.reward > 0
    assert s.feature_version == FEATURE_VERSION
    assert tuple(s.state_features.keys()) == FEATURE_NAMES


def test_build_from_json_reports_marks_recent_as_pending(tmp_path: Path):
    """A trade dated today cannot have a realized 20-day return yet."""
    td = datetime.utcnow().strftime("%Y-%m-%d")
    report = _fake_report("COMI.CA", td, "BUY", fwd_20d=0.08)
    report_path = tmp_path / "report_COMI.CA_today.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    samples = build_from_json_reports([report_path])
    assert len(samples) == 1
    assert samples[0].trade_result == "PENDING"
    assert samples[0].reward is None


def test_build_from_json_reports_ignores_unreadable(tmp_path: Path, caplog):
    bad = tmp_path / "report_X.json"
    bad.write_text("not valid json", encoding="utf-8")
    samples = build_from_json_reports([bad])
    assert samples == []


def test_build_from_json_reports_infers_ticker_from_filename(tmp_path: Path):
    """When ``session.ticker`` is missing, filename pattern saves us."""
    td = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")
    report = _fake_report("COMI.CA", td, "BUY", fwd_20d=0.08)
    report["session"].pop("ticker")
    report_path = tmp_path / "report_COMI.CA_inferred.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    samples = build_from_json_reports([report_path])
    assert len(samples) == 1
    assert samples[0].ticker == "COMI.CA"


# ──────────────────────────────────────────────────────────────────────────────
# Serialization round-trip
# ──────────────────────────────────────────────────────────────────────────────


def test_samples_to_dataframe_has_one_column_per_feature():
    s = _sample()
    df = samples_to_dataframe([s])
    assert len(df) == 1
    for name in FEATURE_NAMES:
        assert f"feat__{name}" in df.columns
    # Schema metadata columns present
    assert "feature_version" in df.columns
    assert "dataset_schema_version" in df.columns
    assert df.iloc[0]["dataset_schema_version"] == DATASET_SCHEMA_VERSION


def test_write_parquet_round_trip(tmp_path: Path):
    samples = [_sample(), _sample(ticker="EAST.CA", action="SELL", fwd_20d=-0.04, reward=0.02)]
    out = tmp_path / "training_v1.parquet"
    written = write_parquet(samples, out)
    assert written.exists()
    # Parquet may not be available on all CI; if it fell back to CSV the
    # write_parquet contract says we get a .csv extension back.
    if written.suffix == ".parquet":
        import pandas as pd
        df = pd.read_parquet(written)
    else:
        import pandas as pd
        df = pd.read_csv(written)
    assert len(df) == 2
    assert set(df["ticker"]) == {"COMI.CA", "EAST.CA"}


# ──────────────────────────────────────────────────────────────────────────────
# Summarize
# ──────────────────────────────────────────────────────────────────────────────


def test_summarize_counts_correctly():
    samples = [
        _sample(ticker="COMI.CA", action="BUY"),
        _sample(ticker="COMI.CA", action="HOLD", trade_result="PENDING", reward=None),
        _sample(ticker="EAST.CA", action="SELL", trade_result="LOSS", reward=-0.02),
    ]
    summary = summarize(samples)
    assert summary["n_samples"] == 3
    assert summary["n_pending"] == 1
    assert summary["n_hold"] == 1
    assert sorted(summary["tickers"]) == ["COMI.CA", "EAST.CA"]
    assert summary["action_counts"] == {"BUY": 1, "SELL": 1, "HOLD": 1}


def test_summarize_empty_returns_zero_count():
    summary = summarize([])
    assert summary == {"n_samples": 0}
