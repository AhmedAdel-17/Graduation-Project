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

POLICY_NAME = "v3_sector_leverage_aware"

# Sectors where LLM down-calls are structurally unreliable on EGX.
_SECTORS_ALWAYS_UP: frozenset = frozenset({"banks"})

# D/E threshold above which leverage-driven distress signals are noise:
# these companies tend to refinance or do asset sales rather than report
# lower earnings.
_MAX_DE_FOR_DOWN_CALL: float = 4.0

# When a non-up raw signal is overridden to "up" by a calibration gate, the LLM's
# original confidence should not carry over (it was confident in "down", not "up").
# Cap at naive-baseline level so Brier stays competitive with always-up.
_CALIBRATED_UP_CONFIDENCE: int = 60


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
            calibrated_confidence=_CALIBRATED_UP_CONFIDENCE,
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
            calibrated_confidence=_CALIBRATED_UP_CONFIDENCE,
        )

    # Gate 0b: Extreme leverage exclusion — companies with D/E > 4 on EGX tend
    # to refinance or sell assets rather than sustain lower earnings.
    if de_ratio is not None and de_ratio > _MAX_DE_FOR_DOWN_CALL:
        notes.append(
            f"raw=down conf={conf} de_ratio={de_ratio:.2f} > {_MAX_DE_FOR_DOWN_CALL}; "
            "calibrated to up (extreme leverage excluded from down calls — v3 gate)"
        )
        return CalibrationResult(
            calibrated_direction="up",
            policy=POLICY_NAME,
            notes=notes,
            calibrated_confidence=_CALIBRATED_UP_CONFIDENCE,
        )

    # Gate 1: Confidence must be >= 75 to keep a down prediction.
    if conf < 75:
        notes.append(f"raw=down conf={conf} < 75; calibrated to up (low confidence)")
        return CalibrationResult(
            calibrated_direction="up",
            policy=POLICY_NAME,
            notes=notes,
            calibrated_confidence=_CALIBRATED_UP_CONFIDENCE,
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
                calibrated_confidence=_CALIBRATED_UP_CONFIDENCE,
            )

    # Fallback (should not be reached)
    notes.append(f"unexpected raw={raw}; defaulting to up")
    return CalibrationResult(
        calibrated_direction="up",
        policy=POLICY_NAME,
        notes=notes,
        calibrated_confidence=_CALIBRATED_UP_CONFIDENCE,
    )
