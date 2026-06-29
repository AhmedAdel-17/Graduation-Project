"""
Deterministic signal calibration for the EGX Fundamental Analyst.

Separates the LLM's assessment (outlook, risk) from the final directional
label by applying a base-rate-aware calibration policy.

Design rationale (Phase A diagnostic, 2026-04-27):
  - The LLM produces high-quality reasoning but poor directional labels
  - Non-up predictions have ~27% precision (near random)
  - ~76% of annual EGX outcomes are "up" in 2020-2024
  - Calibration requires strong evidence to override the up base rate
  - The calibrated direction is what downstream agents should consume

Policy: v3_sector_leverage_aware
  - Default to "up" (base-rate-aware)
  - Banks sector: always "up" — CBE recapitalization patterns make down
    predictions unreliable regardless of distress signals
  - Extreme leverage (D/E > 4): always "up" — highly leveraged firms on
    EGX tend to refinance or sell assets rather than report lower earnings
  - Keep "down" only when: raw=down, bearish outlook, high risk, conf >= 75,
    sector != banks, D/E <= 4
  - Do not emit "flat" as the final calibrated direction
  - Preserve flat/downside/risk information in raw fields and calibration notes
"""
from __future__ import annotations

from typing import List, NamedTuple, Optional

from tradingagents.dataflows.config import get_config

POLICY_NAME = "v3_sector_leverage_aware"

# Sectors where LLM down-calls are structurally unreliable on EGX.
_SECTORS_ALWAYS_UP: frozenset = frozenset({"banks"})

# Hardcoded defaults (overridden by config when available).
_DEFAULT_MAX_DE_FOR_DOWN_CALL: float = 4.0
_DEFAULT_CALIBRATED_UP_CONFIDENCE: int = 60

# P2: CBE rate threshold for high-rate regime.
# When risk_free_rate > this value, "flat" is preserved for non-bank sectors.
_DEFAULT_HIGH_RATE_THRESHOLD: float = 0.15


class CalibrationResult(NamedTuple):
    """Output of the calibration function."""
    calibrated_direction: str       # "up" | "down"
    policy: str                     # policy name/version
    notes: List[str]                # audit trail of decisions
    calibrated_confidence: int      # min(raw_llm_confidence, data_confidence + 20)


def calibrate_earnings_direction(
    fundamental_outlook: str,
    downside_risk_level: str,
    raw_earnings_direction: str,
    earnings_direction_confidence: int,
    freq: str = "annual",
    data_confidence: int = 100,
    sector: str = "",
    de_ratio: Optional[float] = None,
    risk_free_rate: Optional[float] = None,
) -> CalibrationResult:
    """
    Apply deterministic base-rate-aware calibration to the LLM's raw direction.

    Args:
        fundamental_outlook: "bullish" | "neutral" | "bearish" | ""
        downside_risk_level: "low" | "moderate" | "high" | ""
        raw_earnings_direction: "up" | "down" | "flat" | ""
        earnings_direction_confidence: 0-100
        freq: "annual" or "quarterly"
        data_confidence: data quality score (0-100), used to cap LLM confidence
        sector: ticker's sector ("banks" | "real_estate" | "holdings" | "operational")
        de_ratio: debt-to-equity ratio from the financial calculator, or None if unavailable
        risk_free_rate: date-aware CBE policy rate, or None if unavailable. Used for
            P2 high-rate regime adjustment (flat preservation).

    Returns:
        CalibrationResult with calibrated direction, policy name, and notes.
    """
    cfg = get_config()
    _max_de = cfg.get("calibration_max_de_for_down", _DEFAULT_MAX_DE_FOR_DOWN_CALL)
    _cal_up_conf = cfg.get("calibration_up_confidence", _DEFAULT_CALIBRATED_UP_CONFIDENCE)

    notes: List[str] = []
    raw = raw_earnings_direction
    outlook = fundamental_outlook
    risk = downside_risk_level
    conf = earnings_direction_confidence

    # Confidence calibration: cap LLM confidence by data quality.
    # Formula: min(raw_llm_confidence, data_confidence + 20)
    # data_confidence+20 allows a small LLM premium over data quality.
    cal_conf: int = min(conf, data_confidence + 20)
    if cal_conf != conf:
        notes.append(
            f"confidence calibrated: raw={conf} → {cal_conf} "
            f"(data_confidence={data_confidence}, cap={data_confidence + 20})"
        )

    # If no raw direction was predicted, use the base-rate-aware default.
    if raw not in ("up", "down", "flat"):
        return CalibrationResult(
            calibrated_direction="up",
            policy=POLICY_NAME,
            notes=["no valid raw direction; defaulted final direction to up"],
            calibrated_confidence=cal_conf,
        )

    # If raw is already "up", keep it — no override needed.
    if raw == "up":
        notes.append("raw=up; kept as-is")
        return CalibrationResult(
            calibrated_direction="up",
            policy=POLICY_NAME,
            notes=notes,
            calibrated_confidence=cal_conf,
        )

    # --- Non-up predictions: apply calibration gates ---

    # Final flat labels are not validated yet.  In normal-rate regimes or for
    # banks, flat is converted to up (base-rate-aware default).  In high-rate
    # regimes for non-bank sectors, flat is preserved as-is (P2 honesty fix:
    # prevents contradictory "calibrated up but raw evidence bearish" reports).
    _high_rate = (risk_free_rate is not None
                  and risk_free_rate > _DEFAULT_HIGH_RATE_THRESHOLD)
    if raw == "flat":
        if _high_rate and sector not in _SECTORS_ALWAYS_UP:
            notes.append(
                f"raw=flat conf={conf} outlook={outlook} risk={risk} "
                f"rfr={risk_free_rate:.2%} sector={sector}; "
                "kept as flat (P2: high-rate non-bank regime — flat is honest)"
            )
            return CalibrationResult(
                calibrated_direction="flat",
                policy=POLICY_NAME,
                notes=notes,
                calibrated_confidence=cal_conf,
            )
        else:
            notes.append(
                f"raw=flat conf={conf} outlook={outlook} risk={risk}; "
                "calibrated to up (flat retained only as raw/risk context)"
            )
            return CalibrationResult(
                calibrated_direction="up",
                policy=POLICY_NAME,
                notes=notes,
                calibrated_confidence=_cal_up_conf,
            )

    # Gate 0a: Sector exclusion — banks follow CBE recapitalization patterns.
    # Down-calls on banks are structurally unreliable regardless of distress signals.
    if sector in _SECTORS_ALWAYS_UP:
        notes.append(
            f"raw=down conf={conf} sector={sector}; "
            "calibrated to up (bank sector excluded from down calls — v3 gate)"
        )
        return CalibrationResult(
            calibrated_direction="up",
            policy=POLICY_NAME,
            notes=notes,
            calibrated_confidence=_cal_up_conf,
        )

    # Gate 0b: Extreme leverage exclusion — companies with D/E > 4 on EGX tend
    # to refinance or sell assets rather than sustain lower earnings.
    if de_ratio is not None and de_ratio > _max_de:
        notes.append(
            f"raw=down conf={conf} de_ratio={de_ratio:.2f} > {_max_de}; "
            "calibrated to up (extreme leverage excluded from down calls — v3 gate)"
        )
        return CalibrationResult(
            calibrated_direction="up",
            policy=POLICY_NAME,
            notes=notes,
            calibrated_confidence=_cal_up_conf,
        )

    # Gate 1: Confidence must be >= 75 to keep a down prediction.
    if conf < 75:
        notes.append(f"raw=down conf={conf} < 75; calibrated to up (low confidence)")
        return CalibrationResult(
            calibrated_direction="up",
            policy=POLICY_NAME,
            notes=notes,
            calibrated_confidence=_cal_up_conf,
        )

    # Gate 2: For "down", require bearish outlook + high risk.
    if raw == "down":
        if outlook == "bearish" and risk == "high":
            notes.append(
                f"raw=down conf={conf} outlook=bearish risk=high sector={sector} de={de_ratio}; "
                "kept as down (v3 sector-leverage-aware gate passed)"
            )
            return CalibrationResult(
                calibrated_direction="down",
                policy=POLICY_NAME,
                notes=notes,
                calibrated_confidence=cal_conf,  # keep LLM confidence for genuine down calls
            )
        else:
            notes.append(
                f"raw=down conf={conf} outlook={outlook} risk={risk}; "
                "calibrated to up (outlook/risk do not pass down gate)"
            )
            return CalibrationResult(
                calibrated_direction="up",
                policy=POLICY_NAME,
                notes=notes,
                calibrated_confidence=_cal_up_conf,
            )

    # Fallback (should not be reached)
    notes.append(f"unexpected raw={raw}; defaulting to up")
    return CalibrationResult(
        calibrated_direction="up",
        policy=POLICY_NAME,
        notes=notes,
        calibrated_confidence=_cal_up_conf,
    )
