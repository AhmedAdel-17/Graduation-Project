import json
import re
from typing import Any, Dict, Optional
from tradingagents.dataflows.config import get_config

# ---------------------------------------------------------------------------
# Phase 3 helpers (identical to bull_researcher — shared logic)
# ---------------------------------------------------------------------------

_NO_SIGNAL_PHRASES = (
    "insufficient data — excluded",
    "social sentiment: insufficient",
    "layer_c_status: no_signal",
)


def _format_sentiment_section(
    sentiment_report: str,
    blend_result: Optional[Dict[str, Any]],
) -> str:
    """Return a researcher-safe sentiment context string.

    If social sentiment was excluded, instructs the LLM to omit it entirely.
    Otherwise surfaces the blend modifiers with an explicit note that they
    affect execution only, not the directional thesis.
    """
    report_lower = (sentiment_report or "").lower()
    is_no_signal = any(phrase in report_lower for phrase in _NO_SIGNAL_PHRASES)

    if is_no_signal:
        return (
            "EXCLUDED — insufficient social data. "
            "Do NOT reference, speculate about, or include social sentiment in your thesis."
        )

    conf_mult  = 1.0
    size_mult  = 1.0
    blend_audit = "no_blend"
    if isinstance(blend_result, dict):
        conf_mult  = float(blend_result.get("confidence_multiplier",  1.0))
        size_mult  = float(blend_result.get("position_size_multiplier", 1.0))
        blend_audit = str(blend_result.get("audit", "no_blend"))

    return (
        f"{sentiment_report or 'No social sentiment data.'}\n\n"
        f"Sentiment context modifiers (execution only — do NOT use to change thesis direction):\n"
        f"  confidence×{conf_mult:.2f}  |  position-size×{size_mult:.2f}\n"
        f"  [{blend_audit}]"
    )


# =============================================================================
# Bear Researcher for EGX Market
# =============================================================================
# Institutional-grade bearish thesis combining all analyst signals
# CRITICAL: No short selling on EGX - bearish = AVOID or REDUCE position
# Explicit liquidity discussion, time horizons, and invalidation conditions
# =============================================================================


def create_bear_researcher(llm, memory):
    """
    Create the Bear Researcher agent for EGX.
    
    This agent:
    - Combines signals from Technical, Fundamental, and News analysts
    - Does NOT assume short selling is possible (EGX constraint)
    - Recommends AVOID or REDUCE positions, not short
    - Explicitly discusses EGX liquidity constraints
    - Defines time horizons for the thesis
    - Specifies invalidation conditions
    - Outputs structured JSON thesis
    """

    def bear_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bear_history = investment_debate_state.get("bear_history", "")

        current_response = investment_debate_state.get("current_response", "")
        market_research_report = state.get("market_report", "")
        sentiment_report = state.get("sentiment_report", "")
        news_report = state.get("news_report", "")
        fundamentals_report = state.get("fundamentals_report", "")
        
        # Get structured analyses if available
        technical_analysis = state.get("technical_analysis", {})
        fundamental_analysis = state.get("fundamental_analysis", {})
        sentiment_analysis = state.get("sentiment_analysis", {})
        
        # Check if this is EGX market
        config = get_config()
        target_market = config.get("target_market", "US")
        is_egx = target_market == "EGX"
        
        # Get EGX-specific info
        low_liquidity = state.get("low_liquidity", False)
        ticker = state.get("company_of_interest", "")

        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        memory_where = {"ticker": ticker} if ticker else None
        memory_threshold = float(config.get("memory_min_similarity", 0.30))
        past_memories = memory.get_memories(
            curr_situation,
            n_matches=2,
            where=memory_where,
            min_similarity=memory_threshold,
        )

        past_memory_str = ""
        for i, rec in enumerate(past_memories, 1):
            past_memory_str += rec["recommendation"] + "\n\n"

        # ── Phase 3 (PR 8): NO_SIGNAL guard ──────────────────────────────────
        sentiment_section = _format_sentiment_section(
            sentiment_report, state.get("sentiment_blend_result")
        )

        # EGX-specific prompt with NO SHORT SELLING
        egx_context = ""
        if is_egx:
            egx_context = f"""
## EGX Market Context (CRITICAL)
- Market: Egyptian Exchange (EGX)
- Currency: Egyptian Pound (EGP)
- Daily price limit: ±10% (circuit breaker)
- Trading hours: 10:00-14:30 Cairo time
- Liquidity flag for {ticker}: {"LOW LIQUIDITY" if low_liquidity else "Normal liquidity"}

## ⚠️ CRITICAL CONSTRAINT: NO SHORT SELLING ON EGX
You CANNOT recommend shorting the stock. Instead, your bearish thesis should recommend:
- **AVOID**: Do not initiate new positions
- **REDUCE**: Trim or exit existing positions
- **UNDERWEIGHT**: Allocate less than benchmark weight
- **WAIT**: Wait for better entry point before buying

Your bearish thesis is about PROTECTING capital, not profiting from decline.

## Liquidity Considerations for Exit
{"⚠️ LOW LIQUIDITY affects exit strategy. Address:" if low_liquidity else "Consider liquidity for position reduction:"}
- Time required to exit position without price impact
- Potential slippage on sell orders
- Daily volume constraints on execution
- Risk of being "stuck" in the position
"""

        prompt = f"""You are a Bear Researcher building an institutional-grade investment thesis advising AGAINST investing in (or reducing exposure to) the stock.

{egx_context}

## Your Task
Build a comprehensive BEARISH thesis by:
1. COMBINING signals from all analyst reports (Technical, Fundamental, News)
2. Recommending AVOID/REDUCE/UNDERWEIGHT (NOT short selling)
3. Discussing liquidity explicitly (especially for exiting positions)
4. Defining clear time horizons
5. Specifying conditions that would INVALIDATE your bearish thesis

## Signal Integration Requirements
You have access to analyst signals — use JSON when available (more token-efficient):

### Technical Analysis (Chartist):
{json.dumps(technical_analysis, indent=2) if technical_analysis else market_research_report}

### Fundamental Analysis (Accountant):
{json.dumps(fundamental_analysis, indent=2) if fundamental_analysis else fundamentals_report}

### News & Sentiment Analysis (Journalist):
{json.dumps(sentiment_analysis, indent=2) if sentiment_analysis else news_report}

### Social Sentiment (Phase 3 — context modifier, NOT directional input):
{sentiment_section}

## Debate Context
Conversation history: {history}
Last bull argument to counter: {current_response}
Lessons from past similar situations: {past_memory_str}

## Key Points to Address
1. **Signal Agreement**: Where do Technical, Fundamental, and News signals ALIGN to support caution?
2. **Key Risks**: What specific risks threaten the company or stock price?
3. **Valuation Concerns**: Why is current valuation unattractive or risky? (use ranges)
4. **Time Horizon**: When might risks materialize? Define SHORT/MEDIUM/LONG-TERM outlook
5. **Exit Strategy**: For existing holders, how should they {"carefully exit given low liquidity" if low_liquidity else "reduce exposure"}?
6. **Invalidation Conditions**: What would make you WRONG (turn bullish)? Be specific.

## REMEMBER: No Short Selling Recommendations
- ❌ DO NOT recommend: "short the stock", "profit from decline", "sell short"
- ✅ DO recommend: "avoid buying", "reduce position", "wait for lower entry", "underweight vs benchmark"

## Required Output Format
End your argument with a JSON block:

```json
{{
    "thesis_type": "bearish",
    "recommendation": "avoid|reduce|underweight|wait",
    "conviction_level": "high|moderate|low",
    "time_horizon": {{
        "primary": "short_term|medium_term|long_term",
        "risk_materialization": "when key risks may materialize",
        "review_in": "timeframe to reassess thesis"
    }},
    "signal_summary": {{
        "technical": "bullish|neutral|bearish",
        "fundamental": "bullish|neutral|bearish",
        "sentiment": "bullish|neutral|bearish",
        "alignment_score": "strong|moderate|weak"
    }},
    "liquidity_assessment": {{
        "is_low_liquidity": true|false,
        "exit_difficulty": "easy|moderate|difficult",
        "recommended_exit_strategy": "description of how to reduce/exit"
    }},
    "key_risks": ["list of specific risks"],
    "downside_range": {{
        "support_level_1": number,
        "support_level_2": number,
        "worst_case": number,
        "currency": "EGP"
    }},
    "invalidation_conditions": [
        "condition 1 that would make bearish thesis wrong",
        "condition 2"
    ],
    "for_existing_holders": "specific advice for those already holding the stock"
}}
```

Now present your compelling bear argument, counter the bull's claims, and provide your structured thesis. Remember: AVOID/REDUCE recommendations only, NO SHORT SELLING."""

        response = llm.invoke(prompt)

        argument = f"Bear Analyst: {response.content}"
        
        # Try to extract structured thesis
        bear_thesis = None
        try:
            json_match = re.search(r'```json\s*(.*?)\s*```', response.content, re.DOTALL)
            if json_match:
                bear_thesis = json.loads(json_match.group(1))
        except (json.JSONDecodeError, AttributeError):
            bear_thesis = None

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bear_history": bear_history + "\n" + argument,
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
            "bear_thesis": bear_thesis,
            # Pass through bull_thesis so _keep_last doesn't drop it when bear runs after bull
            "bull_thesis": investment_debate_state.get("bull_thesis"),
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bear_node

