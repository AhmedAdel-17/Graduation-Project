"""
Structured JSON logging with correlation context for EGX Trading Agents.

Provides:
  - JSON-formatted log output (via python-json-logger)
  - Automatic injection of session_id, ticker, trade_date into every log record
  - Context propagation via contextvars (works in both threads and asyncio)

Usage:
  from tradingagents.observability import setup_logging, set_trace_context

  setup_logging(json_output=True)
  set_trace_context(session_id="abc", ticker="COMI.CA", trade_date="2024-06-01")
  logger.info("pipeline complete")  # includes session_id, ticker, trade_date
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional

try:
    from pythonjsonlogger import jsonlogger
    _HAS_JSON_LOGGER = True
except ImportError:
    _HAS_JSON_LOGGER = False


@dataclass
class TraceContext:
    """Correlation context injected into every log record."""
    session_id: str = ""
    ticker: str = ""
    trade_date: str = ""


_trace_ctx: ContextVar[TraceContext] = ContextVar("trace_ctx", default=TraceContext())


def set_trace_context(
    session_id: str = "",
    ticker: str = "",
    trade_date: str = "",
) -> None:
    """Set the current trace context. Call at the top of propagate() or API handlers."""
    _trace_ctx.set(TraceContext(session_id=session_id, ticker=ticker, trade_date=trade_date))


def get_trace_context() -> TraceContext:
    """Get the current trace context."""
    return _trace_ctx.get()


class _ContextFilter(logging.Filter):
    """Injects TraceContext fields into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = _trace_ctx.get()
        record.session_id = ctx.session_id  # type: ignore[attr-defined]
        record.ticker = ctx.ticker  # type: ignore[attr-defined]
        record.trade_date = ctx.trade_date  # type: ignore[attr-defined]
        return True


def setup_logging(
    level: str = "INFO",
    json_output: bool = True,
    log_file: Optional[str] = None,
) -> None:
    """
    Configure structured logging for the application.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR).
        json_output: If True and python-json-logger is installed, emit JSON logs.
            Falls back to text format if the library is not available.
        log_file: Optional path to write JSON logs. Defaults to env var
            TRADINGAGENTS_LOG_FILE (./logs/tradingagents.log if set to "auto").
            Set to empty string or "none" to disable file logging.
    """
    import os

    # Resolve log_file from env if not explicitly passed
    if log_file is None:
        env_log = os.environ.get("TRADINGAGENTS_LOG_FILE", "")
        if env_log.lower() == "auto":
            log_file = "./logs/tradingagents.log"
        elif env_log and env_log.lower() != "none":
            log_file = env_log

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to prevent duplicates on re-init
    root.handlers.clear()

    # Context filter for correlation IDs
    ctx_filter = _ContextFilter()

    if json_output and _HAS_JSON_LOGGER:
        formatter = jsonlogger.JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s %(session_id)s %(ticker)s %(trade_date)s",
            rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

    # Console handler
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    console.addFilter(ctx_filter)
    root.addHandler(console)

    # File handler for Promtail log shipping (Phase 3)
    if log_file:
        import pathlib
        pathlib.Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=10_000_000, backupCount=5
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(ctx_filter)
        root.addHandler(file_handler)
