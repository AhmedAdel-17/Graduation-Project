"""
Dashboard persistent store — SQLite fallback for when Postgres is unavailable.
Stores: investor profiles, shadow run index, run metadata.
DB file: ./data/dashboard.db (auto-created)
"""
import sqlite3
import json
import os
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

DB_PATH = os.environ.get("DASHBOARD_DB_PATH", "./data/dashboard.db")

def _get_conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    """Create tables if they don't exist."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS investor_profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            risk_tolerance TEXT DEFAULT 'moderate',
            investment_horizon TEXT DEFAULT 'medium_term',
            capital_size REAL DEFAULT 1000000,
            max_position_pct REAL DEFAULT 0.10,
            sector_preferences TEXT DEFAULT '[]',
            sector_exclusions TEXT DEFAULT '[]',
            trading_style TEXT DEFAULT 'position',
            benchmark_target TEXT DEFAULT 'EGX30',
            investor_category TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS shadow_runs (
            id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            signal TEXT,
            confidence_overall REAL,
            confidence_technical REAL,
            confidence_fundamental REAL,
            confidence_sentiment REAL,
            latest_price REAL,
            price_date TEXT,
            judge_decision_summary TEXT,
            bull_thesis_summary TEXT,
            bear_thesis_summary TEXT,
            trader_plan_summary TEXT,
            risk_rationale_summary TEXT,
            macro_context TEXT,
            market_breadth TEXT,
            memory_status TEXT,
            warnings TEXT DEFAULT '[]',
            errors TEXT DEFAULT '[]',
            model_provider TEXT,
            model_name TEXT,
            duration_seconds REAL,
            status TEXT DEFAULT 'completed',
            backtest_mode INTEGER DEFAULT 0,
            profile_id TEXT,
            report_path TEXT,
            log_path TEXT,
            records_dir TEXT,
            created_at TEXT NOT NULL,
            pipeline_audit TEXT,
            FOREIGN KEY (profile_id) REFERENCES investor_profiles(id)
        );

        CREATE INDEX IF NOT EXISTS idx_shadow_runs_ticker ON shadow_runs(ticker);
        CREATE INDEX IF NOT EXISTS idx_shadow_runs_date ON shadow_runs(created_at);
    """)
    conn.commit()

    # Safe migration: add investor_context_snapshot column if missing
    cursor = conn.execute("PRAGMA table_info(shadow_runs)")
    columns = {row["name"] for row in cursor.fetchall()}
    if "investor_context_snapshot" not in columns:
        conn.execute("ALTER TABLE shadow_runs ADD COLUMN investor_context_snapshot TEXT")
        conn.commit()
    if "pipeline_audit" not in columns:
        conn.execute("ALTER TABLE shadow_runs ADD COLUMN pipeline_audit TEXT")
        conn.commit()

    conn.close()

# ─── Profile CRUD ────────────────────────────────────────────────────────

def create_profile(data: Dict[str, Any]) -> Dict[str, Any]:
    profile_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    conn = _get_conn()
    conn.execute("""
        INSERT INTO investor_profiles
        (id, name, risk_tolerance, investment_horizon, capital_size, max_position_pct,
         sector_preferences, sector_exclusions, trading_style, benchmark_target,
         investor_category, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        profile_id,
        data.get("name", "Default"),
        data.get("risk_tolerance", "moderate"),
        data.get("investment_horizon", "medium_term"),
        data.get("capital_size", 1_000_000),
        data.get("max_position_pct", 0.10),
        json.dumps(data.get("sector_preferences", [])),
        json.dumps(data.get("sector_exclusions", [])),
        data.get("trading_style", "position"),
        data.get("benchmark_target", "EGX30"),
        data.get("investor_category", ""),
        data.get("notes", ""),
        now, now,
    ))
    conn.commit()
    conn.close()
    return get_profile(profile_id)

def get_profile(profile_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM investor_profiles WHERE id = ?", (profile_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_profile(row)

def list_profiles() -> List[Dict[str, Any]]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM investor_profiles ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [_row_to_profile(r) for r in rows]

def update_profile(profile_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    existing = get_profile(profile_id)
    if not existing:
        return None
    now = datetime.now().isoformat()
    conn = _get_conn()
    conn.execute("""
        UPDATE investor_profiles SET
            name = ?, risk_tolerance = ?, investment_horizon = ?, capital_size = ?,
            max_position_pct = ?, sector_preferences = ?, sector_exclusions = ?,
            trading_style = ?, benchmark_target = ?, investor_category = ?,
            notes = ?, updated_at = ?
        WHERE id = ?
    """, (
        data.get("name", existing["name"]),
        data.get("risk_tolerance", existing["risk_tolerance"]),
        data.get("investment_horizon", existing["investment_horizon"]),
        data.get("capital_size", existing["capital_size"]),
        data.get("max_position_pct", existing["max_position_pct"]),
        json.dumps(data.get("sector_preferences", existing["sector_preferences"])),
        json.dumps(data.get("sector_exclusions", existing["sector_exclusions"])),
        data.get("trading_style", existing["trading_style"]),
        data.get("benchmark_target", existing["benchmark_target"]),
        data.get("investor_category", existing["investor_category"]),
        data.get("notes", existing["notes"]),
        now,
        profile_id,
    ))
    conn.commit()
    conn.close()
    return get_profile(profile_id)

def delete_profile(profile_id: str) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM investor_profiles WHERE id = ?", (profile_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0

def _row_to_profile(row) -> Dict[str, Any]:
    d = dict(row)
    d["sector_preferences"] = json.loads(d.get("sector_preferences") or "[]")
    d["sector_exclusions"] = json.loads(d.get("sector_exclusions") or "[]")
    return d

# ─── Shadow Run CRUD ─────────────────────────────────────────────────────

def record_shadow_run(data: Dict[str, Any]) -> Dict[str, Any]:
    run_id = data.get("id") or str(uuid.uuid4())[:12]
    now = datetime.now().isoformat()
    conn = _get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO shadow_runs
        (id, ticker, trade_date, signal, confidence_overall, confidence_technical,
         confidence_fundamental, confidence_sentiment, latest_price, price_date,
         judge_decision_summary, bull_thesis_summary, bear_thesis_summary,
         trader_plan_summary, risk_rationale_summary, macro_context, market_breadth,
         memory_status, warnings, errors, model_provider, model_name,
         duration_seconds, status, backtest_mode, profile_id,
         report_path, log_path, records_dir, investor_context_snapshot,
         pipeline_audit, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        run_id,
        data.get("ticker", ""),
        data.get("trade_date", ""),
        data.get("signal", ""),
        data.get("confidence_overall"),
        data.get("confidence_technical"),
        data.get("confidence_fundamental"),
        data.get("confidence_sentiment"),
        data.get("latest_price"),
        data.get("price_date"),
        data.get("judge_decision_summary", ""),
        data.get("bull_thesis_summary", ""),
        data.get("bear_thesis_summary", ""),
        data.get("trader_plan_summary", ""),
        data.get("risk_rationale_summary", ""),
        json.dumps(data.get("macro_context")) if data.get("macro_context") else None,
        json.dumps(data.get("market_breadth")) if data.get("market_breadth") else None,
        json.dumps(data.get("memory_status")) if data.get("memory_status") else None,
        json.dumps(data.get("warnings", [])),
        json.dumps(data.get("errors", [])),
        data.get("model_provider", ""),
        data.get("model_name", ""),
        data.get("duration_seconds"),
        data.get("status", "completed"),
        1 if data.get("backtest_mode") else 0,
        data.get("profile_id"),
        data.get("report_path", ""),
        data.get("log_path", ""),
        data.get("records_dir", ""),
        json.dumps(data.get("investor_context_snapshot")) if data.get("investor_context_snapshot") else None,
        json.dumps(data.get("pipeline_audit")) if data.get("pipeline_audit") else None,
        data.get("created_at", now),
    ))
    conn.commit()
    conn.close()
    return get_shadow_run(run_id)

def get_shadow_run(run_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM shadow_runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_run(row)

def list_shadow_runs(ticker: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    conn = _get_conn()
    if ticker:
        rows = conn.execute(
            "SELECT * FROM shadow_runs WHERE ticker = ? ORDER BY created_at DESC LIMIT ?",
            (ticker, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM shadow_runs ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [_row_to_run(r) for r in rows]

def _row_to_run(row) -> Dict[str, Any]:
    d = dict(row)
    for key in ("macro_context", "market_breadth", "memory_status", "investor_context_snapshot", "pipeline_audit"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    for key in ("warnings", "errors"):
        try:
            d[key] = json.loads(d.get(key) or "[]")
        except (json.JSONDecodeError, TypeError):
            d[key] = []
    d["backtest_mode"] = bool(d.get("backtest_mode"))
    return d

# ─── System Status ───────────────────────────────────────────────────────

def get_system_status() -> Dict[str, Any]:
    """Aggregate system health from various sources.

    Service URLs are configurable via environment variables so the
    monitoring page works outside localhost (e.g. Docker, remote dev):
        STOCKHIVE_API_URL, STOCKHIVE_DASHBOARD_URL,
        STOCKHIVE_GRAFANA_URL, STOCKHIVE_PROMETHEUS_URL,
        STOCKHIVE_LOKI_URL, STOCKHIVE_REDIS_URL
    """
    import os
    import socket
    from urllib.parse import urlparse

    api_url        = os.getenv("STOCKHIVE_API_URL",        "http://localhost:8000")
    dashboard_url  = os.getenv("STOCKHIVE_DASHBOARD_URL",  "http://localhost:5173")
    grafana_url    = os.getenv("STOCKHIVE_GRAFANA_URL",    "http://localhost:3000")
    prometheus_url = os.getenv("STOCKHIVE_PROMETHEUS_URL", "http://localhost:9090")
    loki_url       = os.getenv("STOCKHIVE_LOKI_URL",       "http://localhost:3100")
    redis_url      = os.getenv("STOCKHIVE_REDIS_URL",      "localhost:6379")

    status = {
        "api_server":  {"status": "up",      "url": api_url},
        "dashboard":   {"status": "unknown", "url": dashboard_url},
        "grafana":     {"status": "unknown", "url": grafana_url, "login": "admin/admin"},
        "prometheus":  {"status": "unknown", "url": prometheus_url},
        "loki":        {"status": "unknown", "url": loki_url},
        "redis":       {"status": "unknown", "url": redis_url},
        "last_shadow_run": None,
    }

    # Check services via quick TCP probe
    def _extract_host_port(url: str, default_port: int) -> tuple:
        if "://" in url:
            parsed = urlparse(url)
            return (parsed.hostname or "localhost", parsed.port or default_port)
        parts = url.rsplit(":", 1)
        host = parts[0]
        port = int(parts[1]) if len(parts) == 2 else default_port
        return (host, port)

    svc_probes = [
        ("dashboard",   dashboard_url,  5173),
        ("grafana",     grafana_url,    3000),
        ("prometheus",  prometheus_url, 9090),
        ("loki",        loki_url,       3100),
        ("redis",       redis_url,      6379),
    ]
    for svc, url, default_port in svc_probes:
        host, port = _extract_host_port(url, default_port)
        try:
            s = socket.create_connection((host, port), timeout=1)
            s.close()
            status[svc]["status"] = "up"
        except (socket.timeout, ConnectionRefusedError, OSError):
            status[svc]["status"] = "down"

    # Last shadow run
    runs = list_shadow_runs(limit=1)
    if runs:
        status["last_shadow_run"] = {
            "id": runs[0]["id"],
            "ticker": runs[0]["ticker"],
            "signal": runs[0]["signal"],
            "created_at": runs[0]["created_at"],
        }

    return status

# Initialize on import
init_db()
