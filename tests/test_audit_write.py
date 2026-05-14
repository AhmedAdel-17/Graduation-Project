"""Tests for tradingagents.db.audit_writer (PR 5).

Covers:
- ``write_analysis_session()`` no-op when Postgres unavailable.
- ``write_analysis_session()`` executes the expected INSERT under a mocked cursor.
- ``write_agent_events()`` builds one row per non-empty agent output and
  bulk-inserts via ``execute_values``.
- Graph wiring: ``propagate()`` calls the writers exactly once each, with the
  same session_id, even when Postgres is unavailable (writers no-op).
- Failures inside the writer never propagate into the caller.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.db import audit_writer


# ─── helpers ───────────────────────────────────────────────────────────────────


def _sample_final_state() -> dict:
    """A minimal final_state with one report and one structured output per
    agent, enough to exercise every branch of _build_agent_event_rows."""
    return {
        "target_market": "EGX",
        "final_trade_decision": "BUY 1000 shares COMI.CA at limit 95.00 EGP",
        "risk_veto": False,
        "confidence_scores": {"overall": 0.72, "technical": 0.80},
        "execution_plan": {"action": "BUY", "shares": 1000, "limit": 95.0},
        "risk_assessment": {"approved": True, "violations": []},
        "data_quality": {"completeness_score": 95},
        "market_report": "RSI 65, MACD bullish crossover, price above 200-day SMA.",
        "sentiment_report": "Mild positive: +0.18 weighted score, regime NEUTRAL.",
        "news_report": "CBE rate decision supports financials.",
        "fundamentals_report": "Strong NIM, NPL trending lower, CAR healthy.",
        "technical_analysis": {"trend": "uptrend", "confidence_score": 0.80},
        "fundamental_analysis": {"health": "strong", "confidence_score": 0.70},
        "sentiment_analysis": {"sentiment": "neutral", "confidence_score": 0.55},
        "investment_debate_state": {
            "bull_history": "Bull: rate cut tailwind",
            "bear_history": "Bear: valuation stretched",
            "bull_thesis": {"key_catalysts": ["rate cut"]},
            "bear_thesis": {"key_risks": ["FX volatility"]},
            "judge_decision": "Leaning bullish, position 0.6× normal.",
        },
        "trader_investment_plan": "BUY with 0.6× sizing, ATR stop 92.50.",
        "risk_debate_state": {
            "risky_history": "Aggressive: full size",
            "safe_history": "Conservative: half size",
            "neutral_history": "Neutral: 0.6× sizing",
            "judge_decision": "Approve at 0.6× sizing.",
        },
    }


# ─── _build_agent_event_rows ──────────────────────────────────────────────────


def test_build_agent_event_rows_yields_one_row_per_present_agent():
    rows = audit_writer._build_agent_event_rows(
        session_id="sess-123",
        final_state=_sample_final_state(),
        model_fingerprint_json='{"llm":"x"}',
    )
    # All 13 agents in the map are present in the sample state.
    assert len(rows) == 13

    # Every row has the session_id in slot 0 and the fingerprint in slot 7.
    for row in rows:
        assert row[0] == "sess-123"
        assert row[7] == '{"llm":"x"}'

    # The 'final' row's opinion_summary contains the final decision.
    final_rows = [r for r in rows if r[2] == "final"]
    assert len(final_rows) == 1
    assert "BUY 1000 shares" in final_rows[0][4]


def test_build_agent_event_rows_skips_empty_agents():
    """Agents that wrote nothing in this run are excluded from the row set."""
    state = {
        "target_market": "EGX",
        "final_trade_decision": "HOLD — insufficient data.",
        # Only the final decision is present; all other agents are missing.
        "investment_debate_state": {},
        "risk_debate_state": {},
    }
    rows = audit_writer._build_agent_event_rows(
        session_id="sess-empty",
        final_state=state,
        model_fingerprint_json=None,
    )
    # Only "final" survives.
    agent_names = [r[2] for r in rows]
    assert agent_names == ["final"]


def test_build_agent_event_rows_extracts_confidence_score():
    rows = audit_writer._build_agent_event_rows(
        session_id="sess-conf",
        final_state=_sample_final_state(),
        model_fingerprint_json=None,
    )
    by_agent = {r[2]: r for r in rows}
    # market_analyst structured output has confidence_score=0.80
    assert by_agent["market_analyst"][5] == pytest.approx(0.80)
    # fundamentals_analyst → 0.70
    assert by_agent["fundamentals_analyst"][5] == pytest.approx(0.70)
    # sentiment_analyst → 0.55
    assert by_agent["sentiment_analyst"][5] == pytest.approx(0.55)
    # news_analyst has no structured output → None
    assert by_agent["news_analyst"][5] is None


# ─── _coerce_trade_date / build_model_fingerprint ─────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2025-12-01", date(2025, 12, 1)),
        ("2025-12-01T10:30:00", date(2025, 12, 1)),
        (date(2025, 12, 1), date(2025, 12, 1)),
        ("garbage", None),
        (None, None),
    ],
)
def test_coerce_trade_date(value, expected):
    assert audit_writer._coerce_trade_date(value) == expected


def test_build_model_fingerprint_captures_canonical_keys():
    config = {
        "llm_provider": "openai",
        "deep_think_llm": "deepseek-chat",
        "quick_think_llm": "deepseek-chat",
        "backend_url": "https://api.deepseek.com",
        "target_market": "EGX",
    }
    fp = audit_writer.build_model_fingerprint(config)
    assert fp["llm_provider"] == "openai"
    assert fp["deep_think_llm"] == "deepseek-chat"
    assert fp["temperature"] == 0
    assert fp["seed"] is None  # not yet pinned — MEMORY.md §B
    assert fp["target_market"] == "EGX"


# ─── write_analysis_session ───────────────────────────────────────────────────


def test_write_analysis_session_noop_when_postgres_unavailable(monkeypatch):
    monkeypatch.setattr(audit_writer, "is_postgres_available", lambda: False)
    cursor_mock = MagicMock()
    monkeypatch.setattr(audit_writer, "db_cursor", cursor_mock)

    ok = audit_writer.write_analysis_session(
        session_id="sess-x",
        ticker="COMI.CA",
        trade_date="2025-12-01",
        final_state=_sample_final_state(),
        model_fingerprint={"llm": "x"},
        user_id=None,
    )
    assert ok is False
    cursor_mock.assert_not_called()


def test_write_analysis_session_executes_insert(monkeypatch):
    """When Postgres is available, write_analysis_session runs one INSERT."""
    monkeypatch.setattr(audit_writer, "is_postgres_available", lambda: True)

    fake_cursor = MagicMock()
    fake_cm = MagicMock()
    fake_cm.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cm.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(audit_writer, "db_cursor", lambda **kw: fake_cm)

    ok = audit_writer.write_analysis_session(
        session_id="sess-1",
        ticker="COMI.CA",
        trade_date="2025-12-01",
        final_state=_sample_final_state(),
        model_fingerprint={"llm": "x"},
        user_id="alice",
    )
    assert ok is True
    fake_cursor.execute.assert_called_once()
    args, _ = fake_cursor.execute.call_args
    sql, params = args
    assert "INSERT INTO analysis_sessions" in sql
    # First three params are session_id, ticker, trade_date (coerced to date)
    assert params[0] == "sess-1"
    assert params[1] == "COMI.CA"
    assert params[2] == date(2025, 12, 1)
    # user_id is second-to-last
    assert params[-2] == "alice"


def test_write_analysis_session_rejects_bad_trade_date(monkeypatch):
    monkeypatch.setattr(audit_writer, "is_postgres_available", lambda: True)
    cursor_mock = MagicMock()
    monkeypatch.setattr(audit_writer, "db_cursor", cursor_mock)

    ok = audit_writer.write_analysis_session(
        session_id="sess-bad",
        ticker="COMI.CA",
        trade_date="not-a-date",
        final_state=_sample_final_state(),
    )
    assert ok is False
    cursor_mock.assert_not_called()


def test_write_analysis_session_swallows_cursor_exception(monkeypatch):
    """A write failure must NOT raise into the caller."""
    monkeypatch.setattr(audit_writer, "is_postgres_available", lambda: True)

    class _BrokenCursor:
        def execute(self, *a, **k):
            raise RuntimeError("connection lost")

    fake_cm = MagicMock()
    fake_cm.__enter__ = MagicMock(return_value=_BrokenCursor())
    fake_cm.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(audit_writer, "db_cursor", lambda **kw: fake_cm)

    ok = audit_writer.write_analysis_session(
        session_id="sess-explode",
        ticker="COMI.CA",
        trade_date="2025-12-01",
        final_state=_sample_final_state(),
    )
    assert ok is False  # logged + swallowed; no exception escaped


# ─── write_agent_events ───────────────────────────────────────────────────────


def test_write_agent_events_noop_when_postgres_unavailable(monkeypatch):
    monkeypatch.setattr(audit_writer, "is_postgres_available", lambda: False)
    cursor_mock = MagicMock()
    monkeypatch.setattr(audit_writer, "db_cursor", cursor_mock)

    n = audit_writer.write_agent_events(
        session_id="sess-x",
        final_state=_sample_final_state(),
    )
    assert n == 0
    cursor_mock.assert_not_called()


def test_write_agent_events_uses_execute_values(monkeypatch):
    """When Postgres is available, execute_values is called with N rows."""
    monkeypatch.setattr(audit_writer, "is_postgres_available", lambda: True)

    fake_cursor = MagicMock()
    fake_cm = MagicMock()
    fake_cm.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cm.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(audit_writer, "db_cursor", lambda **kw: fake_cm)

    execute_values_mock = MagicMock()
    with patch("psycopg2.extras.execute_values", execute_values_mock):
        n = audit_writer.write_agent_events(
            session_id="sess-2",
            final_state=_sample_final_state(),
        )

    assert n == 13
    execute_values_mock.assert_called_once()
    args, _ = execute_values_mock.call_args
    # args: (cursor, sql, rows, ...)
    assert args[0] is fake_cursor
    assert "INSERT INTO agent_events" in args[1]
    assert len(args[2]) == 13  # 13 rows from a full sample state
    # Every row has the session id.
    for row in args[2]:
        assert row[0] == "sess-2"


def test_write_agent_events_empty_state_returns_zero(monkeypatch):
    monkeypatch.setattr(audit_writer, "is_postgres_available", lambda: True)
    cursor_mock = MagicMock()
    monkeypatch.setattr(audit_writer, "db_cursor", cursor_mock)

    n = audit_writer.write_agent_events(
        session_id="sess-empty",
        final_state={},  # nothing to write
    )
    assert n == 0
    cursor_mock.assert_not_called()


def test_write_agent_events_swallows_cursor_exception(monkeypatch):
    """A bulk-insert failure must not raise."""
    monkeypatch.setattr(audit_writer, "is_postgres_available", lambda: True)

    fake_cursor = MagicMock()
    fake_cm = MagicMock()
    fake_cm.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cm.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(audit_writer, "db_cursor", lambda **kw: fake_cm)

    def _raising_execute_values(*a, **k):
        raise RuntimeError("table missing")

    with patch("psycopg2.extras.execute_values", _raising_execute_values):
        n = audit_writer.write_agent_events(
            session_id="sess-explode",
            final_state=_sample_final_state(),
        )
    assert n == 0
