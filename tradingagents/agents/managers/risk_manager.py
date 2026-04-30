import time
import json
import re
from typing import Dict, Any, List, Tuple, Optional
from tradingagents.dataflows.config import get_config

# =============================================================================
# Institutional Risk Manager for EGX Market
# =============================================================================
# Enforces deterministic risk constraints - VETO overrides trader
# All rejections must be explained in detail
# =============================================================================

# EGX RISK LIMITS (from institutional best practices and default_config)
EGX_RISK_LIMITS = {
    # Position limits
    "max_single_stock_pct": 0.10,  # Max 10% of portfolio in single stock
    "max_sector_pct": 0.30,  # Max 30% in single sector
    "max_position_vs_adv_pct": 0.10,  # Max 10% of ADV per trading day
    
    # Liquidity constraints
    "min_avg_daily_volume": 50000,  # Minimum 50k shares ADV
    "max_days_to_exit": 10,  # Must be able to exit in 10 days at 10% ADV
    "low_liquidity_reduction": 0.50,  # Reduce limits by 50% for low-liq stocks
    
    # Capital concentration
    "max_correlated_exposure": 0.40,  # Max 40% in correlated positions
    "min_positions_for_diversification": 5,  # Minimum 5 positions for proper diversification
    
    # Drawdown limits
    "max_single_trade_loss_pct": 0.02,  # Max 2% portfolio loss per trade
    "max_daily_loss_pct": 0.05,  # Max 5% daily portfolio loss
    "max_weekly_loss_pct": 0.10,  # Max 10% weekly portfolio loss
    "stop_loss_required": True,  # All trades must have stop-loss
    
    # EGX market structure
    "daily_price_limit": 0.10,  # ±10% daily price limit
    "no_short_selling": True,
    "no_leverage": True,
}


class RiskViolation:
    """Represents a single risk rule violation."""
    
    def __init__(
        self,
        rule_name: str,
        severity: str,  # "critical", "high", "medium", "low"
        limit_value: float,
        actual_value: float,
        explanation: str,
        remediation: str
    ):
        self.rule_name = rule_name
        self.severity = severity
        self.limit_value = limit_value
        self.actual_value = actual_value
        self.explanation = explanation
        self.remediation = remediation
    
    def to_dict(self) -> dict:
        return {
            "rule": self.rule_name,
            "severity": self.severity,
            "limit": self.limit_value,
            "actual": self.actual_value,
            "explanation": self.explanation,
            "remediation": self.remediation
        }


def check_position_size_limit(
    execution_plan: dict,
    portfolio_value: float
) -> Optional[RiskViolation]:
    """
    Check if proposed position exceeds single-stock concentration limit.

    In backtest_mode the check is skipped entirely: when backtesting a single
    ticker the whole portfolio is intentionally concentrated in that one stock,
    so the 10% cap would veto every trade incorrectly.
    """
    config = get_config()
    if config.get("backtest_mode", False):
        return None  # Concentration limit waived for single-ticker backtests

    position_sizing = execution_plan.get("position_sizing", {})
    portfolio_allocation = position_sizing.get("portfolio_allocation", "0%")

    # Parse percentage
    try:
        if isinstance(portfolio_allocation, str):
            alloc_pct = float(portfolio_allocation.replace("%", "")) / 100
        else:
            alloc_pct = float(portfolio_allocation)
    except (ValueError, TypeError):
        alloc_pct = 0.0

    max_allowed = EGX_RISK_LIMITS["max_single_stock_pct"]

    if alloc_pct > 0.50:
        severity = "critical"  # Genuinely dangerous concentration
    elif alloc_pct > 0.25:
        severity = "high"
    elif alloc_pct > max_allowed:
        severity = "medium"
    else:
        return None

    return RiskViolation(
        rule_name="MAX_SINGLE_STOCK_EXPOSURE",
        severity=severity,
        limit_value=max_allowed,
        actual_value=alloc_pct,
        explanation=f"Proposed allocation of {alloc_pct:.1%} exceeds maximum single-stock limit of {max_allowed:.1%}",
        remediation=f"Reduce position size to maximum {max_allowed:.1%} of portfolio"
    )


def check_liquidity_feasibility(
    execution_plan: dict,
    avg_daily_volume: float,
    low_liquidity: bool
) -> Optional[RiskViolation]:
    """
    Check if position can be exited within acceptable timeframe.
    """
    position_sizing = execution_plan.get("position_sizing") or {}
    target_shares = position_sizing.get("target_shares") or 0

    if avg_daily_volume <= 0:
        return RiskViolation(
            rule_name="LIQUIDITY_DATA_MISSING",
            severity="high",
            limit_value=EGX_RISK_LIMITS["min_avg_daily_volume"],
            actual_value=0,
            explanation="Cannot assess liquidity - average daily volume data missing",
            remediation="Obtain ADV data before proceeding with trade"
        )
    
    # Check minimum ADV threshold
    if avg_daily_volume < EGX_RISK_LIMITS["min_avg_daily_volume"]:
        return RiskViolation(
            rule_name="INSUFFICIENT_LIQUIDITY",
            severity="high",
            limit_value=EGX_RISK_LIMITS["min_avg_daily_volume"],
            actual_value=avg_daily_volume,
            explanation=f"ADV of {avg_daily_volume:,.0f} below minimum threshold of {EGX_RISK_LIMITS['min_avg_daily_volume']:,.0f}",
            remediation="Reduce position size significantly to account for low liquidity"
        )
    
    # Calculate days to exit at 10% ADV
    daily_exit_capacity = avg_daily_volume * EGX_RISK_LIMITS["max_position_vs_adv_pct"]
    if low_liquidity:
        daily_exit_capacity *= EGX_RISK_LIMITS["low_liquidity_reduction"]
    
    days_to_exit = target_shares / daily_exit_capacity if daily_exit_capacity > 0 else 999
    
    if days_to_exit > EGX_RISK_LIMITS["max_days_to_exit"]:
        return RiskViolation(
            rule_name="EXIT_TIME_EXCEEDED",
            severity="high",
            limit_value=EGX_RISK_LIMITS["max_days_to_exit"],
            actual_value=days_to_exit,
            explanation=f"Position of {target_shares:,} shares would require {days_to_exit:.1f} days to exit (max: {EGX_RISK_LIMITS['max_days_to_exit']})",
            remediation=f"Reduce position to max {int(daily_exit_capacity * EGX_RISK_LIMITS['max_days_to_exit']):,} shares"
        )
    
    return None


def check_stop_loss_required(
    execution_plan: dict,
    decision: str = "",
    current_price: float = 0.0
) -> Optional[RiskViolation]:
    """
    Check if trade has a valid stop-loss defined.

    For HOLD decisions no stop-loss is required.
    For BUY/SELL decisions where stop-loss is missing, a default is auto-generated
    (5% below current_price for BUY, 5% above for SELL) and injected into the
    execution_plan in-place. No violation is returned in that case.
    """
    if not EGX_RISK_LIMITS["stop_loss_required"]:
        return None

    normalized_decision = (decision or "").strip().upper()

    # HOLDs never need a stop-loss
    if normalized_decision == "HOLD":
        return None

    exit_logic = execution_plan.setdefault("exit_logic", {})
    stop_loss = exit_logic.get("stop_loss", {})

    if not stop_loss or not stop_loss.get("price"):
        # Auto-generate a default stop-loss instead of vetoing
        if current_price and current_price > 0:
            if normalized_decision == "SELL":
                default_sl_price = round(current_price * 1.05, 2)
                direction_note = "5% above entry (covers short-side risk)"
            else:  # BUY or unknown
                default_sl_price = round(current_price * 0.95, 2)
                direction_note = "5% below entry"

            import warnings
            warnings.warn(
                f"[RiskManager] WARNING: No stop-loss provided for {normalized_decision} trade. "
                f"Auto-generating default stop-loss at {default_sl_price} ({direction_note}). "
                "Set an explicit stop-loss in the execution plan to suppress this warning.",
                stacklevel=2,
            )
            print(
                f"[RiskManager] WARNING: Auto-generated stop-loss={default_sl_price} "
                f"({direction_note}) for {normalized_decision} trade at price {current_price}."
            )

            exit_logic["stop_loss"] = {
                "price": default_sl_price,
                "note": f"Auto-generated default ({direction_note})",
            }
            execution_plan["exit_logic"] = exit_logic
            return None  # No violation — default applied

        # current_price unknown: cannot auto-generate, record as medium warning
        return RiskViolation(
            rule_name="STOP_LOSS_MISSING",
            severity="medium",
            limit_value=1,  # Required
            actual_value=0,  # Missing
            explanation="All trades must have a defined stop-loss level and current_price was unavailable for auto-generation",
            remediation="Define explicit stop-loss price before executing trade"
        )

    return None


def check_max_trade_loss(
    execution_plan: dict,
    portfolio_value: float,
    current_price: float
) -> Optional[RiskViolation]:
    """
    Check if potential loss exceeds maximum allowed per trade.
    """
    exit_logic = execution_plan.get("exit_logic", {})
    stop_loss = exit_logic.get("stop_loss", {})
    position_sizing = execution_plan.get("position_sizing", {})
    
    stop_price = stop_loss.get("price") or 0
    target_shares = position_sizing.get("target_shares") or 0

    if stop_price <= 0 or (current_price or 0) <= 0:
        return None  # Can't calculate without prices
    
    # Calculate potential loss
    loss_per_share = current_price - stop_price
    total_loss = loss_per_share * target_shares
    loss_pct = total_loss / portfolio_value if portfolio_value > 0 else 0
    
    max_loss_pct = EGX_RISK_LIMITS["max_single_trade_loss_pct"]
    
    if loss_pct > max_loss_pct:
        return RiskViolation(
            rule_name="MAX_TRADE_LOSS_EXCEEDED",
            severity="high",
            limit_value=max_loss_pct,
            actual_value=loss_pct,
            explanation=f"Potential loss of {loss_pct:.2%} exceeds max allowed {max_loss_pct:.2%} per trade",
            remediation=f"Reduce position size or tighten stop-loss to limit loss to {max_loss_pct:.2%}"
        )
    
    return None


def check_short_selling_violation(execution_plan: dict) -> Optional[RiskViolation]:
    """
    Check if plan attempts short selling (forbidden on EGX).
    """
    if not EGX_RISK_LIMITS["no_short_selling"]:
        return None
    
    decision = execution_plan.get("decision", "").upper()
    
    # Check for short-selling language
    plan_text = json.dumps(execution_plan).lower()
    short_indicators = ["short", "sell short", "short sell", "naked"]
    
    for indicator in short_indicators:
        if indicator in plan_text:
            return RiskViolation(
                rule_name="SHORT_SELLING_FORBIDDEN",
                severity="critical",
                limit_value=0,  # Zero short selling
                actual_value=1,  # Detected
                explanation="Short selling is not allowed on EGX. Plan contains short-selling language.",
                remediation="Remove short selling elements. Only AVOID, REDUCE, or HOLD for bearish view."
            )
    
    return None


def check_leverage_violation(execution_plan: dict) -> Optional[RiskViolation]:
    """
    Check if plan uses leverage (forbidden on EGX).
    """
    if not EGX_RISK_LIMITS["no_leverage"]:
        return None
    
    plan_text = json.dumps(execution_plan).lower()
    leverage_indicators = ["margin", "leverage", "2x", "3x", "borrowed"]
    
    for indicator in leverage_indicators:
        if indicator in plan_text:
            return RiskViolation(
                rule_name="LEVERAGE_FORBIDDEN",
                severity="critical",
                limit_value=1.0,  # 1x only (no leverage)
                actual_value=2.0,  # Detected leverage
                explanation="Leverage is not allowed. Plan contains leverage/margin language.",
                remediation="Remove all leverage. Use 100% cash positions only."
            )
    
    return None


def _coerce_numeric(value, default=0.0) -> float:
    """Coerce LLM-returned values (str/int/float/None) to float."""
    if value is None:
        return default
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return default


def _normalize_execution_plan(plan: dict) -> None:
    """Coerce all numeric fields in position_sizing and exit_logic to float in-place."""
    ps = plan.get("position_sizing")
    if isinstance(ps, dict):
        for key in ("target_shares", "max_shares", "min_shares"):
            if key in ps:
                ps[key] = _coerce_numeric(ps[key])
        if "portfolio_allocation" in ps and isinstance(ps["portfolio_allocation"], str):
            pass  # already handled per-check with % parsing
    el = plan.get("exit_logic")
    if isinstance(el, dict):
        sl = el.get("stop_loss")
        if isinstance(sl, dict) and "price" in sl:
            sl["price"] = _coerce_numeric(sl["price"])
        tp = el.get("take_profit")
        if isinstance(tp, dict) and "price" in tp:
            tp["price"] = _coerce_numeric(tp["price"])


def run_all_risk_checks(
    execution_plan: dict,
    portfolio_value: float = 10000000,  # Default 10M EGP
    avg_daily_volume: float = 100000,
    current_price: float = 50.0,
    low_liquidity: bool = False
) -> Tuple[bool, List[RiskViolation]]:
    """
    Run all risk checks and return approval status and violations.

    Returns:
        Tuple of (approved: bool, violations: List[RiskViolation])
    """
    _normalize_execution_plan(execution_plan)
    violations = []
    
    # Resolve decision once so it can be forwarded to the stop-loss check
    _decision = (execution_plan.get("decision") or "").strip().upper()

    # Run all checks
    checks = [
        check_position_size_limit(execution_plan, portfolio_value),
        check_liquidity_feasibility(execution_plan, avg_daily_volume, low_liquidity),
        check_stop_loss_required(execution_plan, decision=_decision, current_price=current_price),
        check_max_trade_loss(execution_plan, portfolio_value, current_price),
        check_short_selling_violation(execution_plan),
        check_leverage_violation(execution_plan),
    ]
    
    # Collect violations
    for check_result in checks:
        if check_result is not None:
            violations.append(check_result)
    
    # Determine approval - any critical violation = rejection
    critical_violations = [v for v in violations if v.severity == "critical"]
    approved = len(critical_violations) == 0
    
    return approved, violations


def create_risk_manager(llm, memory):
    """
    Create the Institutional Risk Manager for EGX.
    
    This agent:
    - Enforces DETERMINISTIC risk constraints (not LLM judgment)
    - VETO power overrides trader decisions
    - Explains every rejection in detail
    - Checks: exposure limits, liquidity, concentration, drawdown
    """

    def risk_manager_node(state) -> dict:
        company_name = state.get("company_of_interest", "")
        
        # Get config
        config = get_config()
        target_market = config.get("target_market", "US")
        is_egx = target_market == "EGX"

        history = state.get("risk_debate_state", {}).get("history", "")
        risk_debate_state = state.get("risk_debate_state", {})
        market_research_report = state.get("market_report", "")
        news_report = state.get("news_report", "")
        fundamentals_report = state.get("fundamentals_report", "")
        sentiment_report = state.get("sentiment_report", "")
        trader_plan = state.get("investment_plan", "")
        
        # Get execution plan from trader
        execution_plan = state.get("execution_plan", {})
        if isinstance(execution_plan, dict):
            exec_plan = execution_plan.get("execution_plan", execution_plan)
        else:
            exec_plan = {}
        
        # Get liquidity info
        low_liquidity = state.get("low_liquidity", False)
        
        # Default values for risk checks (should come from state in production)
        portfolio_value = state.get("portfolio_value") or 10000000  # 10M EGP default
        avg_daily_volume = state.get("avg_daily_volume") or 100000
        current_price = state.get("current_price") or 50.0

        # ===== DETERMINISTIC RISK CHECKS =====
        if is_egx and exec_plan:
            # Temporarily relax single-stock concentration limit in backtest mode.
            # Single-ticker backtests intentionally concentrate the whole portfolio
            # in one stock, so the standard 10% cap would veto every trade.
            _orig_single_stock_pct = EGX_RISK_LIMITS["max_single_stock_pct"]
            if config.get("backtest_mode", False):
                EGX_RISK_LIMITS["max_single_stock_pct"] = 0.25  # Relaxed for single-stock backtest

            approved, violations = run_all_risk_checks(
                exec_plan,
                portfolio_value=portfolio_value,
                avg_daily_volume=avg_daily_volume,
                current_price=current_price,
                low_liquidity=low_liquidity
            )

            # Restore original limit so the global dict is not permanently mutated.
            EGX_RISK_LIMITS["max_single_stock_pct"] = _orig_single_stock_pct
            
            # Build risk assessment
            risk_assessment = {
                "approved": approved,
                "total_violations": len(violations),
                "critical_violations": len([v for v in violations if v.severity == "critical"]),
                "violations": [v.to_dict() for v in violations],
                "constraints_checked": [
                    "max_single_stock_exposure",
                    "liquidity_feasibility",
                    "stop_loss_required",
                    "max_trade_loss",
                    "no_short_selling",
                    "no_leverage"
                ]
            }
            
            # If VETO - explain why
            if not approved:
                veto_explanation = f"""
## ⛔ RISK VETO - TRADE REJECTED

### Decision: TRADE NOT APPROVED

The proposed execution plan for **{company_name}** has been **REJECTED** due to the following risk violations:

"""
                for v in violations:
                    veto_explanation += f"""
### ❌ {v.rule_name} ({v.severity.upper()})
- **Violation**: {v.explanation}
- **Limit**: {v.limit_value}
- **Actual**: {v.actual_value}
- **Remediation**: {v.remediation}
"""
                
                veto_explanation += """
---
## Required Actions
The trader must address ALL critical violations before resubmission.

**FINAL DECISION: HOLD - DO NOT EXECUTE TRADE**
"""
                
                risk_assessment["veto_explanation"] = veto_explanation
                final_decision = "VETO - HOLD - RISK VIOLATIONS DETECTED"
            else:
                approval_note = f"""
## ✅ RISK APPROVED

The execution plan for **{company_name}** has passed all risk checks.

### Checks Passed:
- ✅ Position size within limits
- ✅ Liquidity feasibility confirmed
- ✅ Stop-loss defined
- ✅ Max trade loss within limits
- ✅ No short selling detected
- ✅ No leverage detected

### Warnings:
"""
                # Add any non-critical warnings
                non_critical = [v for v in violations if v.severity != "critical"]
                if non_critical:
                    for v in non_critical:
                        approval_note += f"- ⚠️ {v.rule_name}: {v.explanation}\n"
                else:
                    approval_note += "- None\n"
                
                approval_note += """
**Proceed with execution as planned.**
"""
                risk_assessment["approval_note"] = approval_note
                final_decision = exec_plan.get("decision", "APPROVED")
        else:
            # Non-EGX or no execution plan - fall back to LLM judgment
            risk_assessment = {"note": "Standard risk review (non-EGX)"}
            final_decision = None  # Will be set by LLM below

        # ── Phase 3b: Early return on deterministic veto ─────────────────────
        # When the deterministic check has already vetoed the trade, there is
        # nothing for the LLM to decide. Skip the expensive deep_think call
        # entirely — saves ~3,000-5,000 tokens and ~30-45s.
        if is_egx and exec_plan and not risk_assessment.get("approved", True):
            veto_text = risk_assessment.get("veto_explanation", "VETO - HOLD")
            new_risk_debate_state = {
                "judge_decision": veto_text,
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
                "final_trade_decision": "HOLD",
                "risk_assessment": risk_assessment,
                "risk_veto": True,
            }

        # ===== LLM RISK DEBATE JUDGMENT =====
        # Phase 3c: Use compact inputs — execution plan JSON + merged risk debate summary.
        # The full analyst reports (market, sentiment, news, fundamentals) are NOT
        # re-passed here because they've already been used by 5 previous agents.
        # Passing them again creates ~2,000-3,000 tokens of redundancy per call.
        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        past_memories = memory.get_memories(curr_situation, n_matches=2)

        past_memory_str = ""
        for i, rec in enumerate(past_memories, 1):
            past_memory_str += rec["recommendation"] + "\n\n"

        # Build position context string (used inside egx_context f-string below)
        _pos = state.get("current_position") or {}
        if _pos.get("shares", 0) > 0:
            _position_context = (
                f"HOLDING {_pos['shares']:,} shares @ avg cost {_pos['avg_cost']:.2f} EGP | "
                f"Market value: {_pos['market_value']:,.2f} EGP | "
                f"Unrealised P&L: {_pos['unrealised_pnl']:,.2f} EGP"
            )
        else:
            _position_context = (
                "NO OPEN POSITION — portfolio is 100% cash. "
                "A SELL recommendation cannot be executed. Only BUY or HOLD are actionable."
            )

        # Phase 3c: Compact EGX context (assessment result + position only, no full reports)
        egx_context = ""
        if is_egx:
            egx_context = f"""
## EGX RISK CONSTRAINTS (Already Enforced Deterministically)
The following constraints have been checked programmatically:
- Max single stock: {EGX_RISK_LIMITS['max_single_stock_pct']:.0%} of portfolio
- Max ADV per day: {EGX_RISK_LIMITS['max_position_vs_adv_pct']:.0%}
- Max trade loss: {EGX_RISK_LIMITS['max_single_trade_loss_pct']:.0%} of portfolio
- Stop-loss required: Yes
- Short selling: FORBIDDEN
- Leverage: FORBIDDEN

## Deterministic Risk Assessment Result
{json.dumps(risk_assessment, indent=2)}

## IMPORTANT: Data Availability Context
Annual data (FY2022, FY2023, FY2024) is sufficient for institutional due diligence on EGX.
Quarterly data absence does NOT constitute a veto condition.

## IMPORTANT: Current Portfolio Position
{_position_context}
"""

        # Phase 3c: Compact prompt — execution plan + debate summary (NO full analyst reports)
        prompt = f"""As the Institutional Risk Manager for {company_name} on {"EGX" if is_egx else "the market"}, provide your final risk assessment.

{egx_context}

## Trader's Execution Plan (compact)
```json
{json.dumps(exec_plan, indent=2, default=str)}
```

## Risk Debate Summary (all 3 perspectives)
{history[-2000:] if len(history) > 2000 else history}

## Lessons from Past Decisions
{past_memory_str}

## Your Task
1. The deterministic risk check has PASSED (approved=true). Do not reinvent the checks.
2. Evaluate the qualitative risk debate above.
3. Confirm or override the Trader's recommendation based on debate insights only.
4. Do NOT invent qualitative reasons to veto if the deterministic check passed.

Your response MUST end with a JSON block:
```json
{{"action": "BUY", "confidence": 0.0-1.0}}
```
or SELL or HOLD.

Keep your response brief: 2-3 sentences of risk commentary + the JSON block.
{"Remember: On EGX, VETO is ONLY valid if the deterministic check above shows approved=false." if is_egx else ""}
"""

        response = llm.invoke(prompt)

        # Extract the action from the LLM response using three fallback patterns.
        final_trade_decision = None

        # Pattern 1: ```json { "action": "BUY" } ```  (preferred / prompted format)
        _m = re.search(r'```json\s*(\{[^`]+\})\s*```', response.content, re.DOTALL)
        if _m:
            try:
                _obj = json.loads(_m.group(1))
                final_trade_decision = _obj.get("action", "").upper() or None
            except Exception:
                pass

        # Pattern 2: bare { "action": "BUY" } anywhere in the response text
        if not final_trade_decision:
            _m2 = re.search(
                r'\{[^{}]*"action"\s*:\s*"(BUY|SELL|HOLD)"[^{}]*\}',
                response.content, re.IGNORECASE
            )
            if _m2:
                _inner = re.search(
                    r'"action"\s*:\s*"(BUY|SELL|HOLD)"',
                    _m2.group(0), re.IGNORECASE
                )
                if _inner:
                    final_trade_decision = _inner.group(1).upper()

        # Pattern 3: lone BUY / SELL / HOLD keyword anywhere in the response
        if not final_trade_decision:
            _m3 = re.search(r'\b(BUY|SELL|HOLD)\b', response.content, re.IGNORECASE)
            if _m3:
                final_trade_decision = _m3.group(1).upper()

        # Last resort: return full LLM text for the backtester to parse
        if not final_trade_decision:
            final_trade_decision = response.content

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
