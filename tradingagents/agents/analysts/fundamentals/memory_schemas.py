"""
Typed schemas for Fundamental Analyst Phase 3 memory.

The memory tiers are deliberately local and audit-friendly:
  - OperationalMemoryItem: recent period-level analysis and reflection
  - StrategicMemoryItem: persistent ticker-level observations

Importance scores are deterministic engineering heuristics, not LLM self-scores:
  - 5: thesis + outcome record
  - 4: thesis-vs-actual delta record
  - 3: ratio snapshot
  - 5: strategic observation
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


Direction = Literal["up", "down", "flat"]
Frequency = Literal["annual", "quarterly"]


def utc_now_iso() -> str:
    """Return an audit-friendly UTC timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class OperationalMemoryItem(BaseModel):
    """Recent period-level Fundamental Analyst memory."""

    ticker: str
    period_end_date: str
    frequency: Frequency = "annual"
    thesis_text: str = ""
    earnings_direction_prediction: Optional[Direction] = None
    earnings_direction_confidence: int = Field(default=0, ge=0, le=100)
    actual_earnings_direction: Optional[Direction] = None
    thesis_vs_actual_delta: Optional[str] = None
    ratio_snapshot: Dict[str, Any] = Field(default_factory=dict)
    key_risks: List[str] = Field(default_factory=list)
    distress_flags: List[str] = Field(default_factory=list)
    data_confidence: int = Field(default=0, ge=0, le=100)
    signal_coherence: int = Field(default=100, ge=0, le=100)
    importance: int = Field(default=3, ge=1, le=5)
    write_date: str = Field(default_factory=utc_now_iso)
    source_run_id: str = ""
    memory_tier: Literal["operational"] = "operational"

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper().replace(".CA", "").strip()

    @field_validator("key_risks", "distress_flags")
    @classmethod
    def drop_empty_strings(cls, values: List[str]) -> List[str]:
        return [str(v).strip() for v in values if str(v).strip()]


class StrategicMemoryItem(BaseModel):
    """Long-lived ticker-level Fundamental Analyst memory."""

    ticker: str
    structural_observations: List[str] = Field(default_factory=list)
    recurring_risk_themes: List[str] = Field(default_factory=list)
    recurring_data_issues: List[str] = Field(default_factory=list)
    cumulative_accuracy: Dict[str, Any] = Field(default_factory=dict)
    persistent_narrative: str = ""
    sector_specific_notes: List[str] = Field(default_factory=list)
    recurring_thesis_mistakes: List[str] = Field(default_factory=list)
    source_data_limitations: List[str] = Field(default_factory=list)
    importance: int = Field(default=5, ge=1, le=5)
    last_updated: str = Field(default_factory=utc_now_iso)
    source_run_ids: List[str] = Field(default_factory=list)
    memory_tier: Literal["strategic"] = "strategic"

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper().replace(".CA", "").strip()

    @field_validator(
        "structural_observations",
        "recurring_risk_themes",
        "recurring_data_issues",
        "sector_specific_notes",
        "recurring_thesis_mistakes",
        "source_data_limitations",
        "source_run_ids",
    )
    @classmethod
    def clean_list(cls, values: List[str]) -> List[str]:
        return [str(v).strip() for v in values if str(v).strip()]

