#!/usr/bin/env python3
"""Capture reasoning artifacts for manual and automated quality review."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
import argparse
from typing import List

def capture_artifacts(ticker: str, trade_date: str, output_dir: str):
    """Run the full pipeline and save all reasoning artifacts."""
    from copy import deepcopy
    
    # We must not mutate DEFAULT_CONFIG globally
    config = deepcopy(DEFAULT_CONFIG)
    config["backtest_mode"] = True
    config["trade_date"] = trade_date

    print(f"Capturing artifacts for {ticker} on {trade_date}")
    
    tag = TradingAgentsGraph(
        selected_analysts=["market", "social", "news", "fundamentals"],
        config=config,
    )

    try:
        final_state, signal = tag.propagate(ticker, trade_date)
    except Exception as e:
        print(f"ERROR: Graph invocation failed for {ticker} on {trade_date}: {e}")
        import traceback
        traceback.print_exc()
        return

    out_path = Path(output_dir) / ticker / trade_date
    out_path.mkdir(parents=True, exist_ok=True)
    
    invest_state = final_state.get("investment_debate_state", {})
    risk_state = final_state.get("risk_debate_state", {})
    exec_plan = final_state.get("execution_plan", {})
    risk_assess = final_state.get("risk_assessment", {})
    
    def write_json(fname, data):
        if data:
            with open(out_path / fname, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                
    def write_txt(fname, text):
        if text:
            with open(out_path / fname, "w") as f:
                f.write(text)

    # Investment
    write_json("bull_thesis.json", invest_state.get("bull_thesis"))
    write_json("bear_thesis.json", invest_state.get("bear_thesis"))
    write_txt("bull_text.md", invest_state.get("bull_history"))
    write_txt("bear_text.md", invest_state.get("bear_history"))
    # investment_plan lives on the top-level state, not inside investment_debate_state
    write_txt("research_manager.md", final_state.get("investment_plan"))
    
    # Execution
    write_json("execution_plan.json", exec_plan)
    write_txt("trader_text.md", final_state.get("trader_investment_plan"))
    
    # Risk
    write_txt("risk_debate.md", risk_state.get("history"))
    write_txt("risk_risky.md", risk_state.get("risky_history"))
    write_txt("risk_safe.md", risk_state.get("safe_history"))
    write_txt("risk_neutral.md", risk_state.get("neutral_history"))
    write_json("risk_assessment.json", risk_assess)
    
    # Final — write both the raw decision string and the clean signal
    write_txt("final_decision.txt", signal)
    write_json("confidence.json", final_state.get("confidence_scores"))
    
    print(f"Saved artifacts to {out_path}")

def main():
    parser = argparse.ArgumentParser(description="Capture reasoning artifacts")
    parser.add_argument("--ticker", required=True, help="Ticker symbol e.g. COMI.CA")
    parser.add_argument("--dates", required=True, help="Comma-separated dates")
    parser.add_argument("--output", default="results/reasoning_artifacts", help="Output dir")
    args = parser.parse_args()
    
    dates = [d.strip() for d in args.dates.split(",")]
    
    for d in dates:
        capture_artifacts(args.ticker, d, args.output)

if __name__ == "__main__":
    main()
