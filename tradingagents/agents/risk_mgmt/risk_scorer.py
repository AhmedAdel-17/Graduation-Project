"""
Deterministic Risk Scorer for EGX Trading System.

This is the FIRST layer of the risk pipeline (runs before any LLM):

    Trader -> Risk Scorer -> (VETO) -> Risk Veto Node -> END
                          -> (ALLOW/WARN/THROTTLE) -> Merged Risk Debate -> Risk Manager -> END

The scorer implements the Alshiekh et al. (AAAI 2018) shielding pattern:
a synthesized shield intercepts hard-rule violations before the LLM policy
is ever consulted.  This matches the architecture mandated by SEC Rule 15c3-5
(pre-trade automated hard controls required) and MiFID II RTS 6 Article 15.

Key improvements over the previous in-manager approach:
- Deterministic checks fire BEFORE the merged debate LLM call (saves tokens on vetoed trades)
- Two-tier liquidity: THROTTLE at 5% ADV, hard VETO at 10% ADV
- ATR-based stop generation (2xATR primary, 5% fallback only)
- Fixed short-selling regex (no false positives on "short_term" fields)
- Max trade loss upgraded to CRITICAL veto (was HIGH warning)
- EGX price band check (regulatory fact, not engineering parameter)
- Structured risk_metrics output for LLM Risk Manager context
- THROTTLE action auto-adjusts max_shares_per_day to 5% ADV

Literature grounding:
- Alshiekh et al. (AAAI 2018): Safe RL via Shielding
- Almgren & Chriss (JOR 2000): continuous market impact => two-tier liquidity
- Bangia et al. (1999): L-VaR exogenous/endogenous decomposition
- Farag (2013, 2015): EGX magnet zone and 1-day reversal pattern
- Kaminski & Lo (JFM 2014): ATR stops add value under momentum/regime-switching
- Wilder (1978): ATR(14) volatility-scaled stop baseline
- Elder (1993, 2014): 2% per-trade hard cap
- Tharp (2008): R-multiple framework

Module layout (R4 split, 2026-06-18):
- ``risk_limits``  — EGX_RISK_LIMITS, EGX_FOREIGN_RESTRICTED, RiskViolation, coercion helpers
- ``risk_checks``  — the eight deterministic ``check_*`` rule evaluators
- ``risk_scorer``  — action classification, throttling, metrics, veto text, and the graph
                     nodes. This module remains the public import surface: every name
                     that used to live here is re-exported below, so existing
                     ``from ...risk_scorer import X`` imports keep working unchanged.
"""

import json
import logging
from typing import Dict, Any, List, Tuple, Optional

from tradingagents.dataflows.config import get_config

# Re-export the constants, data structure, and low-level helpers so external
# callers (risk_manager, egx_validation, tests) keep importing them from here.
from tradingagents.agents.risk_mgmt.risk_limits import (  # noqa: F401
    EGX_RISK_LIMITS,
    EGX_FOREIGN_RESTRICTED,
    RiskViolation,
    _coerce_numeric,
    _normalize_execution_plan,
)

# Re-export the individual deterministic checks (used by tests + run_all_risk_checks).
from tradingagents.agents.risk_mgmt.risk_checks import (  # noqa: F401
    check_position_size_limit,
    check_liquidity_participation,
    check_exit_horizon,
    check_stop_loss_atr,
    check_max_trade_loss,
    check_short_selling_violation,
    check_leverage_violation,
    check_egx_price_band,
)

logger = logging.getLogger("tradingagents.risk_scorer")


# =============================================================================
# Risk Action Classification and Throttling
# =============================================================================

def determine_risk_action(violations: List[RiskViolation]) -> str:
    """
    Map violation list to a single risk action.

    VETO    — any CRITICAL violation present
    THROTTLE — only HIGH violations present AND one of them is LIQUIDITY_THROTTLE_ZONE
               (position can be auto-adjusted and trade can proceed)
    WARN    — only HIGH/MEDIUM/LOW violations (no throttle applicable)
    ALLOW   — no violations
    """
    if not violations:
        return "ALLOW"

    severities = {v.severity for v in violations}
    rules = {v.rule_name for v in violations}

    if "critical" in severities:
        return "VETO"

    if "high" in severities:
        # Only upgrade to THROTTLE if the sole high-severity issue is the throttle zone
        high_violations = [v for v in violations if v.severity == "high"]
        if (
            len(high_violations) == 1
            and high_violations[0].rule_name == "LIQUIDITY_THROTTLE_ZONE"
        ):
            return "THROTTLE"
        return "WARN"

    return "WARN"


def apply_throttle_adjustments(
    execution_plan: dict,
    avg_daily_volume: float,
    low_liquidity: bool,
) -> dict:
    """
    Reduce max_shares_per_day to the 5% ADV throttle threshold.
    Mutates execution_plan in-place and returns an adjustment summary.

    Grounded in Almgren-Chriss: halving participation rate reduces
    temporary market impact by ~30% under square-root impact model.
    """
    effective_adv = avg_daily_volume
    if low_liquidity:
        effective_adv *= EGX_RISK_LIMITS["low_liquidity_reduction"]

    throttle_daily = int(effective_adv * EGX_RISK_LIMITS["throttle_adv_threshold"])

    ps = execution_plan.setdefault("position_sizing", {})
    old_daily = _coerce_numeric(ps.get("max_shares_per_day", 0))
    target_shares = _coerce_numeric(ps.get("target_shares", 0))

    ps["max_shares_per_day"] = throttle_daily
    new_days = (int(target_shares / throttle_daily) + 1) if throttle_daily > 0 else 999
    ps["execution_days"] = new_days
    ps["throttle_applied"] = True

    adjustment = {
        "throttle_applied": True,
        "old_max_shares_per_day": old_daily,
        "new_max_shares_per_day": throttle_daily,
        "new_execution_days": new_days,
        "effective_adv": effective_adv,
        "throttle_threshold_pct": EGX_RISK_LIMITS["throttle_adv_threshold"],
    }
    logger.info(
        "[RiskScorer] THROTTLE applied: max_shares_per_day %s → %s (5%% of %s ADV), execution_days → %s",
        f"{old_daily:,.0f}", f"{throttle_daily:,}", f"{effective_adv:,.0f}", new_days,
    )
    return adjustment


# =============================================================================
# Risk Metrics Computation
# =============================================================================

def compute_risk_metrics(
    execution_plan: dict,
    portfolio_value: float,
    avg_daily_volume: float,
    current_price: float,
    low_liquidity: bool,
    technical_analysis: dict,
) -> dict:
    """
    Compute structured risk metrics for the execution plan.
    These are passed to the LLM Risk Manager as factual context so the
    LLM evaluates pre-computed numbers rather than guessing them.
    """
    metrics: dict = {}

    ps = execution_plan.get("position_sizing") or {}
    exit_logic = execution_plan.get("exit_logic") or {}
    entry_logic = execution_plan.get("entry_logic") or {}

    # 1. Position concentration
    alloc_str = ps.get("portfolio_allocation", "0%")
    try:
        metrics["position_pct"] = (
            float(str(alloc_str).replace("%", "")) / 100
            if "%" in str(alloc_str)
            else float(alloc_str)
        )
    except (ValueError, TypeError):
        metrics["position_pct"] = 0.0

    # 2. ADV participation rate
    effective_adv = avg_daily_volume * (
        EGX_RISK_LIMITS["low_liquidity_reduction"] if low_liquidity else 1.0
    )
    max_daily = _coerce_numeric(ps.get("max_shares_per_day", 0))
    metrics["adv_participation_pct"] = (
        round(max_daily / effective_adv, 4) if effective_adv > 0 else 0.0
    )

    # 3. Days to exit
    target_shares = _coerce_numeric(ps.get("target_shares", 0))
    daily_exit = effective_adv * EGX_RISK_LIMITS["max_position_vs_adv_pct"]
    metrics["days_to_exit"] = (
        round(target_shares / daily_exit, 1) if daily_exit > 0 else 999.0
    )

    # 4. Per-trade loss
    stop_price = _coerce_numeric((exit_logic.get("stop_loss") or {}).get("price", 0))
    if stop_price > 0 and current_price > 0 and target_shares > 0 and portfolio_value > 0:
        loss_per_share = abs(current_price - stop_price)
        metrics["per_trade_loss_pct"] = round(
            (loss_per_share * target_shares) / portfolio_value, 4
        )
    else:
        metrics["per_trade_loss_pct"] = 0.0

    # 5. Stop distance
    if stop_price > 0 and current_price > 0:
        metrics["stop_distance_pct"] = round(
            abs(current_price - stop_price) / current_price, 4
        )
    else:
        metrics["stop_distance_pct"] = 0.0

    # 6. ATR availability and derived stop distance
    atr_val: Optional[float] = None
    if isinstance(technical_analysis, dict):
        raw = (
            technical_analysis.get("atr_14")
            or technical_analysis.get("atr")
            or 0
        )
        v = _coerce_numeric(raw)
        if v > 0:
            atr_val = v

    metrics["atr_14_available"] = atr_val is not None
    if atr_val and current_price > 0:
        metrics["atr_14"] = atr_val
        metrics["atr_stop_distance_pct"] = round(
            min(
                EGX_RISK_LIMITS["atr_stop_multiplier"] * atr_val / current_price,
                EGX_RISK_LIMITS["atr_stop_max_pct"],
            ),
            4,
        )

    # 7. Price band proximity
    limit_price = _coerce_numeric(
        (entry_logic.get("entry_zone") or {}).get("limit_price", 0)
    )
    if limit_price > 0 and current_price > 0:
        daily_limit = EGX_RISK_LIMITS["daily_price_limit"]
        upper_band = current_price * (1 + daily_limit)
        lower_band = current_price * (1 - daily_limit)
        metrics["price_band_proximity_pct"] = round(
            min(
                abs(limit_price - upper_band) / current_price,
                abs(limit_price - lower_band) / current_price,
            ),
            4,
        )

    return metrics


# =============================================================================
# Veto Explanation Builder
# =============================================================================

def _build_veto_explanation(
    violations: List[RiskViolation],
    company_name: str,
) -> str:
    critical = [v for v in violations if v.severity == "critical"]
    others = [v for v in violations if v.severity != "critical"]

    lines = [
        f"## ⛔ RISK VETO — TRADE REJECTED",
        f"",
        f"The execution plan for **{company_name}** was rejected by the Deterministic Risk Scorer.",
        f"The following critical violations were detected BEFORE any LLM was consulted:",
        f"",
    ]
    for v in critical:
        lines += [
            f"### ❌ {v.rule_name} (CRITICAL)",
            f"- **Violation**: {v.explanation}",
            f"- **Limit**: {v.limit_value}",
            f"- **Actual**: {v.actual_value}",
            f"- **Remediation**: {v.remediation}",
            f"",
        ]
    if others:
        lines.append("### Additional warnings (non-critical):")
        for v in others:
            lines.append(f"- ⚠️ {v.rule_name} ({v.severity}): {v.explanation}")
        lines.append("")

    lines += [
        "---",
        "**FINAL DECISION: HOLD — DO NOT EXECUTE TRADE**",
        "Address ALL critical violations before resubmission.",
    ]
    return "\n".join(lines)


# =============================================================================
# Risk Scorer Node Factory
# =============================================================================

def create_risk_scorer_node():
    """
    Factory for the Deterministic Risk Scorer graph node.

    Returns a node function that:
      1. Runs all deterministic checks
      2. Classifies outcome as ALLOW / WARN / THROTTLE / VETO
      3. Auto-adjusts execution_plan if THROTTLE
      4. Computes risk_metrics for LLM context
      5. Writes risk_action, risk_assessment, risk_metrics, execution_plan to state
    """

    def risk_scorer_node(state: dict) -> dict:
        config = get_config()
        target_market = config.get("target_market", "US")
        is_egx = target_market == "EGX"

        # Extract execution plan
        execution_plan_raw = state.get("execution_plan") or {}
        if isinstance(execution_plan_raw, dict):
            exec_plan = execution_plan_raw.get("execution_plan", execution_plan_raw)
        else:
            exec_plan = {}

        # Non-EGX passthrough — no deterministic checks
        if not is_egx or not exec_plan:
            return {
                "risk_action": "ALLOW",
                "risk_assessment": {
                    "note": "Non-EGX market or missing execution plan — deterministic scorer skipped"
                },
                "risk_metrics": {},
            }

        # Portfolio context
        portfolio_value = state.get("portfolio_value") or 10_000_000
        avg_daily_volume = state.get("avg_daily_volume") or 100_000
        current_price = state.get("current_price") or 50.0
        low_liquidity = state.get("low_liquidity", False)
        technical_analysis = state.get("technical_analysis") or {}

        # Backtest mode: relax single-stock concentration limit
        _orig_pct = EGX_RISK_LIMITS["max_single_stock_pct"]
        if config.get("backtest_mode", False):
            EGX_RISK_LIMITS["max_single_stock_pct"] = 0.25

        _normalize_execution_plan(exec_plan)
        _decision = (exec_plan.get("decision") or "").strip().upper()

        # ── Run all deterministic checks ──────────────────────────────────────
        violations: List[RiskViolation] = []
        for result in [
            check_position_size_limit(exec_plan, portfolio_value),
            check_liquidity_participation(exec_plan, avg_daily_volume, low_liquidity),
            check_exit_horizon(exec_plan, avg_daily_volume, low_liquidity),
            check_stop_loss_atr(exec_plan, _decision, current_price, technical_analysis),
            check_max_trade_loss(exec_plan, portfolio_value, current_price),
            check_short_selling_violation(exec_plan),
            check_leverage_violation(exec_plan),
            check_egx_price_band(exec_plan, current_price),
        ]:
            if result is not None:
                violations.append(result)

        # Restore original limit
        EGX_RISK_LIMITS["max_single_stock_pct"] = _orig_pct

        # ── Classify action ───────────────────────────────────────────────────
        risk_action = determine_risk_action(violations)

        # ── Apply throttle if needed ──────────────────────────────────────────
        throttle_adjustments: dict = {}
        if risk_action == "THROTTLE":
            throttle_adjustments = apply_throttle_adjustments(
                exec_plan, avg_daily_volume, low_liquidity
            )

        # ── Compute structured metrics ────────────────────────────────────────
        risk_metrics = compute_risk_metrics(
            exec_plan, portfolio_value, avg_daily_volume,
            current_price, low_liquidity, technical_analysis,
        )

        # ── Build risk assessment dict ────────────────────────────────────────
        critical_violations = [v for v in violations if v.severity == "critical"]
        non_critical = [v for v in violations if v.severity != "critical"]
        risk_assessment = {
            "approved": risk_action != "VETO",
            "risk_action": risk_action,
            "total_violations": len(violations),
            "critical_violations": len(critical_violations),
            # Named fields for downstream consumers and logging
            "hard_violations": [v.to_dict() for v in critical_violations],
            "warnings": [v.to_dict() for v in non_critical],
            # Full list (kept for backward compatibility)
            "violations": [v.to_dict() for v in violations],
            "throttle_adjustments": throttle_adjustments,
            "risk_metrics": risk_metrics,
            "constraints_checked": [
                "position_size_limit",
                "liquidity_participation",
                "exit_horizon",
                "stop_loss_atr",
                "max_trade_loss",
                "short_selling",
                "leverage",
                "egx_price_band",
            ],
        }

        if risk_action == "VETO":
            risk_assessment["veto_explanation"] = _build_veto_explanation(
                violations, state.get("company_of_interest", "")
            )

        # Write back potentially-mutated execution plan
        if isinstance(execution_plan_raw, dict) and "execution_plan" in execution_plan_raw:
            updated_exec_plan = {**execution_plan_raw, "execution_plan": exec_plan}
        else:
            updated_exec_plan = exec_plan

        return {
            "risk_action": risk_action,
            "risk_assessment": risk_assessment,
            "risk_metrics": risk_metrics,
            "execution_plan": updated_exec_plan,
        }

    return risk_scorer_node


# =============================================================================
# Risk Veto Node (standalone — no LLM, no factory)
# =============================================================================

def risk_veto_node(state: dict) -> dict:
    """
    Called when Risk Scorer issues a VETO.
    Returns HOLD without invoking any LLM.

    This is the Alshiekh (2018) shield: hard-rule violations are intercepted
    before the learned policy (merged debate + LLM risk manager) is consulted.
    Saves the token cost of the merged debate call (~2,000–4,000 tokens).
    """
    risk_assessment = state.get("risk_assessment") or {}
    veto_text = risk_assessment.get(
        "veto_explanation",
        "RISK VETO: Trade rejected by deterministic checks.",
    )

    existing_rds = state.get("risk_debate_state") or {}
    new_risk_debate_state = {
        "risky_history": existing_rds.get("risky_history", ""),
        "safe_history": existing_rds.get("safe_history", ""),
        "neutral_history": existing_rds.get("neutral_history", ""),
        "history": existing_rds.get("history", ""),
        "latest_speaker": "Risk_Scorer",
        "current_risky_response": existing_rds.get("current_risky_response", ""),
        "current_safe_response": existing_rds.get("current_safe_response", ""),
        "current_neutral_response": existing_rds.get("current_neutral_response", ""),
        "judge_decision": veto_text,
        "count": existing_rds.get("count", 0),
    }

    return {
        "risk_debate_state": new_risk_debate_state,
        "final_trade_decision": "HOLD",
        "risk_veto": True,
    }


# =============================================================================
# Convenience runner (backward-compatibility entry point)
# =============================================================================

def run_all_risk_checks(
    execution_plan: dict,
    portfolio_value: float,
    avg_daily_volume: float,
    current_price: float,
    low_liquidity: bool = False,
    technical_analysis: dict | None = None,
) -> tuple[bool, List[RiskViolation]]:
    """Run all deterministic EGX risk checks and return (approved, violations).

    ``approved`` is True when there are no critical violations.
    This function is the programmatic entry point used by tests and scripts;
    the graph uses ``create_risk_scorer_node()`` instead.
    """
    _normalize_execution_plan(execution_plan)
    _decision = (execution_plan.get("decision") or "").strip().upper()

    violations: List[RiskViolation] = []
    for result in [
        check_position_size_limit(execution_plan, portfolio_value),
        check_liquidity_participation(execution_plan, avg_daily_volume, low_liquidity),
        check_exit_horizon(execution_plan, avg_daily_volume, low_liquidity),
        check_stop_loss_atr(
            execution_plan, _decision, current_price, technical_analysis or {}
        ),
        check_max_trade_loss(execution_plan, portfolio_value, current_price),
        check_short_selling_violation(execution_plan),
        check_leverage_violation(execution_plan),
        check_egx_price_band(execution_plan, current_price),
    ]:
        if result is not None:
            violations.append(result)

    critical = [v for v in violations if v.severity == "critical"]
    approved = len(critical) == 0
    return approved, violations
