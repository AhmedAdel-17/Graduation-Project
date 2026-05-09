"""
Constitutional Risk Manager for EGX Trading System.

This is the THIRD layer of the risk pipeline (runs after deterministic scoring
and merged LLM debate):

    Risk Scorer -> (VETO) -> Risk Veto Node -> END
    Risk Scorer -> (ALLOW/WARN/THROTTLE) -> Merged Risk Debate -> Risk Manager -> END

The Risk Manager is an LLM-based Constitutional critic anchored to an explicit
EGX Trading Constitution (Bai et al. 2022 Constitutional AI pattern).
Its role is:
  1. Evaluate qualitative risks the deterministic scorer cannot capture
  2. Check trade against each constitution clause explicitly
  3. Confirm or override the Trader's recommendation

It does NOT re-run deterministic checks (those are done by risk_scorer.py).
It does NOT veto on hard-rule violations (those were caught before this node ran).

A lightweight Final Deterministic Gate runs after the LLM decision to catch
the 4 cases where LLM output could contradict hard facts:
  - SELL with no open position (long-only constraint)
  - BUY on foreign-restricted ticker (SCEM, SDTI)
  - LLM action contradicts execution_plan decision (flag only, no override)

Architecture literature:
- Bai et al. (Anthropic 2022): Constitutional AI — explicit rule list anchors LLM critique
- Shinn et al. (NeurIPS 2023): Reflexion Actor + Evaluator + Self-Reflector pattern
- FinCon (Yu et al. NeurIPS 2024): CVaR-computed metrics fed to LLM manager
- Zheng et al. (NeurIPS 2023): position bias, verbosity bias mitigations in LLM judges
- Mosqueira-Rey et al. (AI Review 2023): HITL — LLM as advisor, human/rule oversight on hard cases
"""

import json
import re
from typing import List, Tuple, Optional

from tradingagents.dataflows.config import get_config
from tradingagents.agents.risk_mgmt.risk_scorer import EGX_FOREIGN_RESTRICTED


# =============================================================================
# EGX Trading Constitution
# Explicit rule list for Constitutional AI anchoring (Bai et al. 2022).
# The LLM Risk Manager evaluates each clause explicitly.
# =============================================================================

EGX_TRADING_CONSTITUTION = """
## EGX TRADING CONSTITUTION
Evaluate this trade against each clause. Reference clause numbers in your response.

**Market Structure Constraints (Regulatory Facts)**
1. LONG-ONLY: SELL means exit/reduce an existing long position only.
   Opening a short position is operationally forbidden on EGX (FRA short-sell regs,
   150% cash collateral required, MCDR execution — inaccessible to algorithmic flow).
2. NO LEVERAGE: 100% cash only. No margin, borrowed capital, or leveraged instruments (2x/3x).
3. PRICE BANDS: EGX List B stocks have a ±10% daily limit (halt at ±5%).
   List A stocks have a ±20% daily limit (halt at ±10%).
   Orders priced outside the band are rejected by the exchange.
4. SETTLEMENT: T+2 via MCDR. Factor into exit timing and cash management.
5. NO MARKET ORDERS: Only limit, limit_ioc, VWAP, and TWAP order types on EGX.

**EGX Microstructure Rules (Empirically Grounded)**
6. MAGNET ZONE: Avoid entries within 1.5% of the daily band limit.
   Adverse selection and forced unwinding are elevated near band limits (Farag 2013, 2015).
7. REVERSAL PATTERN: After a limit-down event, EGX exhibits a 1-day price reversal.
   Do NOT close long positions during a confirmed circuit-breaker halt (Farag 2013).
8. CIRCUIT BREAKERS: The EGX100 index-level halt is asymmetric (declines only, since March 2020).
   Do not chase prices or initiate new positions during a market-wide halt.
9. TICK SIZE: 0.001 EGP for prices below 2 EGP; 0.01 EGP otherwise (since Sept 2018).
   Limit prices must conform to the applicable tick size.

**Position Sizing and Risk Limits (Institutionally Grounded)**
10. POSITION SIZING: Maximum 10% of portfolio in any single stock (UCITS Art. 52(2)).
11. STOP-LOSS: All BUY/SELL trades require a defined stop-loss.
    ATR-based (2×ATR(14)) is preferred; 5% fixed is only a backstop.
12. LIQUIDITY: Daily entry must not exceed 10% ADV (hard veto).
    Throttle zone: 5–10% ADV (position auto-adjusted by scorer).
    Low-liquidity stocks: halved thresholds apply.
13. LOSS LIMIT: Maximum 2% of portfolio at risk per trade (Elder 1993; Tharp 2008).

**Eligibility Constraints**
14. FOREIGN-RESTRICTED TICKERS: SCEM and SDTI have foreign ownership limits.
    Verify eligibility before recommending BUY for these tickers.
15. SELL ELIGIBILITY: A SELL recommendation is only executable if the portfolio
    currently holds shares of the relevant stock. Cash-only portfolios cannot SELL.
"""


# =============================================================================
# Final Deterministic Gate
# =============================================================================

def _final_gate(
    decision: str,
    exec_plan: dict,
    current_position: dict,
) -> Tuple[str, List[str]]:
    """
    Lightweight sanity checks AFTER the LLM decision.
    Only catches cases where LLM output contradicts hard, observable facts.
    Does NOT duplicate the Risk Scorer's checks.

    Returns (final_decision, list_of_override_notes).
    """
    issues: List[str] = []
    _pos = current_position or {}

    # Gate 1: SELL with no open position (long-only, EGX)
    if decision == "SELL" and _pos.get("shares", 0) == 0:
        issues.append(
            "Final gate override: LLM issued SELL but portfolio has no open position "
            "(EGX long-only constraint — SELL is exit-only). Overriding to HOLD."
        )
        return "HOLD", issues

    # Gate 2: BUY on foreign-restricted ticker
    symbol = (exec_plan.get("symbol") or "").upper()
    if decision == "BUY" and symbol in EGX_FOREIGN_RESTRICTED:
        issues.append(
            f"Final gate override: {symbol} is on EGX foreign-restricted list (SCEM/SDTI). "
            "BUY overridden to HOLD — verify foreign ownership eligibility first."
        )
        return "HOLD", issues

    # Gate 3: LLM action contradicts execution_plan direction (flag, no override)
    plan_decision = (exec_plan.get("decision") or "").strip().upper()
    if plan_decision and decision not in ("HOLD",) and decision != plan_decision:
        issues.append(
            f"Final gate note: LLM decided {decision} but Trader's execution_plan says "
            f"{plan_decision}. No override — LLM may have valid qualitative reasons. "
            "Review risk_debate_state for context."
        )

    return decision, issues


# =============================================================================
# Risk Manager Node Factory
# =============================================================================

def create_risk_manager(llm, memory):
    """
    Create the Constitutional Risk Manager node.

    This node:
    - Receives pre-scored risk_assessment, risk_action, and risk_metrics from Risk Scorer
    - Receives the merged debate output from Merged Risk Debate
    - Runs an LLM critique anchored to the EGX Trading Constitution
    - Applies a final deterministic gate before returning
    - Does NOT re-run deterministic checks
    """

    def risk_manager_node(state: dict) -> dict:
        company_name = state.get("company_of_interest", "")
        config = get_config()
        target_market = config.get("target_market", "US")
        is_egx = target_market == "EGX"

        # ── Inputs from Risk Scorer ───────────────────────────────────────────
        risk_assessment = state.get("risk_assessment") or {}
        risk_action = state.get("risk_action", "ALLOW")
        risk_metrics = state.get("risk_metrics") or {}

        # ── Merged debate output ──────────────────────────────────────────────
        risk_debate_state = state.get("risk_debate_state") or {}
        history = risk_debate_state.get("history", "")

        # ── Execution plan (possibly throttled by scorer) ─────────────────────
        execution_plan_raw = state.get("execution_plan") or {}
        if isinstance(execution_plan_raw, dict):
            exec_plan = execution_plan_raw.get("execution_plan", execution_plan_raw)
        else:
            exec_plan = {}

        # ── Portfolio context ─────────────────────────────────────────────────
        current_position = state.get("current_position") or {}

        if current_position.get("shares", 0) > 0:
            _p = current_position
            position_context = (
                f"HOLDING {_p['shares']:,} shares @ avg cost {_p.get('avg_cost', 0):.2f} EGP | "
                f"Market value: {_p.get('market_value', 0):,.2f} EGP | "
                f"Unrealised P&L: {_p.get('unrealised_pnl', 0):,.2f} EGP"
            )
        else:
            position_context = (
                "NO OPEN POSITION — portfolio is 100% cash. "
                "A SELL recommendation cannot be executed (long-only, EGX). "
                "Only BUY or HOLD are actionable."
            )

        # ── Memory context ────────────────────────────────────────────────────
        market_report = state.get("market_report", "")
        news_report = state.get("news_report", "")
        fundamentals_report = state.get("fundamentals_report", "")
        sentiment_report = state.get("sentiment_report", "")
        curr_situation = (
            f"{market_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        )
        past_memories = memory.get_memories(curr_situation, n_matches=2)
        past_memory_str = "".join(
            rec["recommendation"] + "\n\n" for rec in past_memories
        )

        # ── Build scorer context section ──────────────────────────────────────
        # Strip veto_explanation from assessment shown to LLM (VETO never reaches here)
        assessment_for_llm = {
            k: v for k, v in risk_assessment.items()
            if k not in ("veto_explanation",)
        }

        scorer_context = f"""
## Deterministic Risk Scorer Result
Risk Action: **{risk_action}**
(ALLOW = all checks passed | WARN = non-critical issues | THROTTLE = position auto-adjusted to 5% ADV)

### Risk Metrics (computed pre-LLM)
```json
{json.dumps(risk_metrics, indent=2, default=str)}
```

### Full Scorer Assessment
```json
{json.dumps(assessment_for_llm, indent=2, default=str)}
```
"""

        # ── Constitution section ──────────────────────────────────────────────
        constitution_section = EGX_TRADING_CONSTITUTION if is_egx else ""

        # ── Data availability note (EGX annual-reporting context) ─────────────
        data_note = (
            "\n**Data availability**: Annual data (FY2022–FY2024) is sufficient for EGX "
            "institutional due diligence. Absence of quarterly data is NOT a veto condition."
            if is_egx else ""
        )

        # ── Full prompt ───────────────────────────────────────────────────────
        prompt = f"""You are the Constitutional Risk Manager for {company_name} on {"EGX" if is_egx else "the market"}.

{constitution_section}

{scorer_context}

## Current Portfolio Position
{position_context}
{data_note}

## Trader's Execution Plan (post-throttle if THROTTLE was applied)
```json
{json.dumps(exec_plan, indent=2, default=str)}
```

## Risk Debate Summary (Risky / Safe / Neutral perspectives)
{history[-2000:] if len(history) > 2000 else history}

## Lessons from Past Decisions
{past_memory_str if past_memory_str.strip() else "No relevant past decisions found."}

## Your Task
The Deterministic Risk Scorer has already enforced all hard rules.
Your role is QUALITATIVE critique only:

1. **Constitution check**: Explicitly note which constitution clauses are satisfied and flag any concerns.
2. **Debate quality**: Evaluate whether the Risky/Safe/Neutral perspectives are substantive or shallow.
   - If they agree too easily, flag as low-quality debate.
   - If one perspective dominates without evidence, reduce confidence.
3. **Qualitative risks**: Identify risks the scorer cannot capture (regime change, news catalysts, management quality, EGX-specific microstructure concerns not already scored).
4. **Decision**: Confirm or override the Trader's recommendation.

**You must NOT**:
- Re-invent or override the scorer's deterministic checks.
- Invent a qualitative veto if risk_action is ALLOW/WARN/THROTTLE.
- Be swayed by confident tone without substantive evidence in the debate.
- Issue a SELL if the position context shows no open shares.

**Bias mitigations (Zheng et al. 2023)**:
- Do not favour a perspective because it appeared first in the debate.
- Do not favour a longer/more verbose perspective — assess substance, not length.
- Do not agree with the Trader's plan simply because it was well-formatted.

Your response format:
1. Constitution compliance: 2–3 sentences referencing specific clauses.
2. Debate quality note: 1 sentence.
3. Qualitative risk commentary: 2–3 sentences.
4. Final decision:

```json
{{"action": "BUY", "confidence": 0.0}}
```
Replace BUY with SELL or HOLD. Confidence: 0.0 (low) to 1.0 (high).
"""

        response = llm.invoke(prompt)

        # ── Parse LLM decision ────────────────────────────────────────────────
        final_trade_decision: Optional[str] = None

        # Pattern 1: ```json { "action": "BUY" } ```
        _m = re.search(r"```json\s*(\{[^`]+\})\s*```", response.content, re.DOTALL)
        if _m:
            try:
                _obj = json.loads(_m.group(1))
                final_trade_decision = (_obj.get("action") or "").upper() or None
            except Exception:
                pass

        # Pattern 2: bare { "action": "BUY" }
        if not final_trade_decision:
            _m2 = re.search(
                r'\{[^{}]*"action"\s*:\s*"(BUY|SELL|HOLD)"[^{}]*\}',
                response.content, re.IGNORECASE,
            )
            if _m2:
                _inner = re.search(
                    r'"action"\s*:\s*"(BUY|SELL|HOLD)"', _m2.group(0), re.IGNORECASE
                )
                if _inner:
                    final_trade_decision = _inner.group(1).upper()

        # Pattern 3: lone BUY / SELL / HOLD keyword
        if not final_trade_decision:
            _m3 = re.search(r"\b(BUY|SELL|HOLD)\b", response.content, re.IGNORECASE)
            if _m3:
                final_trade_decision = _m3.group(1).upper()

        # Last resort: return raw text
        if not final_trade_decision:
            final_trade_decision = response.content

        # ── Final deterministic gate ──────────────────────────────────────────
        final_trade_decision, gate_issues = _final_gate(
            final_trade_decision, exec_plan, current_position
        )
        if gate_issues:
            risk_assessment = {**risk_assessment, "final_gate_notes": gate_issues}

        # ── Update risk_debate_state ──────────────────────────────────────────
        new_risk_debate_state = {
            "judge_decision": response.content,
            "history": risk_debate_state.get("history", ""),
            "risky_history": risk_debate_state.get("risky_history", ""),
            "safe_history": risk_debate_state.get("safe_history", ""),
            "neutral_history": risk_debate_state.get("neutral_history", ""),
            "latest_speaker": "Risk_Manager",
            "current_risky_response": risk_debate_state.get("current_risky_response", ""),
            "current_safe_response": risk_debate_state.get("current_safe_response", ""),
            "current_neutral_response": risk_debate_state.get("current_neutral_response", ""),
            "count": risk_debate_state.get("count", 0),
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision,
            "risk_assessment": risk_assessment,
        }

    return risk_manager_node


# =============================================================================
# Backward-compatibility re-exports (functions moved to risk_scorer.py)
# Tests and scripts that imported these from risk_manager continue to work.
# =============================================================================
from tradingagents.agents.risk_mgmt.risk_scorer import (  # noqa: E402, F401
    check_short_selling_violation,
    check_leverage_violation,
    run_all_risk_checks,
    EGX_RISK_LIMITS,
)
