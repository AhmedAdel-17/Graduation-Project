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

        # Portfolio holdings are intentionally NOT an input to the signal. This is
        # a research / decision-support tool that emits a directional BUY / HOLD /
        # SELL VIEW on the stock. Whether the user is long, flat, or in cash is an
        # execution concern for the human PM — never a factor that softens or gates
        # the thesis. The signal is generated the same way regardless of position.
        position_context = ""

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
            # Position-agnostic directional view. The decision space is always the
            # full {BUY, HOLD, SELL} — portfolio holdings do NOT gate it. A bearish
            # read is a SELL, never a HOLD-because-we-hold-no-shares.
            decision_framework_section = """## Decision Framework — directional research view
Emit a directional VIEW on the stock. Portfolio holdings are NOT an input: produce the
signal the evidence supports regardless of whether the reader is long, flat, or in cash.

- **BUY**: the balance of evidence is bullish — you expect the stock to rise /
  outperform over the holding window, and the expected upside exceeds the downside
  after trading costs. A clear, moderate bullish tilt is ENOUGH — you do not need a
  slam-dunk.
- **SELL**: the balance of evidence is bearish — you expect the stock to fall /
  underperform. A bearish read is a SELL, not a HOLD. (Execution note only: EGX is
  long-only, so acting on SELL means exit/avoid, never short — that is the human PM's
  concern and must NOT soften your view into HOLD.)
- **HOLD**: the bull and bear cases are genuinely balanced, or the data is genuinely
  insufficient to judge — there is no directional edge either way.

Weigh BUY, SELL and HOLD on the SAME evidentiary bar. HOLD is a real "no edge" verdict
with its own opportunity cost, not a safe default to hide behind.

## CRITICAL RULE
Commit to the direction the evidence supports. Do not manufacture conviction, but do not
collapse a genuinely bullish or bearish read into HOLD to feel safe. Decide on the
evidence, not on which answer feels safer."""

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

        # Full directional option set — position-agnostic.
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

        # Capture the CIO's OWN confidence in the final decision. This is the
        # confidence the dashboard should surface — it describes the decision
        # that was actually made, unlike the bull researcher's conviction_level
        # (which describes the bull thesis even when it lost the debate).
        cio_confidence: float | None = None
        if isinstance(decision_json, dict):
            raw_conf = decision_json.get("confidence")
            if raw_conf is not None:
                try:
                    cio_confidence = max(0.0, min(1.0, float(raw_conf)))
                except (TypeError, ValueError):
                    cio_confidence = None

        new_investment_debate_state = {
            "judge_decision": response.content,
            "history": investment_debate_state.get("history", ""),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "bull_thesis": bull_thesis,
            "bear_thesis": bear_thesis,
            "current_response": response.content,
            "count": investment_debate_state["count"],
            "cio_confidence": cio_confidence,
        }

        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": response.content,
        }

    return research_manager_node
