"""
TradingAgents Dashboard — FastAPI Backend Server

Wraps the existing TradingAgentsGraph and exposes:
  - REST endpoints for config, stock data, indicators, fundamentals, news, results
  - WebSocket endpoint for real-time analysis streaming

No existing backend files are modified.
"""

import os
import sys
import json
import asyncio
import logging
import importlib.util
import re
import socket
import time

# Load .env before any tradingagents import so keys are available at module init
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass
import traceback
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager
from urllib.parse import urlparse

logger = logging.getLogger("tradingagents.api_server")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Add project root to path so we can import tradingagents
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tradingagents.dataflows.config import get_config, set_config
from tradingagents.dataflows.interface import route_to_vendor, TOOLS_CATEGORIES, VENDOR_LIST
from tradingagents.graph.trading_graph import TradingAgentsGraph

# Live broadcast hub — lets the admin dashboard watch ANY run (main dashboard or
# admin) in real time. Best-effort; never affects a run.
try:
    from server.live_hub import live_hub
except Exception:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from live_hub import live_hub  # type: ignore

# Try importing EGX-specific tools
try:
    from tradingagents.dataflows.local import (
        get_egx_fundamentals_summary,
        get_egx_news_combined,
    )
    EGX_TOOLS_AVAILABLE = True
except ImportError:
    EGX_TOOLS_AVAILABLE = False

# Try importing risk limits
try:
    from tradingagents.agents.managers.risk_manager import EGX_RISK_LIMITS
except ImportError:
    EGX_RISK_LIMITS = {}

# region agent log (debug-mode instrumentation)
DEBUG_LOG_PATH = PROJECT_ROOT / "debug-e61002.log"
DEBUG_SESSION_ID = "e61002"

def _dbg_log(*, hypothesis_id: str, location: str, message: str, data: Dict[str, Any] | None = None, run_id: str = "pre-fix") -> None:
    """
    Debug-mode NDJSON logger (writes to workspace debug-e61002.log).
    Avoid secrets/PII. Best-effort: never raise.
    """
    try:
        rec = {
            "sessionId": DEBUG_SESSION_ID,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(datetime.utcnow().timestamp() * 1000),
        }
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
# endregion agent log (debug-mode instrumentation)

def _normalize_ticker(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if not t:
        return t
    return t if t.endswith(".CA") else f"{t}.CA"

def _parse_percent(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        s = s.replace("%", "").replace(",", "")
        # Tolerate appended context like "60.00% [95% CI 30.0%–80.0%]".
        # We only care about the leading percentage number.
        parts = s.split()
        if parts:
            s = parts[0]
        try:
            return float(s)
        except ValueError:
            return None
    return None

def _parse_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if not s:
            return None
        # Strip currency suffixes
        s = s.replace("EGP", "").strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _tcp_reachable_from_url(url: str, *, timeout_seconds: float = 0.2) -> Optional[bool]:
    if not url:
        return None
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port
    if not host:
        return None
    if port is None:
        if parsed.scheme.startswith("postgres"):
            port = 5432
        elif parsed.scheme.startswith("redis"):
            port = 6379
        else:
            return None
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


_CHROMA_AGENT_COLLECTIONS = (
    "bull_memory",
    "bear_memory",
    "trader_memory",
    "invest_judge_memory",
    "risk_manager_memory",
)


def _seed_count_per_collection() -> Dict[str, int]:
    """Expected seed count per collection name. Used by /api/health to surface
    a ``memory.seeded`` boolean — True when the collection has at least the
    seeded number of rows. Best-effort; on any import failure returns an empty
    dict (callers default to False).
    """
    try:
        from tradingagents.agents.utils.seed_memories import EGX_SEED_MEMORIES
    except Exception:  # pragma: no cover — defensive
        return {}
    counts: Dict[str, int] = {}
    for entry in EGX_SEED_MEMORIES:
        name = entry.get("agent_name")
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _postgres_audit_diagnostics(postgres_url: str) -> Dict[str, Any]:
    """Inspect the audit + backtest tables. Best-effort, fully guarded — a
    failure here never crashes /api/health.

    Returns a dict with:
      - audit_write_lag_seconds: int | None — seconds since most recent
        analysis_sessions row, or None when the table is empty / unreachable.
      - backtest_runs_count: int | None — informational count.
      - reachable: bool
    """
    info: Dict[str, Any] = {
        "audit_write_lag_seconds": None,
        "backtest_runs_count": None,
        "reachable": False,
    }
    if not postgres_url:
        return info
    try:
        from tradingagents.db import cursor as db_cursor
        from tradingagents.db import is_postgres_available

        if not is_postgres_available():
            return info

        with db_cursor() as cur:
            cur.execute(
                """
                SELECT EXTRACT(EPOCH FROM (NOW() - MAX(created_at)))::int AS lag_seconds
                FROM analysis_sessions;
                """
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                info["audit_write_lag_seconds"] = int(row[0])

            cur.execute("SELECT COUNT(*) FROM backtest_runs;")
            row = cur.fetchone()
            if row is not None:
                info["backtest_runs_count"] = int(row[0])

        info["reachable"] = True
    except Exception as exc:  # pragma: no cover — defensive
        info["error"] = str(exc)
    return info


def _chroma_persistence_diagnostics(chroma_persist_dir: Optional[str]) -> Dict[str, Any]:
    """Inspect the on-disk Chroma store. Best-effort — never raises into health."""
    info: Dict[str, Any] = {
        "persist_dir": chroma_persist_dir or None,
        "persistent": bool(chroma_persist_dir),
        "collection_counts": None,
        "total_documents": None,
    }
    if not chroma_persist_dir:
        return info
    try:
        import chromadb  # local import — keeps health endpoint cheap on import

        if not os.path.isdir(chroma_persist_dir):
            info["collection_counts"] = {}
            info["total_documents"] = 0
            return info
        client = chromadb.PersistentClient(path=chroma_persist_dir)
        counts: Dict[str, int] = {}
        for name in _CHROMA_AGENT_COLLECTIONS:
            try:
                coll = client.get_or_create_collection(name=name)
                counts[name] = int(coll.count())
            except Exception:  # pragma: no cover — defensive
                counts[name] = -1
        info["collection_counts"] = counts
        info["total_documents"] = sum(c for c in counts.values() if c >= 0)
    except Exception as exc:  # pragma: no cover — defensive
        info["error"] = str(exc)
    return info


def _runtime_diagnostics() -> Dict[str, Any]:
    config = get_config()
    memory_backend = str(config.get("memory_backend", "chroma")).strip().lower() or "chroma"
    postgres_url = config.get("postgres_url") or os.environ.get("POSTGRES_URL", "")
    redis_url = config.get("redis_url") or os.environ.get("REDIS_URL", "")
    chroma_persist_dir = config.get("chroma_persist_dir") or os.environ.get("CHROMA_PERSIST_DIR", "")
    postgres_reachable = _tcp_reachable_from_url(postgres_url)
    redis_reachable = _tcp_reachable_from_url(redis_url)
    redis_package_available = _module_available("redis")

    chroma_info = _chroma_persistence_diagnostics(chroma_persist_dir or None) \
        if memory_backend == "chroma" else {
            "persist_dir": None,
            "persistent": False,
            "collection_counts": None,
            "total_documents": None,
        }

    # PR 9: per-collection ``seeded`` flag — True when chroma_collection_count
    # for that agent meets or exceeds the expected seed corpus size.
    expected_seeds = _seed_count_per_collection()
    chroma_counts = chroma_info.get("collection_counts") or {}
    seeded: Dict[str, bool] = {}
    for name in _CHROMA_AGENT_COLLECTIONS:
        actual = chroma_counts.get(name, 0)
        expected = expected_seeds.get(name, 0)
        seeded[name] = bool(actual >= expected and expected > 0)

    # PR 9: audit / backtest table diagnostics (best-effort).
    pg_audit = _postgres_audit_diagnostics(postgres_url)

    degraded_reasons: List[str] = []
    if memory_backend in {"postgres", "pgvector", "persistent"} and postgres_reachable is not True:
        degraded_reasons.append("postgres_vector_memory_unavailable")
    if redis_url and (not redis_package_available or redis_reachable is not True):
        degraded_reasons.append("redis_streaming_unavailable")
    if memory_backend == "chroma" and not chroma_info.get("persistent"):
        degraded_reasons.append("chroma_memory_in_memory_only")
    if (
        memory_backend == "chroma"
        and chroma_info.get("persistent")
        and expected_seeds
        and not any(seeded.values())
    ):
        degraded_reasons.append("chroma_collections_unseeded")

    return {
        "memory": {
            "backend": memory_backend,
            "vector_store": "chromadb" if memory_backend == "chroma" else "postgres_pgvector",
            "postgres_vector_required": memory_backend in {"postgres", "pgvector", "persistent"},
            "chroma_persist_dir": chroma_info.get("persist_dir"),
            "chroma_persistent": chroma_info.get("persistent"),
            "chroma_collection_counts": chroma_info.get("collection_counts"),
            "chroma_total_documents": chroma_info.get("total_documents"),
            "seeded": seeded,
            "min_similarity": float(config.get("memory_min_similarity", 0.30)),
        },
        "postgres": {
            "configured": bool(postgres_url),
            "reachable": postgres_reachable,
            "audit_write_lag_seconds": pg_audit.get("audit_write_lag_seconds"),
            "backtest_runs_count": pg_audit.get("backtest_runs_count"),
            "purpose": "audit/backtest persistence only unless memory_backend=postgres",
        },
        "redis": {
            "configured": bool(redis_url),
            "package_available": redis_package_available,
            "reachable": redis_reachable,
            "purpose": "optional websocket progress streaming",
        },
        "degraded": bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
    }

def _normalize_llm_report(report: Dict[str, Any], *, session_id: str) -> Dict[str, Any]:
    """
    Convert scripts/backtester.py report JSON (string-y metrics, portfolio_value keys)
    into the dashboard API contract (numeric metrics + EquityPoint[]).
    """
    ticker = str(report.get("session") or "")
    ticker_norm = _normalize_ticker(ticker) if ticker else ""
    report_error = report.get("error")
    report_error = str(report_error) if report_error else None
    trades_raw = report.get("trades") if isinstance(report.get("trades"), list) else []
    daily_raw = report.get("daily_portfolio") if isinstance(report.get("daily_portfolio"), list) else []
    bm_raw = report.get("benchmark_history") if isinstance(report.get("benchmark_history"), list) else []
    buyhold_raw = report.get("buyhold_history") if isinstance(report.get("buyhold_history"), list) else []
    benchmark_block = report.get("benchmark") if isinstance(report.get("benchmark"), dict) else {}

    # Metrics (legacy shape from backtester.py)
    legacy = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    total_return_pct = _parse_percent(legacy.get("Total Return"))
    sharpe_ratio = _parse_float(legacy.get("Sharpe Ratio"))
    sortino_ratio = _parse_float(legacy.get("Sortino Ratio"))
    max_drawdown_pct = _parse_percent(legacy.get("Max Drawdown"))
    win_rate_pct = _parse_percent(legacy.get("Win Rate"))
    benchmark_return_pct = _parse_percent(legacy.get("Benchmark Return"))
    buyhold_return_pct = _parse_percent(legacy.get("Buy&Hold Return"))
    alpha_pct = _parse_percent(legacy.get("Alpha"))
    closed_trades = legacy.get("Closed Trades")
    if isinstance(closed_trades, (int, float)):
        closed_trades = int(closed_trades)
    else:
        closed_trades = None

    # Equity series normalization
    equity = []
    for r in daily_raw:
        if not isinstance(r, dict):
            continue
        d = r.get("date")
        val = r.get("equity")
        if val is None:
            val = r.get("value")
        if val is None:
            val = r.get("portfolio_value")
        if d and val is not None:
            try:
                equity.append({"date": str(d), "equity": float(val)})
            except Exception:
                continue

    final_equity = equity[-1]["equity"] if equity else None
    initial_capital = equity[0]["equity"] if equity else None

    # Trades normalization (BacktestTrade)
    trades = []
    for t in trades_raw:
        if not isinstance(t, dict):
            continue
        price = t.get("exec_price")
        if price is None:
            price = t.get("close_price")
        qty = t.get("shares")
        pnl = t.get("realized_pnl")
        trades.append(
            {
                "date": t.get("date"),
                "ticker": t.get("ticker") or ticker_norm,
                "action": t.get("action"),
                "price": _parse_float(price),
                "quantity": int(qty) if isinstance(qty, (int, float)) and qty is not None else qty,
                "pnl": _parse_float(pnl),
                "confidence": _parse_float(t.get("confidence")),
                "reasoning": t.get("reasoning"),
                # Trader's structured exit plan (take_profit / stop_loss /
                # time_stop / invalidation_triggers). Passed through verbatim
                # so the dashboard can render an inline expandable row.
                "exit_plan": t.get("exit_plan") if isinstance(t.get("exit_plan"), dict) else None,
            }
        )

    # Benchmark normalization (EquityPoint)
    bm = []
    for r in bm_raw:
        if not isinstance(r, dict):
            continue
        d = r.get("date")
        val = r.get("equity")
        if val is None:
            val = r.get("value")
        if val is None:
            val = r.get("close")
        if d and val is not None:
            try:
                bm.append({"date": str(d), "equity": float(val)})
            except Exception:
                continue

    # Buy-and-hold (same-ticker) equity series normalization. The dashboard
    # uses this for the Scenario Comparison section (Strategy vs same-ticker
    # buy-and-hold). The backtester writes {date, price, value} entries.
    buyhold = []
    for r in buyhold_raw:
        if not isinstance(r, dict):
            continue
        d = r.get("date")
        val = r.get("value", r.get("equity"))
        if d and val is not None:
            try:
                buyhold.append({"date": str(d), "equity": float(val)})
            except Exception:
                continue

    metrics: Dict[str, Any] = {
        "total_return_pct": total_return_pct,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
        "max_drawdown_pct": abs(max_drawdown_pct) if max_drawdown_pct is not None else None,
        "win_rate": win_rate_pct,
        "total_trades": len(trades_raw),
        "closed_trades": closed_trades,
        "benchmark_return_pct": benchmark_return_pct,
        "buyhold_return_pct": buyhold_return_pct,
        "alpha_pct": alpha_pct,
        "final_equity": final_equity,
        "initial_capital": initial_capital,
    }
    metrics = {k: v for k, v in metrics.items() if v is not None}

    # Pass audit_log through (already in JSON report). Dashboard reads the
    # last non-empty risk_judge_text / bull_thesis_summary / bear_thesis_summary
    # to populate the agent cards with real text instead of templated copy.
    audit_log_raw = report.get("audit_log") if isinstance(report.get("audit_log"), list) else []

    # Post-hoc directional accuracy (leak-safe; "was each call right?").
    directional_accuracy = (
        report.get("directional_accuracy")
        if isinstance(report.get("directional_accuracy"), dict) else None
    )

    # Decision-quality block + per-prediction rows (the thesis "skillful, not
    # random" evidence). predictions[] carries session_id per date so the
    # dashboard can open each prediction's full reasoning trace via
    # /api/sessions/{session_id}/trace.
    decision_quality = (
        report.get("decision_quality")
        if isinstance(report.get("decision_quality"), dict) else None
    )
    predictions = (
        report.get("predictions")
        if isinstance(report.get("predictions"), list) else []
    )
    run_config = (
        report.get("run_config")
        if isinstance(report.get("run_config"), dict) else None
    )
    # Scenario event-study block (Follow-the-AI vs EGX30 index). Present only on
    # reports written by scripts/scenario_backtest.py.
    scenario_comparison = (
        report.get("scenario_comparison")
        if isinstance(report.get("scenario_comparison"), dict) else None
    )

    return {
        "ticker": ticker_norm or ticker,
        "llm": {
            "session_id": session_id,
            "error": report_error,
            "metrics": metrics,
            "trades": trades,
            "daily_portfolio": equity,
            "benchmark_history": bm,
            "buyhold_history": buyhold,
            "benchmark": benchmark_block,
            "directional_accuracy": directional_accuracy,
            "decision_quality": decision_quality,
            "predictions": predictions,
            "run_config": run_config,
            "scenario_comparison": scenario_comparison,
            "audit_log": audit_log_raw,
        },
    }


def _normalize_bt_report(report: Dict[str, Any], *, session_id: str) -> Dict[str, Any]:
    """Normalize Backtrader benchmark JSON to the dashboard API contract."""
    ticker = str(report.get("session") or "")
    ticker_norm = _normalize_ticker(ticker) if ticker else ""
    legacy = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    trades_raw = report.get("trades") if isinstance(report.get("trades"), list) else []
    daily_raw = report.get("daily_portfolio") if isinstance(report.get("daily_portfolio"), list) else []

    equity = []
    for row in daily_raw:
        if not isinstance(row, dict):
            continue
        d = row.get("date")
        val = row.get("equity", row.get("value", row.get("portfolio_value")))
        parsed = _parse_float(val)
        if d and parsed is not None:
            equity.append({"date": str(d), "equity": parsed})

    trades = []
    for row in trades_raw:
        if not isinstance(row, dict):
            continue
        qty = row.get("shares", row.get("quantity", row.get("size")))
        trades.append(
            {
                "date": row.get("date"),
                "ticker": row.get("ticker") or ticker_norm,
                "action": row.get("action"),
                "price": _parse_float(row.get("exec_price", row.get("price", row.get("close_price")))),
                "quantity": int(qty) if isinstance(qty, (int, float)) else qty,
                "pnl": _parse_float(row.get("realized_pnl", row.get("pnl"))),
            }
        )

    max_drawdown = _parse_percent(legacy.get("Max Drawdown"))
    metrics: Dict[str, Any] = {
        "total_return_pct": _parse_percent(legacy.get("Total Return")),
        "benchmark_return_pct": _parse_percent(legacy.get("Benchmark Return")),
        "alpha_pct": _parse_percent(legacy.get("Alpha")),
        "sharpe_ratio": _parse_float(legacy.get("Sharpe Ratio")),
        "calmar_ratio": _parse_float(legacy.get("Calmar Ratio")),
        "max_drawdown_pct": abs(max_drawdown) if max_drawdown is not None else None,
        "win_rate": _parse_percent(legacy.get("Win Rate")),
        "total_trades": _parse_float(legacy.get("Total Trades")) or len(trades_raw),
        "total_commissions": _parse_float(legacy.get("Total Commissions")),
        "final_equity": _parse_float(legacy.get("Final Portfolio")) or (equity[-1]["equity"] if equity else None),
        "initial_capital": equity[0]["equity"] if equity else None,
    }
    metrics = {k: v for k, v in metrics.items() if v is not None}

    return {
        "ticker": ticker_norm or ticker,
        "bt": {
            "session_id": session_id,
            "metrics": metrics,
            "trades": trades,
            "daily_portfolio": equity,
        },
    }


# =============================================================================
# Pydantic Models
# =============================================================================

class ConfigUpdate(BaseModel):
    backend_url: Optional[str] = None
    backend_api_key: Optional[str] = None
    deep_think_model: Optional[str] = None
    quick_think_model: Optional[str] = None
    target_market: Optional[str] = None
    data_vendors: Optional[Dict[str, str]] = None
    online_tools: Optional[bool] = None


class AnalysisRequest(BaseModel):
    ticker: str
    trade_date: str
    selected_analysts: List[str] = Field(
        default=["market", "fundamentals"]  # "news" and "social" disabled by default per user request
    )
    max_debate_rounds: int = Field(default=1, ge=1, le=5)
    max_risk_rounds: int = Field(default=1, ge=1, le=5)


class AgentUpdate(BaseModel):
    type: str  # "agent_update", "error", "complete"
    node: str
    status: str  # "idle", "in_progress", "completed", "error"
    state_keys: List[str]
    data: Dict[str, Any]
    timestamp: str


class InvestorProfileRequest(BaseModel):
    interview_text: str = Field(
        ...,
        min_length=10,
        description="Raw transcript of the investor onboarding interview",
    )


# =============================================================================
# App Lifecycle
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    print("="*60)
    print("  TradingAgents Dashboard API Server")
    print(f"  Project root: {PROJECT_ROOT}")
    print(f"  EGX tools: {'available' if EGX_TOOLS_AVAILABLE else 'not available'}")
    print("="*60)
    yield
    print("Server shutting down...")


# =============================================================================
# FastAPI App
# =============================================================================

app = FastAPI(
    title="TradingAgents Dashboard API",
    version="1.0.0",
    description="REST + WebSocket API for the TradingAgents multi-agent trading system.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Portfolio Assistant subsystem (P4). Mounted only when pa_enabled (env
# PA_ENABLED, default 1); the default service injects the real GraphRunLauncher
# so stale signals trigger a background TradingAgentsGraph refresh.
if get_config().get("pa_enabled", True):
    from server.portfolio_routes import router as portfolio_router
    app.include_router(portfolio_router)


# =============================================================================
# Health Check
# =============================================================================

@app.get("/api/health")
async def health():
    diagnostics = _runtime_diagnostics()
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "egx_tools": EGX_TOOLS_AVAILABLE,
        "diagnostics": diagnostics,
    }


# =============================================================================
# Macro Endpoint
# =============================================================================

@app.get("/api/macro")
async def get_macro(as_of: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today")):
    """
    Return the EGX macro snapshot (CBE rate, USD/EGP, EGX30 trend, CPI, Brent, IMF).

    Deterministic, no LLM. Falls back to config defaults if yfinance is unavailable.
    """
    from tradingagents.dataflows.macro_provider import get_egx_macro_context
    target_date = as_of or datetime.now().strftime("%Y-%m-%d")
    try:
        ctx = get_egx_macro_context(as_of_date=target_date, config=get_config())
        return {"status": "ok", "macro_context": ctx}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Macro fetch failed: {exc}")


@app.get("/api/market/indices")
async def get_market_indices_endpoint(
    as_of: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today")
):
    """
    Headline market indices for the dashboard strip — spot level + 1-day change.

    Covers EGX30, Gold and USD/EGP (instruments with a dependable free feed).
    Deterministic, no LLM. Each instrument degrades independently.
    """
    from tradingagents.dataflows.macro_provider import get_market_indices
    try:
        return {"status": "ok", **get_market_indices(as_of_date=as_of)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Indices fetch failed: {exc}")


# =============================================================================
# Configuration Endpoints
# =============================================================================

@app.get("/api/config")
async def get_configuration():
    """Get current system configuration."""
    config = get_config()
    # Redact API keys for security
    safe_config = {**config}
    if "backend_api_key" in safe_config:
        key = safe_config["backend_api_key"]
        safe_config["backend_api_key"] = f"{key[:8]}...{key[-4:]}" if key and len(key) > 12 else "***"
    return {
        "config": safe_config,
        "available_vendors": VENDOR_LIST,
        "tool_categories": {k: v["description"] for k, v in TOOLS_CATEGORIES.items()},
        "risk_limits": EGX_RISK_LIMITS,
    }


@app.put("/api/config")
async def update_configuration(update: ConfigUpdate):
    """Update system configuration."""
    changes = update.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(400, "No configuration changes provided")
    
    # Auto-detect provider from URL if backend_url is being updated
    if "backend_url" in changes:
        url = changes["backend_url"].lower()
        if "googleapis" in url:
            changes["llm_provider"] = "google"
        elif "anthropic" in url:
            changes["llm_provider"] = "anthropic"
        else:
            # Default to OpenAI compatible (Groq, Ollama, OpenRouter, OpenAI)
            changes["llm_provider"] = "openai"
            
    # Merge with current config and set
    current = get_config()
    current.update(changes)
    set_config(current)
    return {"status": "updated", "applied": list(changes.keys())}


# =============================================================================
# Stock Data Endpoints
# =============================================================================

@app.get("/api/stock/{ticker}")
async def get_stock_data(
    ticker: str,
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    days: int = Query(30, description="Lookback days if dates not provided"),
):
    """Fetch OHLCV stock data for a ticker."""
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")
    if not start_date:
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        result = await asyncio.to_thread(
            route_to_vendor, "get_stock_data", ticker, start_date, end_date
        )
        return {"ticker": ticker, "start_date": start_date, "end_date": end_date, "data": result}
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch stock data: {str(e)}")


@app.get("/api/indicators/{ticker}")
async def get_indicators(
    ticker: str,
    indicator: str = Query("RSI", description="Technical indicator name"),
    curr_date: Optional[str] = Query(None, description="Current date YYYY-MM-DD"),
    look_back_days: int = Query(30, description="Lookback window"),
):
    """Fetch technical indicators for a ticker."""
    if not curr_date:
        curr_date = datetime.now().strftime("%Y-%m-%d")

    try:
        result = await asyncio.to_thread(
            route_to_vendor, "get_indicators", ticker, indicator, curr_date, look_back_days
        )
        return {"ticker": ticker, "indicator": indicator, "data": result}
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch indicators: {str(e)}")


# =============================================================================
# Fundamentals Endpoints
# =============================================================================

@app.get("/api/fundamentals/{ticker}")
async def get_fundamentals(
    ticker: str,
    curr_date: Optional[str] = Query(None, description="Current date YYYY-MM-DD"),
):
    """Fetch fundamental data for a ticker."""
    if not curr_date:
        curr_date = datetime.now().strftime("%Y-%m-%d")

    # Try EGX-specific tools first
    if EGX_TOOLS_AVAILABLE:
        try:
            result = await asyncio.to_thread(
                get_egx_fundamentals_summary, ticker, curr_date
            )
            return {"ticker": ticker, "source": "egx_local", "data": result}
        except Exception:
            pass  # Fall through to generic

    # Generic vendor routing
    try:
        result = await asyncio.to_thread(
            route_to_vendor, "get_fundamentals", ticker, curr_date
        )
        return {"ticker": ticker, "source": "vendor", "data": result}
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch fundamentals: {str(e)}")


# =============================================================================
# News Endpoints
# =============================================================================

@app.get("/api/news/{ticker}")
async def get_news(
    ticker: str,
    curr_date: Optional[str] = Query(None, description="Current date YYYY-MM-DD"),
    look_back_days: int = Query(7, description="Days of news to retrieve"),
):
    """Fetch news data for a ticker."""
    if not curr_date:
        curr_date = datetime.now().strftime("%Y-%m-%d")

    # Try EGX-specific tools first
    if EGX_TOOLS_AVAILABLE:
        try:
            result = await asyncio.to_thread(
                get_egx_news_combined, ticker, curr_date, look_back_days
            )
            return {"ticker": ticker, "source": "egx_local", "data": result}
        except Exception:
            pass  # Fall through to generic

    # Generic vendor routing
    start_date = (datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=look_back_days)).strftime("%Y-%m-%d")
    try:
        result = await asyncio.to_thread(
            route_to_vendor, "get_news", ticker, start_date, curr_date
        )
        return {"ticker": ticker, "source": "vendor", "data": result}
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch news: {str(e)}")


# =============================================================================
# Analysis Results (History) Endpoints
# =============================================================================

@app.get("/api/results")
async def list_results():
    """List past LIVE analysis runs, newest first.

    Primary source: Postgres ``analysis_sessions`` filtered to
    ``run_type='live'`` (written by the multi-agent graph and the quick-analysis
    path). This is the same store the trace-detail endpoint reads, so the list
    and the detail are always consistent — backtest-interior analyses
    (``run_type='backtest'``) are excluded so they never pollute "My Analyses".

    Fallback: the legacy ``audit_logs/<ticker>/audit_log.jsonl`` files, used only
    when Postgres is unreachable.
    """
    # ── Postgres path ─────────────────────────────────────────────────────
    try:
        from tradingagents.db import is_postgres_available
        from tradingagents.db.connection import cursor as db_cursor
        pg_ok = is_postgres_available()
    except Exception:
        pg_ok = False

    if pg_ok:
        try:
            sessions: List[Dict[str, Any]] = []
            with db_cursor(dict_cursor=True) as cur:
                cur.execute(
                    """
                    SELECT session_id, ticker, trade_date, market,
                           final_decision, confidence_overall, risk_veto,
                           run_type, created_at
                      FROM analysis_sessions
                     ORDER BY created_at DESC
                     LIMIT 500
                    """
                )
                for row in cur.fetchall():
                    d = _trace_row_to_dict(row)
                    decision = (d.get("final_decision") or "").strip()
                    sessions.append({
                        "ticker": d.get("ticker") or "—",
                        "session_id": d.get("session_id"),
                        "trade_date": d.get("trade_date") or "",
                        "market": d.get("market") or "EGX",
                        "final_decision": decision.split("\n")[0][:24] or None,
                        "confidence_overall": d.get("confidence_overall"),
                        "risk_veto": bool(d.get("risk_veto")),
                        "run_type": d.get("run_type") or "live",
                        "timestamp": d.get("created_at") or "",
                    })
            return {"sessions": sessions}
        except Exception as exc:
            logger.warning("Postgres results list failed, falling back to jsonl: %s", exc)
            return {"error": str(exc), "sessions": []}

    # ── JSONL fallback (legacy on-disk audit trail) ───────────────────────
    audit_dir = PROJECT_ROOT / "audit_logs"
    if not audit_dir.exists():
        return {"sessions": []}

    sessions = []
    for ticker_dir in sorted(audit_dir.iterdir()):
        if not ticker_dir.is_dir():
            continue
        ticker = ticker_dir.name

        jsonl_path = ticker_dir / "audit_log.jsonl"
        if jsonl_path.exists():
            try:
                with open(jsonl_path, "r", encoding="utf-8") as f:
                    for line in f:
                        entry = json.loads(line.strip())
                        if entry.get("event") == "SESSION_START":
                            sessions.append({
                                "ticker": ticker,
                                "session_id": entry.get("_session_id", "unknown"),
                                "trade_date": entry.get("trade_date", "unknown"),
                                "market": entry.get("market", "unknown"),
                                "run_type": "live",
                                "timestamp": entry.get("_logged_at", ""),
                            })
            except Exception:
                pass

    return {"sessions": sorted(sessions, key=lambda s: s["timestamp"], reverse=True)}


@app.get("/api/sessions/{session_id}/trace")
async def get_session_trace(session_id: str):
    """
    Return the full reasoning trace for one analysis session.

    Primary source: Postgres (analysis_sessions + agent_events, written by
    tradingagents.db.audit_writer when POSTGRES_URL is configured — see
    MEMORY.md PR 5).
    Fallback source: audit_logs/<ticker>/audit_log.jsonl files (legacy on-disk
    audit trail).

    Response shape:
        {
            "source": "postgres" | "jsonl" | "none",
            "session": { session_id, ticker, trade_date, final_decision, ... }
                       | null,
            "events":  [ { agent_name, event_type, structured_output, ... } ]
        }
    """
    # ── Postgres path ─────────────────────────────────────────────────────
    try:
        from tradingagents.db import is_postgres_available
        from tradingagents.db.connection import cursor as db_cursor
        pg_ok = is_postgres_available()
    except Exception:
        pg_ok = False

    if pg_ok:
        try:
            session: Optional[Dict[str, Any]] = None
            events: List[Dict[str, Any]] = []
            with db_cursor(dict_cursor=True) as cur:
                cur.execute(
                    """
                    SELECT session_id, ticker, trade_date, market,
                           final_decision, risk_veto, confidence_overall,
                           confidence_scores, execution_plan, risk_assessment,
                           data_quality, full_state, model_fingerprint, user_id,
                           created_at
                      FROM analysis_sessions
                     WHERE session_id = %s
                     LIMIT 1
                    """,
                    (session_id,),
                )
                row = cur.fetchone()
                if row is not None:
                    session = _trace_row_to_dict(row)

                cur.execute(
                    """
                    SELECT event_type, agent_name, opinion_type, opinion_summary,
                           confidence_score, structured_output, model_fingerprint,
                           logged_at
                      FROM agent_events
                     WHERE session_id = %s
                     ORDER BY logged_at ASC, id ASC
                    """,
                    (session_id,),
                )
                for ev in cur.fetchall():
                    events.append(_trace_row_to_dict(ev))

            if session is not None:
                return {"source": "postgres", "session": session, "events": events}
        except Exception as exc:
            logger.warning("Postgres trace lookup failed for %s: %s", session_id, exc)

    # ── JSONL fallback ────────────────────────────────────────────────────
    audit_dir = PROJECT_ROOT / "audit_logs"
    if audit_dir.exists():
        session_entry: Optional[Dict[str, Any]] = None
        entries: List[Dict[str, Any]] = []
        ticker_found: Optional[str] = None
        for ticker_dir in audit_dir.iterdir():
            if not ticker_dir.is_dir():
                continue
            jsonl_path = ticker_dir / "audit_log.jsonl"
            if not jsonl_path.exists():
                continue
            try:
                with open(jsonl_path, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            entry = json.loads(line.strip())
                        except json.JSONDecodeError:
                            continue
                        if entry.get("_session_id") != session_id:
                            continue
                        ticker_found = ticker_dir.name
                        if entry.get("event") == "SESSION_START":
                            session_entry = entry
                        entries.append(entry)
            except OSError:
                continue
            if entries:
                break

        if entries:
            return {
                "source": "jsonl",
                "session": _jsonl_to_session(session_entry, ticker_found, entries),
                "events": [_jsonl_entry_to_event(e) for e in entries],
            }

    raise HTTPException(404, f"Session {session_id} not found")


def _trace_row_to_dict(row: Any) -> Dict[str, Any]:
    """Convert a psycopg2 DictRow into a JSON-safe dict.

    Decimal -> float (audit numbers fit comfortably in float64);
    date / datetime -> ISO 8601 string;
    everything else passes through.
    """
    from decimal import Decimal
    out: Dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if isinstance(value, Decimal):
            out[key] = float(value)
        elif isinstance(value, (datetime,)):
            out[key] = value.isoformat()
        elif hasattr(value, "isoformat"):  # date
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _jsonl_to_session(
    session_entry: Optional[Dict[str, Any]],
    ticker: Optional[str],
    all_entries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a session-row-shaped dict from JSONL audit entries."""
    # Pull final decision + confidence from the last entry that carries them.
    final_decision: Optional[str] = None
    confidence_overall: Optional[float] = None
    full_state: Optional[Dict[str, Any]] = None
    for entry in all_entries:
        if entry.get("final_trade_decision"):
            final_decision = str(entry["final_trade_decision"])
        if "confidence_scores" in entry and isinstance(entry["confidence_scores"], dict):
            confidence_overall = entry["confidence_scores"].get("overall")
        if entry.get("event") == "FINAL_STATE" and isinstance(entry.get("state"), dict):
            full_state = entry["state"]

    start = session_entry or (all_entries[0] if all_entries else {})
    return {
        "session_id": start.get("_session_id"),
        "ticker": ticker or start.get("ticker"),
        "trade_date": start.get("trade_date"),
        "market": start.get("market") or "EGX",
        "final_decision": final_decision,
        "confidence_overall": confidence_overall,
        "full_state": full_state,
        "model_fingerprint": start.get("model_fingerprint"),
        "created_at": start.get("_logged_at"),
    }


def _jsonl_entry_to_event(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Project a JSONL line into the agent_events response shape."""
    return {
        "event_type": entry.get("event") or "unknown",
        "agent_name": entry.get("_agent") or entry.get("agent"),
        "opinion_type": entry.get("opinion_type"),
        "opinion_summary": entry.get("opinion_summary") or entry.get("summary"),
        "confidence_score": entry.get("confidence_score"),
        "structured_output": entry,
        "model_fingerprint": entry.get("model_fingerprint"),
        "logged_at": entry.get("_logged_at"),
    }



@app.get("/api/prediction/session/{session_id}")
async def get_past_prediction(session_id: str):
    """Retrieve a past analysis session and format it as a full PredictionResult."""
    try:
        from tradingagents.db import is_postgres_available
        from tradingagents.db.connection import cursor as db_cursor
        from fastapi import HTTPException
        if not is_postgres_available():
            raise HTTPException(503, "Postgres not available")
        
        with db_cursor() as cur:
            cur.execute(
                """
                SELECT ticker, trade_date, full_state
                FROM analysis_sessions
                WHERE session_id = %s
                """,
                (session_id,)
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Session not found")
            
            ticker, trade_date, final_state = row
            
            # ── Reconstruct price data ──────────────────────────────
            # New live runs (saved by analyze-full) store a "price" dict.
            # Old backtest runs store "current_price" as a top-level float.
            price_dict = final_state.get("price")
            if not price_dict or not isinstance(price_dict, dict) or not price_dict.get("current"):
                cp = final_state.get("current_price")
                if cp and isinstance(cp, (int, float)) and cp > 0:
                    price_dict = {"current": float(cp), "daily_change": None, "weekly_change": None}
                else:
                    price_dict = {}

            # ── Reconstruct indicators ──────────────────────────────
            indicators_dict = final_state.get("indicators")
            if not indicators_dict or not isinstance(indicators_dict, dict):
                ta = final_state.get("technical_analysis") or {}
                signals = ta.get("indicator_signals") or {}
                trend_info = ta.get("trend_direction") or {}
                trend_str = None
                if isinstance(trend_info, dict):
                    trend_str = trend_info.get("direction")
                elif isinstance(trend_info, str):
                    trend_str = trend_info
                indicators_dict = {
                    "rsi": signals.get("rsi"),
                    "trend": trend_str,
                }

            # ── Base payload ────────────────────────────────────────
            base = {
                "ticker": ticker,
                "name": ticker,
                "price": price_dict,
                "indicators": indicators_dict,
                "technical_panel": final_state.get("technical_panel"),
                "price_history": final_state.get("price_history") or [],
            }
            
            # ── Map final_state → Recommendation ────────────────────
            debate = final_state.get("investment_debate_state") or {}
            bull_thesis = debate.get("bull_thesis") or {}
            bear_thesis = debate.get("bear_thesis") or {}
            judge_decision = debate.get("judge_decision") or final_state.get("investment_plan") or ""
            final_decision_text = final_state.get("final_trade_decision") or ""

            try:
                from tradingagents.graph.signal_processing import SignalProcessor
                signal = SignalProcessor(None).process_signal(final_decision_text) or "HOLD"
            except Exception:
                signal = "HOLD"
            signal = (signal or "HOLD").upper()

            def _thesis_to_text(thesis: dict, history: str) -> str:
                prose = (history or "").strip()
                if prose:
                    return prose
                if isinstance(thesis, dict) and thesis:
                    try:
                        import json
                        return json.dumps(thesis, indent=2, ensure_ascii=False)
                    except Exception:
                        pass
                return ""

            bull_case = _thesis_to_text(bull_thesis, debate.get("bull_history", ""))
            bear_case = _thesis_to_text(bear_thesis, debate.get("bear_history", ""))

            current_price = price_dict.get("current") or 0
            target_price = None
            stop_loss = None
            if isinstance(bull_thesis, dict):
                upside = bull_thesis.get("upside_scenario", {}) or {}
                base_pct = upside.get("base_case_upside_pct")
                if isinstance(base_pct, (int, float)) and current_price:
                    target_price = round(current_price * (1 + base_pct / 100.0), 2)
            if isinstance(bear_thesis, dict):
                downside = bear_thesis.get("downside_range", {}) or {}
                sl = downside.get("support_level_1")
                if isinstance(sl, (int, float)):
                    stop_loss = float(sl)
            
            stop_loss_is_default = stop_loss is None
            if stop_loss is None and current_price:
                stop_loss = round(current_price * 0.9, 2)

            # ── Confidence ──────────────────────────────────────────
            confidence = "MEDIUM"
            if isinstance(bull_thesis, dict):
                cv = (bull_thesis.get("conviction_level") or "").upper()
                if cv in ("HIGH", "MEDIUM", "LOW"):
                    confidence = cv
                elif cv == "MODERATE":
                    confidence = "MEDIUM"
            # Also check confidence_scores from the state
            cs = final_state.get("confidence_scores") or {}
            overall_conf = cs.get("overall")
            if isinstance(overall_conf, (int, float)):
                if overall_conf >= 0.7:
                    confidence = "HIGH"
                elif overall_conf <= 0.35:
                    confidence = "LOW"

            # ── Execution plan / time horizon ───────────────────────
            raw_plan = final_state.get("execution_plan") or {}
            plan = raw_plan.get("execution_plan", raw_plan) if isinstance(raw_plan, dict) else {}

            risk_action = str(final_state.get("risk_action") or "ALLOW").upper()
            risk_profile = {
                "VETO": "HIGH",
                "THROTTLE": "MEDIUM",
                "WARN": "MEDIUM",
                "ALLOW": "LOW",
            }.get(risk_action, "MEDIUM")
            
            if current_price and stop_loss and not stop_loss_is_default:
                stop_dist_pct = abs((stop_loss - current_price) / current_price) * 100
                if stop_dist_pct >= 12:
                    risk_profile = "HIGH"
                elif stop_dist_pct >= 7 and risk_profile == "LOW":
                    risk_profile = "MEDIUM"

            # Time horizon: try execution_plan.exit_logic.time_stop, then bull_thesis.time_horizon
            time_horizon = None
            if isinstance(plan, dict):
                exit_logic = plan.get("exit_logic") or {}
                ts = exit_logic.get("time_stop")
                if isinstance(ts, str) and ts.strip() and "[" not in ts:
                    time_horizon = ts.strip()
            if not time_horizon and isinstance(bull_thesis, dict):
                th = bull_thesis.get("time_horizon")
                if isinstance(th, str) and th.strip():
                    time_horizon = th.strip()

            # ── Build the trader investment plan as the execution rationale ──
            trader_plan = final_state.get("trader_investment_plan") or ""
            final_rec = final_state.get("execution_plan", {})
            final_rec_text = ""
            if isinstance(final_rec, dict):
                fr = final_rec.get("final_recommendation")
                if isinstance(fr, str) and fr.strip():
                    final_rec_text = fr

            recommendation = {
                "signal": signal,
                "confidence": confidence,
                "target_price": target_price,
                "stop_loss": stop_loss,
                "risk": risk_profile,
                "time_horizon": time_horizon,
                "bull_case": bull_case or "No bullish thesis was returned for this run.",
                "bear_case": bear_case or "No bearish thesis was returned for this run.",
                "neutral_case": judge_decision or "",
                "rationale": judge_decision or "",
                "recommendation": final_rec_text or final_decision_text or judge_decision or "",
                "full_text": trader_plan or final_decision_text,
                "styled_recommendations": final_state.get("styled_recommendations"),
            }

            return {
                **base,
                "session_id": session_id,
                "recommendation": recommendation,
                "llm_error": None,
                "status": "ok",
                "pipeline": "historical",
                "_final_state_keys": list(final_state.keys()),
            }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to retrieve past prediction: {str(exc)}")


@app.get("/api/results/{ticker}/{session_id}")
async def get_result_detail(ticker: str, session_id: str):
    """Get detailed results for a specific analysis session."""
    jsonl_path = PROJECT_ROOT / "audit_logs" / ticker / "audit_log.jsonl"
    md_path = PROJECT_ROOT / "audit_logs" / ticker / "audit_summary.md"

    if not jsonl_path.exists():
        raise HTTPException(404, f"No results found for {ticker}")

    # Parse all entries for this session
    entries = []
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                entry = json.loads(line.strip())
                if entry.get("_session_id") == session_id:
                    entries.append(entry)
    except Exception as e:
        raise HTTPException(500, f"Failed to parse results: {str(e)}")

    if not entries:
        raise HTTPException(404, f"Session {session_id} not found for {ticker}")

    # Read markdown summary if available
    markdown_summary = ""
    if md_path.exists():
        try:
            markdown_summary = md_path.read_text(encoding="utf-8")
        except Exception:
            pass

    return {
        "ticker": ticker,
        "session_id": session_id,
        "entries": entries,
        "markdown_summary": markdown_summary,
    }


# =============================================================================
# Agent Memory + Reflection Endpoints (PR5)
# =============================================================================
#
# Read-only surfaces over tradingagents.agents.utils.memory.FinancialSituationMemory.
# Five canonical collections, matching trading_graph.py:
#   bull_memory, bear_memory, trader_memory, invest_judge_memory, risk_manager_memory
#
# We lazily instantiate each collection on first request and cache for the
# process lifetime so we don't pay the Chroma/BM25 init cost on every call.

# Lazy module-level cache. Keyed by agent_name; populated on demand.
_MEMORY_INSTANCES: Dict[str, Any] = {}

# Allowlist of agent_name path-parameters. Anything outside this set is rejected
# at the boundary so a typo can't accidentally spawn a new Chroma collection.
_MEMORY_AGENT_ALLOWLIST = frozenset({
    "bull_memory",
    "bear_memory",
    "trader_memory",
    "invest_judge_memory",
    "risk_manager_memory",
})


def _get_memory(agent_name: str):
    """Return a cached FinancialSituationMemory for the named collection.

    Lazy-instantiates on first call. The same `config` used by the graph
    drives Chroma path / embedding backend / seed-corpus behaviour.
    """
    if agent_name in _MEMORY_INSTANCES:
        return _MEMORY_INSTANCES[agent_name]
    # Import inside the function so test suites that don't exercise memory
    # don't pay the chromadb import cost at module load.
    from tradingagents.agents.utils.memory import FinancialSituationMemory
    memory = FinancialSituationMemory(agent_name, get_config())
    _MEMORY_INSTANCES[agent_name] = memory
    return memory


@app.get("/api/memory/{agent_name}/search")
async def memory_search(
    agent_name: str,
    ticker: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None, description="Free-text query; defaults to a ticker-shaped probe"),
    k: int = Query(default=5, ge=1, le=50),
    min_similarity: Optional[float] = Query(default=None, ge=0.0, le=1.0),
):
    """Top-K vector + BM25 similarity search over one agent's memory.

    Mirrors `FinancialSituationMemory.get_memories(...)`. When `ticker` is
    supplied we add a Chroma metadata filter so seeded/reflection rows for
    other tickers are excluded. Both `q` and `ticker` are optional; if both
    are omitted we use a generic probe to surface the agent's own seed
    corpus (cold-start UX).
    """
    if agent_name not in _MEMORY_AGENT_ALLOWLIST:
        raise HTTPException(
            400,
            f"Unknown memory collection '{agent_name}'. Allowed: "
            + ", ".join(sorted(_MEMORY_AGENT_ALLOWLIST)),
        )

    memory = _get_memory(agent_name)
    effective_q = q or (f"Recent context for {ticker}" if ticker else "EGX context")
    where = {"ticker": ticker} if ticker else None
    threshold = float(min_similarity) if min_similarity is not None else None

    try:
        raw = memory.get_memories(
            effective_q,
            n_matches=k,
            where=where,
            min_similarity=threshold,
        )
    except Exception as exc:
        logger.warning("memory search failed (%s): %s", agent_name, exc)
        raise HTTPException(500, f"memory search failed: {exc}")

    return {
        "agent_name": agent_name,
        "query": effective_q,
        "ticker": ticker,
        "k": k,
        "min_similarity": threshold,
        "results": raw,
    }


@app.get("/api/memory/{agent_name}/entries")
async def memory_entries(
    agent_name: str,
    ticker: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """List recent entries from one agent's collection (Chroma + BM25 seeds).

    No similarity query — this is a raw dump for the Memory tab's "seeded
    vs learned" split. Seeds are surfaced from the in-memory BM25 corpus
    (PR8 of the DB-infra pass); learned rows come from Chroma when
    embeddings are enabled, or from the same BM25 corpus when not.

    Each entry's `memory_type` ∈ {thesis, execution, risk_decision,
    reflection} lets the UI separate operator-curated seeds from runtime
    writes.
    """
    if agent_name not in _MEMORY_AGENT_ALLOWLIST:
        raise HTTPException(
            400,
            f"Unknown memory collection '{agent_name}'",
        )

    memory = _get_memory(agent_name)
    entries: List[Dict[str, Any]] = []
    source = "chroma" if memory.embeddings_enabled else "bm25"

    if memory.embeddings_enabled:
        try:
            # collection.get() returns {ids, documents, metadatas} for matching
            # rows. We cap with `limit` defensively.
            get_kwargs: Dict[str, Any] = {
                "limit": limit,
                "include": ["metadatas", "documents"],
            }
            if ticker:
                get_kwargs["where"] = {"ticker": ticker}
            result = memory.situation_collection.get(**get_kwargs)
            ids = result.get("ids") or []
            docs = result.get("documents") or []
            metas = result.get("metadatas") or []
            for i, doc in enumerate(docs):
                row_id = ids[i] if i < len(ids) else None
                meta = metas[i] if i < len(metas) else {}
                entries.append({
                    "id": row_id,
                    "situation": doc,
                    "recommendation": meta.get("recommendation", ""),
                    "metadata": {
                        k: meta.get(k)
                        for k in memory.METADATA_KEYS
                        if meta.get(k) is not None
                    },
                    "seeded": isinstance(row_id, str) and row_id.startswith("seed_"),
                })
        except Exception as exc:
            logger.warning("chroma scan failed (%s): %s", agent_name, exc)
    else:
        # BM25-only path (embedding-disabled backends like DeepSeek/Groq).
        for idx, (doc, meta) in enumerate(memory._bm25_corpus):
            if ticker and meta.get("ticker") != ticker:
                continue
            if len(entries) >= limit:
                break
            entries.append({
                "id": f"bm25_{idx}",
                "situation": doc,
                "recommendation": meta.get("recommendation", ""),
                "metadata": {
                    k: meta.get(k)
                    for k in memory.METADATA_KEYS
                    if meta.get(k) is not None
                },
                "seeded": meta.get("memory_type") not in {"reflection", "thesis", "execution", "risk_decision"},
            })

    return {
        "agent_name": agent_name,
        "source": source,
        "ticker": ticker,
        "total": len(entries),
        "entries": entries,
    }


@app.get("/api/reflections")
async def list_reflections(
    ticker: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
):
    """List recent reflection rows across every agent's memory collection.

    Reflections are written post-trade by `tradingagents.graph.reflection`
    with `memory_type='reflection'`. We walk all five collections, filter
    on the reflection memory_type, and return the union sorted by
    trade_date desc.
    """
    rows: List[Dict[str, Any]] = []
    for agent_name in sorted(_MEMORY_AGENT_ALLOWLIST):
        try:
            memory = _get_memory(agent_name)
        except Exception as exc:
            logger.warning("memory init failed (%s): %s", agent_name, exc)
            continue

        try:
            if memory.embeddings_enabled:
                where: Dict[str, Any] = {"memory_type": "reflection"}
                if ticker:
                    where = {
                        "$and": [
                            {"memory_type": "reflection"},
                            {"ticker": ticker},
                        ]
                    }
                result = memory.situation_collection.get(
                    where=where,
                    limit=limit,
                    include=["metadatas", "documents"],
                )
                docs = result.get("documents") or []
                metas = result.get("metadatas") or []
                for i, doc in enumerate(docs):
                    meta = metas[i] if i < len(metas) else {}
                    rows.append(_reflection_row(agent_name, doc, meta))
            else:
                # BM25 corpus walk — mostly seeds + any learned reflections.
                for doc, meta in memory._bm25_corpus:
                    if meta.get("memory_type") != "reflection":
                        continue
                    if ticker and meta.get("ticker") != ticker:
                        continue
                    rows.append(_reflection_row(agent_name, doc, meta))
        except Exception as exc:
            logger.warning("reflection scan failed (%s): %s", agent_name, exc)
            continue

    # Sort by trade_date desc when present, then by agent_name for determinism.
    def _sort_key(r: Dict[str, Any]) -> tuple:
        td = r.get("trade_date") or ""
        return (td, r.get("agent_name") or "")
    rows.sort(key=_sort_key, reverse=True)
    return {
        "ticker": ticker,
        "total": len(rows),
        "reflections": rows[:limit],
    }


def _reflection_row(agent_name: str, situation: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Project an agent memory row into the reflection response shape."""
    return {
        "agent_name": agent_name,
        "situation": situation,
        "recommendation": metadata.get("recommendation", ""),
        "ticker": metadata.get("ticker"),
        "trade_date": metadata.get("trade_date"),
        "outcome": metadata.get("outcome"),
        "confidence": metadata.get("confidence"),
    }


# =============================================================================
# RL Meta-Policy Endpoints (PR7)
# =============================================================================
#
# Two read-only endpoints over the offline-RL meta-policy artefacts that
# already exist in this repo:
#
#   - tradingagents.default_config carries the env-backed flag + model path
#   - agent_events rows with event_type='rl_meta_size_adjustment' carry the
#     per-decision size multiplier + Q-values + model fingerprint
#     (MEMORY.md §4b PR C, tradingagents.db.audit_writer.write_rl_meta_event)
#   - scripts/backtester.py optionally records the same fields on trades.
#
# Both endpoints degrade gracefully when Postgres is unreachable: status
# always returns the env flag; decisions returns an empty list rather than
# 500.


@app.get("/api/rl/status")
async def get_rl_status():
    """Surface the RL meta-policy flag, model path, and (when loaded) the
    current policy's fingerprint.

    The dashboard footer and Diagnostics page use this to render an
    "RL ON / OFF" pill and the model's `weights_sha256_16` so operators can
    correlate trades against a specific checkpoint.
    """
    config = get_config()
    enabled = bool(config.get("rl_meta_policy_enabled"))
    model_path = config.get("rl_model_path") or os.environ.get("RL_MODEL_PATH", "")

    fingerprint: Optional[Dict[str, Any]] = None
    feature_version: Optional[str] = None
    if enabled and model_path:
        # Best-effort load. We don't want a missing checkpoint to crash the
        # health surface — if the file is gone or torch isn't available we
        # report `loaded=False` and let the UI render a warning.
        try:
            from tradingagents.rl.policy import RLSizingPolicy

            policy = RLSizingPolicy.load(model_path)
            fingerprint = dict(getattr(policy, "model_fingerprint", {}) or {})
            feature_version = str(getattr(policy, "feature_version", "") or "")
        except Exception as exc:
            logger.info("rl_status: policy load failed (%s)", exc)

    # When we couldn't load the file, surface the path so the operator can
    # check existence themselves.
    return {
        "enabled": enabled,
        "model_path": model_path or None,
        "loaded": fingerprint is not None,
        "feature_version": feature_version,
        "model_fingerprint": fingerprint,
    }


@app.get("/api/rl/decisions")
async def list_rl_decisions(
    ticker: Optional[str] = Query(default=None),
    session_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """List recent RL meta-policy decisions from agent_events.

    Each row was written by ``tradingagents.db.audit_writer.write_rl_meta_event``
    with ``event_type='rl_meta_size_adjustment'``. Optional filters:

    - ``ticker``   — joins via ``analysis_sessions`` so the table's ticker
                     column does the filtering even though agent_events
                     doesn't carry the ticker directly.
    - ``session_id`` — exact match on the originating graph session.

    Returns ``[]`` (200, not 500) when Postgres is unreachable, so the
    backtest-detail RL panel can render a graceful empty state without
    bringing down the rest of the page.
    """
    try:
        from tradingagents.db import is_postgres_available
        from tradingagents.db.connection import cursor as db_cursor
        pg_ok = is_postgres_available()
    except Exception:
        pg_ok = False

    if not pg_ok:
        return {
            "decisions": [],
            "source": "none",
            "reason": "postgres_unavailable",
        }

    # The session join is only needed when filtering by ticker; otherwise we
    # skip it for a cheaper scan.
    sql = (
        "SELECT ae.session_id, ae.event_type, ae.agent_name, "
        "       ae.opinion_summary, ae.confidence_score, ae.structured_output, "
        "       ae.model_fingerprint, ae.logged_at"
    )
    params: List[Any] = []
    if ticker:
        sql += (
            ", s.ticker, s.trade_date "
            " FROM agent_events ae "
            " LEFT JOIN analysis_sessions s ON s.session_id = ae.session_id "
            " WHERE ae.event_type = %s AND s.ticker = %s"
        )
        params.extend(["rl_meta_size_adjustment", ticker])
    else:
        sql += (
            ", NULL::text AS ticker, NULL::date AS trade_date "
            " FROM agent_events ae "
            " WHERE ae.event_type = %s"
        )
        params.append("rl_meta_size_adjustment")

    if session_id:
        sql += " AND ae.session_id = %s"
        params.append(session_id)
    sql += " ORDER BY ae.logged_at DESC, ae.id DESC LIMIT %s"
    params.append(limit)

    decisions: List[Dict[str, Any]] = []
    try:
        with db_cursor(dict_cursor=True) as cur:
            cur.execute(sql, tuple(params))
            for row in cur.fetchall():
                decisions.append(_trace_row_to_dict(row))
    except Exception as exc:
        logger.warning("rl_decisions query failed: %s", exc)
        return {
            "decisions": [],
            "source": "none",
            "reason": "query_failed",
        }

    return {
        "decisions": decisions,
        "source": "postgres",
        "ticker": ticker,
        "session_id": session_id,
        "total": len(decisions),
    }


# =============================================================================
# Diagnostics — Prompt registry + fingerprint drift (PR9)
# =============================================================================

# In-process TTL cache for the PROMPTS.md parse. The file is small (≈70 KB)
# and the parse is regex-only, but we cache anyway so the Diagnostics page
# doesn't re-read the file on every status-poll cycle. Refresh every 60 s.
_PROMPTS_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_PROMPTS_TTL_SECONDS = 60.0


def _parse_prompts_md(path: Path) -> Dict[str, Any]:
    """Extract the prompt catalog from PROMPTS.md.

    The convention is: each prompt block starts with a level-3 heading
    ``### P-<ID> — <title>`` (em-dash separator). We scan once, returning
    one record per ID with the line number for deep-linking from the UI.
    """
    if not path.exists():
        return {"source": "none", "path": str(path), "total": 0, "prompts": []}

    prompts: List[Dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("PROMPTS.md read failed: %s", exc)
        return {"source": "none", "path": str(path), "total": 0, "prompts": []}

    lines = text.splitlines()
    # The em-dash (—) is the canonical separator. The ID group is lazy and
    # allows slashes/spaces so sibling IDs (``P-TRADER-SYS / P-TRADER-USR``)
    # are captured together, then split downstream. A plain hyphen could
    # collide with the hyphens *inside* IDs like ``P-FUND-COT-2``, so we
    # don't accept it as a fallback.
    pattern = re.compile(r"^###\s+(P-[A-Z0-9\-/\s]+?)\s+—\s+(.+?)\s*$")
    for i, raw in enumerate(lines, start=1):
        m = pattern.match(raw)
        if not m:
            continue
        ids_part = m.group(1).strip()
        title = m.group(2).strip()
        # Some headings use slashes to list sibling IDs that share a body
        # (e.g. ``P-TRADER-SYS / P-TRADER-USR``) — split them out so each
        # ID becomes its own row in the UI.
        for pid in [s.strip() for s in ids_part.split("/") if s.strip()]:
            prompts.append({"id": pid, "title": title, "line": i})

    return {
        "source": "prompts_md",
        "path": str(path),
        "total": len(prompts),
        "prompts": prompts,
    }


@app.get("/api/diagnostics/prompts")
async def get_diagnostics_prompts():
    """Return the parsed PROMPTS.md catalog with id, title, and line number.

    Cached for 60 seconds so the Diagnostics page can poll cheaply.
    Returns ``{source: "none", prompts: []}`` when the file is missing
    (e.g. trimmed container deploy) rather than 500.
    """
    now = time.time()
    if (
        _PROMPTS_CACHE["data"] is not None
        and now - _PROMPTS_CACHE["ts"] < _PROMPTS_TTL_SECONDS
    ):
        return _PROMPTS_CACHE["data"]

    data = _parse_prompts_md(PROJECT_ROOT / "PROMPTS.md")
    _PROMPTS_CACHE["data"] = data
    _PROMPTS_CACHE["ts"] = now
    return data


@app.get("/api/diagnostics/fingerprints")
async def get_diagnostics_fingerprints(
    days: int = Query(default=30, ge=1, le=365),
):
    """Distinct ``model_fingerprint`` values seen in the last ``days`` days.

    Used by the Diagnostics page to render a drift sparkline and a "models
    in use" table. Reads from ``agent_events.model_fingerprint`` (JSONB,
    added by ``scripts/db/apply_schema_v2.sql``). Returns an empty payload
    when Postgres is unavailable so the panel can render an explanation.
    """
    try:
        from tradingagents.db import is_postgres_available
        from tradingagents.db.connection import cursor as db_cursor
        pg_ok = is_postgres_available()
    except Exception:
        pg_ok = False

    if not pg_ok:
        return {
            "source": "none",
            "reason": "postgres_unavailable",
            "days": days,
            "total_events": 0,
            "distinct_fingerprints": 0,
            "fingerprints": [],
            "daily_counts": [],
        }

    # Cast JSONB to text for the GROUP BY so equality treats the JSON
    # documents as opaque blobs. The dashboard parses each blob back into
    # a dict before rendering.
    fp_sql = (
        "SELECT model_fingerprint::text AS fp_text, "
        "       MIN(logged_at) AS first_seen, "
        "       MAX(logged_at) AS last_seen, "
        "       COUNT(*) AS event_count "
        "  FROM agent_events "
        " WHERE logged_at >= NOW() - (%s || ' days')::interval "
        "   AND model_fingerprint IS NOT NULL "
        " GROUP BY model_fingerprint::text "
        " ORDER BY last_seen DESC "
        " LIMIT 50"
    )

    daily_sql = (
        "SELECT DATE(logged_at) AS day, "
        "       COUNT(*) AS events, "
        "       COUNT(DISTINCT model_fingerprint::text) AS distinct_fps "
        "  FROM agent_events "
        " WHERE logged_at >= NOW() - (%s || ' days')::interval "
        " GROUP BY DATE(logged_at) "
        " ORDER BY day ASC"
    )

    fingerprints: List[Dict[str, Any]] = []
    daily: List[Dict[str, Any]] = []
    total_events = 0
    try:
        with db_cursor(dict_cursor=True) as cur:
            cur.execute(fp_sql, (str(days),))
            for row in cur.fetchall():
                d = _trace_row_to_dict(row)
                fp_text = d.get("fp_text") or ""
                try:
                    fp_obj = json.loads(fp_text) if fp_text else None
                except (TypeError, ValueError):
                    fp_obj = None
                fingerprints.append({
                    "fingerprint": fp_obj,
                    "fingerprint_text": fp_text,
                    "first_seen": d.get("first_seen"),
                    "last_seen": d.get("last_seen"),
                    "event_count": int(d.get("event_count") or 0),
                })
                total_events += int(d.get("event_count") or 0)

            cur.execute(daily_sql, (str(days),))
            for row in cur.fetchall():
                d = _trace_row_to_dict(row)
                daily.append({
                    "day": d.get("day"),
                    "events": int(d.get("events") or 0),
                    "distinct": int(d.get("distinct_fps") or 0),
                })
    except Exception as exc:
        logger.warning("fingerprints query failed: %s", exc)
        return {
            "source": "none",
            "reason": "query_failed",
            "days": days,
            "total_events": 0,
            "distinct_fingerprints": 0,
            "fingerprints": [],
            "daily_counts": [],
        }

    return {
        "source": "postgres",
        "days": days,
        "total_events": total_events,
        "distinct_fingerprints": len(fingerprints),
        "fingerprints": fingerprints,
        "daily_counts": daily,
    }


# =============================================================================
# Admin Monitoring Suite — read-only aggregation endpoints
# =============================================================================
# These power the dashboard's /admin Performance Analytics and Error Center.
# They ONLY read existing audit tables (analysis_sessions, agent_events) — no
# trading logic, no writes. They degrade to ``source: "none"`` when Postgres is
# unavailable so the frontend can fall back to clearly-flagged sample data.


def _admin_pg_ok() -> bool:
    try:
        from tradingagents.db import is_postgres_available
        return is_postgres_available()
    except Exception:
        return False


@app.get("/api/admin/metrics")
async def get_admin_metrics(days: int = Query(default=30, ge=1, le=365)):
    """Aggregate run + agent metrics over the last ``days`` days.

    Real (from Postgres): daily run counts, decision mix (BUY/SELL/HOLD/OTHER),
    per-agent execution counts + average confidence, overall averages. Token /
    cost / per-agent latency are NOT persisted in ``agent_events`` yet, so the
    dashboard layers a clearly-flagged estimate on top of this payload.
    """
    empty = {
        "source": "none",
        "reason": "postgres_unavailable",
        "days": days,
        "total_runs": 0,
        "total_events": 0,
        "avg_confidence": None,
        "daily_runs": [],
        "decision_counts": {},
        "per_agent": [],
    }
    if not _admin_pg_ok():
        return empty

    from tradingagents.db.connection import cursor as db_cursor

    daily_sql = (
        "SELECT DATE(created_at) AS day, COUNT(*) AS runs "
        "  FROM analysis_sessions "
        " WHERE created_at >= NOW() - (%s || ' days')::interval "
        " GROUP BY DATE(created_at) ORDER BY day ASC"
    )
    decision_sql = (
        "SELECT CASE "
        "         WHEN UPPER(final_decision) LIKE '%%BUY%%'  THEN 'BUY' "
        "         WHEN UPPER(final_decision) LIKE '%%SELL%%' THEN 'SELL' "
        "         WHEN UPPER(final_decision) LIKE '%%HOLD%%' THEN 'HOLD' "
        "         ELSE 'OTHER' END AS bucket, COUNT(*) AS n "
        "  FROM analysis_sessions "
        " WHERE created_at >= NOW() - (%s || ' days')::interval "
        " GROUP BY 1"
    )
    agent_sql = (
        "SELECT agent_name, COUNT(*) AS executions, AVG(confidence_score) AS avg_conf, "
        "       MAX(logged_at) AS last_seen "
        "  FROM agent_events "
        " WHERE logged_at >= NOW() - (%s || ' days')::interval "
        "   AND agent_name IS NOT NULL "
        " GROUP BY agent_name ORDER BY executions DESC"
    )
    summary_sql = (
        "SELECT AVG(confidence_overall) AS avg_conf FROM analysis_sessions "
        " WHERE created_at >= NOW() - (%s || ' days')::interval"
    )

    try:
        daily: List[Dict[str, Any]] = []
        decisions: Dict[str, int] = {}
        per_agent: List[Dict[str, Any]] = []
        avg_conf: Optional[float] = None
        with db_cursor(dict_cursor=True) as cur:
            cur.execute(daily_sql, (str(days),))
            for row in cur.fetchall():
                d = _trace_row_to_dict(row)
                daily.append({"day": d.get("day"), "runs": int(d.get("runs") or 0)})

            cur.execute(decision_sql, (str(days),))
            for row in cur.fetchall():
                d = _trace_row_to_dict(row)
                decisions[str(d.get("bucket") or "OTHER")] = int(d.get("n") or 0)

            cur.execute(agent_sql, (str(days),))
            for row in cur.fetchall():
                d = _trace_row_to_dict(row)
                ac = d.get("avg_conf")
                per_agent.append({
                    "agent_name": d.get("agent_name"),
                    "executions": int(d.get("executions") or 0),
                    "avg_confidence": float(ac) if ac is not None else None,
                    "last_seen": d.get("last_seen"),
                })

            cur.execute(summary_sql, (str(days),))
            srow = cur.fetchone()
            if srow is not None:
                sd = _trace_row_to_dict(srow)
                avg_conf = float(sd["avg_conf"]) if sd.get("avg_conf") is not None else None
    except Exception as exc:
        logger.warning("admin metrics query failed: %s", exc)
        return {**empty, "reason": "query_failed"}

    return {
        "source": "postgres",
        "days": days,
        "total_runs": sum(x["runs"] for x in daily),
        "total_events": sum(x["executions"] for x in per_agent),
        "avg_confidence": avg_conf,
        "daily_runs": daily,
        "decision_counts": decisions,
        "per_agent": per_agent,
    }


@app.get("/api/admin/errors")
async def get_admin_errors(days: int = Query(default=30, ge=1, le=365)):
    """Best-effort error feed over the last ``days`` days.

    There is no dedicated error table, so this surfaces two honest signals from
    the audit trail: (1) agent events whose type/summary looks like a failure,
    and (2) risk vetoes (a notable, non-error outcome). When nothing is found
    the dashboard falls back to clearly-flagged sample data so the screen still
    demonstrates the workflow.
    """
    empty = {"source": "none", "reason": "postgres_unavailable", "days": days, "errors": [], "total": 0}
    if not _admin_pg_ok():
        return empty

    from tradingagents.db.connection import cursor as db_cursor

    err_sql = (
        "SELECT e.logged_at, e.session_id, e.agent_name, e.event_type, "
        "       e.opinion_summary, s.ticker "
        "  FROM agent_events e "
        "  LEFT JOIN analysis_sessions s ON s.session_id = e.session_id "
        " WHERE e.logged_at >= NOW() - (%s || ' days')::interval "
        "   AND ( e.event_type ILIKE '%%error%%' OR e.event_type ILIKE '%%fail%%' "
        "      OR e.opinion_summary ILIKE '%%error%%' OR e.opinion_summary ILIKE '%%failed%%' "
        "      OR e.opinion_summary ILIKE '%%exception%%' OR e.opinion_summary ILIKE '%%timeout%%' ) "
        " ORDER BY e.logged_at DESC LIMIT 200"
    )
    veto_sql = (
        "SELECT created_at, session_id, ticker, final_decision "
        "  FROM analysis_sessions "
        " WHERE created_at >= NOW() - (%s || ' days')::interval AND risk_veto = TRUE "
        " ORDER BY created_at DESC LIMIT 100"
    )

    errors: List[Dict[str, Any]] = []
    try:
        with db_cursor(dict_cursor=True) as cur:
            cur.execute(err_sql, (str(days),))
            for row in cur.fetchall():
                d = _trace_row_to_dict(row)
                errors.append({
                    "timestamp": d.get("logged_at"),
                    "session_id": d.get("session_id"),
                    "ticker": d.get("ticker"),
                    "agent": d.get("agent_name"),
                    "error_type": d.get("event_type") or "agent_error",
                    "severity": "error",
                    "category": "agent",
                    "message": d.get("opinion_summary") or "(no detail recorded)",
                })

            cur.execute(veto_sql, (str(days),))
            for row in cur.fetchall():
                d = _trace_row_to_dict(row)
                errors.append({
                    "timestamp": d.get("created_at"),
                    "session_id": d.get("session_id"),
                    "ticker": d.get("ticker"),
                    "agent": "Risk Judge",
                    "error_type": "risk_veto",
                    "severity": "warning",
                    "category": "risk",
                    "message": f"Risk veto applied (decision: {d.get('final_decision') or 'n/a'})",
                })
    except Exception as exc:
        logger.warning("admin errors query failed: %s", exc)
        return {**empty, "reason": "query_failed"}

    errors.sort(key=lambda e: str(e.get("timestamp") or ""), reverse=True)
    return {"source": "postgres", "days": days, "errors": errors, "total": len(errors)}


# =============================================================================
# WebSocket — Real-Time Analysis Streaming
# =============================================================================

# Active analysis tracking
active_analyses: Dict[str, bool] = {}


# ── Live broadcast helpers (shared by analyze_full + the analyze WS) ──────────

def _hub_node_from_chunk(chunk: dict) -> tuple:
    """Map a LangGraph updates-mode chunk to (node_name, status, changed_keys)."""
    node_keys = [k for k in chunk.keys() if k != "messages"]
    if len(node_keys) == 1:
        node = node_keys[0]
        delta = chunk[node] if isinstance(chunk[node], dict) else {}
        return node, "completed", list(delta.keys()) if isinstance(delta, dict) else []
    if "market_report" in chunk:
        return "Market Analyst", "completed", node_keys
    if "sentiment_report" in chunk:
        return "Social Analyst", "completed", node_keys
    if "news_report" in chunk:
        return "News Analyst", "completed", node_keys
    if "fundamentals_report" in chunk:
        return "Fundamentals Analyst", "completed", node_keys
    if "trader_investment_plan" in chunk:
        return "Trader", "completed", node_keys
    if "final_trade_decision" in chunk:
        return "Risk Judge", "completed", node_keys
    return "System", "in_progress", node_keys


def _hub_publish(run_id, ticker, trade_date, source, *, ftype, node, status,
                 state_keys=None, data=None) -> None:
    """Best-effort publish of one live frame to the broadcast hub."""
    try:
        live_hub.publish({
            "run_id": run_id,
            "ticker": ticker,
            "trade_date": trade_date,
            "source": source,
            "type": ftype,
            "node": node,
            "status": status,
            "state_keys": state_keys or [],
            "data": data or {},
            "timestamp": datetime.now().isoformat(),
        })
    except Exception:  # pragma: no cover
        pass


@app.websocket("/api/admin/live")
async def admin_live_feed(websocket: WebSocket):
    """Read-only admin feed: broadcasts frames from EVERY run (main dashboard or
    admin), so an analysis started anywhere is visible live here. The client
    sends nothing; it just receives a connect-time snapshot then live frames."""
    await websocket.accept()
    live_hub.set_loop(asyncio.get_running_loop())
    queue = live_hub.subscribe()
    try:
        # Replay buffered frames for active/recent runs so a late client catches up.
        await websocket.send_json({"type": "snapshot", "runs": live_hub.snapshot()})
        while True:
            frame = await queue.get()
            await websocket.send_json(frame)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover
        logger.debug("admin live feed closed: %s", exc)
    finally:
        live_hub.unsubscribe(queue)

# =============================================================================
# Backtesting Endpoints
# =============================================================================

class RunBacktestRequest(BaseModel):
    ticker: str
    start_date: str
    end_date: str
    interval: int = 5
    initial_capital: float = 1000000.0
    selected_analysts: List[str] = ["market", "fundamentals", "news", "social"]

@app.get("/api/backtests")
async def list_backtests():
    """List all backtest results (LLM + Backtrader) from backtest_results/ directory."""
    audit_dir = PROJECT_ROOT / "backtest_results"
    if not audit_dir.exists():
        _dbg_log(
            hypothesis_id="A",
            location="server/api_server.py:list_backtests",
            message="backtest_results directory missing",
            data={"expected": str(audit_dir), "cwd": os.getcwd()},
            run_id="pre-fix",
        )
        return {"sessions": []}

    sessions = []

    # LLM multi-agent reports
    for file in sorted(audit_dir.glob("report_*.json"), reverse=True):
        try:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)
            session_id = file.stem.replace("report_", "")
            ticker = data.get("session", "")
            # Normalize metrics for dashboard
            normalized = _normalize_llm_report(data, session_id=session_id)
            llm = (normalized.get("llm") or {}) if isinstance(normalized, dict) else {}
            start_date, end_date = _bt_period_from_report(data)
            sessions.append({
                "session_id": session_id,
                "ticker":     normalized.get("ticker") or ticker,
                "engine":     "llm_multi_agent",
                "metrics":    llm.get("metrics", {}) if isinstance(llm, dict) else {},
                "total_trades": len(data.get("trades", [])),
                "start_date": start_date,
                "end_date": end_date,
                "ran_at": _bt_ran_at_from_stem(file.stem),
            })
        except Exception:
            continue

    # Backtrader classical benchmark reports
    for file in sorted(audit_dir.glob("bt_report_*.json"), reverse=True):
        try:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)
            session_id = file.stem          # full stem: bt_report_COMI.CA_TIMESTAMP
            ticker = data.get("session", "")
            normalized = _normalize_bt_report(data, session_id=session_id)
            bt = (normalized.get("bt") or {}) if isinstance(normalized, dict) else {}
            start_date, end_date = _bt_period_from_report(data)
            sessions.append({
                "session_id": session_id,
                "ticker":     normalized.get("ticker") or ticker,
                "engine":     "classical_technical",
                "metrics":    bt.get("metrics", {}) if isinstance(bt, dict) else {},
                "total_trades": len(data.get("trades", [])),
                "start_date": start_date,
                "end_date": end_date,
                "ran_at": _bt_ran_at_from_stem(file.stem),
            })
        except Exception:
            continue

    sessions.sort(key=lambda s: s.get("ran_at") or s["session_id"], reverse=True)
    return {"sessions": sessions}


def _bt_period_from_report(data: Dict[str, Any]) -> tuple:
    """Best-effort (start_date, end_date) for a backtest report.

    The chosen window is the first/last bar of the recorded equity curve;
    falls back to the first/last trade date when the curve is empty.
    """
    daily = data.get("daily_portfolio") if isinstance(data.get("daily_portfolio"), list) else []
    if daily:
        return daily[0].get("date"), daily[-1].get("date")
    trades = data.get("trades") if isinstance(data.get("trades"), list) else []
    if trades:
        return trades[0].get("date"), trades[-1].get("date")
    return None, None


def _bt_ran_at_from_stem(stem: str) -> Optional[str]:
    """Parse the run timestamp embedded in a report filename stem.

    Filenames look like ``report_COMI.CA_20260625_024112`` or
    ``bt_report_COMI.CA_20260625_024112``. Returns an ISO 8601 string or None.
    """
    import re as _re
    m = _re.search(r"(\d{8})_(\d{6})$", stem)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").isoformat() + "Z"
    except ValueError:
        return None

@app.get("/api/backtests/compare/{ticker}")
async def compare_backtests(ticker: str):
    """
    Find the most recent LLM + Backtrader reports for a ticker and return
    side-by-side data suitable for the comparison table and dual equity curve.
    """
    audit_dir = PROJECT_ROOT / "backtest_results"
    if not audit_dir.exists():
        _dbg_log(
            hypothesis_id="A",
            location="server/api_server.py:compare_backtests",
            message="backtest_results directory missing",
            data={"expected": str(audit_dir), "cwd": os.getcwd(), "ticker": ticker},
            run_id="pre-fix",
        )
        raise HTTPException(404, "No backtest results found")

    # Normalise ticker to SYMBOL.CA format
    ticker_norm = _normalize_ticker(ticker)

    llm_files = sorted(audit_dir.glob(f"report_{ticker_norm}_*.json"), reverse=True)
    bt_files  = sorted(audit_dir.glob(f"bt_report_{ticker_norm}_*.json"), reverse=True)

    if not llm_files and not bt_files:
        _dbg_log(
            hypothesis_id="D",
            location="server/api_server.py:compare_backtests",
            message="No reports found for ticker",
            data={"ticker_input": ticker, "ticker_norm": ticker_norm},
            run_id="pre-fix",
        )
        raise HTTPException(404, f"No backtest results found for {ticker_norm}")

    result: Dict[str, Any] = {"ticker": ticker_norm, "llm": None, "bt": None}

    if llm_files:
        with open(llm_files[0], encoding="utf-8") as f:
            llm_data = json.load(f)
        normalized = _normalize_llm_report(llm_data, session_id=llm_files[0].stem.replace("report_", ""))
        result["llm"] = (normalized.get("llm") if isinstance(normalized, dict) else None)

    if bt_files:
        with open(bt_files[0], encoding="utf-8") as f:
            bt_data = json.load(f)
        normalized = _normalize_bt_report(bt_data, session_id=bt_files[0].stem)
        result["bt"] = (normalized.get("bt") if isinstance(normalized, dict) else None)

    return result


@app.get("/api/backtests/{session_id}")
async def get_backtest_detail(session_id: str):
    """Retrieve detailed execution trace for a single backtest run.

    Returns the dashboard-contract shape (numeric metrics, normalized
    trades + equity series). Both the LLM multi-agent reports and the
    Backtrader benchmark reports are handled.
    """
    audit_dir = PROJECT_ROOT / "backtest_results"

    target_file = audit_dir / f"report_{session_id}.json"
    is_llm = True
    if not target_file.exists():
        target_file = audit_dir / f"{session_id}.json"
        is_llm = False
    if not target_file.exists():
        raise HTTPException(404, "Backtest not found")

    try:
        with open(target_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        raise HTTPException(500, f"Error reading report: {e}")

    try:
        if is_llm:
            normalized = _normalize_llm_report(raw, session_id=session_id)
            block = normalized.get("llm") or {}
            engine = "llm_multi_agent"
        else:
            normalized = _normalize_bt_report(raw, session_id=session_id)
            block = normalized.get("bt") or {}
            engine = "classical_technical"

        # Pull through fields the dashboard surfaces directly.
        daily = block.get("daily_portfolio") or []
        start_date = daily[0]["date"] if daily else None
        end_date = daily[-1]["date"] if daily else None

        return {
            "session_id": session_id,
            "ticker": normalized.get("ticker"),
            "engine": engine,
            "start_date": start_date,
            "end_date": end_date,
            "metrics": block.get("metrics") or {},
            "trades": block.get("trades") or [],
            "daily_portfolio": daily,
            "benchmark_history": block.get("benchmark_history") or [],
            # Pass these through so the dashboard's Scenario Comparison +
            # rich agent-text cards have everything they need.
            "buyhold_history": block.get("buyhold_history") or [],
            "benchmark": block.get("benchmark") or {},
            # Thesis decision-quality evidence + per-prediction drill-down.
            # predictions[] carries session_id per date → the dashboard opens
            # each prediction's full reasoning trace (same screen as a live run).
            "decision_quality": block.get("decision_quality"),
            "predictions": block.get("predictions") or [],
            "run_config": block.get("run_config"),
            "scenario_comparison": block.get("scenario_comparison"),
            "directional_accuracy": block.get("directional_accuracy"),
            "audit_log": raw.get("audit_log") or [],
            "cost_model": raw.get("cost_model") or {},
            "error": block.get("error"),
        }
    except Exception as e:
        raise HTTPException(500, f"Error normalizing report: {e}")

class RunBtRequest(BaseModel):
    ticker: str
    start_date: str
    end_date: str
    initial_capital: float = 1_000_000.0


@app.post("/api/backtests/run-bt")
async def run_bt_endpoint(req: RunBtRequest, background_tasks: BackgroundTasks):
    """Trigger a Backtrader classical technical benchmark run in the background."""
    def _run():
        try:
            from scripts.bt_benchmark import run_bt_benchmark
            run_bt_benchmark(
                ticker=req.ticker,
                start_date=req.start_date,
                end_date=req.end_date,
                initial_capital=req.initial_capital,
            )
        except Exception as e:
            print(f"Background BT benchmark failed: {e}")
            traceback.print_exc()

    background_tasks.add_task(_run)
    return {"status": "started", "message": f"Backtrader benchmark started for {req.ticker}."}


@app.post("/api/backtests/run")
async def run_backtest_endpoint(req: RunBacktestRequest, background_tasks: BackgroundTasks):
    """Trigger a new asynchronous backtest execution spanning a given timeframe."""
    # Normalize ticker to SYMBOL.CA (matches compare/list filters)
    req.ticker = _normalize_ticker(req.ticker)

    _dbg_log(
        hypothesis_id="A",
        location="server/api_server.py:run_backtest_endpoint",
        message="Backtest request accepted",
        data={
            "ticker": req.ticker,
            "start_date": req.start_date,
            "end_date": req.end_date,
            "interval": req.interval,
            "initial_capital": req.initial_capital,
            "selected_analysts_count": len(req.selected_analysts or []),
            "cwd": os.getcwd(),
            "project_root": str(PROJECT_ROOT),
            "results_dir_expected": str(PROJECT_ROOT / "backtest_results"),
        },
        run_id="pre-fix",
    )

    def run_bt():
        try:
            # Scenario event-study: decide ONCE on start_date, evaluate at end_date.
            # Market + fundamentals only; local CSV data; no news/social.
            from scripts.scenario_backtest import run_single_scenario
            run_single_scenario(
                req.ticker,
                start=req.start_date,
                end=req.end_date,
                initial_capital=req.initial_capital,
                rfr=0.0,
            )
            # Hypothesis A: report may be written to wrong dir; verify expected dir has report.
            try:
                out_dir = PROJECT_ROOT / "backtest_results"
                latest = next(iter(sorted(out_dir.glob(f"report_{req.ticker}_*.json"), reverse=True)), None)
                _dbg_log(
                    hypothesis_id="A",
                    location="server/api_server.py:run_backtest_endpoint",
                    message="Backtest background task completed",
                    data={
                        "results_dir_expected": str(out_dir),
                        "latest_report_found": str(latest) if latest else None,
                    },
                    run_id="pre-fix",
                )
            except Exception:
                pass
        except Exception as e:
            # Emit a report-shaped error artifact so the dashboard can surface it.
            try:
                out_dir = PROJECT_ROOT / "backtest_results"
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                err_path = out_dir / f"report_{req.ticker}_{ts}.json"
                payload = {
                    "session": req.ticker,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                }
                err_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                _dbg_log(
                    hypothesis_id="C",
                    location="server/api_server.py:run_backtest_endpoint",
                    message="Backtest background task failed (error report written)",
                    data={"error": str(e), "error_report": str(err_path)},
                    run_id="pre-fix",
                )
            except Exception:
                _dbg_log(
                    hypothesis_id="C",
                    location="server/api_server.py:run_backtest_endpoint",
                    message="Backtest background task failed (could not write error report)",
                    data={"error": str(e)},
                    run_id="pre-fix",
                )
    
    background_tasks.add_task(run_bt)
    return {"status": "started", "message": f"Backtest started for {req.ticker}."}




@app.websocket("/api/analyze")
async def analyze_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for streaming analysis.

    Client sends: { "ticker": "COMI.CA", "trade_date": "2026-02-12", ... }
    Server streams: { "type": "agent_update", "node": "...", "data": {...} }

    Progress events are published via Redis by the agent pipeline and forwarded
    here in real-time alongside the existing chunk-based state streaming.
    """
    await websocket.accept()

    try:
        # Wait for analysis request
        raw = await websocket.receive_text()
        request = json.loads(raw)

        ticker = request.get("ticker", "")
        trade_date = request.get("trade_date", datetime.now().strftime("%Y-%m-%d"))
        selected_analysts = request.get("selected_analysts", ["market", "social", "news", "fundamentals"])
        max_debate_rounds = request.get("max_debate_rounds", 1)
        max_risk_rounds = request.get("max_risk_rounds", 1)

        if not ticker:
            await websocket.send_json({
                "type": "error",
                "node": "System",
                "status": "error",
                "state_keys": [],
                "data": {"message": "Ticker is required"},
                "timestamp": datetime.now().isoformat(),
            })
            return

        # Prevent concurrent analyses
        if active_analyses.get(ticker):
            await websocket.send_json({
                "type": "error",
                "node": "System",
                "status": "error",
                "state_keys": [],
                "data": {"message": f"Analysis already running for {ticker}"},
                "timestamp": datetime.now().isoformat(),
            })
            return

        active_analyses[ticker] = True

        # Send initialization message
        await websocket.send_json({
            "type": "agent_update",
            "node": "System",
            "status": "in_progress",
            "state_keys": [],
            "data": {
                "message": f"Starting analysis for {ticker} on {trade_date}",
                "ticker": ticker,
                "trade_date": trade_date,
                "selected_analysts": selected_analysts,
            },
            "timestamp": datetime.now().isoformat(),
        })

        # ── Redis progress events + graph chunk streaming, merged ─────────────
        # The agent pipeline publishes progress events (prefetch_started,
        # agent_finished, final_decision) to Redis. We subscribe here and
        # forward them to the browser alongside the existing chunk-based
        # state streaming from _run_streaming_analysis.
        try:
            from redis_pubsub import AgentEventSubscriber
            REDIS_STREAMING = True
        except ImportError:
            REDIS_STREAMING = False

        try:
            if REDIS_STREAMING:
                # Run graph + Redis subscriber concurrently
                redis_task = asyncio.create_task(
                    _forward_redis_events(websocket, ticker)
                )
                await _run_streaming_analysis(
                    websocket, ticker, trade_date,
                    selected_analysts, max_debate_rounds, max_risk_rounds
                )
                # Cancel redis forwarding once graph is done
                redis_task.cancel()
                try:
                    await redis_task
                except asyncio.CancelledError:
                    pass
            else:
                await _run_streaming_analysis(
                    websocket, ticker, trade_date,
                    selected_analysts, max_debate_rounds, max_risk_rounds
                )
        finally:
            active_analyses.pop(ticker, None)

    except WebSocketDisconnect:
        pass
    except json.JSONDecodeError:
        await websocket.send_json({
            "type": "error",
            "node": "System",
            "status": "error",
            "state_keys": [],
            "data": {"message": "Invalid JSON in request"},
            "timestamp": datetime.now().isoformat(),
        })
    except Exception as e:
        try:
            await websocket.send_json({
                "type": "error",
                "node": "System",
                "status": "error",
                "state_keys": [],
                "data": {"message": str(e), "traceback": traceback.format_exc()},
                "timestamp": datetime.now().isoformat(),
            })
        except Exception:
            pass


async def _forward_redis_events(websocket: WebSocket, ticker: str):
    """Background task: forward Redis pub/sub events to the WebSocket client."""
    try:
        from redis_pubsub import AgentEventSubscriber
        async with AgentEventSubscriber(ticker, timeout_seconds=300) as sub:
            async for event in sub:
                try:
                    await websocket.send_json({
                        "type": "agent_update",
                        "node": event.get("agent", event.get("stage", "System")),
                        "status": "completed" if event.get("event") == "agent_finished" else "in_progress",
                        "state_keys": [],
                        "data": event,
                        "timestamp": event.get("timestamp", datetime.now().isoformat()),
                    })
                except Exception:
                    break
    except Exception as exc:
        logger.warning("Redis websocket forwarding unavailable for %s: %s", ticker, exc)


async def _run_streaming_analysis(
    websocket: WebSocket,
    ticker: str,
    trade_date: str,
    selected_analysts: List[str],
    max_debate_rounds: int,
    max_risk_rounds: int,
):
    """Run the TradingAgentsGraph and stream state updates over WebSocket."""

    config = get_config()

    # Also broadcast this run to the admin live feed (so another admin tab / the
    # Agent Monitor can watch it). Best-effort.
    from uuid import uuid4
    _run_id = uuid4().hex
    live_hub.set_loop(asyncio.get_running_loop())
    _hub_publish(_run_id, ticker, trade_date, "admin", ftype="run_started",
                 node="System", status="in_progress",
                 data={"selected_analysts": selected_analysts})

    # In `stream_mode="updates"` each chunk has the shape
    #   { "<Node Name>": <state delta dict>, ... }
    # so the node name is literally the key. We still fall back to the old
    # "values"-mode key sniffing in case some node emits a state-shaped chunk.
    def get_node_from_chunk(chunk: dict) -> tuple[str, str, list[str]]:
        """Returns (node_name, status, changed_keys)."""
        node_keys = [k for k in chunk.keys() if k != "messages"]

        # Updates mode: one outer key = the node that just finished.
        if len(node_keys) == 1:
            node = node_keys[0]
            delta = chunk[node] if isinstance(chunk[node], dict) else {}
            inner_keys = list(delta.keys()) if isinstance(delta, dict) else []
            return node, "completed", inner_keys

        # Fallback: values-mode shape (full state). Reuse the original heuristics.
        if "market_report" in chunk:
            return "Market Analyst", "completed", node_keys
        if "sentiment_report" in chunk:
            return "Social Analyst", "completed", node_keys
        if "news_report" in chunk:
            return "News Analyst", "completed", node_keys
        if "fundamentals_report" in chunk:
            return "Fundamentals Analyst", "completed", node_keys
        if "trader_investment_plan" in chunk:
            return "Trader", "completed", node_keys
        if "final_trade_decision" in chunk:
            return "Risk Judge", "completed", node_keys
        return "System", "in_progress", node_keys

    def serialize_chunk_data(chunk: dict) -> dict:
        """Serialize chunk data to JSON-safe format."""
        result = {}
        for key, value in chunk.items():
            if key == "messages":
                # Extract message content for the feed
                if value and len(value) > 0:
                    last_msg = value[-1]
                    if hasattr(last_msg, "content"):
                        content = last_msg.content
                        if isinstance(content, list):
                            content = " ".join(
                                item.get("text", str(item))
                                if isinstance(item, dict) else str(item)
                                for item in content
                            )
                        result["_message"] = content
                    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                        result["_tool_calls"] = [
                            {"name": tc["name"] if isinstance(tc, dict) else tc.name,
                             "args": tc["args"] if isinstance(tc, dict) else tc.args}
                            for tc in last_msg.tool_calls
                        ]
            else:
                try:
                    json.dumps(value)  # Test serializability
                    result[key] = value
                except (TypeError, ValueError):
                    result[key] = str(value)
        return result

    # True streaming: graph runs in a thread and pushes chunks into an asyncio
    # Queue; this coroutine drains the queue and sends each chunk over the
    # WebSocket as soon as it arrives — no buffering until completion.
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    _SENTINEL = object()  # marks end-of-stream

    def run_graph_streaming():
        """Synchronous graph execution — runs in thread pool, pushes to queue."""
        run_config = {**config}
        run_config["max_debate_rounds"] = max_debate_rounds
        run_config["max_risk_discuss_rounds"] = max_risk_rounds
        graph = TradingAgentsGraph(
            config=run_config,
            selected_analysts=selected_analysts,
            debug=True,
        )
        graph.ticker = ticker

        init_state = graph.propagator.create_initial_state(ticker, trade_date)
        args = graph.propagator.get_graph_args()
        # Override stream_mode for the websocket path so each node emits its
        # own chunk (otherwise the parallel analyst fan-out would block all
        # progress events until every analyst finished — 60-120s of silence).
        args["stream_mode"] = "updates"

        try:
            for chunk in graph.graph.stream(init_state, **args):
                loop.call_soon_threadsafe(queue.put_nowait, chunk)
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

    # Launch graph in background thread
    executor_future = loop.run_in_executor(None, run_graph_streaming)

    # Stream-mode "updates" emits only state deltas, so accumulate into a
    # running final_state for the completion frame.
    final_state: dict = {}
    HEARTBEAT_SEC = 5
    started_at = datetime.now()
    last_node_seen = "System"

    try:
        while True:
            # Wait for the next chunk, but emit a heartbeat every HEARTBEAT_SEC
            # so the UI knows the graph is alive during long LLM calls.
            try:
                item = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SEC)
            except asyncio.TimeoutError:
                elapsed = int((datetime.now() - started_at).total_seconds())
                await websocket.send_json({
                    "type": "agent_update",
                    "node": last_node_seen,
                    "status": "in_progress",
                    "state_keys": [],
                    "data": {
                        "_heartbeat": True,
                        "elapsed_sec": elapsed,
                        "message": f"Still running… ({elapsed}s elapsed)",
                    },
                    "timestamp": datetime.now().isoformat(),
                })
                continue

            if item is _SENTINEL:
                break
            if isinstance(item, Exception):
                raise item

            chunk = item

            # Merge into final_state (updates-mode chunk is {node: delta}).
            for _node_key, delta in chunk.items():
                if isinstance(delta, dict):
                    final_state.update(delta)
                else:
                    final_state[_node_key] = delta

            has_non_msg = any(k != "messages" for k in chunk.keys())
            msgs = chunk.get("messages") or []

            if not msgs and not has_non_msg:
                continue

            node, status, keys = get_node_from_chunk(chunk)
            last_node_seen = node
            data = serialize_chunk_data(chunk)
            await websocket.send_json({
                "type": "agent_update",
                "node": node,
                "status": status,
                "state_keys": keys,
                "data": data,
                "timestamp": datetime.now().isoformat(),
            })
            _hub_publish(_run_id, ticker, trade_date, "admin", ftype="agent_update",
                         node=node, status=status, state_keys=keys, data={})
    finally:
        await executor_future  # ensure thread is fully done

    # Send completion
    await websocket.send_json({
        "type": "complete",
        "node": "System",
        "status": "completed",
        "state_keys": list(final_state.keys()),
        "data": {
            "final_trade_decision": final_state.get("final_trade_decision", ""),
            "risk_assessment": final_state.get("risk_assessment", {}),
            "message": "Analysis complete",
        },
        "timestamp": datetime.now().isoformat(),
    })
    _hub_publish(_run_id, ticker, trade_date, "admin", ftype="complete",
                 node="Final Recommendation", status="completed",
                 data={"final_trade_decision": str(final_state.get("final_trade_decision", ""))[:300]})


# =============================================================================
# Entry Point
# =============================================================================

_QUICK_CONF_MAP = {"HIGH": 0.8, "MEDIUM": 0.55, "MED": 0.55, "LOW": 0.3}


def _quick_conf_to_float(value: Any) -> Optional[float]:
    """Map a quick-analysis confidence label (or number) to a 0–1 float."""
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 100.0 if v > 1.0 else v
    if isinstance(value, str):
        return _QUICK_CONF_MAP.get(value.strip().upper())
    return None


def _persist_quick_analysis_to_pg(
    *, session_id: str, ticker: str, trade_date: str,
    result: Dict[str, Any], rec: Dict[str, Any],
) -> None:
    """Write a quick (single-LLM) dashboard analysis into Supabase as a full
    ``run_type='live'`` session plus synthetic agent_events, so it appears in
    "My Analyses" and renders its full reasoning in the trace panel.

    No-ops silently when Postgres is unavailable (the JSONL fallback still runs).
    """
    from tradingagents.db import audit_writer, is_postgres_available
    from tradingagents.default_config import DEFAULT_CONFIG
    if not is_postgres_available():
        return

    signal = str(rec.get("signal", "HOLD") or "HOLD").upper()
    overall = _quick_conf_to_float(rec.get("confidence"))

    # A final-state-shaped dict the writers already know how to project.
    synthetic_state: Dict[str, Any] = {
        "final_trade_decision": signal,
        "confidence_scores": {"overall": overall} if overall is not None else {},
        "target_market": "EGX",
        "analysis_mode": "quick",
        "price": result.get("price"),
        "indicators": result.get("indicators"),
        "recommendation": rec,
        "investment_debate_state": {
            "bull_history": rec.get("bull_case"),
            "bear_history": rec.get("bear_case"),
            "judge_decision": "\n\n".join(
                p for p in (rec.get("neutral_case"), rec.get("rationale")) if p
            ) or None,
        },
        "trader_investment_plan": rec.get("recommendation"),
    }

    fingerprint = audit_writer.build_model_fingerprint(dict(DEFAULT_CONFIG))
    fingerprint["analysis_mode"] = "quick"
    audit_writer.write_analysis_session(
        session_id=session_id,
        ticker=ticker,
        trade_date=trade_date,
        final_state=synthetic_state,
        model_fingerprint=fingerprint,
        user_id="local",
        run_type="live",
    )
    audit_writer.write_agent_events(
        session_id=session_id,
        final_state=synthetic_state,
        model_fingerprint=fingerprint,
    )


# =============================================================================
# Test / Utility Endpoints
# =============================================================================

class TestEgxRequest(BaseModel):
    ticker: Optional[str] = None

@app.post("/api/test/random-egx")
async def test_random_egx(req: TestEgxRequest = TestEgxRequest()):
    """
    Run EGX stock test using the CLI script logic.
    If ticker is provided in the JSON body, analyzes that specific stock.
    Otherwise, picks a random stock from the EGX list.
    Returns structured analysis data.
    """
    import random
    
    # Lazy import to avoid circular dep issues early on
    try:
        from run_egx_prediction import analyze_ticker_for_api
        from tradingagents.default_config import EGX_TICKERS
    except ImportError:
        # Fallback if imports fail (e.g. running from wrong dir)
        sys.path.append(str(PROJECT_ROOT))
        from run_egx_prediction import analyze_ticker_for_api
        from tradingagents.default_config import EGX_TICKERS

    # Use provided ticker or pick a random one
    if req.ticker:
        selected_ticker = req.ticker if req.ticker.endswith('.CA') else f"{req.ticker}.CA"
    else:
        selected_ticker = random.choice(EGX_TICKERS)
    
    try:
        # Run analysis in thread pool because it's blocking IO
        result = await asyncio.to_thread(analyze_ticker_for_api, selected_ticker)

        llm_failed = bool(result.get("error") or result.get("llm_error"))

        # Persist to audit_logs so History page can discover it
        if not llm_failed:
            try:
                import uuid
                session_id = str(uuid.uuid4())[:8]
                ticker_clean = selected_ticker.replace(".CA", "")
                audit_dir = PROJECT_ROOT / "audit_logs" / ticker_clean
                audit_dir.mkdir(parents=True, exist_ok=True)
                
                now = datetime.utcnow().isoformat() + "Z"
                rec = result.get("recommendation", {})

                # ── Persist the FULL quick-analysis to Supabase (run_type='live')
                # so it shows in "My Analyses" with every detail viewable, exactly
                # like a full multi-agent run. The synthetic agent_events let the
                # existing trace UI render the bull / bear / judge sections.
                try:
                    _persist_quick_analysis_to_pg(
                        session_id=session_id,
                        ticker=selected_ticker,
                        trade_date=datetime.now().strftime("%Y-%m-%d"),
                        result=result,
                        rec=rec,
                    )
                except Exception as _pg_err:
                    logger.warning("Quick-analysis Supabase persist failed: %s", _pg_err)

                # Write JSONL entries (legacy on-disk fallback for when PG is down)
                jsonl_path = audit_dir / "audit_log.jsonl"
                entries = [
                    {
                        "event": "SESSION_START",
                        "_session_id": session_id,
                        "trade_date": datetime.now().strftime("%Y-%m-%d"),
                        "market": "EGX",
                        "_logged_at": now,
                    },
                    {
                        "event": "QUICK_ANALYSIS_RESULT",
                        "_session_id": session_id,
                        "agent_name": "Quick Analysis",
                        "signal": rec.get("signal", "HOLD"),
                        "confidence": rec.get("confidence", "MEDIUM"),
                        "target_price": rec.get("target_price"),
                        "stop_loss": rec.get("stop_loss"),
                        "risk": rec.get("risk"),
                        "_logged_at": now,
                    },
                ]

                # PR 9: add SENTIMENT_CONTEXT event when final_state is available
                try:
                    from tradingagents.sentiment.surfacing import build_sentiment_context_event
                    _final_state = result.get("_final_state") or {}
                    if _final_state:
                        entries.append(
                            build_sentiment_context_event(
                                session_id=session_id,
                                ticker=ticker_clean,
                                trade_date=datetime.now().strftime("%Y-%m-%d"),
                                final_state=_final_state,
                                logged_at=now,
                            )
                        )
                except Exception:
                    pass  # sentiment context is best-effort; never fail the audit write

                with open(jsonl_path, "a", encoding="utf-8") as f:
                    for entry in entries:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                
                # Write markdown summary
                md_path = audit_dir / "audit_summary.md"
                price = result.get("price", {})
                indicators = result.get("indicators", {})
                md_content = f"""# {selected_ticker} — Quick Analysis Report

**Date:** {datetime.now().strftime("%Y-%m-%d %H:%M")}  
**Signal:** {rec.get("signal", "N/A")} | **Confidence:** {rec.get("confidence", "N/A")} | **Risk:** {rec.get("risk", "N/A")}

## Price Summary
- Current: {price.get("current", 0):.2f} EGP ({price.get("source", "N/A")})
- Daily Change: {price.get("daily_change", 0):+.2f}%
- Weekly Change: {price.get("weekly_change", 0):+.2f}%

## Technical Indicators
- SMA(5): {indicators.get("sma_5", "N/A")} | SMA(10): {indicators.get("sma_10", "N/A")}
- RSI(14): {indicators.get("rsi", "N/A")} | Trend: {indicators.get("trend", "N/A")}

## Bull Case
{rec.get("bull_case", "N/A")}

## Bear Case
{rec.get("bear_case", "N/A")}

## Neutral Analysis (Judge)
{rec.get("neutral_case", "N/A")}

## Rationale
{rec.get("rationale", "N/A")}

## Investment Plan
{rec.get("recommendation", "N/A")}

---
*Target Price: {rec.get("target_price", "N/A")} EGP | Stop Loss: {rec.get("stop_loss", "N/A")} EGP*
"""
                md_path.write_text(md_content, encoding="utf-8")
                
                # Attach session_id to the result so frontend can reference it
                result["session_id"] = session_id
                
            except Exception as audit_err:
                # Don't fail the main response if audit logging fails
                print(f"[WARN] Failed to write audit log: {audit_err}")
        
        if llm_failed:
            return {**result, "status": "degraded"}
        return result
    except Exception as e:
        return {"error": f"Analysis failed for {selected_ticker}: {str(e)}"}


class FullAnalyzeRequest(BaseModel):
    ticker: str
    selected_analysts: Optional[List[str]] = None
    max_debate_rounds: int = 1
    max_risk_rounds: int = 1


@app.post("/api/analyze-full")
async def analyze_full(req: FullAnalyzeRequest):
    """
    Run the FULL multi-agent TradingAgentsGraph (market + fundamentals + news +
    social analysts -> bull/bear debate -> research manager -> trader -> risk
    manager) and return a PredictionResult-shaped payload that the dashboard's
    existing UI can render.

    This is the synchronous counterpart to the /api/analyze WebSocket endpoint
    — same engine, no streaming. Expect a 3-8 minute wall time.
    """
    # Lazy import (matches test_random_egx pattern)
    try:
        from run_egx_prediction import analyze_ticker_for_api
        from tradingagents.default_config import EGX_TICKERS
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from tradingagents.graph.signal_processing import SignalProcessor
        from langchain_openai import ChatOpenAI
    except ImportError:
        sys.path.append(str(PROJECT_ROOT))
        from run_egx_prediction import analyze_ticker_for_api
        from tradingagents.default_config import EGX_TICKERS
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from tradingagents.graph.signal_processing import SignalProcessor
        from langchain_openai import ChatOpenAI

    ticker = req.ticker if req.ticker.endswith(".CA") else f"{req.ticker}.CA"
    selected_analysts = req.selected_analysts or ["market", "social", "news", "fundamentals"]
    trade_date = datetime.now().strftime("%Y-%m-%d")

    # Live broadcast — make this run visible in the admin dashboard in real time.
    from uuid import uuid4
    run_id = uuid4().hex
    live_hub.set_loop(asyncio.get_running_loop())
    _hub_publish(
        run_id, ticker, trade_date, "main",
        ftype="run_started", node="System", status="in_progress",
        data={"message": f"Full pipeline started for {ticker}",
              "selected_analysts": selected_analysts},
    )

    # Step 1: pull price/indicators/price_history (cheap; reuses existing helper).
    # The quick-LLM recommendation it returns is discarded — we overwrite it
    # below with the full multi-agent graph output.
    try:
        base = await asyncio.to_thread(analyze_ticker_for_api, ticker)
    except Exception as exc:
        return {"error": f"Price fetch failed for {ticker}: {exc}"}

    if base.get("error"):
        return base

    # Step 2: run the full TradingAgentsGraph synchronously in a thread.
    def _run_graph():
        from copy import deepcopy
        run_config = deepcopy(get_config())
        run_config["max_debate_rounds"] = req.max_debate_rounds
        run_config["max_risk_discuss_rounds"] = req.max_risk_rounds
        graph = TradingAgentsGraph(
            config=run_config,
            selected_analysts=selected_analysts,
            debug=False,
        )
        graph.ticker = ticker
        init_state = graph.propagator.create_initial_state(ticker, trade_date)
        args = graph.propagator.get_graph_args()
        # updates-mode so each node emits its own chunk → live per-agent frames.
        args["stream_mode"] = "updates"
        final_state: dict = {}
        for chunk in graph.graph.stream(init_state, **args):
            if isinstance(chunk, dict):
                for _node, delta in chunk.items():
                    if isinstance(delta, dict):
                        final_state.update(delta)
                if any(k != "messages" for k in chunk.keys()):
                    try:
                        node, status, keys = _hub_node_from_chunk(chunk)
                        _hub_publish(run_id, ticker, trade_date, "main",
                                     ftype="agent_update", node=node, status=status,
                                     state_keys=keys, data={})
                    except Exception:
                        pass
        return final_state

    try:
        final_state = await asyncio.to_thread(_run_graph)
    except Exception as exc:
        # Surface the full traceback to the server log so we can diagnose.
        # Without this the failure is invisible (the JSON response carries
        # `llm_error` but the terminal stays silent).
        import traceback as _tb
        print(f"\n[/api/analyze-full] FULL PIPELINE FAILED for {ticker}:", flush=True)
        print(_tb.format_exc(), flush=True)
        _hub_publish(run_id, ticker, trade_date, "main", ftype="error",
                     node="System", status="error", data={"message": str(exc)})
        return {
            **base,
            "llm_error": f"Full pipeline failed: {exc}",
            "status": "degraded",
        }

    try:
        from tradingagents.db import audit_writer
        fingerprint = audit_writer.build_model_fingerprint(get_config())
        
        # Inject base fields into final_state so they are preserved in the DB
        state_to_save = dict(final_state)
        state_to_save["price"] = base.get("price")
        state_to_save["indicators"] = base.get("indicators")
        state_to_save["technical_panel"] = base.get("technical_panel")
        state_to_save["price_history"] = base.get("price_history")
        
        audit_writer.write_analysis_session(
            session_id=run_id,
            ticker=ticker,
            trade_date=trade_date,
            final_state=state_to_save,
            model_fingerprint=fingerprint,
            user_id="local",
            run_type="live",
        )
        audit_writer.write_agent_events(
            session_id=run_id,
            final_state=state_to_save,
            model_fingerprint=fingerprint,
        )
    except Exception as exc:
        logger.warning("Audit write failed in analyze-full: %s", exc)

    _hub_publish(
        run_id, ticker, trade_date, "main",
        ftype="complete", node="Final Recommendation", status="completed",
        data={"final_trade_decision": str(final_state.get("final_trade_decision", ""))[:300]},
    )

    # Step 3: map final_state -> Recommendation shape the UI already consumes.
    debate = final_state.get("investment_debate_state") or {}
    bull_thesis = debate.get("bull_thesis") or {}
    bear_thesis = debate.get("bear_thesis") or {}
    judge_decision = debate.get("judge_decision") or final_state.get("investment_plan") or ""
    final_decision_text = final_state.get("final_trade_decision") or ""

    # Extract BUY/SELL/HOLD via the same regex processor the graph uses
    try:
        signal = SignalProcessor(None).process_signal(final_decision_text) or "HOLD"
    except Exception:
        signal = "HOLD"
    signal = (signal or "HOLD").upper()

    # Build bull/bear strings. The full LLM argument lives in `*_history` as
    # prose followed by a ```json ...``` block, and the prose is where the
    # researcher actually discusses news headlines, social sentiment, and
    # signal alignment. The structured `thesis` JSON only carries a one-word
    # signal_summary.sentiment field — useless on its own. Prefer the prose;
    # use the JSON only when no prose was captured.
    def _thesis_to_text(thesis: dict, history: str) -> str:
        prose = (history or "").strip()
        if prose:
            return prose
        if isinstance(thesis, dict) and thesis:
            try:
                return json.dumps(thesis, indent=2, ensure_ascii=False)
            except Exception:
                pass
        return ""

    bull_case = _thesis_to_text(bull_thesis, debate.get("bull_history", ""))
    bear_case = _thesis_to_text(bear_thesis, debate.get("bear_history", ""))

    current_price = (base.get("price") or {}).get("current") or 0
    target_price = None
    stop_loss = None
    if isinstance(bull_thesis, dict):
        upside = bull_thesis.get("upside_scenario", {}) or {}
        base_pct = upside.get("base_case_upside_pct")
        if isinstance(base_pct, (int, float)) and current_price:
            target_price = round(current_price * (1 + base_pct / 100.0), 2)
    if isinstance(bear_thesis, dict):
        downside = bear_thesis.get("downside_range", {}) or {}
        sl = downside.get("support_level_1")
        if isinstance(sl, (int, float)):
            stop_loss = float(sl)
    # Track whether the stop is a real thesis stop or a synthetic fallback. The
    # fallback sits at exactly 10% below spot, which must NOT be allowed to drive
    # the risk profile (it would pin every run to HIGH — see risk_profile below).
    stop_loss_is_default = stop_loss is None
    if stop_loss is None and current_price:
        stop_loss = round(current_price * 0.9, 2)

    confidence = "MEDIUM"
    if isinstance(bull_thesis, dict):
        cv = (bull_thesis.get("conviction_level") or "").upper()
        if cv in ("HIGH", "MEDIUM", "LOW"):
            confidence = cv

    # Unwrap the trader's structured execution plan (may be double-nested as
    # {"execution_plan": {...}}). It carries per-stock risk controls + the
    # thesis time-stop, both of which we surface instead of hardcoded constants.
    raw_plan = final_state.get("execution_plan") or {}
    plan = raw_plan.get("execution_plan", raw_plan) if isinstance(raw_plan, dict) else {}

    # Risk profile — DERIVED from the deterministic risk scorer + the stop
    # distance, not a fixed "MEDIUM". This is the real per-stock risk read.
    # THROTTLE is a position-SIZING adjustment (5-10% ADV), not an inherently
    # high-risk verdict, so it maps to MEDIUM, not HIGH.
    risk_action = str(final_state.get("risk_action") or "ALLOW").upper()
    risk_profile = {
        "VETO": "HIGH",
        "THROTTLE": "MEDIUM",
        "WARN": "MEDIUM",
        "ALLOW": "LOW",
    }.get(risk_action, "MEDIUM")
    # Escalate (never downgrade) by how far the protective stop sits from spot —
    # a WIDER stop means more capital at risk per trade. Only a REAL thesis stop
    # may drive this; the synthetic 10%-below fallback must not (it would pin
    # every fallback run to HIGH). Thresholds account for EGX's ±10% daily band,
    # so a ~10% stop is normal, not extreme.
    if current_price and stop_loss and not stop_loss_is_default:
        stop_dist_pct = abs((stop_loss - current_price) / current_price) * 100
        if stop_dist_pct >= 12:
            risk_profile = "HIGH"
        elif stop_dist_pct >= 7 and risk_profile == "LOW":
            risk_profile = "MEDIUM"

    # Time horizon — from the trader's thesis time-stop when available; None
    # (rendered as "—") rather than a fabricated "2–4 weeks" when it isn't.
    time_horizon = None
    if isinstance(plan, dict):
        exit_logic = plan.get("exit_logic") or {}
        ts = exit_logic.get("time_stop")
        if isinstance(ts, str) and ts.strip() and "[" not in ts:
            time_horizon = ts.strip()

    recommendation = {
        "signal": signal,
        "confidence": confidence,
        "target_price": target_price,
        "stop_loss": stop_loss,
        "risk": risk_profile,
        "time_horizon": time_horizon,
        "bull_case": bull_case or "No bullish thesis was returned for this run.",
        "bear_case": bear_case or "No bearish thesis was returned for this run.",
        "neutral_case": judge_decision or "",
        "rationale": judge_decision or "",
        "recommendation": final_decision_text or judge_decision or "",
        "full_text": final_decision_text,
        # Per-trading-style recommendations (Swing / Position / Long-Term) from
        # the Trader. Dashboard-only; not part of the live decision path.
        "styled_recommendations": final_state.get("styled_recommendations"),
    }

    return {
        **base,
        "session_id": run_id,
        "recommendation": recommendation,
        "llm_error": None,
        "status": "ok",
        "pipeline": "full",
        "_final_state_keys": list(final_state.keys()),
    }


@app.get("/api/technical-panel/{ticker}")
async def get_technical_panel_endpoint(ticker: str, as_of: Optional[str] = None):
    """Full Investing-style technical panel for a ticker (live, or as-of a date).

    Returns the 12 indicators + Buy/Sell/Neutral verdicts + SMA/EMA grid + summary
    tallies + 5 pivot systems, computed deterministically from OHLCV by the same engine
    the backtest dataset uses (look-ahead-safe). ``as_of`` (YYYY-MM-DD) is optional;
    defaults to the latest available trading day.
    """
    if not ticker.upper().endswith(".CA"):
        ticker = f"{ticker.upper()}.CA"
    try:
        from tradingagents.dataflows.technical_panel import get_live_panel
        return await asyncio.to_thread(get_live_panel, ticker, as_of)
    except Exception as exc:
        return {"ticker": ticker, "as_of": as_of, "error": str(exc), "panel": None}


@app.get("/api/test/egx-tickers")
async def get_egx_tickers():
    """
    Returns the list of available EGX tickers for the stock picker.
    """
    from tradingagents.default_config import EGX_TICKERS

    # Return tickers with friendly names
    ticker_list = [
        {"ticker": t, "name": t.replace(".CA", "")} 
        for t in sorted(EGX_TICKERS)
    ]
    
    return {"tickers": ticker_list}


# =============================================================================
# Investor Profiling Endpoint
# =============================================================================

@app.post("/api/investor-profile")
async def classify_investor(request: InvestorProfileRequest):
    """
    Classify an investor based on their onboarding interview.

    Accepts a free-form interview transcript and returns:
      - investor_category  : INTRADAY | SWING | POSITION_6MO
      - confidence_score   : 0.0 - 1.0
      - trigger_frequency  : cron expression for data-refresh scheduling
      - analysis_priority  : ordered list of analyst modules to prioritise
      - reasoning          : brief LLM explanation

    The trigger_frequency can be fed directly into a scheduler to drive
    automated analysis loops appropriate for this investor's velocity.
    """
    try:
        from tradingagents.agents.profiling import InvestorProfilingAgent
    except ImportError as exc:
        raise HTTPException(500, f"Profiling agent unavailable: {exc}")

    try:
        agent = InvestorProfilingAgent()
        profile = agent.classify(request.interview_text)
        return profile.model_dump()
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        logger.error("investor-profile endpoint error: %s", exc, exc_info=True)
        raise HTTPException(500, f"Classification failed: {exc}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server.api_server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )

