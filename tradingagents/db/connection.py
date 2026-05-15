"""Pooled Postgres connection layer.

Single process-wide ``psycopg2.pool.ThreadedConnectionPool`` keyed off the
``POSTGRES_URL`` env var, with a context-manager ``cursor()`` helper that
auto-commits on success / rolls back on exception / returns the connection
to the pool either way.

All public helpers degrade gracefully when Postgres is unreachable: they
return ``None`` / ``False`` and log at WARNING. ``cursor()`` is the one
exception — it raises ``RuntimeError`` if invoked when the pool is
unavailable, so call sites must either guard with ``is_postgres_available()``
or catch the error.
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

logger = logging.getLogger("tradingagents.db")

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool

    _PSYCOPG2_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised only when extra not installed
    psycopg2 = None  # type: ignore[assignment]
    _PSYCOPG2_AVAILABLE = False
    logger.info(
        "psycopg2 not installed; Postgres-backed features disabled. "
        "Install via: pip install -e .[postgres]"
    )

try:
    from pgvector.psycopg2 import register_vector as _register_vector

    _PGVECTOR_AVAILABLE = True
except ImportError:  # pragma: no cover
    _register_vector = None  # type: ignore[assignment]
    _PGVECTOR_AVAILABLE = False


_MIN_CONN = 1
_MAX_CONN = 10

_POOL: Optional[Any] = None
_POOL_LOCK = threading.Lock()
_POOL_FAILED = False


def get_postgres_url() -> Optional[str]:
    """Return the configured ``POSTGRES_URL`` or ``None`` when unset/blank."""
    url = os.environ.get("POSTGRES_URL", "").strip()
    return url or None


def is_postgres_available() -> bool:
    """Return True iff the driver is importable, a URL is set, and the pool
    can be (or has been) initialized. Cheap on repeated calls.
    """
    if not _PSYCOPG2_AVAILABLE:
        return False
    if get_postgres_url() is None:
        return False
    if _POOL_FAILED:
        return False
    return get_pool() is not None


def get_pool() -> Optional[Any]:
    """Return the singleton ``ThreadedConnectionPool``, initializing on first
    use. Returns ``None`` when Postgres is unavailable. Never raises.
    """
    global _POOL, _POOL_FAILED

    if not _PSYCOPG2_AVAILABLE or _POOL_FAILED:
        return None
    if _POOL is not None:
        return _POOL

    url = get_postgres_url()
    if url is None:
        return None

    with _POOL_LOCK:
        if _POOL is not None:
            return _POOL
        if _POOL_FAILED:
            return None
        try:
            _POOL = psycopg2.pool.ThreadedConnectionPool(_MIN_CONN, _MAX_CONN, url)
            logger.info(
                "Postgres pool initialized (min=%d, max=%d)", _MIN_CONN, _MAX_CONN
            )
            return _POOL
        except Exception as exc:
            _POOL_FAILED = True
            logger.warning(
                "Postgres pool init failed; backend disabled for this process: %s", exc
            )
            return None


def ping() -> bool:
    """Round-trip ``SELECT 1`` through the pool. Returns False on any failure."""
    if not is_postgres_available():
        return False
    try:
        with cursor() as cur:
            cur.execute("SELECT 1;")
            cur.fetchone()
        return True
    except Exception as exc:
        logger.warning("Postgres ping failed: %s", exc)
        return False


@contextmanager
def cursor(
    *, dict_cursor: bool = False, register_pgvector: bool = False
) -> Iterator[Any]:
    """Borrow a connection from the pool and yield a cursor.

    - Commits on normal exit; rolls back on exception; always returns the
      connection to the pool.
    - ``dict_cursor=True`` yields a ``psycopg2.extras.DictCursor``.
    - ``register_pgvector=True`` installs the pgvector adapter on the
      borrowed connection (no-op when pgvector is not importable).
    - Raises ``RuntimeError`` if the pool is unavailable — callers wanting
      graceful degradation should check ``is_postgres_available()`` first
      or catch the error.
    """
    pool = get_pool()
    if pool is None:
        raise RuntimeError("Postgres pool unavailable")

    conn = pool.getconn()
    try:
        if register_pgvector and _PGVECTOR_AVAILABLE:
            try:
                _register_vector(conn)
            except Exception as exc:  # pragma: no cover
                logger.warning("pgvector adapter registration failed: %s", exc)

        cur = (
            conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            if dict_cursor
            else conn.cursor()
        )
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            try:
                cur.close()
            except Exception:  # pragma: no cover
                pass
    finally:
        try:
            pool.putconn(conn)
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed returning connection to pool: %s", exc)


def reset_pool() -> None:
    """Close all pooled connections and clear the singleton.

    Primarily for tests. Production callers should not need this.
    """
    global _POOL, _POOL_FAILED
    with _POOL_LOCK:
        if _POOL is not None:
            try:
                _POOL.closeall()
            except Exception as exc:  # pragma: no cover
                logger.warning("Error closing Postgres pool: %s", exc)
        _POOL = None
        _POOL_FAILED = False
