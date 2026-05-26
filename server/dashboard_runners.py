"""
Background job runners — invoke TradingAgentsGraph and stream agent updates.

Each runner is called from a ThreadPoolExecutor (see dashboard_jobs.py).
It must:
  1. Update the corresponding Run / Backtest DB row as it progresses
  2. Publish agent_start / agent_progress / agent_done / decision / complete / error
     messages via dashboard_jobs.emit_*
  3. Mark the job done (success or failure) when finished
"""
from __future__ import annotations

import json
import logging
import re
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .dashboard_db import Backtest, Job, Run, SessionLocal
from .dashboard_jobs import (
    emit_agent_done,
    emit_agent_start,
    emit_complete,
    emit_decision,
    emit_error,
    is_cancelled,
    mark_done,
    publish,
)

logger = logging.getLogger("dashboard.runners")


# ---------------------------------------------------------------------------
# Map LangGraph node names → friendly agent labels for the UI
# ---------------------------------------------------------------------------

NODE_LABELS = {
    "Fundamentals Analyst":   "Fundamentals Analyst",
    "News Analyst":           "News Analyst",
    "Social Analyst":         "Social Media Analyst",
    "Market Analyst":         "Market Analyst",
    "Bull Researcher":        "Bull Researcher",
    "Bear Researcher":        "Bear Researcher",
    "Research Manager":       "Research Manager",
    "Trader":                 "Trader",
    "Risk Scorer":            "Risk Scorer",
    "Merged Risk Debate":     "Risk Debators",
    "Risk Judge":             "Risk Manager",
    "Risk Veto":              "Risk Manager",
}

# Nodes that are internal plumbing — don't emit them to the dashboard
SILENT_NODES = {
    "Msg Clear Fundamentals", "Msg Clear Market", "Msg Clear News", "Msg Clear Social",
    "tools_market", "tools_news", "tools_social", "tools_fundamentals",
    "Analysts Sync",
}


# ---------------------------------------------------------------------------
# Prediction job
# ---------------------------------------------------------------------------

def run_prediction_job(
    job_id: str,
    run_id: str,
    ticker: str,
    trade_date: str,
    portfolio_value: float,
    risk_appetite: str = "conservative",
) -> None:
    """Run a single TradingAgentsGraph prediction, streaming agent events."""
    db = SessionLocal()
    try:
        run = db.query(Run).filter(Run.id == run_id).first()
        job = db.query(Job).filter(Job.id == job_id).first()
        if run is None or job is None:
            logger.error("run_prediction_job: missing DB rows (run=%s job=%s)", run_id, job_id)
            return

        try:
            _execute_prediction(db, job, run, ticker, trade_date, portfolio_value, risk_appetite)
        except Exception as exc:
            logger.exception("Prediction failed for run %s: %s", run_id, exc)
            # Reset the session (a flush may have left it in a rolled-back state)
            try:
                db.rollback()
            except Exception:
                pass
            # Re-fetch — the run/job may have been deleted by the user mid-flight.
            # If so, there's nothing to update; just emit the error and bail.
            fresh_run = db.query(Run).filter(Run.id == run_id).first()
            fresh_job = db.query(Job).filter(Job.id == job_id).first()
            if fresh_run is not None and fresh_job is not None:
                fresh_run.status = "failed"
                fresh_run.error = f"{type(exc).__name__}: {exc}"
                fresh_run.finished_at = datetime.utcnow()
                fresh_job.status = "failed"
                fresh_job.error = fresh_run.error
                fresh_job.finished_at = datetime.utcnow()
                try:
                    db.commit()
                except Exception:
                    db.rollback()
            else:
                logger.warning("Run %s was deleted before it could be marked failed", run_id)
            emit_error(job_id, str(exc))
        finally:
            mark_done(job_id)
    finally:
        db.close()


def _execute_prediction(
    db,
    job: Job,
    run: Run,
    ticker: str,
    trade_date: str,
    portfolio_value: float,
    risk_appetite: str = "conservative",
) -> None:
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    job_id = job.id
    run_id = run.id

    # Normalize risk_appetite to one of the three known modes
    appetite = (risk_appetite or "conservative").lower()
    if appetite not in ("conservative", "balanced", "aggressive"):
        appetite = "conservative"

    # Build graph
    config = {
        **DEFAULT_CONFIG,
        "target_market":           "EGX",
        "trading_currency":        "EGP",
        "backtest_mode":           True,
        "max_debate_rounds":       1,
        "max_risk_discuss_rounds": 1,
        "risk_appetite":           appetite,    # read by trader.py
    }
    set_config(config)

    graph = TradingAgentsGraph(
        selected_analysts=["market", "fundamentals", "news", "social"],
        config=config,
        debug=False,
    )

    # Fetch the REAL current price + ADV so the Fundamentals analyst computes
    # P/E against the actual market price (not a hardcoded placeholder).
    # This was the cause of the fake "2.74x P/E valuation anomaly" bug —
    # a hardcoded 50.0 made every stock look artificially cheap.
    current_price, avg_volume = _fetch_price_and_volume(ticker, trade_date)

    # Initial state
    init_state = {
        "company_of_interest": ticker,
        "trade_date":          trade_date,
        "messages":            [("human", ticker)],
        "portfolio_value":     portfolio_value,
        "current_price":       current_price,
        "avg_daily_volume":    avg_volume,
        "current_position":    {},
    }

    # Emit "running" state for all 4 parallel analysts up front so the
    # dashboard shows visible activity within ~1s of clicking Run, instead
    # of waiting 30-60s for the first node to finish.
    for agent in ("Market Analyst", "Fundamentals Analyst", "News Analyst",
                  "Social Media Analyst"):
        emit_agent_start(job_id, agent)

    # Stream node updates and forward them to the dashboard
    accumulated: Dict[str, Any] = {}
    agents_seen: set = {"Market Analyst", "Fundamentals Analyst",
                        "News Analyst", "Social Media Analyst"}
    args = {**graph.propagator.get_graph_args(), "stream_mode": "updates"}

    for chunk in graph.graph.stream(init_state, **args):
        if is_cancelled(job_id):
            run.status = "failed"
            run.error = "Cancelled by user"
            job.status = "cancelled"
            job.finished_at = datetime.utcnow()
            db.commit()
            emit_error(job_id, "Cancelled by user")
            return

        for node_name, node_updates in chunk.items():
            if node_name in SILENT_NODES:
                continue
            if not isinstance(node_updates, dict):
                continue

            accumulated.update(node_updates)
            label = NODE_LABELS.get(node_name, node_name)

            # First time we see this agent → emit "started" before "done"
            if label not in agents_seen:
                emit_agent_start(job_id, label)
                agents_seen.add(label)

            summary, details = _summarise_node(node_name, node_updates, accumulated)
            emit_agent_done(job_id, label, summary, details)

    # ── Build the final decision payload ──────────────────────────────────
    decision_text = accumulated.get("final_trade_decision") or ""
    action = _extract_action(decision_text)
    confidence = _extract_confidence(accumulated)
    rationale = _extract_rationale(decision_text)

    macro = accumulated.get("macro_context") or {}
    payload = {
        "macro_context": {
            "cbe_rate":           macro.get("cbe_policy_rate", 0.0),
            "real_rate":          macro.get("real_rate", 0.0),
            "usd_egp":            macro.get("usd_egp", 0.0),
            "fx_trend":           macro.get("fx_trend", "unknown"),
            "egx30_trend":        macro.get("egx30_trend", "unknown"),
            "brent_usd":          macro.get("brent_usd", 0.0),
            "imf_program_active": bool(macro.get("imf_program_active", False)),
        },
        "agents":           [],  # client builds from stream; we keep DB lean
        "bull_thesis":      (accumulated.get("investment_debate_state") or {}).get("bull_thesis"),
        "bear_thesis":      (accumulated.get("investment_debate_state") or {}).get("bear_thesis"),
        "execution_plan":   accumulated.get("execution_plan"),
        "risk_assessment":  accumulated.get("risk_assessment") or {},
    }

    run.status = "done"
    run.decision = action
    run.confidence = confidence
    run.rationale = rationale
    run.payload = json.dumps(payload, default=str)
    run.finished_at = datetime.utcnow()

    job.status = "done"
    job.finished_at = datetime.utcnow()
    db.commit()

    emit_decision(job_id, action, confidence, rationale, details={"run_id": run_id})
    emit_complete(job_id, run_id)


# ---------------------------------------------------------------------------
# Backtest job
# ---------------------------------------------------------------------------

def run_backtest_job(
    job_id: str,
    backtest_id: str,
    ticker: str,
    start_date: str,
    end_date: str,
    interval_days: int,
    capital: float,
    analysts: List[str],
) -> None:
    """Run a multi-date backtest, streaming per-date decisions."""
    db = SessionLocal()
    try:
        bt = db.query(Backtest).filter(Backtest.id == backtest_id).first()
        job = db.query(Job).filter(Job.id == job_id).first()
        if bt is None or job is None:
            logger.error("run_backtest_job: missing DB rows")
            return

        try:
            _execute_backtest(db, job, bt, ticker, start_date, end_date, interval_days, capital, analysts)
        except Exception as exc:
            logger.exception("Backtest failed: %s", exc)
            bt.status = "failed"
            bt.error = f"{type(exc).__name__}: {exc}"
            bt.finished_at = datetime.utcnow()
            job.status = "failed"
            job.error = bt.error
            job.finished_at = datetime.utcnow()
            db.commit()
            emit_error(job_id, str(exc))
        finally:
            mark_done(job_id)
    finally:
        db.close()


def _execute_backtest(
    db, job: Job, bt: Backtest,
    ticker: str, start_date: str, end_date: str,
    interval_days: int, capital: float, analysts: List[str],
) -> None:
    """
    Run the existing scripts/backtester.py engine.

    BacktestingEngine API (verified against scripts/backtester.py):
        engine = BacktestingEngine(initial_capital=..., target_market="EGX",
                                   benchmark_ticker=None)
        engine.run_backtest(ticker=..., start_date=..., end_date=...,
                            interval_days=..., analysts=[...])
        # results live on engine.trade_history, engine.daily_history,
        # engine.portfolio_value; metrics via engine._calculate_metrics()
    """
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import DEFAULT_CONFIG
    set_config({**DEFAULT_CONFIG, "target_market": "EGX", "backtest_mode": True})

    job_id = job.id
    bt_id = bt.id

    publish(job_id, {
        "type": "agent_progress",
        "agent": "Backtester",
        "message": f"Starting backtest {ticker} {start_date} → {end_date}",
    })

    # Import the existing backtester
    import importlib.util
    bt_script = Path(__file__).resolve().parent.parent / "scripts" / "backtester.py"
    spec = importlib.util.spec_from_file_location("backtester", bt_script)
    backtester_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backtester_mod)

    engine = backtester_mod.BacktestingEngine(
        initial_capital=capital,
        target_market="EGX",
        # "^EGX30" triggers the benchmark-loading path, which reads the local
        # "EGX 30 Historical Data.csv" so alpha is computed vs the index.
        benchmark_ticker="^EGX30",
    )

    # Normalize analyst names to lowercase — the graph builds conditional-edge
    # method names like `should_continue_market`, so a capitalized "Market"
    # (as the dashboard sends) would crash with AttributeError.
    norm_analysts = [str(a).strip().lower() for a in (analysts or [])] or [
        "market", "fundamentals", "news", "social"
    ]

    # Per-date progress → WebSocket, so the dashboard stream + bar update live
    # as each trade date finishes (instead of being stuck at "1/N").
    def _on_date(done: int, total: int, date_str: str, decision):
        publish(job_id, {
            "type": "agent_progress",
            "agent": "Backtester",
            "message": f"[{done}/{total}] {date_str} → {decision or 'evaluating…'}",
            "done": done,
            "total": total,
        })

    # run_backtest is synchronous, doesn't return anything useful — results
    # accumulate on engine attributes (trade_history, daily_history, etc.)
    engine.run_backtest(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
        interval_days=interval_days,
        analysts=norm_analysts,
        progress_callback=_on_date,
    )

    # ── Pull raw data straight off the engine ──────────────────────────────
    trades_raw: List[Dict[str, Any]] = engine.trade_history or []
    daily_raw:  List[Dict[str, Any]] = engine.daily_history or []
    buyhold_raw: List[Dict[str, Any]] = getattr(engine, "buyhold_history", []) or []
    benchmark_raw: List[Dict[str, Any]] = getattr(engine, "benchmark_history", []) or []

    # Build a {date: benchmark_portfolio_value} map for overlaying EGX30 on the
    # equity curve. benchmark_history rows look like {date, price, value}, where
    # "value" is your capital scaled by the EGX30's move since the start.
    benchmark_by_date: Dict[str, float] = {}
    for row in benchmark_raw:
        d = row.get("date")
        v = row.get("value")
        if d and v is not None:
            try:
                benchmark_by_date[d] = float(v)
            except (TypeError, ValueError):
                pass

    # Horizons used for the alpha-decay curve (must match backtester call)
    DECAY_HORIZONS = [1, 5, 10, 20, 40]

    trades = [
        {
            "date":               t.get("date"),
            "action":             str(t.get("action") or t.get("decision") or "HOLD").upper(),
            "price":              float(t.get("price") or t.get("exec_price") or 0),
            "shares":             int(t.get("shares") or 0),
            "realized_pnl":       float(t.get("realized_pnl") or t.get("pnl") or 0),
            "commission":         float(t.get("commission") or 0),
            "portfolio_value":    float(t.get("portfolio_value_after") or t.get("portfolio_value") or 0),
            "confidence":         t.get("confidence"),
            "forward_return_20d": t.get("forward_return_20d"),
            # Per-horizon forward returns for the decay curve
            "forward_returns":    {
                str(h): t.get(f"forward_return_{h}d") for h in DECAY_HORIZONS
            },
            "outcome":            str(t.get("trade_result") or t.get("outcome") or "PENDING").upper(),
        }
        for t in trades_raw
    ]

    # ── Alpha-decay curve ──────────────────────────────────────────────────
    # Average signed forward return at each horizon, grouped by action.
    # This shows how the signal's edge evolves with holding period — a
    # POST-HOC diagnostic (uses future prices intentionally; NOT a tradeable
    # return and NOT fed back into decisions). See MEMORY.md §C1.
    def _avg_forward(action_filter, horizon: int):
        vals = [
            t.get(f"forward_return_{horizon}d")
            for t in trades_raw
            if (action_filter is None or str(t.get("action", "")).upper() == action_filter)
            and t.get(f"forward_return_{horizon}d") is not None
        ]
        if not vals:
            return None
        return round(sum(vals) / len(vals) * 100, 3)  # as percent

    n_buy = sum(1 for t in trades_raw if str(t.get("action", "")).upper() == "BUY")
    n_sell = sum(1 for t in trades_raw if str(t.get("action", "")).upper() == "SELL")
    alpha_decay = {
        "horizons": DECAY_HORIZONS,
        "all":  [_avg_forward(None, h) for h in DECAY_HORIZONS],
        "buy":  [_avg_forward("BUY", h) for h in DECAY_HORIZONS],
        "sell": [_avg_forward("SELL", h) for h in DECAY_HORIZONS],
        "n_buy":  n_buy,
        "n_sell": n_sell,
    }

    equity_curve = [
        {
            "date":      row.get("date"),
            "value":     float(row.get("portfolio_value") or row.get("value") or 0),
            "benchmark": benchmark_by_date.get(row.get("date")),  # EGX30 overlay
        }
        for row in daily_raw
        if row.get("date")
    ]
    # If we only have trade-level history, synthesize the equity curve from it
    if not equity_curve and trades_raw:
        pv = capital
        for t in trades_raw:
            pv = float(t.get("portfolio_value_after") or pv)
            d = t.get("date")
            equity_curve.append({
                "date": d,
                "value": pv,
                "benchmark": benchmark_by_date.get(d),
            })

    # ── Parse the formatted metrics dict (string values like "12.34%") ─────
    raw_metrics: Dict[str, str] = {}
    try:
        raw_metrics = engine._calculate_metrics() or {}
    except Exception as exc:
        logger.warning("metrics calculation failed: %s", exc)

    # Whether EGX30 benchmark data was actually available this run
    benchmark_available = len(benchmark_by_date) > 0

    metrics_out = {
        "total_return_pct":      _parse_pct(raw_metrics.get("Total Return")),
        "benchmark_return_pct":  _parse_pct(raw_metrics.get("Benchmark Return")),   # EGX30
        "alpha_pct":             _parse_pct(raw_metrics.get("Alpha")),              # vs EGX30 ← headline
        "buy_hold_return_pct":   _parse_pct(raw_metrics.get("Buy&Hold Return")),
        "strategy_alpha_pct":    _parse_pct(raw_metrics.get("Strategy Alpha")),     # vs buy&hold
        "sharpe":                _parse_float(raw_metrics.get("Sharpe Ratio")),
        "calmar":                _parse_float(raw_metrics.get("Calmar Ratio")),
        "max_drawdown_pct":      _parse_pct(raw_metrics.get("Max Drawdown")),
        "win_rate_pct":          _parse_pct(raw_metrics.get("Win Rate")),
        "hit_rate_pct":          0.0,
        "total_trades":          int(raw_metrics.get("Total Trades") or len(trades_raw) or 0),
        "final_portfolio":       float(engine.portfolio_value or capital),
        "total_commissions_egp": _parse_egp(raw_metrics.get("Total Commissions")),
        "benchmark_available":   benchmark_available,
        "alpha_decay":           alpha_decay,
    }

    bt.status = "done"
    bt.trades = json.dumps(trades, default=str)
    bt.equity_curve = json.dumps(equity_curve, default=str)
    bt.metrics = json.dumps(metrics_out)
    bt.finished_at = datetime.utcnow()

    job.status = "done"
    job.finished_at = datetime.utcnow()
    db.commit()

    publish(job_id, {
        "type": "agent_done",
        "agent": "Backtester",
        "summary": f"{metrics_out['total_trades']} trades, {metrics_out['total_return_pct']:.2f}% return",
        "details": metrics_out,
    })
    emit_complete(job_id, bt_id)


# ---------------------------------------------------------------------------
# Helpers — extract action/confidence/rationale from messy LLM output
# ---------------------------------------------------------------------------

_DECISION_RE = re.compile(r"\b(BUY|SELL|HOLD)\b", re.IGNORECASE)


def _fetch_price_and_volume(ticker: str, trade_date: str) -> tuple[float, float]:
    """
    Fetch the real close price + average daily volume as of trade_date.

    Returns (close_price, avg_daily_volume). Falls back to (None, 2_000_000)
    if the fetch fails — None price tells the Fundamentals analyst to use its
    CSV P/E fallback rather than computing a bogus ratio from a placeholder.
    """
    try:
        import yfinance as yf
        from datetime import datetime, timedelta

        end = datetime.strptime(trade_date, "%Y-%m-%d")
        start = end - timedelta(days=40)  # ~20 trading days for ADV
        df = yf.download(
            ticker,
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        if df is None or df.empty or "Close" not in df.columns:
            logger.warning("price fetch empty for %s; using CSV fallback", ticker)
            return None, 2_000_000.0

        closes = df["Close"].dropna()
        last = closes.iloc[-1]
        price = float(last.iloc[0]) if hasattr(last, "iloc") else float(last)

        avg_vol = 2_000_000.0
        if "Volume" in df.columns:
            vols = df["Volume"].dropna()
            if len(vols):
                mean_vol = vols.mean()
                avg_vol = float(mean_vol.iloc[0]) if hasattr(mean_vol, "iloc") else float(mean_vol)

        logger.info("Fetched %s price=%.2f avg_vol=%.0f as of %s", ticker, price, avg_vol, trade_date)
        return price, avg_vol
    except Exception as exc:
        logger.warning("price/volume fetch failed for %s: %s", ticker, exc)
        return None, 2_000_000.0


def _parse_pct(s: Any) -> float:
    """Parse a percent-formatted string like '12.34%' into a float 12.34."""
    if s is None:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
    return float(m.group(0)) if m else 0.0


def _parse_float(s: Any) -> float:
    """Parse a number-formatted string like '1.45' into a float."""
    if s is None:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
    return float(m.group(0)) if m else 0.0


def _parse_egp(s: Any) -> float:
    """Parse a currency-formatted string like '1,000,000.00 EGP' into a float."""
    if s is None:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
    return float(m.group(0)) if m else 0.0


def _extract_action(text: Any) -> str:
    if not text:
        return "HOLD"
    s = str(text)
    m = _DECISION_RE.search(s)
    return m.group(1).upper() if m else "HOLD"


def _extract_confidence(state: Dict[str, Any]) -> float:
    # Priority order: risk_assessment.llm_confidence → confidence_scores.overall → 0.5
    ra = state.get("risk_assessment") or {}
    if isinstance(ra.get("llm_confidence"), (int, float)):
        return max(0.0, min(1.0, float(ra["llm_confidence"])))

    cs = state.get("confidence_scores") or {}
    if isinstance(cs.get("overall"), (int, float)):
        v = float(cs["overall"])
        return max(0.0, min(1.0, v if v <= 1.0 else v / 100.0))

    return 0.5


def _extract_rationale(text: Any) -> str:
    if not text:
        return ""
    s = str(text)
    # If a JSON {"rationale": "..."} is present, use it
    m = re.search(r'"rationale"\s*:\s*"([^"]+)"', s)
    if m:
        return m.group(1)
    # Otherwise return the first 300 chars
    return s.strip()[:300]


def _summarise_node(
    node_name: str,
    updates: Dict[str, Any],
    accumulated: Dict[str, Any],
) -> tuple[str, Dict[str, Any]]:
    """
    Build a one-liner summary + details dict for an agent node's output.
    Used by the dashboard's AgentCard.
    """
    details: Dict[str, Any] = {}

    if "technical_analysis" in updates and isinstance(updates["technical_analysis"], dict):
        ta = updates["technical_analysis"]
        trend = (ta.get("trend_direction") or {}).get("direction", "neutral")
        conf = ta.get("confidence_score", 0)
        details = ta
        return f"Trend: {trend}, confidence {conf}", details

    if "fundamental_analysis" in updates and isinstance(updates["fundamental_analysis"], dict):
        fa = updates["fundamental_analysis"]
        health = fa.get("financial_health", "unknown")
        conf = fa.get("confidence_score", 0)
        details = fa
        return f"Health: {health}, confidence {conf}", details

    if "sentiment_analysis" in updates and isinstance(updates["sentiment_analysis"], dict):
        sa = updates["sentiment_analysis"]
        sent = sa.get("sentiment", "neutral")
        conf = sa.get("confidence_score", 0)
        nc = sa.get("news_coverage") or {}
        articles = nc.get("total_articles", 0)
        sources = nc.get("sources_count", 0)
        details = sa
        return f"{sent.title()} ({articles} articles from {sources} sources)", details

    if "sentiment_report" in updates:
        text = str(updates["sentiment_report"])[:300]
        return f"Social sentiment analysed", {"excerpt": text}

    if "investment_debate_state" in updates and isinstance(updates["investment_debate_state"], dict):
        ids = updates["investment_debate_state"]
        if ids.get("bull_thesis") and node_name == "Bull Researcher":
            bt = ids["bull_thesis"]
            return f"{bt.get('conviction_level', 'moderate')} conviction bull thesis", bt
        if ids.get("bear_thesis") and node_name == "Bear Researcher":
            br = ids["bear_thesis"]
            return f"{br.get('conviction_level', 'moderate')} conviction bear thesis", br
        if ids.get("judge_decision"):
            # Full text for the details panel; short summary for the agent card
            full = str(ids["judge_decision"])
            short_summary = full[:300]
            if len(full) > 300 and " " in short_summary:
                # Truncate at last space so the card preview isn't mid-word
                short_summary = short_summary.rsplit(" ", 1)[0] + "…"
            return short_summary, {"judge_decision": full[:5000]}

    if "execution_plan" in updates and isinstance(updates["execution_plan"], dict):
        ep_raw = updates["execution_plan"]
        ep = ep_raw.get("execution_plan", ep_raw) if isinstance(ep_raw, dict) else {}
        decision = ep.get("decision", "?")
        conviction = ep.get("conviction", "?")
        details = ep
        return f"{decision} ({conviction} conviction)", details

    if "risk_assessment" in updates and isinstance(updates["risk_assessment"], dict):
        ra = updates["risk_assessment"]
        action = ra.get("llm_action") or "?"
        warnings_n = len(ra.get("warnings") or [])
        details = ra
        return f"Risk decision: {action}, {warnings_n} warnings", details

    if "risk_action" in updates:
        return f"Risk action: {updates['risk_action']}", {"risk_action": updates["risk_action"]}

    # Fallback: just show field names
    keys = [k for k in updates.keys() if not k.endswith("_messages")]
    return f"Completed ({', '.join(keys[:3])})", {"keys": keys}
