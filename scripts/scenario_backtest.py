"""
EGX Scenario Backtest — "Follow the AI vs. buy the index" event study
=====================================================================
The methodology the thesis evaluates (Chapters 7 & 8):

  1. Pick a ticker + a start date + an end date.
  2. On the **start date**, run the multi-agent system ONCE (exactly like a live
     run, but market + fundamentals only — no news/social) to get a single
     prediction: BUY / SELL / HOLD. The agent sees only data up to the start
     date (look-ahead clamp), so the call is honest.
  3. At the **end date**, compare two scenarios over the holding window:
        Scenario 1 — FOLLOW the AI:
            BUY        -> hold the stock start->end  (return net of round-trip cost)
            HOLD/SELL  -> stay in cash               (EGX is long-only: a SELL/HOLD
                                                      when flat means "don't own it")
        Scenario 2 — IGNORE the AI:
            put the money in the EGX30 index over the same window.
  4. Did following the AI beat simply buying the index?

Run it across many tickers (one decision each) to build a sample. Each run is
persisted as a normal session (run_type='backtest'), so its full reasoning trace
opens in the dashboard exactly like a live run, and a dashboard-compatible report
is written so it appears under History -> Backtests.

The decision context uses a LOW required-return hurdle (default 0.0 via the
disclosed `decision_rfr_override`) so the system expresses a directional view
instead of defaulting to "cash at 27.5% is better" on every name. This is a
disclosed configuration; the comparison itself is then honest — the AI's calls
either beat the index or they don't.

Usage
-----
    python scripts/scenario_backtest.py \
        --tickers COMI.CA,ETEL.CA,TMGH.CA,ABUK.CA,SWDY.CA,EFID.CA \
        --start 2024-01-01 --end 2024-06-30 --rfr 0.0

    python scripts/scenario_backtest.py --all --start 2024-01-01 --end 2024-06-30
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from tradingagents.dataflows.config import get_config, set_config
from tradingagents.dataflows.egx_costs import round_trip_cost_pct
from scripts.backtester import BacktestingEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("scenario_backtest")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = PROJECT_ROOT / "backtest_results"
THESIS_DIR = PROJECT_ROOT / "thesis_results"
CHARTS_DIR = THESIS_DIR / "charts"
DEFAULT_INITIAL_CAPITAL = 1_000_000.0
# Benchmark source, best first. The daily EGX30 ETF (data/) is preferred — it is
# the actual instrument a retail user would buy and has dense daily history
# (2020→2026); the monthly index CSV is the fallback.
EGX30_CSV_CANDIDATES = [
    PROJECT_ROOT / "data" / "EGX30ETF ETF Stock Price History.csv",
    PROJECT_ROOT / "EGX30ETF ETF Stock Price History.csv",
    PROJECT_ROOT / "EGX 30 Historical Data.csv",
]


def _load_egx30_proxy_benchmark(
    engine: "BacktestingEngine", gateway, start: str, end: str
) -> bool:
    """Offline EGX30 proxy: equal-weight mean return across ``EGX_TICKERS``.

    Used when no EGX30 ETF/index CSV is on disk. Prices come from the same local
    OHLCV CSVs the backtest already uses (``ohlcv_local_only``).
    """
    from tradingagents.default_config import EGX_TICKERS

    rets: List[float] = []
    for sym in EGX_TICKERS:
        p0 = _price_asof(gateway, sym, start)
        p1 = _price_asof(gateway, sym, end)
        if p0 and p1 and p0 > 0:
            rets.append((p1 - p0) / p0)
    if len(rets) < 5:
        logger.warning(
            "EGX30 proxy: only %d/%d tickers had prices — index comparison disabled.",
            len(rets), len(EGX_TICKERS),
        )
        return False
    avg_ret = sum(rets) / len(rets)
    engine._bm_data_map = {start: 100.0, end: 100.0 * (1.0 + avg_ret)}
    engine.benchmark_start_price = 100.0
    engine.benchmark_ticker = "EGX30_PROXY"
    logger.info(
        "Benchmark: equal-weight EGX30 proxy (%d/%d tickers, %.2f%% over window)",
        len(rets), len(EGX_TICKERS), avg_ret * 100.0,
    )
    return True


def _load_benchmark(
    engine: "BacktestingEngine", gateway=None, start: str = "", end: str = ""
) -> Optional[str]:
    """Load EGX30 benchmark data into the engine.

    Tries local ETF/index CSVs first, then falls back to an equal-weight proxy
    built from ``data/egx30_ohlcv`` (offline-safe).
    """
    for cand in EGX30_CSV_CANDIDATES:
        if cand.exists() and engine._load_egx30_csv(str(cand)):
            logger.info("Benchmark: %s", cand.name)
            return str(cand)
    if gateway and start and end and _load_egx30_proxy_benchmark(engine, gateway, start, end):
        return "EGX30_PROXY"
    logger.warning("No EGX30 benchmark data — index comparison disabled.")
    return None


def _directional_accuracy_single(
    predicted: str,
    stock_ret: float,
    *,
    hold_band: float = 0.01,
) -> Dict[str, Any]:
    """Post-hoc hit-rate for a single start→end prediction (reporting only)."""
    dec = (predicted or "HOLD").upper()
    if dec == "BUY":
        ok = stock_ret > hold_band
    elif dec == "SELL":
        ok = stock_ret < -hold_band
    else:
        ok = abs(stock_ret) <= hold_band
    by_dec = {k: {"n": 0, "correct": 0} for k in ("BUY", "SELL", "HOLD")}
    if dec in by_dec:
        by_dec[dec] = {"n": 1, "correct": int(ok)}
    actionable = dec in ("BUY", "SELL")
    return {
        "horizon": "start_to_end",
        "hold_band_pct": round(hold_band * 100, 3),
        "evaluated_decisions": 1,
        "overall_hit_rate": 1.0 if ok else 0.0,
        "actionable_hit_rate": (1.0 if ok else 0.0) if actionable else None,
        "by_decision": by_dec,
        "note": "Single-decision scenario: was the prediction right vs the realized move?",
        "detail": [{
            "date": "window",
            "decision": dec,
            "fwd_return_pct": round(stock_ret * 100, 4),
            "correct": ok,
        }],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Price helpers
# ─────────────────────────────────────────────────────────────────────────────


def _price_asof(gateway, ticker: str, date: str, lookback_days: int = 25) -> Optional[float]:
    """Last available close on/before ``date`` (look-back window to handle
    weekends/holidays). Caller must clear any look-ahead clamp first when
    fetching the END price."""
    start = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    try:
        raw = gateway.fetch_stock_data(ticker, start_date=start, end_date=date)
    except Exception as exc:
        logger.warning("price fetch failed for %s @ %s: %s", ticker, date, exc)
        return None
    rows = []
    for r in (raw.get("data") or []):
        d, c = r.get("date"), r.get("close")
        try:
            if d and c is not None and str(d) <= date and float(c) > 0:
                rows.append((str(d), float(c)))
        except (TypeError, ValueError):
            continue
    rows.sort()
    return rows[-1][1] if rows else None


def _judge_rationale(final_state: Dict[str, Any]) -> Optional[str]:
    inv = final_state.get("investment_debate_state") or {}
    txt = (inv.get("judge_decision") if isinstance(inv, dict) else "") or ""
    m = re.search(r'"rationale"\s*:\s*"([^"]+)"', txt)
    return m.group(1).strip() if m else None


def _predicted_direction(final_state: Dict[str, Any], execution_plan: Dict[str, Any]) -> Optional[str]:
    """The system's RAW directional view (BUY/SELL/HOLD), before EGX's long-only
    constraint rewrites a SELL into a HOLD (you can't sell what you don't own).

    This is what the thesis evaluates as the 'prediction'. Priority:
      1. the CIO's structured decision JSON ({"decision": "..."}) — the analyst view;
      2. the trader's execution-plan decision;
      3. None (caller falls back to the executed action).
    """
    inv = final_state.get("investment_debate_state") or {}
    txt = (inv.get("judge_decision") if isinstance(inv, dict) else "") or ""
    m = re.search(r'"decision"\s*:\s*"(BUY|SELL|HOLD)"', txt, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    if isinstance(execution_plan, dict):
        d = (execution_plan.get("decision") or "").upper()
        if d in ("BUY", "SELL", "HOLD"):
            return d
    return None


# ─────────────────────────────────────────────────────────────────────────────
# One ticker
# ─────────────────────────────────────────────────────────────────────────────


def run_one(engine: BacktestingEngine, graph, ticker: str, start: str, end: str) -> Optional[Dict[str, Any]]:
    logger.info("=" * 64)
    logger.info("[SCENARIO] %s | decide on %s, evaluate at %s", ticker, start, end)

    # 1. Single prediction on the start date (backtest_mode clamps data to start).
    #    Use the engine's transient-retry wrapper (backoff on 429/5xx/connection
    #    errors) so a single network hiccup doesn't lose a ticker in a long batch.
    try:
        final_state, _ = engine._propagate_with_retry(graph, ticker, start)
    except Exception as exc:
        logger.exception("agent run failed for %s: %s", ticker, exc)
        return None
    session_id = getattr(graph, "session_id", None)

    exec_plan = final_state.get("execution_plan", {}) or {}
    if isinstance(exec_plan, dict) and "execution_plan" in exec_plan:
        exec_plan = exec_plan["execution_plan"]
    decision, path = BacktestingEngine._resolve_decision(final_state, exec_plan)
    # The system's RAW directional view (BUY/SELL/HOLD) before EGX long-only rewrites
    # SELL -> HOLD. This is the 'prediction' the thesis reports; `decision` is the
    # executed action (own vs cash). When no raw view is parseable, fall back to it.
    predicted = _predicted_direction(final_state, exec_plan) or decision
    conf = (final_state.get("confidence_scores") or {}).get("overall")
    rationale = _judge_rationale(final_state)
    logger.info("  predicted=%s | executed=%s (path=%s) conf=%s", predicted, decision, path, conf)

    # 2. Outcome prices — clear the look-ahead clamp the decision left behind.
    set_config({"trade_date": None})
    p_start = _price_asof(engine.gateway, ticker, start)
    p_end = _price_asof(engine.gateway, ticker, end)
    if not p_start or not p_end:
        logger.warning("  missing price data (start=%s end=%s) — skipping %s", p_start, p_end, ticker)
        return None
    stock_ret = (p_end - p_start) / p_start

    # 3. Follow-the-AI return — SIMPLE: BUY => own the stock (return net of costs);
    #    SELL/HOLD => you don't own it (stay out, 0%). EGX is long-only. No cash
    #    interest is credited — this is a pure prediction-vs-index comparison.
    rt_cost = round_trip_cost_pct(False)
    own_stock = decision == "BUY"  # only a final BUY actually buys
    follow_ret = (stock_ret - rt_cost) if own_stock else 0.0
    if predicted == "BUY" and own_stock:
        action_taken = "bought & held the stock"
    elif predicted == "BUY":
        action_taken = "BUY blocked by a risk veto — stayed out"
    elif predicted == "SELL":
        action_taken = "predicted a decline — stayed out"
    else:
        action_taken = "no clear edge — stayed out"

    # 4. Index scenario (EGX30 ETF, forward-filled as-of each date).
    idx_start = engine._bm_price_asof(start)
    idx_end = engine._bm_price_asof(end)
    idx_ret = ((idx_end - idx_start) / idx_start) if (idx_start and idx_end and idx_start > 0) else None
    beat = (follow_ret > idx_ret) if idx_ret is not None else None
    prediction_correct = _directional_accuracy_single(predicted, stock_ret)["overall_hit_rate"] == 1.0

    logger.info(
        "  predicted=%s | stock %.2f -> %.2f (%.1f%%) | follow %.1f%% | EGX30 ETF %.1f%% | beat=%s",
        predicted, p_start, p_end, stock_ret * 100, follow_ret * 100,
        (idx_ret * 100 if idx_ret is not None else float("nan")), beat,
    )

    rec = {
        "ticker": ticker, "start": start, "end": end,
        # predicted_direction = the system's raw view (BUY/SELL/HOLD); decision =
        # the executed action after EGX long-only / risk gates.
        "predicted_direction": predicted,
        "decision": decision, "decision_path": path, "session_id": session_id,
        "confidence": conf, "rationale": rationale, "action_taken": action_taken,
        "price_start": round(p_start, 4), "price_end": round(p_end, 4),
        "stock_return_pct": round(stock_ret * 100, 4),
        "follow_return_pct": round(follow_ret * 100, 4),
        "followed_beat_index": beat,
        "outperformance_pct": round((follow_ret - idx_ret) * 100, 4) if idx_ret is not None else None,
        "index_return_pct": round(idx_ret * 100, 4) if idx_ret is not None else None,
        "round_trip_cost_pct": round(rt_cost * 100, 4),
        "prediction_correct": prediction_correct,
    }
    _write_dashboard_report(engine, rec, idx_start, idx_end)
    return rec


def _write_dashboard_report(engine, rec, idx_start, idx_end) -> None:
    """Write a dashboard-compatible report so the run appears under
    History -> Backtests, with the single prediction clickable to its trace."""
    capital = float(getattr(engine, "initial_capital", DEFAULT_INITIAL_CAPITAL))
    follow = rec["follow_return_pct"] / 100.0
    stock = rec["stock_return_pct"] / 100.0
    idx = (rec["index_return_pct"] or 0.0) / 100.0
    predicted = rec.get("predicted_direction") or rec["decision"]
    directional_accuracy = _directional_accuracy_single(predicted, stock)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    report = {
        "session": rec["ticker"],
        "error": None,
        "mode": "scenario_event_study",
        "run_config": {
            "decision_profile": "tuned_scenario",
            "decision_rfr_override": get_config().get("decision_rfr_override"),
            "start_date": rec["start"], "end_date": rec["end"],
            "initial_capital": capital,
            "analysts": ["market", "fundamentals"], "mode": "scenario_event_study",
        },
        "scenario_comparison": rec,
        "directional_accuracy": directional_accuracy,
        "metrics": {
            "Total Return": f"{rec['follow_return_pct']:.2f}%",
            "Buy&Hold Return": f"{rec['stock_return_pct']:.2f}%",
            "Benchmark Return": (f"{rec['index_return_pct']:.2f}%" if rec["index_return_pct"] is not None else "n/a"),
            "Alpha": (f"{rec['outperformance_pct']:.2f}%" if rec["outperformance_pct"] is not None else "n/a"),
            "Sharpe Ratio": "n/a", "Max Drawdown": "n/a", "Win Rate": "n/a",
            "Final Portfolio": f"{capital * (1 + follow):,.2f} EGP",
        },
        "predictions": [{
            "date": rec["start"], "session_id": rec["session_id"],
            "decision": predicted,
            "executed_action": rec["decision"],
            "confidence": rec["confidence"],
            "correct": rec.get("prediction_correct"),
            "beat_index": rec["followed_beat_index"],
            "forward_return_window_pct": rec["stock_return_pct"],
        }],
        "daily_portfolio": [
            {"date": rec["start"], "portfolio_value": capital, "split": "full"},
            {"date": rec["end"], "portfolio_value": capital * (1 + follow), "split": "full"},
        ],
        "buyhold_history": [
            {"date": rec["start"], "price": rec["price_start"], "value": capital},
            {"date": rec["end"], "price": rec["price_end"], "value": capital * (1 + stock)},
        ],
        "benchmark_history": [
            {"date": rec["start"], "value": capital, "price": idx_start},
            {"date": rec["end"], "value": capital * (1 + idx), "price": idx_end},
        ],
        "audit_log": [{
            "date": rec["start"],
            "parsed_decision": predicted,
            "executed_decision": rec["decision"],
            "confidence": rec["confidence"], "session_id": rec["session_id"],
            "judge_rationale": rec["rationale"],
        }],
        "trades": (
            [{
                "date": rec["start"],
                "ticker": rec["ticker"],
                "action": "BUY",
                "price": rec["price_start"],
                "quantity": int(capital * 0.95 / rec["price_start"]) if rec["price_start"] > 0 else 0,
                "confidence": rec["confidence"],
                "reasoning": rec.get("rationale"),
            }]
            if rec["decision"] == "BUY"
            else []
        ),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"report_{rec['ticker']}_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("  dashboard report -> %s", path.name)


def _build_engine_and_graph(
    rfr: float,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    start: str = "",
    end: str = "",
):
    """Set up the BacktestingEngine (config + gateway + benchmark) and the
    market+fundamentals graph for the scenario decision. Shared by the CLI batch
    and the dashboard single-run endpoint.

    Backtest data discipline (set BEFORE the engine/gateway/graph are built):
      * prefetch_data=False  -> NO news/social fetching pipelines run at all.
      * ohlcv_local_only=True -> OHLCV comes ONLY from the saved data/egx30_ohlcv
        CSVs; no yfinance/EODHD/egxpy API calls for price data.
      * selected_analysts = market + fundamentals only (no news/social analysts).
      * fundamentals already read the local CSVs (load_multi_period).
    """
    set_config({
        "prefetch_data": False,       # do NOT run the news/social prefetch pipelines
        "ohlcv_local_only": True,     # OHLCV from saved CSVs only — no price-data APIs
        "auto_refresh_fundamentals": False,  # never trigger a network fundamentals refresh
        "backtest_decisive_mode": True,      # disclosed: CIO commits to a directional call
    })
    engine = BacktestingEngine(
        initial_capital=float(initial_capital),
        benchmark_ticker="^EGX30",
        decision_profile="tuned",
        decision_rfr_override=float(rfr),
    )
    _load_benchmark(engine, gateway=engine.gateway, start=start, end=end)
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.ablation.runner import _rebuild_graph
    graph = TradingAgentsGraph(selected_analysts=["market", "fundamentals"], debug=False)
    graph.graph = _rebuild_graph(graph, {}, [])

    # TradingAgentsGraph.__init__ runs set_config(DEFAULT_CONFIG), which RE-MERGES
    # DEFAULT_CONFIG's prefetch_data=True over the flags we set above. Re-assert the
    # backtest data discipline AFTER the graph is built — on BOTH the global config
    # (data layer reads get_config()) and graph.config (propagate() reads self.config,
    # which is the DEFAULT_CONFIG object). This is what actually stops the news/social
    # prefetch pipeline from running.
    _flags = {
        "prefetch_data": False,
        "ohlcv_local_only": True,
        "auto_refresh_fundamentals": False,
        "backtest_decisive_mode": True,
        "backtest_mode": True,
    }
    set_config(_flags)
    try:
        graph.config.update(_flags)
    except Exception:
        pass
    return engine, graph


def run_single_scenario(
    ticker: str,
    start: str,
    end: str,
    *,
    rfr: float = 0.0,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
) -> Optional[Dict[str, Any]]:
    """Run ONE scenario backtest (dashboard + CLI entry point).

    Builds its own engine + graph (market + fundamentals only, local CSV data),
    writes the dashboard-compatible report, and returns the record.
    """
    engine, graph = _build_engine_and_graph(
        rfr, initial_capital=initial_capital, start=start, end=end,
    )
    return run_one(engine, graph, _normalize(ticker), start, end)


def _normalize(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    return t if t.endswith(".CA") else f"{t}.CA"


def _existing_record(ticker: str, start: str, end: str) -> Optional[Dict[str, Any]]:
    """Return the scenario_comparison record from the newest report for this
    ticker+window, if one already exists (used for --resume and aggregate-only)."""
    import glob as _glob
    for p in sorted(_glob.glob(str(RESULTS_DIR / f"report_{ticker}_*.json")), reverse=True):
        try:
            r = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        sc = r.get("scenario_comparison")
        if isinstance(sc, dict) and sc.get("start") == start and sc.get("end") == end:
            return sc
    return None


def collect_records(tickers: List[str], start: str, end: str) -> List[Dict[str, Any]]:
    """Gather already-written scenario records for a window (aggregate-only)."""
    out = []
    for t in tickers:
        rec = _existing_record(_normalize(t), start, end)
        if rec:
            out.append(rec)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Aggregate + tables + charts
# ─────────────────────────────────────────────────────────────────────────────


def aggregate(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    scored = [r for r in records if r.get("index_return_pct") is not None]
    n = len(scored)
    if not n:
        return {"n": 0}
    beats = [r for r in scored if r["followed_beat_index"]]
    buys = [r for r in scored if r["decision"] == "BUY"]
    mean_follow = sum(r["follow_return_pct"] for r in scored) / n
    mean_index = sum(r["index_return_pct"] for r in scored) / n
    dist = defaultdict(int)
    for r in records:
        dist[r.get("predicted_direction") or r["decision"]] += 1
    buy_beats = sum(1 for r in buys if r["followed_beat_index"])
    return {
        "n": n,
        "n_beat_index": len(beats),
        "pct_beat_index": round(100.0 * len(beats) / n, 1),
        "mean_follow_return_pct": round(mean_follow, 2),
        "mean_index_return_pct": round(mean_index, 2),
        "mean_outperformance_pct": round(mean_follow - mean_index, 2),
        "n_buy": len(buys),
        "buy_mean_stock_return_pct": round(sum(r["stock_return_pct"] for r in buys) / len(buys), 2) if buys else None,
        "buy_beat_index": f"{buy_beats}/{len(buys)}" if buys else "0/0",
        "action_distribution": dict(dist),
    }


def write_tables(records, summary, args, ts) -> None:
    THESIS_DIR.mkdir(parents=True, exist_ok=True)
    cols = ["ticker", "decision", "confidence", "price_start", "price_end",
            "stock_return_pct", "follow_return_pct", "index_return_pct",
            "outperformance_pct", "followed_beat_index", "session_id"]
    with open(THESIS_DIR / f"scenario_summary_{ts}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in sorted(records, key=lambda x: x["ticker"]):
            w.writerow([r.get(c) for c in cols])

    L = []
    L.append(f"# Scenario backtest — Follow the AI vs. EGX30 index")
    L.append(f"\n_{datetime.now().isoformat(timespec='seconds')}_  ")
    L.append(f"\n**Window:** {args.start} → {args.end} · **Decision hurdle (disclosed):** "
             f"required-return = {get_config().get('decision_rfr_override')} · "
             f"market+fundamentals only.\n")
    L.append("## Per-ticker\n")
    L.append("| Ticker | Call | Stock ret | Follow ret | EGX30 ret | Outperf. | Beat? |")
    L.append("|--------|------|----------:|-----------:|----------:|---------:|:-----:|")
    for r in sorted(records, key=lambda x: (x.get("outperformance_pct") is None, -(x.get("outperformance_pct") or 0))):
        ir = r["index_return_pct"]
        op = r["outperformance_pct"]
        beat = "✅" if r["followed_beat_index"] else ("—" if r["followed_beat_index"] is None else "❌")
        L.append(f"| `{r['ticker']}` | {r['decision']} | {r['stock_return_pct']:.1f}% "
                 f"| {r['follow_return_pct']:.1f}% | {ir:.1f}% | {op:+.1f}% | {beat} |"
                 if ir is not None else
                 f"| `{r['ticker']}` | {r['decision']} | {r['stock_return_pct']:.1f}% "
                 f"| {r['follow_return_pct']:.1f}% | — | — | — |")
    s = summary
    L.append("\n## Pooled result\n")
    if s.get("n"):
        L.append(f"- **Following the AI beat the index in {s['n_beat_index']}/{s['n']} cases "
                 f"({s['pct_beat_index']}%).**")
        L.append(f"- Mean return — follow AI: **{s['mean_follow_return_pct']:.2f}%** vs "
                 f"EGX30: **{s['mean_index_return_pct']:.2f}%** "
                 f"(mean outperformance **{s['mean_outperformance_pct']:+.2f}%**).")
        L.append(f"- BUY calls: {s['n_buy']} (avg stock move {s['buy_mean_stock_return_pct']}%, "
                 f"beat index {s['buy_beat_index']}).")
        L.append(f"- Decision mix: {s['action_distribution']}.")
    else:
        L.append("- No scoreable runs (missing index or price data).")
    L.append("\n_Honest event study: the prediction is made on the start date with no "
             "look-ahead; the outcome is measured over the holding window. EGX is "
             "long-only, so SELL/HOLD = cash._\n")
    with open(THESIS_DIR / f"scenario_summary_{ts}.md", "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def make_charts(records, summary, ts) -> List[str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except Exception as exc:
        logger.warning("matplotlib unavailable, skipping charts: %s", exc)
        return []
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"figure.dpi": 130, "savefig.dpi": 150, "axes.grid": True, "grid.alpha": 0.25})
    scored = [r for r in records if r.get("index_return_pct") is not None]
    out = []
    C_F, C_I = "#0f766e", "#94a3b8"

    # 1. Per-ticker follow vs index
    if scored:
        scored2 = sorted(scored, key=lambda x: -(x["outperformance_pct"] or 0))
        t = [r["ticker"].replace(".CA", "") for r in scored2]
        fol = [r["follow_return_pct"] for r in scored2]
        idx = [r["index_return_pct"] for r in scored2]
        x = np.arange(len(t)); w = 0.4
        fig, ax = plt.subplots(figsize=(max(8, 1.0 * len(t)), 4.6))
        ax.bar(x - w / 2, fol, w, label="Follow the AI", color=C_F)
        ax.bar(x + w / 2, idx, w, label="EGX30 index", color=C_I)
        for i, r in enumerate(scored2):
            ax.annotate(r["decision"], (x[i] - w / 2, fol[i]), ha="center",
                        va="bottom" if fol[i] >= 0 else "top", fontsize=7, color="#334155")
        ax.axhline(0, color="#475569", lw=0.8)
        ax.set_xticks(x); ax.set_xticklabels(t, rotation=40, ha="right")
        ax.set_ylabel("Return over window (%)")
        ax.set_title("Follow the AI vs. put the money in EGX30")
        ax.legend()
        fig.tight_layout(); p = CHARTS_DIR / "scenario_follow_vs_index.png"; fig.savefig(p, bbox_inches="tight"); plt.close(fig)
        out.append(p.name)

    # 2. Aggregate bars
    s = summary
    if s.get("n"):
        fig, ax = plt.subplots(figsize=(5.2, 4.4))
        ax.bar([0, 1], [s["mean_follow_return_pct"], s["mean_index_return_pct"]],
               0.55, color=[C_F, C_I])
        for i, v in enumerate([s["mean_follow_return_pct"], s["mean_index_return_pct"]]):
            ax.annotate(f"{v:.1f}%", (i, v), ha="center", va="bottom", fontsize=11, fontweight="bold")
        ax.axhline(0, color="#475569", lw=0.8)
        ax.set_xticks([0, 1]); ax.set_xticklabels(["Follow the AI", "EGX30 index"])
        ax.set_ylabel("Mean return (%)")
        ax.set_title(f"Mean outcome across {s['n']} tickers\n"
                     f"AI beat the index in {s['n_beat_index']}/{s['n']} ({s['pct_beat_index']}%)")
        fig.tight_layout(); p = CHARTS_DIR / "scenario_mean_outcome.png"; fig.savefig(p, bbox_inches="tight"); plt.close(fig)
        out.append(p.name)

    # 3. Decision vs realized stock move (does BUY pick winners?)
    if scored:
        fig, ax = plt.subplots(figsize=(6.0, 4.4))
        colors = {"BUY": "#059669", "SELL": "#e11d48", "HOLD": "#64748b"}
        for dec in ("BUY", "SELL", "HOLD"):
            pts = [r["stock_return_pct"] for r in scored if r["decision"] == dec]
            ax.scatter([dec] * len(pts), pts, color=colors[dec], s=60, alpha=0.75, edgecolor="white")
        ax.axhline(0, color="#475569", lw=0.8)
        ax.set_ylabel("Realized stock return over window (%)")
        ax.set_title("Prediction vs. what the stock actually did")
        fig.tight_layout(); p = CHARTS_DIR / "scenario_decision_vs_move.png"; fig.savefig(p, bbox_inches="tight"); plt.close(fig)
        out.append(p.name)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(description="EGX follow-the-AI vs index scenario backtest.")
    p.add_argument("--tickers", type=str, default="COMI.CA,ETEL.CA,TMGH.CA,ABUK.CA,SWDY.CA,EFID.CA")
    p.add_argument("--all", action="store_true", help="Use the full EGX_TICKERS universe.")
    p.add_argument("--start", type=str, default="2024-01-01")
    p.add_argument("--end", type=str, default="2024-06-30")
    p.add_argument("--rfr", type=float, default=0.0,
                   help="Disclosed decision required-return hurdle (default 0.0 so the "
                        "system expresses a view instead of defaulting to cash).")
    p.add_argument("--cooldown", type=int, default=3)
    p.add_argument("--resume", action="store_true",
                   help="Skip tickers that already have a report for this window.")
    p.add_argument("--aggregate-only", action="store_true",
                   help="Don't run agents; just build tables+charts from existing reports.")
    args = p.parse_args()

    if args.all:
        from tradingagents.default_config import EGX_TICKERS
        tickers = list(EGX_TICKERS)
    else:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]

    records: List[Dict[str, Any]] = []

    if args.aggregate_only:
        records = collect_records(tickers, args.start, args.end)
        logger.info("Aggregate-only: %d existing records for the window.", len(records))
    else:
        engine, graph = _build_engine_and_graph(
            float(args.rfr), start=args.start, end=args.end,
        )
        import time
        for i, t in enumerate(tickers):
            tnorm = _normalize(t)
            if args.resume:
                existing = _existing_record(tnorm, args.start, args.end)
                if existing:
                    logger.info("[RESUME] %s already done — skipping.", tnorm)
                    records.append(existing)
                    continue
            if i > 0 and args.cooldown:
                time.sleep(args.cooldown)
            rec = run_one(engine, graph, tnorm, args.start, args.end)
            if rec:
                records.append(rec)

    if not records:
        logger.error("No scoreable runs produced.")
        return 1

    summary = aggregate(records)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    write_tables(records, summary, args, ts)
    charts = make_charts(records, summary, ts)
    with open(THESIS_DIR / f"scenario_records_{ts}.json", "w", encoding="utf-8") as f:
        json.dump({"window": [args.start, args.end], "rfr": args.rfr,
                   "summary": summary, "records": records}, f, indent=2, default=str)

    print("\n" + "=" * 70)
    print(f"Scenario backtest: {summary.get('n')} scored tickers, "
          f"window {args.start} → {args.end}")
    if summary.get("n"):
        print(f"Following the AI beat the index in {summary['n_beat_index']}/{summary['n']} "
              f"cases ({summary['pct_beat_index']}%).")
        print(f"Mean return — follow AI {summary['mean_follow_return_pct']:.2f}% "
              f"vs EGX30 {summary['mean_index_return_pct']:.2f}% "
              f"(outperformance {summary['mean_outperformance_pct']:+.2f}%).")
        print(f"Decision mix: {summary['action_distribution']}")
    print(f"Tables + charts → {THESIS_DIR}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
