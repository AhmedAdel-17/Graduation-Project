"""DB layer: centralized Postgres access.

This package owns every Postgres connection in the project. All writers
(persistent agent memory, audit, backtest persistence) MUST route through
``get_pool()`` / ``cursor()`` instead of opening raw ``psycopg2.connect()``
calls.

Graceful by design: when Postgres is unavailable for any reason (driver
missing, ``POSTGRES_URL`` unset, connection refused) every helper returns
``None`` or ``False`` and logs a single warning. Nothing here raises into
the agent graph.
"""

from tradingagents.db.connection import (
    cursor,
    get_pool,
    get_postgres_url,
    is_postgres_available,
    ping,
    reset_pool,
)

__all__ = [
    "cursor",
    "get_pool",
    "get_postgres_url",
    "is_postgres_available",
    "ping",
    "reset_pool",
]
