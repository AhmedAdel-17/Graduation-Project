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
"""

import json
import re
from typing import Dict, Any, List, Tuple, Optional
from tradingagents.dataflows.config import get_config


# =============================================================================
# EGX Risk Limits
# =============================================================================

EGX_RISK_LIMITS = {
    # Position limits
    "max_single_stock_pct": 0.10,          # UCITS Art. 52(2) / ICA §5(b)(1)
    "max_sector_pct": 0.30,
    "max_correlated_exposure": 0.40,       # Analogous to UCITS 5/10/40 aggregate

    # Liquidity thresholds (two-tier per Almgren-Chriss + Bangia et al.)
    "throttle_adv_threshold": 0.05,        # Warn+throttle at 5% ADV participation
    "max_position_vs_adv_pct": 0.10,       # Hard VETO at 10% ADV participation
    "min_avg_daily_volume": 50_000,        # Minimum ADV floor (shares)
    "max_days_to_exit": 10,               # Almgren-Chriss "characteristic time"
    "low_liquidity_reduction": 0.50,       # Halve capacity for low-liq stocks

    # Drawdown limits (Elder 2% rule; Tharp R-multiple)
    "max_single_trade_loss_pct": 0.02,     # CRITICAL — 2% per-trade hard cap
    "max_daily_loss_pct": 0.05,
    "max_weekly_loss_pct": 0.10,
    "stop_loss_required": True,

    # EGX market structure
    "daily_price_limit": 0.10,            # Default ±10% (List B). List A uses ±20%.
    "magnet_zone_pct": 0.015,             # 1.5% inside band => magnet zone (Farag 2013)
    "no_short_selling": True,
    "no_leverage": True,

    # ATR stop parameters (Wilder 1978; Kaminski & Lo 2014)
    "atr_stop_multiplier": 2.0,           # 2xATR(14) primary stop distance
    "atr_stop_max_pct": 0.07,             # Cap at 7% — inside EGX magnet zone
    "fallback_stop_pct": 0.05,            # Fixed 5% only when ATR unavailable
}

# EGX tickers with foreign ownership restrictions
EGX_FOREIGN_RESTRICTED = {"SCEM", "SDTI"}


# =============================================================================
# Data Structures
# =============================================================================

class RiskViolation:
    """Represents a single risk rule violation with severity and remediation."""

    def __init__(
        self,
        rule_name: str,
        severity: str,          # "critical" | "high" | "medium" | "low"
        limit_value: float,
        actual_value: float,
        explanation: str,
        remediation: str,
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
            "remediation": self.remediation,
        }


# =============================================================================
# Helper Utilities
# =============================================================================

def _coerce_numeric(value, default: float = 0.0) -> float:
    """Coerce LLM-returned values (str/int/float/None) to float."""
    if value is None:
        return default
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return default


def _normalize_execution_plan(plan: dict) -> None:
    """Coerce numeric fields in position_sizing and exit_logic to float in-place."""
    ps = plan.get("position_sizing")
    if isinstance(ps, dict):
        for key in ("target_shares", "max_shares", "min_shares", "max_shares_per_day"):
            if key in ps:
                ps[key] = _coerce_numeric(ps[key])
    el = plan.get("exit_logic")
    if isinstance(el, dict):
        sl = el.get("stop_loss")
        if isinstance(sl, dict) and "price" in sl:
            sl["price"] = _coerce_numeric(sl["price"])
        tp = el.get("take_profit")
        if isinstance(tp, dict) and "price" in tp:
            tp["price"] = _coerce_numeric(tp["price"])


# =============================================================================
# Individual Check Functions
# =============================================================================

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
        return None  # Stop already defined — nothing to do

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

    print(
        f"[RiskScorer] Auto-generated stop-loss at {stop_price:.2f} EGP ({note})"
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

    # Word-boundary patterns — only match actual short-selling language
    short_sell_patterns = [
        r"\bshort[\s_-]sell(ing)?\b",
        r"\bsell[\s_-]short\b",
        r"\bnaked[\s_-]short\b",
        r"\bshort[\s_-]position\b",
        r"\bgo[\s_-]short\b",
        r"\bopen[\s_-]short\b",
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
                limit_value=1.0,
                actual_value=2.0,
                explanation=f"Leverage is forbidden on EGX. Found indicator: '{indicator}'",
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
    print(
        f"[RiskScorer] THROTTLE applied: max_shares_per_day "
        f"{old_daily:,.0f} → {throttle_daily:,} (5% of {effective_adv:,.0f} ADV), "
        f"execution_days → {new_days}"
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
