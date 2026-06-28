"""Tests for the forward paper-trading harness (remediation Track A, Phase 0).

Fully deterministic — the realized-price lookup is injected, so no network/LLM.
The key property under test is that look-ahead is structurally impossible:
decisions only score once matured, and only against an injected on/after price.
"""
from datetime import datetime

import pytest

from tradingagents.paper_trading import (
    PaperTradingStore,
    record_decision,
    score_matured,
    compute_metrics,
    group_metrics_by_strategy,
    target_eval_date,
    RESULT_WIN,
    RESULT_LOSS,
    RESULT_NO_POSITION,
)
from tradingagents.dataflows.egx_costs import round_trip_cost_pct


@pytest.fixture
def store(tmp_path):
    return PaperTradingStore(tmp_path / "decisions.jsonl")


def test_record_persists_open_decision(store):
    rec = record_decision(
        store, ticker="COMI.CA", run_date="2026-06-01", decision="BUY",
        entry_price=50.0, confidence=0.7, horizon_days=20,
    )
    assert rec.status == "OPEN"
    assert rec.exit_price is None
    assert rec.round_trip_cost_pct == round_trip_cost_pct(False)
    loaded = store.load()
    assert len(loaded) == 1 and loaded[0].ticker == "COMI.CA"


def test_record_is_idempotent_per_day(store):
    record_decision(store, ticker="COMI.CA", run_date="2026-06-01", decision="BUY", entry_price=50.0)
    record_decision(store, ticker="COMI.CA", run_date="2026-06-01", decision="SELL", entry_price=50.0)
    rows = store.load()
    assert len(rows) == 1
    assert rows[0].decision == "BUY"  # second call was a no-op


def test_unmatured_decision_is_not_scored(store):
    record_decision(store, ticker="COMI.CA", run_date="2026-06-01", decision="BUY",
                    entry_price=50.0, horizon_days=20)
    # "now" is the same day the decision was made — horizon has not elapsed.
    closed = score_matured(store, lambda t, d: 999.0, now=datetime(2026, 6, 1))
    assert closed == []
    assert store.load()[0].status == "OPEN"


def test_matured_buy_win_is_net_of_costs(store):
    record_decision(store, ticker="COMI.CA", run_date="2026-06-01", decision="BUY",
                    entry_price=100.0, horizon_days=20)
    # +10% gross, well clear of ~0.58% round-trip cost.
    closed = score_matured(store, lambda t, d: 110.0, now=datetime(2026, 12, 1))
    assert len(closed) == 1
    d = closed[0]
    assert d.result == RESULT_WIN
    assert d.gross_return == pytest.approx(0.10, abs=1e-9)
    assert d.net_return == pytest.approx(0.10 - round_trip_cost_pct(False), abs=1e-9)


def test_tiny_gain_below_cost_is_a_loss(store):
    record_decision(store, ticker="COMI.CA", run_date="2026-06-01", decision="BUY",
                    entry_price=100.0, horizon_days=20)
    # +0.2% gross does NOT clear ~0.58% cost ⇒ net negative ⇒ LOSS.
    closed = score_matured(store, lambda t, d: 100.2, now=datetime(2026, 12, 1))
    assert closed[0].result == RESULT_LOSS
    assert closed[0].net_return < 0


def test_hold_is_no_position(store):
    record_decision(store, ticker="COMI.CA", run_date="2026-06-01", decision="HOLD",
                    entry_price=100.0, horizon_days=20)
    closed = score_matured(store, lambda t, d: 200.0, now=datetime(2026, 12, 1))
    assert closed[0].result == RESULT_NO_POSITION
    assert closed[0].net_return == 0.0


def test_open_when_price_not_yet_available(store):
    record_decision(store, ticker="COMI.CA", run_date="2026-06-01", decision="BUY",
                    entry_price=100.0, horizon_days=20)
    # Matured by date, but the realized price source returns None ⇒ stays OPEN.
    closed = score_matured(store, lambda t, d: None, now=datetime(2026, 12, 1))
    assert closed == []
    assert store.load()[0].status == "OPEN"


def test_metrics_buy_hit_rate_and_ci(store):
    # 3 BUY wins, 1 BUY loss, 1 HOLD → hit rate 75% on 4 decided.
    for i, (px_exit, dec) in enumerate(
        [(120.0, "BUY"), (115.0, "BUY"), (130.0, "BUY"), (90.0, "BUY"), (100.0, "HOLD")]
    ):
        record_decision(store, ticker=f"T{i}.CA", run_date="2026-06-01", decision=dec,
                        entry_price=100.0, horizon_days=20)

    prices = {"T0.CA": 120.0, "T1.CA": 115.0, "T2.CA": 130.0, "T3.CA": 90.0, "T4.CA": 100.0}
    score_matured(store, lambda t, d: prices[t], now=datetime(2026, 12, 1))

    m = compute_metrics(store.load(), benchmark_return=0.05)
    assert m["n_buy_closed"] == 4
    assert m["buy_wins"] == 3
    assert m["buy_losses"] == 1
    assert m["buy_hit_rate"] == pytest.approx(0.75)
    lo, hi = m["buy_hit_rate_ci95"]
    assert 0.0 <= lo <= 0.75 <= hi <= 1.0
    assert m["mean_net_excess_vs_benchmark"] is not None


def test_strategy_tag_allows_side_by_side_comparison(store):
    # Same ticker/date/entry, two strategies → both recorded, scored independently.
    record_decision(store, ticker="COMI.CA", run_date="2026-06-01", decision="BUY",
                    entry_price=100.0, horizon_days=20, strategy="llm")
    record_decision(store, ticker="COMI.CA", run_date="2026-06-01", decision="HOLD",
                    entry_price=100.0, horizon_days=20, strategy="momentum")
    assert len(store.load()) == 2  # not deduped — different strategies

    score_matured(store, lambda t, d: 120.0, now=datetime(2026, 12, 1))
    grouped = group_metrics_by_strategy(store.load())
    assert grouped["llm"]["buy_wins"] == 1
    assert grouped["momentum"]["n_buy_closed"] == 0  # momentum said HOLD → no position


def test_compute_metrics_strategy_filter(store):
    record_decision(store, ticker="A.CA", run_date="2026-06-01", decision="BUY",
                    entry_price=100.0, strategy="llm")
    record_decision(store, ticker="A.CA", run_date="2026-06-01", decision="BUY",
                    entry_price=100.0, strategy="buy_and_hold")
    score_matured(store, lambda t, d: 90.0, now=datetime(2026, 12, 1))
    m = compute_metrics(store.load(), strategy="llm")
    assert m["strategy"] == "llm"
    assert m["n_buy_closed"] == 1


def test_target_eval_date_is_after_run_date():
    assert target_eval_date("2026-06-01", 20) > "2026-06-01"


def test_persistence_round_trips_through_disk(store):
    record_decision(store, ticker="COMI.CA", run_date="2026-06-01", decision="BUY",
                    entry_price=100.0, horizon_days=20)
    score_matured(store, lambda t, d: 110.0, now=datetime(2026, 12, 1))
    # Re-open the store from disk; the closure must have been persisted.
    reloaded = PaperTradingStore(store.path).load()
    assert reloaded[0].status == "CLOSED"
    assert reloaded[0].result == RESULT_WIN
