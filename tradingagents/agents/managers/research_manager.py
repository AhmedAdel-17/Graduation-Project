import time
import json
import re

from tradingagents.graph.node_record import get_recorder, hash_state_slice, hash_string


# ── P7 helpers: build deterministic context sections for the prompt ──────────

def _build_confidence_context(state: dict) -> str:
    """Build a short structured confidence summary from analyst outputs.

    Reads ``technical_analysis``, ``sentiment_analysis``, and
    ``fundamental_analysis`` dicts already written to state by the
    analyst nodes.  Returns a markdown section string (empty if no
    confidence data is available).
    """
    lines = []

    # Technical / Market analyst
    tech = state.get("technical_analysis") or {}
    if isinstance(tech, dict):
        tc = tech.get("confidence_score")
        if tc is not None:
            tc_val = float(tc) if float(tc) <= 1.0 else float(tc) / 100.0
            lines.append(f"- Technical/Market analyst confidence: {tc_val:.0%}")

    # Fundamentals analyst
    fund = state.get("fundamental_analysis") or {}
    if isinstance(fund, dict):
        qs = fund.get("quality_status", {})
        quality_level = qs.get("level", "")

        if quality_level == "unavailable":
            lines.append(
                "- Fundamentals: UNAVAILABLE — no financial data for this ticker. "
                "This is a legitimate HOLD/skip condition."
            )
        else:
            # Use effective_confidence (penalized for degradation) when available,
            # fall back to confidence_score / data_completeness for legacy compat
            fc = fund.get("effective_confidence") or fund.get("confidence_score") or fund.get("data_completeness")
            if fc is not None:
                fc_val = float(fc) if float(fc) <= 1.0 else float(fc) / 100.0
                tag = ""
                if quality_level == "deterministic_only" and qs.get("enrichment_attempted"):
                    tag = " (DEGRADED — enrichment failed; ratios only, no thesis/valuation)"
                elif quality_level == "deterministic_only":
                    tag = " (deterministic-only — no interpretive enrichment)"
                elif quality_level == "partial":
                    tag = " (partial enrichment — some CoT stages failed)"
                lines.append(f"- Fundamentals analyst confidence: {fc_val:.0%}{tag}")

    # News / Sentiment analyst
    sent = state.get("sentiment_analysis") or {}
    if isinstance(sent, dict):
        sc = sent.get("confidence_score")
        if sc is not None:
            sc_val = float(sc) if float(sc) <= 1.0 else float(sc) / 100.0
            news_absent = sent.get("news_absent", False)
            tag = " (no news — neutral default)" if news_absent else ""
            lines.append(f"- News/Sentiment analyst confidence: {sc_val:.0%}{tag}")

    if not lines:
        return ""

    return (
        "## Analyst Confidence Summary (P7)\n"
        "These are the per-analyst confidence scores from their structured outputs.\n"
        "Low confidence in any analyst means that analyst's conclusions are less reliable.\n"
        + "\n".join(lines)
    )


def _build_cash_drag_section(state: dict, current_position: dict) -> str:
    """Build cash-drag / benchmark awareness prompt section.

    Only emitted when the portfolio is 100% cash AND the EGX30 trend
    is positive (bullish).  Returns empty string otherwise.
    """
    has_position = current_position.get("shares", 0) > 0
    if has_position:
        return ""

    macro = state.get("macro_context") or {}
    egx30_trend = macro.get("egx30_trend", "unknown")
    egx30_ret_1m = macro.get("egx30_return_1m")

    if egx30_trend not in ("bullish",):
        return ""

    ret_str = f"{egx30_ret_1m * 100:.1f}%" if egx30_ret_1m is not None else "positive"

    return (
        "## Cash-Drag / Benchmark Awareness (P7)\n"
        f"The portfolio is currently 100% CASH and the EGX30 has returned {ret_str} over the last month (trend: {egx30_trend}).\n"
        "- Staying in cash during a rising market is an ACTIVE decision to underperform the benchmark.\n"
        "- The opportunity cost of HOLD must be weighed against entry risk. In a rising market, the burden of proof shifts: "
        "you need a concrete stock-specific reason to AVOID entry, not a generic reason to be cautious.\n"
        "- A high risk-free rate alone does NOT justify staying in cash when stock-specific signals are positive and the broad market is rising — "
        "the rate environment is already priced into equity valuations.\n"
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

        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        from tradingagents.default_config import DEFAULT_CONFIG
        ticker = state.get("company_of_interest", "")
        memory_where = {"ticker": ticker} if ticker else None
        memory_threshold = float(
            state.get("memory_min_similarity")
            or DEFAULT_CONFIG.get("memory_min_similarity", 0.30)
        )
        trade_date = state.get("trade_date")
        past_memories = memory.get_memories(
            curr_situation,
            n_matches=2,
            where=memory_where,
            min_similarity=memory_threshold,
            as_of_date=str(trade_date) if trade_date else None,
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

        # ── Anti-churn: inject previous decision context ──────────────────
        # Only inject continuity for an active stance (BUY or SELL).
        # HOLD means no position to protect — no anti-reversal bias needed.
        continuity_context = ""
        prev_decision = state.get("previous_decision")
        prev_date = state.get("previous_decision_date")
        if prev_decision and prev_date and prev_decision == "BUY":
            continuity_context = (
                f"\n\n## TRADING CONTINUITY CONTEXT\n"
                f"Your previous decision was **BUY** on {prev_date} — "
                f"you currently hold a long position.\n"
                f"Before switching to SELL, ensure at least one of these is clearly met:\n"
                f"- Stop-loss level was breached\n"
                f"- The original buy thesis was invalidated by new concrete evidence\n"
                f"- Fundamentals materially deteriorated (earnings miss, solvency shift)\n"
                f"- Sharp drawdown or volatility event since the buy\n\n"
                f"If the bull case remains intact with no material deterioration, "
                f"staying in the position is reasonable. But do NOT default to HOLD "
                f"simply because arguments are balanced — evaluate on merit."
            )
        elif prev_decision and prev_date and prev_decision == "SELL":
            continuity_context = (
                f"\n\n## TRADING CONTINUITY CONTEXT\n"
                f"Your previous decision was **SELL** on {prev_date} — "
                f"you exited the position and are in cash.\n"
                f"Before re-entering with BUY, ensure at least one of these is clearly met:\n"
                f"- A strong new catalyst that fundamentally changes the outlook\n"
                f"- The bear thesis that drove the sell has been invalidated\n"
                f"- Fundamentals materially improved since the sell date\n"
                f"- Price has corrected to a level with favorable risk-reward\n\n"
                f"If conditions are largely unchanged from the sell date, "
                f"staying in cash is reasonable. But do NOT default to HOLD "
                f"simply because arguments are balanced — evaluate on merit."
            )
        # When prev_decision is HOLD or None: no continuity context injected.
        # There is no active stance to protect, so no anti-reversal bias.

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
        try:
            from tradingagents.dataflows.macro_provider import format_macro_context_for_prompt
            macro_section = format_macro_context_for_prompt(state.get("macro_context"))
        except Exception:
            macro_section = ""

        # ── P7: Build confidence context from analyst structured outputs ────
        confidence_context = _build_confidence_context(state)

        # ── P7: Cash-drag / benchmark awareness ────────────────────────────
        cash_drag_section = _build_cash_drag_section(state, current_position)

        # ── Investor profile context (when running for a specific user) ───
        investor_section = ""
        ic = state.get("investor_context")
        if ic:
            investor_section = (
                "## Investor Profile Context\n"
                f"- Risk tolerance: {ic.get('risk_tolerance', 'moderate')}\n"
                f"- Investment horizon: {ic.get('investment_horizon', 'medium_term').replace('_', ' ')}\n"
                f"- Trading style: {ic.get('trading_style', 'position')}\n"
                f"- Benchmark: {ic.get('benchmark_target', 'EGX30')}\n"
                f"- Sector exclusions: {', '.join(ic.get('sector_exclusions', [])) or 'none'}\n\n"
                "Risk tolerance calibrates sizing and urgency, not evidence quality.\n"
                "A conservative investor needs stronger evidence to act; an aggressive "
                "investor does NOT get a pass on weak evidence."
            )

        prompt = f"""You are the Chief Investment Officer making the FINAL investment decision for {state.get('company_of_interest', 'this stock')}.

{macro_section}

## Decision Target
Your decision question is: **"Will this ticker outperform EGX30 over the next evaluation window, net of risk and costs?"**
- BUY means you expect the ticker to beat the index.
- HOLD/SELL means you expect the ticker to match or lag the index — the investor is better off in cash or the benchmark.

## Decision Framework
You MUST commit to one of these decisions:
- **BUY**: The bull case is more compelling AND the stock is likely to outperform EGX30. Even moderate bullish evidence with acceptable risk = BUY.
- **SELL (when already positioned)**: Requires HIGH-CONFIDENCE thesis break, clear downside risk, or expected underperformance vs EGX30 large enough to justify trading costs and missed rebound risk. If the original bull thesis is intact but uncertain, prefer HOLD over SELL — exiting has asymmetric costs (realized losses, commissions, missed recovery).
- **AVOID (when not positioned)**: The bear case is more compelling OR the stock is likely to underperform EGX30. Even moderate bearish evidence = AVOID.
- **HOLD**: ONLY valid if:
  (a) The data is genuinely insufficient to form any view, OR
  (b) The bull and bear cases are EXACTLY balanced AND expected return is close to the benchmark.

## CRITICAL RULE
HOLD is a COST — it means missing opportunities. If the stock has a reasonable chance of beating EGX30 and the bull case has even a slight edge, you MUST choose BUY. Indecision is worse than a small mistake.

{cash_drag_section}

## Multi-Signal Alignment Rule (P7)
When TWO OR MORE of the following independent stock-specific signals are positive, you should lean BUY unless the bear thesis identifies a concrete stock-specific blocker (see Legitimate HOLD Blockers below):
- momentum_label is "moderate_up" or "strong_up"
- rs_label is "outperforming" (stock is beating EGX30)
- volume_confirmed is true (price move backed by volume)
- Valuation is reasonable relative to sector history (P/E not extreme, earnings yield acceptable for sector)
- Bull thesis contains concrete, near-term catalysts (not generic macro hopes)
- Fundamentals are fresh and show stable or improving trends (revenue growth, margin expansion, or improving ratios)

Two or more aligned positive signals represent a preponderance of evidence. Defaulting to HOLD despite multi-signal alignment is a decision error unless a specific blocker applies.

## Legitimate HOLD Blockers (P7)
Even with positive signals, HOLD is justified ONLY when one of these concrete stock-specific conditions exists:
- **Stale or low-confidence fundamentals**: Financial data is outdated (>2 quarters old) or data_confidence is below 40%, making any fundamental conclusion unreliable.
- **Solvency or liquidity deterioration**: Debt/equity is rising sharply, current ratio is falling below 1.0, or interest coverage is declining — the company's ability to meet obligations is in question.
- **Extreme valuation without earnings support**: P/E above 40x with no corresponding earnings growth, or negative/negligible earnings yield caused by stock-specific overvaluation (not the high-rate regime).
- **Volume/liquidity trap**: Average daily volume is so low that entering or exiting would move the price significantly (>10% of ADV for a reasonable position size), or the stock has multi-day zero-volume stretches.
- **Deteriorating momentum with no catalyst**: momentum_label is "moderate_down" or "strong_down" AND rs_label is "underperforming" AND no credible near-term catalyst exists to reverse the trend.

If NONE of these blockers apply and you still choose HOLD, you must explicitly state why — generic caution or "waiting for more clarity" is not sufficient.

## High-Rate Regime Guidance (EGX — P4)
When the macro environment shows CBE policy rate or T-bill yields above 15%:
- Negative earnings-yield spread (EY < risk-free rate) is STRUCTURALLY COMMON across all EGX equities. It is the default state, not a distinguishing signal.
- "Cash earns 27%" or "EY spread is negative" is a MARKET-WIDE observation, NOT a stock-specific bear case. Do NOT treat it as a standalone decisive reason to HOLD.
- It may be cited as context or as one factor among several, but it should only dominate the decision when stock-specific positive evidence is WEAK or ABSENT.
- A generic high cash yield argument does NOT override strong stock-specific bullish evidence.

In high-rate regimes, give MORE weight to stock-specific evidence from the researcher theses:
- Price momentum direction and strength (momentum_label)
- Relative strength vs EGX30 (rs_label) — outperforming stocks are differentiating themselves
- Volume confirmation of price moves
- Revenue and earnings GROWTH trajectory (not just level)
- Sector-specific value drivers (NIM expansion for banks, NAV/replacement-cost for real estate)
- Catalyst proximity (earnings releases, rate-cut expectations, FX events, regulatory changes)

If the bull case shows (positive momentum + outperforming RS + growth positive OR strong sector catalyst) AND no critical stock-specific risk exists, lean BUY. The bear must identify a specific NEAR-TERM stock-level risk beyond the generic rate comparison to justify HOLD.

**EXTREME VALUATION GUARDRAIL:** Very high P/E (above 40x), very low or negative earnings yield caused by stock-specific overvaluation, weak/deteriorating fundamentals, or insufficient earnings history remains a legitimate stock-specific risk INDEPENDENT of the rate regime. Do not override this with momentum alone. Momentum at extreme valuations without fundamental support can indicate speculation rather than value creation.

{confidence_context}

{investor_section}

## Decision Consistency Self-Check (MANDATORY — P4)
Before outputting your final decision:
1. Re-read your own "Why One Side Wins" reasoning.
2. Verify your chosen action (BUY/SELL|HOLD) is the LOGICAL CONCLUSION of that reasoning.
3. If your reasoning concludes favorable risk-reward or asymmetric upside, the action must be BUY — not HOLD by default.
4. If your action is HOLD, your reasoning must NOT contain concrete execution steps or unqualified bullish conclusions. HOLD requires the reasoning to genuinely reflect uncertainty or balance.
5. "When in doubt, HOLD" is acceptable ONLY when the reasoning genuinely concludes uncertainty — not when it concludes a directional lean that you then override with conservatism.
6. If two or more stock-specific signals are positive and no Legitimate HOLD Blocker applies, your action MUST be BUY — re-check before finalizing HOLD.

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

Speak naturally, as if presenting to a portfolio committee.{position_context}{continuity_context}"""

        # ── Recording: capture input state and prompt ────────────────────
        recorder = get_recorder(state)
        _input_keys = [
            "investment_debate_state", "market_report", "sentiment_report",
            "news_report", "fundamentals_report", "company_of_interest",
            "current_position", "macro_context",
        ]
        _input_hash = hash_state_slice(state, _input_keys) if recorder else ""
        _prompt_hash = hash_string(prompt) if recorder else ""

        t0 = time.monotonic()
        response = llm.invoke(prompt)
        _elapsed_ms = (time.monotonic() - t0) * 1000

        # Try to extract structured decision from the response
        decision_json = None
        _record_status = "success"
        _fallback_source = None
        json_match = re.search(r'```json\s*(\{[^`]+\})\s*```', response.content, re.DOTALL)
        if json_match:
            try:
                decision_json = json.loads(json_match.group(1))
            except json.JSONDecodeError:
                _record_status = "fallback"
                _fallback_source = "json_parse_failure"

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
        _return = {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": response.content,
        }

        # ── Recording: write record ──────────────────────────────────────
        if recorder:
            _signal = None
            if decision_json and isinstance(decision_json, dict):
                _signal = decision_json.get("decision")
            recorder.record(
                node_name="research_manager",
                trade_date=state.get("trade_date", ""),
                input_state_keys=_input_keys,
                input_hash=_input_hash,
                prompt_hash=_prompt_hash,
                prompt_text=prompt,
                raw_output=response.content,
                state_update=_return,
                state_update_keys=["investment_debate_state", "investment_plan"],
                signal=_signal,
                wall_clock_ms=_elapsed_ms,
                status=_record_status,
                fallback_source=_fallback_source,
            )

        return _return

    return research_manager_node
