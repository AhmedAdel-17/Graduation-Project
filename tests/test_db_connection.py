"""Tests for tradingagents.db.connection — the pooled Postgres entry point.

Unit tests only; no real database required. The ``ThreadedConnectionPool``
constructor is monkeypatched per-test to simulate available / unavailable
backends.
"""

from __future__ import annotations

import pytest

from tradingagents.db import connection as db_conn


@pytest.fixture(autouse=True)
def _reset_pool_state(monkeypatch):
    """Each test starts with a clean module-level singleton and no URL set."""
    db_conn._POOL = None
    db_conn._POOL_FAILED = False
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    yield
    db_conn._POOL = None
    db_conn._POOL_FAILED = False


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._fetched = False

    def execute(self, *_args, **_kwargs):
        return None

    def fetchone(self):
        self._fetched = True
        return (1,)

    def close(self):
        self._conn.cursor_closed = True


class _FakeConn:
    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.cursor_closed = False

    def cursor(self, cursor_factory=None):  # noqa: ARG002 — match psycopg2 sig
        return _FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _FakePool:
    instances = 0
    closed_all = 0

    def __init__(self, *_args, **_kwargs):
        type(self).instances += 1
        self.conns = []

    def getconn(self):
        conn = _FakeConn()
        self.conns.append(conn)
        return conn

    def putconn(self, conn):
        # In a real pool the conn returns to the pool; we just record release.
        conn.released = True

    def closeall(self):
        type(self).closed_all += 1


@pytest.fixture
def fake_pool(monkeypatch):
    """Patch ``psycopg2.pool.ThreadedConnectionPool`` with a recording fake."""
    if not db_conn._PSYCOPG2_AVAILABLE:
        pytest.skip("psycopg2 not installed")
    _FakePool.instances = 0
    _FakePool.closed_all = 0
    monkeypatch.setattr(
        db_conn.psycopg2.pool, "ThreadedConnectionPool", _FakePool
    )
    return _FakePool


# ── get_postgres_url ───────────────────────────────────────────────────────────


def test_get_postgres_url_returns_none_when_unset():
    assert db_conn.get_postgres_url() is None


def test_get_postgres_url_strips_whitespace(monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", "  postgresql://localhost/test  ")
    assert db_conn.get_postgres_url() == "postgresql://localhost/test"


def test_get_postgres_url_empty_value_treated_as_unset(monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", "    ")
    assert db_conn.get_postgres_url() is None


# ── get_pool / is_postgres_available ──────────────────────────────────────────


def test_get_pool_returns_none_when_url_unset():
    assert db_conn.get_pool() is None
    assert db_conn.is_postgres_available() is False


def test_get_pool_returns_singleton(monkeypatch, fake_pool):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/x")

    pool1 = db_conn.get_pool()
    pool2 = db_conn.get_pool()

    assert pool1 is not None
    assert pool1 is pool2
    assert fake_pool.instances == 1


def test_get_pool_returns_none_on_init_failure_and_stays_sticky(monkeypatch):
    if not db_conn._PSYCOPG2_AVAILABLE:
        pytest.skip("psycopg2 not installed")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://invalid:5432/none")

    def _raising_pool(*_a, **_kw):
        raise OSError("connection refused")

    monkeypatch.setattr(
        db_conn.psycopg2.pool, "ThreadedConnectionPool", _raising_pool
    )

    assert db_conn.get_pool() is None
    assert db_conn._POOL_FAILED is True
    # Sticky: subsequent calls do not retry the constructor.
    assert db_conn.get_pool() is None
    assert db_conn.is_postgres_available() is False


def test_is_postgres_available_true_when_pool_init_succeeds(
    monkeypatch, fake_pool
):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/x")
    assert db_conn.is_postgres_available() is True


# ── cursor() context manager ──────────────────────────────────────────────────


def test_cursor_raises_runtime_error_when_pool_unavailable():
    with pytest.raises(RuntimeError, match="Postgres pool unavailable"):
        with db_conn.cursor():
            pass  # pragma: no cover — block should not enter


def test_cursor_commits_on_success(monkeypatch, fake_pool):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/x")

    with db_conn.cursor() as cur:
        cur.execute("SELECT 1;")
        cur.fetchone()

    pool = db_conn.get_pool()
    conn = pool.conns[0]
    assert conn.committed is True
    assert conn.rolled_back is False
    assert conn.cursor_closed is True
    assert getattr(conn, "released", False) is True


def test_cursor_rolls_back_on_exception(monkeypatch, fake_pool):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/x")

    with pytest.raises(ValueError, match="boom"):
        with db_conn.cursor() as cur:
            cur.execute("SELECT 1;")
            raise ValueError("boom")

    pool = db_conn.get_pool()
    conn = pool.conns[0]
    assert conn.committed is False
    assert conn.rolled_back is True
    assert getattr(conn, "released", False) is True


def test_cursor_supports_dict_cursor_flag(monkeypatch, fake_pool):
    """dict_cursor=True passes a DictCursor factory through to the conn."""
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/x")

    factories_seen = []
    original_cursor = _FakeConn.cursor

    def _spy_cursor(self, cursor_factory=None):
        factories_seen.append(cursor_factory)
        return original_cursor(self, cursor_factory=cursor_factory)

    monkeypatch.setattr(_FakeConn, "cursor", _spy_cursor)

    with db_conn.cursor(dict_cursor=True) as cur:
        cur.execute("SELECT 1;")

    assert len(factories_seen) == 1
    assert factories_seen[0] is db_conn.psycopg2.extras.DictCursor


# ── ping() ─────────────────────────────────────────────────────────────────────


def test_ping_returns_false_when_pool_unavailable():
    assert db_conn.ping() is False


def test_ping_returns_true_when_pool_ok(monkeypatch, fake_pool):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/x")
    assert db_conn.ping() is True


# ── reset_pool ─────────────────────────────────────────────────────────────────


def test_reset_pool_closes_and_clears_singleton(monkeypatch, fake_pool):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/x")

    pool1 = db_conn.get_pool()
    assert pool1 is not None

    db_conn.reset_pool()
    assert fake_pool.closed_all == 1
    assert db_conn._POOL is None
    assert db_conn._POOL_FAILED is False

    # A new pool can be allocated after reset.
    pool2 = db_conn.get_pool()
    assert pool2 is not None
    assert pool2 is not pool1
    assert fake_pool.instances == 2
