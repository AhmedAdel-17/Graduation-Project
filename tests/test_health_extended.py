"""Tests for the PR 9 extensions to ``/api/health`` runtime diagnostics.

Covers:
- ``memory.seeded`` per-collection flag is computed from chroma counts vs.
  the expected seed corpus size.
- ``memory.min_similarity`` is surfaced from config.
- ``postgres.audit_write_lag_seconds`` + ``postgres.backtest_runs_count``
  are populated from a SELECT under a mocked cursor / are None when Postgres
  is unavailable.
- ``chroma_collections_unseeded`` degraded reason fires when persistence is
  on but no seeds have been loaded.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from server import api_server


# ─── memory.seeded + memory.min_similarity ────────────────────────────────────


def test_seeded_flag_true_when_chroma_count_meets_seed_count(monkeypatch):
    """Each collection's seeded flag is True iff its Chroma count ≥ expected
    seed corpus size for that agent."""
    # Pretend chroma has the exact expected number of seeds per collection.
    expected = api_server._seed_count_per_collection()
    assert expected, "seed corpus must be non-empty for this test"

    monkeypatch.setattr(
        api_server,
        "_chroma_persistence_diagnostics",
        lambda _p: {
            "persist_dir": "./chroma_db",
            "persistent": True,
            "collection_counts": expected,  # exactly matches seed counts
            "total_documents": sum(expected.values()),
        },
    )
    monkeypatch.setattr(
        api_server,
        "_postgres_audit_diagnostics",
        lambda _u: {"audit_write_lag_seconds": None, "backtest_runs_count": None, "reachable": False},
    )
    monkeypatch.setattr(
        api_server,
        "get_config",
        lambda: {
            "memory_backend": "chroma",
            "chroma_persist_dir": "./chroma_db",
            "memory_min_similarity": 0.30,
            "postgres_url": "",
            "redis_url": "",
        },
    )
    monkeypatch.setattr(api_server, "_tcp_reachable_from_url", lambda _u: None)
    monkeypatch.setattr(api_server, "_module_available", lambda _n: False)

    diag = api_server._runtime_diagnostics()
    seeded = diag["memory"]["seeded"]
    assert isinstance(seeded, dict)
    for name in api_server._CHROMA_AGENT_COLLECTIONS:
        assert seeded[name] is True, f"{name} should be marked seeded"


def test_seeded_flag_false_when_chroma_empty(monkeypatch):
    monkeypatch.setattr(
        api_server,
        "_chroma_persistence_diagnostics",
        lambda _p: {
            "persist_dir": "./chroma_db",
            "persistent": True,
            "collection_counts": {n: 0 for n in api_server._CHROMA_AGENT_COLLECTIONS},
            "total_documents": 0,
        },
    )
    monkeypatch.setattr(
        api_server,
        "_postgres_audit_diagnostics",
        lambda _u: {"audit_write_lag_seconds": None, "backtest_runs_count": None, "reachable": False},
    )
    monkeypatch.setattr(
        api_server,
        "get_config",
        lambda: {
            "memory_backend": "chroma",
            "chroma_persist_dir": "./chroma_db",
            "memory_min_similarity": 0.30,
            "postgres_url": "",
            "redis_url": "",
        },
    )
    monkeypatch.setattr(api_server, "_tcp_reachable_from_url", lambda _u: None)
    monkeypatch.setattr(api_server, "_module_available", lambda _n: False)

    diag = api_server._runtime_diagnostics()
    seeded = diag["memory"]["seeded"]
    assert all(v is False for v in seeded.values())
    assert "chroma_collections_unseeded" in diag["degraded_reasons"]
    assert diag["degraded"] is True


def test_min_similarity_surfaced_from_config(monkeypatch):
    monkeypatch.setattr(
        api_server,
        "_chroma_persistence_diagnostics",
        lambda _p: {"persist_dir": None, "persistent": False,
                    "collection_counts": None, "total_documents": None},
    )
    monkeypatch.setattr(
        api_server,
        "_postgres_audit_diagnostics",
        lambda _u: {"audit_write_lag_seconds": None, "backtest_runs_count": None, "reachable": False},
    )
    monkeypatch.setattr(
        api_server,
        "get_config",
        lambda: {
            "memory_backend": "chroma",
            "chroma_persist_dir": "",
            "memory_min_similarity": 0.45,
            "postgres_url": "",
            "redis_url": "",
        },
    )
    monkeypatch.setattr(api_server, "_tcp_reachable_from_url", lambda _u: None)
    monkeypatch.setattr(api_server, "_module_available", lambda _n: False)

    diag = api_server._runtime_diagnostics()
    assert diag["memory"]["min_similarity"] == pytest.approx(0.45)


# ─── _postgres_audit_diagnostics direct ───────────────────────────────────────


def test_postgres_audit_diagnostics_empty_when_url_unset():
    info = api_server._postgres_audit_diagnostics("")
    assert info["audit_write_lag_seconds"] is None
    assert info["backtest_runs_count"] is None
    assert info["reachable"] is False


def test_postgres_audit_diagnostics_returns_data_under_mock(monkeypatch):
    """When the pool is available, two SELECTs populate lag + backtest_runs_count."""
    from tradingagents.db import audit_writer as _aw  # noqa: F401 — keep module path live

    fake_cursor = MagicMock()
    # Two successive fetchone() return values for the two SELECTs.
    fake_cursor.fetchone.side_effect = [(42,), (7,)]

    fake_cm = MagicMock()
    fake_cm.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cm.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr(
        "tradingagents.db.connection.is_postgres_available", lambda: True
    )
    # _postgres_audit_diagnostics imports db_cursor from tradingagents.db at
    # call time, so patching the module-level symbol is sufficient.
    import tradingagents.db as tdb
    monkeypatch.setattr(tdb, "cursor", lambda **kw: fake_cm)
    monkeypatch.setattr(tdb, "is_postgres_available", lambda: True)

    info = api_server._postgres_audit_diagnostics("postgresql://localhost/x")
    assert info["audit_write_lag_seconds"] == 42
    assert info["backtest_runs_count"] == 7
    assert info["reachable"] is True


def test_postgres_audit_diagnostics_handles_empty_table(monkeypatch):
    """When analysis_sessions is empty, MAX(created_at) → None → lag stays None."""
    fake_cursor = MagicMock()
    fake_cursor.fetchone.side_effect = [(None,), (0,)]

    fake_cm = MagicMock()
    fake_cm.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cm.__exit__ = MagicMock(return_value=False)

    import tradingagents.db as tdb
    monkeypatch.setattr(tdb, "cursor", lambda **kw: fake_cm)
    monkeypatch.setattr(tdb, "is_postgres_available", lambda: True)

    info = api_server._postgres_audit_diagnostics("postgresql://localhost/x")
    assert info["audit_write_lag_seconds"] is None
    assert info["backtest_runs_count"] == 0
    assert info["reachable"] is True


def test_postgres_audit_diagnostics_swallows_cursor_exception(monkeypatch):
    """A query failure must NOT crash /api/health — just records the error."""
    class _BrokenCursor:
        def execute(self, *a, **k):
            raise RuntimeError("table missing")

    fake_cm = MagicMock()
    fake_cm.__enter__ = MagicMock(return_value=_BrokenCursor())
    fake_cm.__exit__ = MagicMock(return_value=False)

    import tradingagents.db as tdb
    monkeypatch.setattr(tdb, "cursor", lambda **kw: fake_cm)
    monkeypatch.setattr(tdb, "is_postgres_available", lambda: True)

    info = api_server._postgres_audit_diagnostics("postgresql://localhost/x")
    assert info["audit_write_lag_seconds"] is None
    assert info["backtest_runs_count"] is None
    assert "error" in info


# ─── integrated /api/health endpoint contract ─────────────────────────────────


def test_full_diagnostics_contract_surface(monkeypatch):
    """The diagnostics dict must expose every documented field for the
    dashboard / operator tooling that depends on it."""
    monkeypatch.setattr(
        api_server,
        "_chroma_persistence_diagnostics",
        lambda _p: {
            "persist_dir": "./chroma_db",
            "persistent": True,
            "collection_counts": {n: 6 for n in api_server._CHROMA_AGENT_COLLECTIONS},
            "total_documents": 30,
        },
    )
    monkeypatch.setattr(
        api_server,
        "_postgres_audit_diagnostics",
        lambda _u: {
            "audit_write_lag_seconds": 123,
            "backtest_runs_count": 7,
            "reachable": True,
        },
    )
    monkeypatch.setattr(
        api_server,
        "get_config",
        lambda: {
            "memory_backend": "chroma",
            "chroma_persist_dir": "./chroma_db",
            "memory_min_similarity": 0.30,
            "postgres_url": "postgresql://localhost/x",
            "redis_url": "",
        },
    )
    monkeypatch.setattr(api_server, "_tcp_reachable_from_url", lambda _u: True)
    monkeypatch.setattr(api_server, "_module_available", lambda _n: True)

    diag = api_server._runtime_diagnostics()

    # The full contract — surface this list to keep the dashboard/operator
    # docs honest if the schema evolves.
    assert set(diag["memory"]).issuperset({
        "backend", "vector_store", "postgres_vector_required",
        "chroma_persist_dir", "chroma_persistent", "chroma_collection_counts",
        "chroma_total_documents", "seeded", "min_similarity",
    })
    assert set(diag["postgres"]).issuperset({
        "configured", "reachable", "audit_write_lag_seconds",
        "backtest_runs_count", "purpose",
    })
    assert set(diag["redis"]).issuperset({
        "configured", "package_available", "reachable", "purpose",
    })
    assert "degraded" in diag
    assert "degraded_reasons" in diag

    # Specific values from this run.
    assert diag["postgres"]["audit_write_lag_seconds"] == 123
    assert diag["postgres"]["backtest_runs_count"] == 7
    assert all(diag["memory"]["seeded"].values())  # all 5 collections seeded
    # Persistent + seeded → no chroma-related degraded reasons.
    assert "chroma_memory_in_memory_only" not in diag["degraded_reasons"]
    assert "chroma_collections_unseeded" not in diag["degraded_reasons"]
