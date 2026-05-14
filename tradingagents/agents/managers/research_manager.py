import time
import json
import re


def create_research_manager(llm, memory):
    def research_manager_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        # ── Phase 2e: Consume structured theses (primary input) ──────────────
        # Use the structured bull/bear JSON theses written by the researchers
        # instead of the full debate history.  Only include the last exchange
        # from history as context — the rest is already captured in the theses.
        bull_thesis = investment_debate_state.get("bull_thesis", {})
        bear_thesis = investment_debate_state.get("bear_thesis", {})

        # Extract only the last exchange (most recent claim from each side)
        history_lines = [l for l in history.strip().split("\n") if l.strip()]
        # Keep last 4 lines (typically 2 from Bull, 2 from Bear)
        recent_history = "\n".join(history_lines[-4:]) if history_lines else "(no debate history)"

        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        from tradingagents.default_config import DEFAULT_CONFIG
        ticker = state.get("company_of_interest", "")
        memory_where = {"ticker": ticker} if ticker else None
        memory_threshold = float(
            state.get("memory_min_similarity")
            or DEFAULT_CONFIG.get("memory_min_similarity", 0.30)
        )
        past_memories = memory.get_memories(
            curr_situation,
            n_matches=2,
            where=memory_where,
            min_similarity=memory_threshold,
        )

        past_memory_str = ""
        for i, rec in enumerate(past_memories, 1):
            past_memory_str += rec["recommendation"] + "\n\n"

        current_position = state.get("current_position", {})
        if current_position.get("shares", 0) > 0:
            position_context = (
                f"\n\n## CURRENT PORTFOLIO POSITION\n"
                f"You are currently HOLDING {current_position['shares']:,} shares "
                f"at avg cost {current_position.get('avg_cost', 0):.2f} EGP.\n"
                f"Unrealised P&L: {current_position.get('unrealised_pnl', 0):,.2f} EGP.\n"
                f"Consider this when deciding: SELL to lock in profits/cut losses, "
                f"or HOLD to let the position ride."
            )
        else:
            position_context = (
                "\n\n## CURRENT PORTFOLIO POSITION\n"
                "You have NO open position. You are 100% cash.\n"
                "A SELL recommendation is NOT actionable (no shares to sell, no short selling on EGX).\n"
                "Choose BUY to open a new position, or HOLD to stay in cash."
            )

        # Build primary inputs — structured theses preferred, fall back to history
        if bull_thesis:
            bull_section = f"### Bull Thesis (structured)\n{json.dumps(bull_thesis, indent=2)}"
        else:
            bull_section = f"### Bull Argument (last exchange)\n{recent_history}"

        if bear_thesis:
            bear_section = f"### Bear Thesis (structured)\n{json.dumps(bear_thesis, indent=2)}"
        else:
            bear_section = f"### Bear Argument (last exchange)\n{recent_history}"

        prompt = f"""You are the Chief Investment Officer making the FINAL investment decision for {state.get('company_of_interest', 'this stock')}.

## Decision Framework
You MUST commit to one of these decisions:
- **BUY**: The bull case is more compelling. Even moderate bullish evidence with acceptable risk = BUY.
- **SELL**: The bear case is more compelling. Even moderate bearish evidence = SELL (if holding) or AVOID.
- **HOLD**: ONLY valid if:
  (a) The data is genuinely insufficient to form any view, OR
  (b) The bull and bear cases are EXACTLY balanced AND the risk/reward is unfavorable.

## CRITICAL RULE
HOLD is a COST — it means missing opportunities. If either the bull or bear case has even a slight edge, you MUST choose that side. Indecision is worse than a small mistake.

## Structured Research Output

{bull_section}

{bear_section}

### Recent Debate Exchange (last few turns)
{recent_history}

### Lessons from Past Decisions
{past_memory_str}

## Required Output
Structure your response with these four sections — each must be present:

**1. Strongest Bull Case:** In 1-2 sentences, state the most compelling bullish argument.
**2. Strongest Bear Case:** In 1-2 sentences, state the most compelling bearish argument.
**3. Why One Side Wins:** Explain in 2-3 sentences which side has the decisive edge and why the other side falls short.
**4. Decision & Plan:** State BUY, SELL, or HOLD, then provide a detailed investment plan for the trader.

End with:
```json
{{"decision": "BUY|SELL|HOLD", "confidence": 0.0-1.0, "rationale": "one sentence"}}
```

Speak naturally, as if presenting to a portfolio committee.{position_context}"""
        response = llm.invoke(prompt)

        # Try to extract structured decision from the response
        decision_json = None
        json_match = re.search(r'```json\s*(\{[^`]+\})\s*```', response.content, re.DOTALL)
        if json_match:
            try:
                decision_json = json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        new_investment_debate_state = {
            "judge_decision": response.content,
            "history": investment_debate_state.get("history", ""),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "bull_thesis": bull_thesis,
            "bear_thesis": bear_thesis,
            "current_response": response.content,
            "count": investment_debate_state["count"],
        }

        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": response.content,
        }

    return research_manager_node
