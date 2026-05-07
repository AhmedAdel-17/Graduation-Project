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
import traceback
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

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

    # Metrics (legacy shape from backtester.py)
    legacy = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    total_return_pct = _parse_percent(legacy.get("Total Return"))
    sharpe_ratio = _parse_float(legacy.get("Sharpe Ratio"))
    max_drawdown_pct = _parse_percent(legacy.get("Max Drawdown"))
    win_rate_pct = _parse_percent(legacy.get("Win Rate"))

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

    metrics: Dict[str, Any] = {
        "total_return_pct": total_return_pct,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown_pct": abs(max_drawdown_pct) if max_drawdown_pct is not None else None,
        "win_rate": win_rate_pct,
        "total_trades": len(trades_raw),
        "final_equity": final_equity,
        "initial_capital": initial_capital,
    }
    metrics = {k: v for k, v in metrics.items() if v is not None}

    return {
        "ticker": ticker_norm or ticker,
        "llm": {
            "session_id": session_id,
            "error": report_error,
            "metrics": metrics,
            "trades": trades,
            "daily_portfolio": equity,
            "benchmark_history": bm,
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


# =============================================================================
# Health Check
# =============================================================================

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "egx_tools": EGX_TOOLS_AVAILABLE,
    }


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
    """List all past analysis results from audit logs."""
    audit_dir = PROJECT_ROOT / "audit_logs"
    if not audit_dir.exists():
        return {"sessions": []}

    sessions = []
    for ticker_dir in sorted(audit_dir.iterdir()):
        if not ticker_dir.is_dir():
            continue
        ticker = ticker_dir.name

        # Read JSONL log for session metadata
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
                                "timestamp": entry.get("_logged_at", ""),
                            })
            except Exception:
                pass

    return {"sessions": sorted(sessions, key=lambda s: s["timestamp"], reverse=True)}


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
# WebSocket — Real-Time Analysis Streaming
# =============================================================================

# Active analysis tracking
active_analyses: Dict[str, bool] = {}

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
            sessions.append({
                "session_id": session_id,
                "ticker":     normalized.get("ticker") or ticker,
                "engine":     "llm_multi_agent",
                "metrics":    llm.get("metrics", {}) if isinstance(llm, dict) else {},
                "total_trades": len(data.get("trades", [])),
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
            sessions.append({
                "session_id": session_id,
                "ticker":     ticker,
                "engine":     "classical_technical",
                "metrics":    data.get("metrics", {}),
                "total_trades": len(data.get("trades", [])),
            })
        except Exception:
            continue

    sessions.sort(key=lambda s: s["session_id"], reverse=True)
    return {"sessions": sessions}

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
        result["bt"] = {
            "session_id":      bt_files[0].stem,
            "metrics":         bt_data.get("metrics", {}),
            "trades":          bt_data.get("trades", []),
            "daily_portfolio": bt_data.get("daily_portfolio", []),
        }

    return result


@app.get("/api/backtests/{session_id}")
async def get_backtest_detail(session_id: str):
    """Retrieve detailed execution trace for a single backtest run."""
    audit_dir = PROJECT_ROOT / "backtest_results"

    # Try LLM report first (report_{session_id}.json),
    # then BT report (session_id already contains the full stem bt_report_...)
    target_file = audit_dir / f"report_{session_id}.json"
    if not target_file.exists():
        target_file = audit_dir / f"{session_id}.json"
    if not target_file.exists():
        raise HTTPException(404, "Backtest not found")
        
    try:
        with open(target_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(500, f"Error processing file: {e}")

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
            from scripts.backtester import BacktestingEngine
            engine = BacktestingEngine(initial_capital=req.initial_capital)
            engine.run_backtest(
                ticker=req.ticker,
                start_date=req.start_date,
                end_date=req.end_date,
                interval_days=req.interval,
                analysts=req.selected_analysts
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
    except Exception:
        pass  # Redis unavailable — silent no-op


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

    # Determine the active node from chunk keys (mirrors cli/main.py logic)
    def get_node_from_chunk(chunk: dict) -> tuple[str, str, list[str]]:
        """Returns (node_name, status, changed_keys)."""
        changed = [k for k in chunk.keys() if k != "messages"]

        if "market_report" in chunk:
            return "Market Analyst", "completed", changed
        if "sentiment_report" in chunk:
            return "Social Analyst", "completed", changed
        if "news_report" in chunk:
            return "News Analyst", "completed", changed
        if "fundamentals_report" in chunk:
            return "Fundamentals Analyst", "completed", changed

        if "investment_debate_state" in chunk:
            debate = chunk["investment_debate_state"]
            if debate.get("judge_decision"):
                return "Research Manager", "completed", changed
            if debate.get("current_response", "").startswith("Bull"):
                return "Bull Researcher", "in_progress", changed
            return "Bear Researcher", "in_progress", changed

        if "trader_investment_plan" in chunk:
            return "Trader", "completed", changed

        if "risk_debate_state" in chunk:
            risk = chunk["risk_debate_state"]
            if risk.get("judge_decision"):
                return "Risk Judge", "completed", changed
            speaker = risk.get("latest_speaker", "")
            if speaker.startswith("Risky"):
                return "Risky Analyst", "in_progress", changed
            if speaker.startswith("Safe"):
                return "Safe Analyst", "in_progress", changed
            return "Neutral Analyst", "in_progress", changed

        if "final_trade_decision" in chunk:
            return "Risk Judge", "completed", changed

        return "System", "in_progress", changed

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

    # Build and run the graph in a sync thread
    def run_graph():
        """Synchronous graph execution — runs in thread pool."""
        # Inject debate/risk round settings into config
        run_config = {**config}
        run_config["max_debate_rounds"] = max_debate_rounds
        run_config["max_risk_discuss_rounds"] = max_risk_rounds
        graph = TradingAgentsGraph(
            config=run_config,
            selected_analysts=selected_analysts,
            debug=True,  # Enable streaming mode
        )
        graph.ticker = ticker

        init_state = graph.propagator.create_initial_state(ticker, trade_date)
        args = graph.propagator.get_graph_args()

        chunks = []
        for chunk in graph.graph.stream(init_state, **args):
            chunks.append(chunk)

        return chunks

    # Run in thread pool to not block the event loop
    loop = asyncio.get_event_loop()
    chunks = await loop.run_in_executor(None, run_graph)

    # Stream chunks to client
    for chunk in chunks:
        if not chunk.get("messages") or len(chunk.get("messages", [])) == 0:
            # Still send non-message state updates
            if any(k != "messages" for k in chunk.keys()):
                node, status, keys = get_node_from_chunk(chunk)
                data = serialize_chunk_data(chunk)
                await websocket.send_json({
                    "type": "agent_update",
                    "node": node,
                    "status": status,
                    "state_keys": keys,
                    "data": data,
                    "timestamp": datetime.now().isoformat(),
                })
            continue

        node, status, keys = get_node_from_chunk(chunk)
        data = serialize_chunk_data(chunk)

        await websocket.send_json({
            "type": "agent_update",
            "node": node,
            "status": status,
            "state_keys": keys,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        })

    # Send completion
    final_state = chunks[-1] if chunks else {}
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


# =============================================================================
# Entry Point
# =============================================================================

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
        from test_random_egx import EGX_TICKERS
    except ImportError:
        # Fallback if imports fail (e.g. running from wrong dir)
        sys.path.append(str(PROJECT_ROOT))
        from run_egx_prediction import analyze_ticker_for_api
        from test_random_egx import EGX_TICKERS

    # Use provided ticker or pick a random one
    if req.ticker:
        selected_ticker = req.ticker if req.ticker.endswith('.CA') else f"{req.ticker}.CA"
    else:
        selected_ticker = random.choice(EGX_TICKERS)
    
    try:
        # Run analysis in thread pool because it's blocking IO
        result = await asyncio.to_thread(analyze_ticker_for_api, selected_ticker)
        
        # Persist to audit_logs so History page can discover it
        if not result.get("error"):
            try:
                import uuid
                session_id = str(uuid.uuid4())[:8]
                ticker_clean = selected_ticker.replace(".CA", "")
                audit_dir = PROJECT_ROOT / "audit_logs" / ticker_clean
                audit_dir.mkdir(parents=True, exist_ok=True)
                
                now = datetime.utcnow().isoformat() + "Z"
                rec = result.get("recommendation", {})
                
                # Write JSONL entries
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
        
        return result
    except Exception as e:
        return {"error": f"Analysis failed for {selected_ticker}: {str(e)}"}


@app.get("/api/test/egx-tickers")
async def get_egx_tickers():
    """
    Returns the list of available EGX tickers for the stock picker.
    """
    try:
        from test_random_egx import EGX_TICKERS
    except ImportError:
        sys.path.append(str(PROJECT_ROOT))
        from test_random_egx import EGX_TICKERS
    
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

