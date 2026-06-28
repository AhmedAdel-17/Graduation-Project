#!/usr/bin/env python
"""Demo driver: run the FULL multi-agent graph once and dump every agent's output.

Used to show the per-agent results of a single live decision (not a backtest).

    python scripts/demo_agent_run.py --ticker ORHD.CA            # latest trading day
    python scripts/demo_agent_run.py --ticker ORHD.CA --date 2026-06-01

Writes a human-readable dump to logs/agent_run_<ticker>_<date>.md and prints it.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph


def _latest_trading_day() -> str:
    d = datetime.utcnow().date()
    while d.weekday() >= 5:  # Sat/Sun -> back to Friday
        d -= timedelta(days=1)
    return d.isoformat()


def _section(title: str, body) -> str:
    body = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=2, default=str)
    body = (body or "").strip() or "(empty)"
    return f"\n{'='*90}\n## {title}\n{'='*90}\n{body}\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="ORHD.CA")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD; default = latest trading day")
    args = ap.parse_args()
    trade_date = args.date or _latest_trading_day()

    cfg = dict(DEFAULT_CONFIG)
    cfg["prefetch_data"] = True  # exercise live news + social (index sentiment)

    print(f"Running FULL multi-agent graph for {args.ticker} @ {trade_date} (LIVE) ...")
    ta = TradingAgentsGraph(
        selected_analysts=["market", "fundamentals", "news", "social"],
        debug=False, config=cfg,
    )
    final_state, decision = ta.propagate(args.ticker, trade_date)

    debate = final_state.get("investment_debate_state", {}) or {}
    risk = final_state.get("risk_debate_state", {}) or {}

    out = [f"# Live multi-agent run — {args.ticker} @ {trade_date}\n"]
    # ── Analyst team ──
    out.append(_section("MARKET / TECHNICAL ANALYST — report", final_state.get("market_report")))
    out.append(_section("MARKET / TECHNICAL ANALYST — structured", final_state.get("technical_analysis")))
    out.append(_section("FUNDAMENTALS ANALYST — report", final_state.get("fundamentals_report")))
    out.append(_section("FUNDAMENTALS ANALYST — structured", final_state.get("fundamental_analysis")))
    out.append(_section("NEWS ANALYST — report", final_state.get("news_report")))
    out.append(_section("NEWS ANALYST — structured", final_state.get("sentiment_analysis")))
    out.append(_section("SOCIAL / SENTIMENT ANALYST — report (index/sector layers)", final_state.get("sentiment_report")))
    out.append(_section("SOCIAL / SENTIMENT ANALYST — structured", final_state.get("social_sentiment_analysis")))
    out.append(_section("SENTIMENT BLEND (conf×/size× modifier)", final_state.get("sentiment_blend_result")))
    # ── Research debate ──
    out.append(_section("BULL RESEARCHER", debate.get("bull_history")))
    out.append(_section("BEAR RESEARCHER", debate.get("bear_history")))
    out.append(_section("RESEARCH MANAGER — judgment / investment plan",
                        debate.get("judge_decision") or final_state.get("investment_plan")))
    # ── Trader + risk ──
    out.append(_section("TRADER — execution plan", final_state.get("trader_investment_plan")))
    out.append(_section("EXECUTION PLAN (structured)", final_state.get("execution_plan")))
    out.append(_section("RISK — assessment / metrics", {
        "risk_action": final_state.get("risk_action"),
        "risk_metrics": final_state.get("risk_metrics"),
        "risk_assessment": final_state.get("risk_assessment"),
        "risk_veto": final_state.get("risk_veto"),
        "risk_debate_judge": risk.get("judge_decision"),
    }))
    out.append(_section("CONFIDENCE SCORES", final_state.get("confidence_scores")))
    out.append(_section("FINAL TRADE DECISION", final_state.get("final_trade_decision") or str(decision)))
    out.append(_section("SIGNAL (parsed BUY/HOLD/SELL)", str(decision)))

    report = "\n".join(out)
    safe = args.ticker.replace(".", "_")
    path = Path("logs") / f"agent_run_{safe}_{trade_date}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n[saved full dump → {path}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
