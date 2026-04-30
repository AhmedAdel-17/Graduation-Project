"""
Merged Risk Debate — 3 perspectives in a single LLM call.

Replaces three sequential calls (Risky → Safe → Neutral) each passing all four
full analyst reports (~85-90% token redundancy) with a single call that:
  1. Arguments from all three perspectives simultaneously
  2. Inputs only the compact execution plan JSON + structured signal summary
     (NOT the full analyst text reports)

Savings: 2 LLM calls, ~10,000-14,000 tokens, ~60-90s per trade date.
"""

import json
from tradingagents.dataflows.config import get_config


def create_merged_risk_debator(llm):
    """
    Create a single node that produces all three risk debate perspectives
    in one LLM call, then synthesizes them into a compact debate history.

    Replaces `create_risky_debator`, `create_neutral_debator`, and
    `create_safe_debator` in the graph (3 sequential nodes → 1 node).
    """

    def merged_risk_node(state) -> dict:
        risk_debate_state = state.get("risk_debate_state", {})
        history = risk_debate_state.get("history", "")

        config = get_config()
        is_egx = config.get("target_market") == "EGX"

        # ── Compact inputs (Phase 3a: NO full analyst reports) ───────────────
        # Use structured data from state instead of the verbose text reports.
        # The full reports are NOT re-passed here — they've already been used
        # by the Bull/Bear researchers and the Trader. Passing them again is
        # the 85-90% token redundancy the plan identifies.
        execution_plan = state.get("execution_plan", {})
        if isinstance(execution_plan, dict):
            exec_plan = execution_plan.get("execution_plan", execution_plan)
        else:
            exec_plan = {}

        technical_analysis = state.get("technical_analysis", {})
        fundamental_analysis = state.get("fundamental_analysis", {})
        sentiment_analysis = state.get("sentiment_analysis", {})
        investment_debate = state.get("investment_debate_state", {})
        bull_thesis = investment_debate.get("bull_thesis", {})
        bear_thesis = investment_debate.get("bear_thesis", {})

        # Compact signal summary for the risk debate
        signal_summary = {
            "technical": {
                k: v for k, v in technical_analysis.items()
                if k in ("trend_direction", "confidence_score", "signal", "recommendation")
            } if technical_analysis else {},
            "fundamental": {
                k: v for k, v in fundamental_analysis.items()
                if k in ("financial_health", "valuation_gap", "recommendation", "confidence_score")
            } if fundamental_analysis else {},
            "sentiment": {
                k: v for k, v in sentiment_analysis.items()
                if k in ("sentiment", "confidence_score", "combined_sentiment")
            } if sentiment_analysis else {},
        }

        # Investment decision from research manager
        investment_plan = state.get("investment_plan", "")
        # Trim to a concise excerpt (first 500 chars + conclusion)
        if len(investment_plan) > 500:
            plan_summary = investment_plan[:500] + "\n...[trimmed for brevity]"
        else:
            plan_summary = investment_plan

        trader_plan = state.get("trader_investment_plan", "")
        if len(trader_plan) > 500:
            trader_summary = trader_plan[:500] + "\n...[trimmed for brevity]"
        else:
            trader_summary = trader_plan

        low_liquidity = state.get("low_liquidity", False)
        ticker = state.get("company_of_interest", "")

        egx_note = ""
        if is_egx:
            egx_note = f"""
## EGX Market Context
- Long-only (no short selling)
- No leverage
- Daily price limit: ±10%
- Liquidity: {"⚠️ LOW" if low_liquidity else "Normal"}
"""

        prompt = f"""You are conducting a 3-perspective risk debate for {ticker}. Present all three viewpoints sequentially, then synthesize.

{egx_note}

## Execution Plan Under Review
```json
{json.dumps(exec_plan, indent=2, default=str)}
```

## Signal Summary (deterministic outputs)
```json
{json.dumps(signal_summary, indent=2, default=str)}
```

## Investment Manager's Recommendation (excerpt)
{plan_summary}

## Trader's Execution Plan (excerpt)
{trader_summary}

---

Present the risk debate in THREE clearly labeled sections:

### 🔴 RISKY ANALYST (Risk-Taking Perspective)
Champion the upside. Challenge overly conservative concerns. Quantify why the risk/reward favors action. Give a specific upside target price. Counter any excessive caution with data-driven arguments.

### 🟢 SAFE ANALYST (Conservative Perspective)  
Focus on capital preservation. Challenge optimistic assumptions. Identify the 2-3 most credible downside scenarios. Quantify potential losses. What would need to be true for this trade to fail?

### 🟡 NEUTRAL ANALYST (Balanced Perspective)
Synthesize the valid points from both sides. Identify the key unresolved risks. What are the conditions under which you would lean bullish vs. bearish? Provide a balanced risk-adjusted view.

### 📋 SYNTHESIS
In 2-3 sentences, summarize the key risk tensions and what the Risk Manager should weigh most heavily.

Be specific with prices, percentages, and timeframes. Output conversationally, no special formatting needed within each section."""

        response = llm.invoke(prompt)
        debate_text = response.content

        # Parse out individual perspective sections for state compatibility
        import re

        def _extract_section(text: str, heading: str) -> str:
            pattern = rf"(?:###?\s*[🔴🟢🟡📋]*\s*{re.escape(heading)}.*?)(?=###?\s*[🔴🟢🟡📋]|$)"
            m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            return m.group(0).strip() if m else ""

        risky_arg = _extract_section(debate_text, "RISKY ANALYST") or debate_text[:800]
        safe_arg = _extract_section(debate_text, "SAFE ANALYST") or debate_text[800:1600]
        neutral_arg = _extract_section(debate_text, "NEUTRAL ANALYST") or debate_text[1600:2400]

        # Build merged debate history (compatible with existing risk_debate_state schema)
        new_risk_debate_state = {
            "history": (history + "\n\n" + debate_text).strip(),
            "risky_history": risky_arg,
            "safe_history": safe_arg,
            "neutral_history": neutral_arg,
            "latest_speaker": "MergedRiskDebate",
            "current_risky_response": risky_arg,
            "current_safe_response": safe_arg,
            "current_neutral_response": neutral_arg,
            # count=3 so Risk Manager sees a "completed" debate
            "count": risk_debate_state.get("count", 0) + 3,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return merged_risk_node
