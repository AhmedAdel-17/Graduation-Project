"""
SQLite database for the dashboard backend.

Tables:
  users       — auth + preferences
  runs        — single predictions (one TradingAgentsGraph run)
  backtests   — multi-date backtest runs
  jobs        — track async job status (predict/backtest)

We use SQLite for zero-setup MVP. Migrate to Postgres later by changing the
SQLAlchemy URL and running Alembic migrations.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.environ.get("DASHBOARD_DB_PATH", str(PROJECT_ROOT / "dashboard.db"))
DB_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    sectors_of_interest = Column(Text, default="[]")   # JSON array
    alerts_enabled = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_now)

    def to_dict(self) -> Dict[str, Any]:
        try:
            sectors = json.loads(self.sectors_of_interest or "[]")
        except json.JSONDecodeError:
            sectors = []
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "sectors_of_interest": sectors,
            "alerts_enabled": bool(self.alerts_enabled),
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }


class Run(Base):
    """A single-date prediction (one TradingAgentsGraph.propagate() call)."""
    __tablename__ = "runs"
    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    type = Column(String, default="prediction")  # always "prediction"
    ticker = Column(String, nullable=False, index=True)
    trade_date = Column(String, nullable=False)
    portfolio_value = Column(Float, default=1_000_000.0)

    status = Column(String, default="running")   # running | done | failed
    decision = Column(String, nullable=True)     # BUY | SELL | HOLD
    confidence = Column(Float, nullable=True)    # 0-1
    rationale = Column(Text, nullable=True)

    # Full payload as JSON: macro_context, agents[], bull_thesis, bear_thesis,
    # execution_plan, risk_assessment
    payload = Column(Text, default="{}")
    error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=_now)
    finished_at = Column(DateTime, nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        try:
            payload = json.loads(self.payload or "{}")
        except json.JSONDecodeError:
            payload = {}
        return {
            "id": self.id,
            "user_id": self.user_id,
            "type": self.type,
            "ticker": self.ticker,
            "trade_date": self.trade_date,
            "portfolio_value": self.portfolio_value,
            "status": self.status,
            "decision": self.decision,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "macro_context": payload.get("macro_context", {}),
            "agents": payload.get("agents", []),
            "bull_thesis": payload.get("bull_thesis"),
            "bear_thesis": payload.get("bear_thesis"),
            "execution_plan": payload.get("execution_plan"),
            "risk_assessment": payload.get("risk_assessment"),
            "error": self.error,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "finished_at": self.finished_at.isoformat() + "Z" if self.finished_at else None,
        }


class Backtest(Base):
    """A multi-date backtest run."""
    __tablename__ = "backtests"
    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    type = Column(String, default="backtest")
    ticker = Column(String, nullable=False, index=True)
    start_date = Column(String, nullable=False)
    end_date = Column(String, nullable=False)
    interval_days = Column(Integer, default=14)
    capital = Column(Float, default=1_000_000.0)
    analysts = Column(Text, default='["market","fundamentals","news","social"]')  # JSON array

    status = Column(String, default="running")

    # Result JSON blobs
    trades = Column(Text, default="[]")
    equity_curve = Column(Text, default="[]")
    metrics = Column(Text, default="{}")
    error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=_now)
    finished_at = Column(DateTime, nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        try:
            analysts = json.loads(self.analysts or "[]")
            trades = json.loads(self.trades or "[]")
            equity_curve = json.loads(self.equity_curve or "[]")
            metrics = json.loads(self.metrics or "{}")
        except json.JSONDecodeError:
            analysts, trades, equity_curve, metrics = [], [], [], {}
        return {
            "id": self.id,
            "user_id": self.user_id,
            "type": self.type,
            "ticker": self.ticker,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "interval_days": self.interval_days,
            "capital": self.capital,
            "analysts": analysts,
            "status": self.status,
            "trades": trades,
            "equity_curve": equity_curve,
            "metrics": metrics,
            "error": self.error,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "finished_at": self.finished_at.isoformat() + "Z" if self.finished_at else None,
        }


class Job(Base):
    """Tracks an in-flight async job (prediction or backtest)."""
    __tablename__ = "jobs"
    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String, nullable=False)        # "prediction" | "backtest"
    status = Column(String, default="pending")   # pending | running | done | failed | cancelled
    run_id = Column(String, ForeignKey("runs.id"), nullable=True)
    backtest_id = Column(String, ForeignKey("backtests.id"), nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_now)
    finished_at = Column(DateTime, nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status,
            "run_id": self.run_id,
            "backtest_id": self.backtest_id,
            "error": self.error,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "finished_at": self.finished_at.isoformat() + "Z" if self.finished_at else None,
        }


# ---------------------------------------------------------------------------
# Init + session helper
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)


def get_session() -> Session:
    """FastAPI dependency: yield a SQLAlchemy session, then close it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
