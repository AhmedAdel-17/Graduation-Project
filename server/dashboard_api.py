"""
Dashboard backend — FastAPI app that matches the egx-agents dashboard contract.

Endpoints (all under /api/*):
  POST   /api/auth/signup        — create user + return JWT
  POST   /api/auth/login         — auth + return JWT
  GET    /api/auth/me            — current user
  POST   /api/auth/logout        — no-op (client just drops token)

  GET    /api/tickers            — list of EGX tickers (sector + Arabic name)
  GET    /api/macro/current      — current macro context
  GET    /api/macro/egx30-history?days=30
  GET    /api/ticker/{symbol}/quote

  POST   /api/predict            — kick off prediction → {job_id}
  GET    /api/jobs/{id}          — job status
  POST   /api/jobs/{id}/cancel
  GET    /api/runs               — list user's runs (paginated)
  GET    /api/runs/{id}
  DELETE /api/runs/{id}

  POST   /api/backtest           — kick off backtest → {job_id}
  GET    /api/backtests
  GET    /api/backtests/{id}
  DELETE /api/backtests/{id}

  WS     /ws/jobs/{job_id}       — live agent streaming

Run with:
    uvicorn server.dashboard_api:app --reload --port 8000

The dashboard's `src/lib/api.ts` should point at http://localhost:8000.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Load .env before any tradingagents import so the LLM keys etc. are available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Add project root so tradingagents.* imports resolve
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.orm import Session

from .dashboard_db import Backtest, Job, Run, User, get_session, init_db
from .dashboard_auth import (
    create_access_token,
    get_current_user,
    get_user_from_token,
    hash_password,
    verify_password,
)
from .dashboard_jobs import (
    clear_job,
    emit_agent_done,
    emit_agent_start,
    emit_complete,
    emit_decision,
    emit_error,
    is_cancelled,
    mark_done,
    publish,
    request_cancel,
    run_in_background,
    subscribe,
    unsubscribe,
)

logger = logging.getLogger("dashboard.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

# ---------------------------------------------------------------------------
# App + middleware
# ---------------------------------------------------------------------------

app = FastAPI(title="EGX TradingAgents Dashboard API", version="1.0.0")

# CORS — allow the Vite dev server (8080) and common alt ports
_cors_origins = os.environ.get(
    "DASHBOARD_CORS_ORIGINS",
    "http://localhost:8080,http://localhost:5173,http://127.0.0.1:8080,http://127.0.0.1:5173",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    init_db()
    logger.info("Database initialised")


# ---------------------------------------------------------------------------
# EGX ticker registry (from default_config + Arabic name lookup)
# ---------------------------------------------------------------------------

EGX_TICKER_META = {
    # Banks
    "COMI.CA": {"name_en": "Commercial International Bank", "name_ar": "البنك التجاري الدولي", "sector": "Banks"},
    "ADIB.CA": {"name_en": "Abu Dhabi Islamic Bank Egypt", "name_ar": "أبوظبي الإسلامي مصر", "sector": "Banks"},
    "CIEB.CA": {"name_en": "Crédit Agricole Egypt", "name_ar": "كريدي أجريكول مصر", "sector": "Banks"},
    "EXPA.CA": {"name_en": "Export Development Bank", "name_ar": "تنمية الصادرات", "sector": "Banks"},
    "HDBK.CA": {"name_en": "Housing & Development Bank", "name_ar": "الإسكان والتعمير", "sector": "Banks"},
    "QNBA.CA": {"name_en": "QNB Al Ahli", "name_ar": "قطر الوطني الأهلي", "sector": "Banks"},
    "SAUD.CA": {"name_en": "Saudi Egyptian Bank", "name_ar": "السعودي المصري", "sector": "Banks"},
    # Real Estate
    "TMGH.CA": {"name_en": "Talaat Moustafa Group", "name_ar": "طلعت مصطفى", "sector": "Real Estate"},
    "HELI.CA": {"name_en": "Heliopolis Housing", "name_ar": "هليوبوليس للإسكان", "sector": "Real Estate"},
    "PHDC.CA": {"name_en": "Palm Hills Developments", "name_ar": "بالم هيلز", "sector": "Real Estate"},
    "OCDI.CA": {"name_en": "Orascom Construction", "name_ar": "أوراسكوم للإنشاء", "sector": "Real Estate"},
    "ORAS.CA": {"name_en": "Orascom Development", "name_ar": "أوراسكوم للتطوير", "sector": "Real Estate"},
    "EMFD.CA": {"name_en": "Emaar Misr", "name_ar": "إعمار مصر", "sector": "Real Estate"},
    # Industrial
    "EAST.CA": {"name_en": "Eastern Tobacco", "name_ar": "الشرقية للدخان", "sector": "Industrial"},
    "ESRS.CA": {"name_en": "Ezz Steel", "name_ar": "عز للصلب", "sector": "Industrial"},
    "SWDY.CA": {"name_en": "Elsewedy Electric", "name_ar": "السويدي إليكتريك", "sector": "Industrial"},
    "ABUK.CA": {"name_en": "Abu Qir Fertilizers", "name_ar": "أبو قير للأسمدة", "sector": "Industrial"},
    "MFPC.CA": {"name_en": "Misr Fertilizers", "name_ar": "موبكو", "sector": "Industrial"},
    "EGAL.CA": {"name_en": "Egyptian Aluminum", "name_ar": "مصر للألومنيوم", "sector": "Industrial"},
    "EGCH.CA": {"name_en": "Egyptian Chemical Industries", "name_ar": "كيما", "sector": "Industrial"},
    "EFIC.CA": {"name_en": "Egyptian Financial Industrial", "name_ar": "المالية والصناعية", "sector": "Industrial"},
    # Telecom
    "ETEL.CA": {"name_en": "Telecom Egypt", "name_ar": "المصرية للاتصالات", "sector": "Telecom"},
    "FWRY.CA": {"name_en": "Fawry", "name_ar": "فوري", "sector": "Telecom"},
    "EFIH.CA": {"name_en": "EFG Hermes Holding", "name_ar": "إي إف جي هيرميس", "sector": "Telecom"},
    "RAYA.CA": {"name_en": "Raya Holding", "name_ar": "راية القابضة", "sector": "Telecom"},
    # Financial Services
    "HRHO.CA": {"name_en": "EFG Hermes", "name_ar": "هيرميس", "sector": "Financial Services"},
    "BTFH.CA": {"name_en": "Beltone Financial", "name_ar": "بلتون", "sector": "Financial Services"},
    "CICH.CA": {"name_en": "CI Capital Holding", "name_ar": "سي آي كابيتال", "sector": "Financial Services"},
    # Food & Beverage
    "JUFO.CA": {"name_en": "Juhayna Food Industries", "name_ar": "جهينة", "sector": "Food & Beverage"},
    "EFID.CA": {"name_en": "Egyptian Food Industries", "name_ar": "مصر للصناعات الغذائية", "sector": "Food & Beverage"},
    "DOMT.CA": {"name_en": "Domty", "name_ar": "دومتي", "sector": "Food & Beverage"},
}


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=200)
    sectors_of_interest: List[str] = Field(default_factory=list)
    alerts_enabled: bool = False


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class PredictRequest(BaseModel):
    ticker: str
    trade_date: str  # YYYY-MM-DD
    portfolio_value: float = 1_000_000.0
    # "conservative" (default, institutional) | "balanced" | "aggressive"
    risk_appetite: str = "conservative"


class BacktestRequest(BaseModel):
    ticker: str
    start_date: str
    end_date: str
    interval_days: int = 14
    capital: float = 1_000_000.0
    analysts: List[str] = Field(default_factory=lambda: ["market", "fundamentals", "news", "social"])


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "dashboard-api", "version": "1.0.0"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.post("/api/auth/signup")
async def signup(req: SignupRequest, db: Session = Depends(get_session)) -> Dict[str, Any]:
    existing = db.query(User).filter(User.email == req.email.lower()).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        name=req.name.strip(),
        email=req.email.lower(),
        password_hash=hash_password(req.password),
        sectors_of_interest=json.dumps(req.sectors_of_interest),
        alerts_enabled=req.alerts_enabled,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id)
    return {"token": token, "user": user.to_dict()}


@app.post("/api/auth/login")
async def login(req: LoginRequest, db: Session = Depends(get_session)) -> Dict[str, Any]:
    user = db.query(User).filter(User.email == req.email.lower()).first()
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user.id)
    return {"token": token, "user": user.to_dict()}


@app.get("/api/auth/me")
async def me(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    return {"user": current_user.to_dict()}


@app.post("/api/auth/logout")
async def logout(_: User = Depends(get_current_user)) -> Dict[str, str]:
    # Stateless JWT — client just drops the token. Endpoint exists for symmetry.
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Tickers + market data
# ---------------------------------------------------------------------------

@app.get("/api/tickers")
async def list_tickers() -> List[Dict[str, str]]:
    return [
        {"symbol": symbol, **meta}
        for symbol, meta in sorted(EGX_TICKER_META.items())
    ]


@app.get("/api/macro/current")
async def macro_current() -> Dict[str, Any]:
    from tradingagents.dataflows.macro_provider import get_egx_macro_context
    from tradingagents.default_config import DEFAULT_CONFIG

    today = datetime.utcnow().strftime("%Y-%m-%d")
    ctx = get_egx_macro_context(today, DEFAULT_CONFIG)
    # Shape matches the MacroSnapshot TypeScript type.
    # Convert rates from fraction (0.275) to percent (27.5) for display —
    # dashboard formats them with a trailing "%" already.
    return {
        "cbe_rate":           round(float(ctx.get("cbe_policy_rate") or 0.0) * 100, 2),
        "real_rate":          round(float(ctx.get("real_rate") or 0.0) * 100, 2),
        "usd_egp":            float(ctx.get("usd_egp") or 0.0),
        "fx_trend":           ctx.get("fx_trend", "unknown"),
        "egx30_trend":        ctx.get("egx30_trend", "unknown"),
        "egx30_return_1m":    round(float(ctx.get("egx30_return_1m") or 0.0) * 100, 2),
        "brent_usd":          float(ctx.get("brent_usd") or 0.0),
        "imf_program_active": bool(ctx.get("imf_program_active", False)),
        "as_of_date":         ctx.get("as_of_date", today),
    }


@app.get("/api/macro/egx30-history")
async def macro_egx30_history(days: int = Query(30, ge=1, le=365)) -> List[Dict[str, Any]]:
    """Return EGX30 daily closes for the past N days (best-effort via yfinance)."""
    try:
        import yfinance as yf
        end = datetime.utcnow()
        start = end - timedelta(days=days + 10)  # buffer for weekends
        # Try several known EGX30 tickers
        for ticker in ("^CASE30", "EGX30.CA", "CASE30"):
            try:
                df = yf.download(
                    ticker, start=start.strftime("%Y-%m-%d"),
                    end=end.strftime("%Y-%m-%d"), progress=False, auto_adjust=True,
                )
                if df is None or df.empty or "Close" not in df.columns:
                    continue
                closes = df["Close"].dropna()
                out = []
                for date, val in closes.items():
                    v = float(val.iloc[0]) if hasattr(val, "iloc") else float(val)
                    out.append({"date": date.strftime("%Y-%m-%d"), "close": v})
                if out:
                    return out[-days:]
            except Exception:
                continue
    except Exception as exc:
        logger.warning("egx30 history fetch failed: %s", exc)
    return []


@app.get("/api/ticker/{symbol}/quote")
async def ticker_quote(symbol: str) -> Dict[str, Any]:
    try:
        import yfinance as yf
        end = datetime.utcnow()
        start = end - timedelta(days=10)
        df = yf.download(
            symbol, start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"), progress=False, auto_adjust=True,
        )
        if df is None or df.empty:
            raise HTTPException(status_code=404, detail=f"No quote for {symbol}")

        closes = df["Close"].dropna()
        vols = df["Volume"].dropna() if "Volume" in df.columns else None
        last = float(closes.iloc[-1].iloc[0]) if hasattr(closes.iloc[-1], "iloc") else float(closes.iloc[-1])
        if len(closes) >= 2:
            prev = closes.iloc[-2]
            prev_v = float(prev.iloc[0]) if hasattr(prev, "iloc") else float(prev)
            change_pct = ((last - prev_v) / prev_v) if prev_v else 0.0
        else:
            change_pct = 0.0
        volume = 0
        if vols is not None and len(vols):
            last_vol = vols.iloc[-1]
            volume = int(last_vol.iloc[0] if hasattr(last_vol, "iloc") else last_vol)

        return {
            "symbol": symbol,
            "last": round(last, 2),
            "change_pct": round(change_pct, 4),
            "volume": volume,
            "as_of_date": closes.index[-1].strftime("%Y-%m-%d"),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("quote fetch failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=503, detail=f"Quote unavailable: {exc}")


# ---------------------------------------------------------------------------
# Predictions
# ---------------------------------------------------------------------------

@app.post("/api/predict")
async def predict(
    req: PredictRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Dict[str, str]:
    if req.ticker not in EGX_TICKER_META:
        raise HTTPException(status_code=400, detail=f"Unknown ticker: {req.ticker}")

    # Create the Run + Job DB rows
    run = Run(
        user_id=current_user.id,
        ticker=req.ticker,
        trade_date=req.trade_date,
        portfolio_value=req.portfolio_value,
        status="running",
    )
    db.add(run)
    db.flush()

    job = Job(user_id=current_user.id, kind="prediction", status="running", run_id=run.id)
    db.add(job)
    db.commit()

    # Kick off the background prediction
    from .dashboard_runners import run_prediction_job
    run_in_background(
        run_prediction_job,
        job.id, run.id, req.ticker, req.trade_date, req.portfolio_value,
        req.risk_appetite,
    )

    return {"job_id": job.id, "run_id": run.id}


@app.get("/api/jobs/{job_id}")
async def get_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    job = db.query(Job).filter(Job.id == job_id, Job.user_id == current_user.id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Dict[str, str]:
    job = db.query(Job).filter(Job.id == job_id, Job.user_id == current_user.id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in ("done", "failed", "cancelled"):
        return {"status": job.status}
    request_cancel(job_id)
    job.status = "cancelled"
    job.finished_at = datetime.utcnow()
    db.commit()
    return {"status": "cancelled"}


# ---------------------------------------------------------------------------
# Runs CRUD
# ---------------------------------------------------------------------------

@app.get("/api/runs")
async def list_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    type: Optional[str] = None,
    decision: Optional[str] = None,
    ticker: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    q = db.query(Run).filter(Run.user_id == current_user.id)
    if type and type != "all":
        q = q.filter(Run.type == type)
    if decision and decision != "all":
        q = q.filter(Run.decision == decision)
    if ticker:
        q = q.filter(Run.ticker == ticker)

    total = q.count()
    items = (
        q.order_by(Run.created_at.desc())
         .offset((page - 1) * page_size)
         .limit(page_size)
         .all()
    )
    return {
        "items": [r.to_dict() for r in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.get("/api/runs/{run_id}")
async def get_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    run = db.query(Run).filter(Run.id == run_id, Run.user_id == current_user.id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.to_dict()


@app.delete("/api/runs/{run_id}")
async def delete_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Dict[str, str]:
    run = db.query(Run).filter(Run.id == run_id, Run.user_id == current_user.id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    db.delete(run)
    db.commit()
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Backtests CRUD
# ---------------------------------------------------------------------------

@app.post("/api/backtest")
async def start_backtest(
    req: BacktestRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Dict[str, str]:
    if req.ticker not in EGX_TICKER_META:
        raise HTTPException(status_code=400, detail=f"Unknown ticker: {req.ticker}")

    bt = Backtest(
        user_id=current_user.id,
        ticker=req.ticker,
        start_date=req.start_date,
        end_date=req.end_date,
        interval_days=req.interval_days,
        capital=req.capital,
        analysts=json.dumps(req.analysts),
        status="running",
    )
    db.add(bt)
    db.flush()

    job = Job(user_id=current_user.id, kind="backtest", status="running", backtest_id=bt.id)
    db.add(job)
    db.commit()

    from .dashboard_runners import run_backtest_job
    run_in_background(
        run_backtest_job, job.id, bt.id, req.ticker,
        req.start_date, req.end_date, req.interval_days, req.capital, req.analysts,
    )

    return {"job_id": job.id, "backtest_id": bt.id}


@app.get("/api/backtests")
async def list_backtests(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    q = db.query(Backtest).filter(Backtest.user_id == current_user.id)
    total = q.count()
    items = (
        q.order_by(Backtest.created_at.desc())
         .offset((page - 1) * page_size)
         .limit(page_size)
         .all()
    )
    return {
        "items": [b.to_dict() for b in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.get("/api/backtests/{bt_id}")
async def get_backtest(
    bt_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    bt = db.query(Backtest).filter(Backtest.id == bt_id, Backtest.user_id == current_user.id).first()
    if bt is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return bt.to_dict()


@app.delete("/api/backtests/{bt_id}")
async def delete_backtest(
    bt_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Dict[str, str]:
    bt = db.query(Backtest).filter(Backtest.id == bt_id, Backtest.user_id == current_user.id).first()
    if bt is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    db.delete(bt)
    db.commit()
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# WebSocket — live agent streaming
# ---------------------------------------------------------------------------

@app.websocket("/ws/jobs/{job_id}")
async def ws_job(websocket: WebSocket, job_id: str, token: Optional[str] = None) -> None:
    """
    Streams agent_start / agent_progress / agent_done / decision / complete / error
    messages for a job in real time.

    Auth via query param: ws://.../ws/jobs/<id>?token=<jwt>
    """
    from .dashboard_db import SessionLocal
    db = SessionLocal()
    try:
        if not token:
            await websocket.close(code=1008, reason="missing token")
            return
        user = get_user_from_token(token, db)
        if user is None:
            await websocket.close(code=1008, reason="invalid token")
            return

        job = db.query(Job).filter(Job.id == job_id, Job.user_id == user.id).first()
        if job is None:
            await websocket.close(code=1008, reason="job not found")
            return
    finally:
        db.close()

    await websocket.accept()
    q = subscribe(job_id)

    try:
        while True:
            msg = await q.get()
            if msg.get("type") == "_end":
                # End-of-stream sentinel — close cleanly
                break
            await websocket.send_json(msg)
    except WebSocketDisconnect:
        pass
    finally:
        unsubscribe(job_id, q)


# ---------------------------------------------------------------------------
# Convenience root
# ---------------------------------------------------------------------------

@app.get("/")
async def root() -> Dict[str, str]:
    return {
        "service": "EGX TradingAgents Dashboard API",
        "docs": "/docs",
        "health": "/api/health",
    }
