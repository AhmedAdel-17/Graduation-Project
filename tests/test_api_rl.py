"""Tests for the PR7 RL meta-policy endpoints.

Covers:
- /api/rl/status returns the env-backed flag + model path, graceful when
  policy load fails.
- /api/rl/decisions returns an empty list (200, source="none") when
  Postgres is unreachable — no 500.
- /api/rl/decisions queries agent_events filtered by event_type +
  optionally ticker + session_id, mapping psycopg2 dict-rows into the
  response shape.
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    import server.api_server as api_server

    monkeypatch.setattr(api_server, "PROJECT_ROOT", tmp_path)
    return TestClient(api_server.app)


def test_rl_status_reports_disabled_by_default(client, monkeypatch):
    """The default config has rl_meta_policy_enabled=False — endpoint
    should reflect that without attempting to load any model."""
    import server.api_server as api_server

    monkeypatch.setattr(
        api_server,
        "get_config",
        lambda: {"rl_meta_policy_enabled": False, "rl_model_path": ""},
    )
    res = client.get("/api/rl/status")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["enabled"] is False
    assert body["loaded"] is False
    assert body["model_path"] is None


def test_rl_status_handles_policy_load_failure(client, monkeypatch):
    """When the flag is on but the checkpoint can't be loaded (missing
    file, torch missing, etc.) we must NOT 500 — surface loaded=False."""
    import server.api_server as api_server

    monkeypatch.setattr(
        api_server,
        "get_config",
        lambda: {
            "rl_meta_policy_enabled": True,
            "rl_model_path": "/tmp/does-not-exist.pt",
        },
    )
    res = client.get("/api/rl/status")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["enabled"] is True
    assert body["model_path"] == "/tmp/does-not-exist.pt"
    assert body["loaded"] is False
    assert body["model_fingerprint"] is None


def test_rl_decisions_empty_when_postgres_unavailable(client):
    """No DB → response is `{decisions: [], source: "none"}`, not a 500."""
    with patch("tradingagents.db.is_postgres_available", return_value=False):
        res = client.get("/api/rl/decisions")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["decisions"] == []
    assert body["source"] == "none"


def test_rl_decisions_filters_and_serializes(client):
    """Postgres-available path: dict-cursor rows are projected into the
    response shape, with Decimal/date/datetime turned into JSON-safe types."""
    from decimal import Decimal

    sample_rows = [
        {
            "session_id": "sess-aaa",
            "event_type": "rl_meta_size_adjustment",
            "agent_name": "rl_meta_policy",
            "opinion_summary": "size=0.5 q=[...]",
            "confidence_score": Decimal("0.012"),
            "structured_output": {
                "size_multiplier": 0.5,
                "action_index": 2,
                "feature_version": "rl_state_v1",
            },
            "model_fingerprint": {"weights_sha256_16": "abcdef0123456789"},
            "logged_at": datetime(2026, 5, 14, 12, 0, 0),
            "ticker": "COMI.CA",
            "trade_date": date(2026, 5, 14),
        }
    ]
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = sample_rows

    class _CursorCM:
        def __enter__(self_inner):
            return mock_cursor

        def __exit__(self_inner, *exc):
            return False

    with patch("tradingagents.db.is_postgres_available", return_value=True), \
         patch(
             "tradingagents.db.connection.cursor",
             return_value=_CursorCM(),
         ):
        res = client.get("/api/rl/decisions?ticker=COMI.CA&limit=10")

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "postgres"
    assert body["total"] == 1
    assert body["ticker"] == "COMI.CA"
    decision = body["decisions"][0]
    assert decision["session_id"] == "sess-aaa"
    # Decimal serialized to float, datetime/date to ISO strings.
    assert decision["confidence_score"] == pytest.approx(0.012)
    assert decision["trade_date"] == "2026-05-14"
    assert decision["logged_at"].startswith("2026-05-14T12:00:00")
    # The structured payload + fingerprint pass through unchanged.
    assert decision["structured_output"]["size_multiplier"] == 0.5
    assert (
        decision["model_fingerprint"]["weights_sha256_16"] == "abcdef0123456789"
    )

    # Sanity: the SQL had the ticker join + ticker filter + limit param.
    args, _kwargs = mock_cursor.execute.call_args
    sql, params = args
    assert "LEFT JOIN analysis_sessions" in sql
    assert "s.ticker = %s" in sql
    assert "rl_meta_size_adjustment" in params
    assert "COMI.CA" in params
    assert params[-1] == 10


def test_rl_decisions_query_failure_returns_empty(client):
    """If the SQL query itself raises (e.g. column missing), the endpoint
    must degrade to an empty list rather than 500."""

    class _BoomCM:
        def __enter__(self_inner):
            raise RuntimeError("relation missing")

        def __exit__(self_inner, *exc):
            return False

    with patch("tradingagents.db.is_postgres_available", return_value=True), \
         patch("tradingagents.db.connection.cursor", return_value=_BoomCM()):
        res = client.get("/api/rl/decisions")

    assert res.status_code == 200
    body = res.json()
    assert body["decisions"] == []
    assert body["source"] == "none"
    assert body["reason"] == "query_failed"
