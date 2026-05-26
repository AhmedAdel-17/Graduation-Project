import time
import json
import logging
import re

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.macro_provider import format_macro_context_for_prompt
from tradingagents.agents.utils.llm_failover import safe_invoke

logger = logging.getLogger("tradingagents.research_manager")


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

        # Macro context (injected by DataPrefetcher, optional)
        macro_section = format_macro_context_for_prompt(state.get("macro_context"))

        # Build primary inputs — structured theses preferred, fall back to history
        if bull_thesis:
            bull_section = f"### Bull Thesis (structured)\n{json.dumps(bull_thesis, indent=2)}"
        else:
            bull_section = f"### Bull Argument (last exchange)\n{recent_history}"

        if bear_thesis:
            bear_section = f"### Bear Thesis (structured)\n{json.dumps(bear_thesis, indent=2)}"
        else:
            bear_section = f"### Bear Argument (last exchange)\n{recent_history}"

        # ── Risk-appetite-aware framing ──────────────────────────────────────
        # The user picks one of three profiles per run; the framing below adjusts
        # the bar for issuing BUY/SELL vs falling back to HOLD.
        risk_appetite = str(get_config().get("risk_appetite") or "conservative").lower()

        appetite_framing = {
            "conservative": (
                "## RISK PROFILE: CONSERVATIVE (capital preservation)\n"
                "- HOLD is acceptable if fundamentals show material deterioration even when valuation is cheap.\n"
                "- Require strong, multi-factor alignment (fundamentals + technicals + sentiment) before issuing BUY.\n"
                "- When in doubt, preserve capital.\n"
            ),
            "balanced": (
                "## RISK PROFILE: BALANCED (alpha-seeking with risk control)\n"
                "- HOLD is valid only when bull and bear are roughly even AND risk/reward is unattractive.\n"
                "- Issue BUY when the bull case has a clear edge, even if one factor (e.g., macro) is unfavorable.\n"
                "- Weight catalysts, momentum, and valuation alongside fundamentals — don't let any single negative dominate.\n"
            ),
            "aggressive": (
                "## RISK PROFILE: AGGRESSIVE (opportunistic, high-conviction-led)\n"
                "- HOLD is the LAST RESORT, valid only when bull and bear are EXACTLY balanced.\n"
                "- Issue BUY whenever the bull case has any meaningful edge — even if fundamentals show stress, if technicals/momentum/sentiment are aligned, take the trade.\n"
                "- Cheap valuation + bullish technicals is sufficient for BUY even with elevated fundamental risk.\n"
                "- Macro is a tiebreaker, NOT a veto. The opportunity cost of being in cash matters.\n"
            ),
        }.get(risk_appetite, "")

        prompt = f"""You are the Chief Investment Officer making the FINAL investment decision for {state.get('company_of_interest', 'this stock')}.

{appetite_framing}
## Decision Framework
You MUST commit to one of these decisions:
- **BUY**: The bull case is more compelling. Even moderate bullish evidence with acceptable risk = BUY.
- **SELL**: The bear case is more compelling. Even moderate bearish evidence = SELL (if holding) or AVOID.
- **HOLD**: As specified by your risk profile above.

## CRITICAL RULE
HOLD is a COST — it means missing opportunities. Apply your risk profile honestly when choosing between BUY/SELL/HOLD.

## Macro Environment
{macro_section}

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
        _fallback_msg = type("_FallbackMsg", (), {
            "content": (
                '**1. Strongest Bull Case:** Insufficient data.\n'
                '**2. Strongest Bear Case:** Insufficient data.\n'
                '**3. Why One Side Wins:** LLM call failed — defaulting to HOLD.\n'
                '**4. Decision & Plan:** HOLD\n'
                '```json\n{"decision": "HOLD", "confidence": 0.1, "rationale": "LLM failure fallback"}\n```'
            )
        })()
        response = safe_invoke(
            llm, prompt,
            fallback=_fallback_msg,
            agent_name="Research Manager",
        )

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
