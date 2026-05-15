#!/usr/bin/env python3
"""Automated Structural Scoring for Reasoning Quality"""
import json
import os
import sys
import argparse
from pathlib import Path
import csv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

def score_artifacts(ticker: str, trade_date: str, artifact_dir: Path) -> dict:
    scores = {
        "ticker": ticker,
        "trade_date": trade_date,
        "status": "MISSING",
        "bull_words": 0,
        "bear_words": 0,
        "overlap_pct": 0.0,
        "bull_nums": 0,
        "bear_nums": 0,
        "bull_has_inval": False,
        "rm_has_decision": False,
        "rm_mentions_both": False,
        "risk_parts": 0,
        "stop_loss_ok": True,
        "no_short_sell": True,
        "pos_limit_ok": True,
        "stop_below_entry": None,
        "tp_above_entry": None,
    }
    
    run_dir = artifact_dir / ticker / trade_date
    if not run_dir.exists():
        return scores
        
    scores["status"] = "OK"
    
    def read_json(fname):
        p = run_dir / fname
        if p.exists():
            with open(p) as f:
                return json.load(f)
        return None
        
    def read_text(fname):
        p = run_dir / fname
        if p.exists():
            with open(p) as f:
                return f.read()
        return ""
        
    bull = read_text("bull_text.md")
    bear = read_text("bear_text.md")
    bull_json = read_json("bull_thesis.json") or {}

    scores["bull_words"] = len(bull.split())
    scores["bear_words"] = len(bear.split())

    # Overlap
    import re
    bull_set = set(w.lower() for w in bull.split() if len(w) > 3)
    bear_set = set(w.lower() for w in bear.split() if len(w) > 3)
    if bull_set and bear_set:
        intersection = bull_set.intersection(bear_set)
        scores["overlap_pct"] = round(len(intersection) / max(len(bull_set), len(bear_set)), 2)

    # Numbers
    scores["bull_nums"] = len(re.findall(r'\d+', bull))
    scores["bear_nums"] = len(re.findall(r'\d+', bear))

    # Invalidation conditions: prefer bull_thesis.json; fall back to embedded JSON in
    # bull_text.md; finally fall back to keyword presence in the text.
    def _bull_has_invalidation():
        # Primary: structured file
        if bull_json.get("invalidation_conditions"):
            return True
        # Fallback 1: embedded JSON block inside bull_text.md
        if bull:
            json_match = re.search(r'```json\s*(.*?)\s*```', bull, re.DOTALL)
            if json_match:
                try:
                    embedded = json.loads(json_match.group(1))
                    if embedded.get("invalidation_conditions"):
                        return True
                except (json.JSONDecodeError, AttributeError):
                    pass
        # Fallback 2: explicit "Invalidation" heading or keyword in text
        if bull and re.search(r'invalidat(e|ion|ing)|what would make (us|me) wrong', bull, re.I):
            return True
        return False

    scores["bull_has_inval"] = _bull_has_invalidation()
    
    # RM synthesis
    rm = read_text("research_manager.md")
    if rm:
        upper = rm.upper()
        scores["rm_has_decision"] = ("BUY" in upper or "SELL" in upper or "HOLD" in upper)
        scores["rm_mentions_both"] = bool(re.search(r'bull|positive', rm, re.I) and re.search(r'bear|negative', rm, re.I))
        
    # Execution plan constraints
    exec_plan = read_json("execution_plan.json")
    if exec_plan:
        plan = exec_plan.get("execution_plan", {})
        dir_ = plan.get("direction", "").upper()
        
        scores["no_short_sell"] = (dir_ != "SELL_SHORT")
        
        pos_sizing = plan.get("position_sizing", {})
        # position_sizing is a dict; check target_shares vs max_shares when both present
        if isinstance(pos_sizing, dict):
            target = pos_sizing.get("target_shares") or pos_sizing.get("shares", 0)
            max_s = pos_sizing.get("max_shares") or pos_sizing.get("max_shares_total", 0)
            scores["pos_limit_ok"] = (target <= max_s) if (target and max_s) else True
        else:
            scores["pos_limit_ok"] = True
        
        if dir_ == "BUY" or plan.get("decision", "").upper() == "BUY":
            # stop_loss is nested inside exit_logic
            exit_logic = plan.get("exit_logic", {})
            scores["stop_loss_ok"] = bool(
                exit_logic.get("stop_loss") or plan.get("stop_loss")
            )

            # Price ordering sanity: stop < entry < take_profit
            entry_zone = plan.get("entry_zone", {})
            entry_min = entry_zone.get("min") or entry_zone.get("price") or 0
            stop = exit_logic.get("stop_loss") or plan.get("stop_loss") or 0
            tp = exit_logic.get("take_profit") or plan.get("take_profit") or 0
            if entry_min and stop:
                scores["stop_below_entry"] = float(stop) < float(entry_min)
            if entry_min and tp:
                scores["tp_above_entry"] = float(tp) > float(entry_min)
            
    # Risk debate sections
    risk = read_text("risk_debate.md")
    if risk:
        # Check for presence of headers (approximate sections)
        sections = 0
        if "RISK" in risk.upper() or "🔴" in risk: sections += 1
        if "SAFE" in risk.upper() or "🟢" in risk: sections += 1
        if "NEUTRAL" in risk.upper() or "🟡" in risk: sections += 1
        scores["risk_parts"] = sections
        
    return scores

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    
    base_dir = Path(args.dir)
    results = []
    
    if base_dir.exists():
        for ticker_dir in base_dir.iterdir():
            if ticker_dir.is_dir():
                ticker = ticker_dir.name
                for date_dir in ticker_dir.iterdir():
                    if date_dir.is_dir():
                        date_str = date_dir.name
                        scores = score_artifacts(ticker, date_str, base_dir)
                        results.append(scores)
                        
    if not results:
        print("No artifacts found to score.")
        return
        
    keys = results[0].keys()
    with open(args.output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(results)
        
    print(f"Scored {len(results)} artifact runs to {args.output}")

if __name__ == "__main__":
    main()
