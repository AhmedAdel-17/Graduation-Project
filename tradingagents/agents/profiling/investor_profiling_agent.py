"""
Strategic Investment Profiling Agent — SWING-ONLY MODE.

This system is configured for SWING trading only. The classify() method
returns a fixed SWING profile instantly without an LLM call.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("tradingagents.investor_profiling")

# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

VALID_CATEGORIES = {"SWING"}

TRIGGER_MAP: Dict[str, str] = {
    "SWING": "30 14 * * 0-4",   # 14:30 EGT, Sun-Thu (EGX market close)
}

PRIORITY_MAP: Dict[str, List[str]] = {
    "SWING": ["Technical", "Fundamental", "Sentiment"],
}


class InvestorProfile(BaseModel):
    investor_category: str = Field(..., description="SWING")
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    trigger_frequency: str = Field(..., description="Cron expression for data refresh")
    analysis_priority: List[str] = Field(..., description="Ordered analyst priority list")
    reasoning: str = Field(..., description="Why this category was chosen")

    @field_validator("investor_category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        v = v.upper().strip()
        if v not in VALID_CATEGORIES:
            raise ValueError(f"investor_category must be SWING (this system is SWING-only)")
        return v


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------

class InvestorProfilingAgent:
    """
    Returns a fixed SWING profile. No LLM call is made.

    Usage
    -----
    agent = InvestorProfilingAgent()
    profile = agent.classify()
    # profile.investor_category -> "SWING"
    # profile.confidence_score  -> 1.0
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        # Config accepted for interface compatibility but not used.
        self.config = config or {}

    def classify(self, interview_text: str = "") -> InvestorProfile:
        """
        Return a fixed SWING profile regardless of interview text.

        Parameters
        ----------
        interview_text : str
            Ignored. Kept for backward compatibility.

        Returns
        -------
        InvestorProfile
            Always SWING with confidence 1.0.
        """
        logger.info("InvestorProfilingAgent: returning fixed SWING profile (SWING-only mode)")
        return InvestorProfile(
            investor_category="SWING",
            confidence_score=1.0,
            trigger_frequency=TRIGGER_MAP["SWING"],
            analysis_priority=PRIORITY_MAP["SWING"],
            reasoning=(
                "This system is configured for SWING trading only. "
                "All analyses use swing-horizon indicators (daily OHLCV, "
                "weekly rebalance, moderate-risk position sizing). "
                "No LLM call is needed for profiling."
            ),
        )
