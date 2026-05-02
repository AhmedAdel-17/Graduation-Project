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

Policy: v2_conservative_nonup
  - Default to "up" (base-rate-aware)
  - Keep "down" only when: raw direction is down, bearish outlook, high risk, confidence >= 75
  - Do not emit "flat" as the final calibrated direction for now
  - Preserve flat/downside/risk information in raw fields and calibration notes
  - This is deliberately conservative to avoid false non-up final labels
"""
from __future__ import annotations

from typing import List, NamedTuple

POLICY_NAME = "v2_conservative_nonup"


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
) -> CalibrationResult:
    """
    Apply deterministic base-rate-aware calibration to the LLM's raw direction.

    Args:
        fundamental_outlook: "bullish" | "neutral" | "bearish" | ""
        downside_risk_level: "low" | "moderate" | "high" | ""
        raw_earnings_direction: "up" | "down" | "flat" | ""
        earnings_direction_confidence: 0-100
        freq: "annual" or "quarterly"

    Returns:
        CalibrationResult with calibrated direction, policy name, and notes.
    """
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

    # Final flat labels are not validated yet. Preserve the raw flat signal
    # in raw_earnings_direction and notes, but expose final direction as up.
    if raw == "flat":
        notes.append(
            f"raw=flat conf={conf} outlook={outlook} risk={risk}; "
            "calibrated to up (flat retained only as raw/risk context)"
        )
        return CalibrationResult(
            calibrated_direction="up",
            policy=POLICY_NAME,
            notes=notes,
            calibrated_confidence=cal_conf,
        )

    # Gate 1: Confidence must be >= 75 to keep a down prediction.
    if conf < 75:
        notes.append(f"raw=down conf={conf} < 75; calibrated to up (low confidence)")
        return CalibrationResult(
            calibrated_direction="up",
            policy=POLICY_NAME,
            notes=notes,
            calibrated_confidence=cal_conf,
        )

    # Gate 2: For "down", require bearish outlook + high risk.
    if raw == "down":
        if outlook == "bearish" and risk == "high":
            notes.append(
                f"raw=down conf={conf} outlook=bearish risk=high; "
                "kept as down (v2 conservative non-up gate passed)"
            )
            return CalibrationResult(
                calibrated_direction="down",
                policy=POLICY_NAME,
                notes=notes,
                calibrated_confidence=cal_conf,
            )
        else:
            notes.append(
                f"raw=down conf={conf} outlook={outlook} risk={risk}; "
                "calibrated to up (outlook/risk do not pass v2 down gate)"
            )
            return CalibrationResult(
                calibrated_direction="up",
                policy=POLICY_NAME,
                notes=notes,
                calibrated_confidence=cal_conf,
            )

    # Fallback (should not be reached)
    notes.append(f"unexpected raw={raw}; defaulting to up")
    return CalibrationResult(
        calibrated_direction="up",
        policy=POLICY_NAME,
        notes=notes,
        calibrated_confidence=cal_conf,
    )
