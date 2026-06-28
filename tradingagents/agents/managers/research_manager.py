import time
import json

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.egx_costs import round_trip_cost_pct
from tradingagents.agents.utils.temporal import point_in_time_notice
from tradingagents.agents.utils.agent_context import (
    build_macro_section,
    format_past_memories,
    format_sentiment_section,
    parse_fenced_json,
)


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

        from tradingagents.default_config import DEFAULT_CONFIG
        memory_threshold = float(
            state.get("memory_min_similarity")
            or DEFAULT_CONFIG.get("memory_min_similarity", 0.30)
        )
        past_memory_str = format_past_memories(
            state, memory, min_similarity=memory_threshold
        )

        current_position = state.get("current_position", {})
        is_flat = current_position.get("shares", 0) == 0
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

        # Macro overlay (EGX): inject deterministic macro context into prompt.
        macro_section = build_macro_section(state)

        # Cost hurdle context (EGX round-trip cost a BUY's edge must clear).
        _cfg = get_config()
        _low_liq = bool(state.get("low_liquidity", False))
        _rt_cost_pct = round_trip_cost_pct(_low_liq) * 100.0
        _edge_mult = float(_cfg.get("min_edge_cost_multiple", 0.0) or 0.0)
        if _edge_mult > 0:
            _hurdle_pct = _edge_mult * _rt_cost_pct
            cost_section = (
                f"## Trading-Cost Hurdle (EGX)\n"
                f"Round-trip cost for this {'low-liquidity' if _low_liq else 'normal-liquidity'} "
                f"name is ~{_rt_cost_pct:.2f}% (commission + slippage, both sides).\n"
                f"A BUY only makes sense if the expected base-case upside clears "
                f"~{_hurdle_pct:.2f}% ({_edge_mult:g}× cost). If the bull case's base-case "
                f"upside is below that, the expected edge does not pay for the trade — choose HOLD.\n"
            )
        else:
            cost_section = ""

        # ── News + social context (previously read but never shown to the CIO) ──
        # The judge must see the Journalist's news signal (a legitimate DIRECTIONAL
        # input) and the social-sentiment blend (EXECUTION-only context, never
        # directional — consistent with the bull/bear treatment).
        news_analysis = state.get("sentiment_analysis") or {}
        if isinstance(news_analysis, dict) and news_analysis:
            combined = news_analysis.get("combined_sentiment") or {}
            news_section = (
                "### News Signal (Journalist — directional input)\n"
                f"Sentiment: {news_analysis.get('sentiment', 'n/a')} "
                f"(strength {news_analysis.get('sentiment_strength', 'n/a')}, "
                f"confidence {news_analysis.get('confidence_score', 'n/a')}/100)\n"
                f"Combined transformer+LLM: label={combined.get('label', 'n/a')}, "
                f"score={combined.get('score', 'n/a')}, "
                f"confidence={combined.get('confidence', 'n/a')}\n"
                f"Explanation: {news_analysis.get('explanation', '')}\n"
                f"Catalysts from news: {news_analysis.get('catalysts_from_news', [])}\n"
                f"Risks from news: {news_analysis.get('risks_from_news', [])}"
            )
        else:
            news_section = (
                "### News Signal (Journalist — directional input)\n"
                f"{news_report or 'No news report available — treat as low-confidence silence.'}"
            )

        social_section = (
            "### Social Sentiment (execution-only context, NOT a directional input)\n"
            + format_sentiment_section(sentiment_report, state.get("sentiment_blend_result"))
        )

        # ── Decision framework — two disclosed variants ───────────────────────
        # Default (live): capital-preservation-first, HOLD-friendly.
        # DECISIVE backtest mode (config['backtest_decisive_mode']): used ONLY in
        # the thesis event-study backtest, where the news + social analysts are
        # intentionally unavailable (no historical archive). In that setting the
        # default framework reads "data is insufficient -> HOLD" on EVERY name,
        # so the system never expresses a directional view and cannot be
        # evaluated. Decisive mode tells the CIO that the absence of news/social
        # is a KNOWN, EXPECTED backtest condition (not "insufficient data") and to
        # commit to the evidence-based directional call from fundamentals +
        # technicals. This is a disclosed configuration, NOT the live decision
        # logic — live runs are unaffected.
        if bool(get_config().get("backtest_decisive_mode", False)):
            decision_framework_section = """## Decision Framework — DECISIVE BACKTEST MODE (disclosed configuration)
This is a HISTORICAL BACKTEST that evaluates your DIRECTIONAL STOCK-SELECTION skill.
The news and social-sentiment analysts are intentionally unavailable (no historical
archive; using live news would be look-ahead). The absence of news/social coverage is
a KNOWN, EXPECTED condition of this backtest — you are FORBIDDEN from citing an
"information vacuum", "missing news", "zero coverage", "ahead of earnings", or general
macro/currency uncertainty as a reason to avoid a decision. Every listed EGX stock
always has these same conditions here.

You MUST output **BUY** or **SELL**. Weigh the bull thesis against the bear thesis on
the fundamentals + technicals you DO have, and commit to the side the evidence favors:

- **BUY**: the balance of fundamental + technical evidence leans bullish — you expect
  this stock to rise over the holding window. Moderate conviction is enough; you do not
  need a slam-dunk.
- **SELL**: the balance leans bearish — you expect underperformance / would not own it.
- **HOLD**: essentially FORBIDDEN. Use it ONLY in the near-impossible case that the
  bullish and bearish evidence are *exactly* equal with no tie-breaker. Caution,
  distress signals, or uncertainty are NOT reasons for HOLD — bearishness is a **SELL**,
  not a HOLD. "Stay in cash" is NOT an available answer in this mode.

Make the call the evidence points to. A confident-sounding hedge into HOLD/cash is the
one outcome that is not allowed here.

## Signal Weighting — PRIORITIZE THE TIMELY TECHNICAL TREND
The fundamentals here are ANNUAL and can be up to a year stale, and in a high-inflation /
serial-devaluation economy backward-looking statements lag reality badly. The Market
Analyst's TECHNICAL / PRICE-TREND signal (below) reflects CURRENT market action and is the
most timely evidence you have. Weight it heavily:
- A clear UPTREND (price above its key moving averages with positive multi-month momentum)
  is DECISIVE evidence for **BUY** and must NOT be overridden by stale fundamental concerns
  (high leverage, weak Piotroski) unless those point to imminent insolvency.
- A clear DOWNTREND supports **SELL**.
- Only when the technical trend is genuinely flat/ambiguous should the fundamentals break
  the tie."""
        else:
            if is_flat:
                # No open position. EGX is long-only, so SELL is NOT a valid
                # option here — there is nothing to sell and no shorting. The
                # decision space is exactly {BUY, HOLD}. A bearish read means
                # HOLD (stay in cash / do not buy), NOT SELL. This is what
                # prevents the "judge says SELL but verdict is HOLD" contradiction.
                decision_framework_section = """## Decision Framework (NO open position — long-only EGX)
You hold NO shares, so the ONLY valid decisions are **BUY** or **HOLD**.
SELL is NOT available (nothing to sell, no short selling on EGX). If your view is
bearish, the decision is **HOLD** (stay in cash / do not buy) — never SELL.

Commit to the side the evidence favors; do not hide behind HOLD when the evidence
actually leans one way.

- **BUY**: Choose when the bull case is the stronger side AND the expected upside
  exceeds the downside after trading costs. A clear, moderate bullish tilt with
  acceptable risk/reward is ENOUGH — you do not need a slam-dunk. Capital
  preservation matters, but chronically defaulting to HOLD is itself a failure to
  express a view.
- **HOLD**: Choose when the bull and bear cases are genuinely balanced, the
  risk/reward after costs is unattractive, the read is bearish (so you simply do
  not buy), or the data is insufficient.

## CRITICAL RULE
A confident wrong BUY costs real money, so do not manufacture conviction. But equally,
if the bull case is clearly the stronger side with favorable risk/reward, choosing HOLD
"to be safe" is the wrong call — make the BUY."""
            else:
                decision_framework_section = """## Decision Framework (existing long position)
You currently HOLD shares. Capital preservation is the default, but commit to the
side the evidence favors.

- **BUY**: Add to / maintain the position when the bull case is the stronger side AND
  the expected upside exceeds the downside after trading costs. A clear, moderate
  bullish tilt with acceptable risk/reward is ENOUGH.
- **SELL**: Reduce or exit when the bear case is clearly stronger — SELL protects
  capital already at risk (EGX is long-only; it is not a way to profit from a decline).
- **HOLD**: Choose when the cases are genuinely balanced, the risk/reward after costs
  is unattractive, or the data is insufficient.

## CRITICAL RULE
A confident wrong trade costs real money, so do not manufacture conviction. But do not
default to HOLD when the evidence clearly leans one way — express the view the evidence
supports."""

        # Technical/price-trend section. The CIO previously read state['market_report']
        # (line ~19) but NEVER showed it, so the timely momentum signal was lost in the
        # decision. In decisive/momentum backtest mode we surface it prominently so the
        # CIO can weight the current price trend against the (stale) annual fundamentals.
        # Live mode is unchanged (empty section) to preserve existing live behavior.
        if bool(get_config().get("backtest_decisive_mode", False)) and market_research_report:
            technical_section = (
                "### Technical / Price-Trend Signal (Market Analyst — TIMELY directional input)\n"
                + str(market_research_report)[:2000]
            )
        else:
            technical_section = ""

        # Position-aware option wording (precomputed to avoid nested-quote
        # f-string expressions, a SyntaxError on Python < 3.12).
        if is_flat:
            sec4_options = "BUY or HOLD only (no open position — SELL is not available)"
            json_decision_options = "BUY|HOLD"
        else:
            sec4_options = "BUY, SELL, or HOLD"
            json_decision_options = "BUY|SELL|HOLD"

        prompt = f"""You are the Chief Investment Officer making the FINAL investment decision for {state.get('company_of_interest', 'this stock')}.

{point_in_time_notice(state.get("trade_date", ""))}
{macro_section}

{cost_section}
{decision_framework_section}

## Structured Research Output

{bull_section}

{bear_section}

{technical_section}

{news_section}

{social_section}

### Recent Debate Exchange (last few turns)
{recent_history}

### Lessons from Past Decisions
{past_memory_str}

## Required Output
Structure your response with these four sections — each must be present:

**1. Strongest Bull Case:** In 1-2 sentences, state the most compelling bullish argument.
**2. Strongest Bear Case:** In 1-2 sentences, state the most compelling bearish argument.
**3. Why One Side Wins:** Explain in 2-3 sentences which side has the decisive edge and why the other side falls short.
**4. Decision & Plan:** State {sec4_options}, then provide a detailed investment plan for the trader.

End with:
```json
{{"decision": "{json_decision_options}", "confidence": 0.0-1.0, "rationale": "one sentence"}}
```

Speak naturally, as if presenting to a portfolio committee.{position_context}"""
        response = llm.invoke(prompt)

        # Try to extract structured decision from the response
        decision_json = parse_fenced_json(
            response.content, pattern=r'```json\s*(\{[^`]+\})\s*```'
        )

        # Safety: with no open position EGX is long-only, so a CIO SELL is not
        # actionable. Coerce the structured decision to HOLD so it can never be
        # passed downstream as SELL (the final risk gate also catches this, but
        # normalizing here keeps the structured decision self-consistent with the
        # position-aware framework above).
        if is_flat and isinstance(decision_json, dict):
            if str(decision_json.get("decision") or "").strip().upper() == "SELL":
                decision_json["decision"] = "HOLD"

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
