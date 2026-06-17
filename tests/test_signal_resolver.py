"""P3 tests for the Portfolio Assistant signal resolver.

Pure / offline: the DB query and the run launcher are both injected, so these
exercise the freshness window, the stale → quant-prior degradation, and the
bounded enqueue hook without Postgres or a real graph run.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from tradingagents.portfolio import signals as sig_mod
from tradingagents.portfolio.schemas import SignalLabel, SignalSource
from tradingagents.portfolio.signals import SignalResolver

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def _row(decision="BUY", conf=0.7, *, age_days=1.0, session="sess-1"):
    return {
        "session_id": session,
        "final_decision": decision,
        "confidence_overall": conf,
        "created_at": NOW - timedelta(days=age_days),
        "trade_date": (NOW - timedelta(days=age_days)).date(),
    }


def test_fresh_signal_uses_agent_decision():
    rows = {"COMI.CA": _row("BUY", 0.72, age_days=2.0)}
    resolver = SignalResolver(query_fn=lambda t: rows.get(t), max_age_days=7)

    views = resolver.resolve(["COMI.CA"], now=NOW)
    v = views["COMI.CA"]

    assert v.source == SignalSource.AGENT
    assert v.label == SignalLabel.BUY
    assert abs(v.confidence - 0.72) < 1e-9
    assert v.is_stale is False
    assert v.session_id == "sess-1"


def test_freshness_boundary_is_inclusive():
    rows = {"COMI.CA": _row(age_days=7.0)}
    resolver = SignalResolver(query_fn=lambda t: rows.get(t), max_age_days=7)

    v = resolver.resolve(["COMI.CA"], now=NOW)["COMI.CA"]
    assert v.source == SignalSource.AGENT
    assert v.is_stale is False


def test_stale_signal_degrades_to_quant_prior():
    rows = {"COMI.CA": _row("BUY", 0.9, age_days=30.0)}
    resolver = SignalResolver(query_fn=lambda t: rows.get(t), max_age_days=7)

    v = resolver.resolve(["COMI.CA"], now=NOW, enqueue_stale=False)["COMI.CA"]

    assert v.source == SignalSource.QUANT_PRIOR
    assert v.label == SignalLabel.HOLD          # neutral → no view tilt
    assert v.confidence == 0.0
    assert v.is_stale is True
    assert v.age_days is not None and v.age_days > 7


def test_missing_signal_degrades_to_quant_prior():
    resolver = SignalResolver(query_fn=lambda t: None, max_age_days=7)

    v = resolver.resolve(["FWRY.CA"], now=NOW, enqueue_stale=False)["FWRY.CA"]
    assert v.source == SignalSource.QUANT_PRIOR
    assert v.is_stale is True
    assert v.age_days is None


def test_label_extraction_from_proposal_marker():
    rows = {"TMGH.CA": _row("FINAL TRANSACTION PROPOSAL: SELL", 0.6, age_days=1.0)}
    resolver = SignalResolver(query_fn=lambda t: rows.get(t), max_age_days=7)

    v = resolver.resolve(["TMGH.CA"], now=NOW)["TMGH.CA"]
    assert v.label == SignalLabel.SELL


class _FakeLauncher:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.fired = threading.Event()

    def launch(self, ticker: str, trade_date: str) -> None:
        self.calls.append((ticker, trade_date))
        self.fired.set()


def test_stale_signal_enqueues_refresh_run():
    launcher = _FakeLauncher()
    resolver = SignalResolver(
        query_fn=lambda t: None, run_launcher=launcher, max_age_days=7)

    views = resolver.resolve(["COMI.CA"], now=NOW, trade_date="2026-06-15")

    assert views["COMI.CA"].source == SignalSource.QUANT_PRIOR
    assert "COMI.CA" in resolver.enqueued
    assert launcher.fired.wait(timeout=2.0)
    assert launcher.calls == [("COMI.CA", "2026-06-15")]


def test_enqueue_disabled_does_not_launch():
    launcher = _FakeLauncher()
    resolver = SignalResolver(
        query_fn=lambda t: None, run_launcher=launcher, max_age_days=7)

    resolver.resolve(["COMI.CA"], now=NOW, enqueue_stale=False)

    assert resolver.enqueued == []
    assert launcher.calls == []


def test_resolve_dedups_tickers():
    rows = {"COMI.CA": _row(age_days=1.0)}
    resolver = SignalResolver(query_fn=lambda t: rows.get(t), max_age_days=7)

    views = resolver.resolve(["COMI.CA", "COMI.CA"], now=NOW)
    assert list(views) == ["COMI.CA"]


def test_default_query_no_postgres_returns_none(monkeypatch):
    # When Postgres is unavailable the default query degrades to None (finding #4).
    monkeypatch.setattr(
        "tradingagents.db.is_postgres_available", lambda: False, raising=False)
    assert sig_mod._row_query_default("COMI.CA") is None
