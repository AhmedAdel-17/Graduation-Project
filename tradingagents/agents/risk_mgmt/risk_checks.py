"""
Individual deterministic EGX risk-rule evaluators.

Extracted verbatim from the former monolithic ``risk_scorer.py`` (R4 split,
2026-06-18). Each ``check_*`` function inspects an execution plan + portfolio
context and returns a :class:`RiskViolation` or ``None``. They read the shared
``EGX_RISK_LIMITS`` object, so the backtest-mode tweak applied by the scorer node
is honoured here exactly as before.

The logger name is kept as ``tradingagents.risk_scorer`` so log output is identical
to the pre-split module.
"""

import json
import logging
import re
from typing import Optional

from tradingagents.agents.risk_mgmt.risk_limits import (
    EGX_RISK_LIMITS,
    RiskViolation,
    _coerce_numeric,
)

logger = logging.getLogger("tradingagents.risk_scorer")


def check_position_size_limit(
    execution_plan: dict,
    portfolio_value: float,
) -> Optional[RiskViolation]:
    """
    Check single-stock concentration limit.
    Grounded in UCITS Art. 52(2) (10% per issuer) and ICA §5(b)(1).
    Skipped in backtest_mode (caller is responsible for setting the relaxed limit).
    """
    position_sizing = execution_plan.get("position_sizing", {})
    portfolio_allocation = position_sizing.get("portfolio_allocation", "0%")

    try:
        if isinstance(portfolio_allocation, str):
            alloc_pct = float(portfolio_allocation.replace("%", "")) / 100
        else:
            alloc_pct = float(portfolio_allocation)
    except (ValueError, TypeError):
        alloc_pct = 0.0

    max_allowed = EGX_RISK_LIMITS["max_single_stock_pct"]

    if alloc_pct > 0.50:
        severity = "critical"
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
        explanation=f"Proposed allocation {alloc_pct:.1%} exceeds single-stock limit {max_allowed:.1%}",
        remediation=f"Reduce position to ≤{max_allowed:.1%} of portfolio",
    )


def check_liquidity_participation(
    execution_plan: dict,
    avg_daily_volume: float,
    low_liquidity: bool,
) -> Optional[RiskViolation]:
    """
    Two-tier daily participation check (Almgren-Chriss 2000; Bangia et al. 1999).

    Tier 1 — THROTTLE (HIGH):  5% < participation ≤ 10% ADV
      Position auto-adjusted to 5% ADV by apply_throttle_adjustments().

    Tier 2 — VETO (CRITICAL): participation > 10% ADV
      Even after throttle, total position cannot be executed without excessive impact.

    FINRA Rule 5310 (best execution) is satisfied: we prefer throttle over binary veto
    when the order can be re-sized within the limit. Hard veto only when ADV data is
    missing or the threshold is exceeded even at minimum viable position.
    """
    if avg_daily_volume <= 0:
        return RiskViolation(
            rule_name="LIQUIDITY_DATA_MISSING",
            severity="critical",
            limit_value=EGX_RISK_LIMITS["min_avg_daily_volume"],
            actual_value=0,
            explanation="Cannot assess liquidity — average daily volume data is missing or zero",
            remediation="Obtain ADV data before proceeding. Trade cannot be sized without it.",
        )

    effective_adv = avg_daily_volume
    if low_liquidity:
        effective_adv *= EGX_RISK_LIMITS["low_liquidity_reduction"]

    if avg_daily_volume < EGX_RISK_LIMITS["min_avg_daily_volume"]:
        return RiskViolation(
            rule_name="INSUFFICIENT_LIQUIDITY",
            severity="high",
            limit_value=EGX_RISK_LIMITS["min_avg_daily_volume"],
            actual_value=avg_daily_volume,
            explanation=f"ADV {avg_daily_volume:,.0f} shares below minimum {EGX_RISK_LIMITS['min_avg_daily_volume']:,.0f}",
            remediation="This stock is too illiquid for the current position size. Consider a much smaller entry.",
        )

    position_sizing = execution_plan.get("position_sizing") or {}
    max_shares_per_day = _coerce_numeric(position_sizing.get("max_shares_per_day", 0))
    target_shares = _coerce_numeric(position_sizing.get("target_shares", 0))

    # Estimate daily entry if max_shares_per_day not set
    if max_shares_per_day <= 0 and target_shares > 0:
        max_shares_per_day = target_shares / 5  # rough 5-day default

    participation_pct = max_shares_per_day / effective_adv if effective_adv > 0 else 0.0

    veto_threshold = EGX_RISK_LIMITS["max_position_vs_adv_pct"]      # 10%
    throttle_threshold = EGX_RISK_LIMITS["throttle_adv_threshold"]    # 5%

    if participation_pct > veto_threshold:
        return RiskViolation(
            rule_name="LIQUIDITY_PARTICIPATION_EXCEEDED",
            severity="critical",
            limit_value=veto_threshold,
            actual_value=round(participation_pct, 4),
            explanation=(
                f"Daily entry of {max_shares_per_day:,.0f} shares = {participation_pct:.1%} of ADV "
                f"({avg_daily_volume:,.0f}), exceeding the {veto_threshold:.0%} hard limit. "
                "Market impact would be prohibitive even after throttling."
            ),
            remediation=(
                f"Reduce total position or split across more days. "
                f"Max daily entry at 5% ADV = {int(effective_adv * throttle_threshold):,} shares."
            ),
        )

    if participation_pct > throttle_threshold:
        return RiskViolation(
            rule_name="LIQUIDITY_THROTTLE_ZONE",
            severity="high",
            limit_value=throttle_threshold,
            actual_value=round(participation_pct, 4),
            explanation=(
                f"Daily entry of {max_shares_per_day:,.0f} shares = {participation_pct:.1%} of ADV, "
                f"in the throttle zone ({throttle_threshold:.0%}–{veto_threshold:.0%}). "
                "Position will be auto-adjusted to 5% ADV."
            ),
            remediation=(
                f"Auto-throttled to {int(effective_adv * throttle_threshold):,} shares/day. "
                "Execution spread across more days at reduced market impact."
            ),
        )

    return None


def check_exit_horizon(
    execution_plan: dict,
    avg_daily_volume: float,
    low_liquidity: bool,
) -> Optional[RiskViolation]:
    """
    Check whether the total position can be exited within 10 days at 10% ADV.
    Grounded in Almgren-Chriss 'characteristic time' and FRTB liquidity horizons.
    """
    if avg_daily_volume <= 0:
        return None  # Already flagged by check_liquidity_participation

    effective_adv = avg_daily_volume
    if low_liquidity:
        effective_adv *= EGX_RISK_LIMITS["low_liquidity_reduction"]

    position_sizing = execution_plan.get("position_sizing") or {}
    target_shares = _coerce_numeric(position_sizing.get("target_shares", 0))

    daily_exit_capacity = effective_adv * EGX_RISK_LIMITS["max_position_vs_adv_pct"]
    days_to_exit = target_shares / daily_exit_capacity if daily_exit_capacity > 0 else 999

    max_days = EGX_RISK_LIMITS["max_days_to_exit"]
    if days_to_exit > max_days:
        return RiskViolation(
            rule_name="EXIT_HORIZON_EXCEEDED",
            severity="high",
            limit_value=max_days,
            actual_value=round(days_to_exit, 1),
            explanation=(
                f"Position of {target_shares:,.0f} shares requires {days_to_exit:.1f} days to exit "
                f"at 10% ADV (limit: {max_days} days). Illiquidity risk is elevated for EGX."
            ),
            remediation=(
                f"Reduce total position to ≤{int(daily_exit_capacity * max_days):,} shares "
                f"to stay within the {max_days}-day exit horizon."
            ),
        )

    return None


def check_stop_loss_atr(
    execution_plan: dict,
    decision: str,
    current_price: float,
    technical_analysis: dict,
) -> Optional[RiskViolation]:
    """
    Check stop-loss exists. If missing, auto-generate using ATR when available.

    Stop generation priority (Wilder 1978; Kaminski & Lo 2014):
      1. ATR from technical_analysis dict (field: 'atr_14' or 'atr')
      2. ATR from execution_plan risk_controls (Trader-computed)
      3. Fixed 5% fallback — documented explicitly, not silently applied

    EGX constraint (Farag 2013): stop must sit INSIDE the magnet zone.
    Cap stop distance at 7% so it does not coincide with the ±10% band.
    """
    normalized_decision = (decision or "").strip().upper()
    if normalized_decision == "HOLD":
        return None

    exit_logic = execution_plan.setdefault("exit_logic", {})
    stop_loss = exit_logic.get("stop_loss", {})

    if stop_loss and _coerce_numeric(stop_loss.get("price", 0)) > 0:
        # Validate existing stop-loss is within EGX-reasonable range.
        # LLMs sometimes hallucinate stops 30-40% away from current price,
        # which is physically unreachable on EGX (±10% daily limit).
        existing_stop = _coerce_numeric(stop_loss.get("price", 0))
        max_stop_pct = EGX_RISK_LIMITS.get("atr_stop_max_pct", 0.07)
        if current_price and current_price > 0 and existing_stop > 0:
            stop_distance = abs(existing_stop - current_price) / current_price
            if stop_distance > max_stop_pct:
                # Auto-correct: clamp the stop to max allowed distance
                if existing_stop < current_price:  # BUY stop
                    corrected = round(current_price * (1 - max_stop_pct), 2)
                else:  # SELL stop
                    corrected = round(current_price * (1 + max_stop_pct), 2)
                logger.warning(
                    "[RiskScorer] Stop-loss %.2f EGP is %.1f%% from price %.2f "
                    "(max %.0f%%). Clamping to %.2f EGP.",
                    existing_stop, stop_distance * 100, current_price,
                    max_stop_pct * 100, corrected,
                )
                exit_logic["stop_loss"]["price"] = corrected
                exit_logic["stop_loss"]["note"] = (
                    f"Original stop {existing_stop:.2f} EGP was {stop_distance:.1%} "
                    f"from price — clamped to {max_stop_pct:.0%} (EGX magnet-zone cap)."
                )
                exit_logic["stop_loss"]["auto_corrected"] = True
        return None  # Stop defined (possibly corrected) — no violation

    if not current_price or current_price <= 0:
        return RiskViolation(
            rule_name="STOP_LOSS_MISSING",
            severity="medium",
            limit_value=1,
            actual_value=0,
            explanation="Stop-loss missing and current_price unavailable for auto-generation",
            remediation="Define an explicit stop-loss price in the execution plan",
        )

    # Attempt ATR lookup
    atr_value: Optional[float] = None
    atr_source = "none"

    if isinstance(technical_analysis, dict):
        raw = (
            technical_analysis.get("atr_14")
            or technical_analysis.get("atr")
            or 0
        )
        v = _coerce_numeric(raw)
        if v > 0:
            atr_value = v
            atr_source = "technical_analysis"

    if not atr_value:
        risk_controls = execution_plan.get("risk_controls") or {}
        raw = risk_controls.get("atr_14") or risk_controls.get("atr") or 0
        v = _coerce_numeric(raw)
        if v > 0:
            atr_value = v
            atr_source = "execution_plan.risk_controls"

    multiplier = EGX_RISK_LIMITS["atr_stop_multiplier"]   # 2.0
    max_stop_pct = EGX_RISK_LIMITS["atr_stop_max_pct"]    # 0.07 (inside magnet zone)
    fallback_pct = EGX_RISK_LIMITS["fallback_stop_pct"]    # 0.05

    if atr_value and atr_value > 0:
        stop_distance_pct = min(multiplier * atr_value / current_price, max_stop_pct)
        atr_based = True
        note = (
            f"ATR-based stop: {multiplier}×ATR({atr_value:.3f}) = {stop_distance_pct:.1%} "
            f"[source: {atr_source}; capped at {max_stop_pct:.0%} EGX magnet-zone limit]"
        )
    else:
        stop_distance_pct = fallback_pct
        atr_based = False
        note = (
            f"Fixed {fallback_pct:.0%} fallback stop (ATR unavailable). "
            "Add 'atr_14' to technical_analysis for volatility-scaled stops."
        )

    if normalized_decision == "SELL":
        stop_price = round(current_price * (1 + stop_distance_pct), 2)
    else:  # BUY
        stop_price = round(current_price * (1 - stop_distance_pct), 2)

    exit_logic["stop_loss"] = {
        "price": stop_price,
        "note": note,
        "auto_generated": True,
        "atr_based": atr_based,
    }
    execution_plan["exit_logic"] = exit_logic

    logger.info(
        "[RiskScorer] Auto-generated stop-loss at %.2f EGP (%s)", stop_price, note
    )
    return None  # No violation — stop generated


def check_max_trade_loss(
    execution_plan: dict,
    portfolio_value: float,
    current_price: float,
) -> Optional[RiskViolation]:
    """
    Check potential loss against 2% per-trade hard cap (Elder 1993; Tharp 2008).

    Upgraded to CRITICAL (was HIGH in the previous design).
    The 2% rule is the practitioner consensus hard cap — Elder, Tharp, and
    fractional Kelly all converge on it. Treating it as a warning only makes
    it defeatable by the LLM, which is indefensible at a thesis defense.
    """
    exit_logic = execution_plan.get("exit_logic", {})
    stop_loss = exit_logic.get("stop_loss", {})
    position_sizing = execution_plan.get("position_sizing", {})

    stop_price = _coerce_numeric(stop_loss.get("price", 0))
    target_shares = _coerce_numeric(position_sizing.get("target_shares", 0))

    if stop_price <= 0 or (current_price or 0) <= 0 or target_shares <= 0:
        return None

    loss_per_share = abs(current_price - stop_price)
    total_loss = loss_per_share * target_shares
    loss_pct = total_loss / portfolio_value if portfolio_value > 0 else 0.0

    max_loss_pct = EGX_RISK_LIMITS["max_single_trade_loss_pct"]

    if loss_pct > max_loss_pct:
        return RiskViolation(
            rule_name="MAX_TRADE_LOSS_EXCEEDED",
            severity="critical",  # CRITICAL — not HIGH (Elder 2% rule is a hard cap)
            limit_value=max_loss_pct,
            actual_value=round(loss_pct, 4),
            explanation=(
                f"Potential loss {loss_pct:.2%} of portfolio exceeds the {max_loss_pct:.0%} "
                "per-trade hard cap (Elder 1993; Tharp 2008). "
                "Reduce position size or tighten stop-loss."
            ),
            remediation=(
                f"Max target_shares at this stop = "
                f"{int(portfolio_value * max_loss_pct / loss_per_share):,}. "
                "Alternatively, move stop closer to entry."
            ),
        )

    return None


def check_short_selling_violation(execution_plan: dict) -> Optional[RiskViolation]:
    """
    Detect short-selling language in the execution plan.

    Fixed: previous code matched the substring 'short' which false-positived
    on 'short_term', 'short_horizon', 'short-term investment', etc.
    Now uses word-boundary regex patterns targeting only actual short-selling intent.
    """
    if not EGX_RISK_LIMITS["no_short_selling"]:
        return None

    plan_text = json.dumps(execution_plan).lower()

    # Word-boundary patterns — only match actual short-selling language.
    # Avoids false positives on "short-term", "short_term", "short_horizon".
    # The last pattern catches "short COMI.CA 1000" (verb + ticker or quantity).
    short_sell_patterns = [
        r"\bshort[\s_-]sell(ing)?\b",
        r"\bsell[\s_-]short\b",
        r"\bnaked[\s_-]short\b",
        r"\bshort[\s_-]position\b",
        r"\bgo[\s_-]short\b",
        r"\bopen[\s_-]short\b",
        r"\bshort\s+(?:\w+\.ca|\d+)",  # "short comi.ca 1000" (plan_text is lowercased)
    ]

    for pattern in short_sell_patterns:
        if re.search(pattern, plan_text):
            return RiskViolation(
                rule_name="SHORT_SELLING_FORBIDDEN",
                severity="critical",
                limit_value=0,
                actual_value=1,
                explanation=f"Short selling is forbidden on EGX. Pattern matched: '{pattern}'",
                remediation="Use AVOID/REDUCE/HOLD for bearish views. EGX is long-only.",
            )

    return None


def check_leverage_violation(execution_plan: dict) -> Optional[RiskViolation]:
    """
    Detect leverage/margin language in the execution plan.

    Fixed: previous code matched the bare substring 'margin' which false-positived
    on 'margin of safety', 'net interest margin', 'profit margin', etc.
    Now uses word-boundary regex targeting only actual leverage/margin-trading intent.
    """
    if not EGX_RISK_LIMITS["no_leverage"]:
        return None

    plan_text = json.dumps(execution_plan).lower()

    # Word-boundary patterns — only match actual leverage/margin-trading language.
    # Avoids false positives on "margin of safety", "profit margin", "net margin",
    # "net interest margin", "EBITDA margin".
    leverage_patterns = [
        r"\bmargin[\s_-]?trad(e|ing)\b",          # margin trading
        r"\bmargin[\s_-]?account\b",                # margin account
        r"\b(on|use|using|with)[\s_-]?margin\b",   # on/use/using margin
        r"\bmargin\s+to\s+(increase|boost|add)\b",  # margin to increase position
        r"\b\d+x\s*leverag",                        # 2x leverage, 3x leveraged
        r"(?<!\d\.)\b[23]x\b",                      # standalone 2x, 3x (not 1.2x, 0.3x)
        r"\bleverag(e|ed|ing)\b",                   # leverage, leveraged, leveraging
        r"\bborrowed\s+(funds?|capital)\b",          # borrowed funds/capital
    ]

    for pattern in leverage_patterns:
        if re.search(pattern, plan_text):
            return RiskViolation(
                rule_name="LEVERAGE_FORBIDDEN",
                severity="critical",
                limit_value=1.0,
                actual_value=2.0,
                explanation=f"Leverage is forbidden on EGX. Pattern matched: '{pattern}'",
                remediation="Use 100% cash positions only. No margin or leveraged instruments.",
            )

    return None


def check_egx_price_band(
    execution_plan: dict,
    current_price: float,
) -> Optional[RiskViolation]:
    """
    Validate the order's limit price against the EGX daily price band.

    EGX List B (default): ±10% daily limit, halt at ±5%.
    EGX List A:           ±20% daily limit, halt at ±10%.
    We use ±10% as the conservative default since most active EGX stocks are List B.
    Primary EGX source: egx.com.eg Trading Rules — verify for specific ticker.

    Magnet zone (Farag 2013, 2015): when price approaches the band limit,
    adverse selection and forced unwinding increase sharply.  Warn when within
    1.5% of either band boundary.
    """
    if not current_price or current_price <= 0:
        return None

    entry_logic = execution_plan.get("entry_logic") or {}
    entry_zone = entry_logic.get("entry_zone") or {}
    limit_price = _coerce_numeric(entry_zone.get("limit_price", 0))

    if limit_price <= 0:
        return None  # No explicit limit price to check

    daily_limit = EGX_RISK_LIMITS["daily_price_limit"]   # 0.10
    magnet_zone = EGX_RISK_LIMITS["magnet_zone_pct"]      # 0.015

    upper_band = current_price * (1 + daily_limit)
    lower_band = current_price * (1 - daily_limit)

    decision = (execution_plan.get("decision") or "").strip().upper()

    # Hard veto: order price outside the band (would be rejected by EGX)
    if limit_price > upper_band:
        return RiskViolation(
            rule_name="PRICE_OUTSIDE_EGX_BAND",
            severity="critical",
            limit_value=round(upper_band, 2),
            actual_value=round(limit_price, 2),
            explanation=(
                f"Limit price {limit_price:.2f} EGP exceeds EGX upper band "
                f"{upper_band:.2f} EGP (+{daily_limit:.0%}). Order would be rejected by exchange."
            ),
            remediation=f"Set limit price at or below {upper_band:.2f} EGP.",
        )

    if limit_price < lower_band:
        return RiskViolation(
            rule_name="PRICE_OUTSIDE_EGX_BAND",
            severity="critical",
            limit_value=round(lower_band, 2),
            actual_value=round(limit_price, 2),
            explanation=(
                f"Limit price {limit_price:.2f} EGP below EGX lower band "
                f"{lower_band:.2f} EGP (-{daily_limit:.0%}). Order would be rejected by exchange."
            ),
            remediation=f"Set limit price at or above {lower_band:.2f} EGP.",
        )

    # Magnet-zone warning: within 1.5% of band (Farag 2013 adverse selection)
    upper_magnet_threshold = current_price * (1 + daily_limit - magnet_zone)
    lower_magnet_threshold = current_price * (1 - daily_limit + magnet_zone)

    if decision == "BUY" and limit_price > upper_magnet_threshold:
        proximity = (limit_price - current_price) / current_price
        return RiskViolation(
            rule_name="PRICE_IN_EGX_MAGNET_ZONE",
            severity="high",
            limit_value=round(upper_magnet_threshold, 2),
            actual_value=round(limit_price, 2),
            explanation=(
                f"BUY limit {limit_price:.2f} EGP is within the upper magnet zone "
                f"({magnet_zone:.1%} from +{daily_limit:.0%} band). "
                "Adverse selection risk elevated per Farag (2013)."
            ),
            remediation="Move entry price away from the upper band to avoid magnet-zone adverse selection.",
        )

    if decision == "SELL" and limit_price < lower_magnet_threshold:
        return RiskViolation(
            rule_name="PRICE_IN_EGX_MAGNET_ZONE",
            severity="high",
            limit_value=round(lower_magnet_threshold, 2),
            actual_value=round(limit_price, 2),
            explanation=(
                f"SELL limit {limit_price:.2f} EGP is within the lower magnet zone "
                f"({magnet_zone:.1%} from -{daily_limit:.0%} band). "
                "1-day reversal risk elevated — do not unwind into limit-down (Farag 2013, 2015)."
            ),
            remediation=(
                "Consider waiting for the reversal before exiting. "
                "Move sell price above the lower magnet threshold."
            ),
        )

    return None
