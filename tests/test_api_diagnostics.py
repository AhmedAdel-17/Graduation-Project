"""Tests for the PR9 diagnostics endpoints.

Covers:
- /api/diagnostics/prompts parses ``### P-<ID> — title`` headings out of
  PROMPTS.md, splits slash-grouped IDs, caches the result for 60 s, and
  degrades gracefully when the file is missing.
- /api/diagnostics/fingerprints returns an empty payload (200) when
  Postgres is unreachable, JSON-parses the per-row ``fp_text`` blob, and
  surfaces per-day counts.
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
    # Invalidate the module-level TTL cache between tests so each one sees
    # a fresh parse of whatever PROMPTS.md it sets up under tmp_path.
    api_server._PROMPTS_CACHE["data"] = None
    api_server._PROMPTS_CACHE["ts"] = 0.0
    return TestClient(api_server.app)


def test_prompts_missing_file_returns_none_source(client, tmp_path):
    res = client.get("/api/diagnostics/prompts")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "none"
    assert body["total"] == 0
    assert body["prompts"] == []


def test_prompts_parses_headings_and_splits_slashes(client, tmp_path):
    (tmp_path / "PROMPTS.md").write_text(
        "# PROMPTS\n"
        "\n"
        "Intro paragraph that should be skipped.\n"
        "\n"
        "### P-MARKET — Technical (Chartist) Analyst\n"
        "body\n"
        "\n"
        "## Section heading should not match\n"
        "\n"
        "### P-TRADER-SYS / P-TRADER-USR — Institutional Trader\n"
        "body\n"
        "\n"
        "### P-RESMGR — Research Manager\n",
        encoding="utf-8",
    )
    res = client.get("/api/diagnostics/prompts")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "prompts_md"
    ids = [p["id"] for p in body["prompts"]]
    # Slash-separated IDs split into separate rows so the UI can render
    # one card per prompt even when PROMPTS.md groups them.
    assert ids == ["P-MARKET", "P-TRADER-SYS", "P-TRADER-USR", "P-RESMGR"]
    titles = {p["id"]: p["title"] for p in body["prompts"]}
    assert titles["P-MARKET"] == "Technical (Chartist) Analyst"
    assert titles["P-TRADER-SYS"] == "Institutional Trader"
    assert body["total"] == 4
    # The line numbers are 1-indexed and reference the original heading.
    assert all(p["line"] >= 5 for p in body["prompts"])


def test_prompts_response_is_cached(client, tmp_path, monkeypatch):
    """Two back-to-back calls within the 60 s TTL must not re-read the file."""
    import server.api_server as api_server

    (tmp_path / "PROMPTS.md").write_text(
        "### P-FIRST — One\n", encoding="utf-8"
    )

    real_parse = api_server._parse_prompts_md
    call_count = {"n": 0}

    def counting_parse(path):
        call_count["n"] += 1
        return real_parse(path)

    monkeypatch.setattr(api_server, "_parse_prompts_md", counting_parse)

    a = client.get("/api/diagnostics/prompts").json()
    b = client.get("/api/diagnostics/prompts").json()
    assert a == b
    assert call_count["n"] == 1, "second hit should come from the cache"


def test_fingerprints_empty_when_postgres_unavailable(client):
    with patch("tradingagents.db.is_postgres_available", return_value=False):
        res = client.get("/api/diagnostics/fingerprints")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "none"
    assert body["fingerprints"] == []
    assert body["daily_counts"] == []
    assert body["distinct_fingerprints"] == 0


def test_fingerprints_query_failure_returns_empty(client):
    class _BoomCM:
        def __enter__(self_inner):
            raise RuntimeError("relation missing")

        def __exit__(self_inner, *exc):
            return False

    with patch("tradingagents.db.is_postgres_available", return_value=True), \
         patch("tradingagents.db.connection.cursor", return_value=_BoomCM()):
        res = client.get("/api/diagnostics/fingerprints?days=7")

    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "none"
    assert body["reason"] == "query_failed"
    assert body["days"] == 7


def test_fingerprints_serialises_jsonb_blob(client):
    """When Postgres is available, the JSONB ``fp_text`` is parsed back into
    a dict, dates/datetimes are ISO-stringified, and daily counts return
    one row per day."""

    fp_rows = [
        {
            "fp_text": '{"weights_sha256_16": "abcdef0123456789", "feature_version": "rl_state_v1"}',
            "first_seen": datetime(2026, 5, 10, 9, 0, 0),
            "last_seen": datetime(2026, 5, 14, 18, 0, 0),
            "event_count": 5,
        },
        {
            "fp_text": '{"weights_sha256_16": "0011223344556677"}',
            "first_seen": datetime(2026, 5, 12, 9, 0, 0),
            "last_seen": datetime(2026, 5, 12, 9, 0, 0),
            "event_count": 1,
        },
    ]
    daily_rows = [
        {"day": date(2026, 5, 10), "events": 3, "distinct_fps": 1},
        {"day": date(2026, 5, 12), "events": 2, "distinct_fps": 2},
        {"day": date(2026, 5, 14), "events": 1, "distinct_fps": 1},
    ]

    mock_cursor = MagicMock()
    mock_cursor.fetchall.side_effect = [fp_rows, daily_rows]

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
        res = client.get("/api/diagnostics/fingerprints?days=14")

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "postgres"
    assert body["days"] == 14
    assert body["distinct_fingerprints"] == 2
    assert body["total_events"] == 6
    first = body["fingerprints"][0]
    assert first["event_count"] == 5
    # JSONB blob parsed into a dict for the dashboard.
    assert first["fingerprint"]["weights_sha256_16"] == "abcdef0123456789"
    assert first["fingerprint"]["feature_version"] == "rl_state_v1"
    assert first["first_seen"].startswith("2026-05-10T09:00:00")
    # Daily counts: dates ISO-stringified, ints preserved.
    days = [d["day"] for d in body["daily_counts"]]
    assert days == ["2026-05-10", "2026-05-12", "2026-05-14"]
    assert body["daily_counts"][1]["distinct"] == 2
