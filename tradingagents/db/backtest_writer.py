"""Backtester persistence: writes one ``backtest_runs`` row + bulk
``backtest_trades`` rows per run.

Public helpers:

- ``write_backtest_run(run_id, ticker, strategy, start_date, end_date,
  metrics, config)`` — inserts one ``backtest_runs`` row. Metric values
  arrive as strings from ``_calculate_metrics`` (``"1.72%"``, ``"1.25"``,
  ``"1,017,197.11 EGP"``) and are parsed into NUMERIC columns.

- ``write_backtest_trades(run_id, trades)`` — bulk-insert via
  ``psycopg2.extras.execute_values``. Maps the in-memory trade dict shape
  to the ``backtest_trades`` columns.

Both helpers:

- Short-circuit when Postgres is unavailable (``is_postgres_available()``).
- Wrap inserts in the centralized pool's transaction context manager.
- Catch all exceptions, log a warning, and return ``False`` / ``0``.
- Never raise into the caller (the backtester JSON report path remains
  the source of truth on disk even if Postgres is offline).

Schema requirements (db_schema.sql + scripts/db/apply_schema_v2.sql):
``backtest_runs(run_id UNIQUE, ticker, strategy, start_date, end_date,
total_return_pct, benchmark_return_pct, alpha_pct, sharpe_ratio,
calmar_ratio, max_drawdown_pct, win_rate_pct, total_trades,
total_commissions, final_portfolio_egp, metrics JSONB, created_at)``
and ``backtest_trades(run_id FK, trade_date, action, shares, price_egp,
value_egp, commission_egp, portfolio_value, signal, confidence, notes)``.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from tradingagents.db import cursor as db_cursor
from tradingagents.db import is_postgres_available

logger = logging.getLogger("tradingagents.db.backtest")

# Strip non-numeric characters (commas, currency suffix, whitespace) but keep
# leading sign and decimal point.
_NUMERIC_RE = re.compile(r"[^\d.\-+]")


def _parse_percent(value: Any) -> Optional[float]:
    """Parse '1.72%' / '-3.10%' / '0' / 1.72 into a float. None for N/A."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s or s.upper() in {"N/A", "NA", "NONE"}:
        return None
    s = s.replace("%", "").strip()
    try:
        return float(_NUMERIC_RE.sub("", s))
    except (ValueError, TypeError):
        return None


def _parse_float(value: Any) -> Optional[float]:
    """Parse '1.25' / '1,017,197.11 EGP' / 1.25 into a float. None on failure."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s or s.upper() in {"N/A", "NA", "NONE"}:
        return None
    try:
        return float(_NUMERIC_RE.sub("", s))
    except (ValueError, TypeError):
        return None


def _parse_int(value: Any) -> Optional[int]:
    f = _parse_float(value)
    return int(f) if f is not None else None


def _to_jsonb(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps({"_unserializable": str(value)})


def _coerce_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def write_backtest_run(
    *,
    run_id: str,
    ticker: str,
    strategy: str,
    start_date: Any,
    end_date: Any,
    metrics: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
) -> bool:
    """Insert one row into ``backtest_runs``. Returns True on success.

    Metric keys recognised (matches ``_calculate_metrics()`` output):
    ``Total Return``, ``Benchmark Return``, ``Alpha``, ``Sharpe Ratio``,
    ``Calmar Ratio``, ``Max Drawdown``, ``Win Rate``, ``Total Trades``,
    ``Total Commissions``, ``Final Portfolio``.

    Unparseable values become SQL NULL — never aborts the write.
    """
    if not is_postgres_available():
        return False

    sd = _coerce_date(start_date)
    ed = _coerce_date(end_date)
    if sd is None or ed is None:
        logger.warning(
            "backtest write skipped: invalid start/end date (%r → %r → %r)",
            start_date, sd, end_date,
        )
        return False

    metrics = metrics or {}

    try:
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO backtest_runs (
                    run_id, ticker, strategy, start_date, end_date,
                    total_return_pct, benchmark_return_pct, alpha_pct,
                    sharpe_ratio, calmar_ratio, max_drawdown_pct, win_rate_pct,
                    total_trades, total_commissions, final_portfolio_egp,
                    metrics, user_id
                )
                VALUES (%s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s::jsonb, %s)
                ON CONFLICT (run_id) DO NOTHING
                """,
                (
                    run_id,
                    ticker,
                    strategy,
                    sd,
                    ed,
                    _parse_percent(metrics.get("Total Return")),
                    _parse_percent(metrics.get("Benchmark Return")),
                    _parse_percent(metrics.get("Alpha")),
                    _parse_float(metrics.get("Sharpe Ratio")),
                    _parse_float(metrics.get("Calmar Ratio")),
                    _parse_percent(metrics.get("Max Drawdown")),
                    _parse_percent(metrics.get("Win Rate")),
                    _parse_int(metrics.get("Total Trades")),
                    _parse_float(metrics.get("Total Commissions")),
                    _parse_float(metrics.get("Final Portfolio")),
                    _to_jsonb({**metrics, "_config_snapshot": _config_snapshot(config)}),
                    user_id,
                ),
            )
        logger.debug("backtest: backtest_runs row written for run_id=%s", run_id)
        return True
    except Exception as exc:
        logger.warning(
            "backtest write failed (backtest_runs, run_id=%s): %s", run_id, exc
        )
        return False


def _config_snapshot(config: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Project the parts of the runtime config worth preserving in the
    ``metrics`` JSONB blob for later reproducibility audit."""
    if not config:
        return None
    keys = (
        "llm_provider",
        "deep_think_llm",
        "quick_think_llm",
        "backend_url",
        "target_market",
        "backtest_mode",
        "egx_risk_free_rate",
        "max_position_pct_adv",
        "daily_price_limit_pct",
    )
    return {k: config.get(k) for k in keys if k in config}


def _build_trade_rows(
    *,
    run_id: str,
    trades: List[Dict[str, Any]],
    daily_portfolio: Optional[List[Dict[str, Any]]] = None,
) -> List[tuple]:
    """Map in-memory trade dicts to ``backtest_trades`` row tuples.

    Pulled out so we can unit-test row construction without a database.
    """
    # Build a date → portfolio_value lookup for the portfolio_value column.
    pv_by_date: Dict[str, float] = {}
    if daily_portfolio:
        for entry in daily_portfolio:
            d = entry.get("date")
            pv = entry.get("portfolio_value")
            if pv is None:
                pv = entry.get("equity") or entry.get("value")
            if d and pv is not None:
                try:
                    pv_by_date[str(d)] = float(pv)
                except (TypeError, ValueError):
                    continue

    rows: List[tuple] = []
    for t in trades or []:
        td = _coerce_date(t.get("date"))
        if td is None:
            continue  # skip malformed entries; logged at INSERT time would be noisy
        confidence = _parse_float(t.get("confidence"))
        notes = t.get("reasoning")
        if isinstance(notes, str) and len(notes) > 2000:
            notes = notes[:2000] + "…"
        rows.append(
            (
                run_id,
                td,
                t.get("action"),
                _parse_float(t.get("shares")),
                _parse_float(t.get("exec_price") or t.get("close_price")),
                _parse_float(t.get("value")),
                _parse_float(t.get("commission")),
                pv_by_date.get(str(t.get("date"))),
                t.get("action"),  # signal mirrors action for now (LLM not parsed yet)
                confidence,
                notes,
            )
        )
    return rows


def write_backtest_trades(
    *,
    run_id: str,
    trades: List[Dict[str, Any]],
    daily_portfolio: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """Bulk-insert one row per trade. Returns the number of rows written.

    ``daily_portfolio`` is optional; when provided, the daily portfolio
    value for each trade's date is recorded in the ``portfolio_value``
    column (useful for equity-curve reconstruction).
    """
    if not is_postgres_available():
        return 0

    rows = _build_trade_rows(
        run_id=run_id, trades=trades, daily_portfolio=daily_portfolio
    )
    if not rows:
        return 0

    try:
        import psycopg2.extras as _pg_extras

        with db_cursor() as cur:
            _pg_extras.execute_values(
                cur,
                """
                INSERT INTO backtest_trades (
                    run_id, trade_date, action, shares, price_egp, value_egp,
                    commission_egp, portfolio_value, signal, confidence, notes
                )
                VALUES %s
                """,
                rows,
                template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            )
        logger.debug(
            "backtest: %d backtest_trades rows written for run_id=%s", len(rows), run_id
        )
        return len(rows)
    except Exception as exc:
        logger.warning(
            "backtest write failed (backtest_trades, run_id=%s): %s", run_id, exc
        )
        return 0
