"""
Deterministic drop-in replacements for LLM-based agents.

Each function has the same signature as the LangGraph node it replaces:
    def node(state: dict) -> dict

They read from the same state keys and write to the same output keys,
so the graph wiring does not change.
"""

import json
import logging
import re
import time
from datetime import datetime
from typing import Dict, Any, Optional, List

from dateutil.relativedelta import relativedelta

from tradingagents.agents.utils.agent_utils import get_stock_data, get_indicators
from tradingagents.agents.analysts.market_analyst import (
    parse_technical_signals,
    calculate_confidence_score as calc_tech_confidence,
    determine_trend_direction,
    generate_invalidation_conditions,
    EGX_DAILY_INDICATORS,
)

_det_logger = logging.getLogger(__name__)
from tradingagents.agents.analysts.fundamentals_analyst import (
    create_deterministic_fundamentals_analyst as _make_det_fundamentals,
)
from tradingagents.agents.managers.risk_manager import run_all_risk_checks
from tradingagents.agents.trader.trader import calculate_position_limits
from tradingagents.dataflows.config import get_config


# =============================================================================
# A1: Deterministic Market Analyst
# =============================================================================

def deterministic_market_analyst(state: dict) -> dict:
    """
    Replace the LLM-based Market Analyst.

    Calls the SAME tools (get_stock_data, get_indicators) directly,
    then uses the EXISTING deterministic functions from market_analyst.py
    that are currently unused.
    """
    ticker = state["company_of_interest"]
    trade_date = state["trade_date"]
    low_liquidity = state.get("low_liquidity", False)
    volume_missing = state.get("volume_missing", False)

    # 1. Fetch data (same tools the LLM would call)
    try:
        stock_data = get_stock_data.invoke({"ticker": ticker})
    except Exception as e:
        stock_data = f"Error fetching stock data: {e}"

    try:
        indicator_data = get_indicators.invoke({
            "ticker": ticker,
            "indicators": ",".join(EGX_DAILY_INDICATORS),
        })
    except Exception as e:
        indicator_data = f"Error fetching indicators: {e}"

    # 2. Parse indicators into structured signals
    if isinstance(indicator_data, dict):
        parsed = parse_technical_signals(indicator_data)
    else:
        parsed = {
            "signals": {},
            "data_quality": {"missing_indicators": len(EGX_DAILY_INDICATORS),
                             "total_indicators": len(EGX_DAILY_INDICATORS),
                             "data_completeness": 0.0},
        }

    signals = parsed["signals"]
    data_quality = parsed["data_quality"]

    # 3. Determine trend
    trend = determine_trend_direction(signals)

    # 4. Calculate confidence
    confidence = calc_tech_confidence(
        signals,
        low_liquidity=low_liquidity,
        volume_missing=volume_missing,
        data_quality=data_quality,
    )

    # 5. Generate invalidation conditions
    invalidations = generate_invalidation_conditions(trend, signals)

    # ── P3: Compute momentum pack ────────────────────────────────────────
    # Fetch OHLCV directly (tool returns string, we need numeric arrays).
    momentum_pack = None
    try:
        from tradingagents.dataflows.eodhd import get_eodhd_stock_data
        from tradingagents.agents.analysts.fundamentals.momentum import compute_momentum_pack
        from tradingagents.dataflows.egx30_loader import load_egx30_csv

        _p3_start = (
            datetime.strptime(trade_date, "%Y-%m-%d") - relativedelta(days=252)
        ).strftime("%Y-%m-%d")

        # Try EODHD first
        _price_result = get_eodhd_stock_data(ticker, _p3_start, trade_date)
        _closes: List[float] = []
        _volumes: List[float] = []
        _dates: List[str] = []
        if _price_result and _price_result.get("data"):
            _closes = [bar["close"] for bar in _price_result["data"]]
            _volumes = [bar.get("volume", 0) for bar in _price_result["data"]]
            _dates = [bar.get("date", "") for bar in _price_result["data"]]

        # Fallback to yfinance
        if not _closes:
            import yfinance as _yf
            import pandas as _pd
            _df = _yf.download(
                ticker, start=_p3_start, end=trade_date,
                progress=False, auto_adjust=True,
            )
            if _df is not None and not _df.empty:
                if isinstance(_df.columns, _pd.MultiIndex):
                    _df.columns = _df.columns.get_level_values(0)
                if "Close" in _df.columns:
                    _cs = _df["Close"].dropna()
                    _closes = [float(v) for v in _cs.values]
                    _dates = [d.strftime("%Y-%m-%d") for d in _cs.index]
                    if "Volume" in _df.columns:
                        _vs = _df["Volume"].dropna()
                        _volumes = [float(v) for v in _vs.values]

        if _closes and _dates:
            _egx30 = load_egx30_csv()
            momentum_pack = compute_momentum_pack(
                closes=_closes,
                volumes=_volumes,
                dates=_dates,
                trade_date=trade_date,
                egx30_map=_egx30,
            )
    except Exception as _e:
        _det_logger.warning("P3 momentum failed for %s: %s", ticker, _e)

    # 6. Build structured output (same schema the LLM was prompted to produce)
    structured_analysis = {
        "trend_direction": trend,
        "indicator_signals": signals,
        "confidence_score": confidence,
        "confidence_adjustments": [],
        "invalidation_conditions": invalidations,
        "data_quality": data_quality,
        # P3: momentum and relative strength pack
        "momentum": momentum_pack,
    }

    if low_liquidity:
        structured_analysis["confidence_adjustments"].append(
            "Low liquidity penalty applied: -20%"
        )

    # Build a compact text report for downstream agents that read market_report
    report_lines = [
        f"## Technical Analysis for {ticker} ({trade_date})",
        f"Trend: {trend['direction']} ({trend['strength']})",
        f"Confidence: {confidence:.0%}",
        "",
        "### Indicator Signals",
    ]
    for name, sig in signals.items():
        if isinstance(sig, dict) and sig.get("description"):
            report_lines.append(f"- {name}: {sig['description']}")
    report_lines.append("")
    report_lines.append("### Invalidation Conditions")
    for cond in invalidations:
        report_lines.append(f"- {cond}")

    report = "\n".join(report_lines)

    return {
        "market_report": report,
        "technical_analysis": structured_analysis,
        "low_liquidity": low_liquidity,
    }


# =============================================================================
# A2: Deterministic Fundamentals Analyst
# =============================================================================

def deterministic_fundamentals_analyst(state: dict) -> dict:
    """
    Replace the LLM-based Fundamentals Analyst.

    Delegates to create_deterministic_fundamentals_analyst() which is the
    production-grade Phase 1A/1B deterministic pipeline (no LLM calls).
    State schema is identical to the LLM-backed analyst.
    """
    node_fn = _make_det_fundamentals()
    return node_fn(state)


# =============================================================================
# D3-replacement: Deterministic Signal Aggregation
# =============================================================================

def deterministic_signal_aggregation(state: dict) -> dict:
    """
    Replace Bull Researcher + Bear Researcher + Research Manager
    with a single deterministic weighted-signal aggregation.

    Reads the structured analyses already produced by analysts.
    Outputs an investment_plan string and populates the debate state
    with a synthetic judge decision so downstream agents see no schema change.
    """
    technical = state.get("technical_analysis") or {}
    fundamental = state.get("fundamental_analysis") or {}
    sentiment = state.get("sentiment_analysis") or {}
    social_raw = state.get("social_sentiment_analysis", "{}")
    try:
        social = json.loads(social_raw) if isinstance(social_raw, str) else (social_raw or {})
    except (json.JSONDecodeError, TypeError):
        social = {}

    # --- Signal extraction ---
    signal_map = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0,
                  "strong": 1.0, "moderate": 0.5, "weak": 0.0,
                  "overbought": -0.5, "oversold": 0.5}

    # Technical
    tech_dir = (technical.get("trend_direction") or {})
    if isinstance(tech_dir, dict):
        tech_signal = signal_map.get(tech_dir.get("direction", "neutral"), 0.0)
    elif isinstance(tech_dir, str):
        tech_signal = signal_map.get(tech_dir, 0.0)
    else:
        tech_signal = 0.0
    tech_conf = _norm_conf(technical.get("confidence_score"))

    # Fundamental
    health_str = (fundamental.get("financial_health") or "Unknown").lower()
    health_map = {"strong": 0.7, "moderate": 0.2, "weak": -0.5, "unknown": 0.0}
    fund_signal = health_map.get(health_str, 0.0)

    valuation_str = (fundamental.get("valuation_gap") or "Unknown").lower()
    val_map = {"undervalued": 0.5, "fair": 0.0, "overvalued": -0.5, "unknown": 0.0}
    fund_signal += val_map.get(valuation_str, 0.0)
    fund_signal = max(-1.0, min(1.0, fund_signal))
    fund_conf = _norm_conf(fundamental.get("confidence_score"))

    # Sentiment (news)
    combined_sent = sentiment.get("combined_sentiment") or {}
    news_signal = combined_sent.get("score", 0.0) if combined_sent else 0.0
    news_conf = combined_sent.get("confidence", 0.5) if combined_sent else 0.5

    # Social
    social_combined = (social.get("combined_sentiment") or {}) if isinstance(social, dict) else {}
    social_signal = social_combined.get("score", 0.0)
    social_conf = social_combined.get("confidence", 0.3)

    # Blend sentiment
    if news_conf > 0 and social_conf > 0:
        sent_signal = news_signal * 0.6 + social_signal * 0.4
        sent_conf = news_conf * 0.6 + social_conf * 0.4
    elif news_conf > 0:
        sent_signal, sent_conf = news_signal, news_conf
    elif social_conf > 0:
        sent_signal, sent_conf = social_signal, social_conf
    else:
        sent_signal, sent_conf = 0.0, 0.3

    # --- Weighted aggregation ---
    weights = {"technical": 0.40, "fundamental": 0.35, "sentiment": 0.25}

    weighted_score = (
        weights["technical"] * tech_signal * tech_conf
        + weights["fundamental"] * fund_signal * fund_conf
        + weights["sentiment"] * sent_signal * sent_conf
    )

    total_conf = (
        weights["technical"] * tech_conf
        + weights["fundamental"] * fund_conf
        + weights["sentiment"] * sent_conf
    )

    # --- Decision ---
    # Check for existing position to avoid nonsensical SELL when flat
    current_pos = state.get("current_position") or {}
    has_position = current_pos.get("shares", 0) > 0

    if weighted_score > 0.10:
        decision = "BUY"
    elif weighted_score < -0.10:
        decision = "SELL" if has_position else "HOLD"
    else:
        decision = "HOLD"

    confidence = max(0.1, min(1.0, total_conf))

    plan_text = (
        f"SIGNAL AGGREGATION RESULT: {decision} (score={weighted_score:.3f}, confidence={confidence:.2f})\n"
        f"Technical: signal={tech_signal:.2f} conf={tech_conf:.2f}\n"
        f"Fundamental: signal={fund_signal:.2f} conf={fund_conf:.2f}\n"
        f"Sentiment: signal={sent_signal:.2f} conf={sent_conf:.2f}\n"
        f"Weights: tech={weights['technical']}, fund={weights['fundamental']}, sent={weights['sentiment']}"
    )

    judge_json = json.dumps({"decision": decision, "confidence": confidence,
                              "rationale": f"Weighted score {weighted_score:.3f}"})

    # Populate debate state so downstream sees no schema break
    synthetic_debate = {
        "bull_history": "",
        "bear_history": "",
        "history": plan_text,
        "current_response": plan_text,
        "judge_decision": judge_json,
        "count": 2,  # satisfy the debate-round counter
    }

    return {
        "investment_debate_state": synthetic_debate,
        "investment_plan": plan_text,
    }


# =============================================================================
# T1: Deterministic Trader
# =============================================================================

def deterministic_trader(state: dict) -> dict:
    """
    Replace the LLM-based Trader with rule-based execution plan generation.

    Uses calculate_position_limits() which already exists in trader.py.
    """
    config = get_config()
    investment_plan = state.get("investment_plan", "")

    # Extract decision from investment_plan text
    decision = "HOLD"
    for pattern in [
        r'"decision"\s*:\s*"(BUY|SELL|HOLD)"',
        r'RESULT:\s*(BUY|SELL|HOLD)',
        r'\b(BUY|SELL|HOLD)\b',
    ]:
        m = re.search(pattern, investment_plan, re.IGNORECASE)
        if m:
            decision = m.group(1).upper()
            break

    current_price = state.get("current_price") or 50.0
    avg_daily_volume = state.get("avg_daily_volume") or 100000
    portfolio_value = state.get("portfolio_value") or 10_000_000
    low_liquidity = state.get("low_liquidity", False)

    # Use existing position-sizing logic
    limits = calculate_position_limits(
        avg_daily_volume, current_price, portfolio_value, low_liquidity
    )

    # Build execution plan
    if decision == "BUY":
        target_shares = limits["max_shares_total"]
        stop_loss_price = round(current_price * 0.95, 2)
        take_profit_price = round(current_price * 1.15, 2)
    elif decision == "SELL":
        target_shares = (state.get("current_position") or {}).get("shares", 0)
        stop_loss_price = round(current_price * 1.05, 2)
        take_profit_price = round(current_price * 0.90, 2)
    else:
        target_shares = 0
        stop_loss_price = 0
        take_profit_price = 0

    execution_plan = {
        "decision": decision,
        "position_sizing": {
            "target_shares": target_shares,
            "max_shares_per_day": limits["max_shares_per_day"],
            "execution_days": limits["days_to_full_position"],
            "portfolio_allocation": f"{limits['portfolio_constraint_pct']:.0%}",
        },
        "entry_logic": {
            "order_type": "limit",
            "price_range": {"low": round(current_price * 0.99, 2),
                            "high": round(current_price * 1.01, 2)},
        },
        "exit_logic": {
            "take_profit": {"price": take_profit_price},
            "stop_loss": {"price": stop_loss_price},
        },
        "risk_controls": {
            "max_daily_execution_pct": limits["adv_constraint_pct"],
            "low_liquidity_adjusted": limits["low_liquidity_adjustment"],
        },
    }

    plan_text = (
        f"EXECUTION PLAN: {decision}\n"
        f"Target shares: {target_shares}, over {limits['days_to_full_position']} days\n"
        f"Stop-loss: {stop_loss_price}, Take-profit: {take_profit_price}\n"
        f"FINAL TRANSACTION PROPOSAL: **{decision}**"
    )

    return {
        "execution_plan": execution_plan,
        "trader_investment_plan": plan_text,
        "investment_plan": state.get("investment_plan", "") + "\n" + plan_text,
    }


# =============================================================================
# R2: Deterministic Risk Manager (no LLM call)
# =============================================================================

def deterministic_risk_manager(state: dict) -> dict:
    """
    Replace the LLM+deterministic Risk Manager with deterministic-only.

    Uses run_all_risk_checks() which already exists in risk_manager.py.
    The LLM call is removed — the deterministic checks ARE the decision.
    """
    config = get_config()
    execution_plan = state.get("execution_plan") or {}
    if isinstance(execution_plan, dict):
        exec_plan = execution_plan.get("execution_plan", execution_plan)
    else:
        exec_plan = {}

    portfolio_value = state.get("portfolio_value") or 10_000_000
    avg_daily_volume = state.get("avg_daily_volume") or 100000
    current_price = state.get("current_price") or 50.0
    low_liquidity = state.get("low_liquidity", False)

    approved, violations = run_all_risk_checks(
        exec_plan,
        portfolio_value=portfolio_value,
        avg_daily_volume=avg_daily_volume,
        current_price=current_price,
        low_liquidity=low_liquidity,
    )

    risk_assessment = {
        "approved": approved,
        "total_violations": len(violations),
        "critical_violations": len([v for v in violations if v.severity == "critical"]),
        "violations": [v.to_dict() for v in violations],
    }

    if not approved:
        final_decision = "HOLD"
    else:
        final_decision = exec_plan.get("decision", "HOLD")

    # Populate risk_debate_state for schema compatibility
    risk_debate_state = state.get("risk_debate_state") or {}
    new_risk_debate_state = {
        "judge_decision": f"DETERMINISTIC: {final_decision} (violations={len(violations)})",
        "history": risk_debate_state.get("history", ""),
        "risky_history": "", "safe_history": "", "neutral_history": "",
        "latest_speaker": "Risk_Manager",
        "current_risky_response": "", "current_safe_response": "", "current_neutral_response": "",
        "count": risk_debate_state.get("count", 0),
    }

    return {
        "risk_debate_state": new_risk_debate_state,
        "final_trade_decision": final_decision,
        "risk_assessment": risk_assessment,
        "risk_veto": not approved,
    }


# =============================================================================
# S1: Regex Signal Processor
# =============================================================================

def regex_signal_processor(full_signal: str) -> str:
    """
    Replace the LLM-based SignalProcessor.process_signal() with regex.
    """
    if not full_signal:
        return "HOLD"

    signal_upper = full_signal.upper()

    # Check for veto first
    if "VETOED" in signal_upper or "VETO" in signal_upper:
        return "HOLD"

    # Structured JSON decision
    m = re.search(r'"(?:decision|action)"\s*:\s*"(BUY|SELL|HOLD)"', signal_upper)
    if m:
        return m.group(1)

    # Explicit markers
    for pattern in [
        r'(?:FINAL\s+)?(?:DECISION|RECOMMENDATION|SIGNAL|TRANSACTION\s+PROPOSAL)[:\s*]+\*{0,2}(BUY|SELL|HOLD)\*{0,2}',
        r'\b(BUY|SELL|HOLD)\b',
    ]:
        m = re.search(pattern, signal_upper)
        if m:
            return m.group(1)

    return "HOLD"


# =============================================================================
# Helpers
# =============================================================================

def _norm_conf(raw) -> float:
    """Normalize a confidence value to [0, 1]."""
    if raw is None:
        return 0.5
    try:
        v = float(raw)
        return v / 100.0 if v > 1.0 else v
    except (TypeError, ValueError):
        return 0.5
