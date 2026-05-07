import json
import re
from typing import Any, Dict, Optional
from tradingagents.dataflows.config import get_config

# ---------------------------------------------------------------------------
# Phase 3 helpers
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

    If the social analyst excluded sentiment (NO_SIGNAL), returns an instruction
    to the LLM to omit social sentiment from its thesis entirely.
    Otherwise, surfaces the blend modifiers (confidence/size multipliers) so
    the researcher can mention them in execution context — but explicitly states
    these do NOT change the directional thesis.
    """
    report_lower = (sentiment_report or "").lower()
    is_no_signal = any(phrase in report_lower for phrase in _NO_SIGNAL_PHRASES)

    if is_no_signal:
        return (
            "EXCLUDED — insufficient social data. "
            "Do NOT reference, speculate about, or include social sentiment in your thesis."
        )

    # Show the narrative and the blend modifiers
    conf_mult = 1.0
    size_mult = 1.0
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
# Bull Researcher for EGX Market
# =============================================================================
# Institutional-grade bullish thesis combining all analyst signals
# Explicit liquidity discussion, time horizons, and invalidation conditions
# =============================================================================


def create_bull_researcher(llm, memory):
    """
    Create the Bull Researcher agent for EGX.
    
    This agent:
    - Combines signals from Technical, Fundamental, and News analysts
    - Explicitly discusses EGX liquidity constraints
    - Defines time horizons for the thesis
    - Specifies invalidation conditions
    - Outputs structured JSON thesis
    """

    def bull_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bull_history = investment_debate_state.get("bull_history", "")

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
        past_memories = memory.get_memories(curr_situation, n_matches=2)

        past_memory_str = ""
        for i, rec in enumerate(past_memories, 1):
            past_memory_str += rec["recommendation"] + "\n\n"

        # ── Phase 3 (PR 8): NO_SIGNAL guard ──────────────────────────────────
        # If social sentiment was excluded, replace the raw template with a
        # clear instruction so the LLM does not speculate about social signals.
        sentiment_section = _format_sentiment_section(
            sentiment_report, state.get("sentiment_blend_result")
        )

        # EGX-specific prompt
        egx_context = ""
        if is_egx:
            egx_context = f"""
## EGX Market Context (CRITICAL)
- Market: Egyptian Exchange (EGX)
- Currency: Egyptian Pound (EGP)
- Daily price limit: ±10% (circuit breaker)
- No short selling available
- Liquidity flag for {ticker}: {"LOW LIQUIDITY - factor this into your thesis" if low_liquidity else "Normal liquidity"}
- Trading hours: 10:00-14:30 Cairo time

## Liquidity Considerations
{"⚠️ THIS STOCK HAS LOW LIQUIDITY. You MUST address:" if low_liquidity else "Consider liquidity in your thesis:"}
- Position sizing constraints (max 10% of ADV recommended)
- Entry/exit execution risk
- Potential for price impact on position changes
- Time required to accumulate/exit position
"""

        prompt = f"""You are a Bull Researcher building an institutional-grade investment thesis advocating for investing in the stock.

{egx_context}

## Your Task
Build a comprehensive BULLISH thesis by:
1. COMBINING signals from all analyst reports (Technical, Fundamental, News)
2. Discussing liquidity explicitly
3. Defining clear time horizons
4. Specifying conditions that would INVALIDATE your thesis

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
Last bear argument to counter: {current_response}
Lessons from past similar situations: {past_memory_str}

## Key Points to Address
1. **Signal Agreement**: Where do Technical, Fundamental, and News signals ALIGN to support the bull case?
2. **Catalysts**: What specific events or factors could drive the stock higher?
3. **Valuation Support**: Why is current valuation attractive? (use ranges, not point estimates)
4. **Time Horizon**: Define SHORT-TERM (1-4 weeks), MEDIUM-TERM (1-3 months), LONG-TERM (6-12 months) expectations
5. **Liquidity Plan**: How should an investor build/exit a position given {"low" if low_liquidity else "current"} liquidity?
6. **Invalidation Conditions**: What would make you WRONG? Be specific.

## Required Output Format
End your argument with a JSON block:

```json
{{
    "thesis_type": "bullish",
    "conviction_level": "high|moderate|low",
    "time_horizon": {{
        "primary": "short_term|medium_term|long_term",
        "entry_window": "description of optimal entry timing",
        "expected_duration": "how long to hold"
    }},
    "signal_summary": {{
        "technical": "bullish|neutral|bearish",
        "fundamental": "bullish|neutral|bearish",
        "sentiment": "bullish|neutral|bearish",
        "alignment_score": "strong|moderate|weak"
    }},
    "liquidity_assessment": {{
        "is_low_liquidity": true|false,
        "recommended_position_size": "% of portfolio or ADV constraint",
        "execution_strategy": "description of entry/exit approach"
    }},
    "key_catalysts": ["list of potential positive catalysts"],
    "upside_scenario": {{
        "base_case_upside_pct": number (e.g. 15 means +15% from entry),
        "bull_case_upside_pct": number (e.g. 30 means +30% from entry),
        "downside_risk_pct": number (e.g. -10 means -10% if thesis fails),
        "basis": "brief description of what drives the upside estimate"
    }},
    "invalidation_conditions": [
        "specific condition 1 that would invalidate thesis",
        "specific condition 2"
    ],
    "risk_reward_ratio": "estimated ratio (e.g., 2:1)"
}}
```

Now present your compelling bull argument, counter the bear's concerns, and provide your structured thesis."""

        response = llm.invoke(prompt)

        argument = f"Bull Analyst: {response.content}"
        
        # Try to extract structured thesis
        bull_thesis = None
        try:
            json_match = re.search(r'```json\s*(.*?)\s*```', response.content, re.DOTALL)
            if json_match:
                bull_thesis = json.loads(json_match.group(1))
        except (json.JSONDecodeError, AttributeError):
            bull_thesis = None

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bull_history": bull_history + "\n" + argument,
            "bear_history": investment_debate_state.get("bear_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
            "bull_thesis": bull_thesis,
            # Pass through bear_thesis so _keep_last doesn't drop it when bull runs after bear
            "bear_thesis": investment_debate_state.get("bear_thesis"),
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bull_node

