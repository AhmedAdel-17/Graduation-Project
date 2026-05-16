"""Tests for the GET /api/sessions/{session_id}/trace endpoint (PR3 of the
dashboard redesign).

Three paths exercised:
- Postgres unreachable + JSONL audit file present  → source="jsonl"
- Postgres unreachable + no JSONL                  → 404
- Postgres "available" (mocked cursor)             → source="postgres"

The Postgres path uses a mocked psycopg2 DictCursor so the test does not
require a running database.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    """Spin up the FastAPI app pointing at a temp audit_logs root."""
    # Make sure the audit-log directory the endpoint reads is the temp one,
    # not whatever happens to exist in the repo.
    import server.api_server as api_server

    monkeypatch.setattr(api_server, "PROJECT_ROOT", tmp_path)
    return TestClient(api_server.app)


@pytest.fixture
def jsonl_audit(tmp_path) -> str:
    """Write a minimal SESSION_START + SESSION_END JSONL pair and return the
    session_id that maps to it."""
    session_id = "test-session-abc123"
    ticker = "COMI.CA"
    audit_dir = tmp_path / "audit_logs" / ticker
    audit_dir.mkdir(parents=True)

    entries = [
        {
            "event": "SESSION_START",
            "_session_id": session_id,
            "_logged_at": "2026-05-15T10:00:00Z",
            "ticker": ticker,
            "trade_date": "2026-05-15",
            "market": "EGX",
        },
        {
            "event": "ANALYST_OUTPUT",
            "_session_id": session_id,
            "_logged_at": "2026-05-15T10:00:15Z",
            "_agent": "market_analyst",
            "opinion_summary": "RSI 62, trend bullish.",
            "confidence_score": 0.78,
        },
        {
            "event": "FINAL_STATE",
            "_session_id": session_id,
            "_logged_at": "2026-05-15T10:01:00Z",
            "final_trade_decision": "BUY 1000 shares at 95.00",
            "confidence_scores": {"overall": 0.72},
            "state": {"final_trade_decision": "BUY"},
        },
    ]
    with open(audit_dir / "audit_log.jsonl", "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return session_id


def test_trace_jsonl_fallback_returns_session_and_events(client, jsonl_audit):
    """When Postgres is unavailable, the endpoint should walk audit_logs/
    and reconstruct the trace from the JSONL file."""
    with patch("tradingagents.db.is_postgres_available", return_value=False):
        res = client.get(f"/api/sessions/{jsonl_audit}/trace")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "jsonl"
    assert body["session"] is not None
    assert body["session"]["ticker"] == "COMI.CA"
    assert body["session"]["session_id"] == jsonl_audit
    assert body["session"]["final_decision"].startswith("BUY")
    assert body["session"]["confidence_overall"] == pytest.approx(0.72)
    # Three entries written → three events returned.
    assert len(body["events"]) == 3
    # Spot-check the analyst event made it through with its agent_name.
    analyst_events = [e for e in body["events"] if e["agent_name"] == "market_analyst"]
    assert len(analyst_events) == 1
    assert "RSI" in (analyst_events[0]["opinion_summary"] or "")


def test_trace_missing_session_returns_404(client):
    """No JSONL, no Postgres → 404 (not 500)."""
    with patch("tradingagents.db.is_postgres_available", return_value=False):
        res = client.get("/api/sessions/does-not-exist/trace")
    assert res.status_code == 404
    assert "not found" in res.json().get("detail", "").lower()


def test_trace_postgres_path(client):
    """When Postgres is available, the endpoint should query analysis_sessions
    and agent_events instead of touching the JSONL filesystem."""
    sample_session = {
        "session_id": "pg-session-xyz",
        "ticker": "TMGH.CA",
        "trade_date": date(2026, 5, 15),
        "market": "EGX",
        "final_decision": "HOLD",
        "risk_veto": False,
        "confidence_overall": 0.55,
        "confidence_scores": {"overall": 0.55, "technical": 0.60},
        "execution_plan": {"action": "HOLD"},
        "risk_assessment": {"approved": True},
        "data_quality": {"completeness_score": 90},
        "full_state": {"target_market": "EGX"},
        "model_fingerprint": {"deep_think_llm": "deepseek-chat"},
        "user_id": None,
        "created_at": datetime(2026, 5, 15, 10, 0, 0),
    }
    sample_events = [
        {
            "event_type": "analyst_output",
            "agent_name": "market_analyst",
            "opinion_type": None,
            "opinion_summary": "Mild uptrend.",
            "confidence_score": 0.6,
            "structured_output": {"trend": "uptrend"},
            "model_fingerprint": {"deep_think_llm": "deepseek-chat"},
            "logged_at": datetime(2026, 5, 15, 10, 0, 5),
        }
    ]

    # The DictCursor returned by db_cursor(dict_cursor=True) needs to behave
    # like a row mapping that .keys() iterates. A plain dict already does.
    fetch_results = [sample_session, sample_events]
    mock_cursor = MagicMock()
    mock_cursor.fetchone.side_effect = [sample_session]
    mock_cursor.fetchall.side_effect = [sample_events]

    # Context-manager helper: returns the cursor on __enter__, nothing on exit.
    class _CursorCM:
        def __enter__(self_inner):
            return mock_cursor

        def __exit__(self_inner, *exc):
            return False

    with patch("tradingagents.db.is_postgres_available", return_value=True), \
         patch("tradingagents.db.connection.cursor", return_value=_CursorCM()):
        res = client.get(f"/api/sessions/{sample_session['session_id']}/trace")

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "postgres"
    assert body["session"]["ticker"] == "TMGH.CA"
    # Decimals and dates serialized cleanly.
    assert body["session"]["confidence_overall"] == pytest.approx(0.55)
    assert body["session"]["trade_date"] == "2026-05-15"
    assert body["session"]["created_at"].startswith("2026-05-15T10:00:00")
    # One event returned, fields intact.
    assert len(body["events"]) == 1
    ev = body["events"][0]
    assert ev["agent_name"] == "market_analyst"
    assert ev["structured_output"] == {"trend": "uptrend"}
    assert ev["logged_at"].startswith("2026-05-15T10:00:05")
    # Two cursor.execute calls — sessions + events queries.
    assert mock_cursor.execute.call_count == 2
    # The fetch_results binding is just here to silence linters if the test
    # is ever extended; remove if it gets noisy.
    assert fetch_results is not None
