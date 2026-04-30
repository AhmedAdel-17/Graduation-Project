#!/usr/bin/env python3
"""
Generate a polished markdown backtest report from a JSON report file.

Usage:
    python3 scripts/generate_report.py \
        --input  backtest_results/report_COMI.CA_<timestamp>.json \
        --output backtest_results/final_report_COMI.CA.md

Output sections:
  1. Header & Parameters
  2. Per-Date Decision Log  (Reasoning Score / LLM Calls / Trade Time NEW)
  3. All Trades             (entry, exit, forward returns, outcome)
  4. Overall Performance    (returns, Sharpe, drawdown, alpha)
  5. Train / Test Split Summary
  6. Pipeline Efficiency    (aggregated LLM / time / cost stats)
  7. Key Observations       (auto-generated narrative)
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Formatting helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pct(val, default="N/A") -> str:
    if val is None or val == "N/A":
        return default
    try:
        f = float(str(val).replace("%", ""))
        # Already formatted as "X.XX%"
        if isinstance(val, str) and "%" in val:
            return val
        return f"{f:.2%}"
    except (ValueError, TypeError):
        return str(val)


def _fmt(val, default="N/A") -> str:
    return default if (val is None or val == "N/A") else str(val)


def _decision_emoji(d: str) -> str:
    return {"BUY": "🟢 BUY", "SELL": "🔴 SELL", "HOLD": "🟡 HOLD"}.get(
        str(d).upper(), str(d)
    )


def _outcome_emoji(r: str) -> str:
    return {"WIN": "✅ WIN", "LOSS": "❌ LOSS", "NEUTRAL": "➖ NEUTRAL",
            "PENDING": "⏳ PENDING"}.get(str(r).upper(), str(r))


def _score_bar(score: int, max_score: int = 4) -> str:
    filled = "█" * score
    empty  = "░" * (max_score - score)
    return f"{filled}{empty} {score}/{max_score}"


# ─────────────────────────────────────────────────────────────────────────────
# Section builders
# ─────────────────────────────────────────────────────────────────────────────

def _section_header(data: dict) -> str:
    session = data.get("session", "Unknown")
    now     = datetime.now().strftime("%Y-%m-%d %H:%M")
    cost    = data.get("cost_model", {})

    metrics = data.get("metrics", {})
    start   = ""
    end     = ""
    period  = metrics.get("Period", "")
    if "→" in str(period):
        start, end = [p.strip() for p in period.split("→", 1)]

    efficiency = data.get("pipeline_efficiency", {})

    lines = [
        f"# Backtest Report — {session}",
        "",
        f"**Generated:** {now}  ",
        f"**Ticker:** {session}  ",
    ]
    if start:
        lines += [f"**Period:** {start} → {end}  "]

    lines += [
        f"**Initial Capital:** 1,000,000 EGP  ",
        f"**Market:** Egyptian Exchange (EGX)  ",
        "",
        "### Cost Model",
        "| Parameter | Value |",
        "|-----------|-------|",
        f"| Commission per side | {cost.get('commission_per_side', 'N/A')} |",
        f"| Slippage (normal)   | {cost.get('slippage_normal', 'N/A')} |",
        f"| Slippage (low-liq)  | {cost.get('slippage_low_liq', 'N/A')} |",
        f"| Settlement          | {cost.get('settlement', 'N/A')} |",
        f"| Circuit Breaker     | {cost.get('circuit_breaker', 'N/A')} |",
        "",
    ]
    return "\n".join(lines)


def _section_decision_log(audit_log: List[Dict]) -> str:
    if not audit_log:
        return "## 2. Per-Date Decision Log\n\n_No audit entries found._\n"

    lines = [
        "## 2. Per-Date Decision Log",
        "",
        "| Date | Price (EGP) | Parsed Decision | Confidence | Reasoning Score | LLM Calls | Trade Time (s) | Risk Approved |",
        "|------|------------|-----------------|------------|-----------------|-----------|----------------|---------------|",
    ]

    for entry in audit_log:
        date      = entry.get("date", "?")
        price     = f"{entry.get('price', 0):,.2f}" if entry.get("price") else "N/A"
        decision  = _decision_emoji(entry.get("parsed_decision", "?"))
        conf      = entry.get("confidence")
        conf_str  = f"{conf:.2f}" if conf is not None else "N/A"
        rs        = entry.get("reasoning_score")
        rs_str    = _score_bar(int(rs)) if rs is not None else "N/A"
        llm       = entry.get("llm_calls", "N/A")
        t         = entry.get("trade_time_s", "N/A")
        t_str     = f"{t:.0f}s" if isinstance(t, (int, float)) else str(t)
        approved  = entry.get("risk_approved")
        approved_str = ("✅ Yes" if approved else "❌ No") if approved is not None else "N/A"

        lines.append(
            f"| {date} | {price} | {decision} | {conf_str} | {rs_str} | {llm} | {t_str} | {approved_str} |"
        )

    lines.append("")
    return "\n".join(lines)


def _section_trades(trades: List[Dict]) -> str:
    if not trades:
        return "## 3. All Trades\n\n_No trades executed._\n"

    lines = [
        "## 3. All Trades",
        "",
        "| Date | Action | Shares | Exec Price | Value (EGP) | Commission | Realized PnL | +5d Return | +20d Return | Outcome | Split |",
        "|------|--------|--------|-----------|-------------|------------|--------------|-----------|------------|---------|-------|",
    ]

    for t in trades:
        date     = t.get("date", "?")
        action   = _decision_emoji(t.get("action", "?"))
        shares   = f"{t.get('shares', 0):,}"
        exec_p   = f"{t.get('exec_price', 0):,.2f}"
        value    = f"{t.get('value', 0):,.0f}"
        comm     = f"{t.get('commission', 0):,.0f}"
        pnl      = t.get("realized_pnl", 0)
        pnl_str  = f"{pnl:+,.0f}" if pnl else "—"
        r5d      = t.get("forward_return_5d")
        r5d_str  = f"{r5d:.2%}" if r5d is not None else "—"
        r20d     = t.get("forward_return_20d")
        r20d_str = f"{r20d:.2%}" if r20d is not None else "—"
        outcome  = _outcome_emoji(t.get("trade_result", "PENDING"))
        split    = t.get("split", "full")

        lines.append(
            f"| {date} | {action} | {shares} | {exec_p} | {value} | {comm} | {pnl_str} | {r5d_str} | {r20d_str} | {outcome} | {split} |"
        )

    lines.append("")
    return "\n".join(lines)


def _section_overall_performance(metrics: dict, trade_outcomes: dict) -> str:
    lines = [
        "## 4. Overall Performance",
        "",
        "| Metric | Value |",
        "|--------|-------|",
    ]

    order = [
        "Total Return", "Buy&Hold Return", "Strategy Alpha",
        "Benchmark Return", "Alpha",
        "Win Rate", "Max Drawdown", "Sharpe Ratio", "Calmar Ratio",
        "Total Trades", "Total Commissions", "Final Portfolio",
    ]
    for key in order:
        val = metrics.get(key)
        if val is not None:
            lines.append(f"| {key} | {val} |")

    if trade_outcomes:
        lines += [
            "",
            "### Forward-Return Outcomes",
            "",
            "| Outcome | Count |",
            "|---------|-------|",
            f"| ✅ WIN    | {trade_outcomes.get('wins', 0)} |",
            f"| ❌ LOSS   | {trade_outcomes.get('losses', 0)} |",
            f"| ➖ NEUTRAL | {trade_outcomes.get('neutral', 0)} |",
            f"| ⏳ PENDING | {trade_outcomes.get('pending', 0)} |",
            f"| **Hit Rate** | **{trade_outcomes.get('hit_rate', 'N/A')}** |",
        ]

    lines.append("")
    return "\n".join(lines)


def _section_split_summary(split_metrics: dict) -> str:
    if not split_metrics:
        return "## 5. Train / Test Split Summary\n\n_No split configured._\n"

    lines = [
        "## 5. Train / Test Split Summary",
        "",
    ]

    for split_label in ("train", "test"):
        sm = split_metrics.get(split_label, {})
        if not sm or "Note" in sm:
            lines += [f"### {split_label.capitalize()} Split\n_No data._\n"]
            continue

        lines += [
            f"### {split_label.capitalize()} Split",
            "",
            "| Metric | Value |",
            "|--------|-------|",
        ]
        for k, v in sm.items():
            lines.append(f"| {k} | {v} |")
        lines.append("")

    return "\n".join(lines)


def _section_pipeline_efficiency(efficiency: dict, audit_log: List[Dict]) -> str:
    lines = [
        "## 6. Pipeline Efficiency",
        "",
    ]

    if not efficiency:
        lines.append("_No efficiency data recorded (audit_log missing llm_calls field)._\n")
        return "\n".join(lines)

    lines += [
        "| Metric | Value |",
        "|--------|-------|",
        f"| Dates Evaluated | {efficiency.get('dates_evaluated', 'N/A')} |",
        f"| Total LLM Calls | {efficiency.get('total_llm_calls', 'N/A')} |",
        f"| Avg LLM Calls / Date | {efficiency.get('avg_llm_calls_per_date', 'N/A')} |",
        f"| Avg Trade Time | {efficiency.get('avg_trade_time_s', 'N/A')}s |",
        f"| Total Pipeline Time | {efficiency.get('total_trade_time_s', 'N/A')}s |",
        f"| Avg Reasoning Score | {efficiency.get('avg_reasoning_score', 'N/A')} / 4 |",
        "",
        "### Reasoning Score Distribution",
        "",
        "| Score | Count | Meaning |",
        "|-------|-------|---------|",
    ]

    descriptions = {
        "0": "No structured reasoning captured",
        "1": "Invalidation conditions present",
        "2": "Bull + Bear both cited by RM",
        "3": "Full bull/bear + 1 risk perspective",
        "4": "Full bull/bear + all risk perspectives",
    }

    dist = efficiency.get("reasoning_score_dist", {})
    for score_str in ["0", "1", "2", "3", "4"]:
        count = dist.get(score_str, 0)
        desc  = descriptions.get(score_str, "")
        lines.append(f"| {score_str} | {count} | {desc} |")

    # Per-date breakdown (compact)
    if audit_log and any("llm_calls" in a for a in audit_log):
        lines += [
            "",
            "### Per-Date Breakdown",
            "",
            "| Date | LLM Calls | Time (s) | Reasoning Score |",
            "|------|-----------|----------|-----------------|",
        ]
        for a in audit_log:
            if "llm_calls" not in a:
                continue
            rs = a.get("reasoning_score", "?")
            rs_bar = _score_bar(int(rs)) if isinstance(rs, int) else str(rs)
            lines.append(
                f"| {a['date']} | {a['llm_calls']} | {a.get('trade_time_s', '?')} | {rs_bar} |"
            )

    lines.append("")
    return "\n".join(lines)


def _section_key_observations(
    metrics: dict,
    trades: List[Dict],
    efficiency: dict,
    split_metrics: dict,
    audit_log: List[Dict],
) -> str:
    observations = []

    # --- Performance narrative ---
    total_return = metrics.get("Total Return", "")
    try:
        ret_float = float(str(total_return).replace("%", "")) / 100
        if ret_float > 0.05:
            observations.append(
                f"**Positive alpha generated:** The strategy returned {total_return} over the backtest period."
            )
        elif ret_float < -0.05:
            observations.append(
                f"**Negative performance:** Strategy returned {total_return}. Review signal quality and entry timing."
            )
        else:
            observations.append(
                f"**Flat performance ({total_return}):** Returns are close to zero — consider tightening entry criteria."
            )
    except (ValueError, TypeError):
        pass

    # --- Trade frequency ---
    buy_count  = sum(1 for t in trades if t.get("action") == "BUY")
    sell_count = sum(1 for t in trades if t.get("action") == "SELL")
    hold_count = sum(1 for a in audit_log if a.get("parsed_decision") == "HOLD")
    if buy_count + sell_count + hold_count > 0:
        observations.append(
            f"**Activity:** {buy_count} BUY, {sell_count} SELL, {hold_count} HOLD decisions across {len(audit_log)} evaluation dates."
        )

    # --- Win rate ---
    win_rate_str = metrics.get("Win Rate", "")
    try:
        wr = float(str(win_rate_str).replace("%", "")) / 100
        if wr >= 0.60:
            observations.append(f"**Strong win rate ({win_rate_str})** — signal direction is reliable.")
        elif wr < 0.40 and sell_count > 0:
            observations.append(f"**Low win rate ({win_rate_str})** — consider filtering trades by confidence threshold.")
    except (ValueError, TypeError):
        pass

    # --- Reasoning score ---
    avg_rs = efficiency.get("avg_reasoning_score")
    if avg_rs is not None:
        if float(avg_rs) >= 3.0:
            observations.append(
                f"**High reasoning quality (avg {avg_rs}/4):** Bull thesis, RM synthesis, and risk debate all consistently present."
            )
        elif float(avg_rs) < 2.0:
            observations.append(
                f"**Low reasoning quality (avg {avg_rs}/4):** Pipeline is not reliably producing structured theses — check agent prompts."
            )
        else:
            observations.append(
                f"**Moderate reasoning quality (avg {avg_rs}/4):** Some dates missing invalidation conditions or risk perspectives."
            )

    # --- Train vs test drift ---
    train_sm = split_metrics.get("train", {})
    test_sm  = split_metrics.get("test", {})
    if train_sm and test_sm and "Note" not in train_sm and "Note" not in test_sm:
        try:
            tr_train = float(str(train_sm.get("Total Return", "0")).replace("%", "")) / 100
            tr_test  = float(str(test_sm.get("Total Return", "0")).replace("%", "")) / 100
            if tr_train > 0.05 and tr_test < 0:
                observations.append(
                    "**Train/Test degradation detected:** Strong training returns did not generalise to the test period. "
                    "Risk of overfitting to in-sample market regime."
                )
            elif abs(tr_train - tr_test) < 0.03:
                observations.append(
                    "**Consistent train/test performance:** Return profile is stable across both splits — signals generalise well."
                )
        except (ValueError, TypeError):
            pass

    # --- Circuit breaker / risk veto count ---
    veto_count = sum(
        1 for a in audit_log
        if str(a.get("parsed_decision", "")).upper() == "HOLD"
        and a.get("risk_approved") is False
    )
    if veto_count > 0:
        observations.append(
            f"**Risk veto applied on {veto_count} date(s):** Risk Manager overrode the trader's recommendation to HOLD."
        )

    # --- LLM efficiency ---
    avg_t = efficiency.get("avg_trade_time_s")
    avg_calls = efficiency.get("avg_llm_calls_per_date")
    if avg_t and avg_calls:
        observations.append(
            f"**Pipeline efficiency:** avg {avg_calls} LLM calls and {avg_t}s per evaluation date."
        )

    lines = [
        "## 7. Key Observations",
        "",
    ]
    for obs in observations:
        lines.append(f"- {obs}")
    lines.append("")
    lines.append("---")
    lines.append(f"_Report auto-generated by `generate_report.py` on {datetime.now().strftime('%Y-%m-%d %H:%M')}_")
    lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def generate_report(input_path: str, output_path: str) -> None:
    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    metrics        = data.get("metrics", {})
    split_metrics  = data.get("split_metrics", {})
    trade_outcomes = data.get("trade_outcomes", {})
    efficiency     = data.get("pipeline_efficiency", {})
    trades         = data.get("trades", [])
    audit_log      = data.get("audit_log", [])

    sections = [
        _section_header(data),
        _section_decision_log(audit_log),
        _section_trades(trades),
        _section_overall_performance(metrics, trade_outcomes),
        _section_split_summary(split_metrics),
        _section_pipeline_efficiency(efficiency, audit_log),
        _section_key_observations(metrics, trades, efficiency, split_metrics, audit_log),
    ]

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sections))

    print(f"Report written → {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Render backtest JSON to markdown report")
    parser.add_argument("--input",  required=True, help="Path to report_*.json")
    parser.add_argument("--output", required=True, help="Output .md path")
    args = parser.parse_args()
    generate_report(args.input, args.output)
