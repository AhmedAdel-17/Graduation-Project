"""
Output schema for the EGX Fundamental Analyst.

FundamentalAnalysisReport is the single structured contract consumed by
downstream agents (Bull/Bear Researchers).

Three quality scores — conceptually separated:
  data_confidence  (0–100): How much usable data is available?
  signal_coherence (0–100): Are the numbers mathematically consistent?
  distress_flags   (List):  What business condition alerts fired?

These are NOT collapsed into one score. Distressed companies with complete
data should have high data_confidence, high signal_coherence, and non-empty
distress_flags. Conflating them loses information for downstream agents.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class FundamentalAnalysisReport(BaseModel):
    """
    Structured output of the EGX Fundamental Analyst.
    Consumed by Bull/Bear Researchers and the Reflector.
    """

    # ── Identity ─────────────────────────────────────────────────────────────
    ticker: str
    analysis_date: str          # ISO date: YYYY-MM-DD
    fiscal_period: str          # e.g. "FY2023" or "Q3-2024"
    sector: str                 # "banks" | "real_estate" | "holdings" | "operational"

    # ── Computed ratios (financial_calculator.py) ─────────────────────────────
    # All values are floats or None (None = undefined / not computable).
    # No thresholds. No labels. Raw numbers only.
    ratios: Dict[str, Optional[float]] = Field(
        default_factory=dict,
        description=(
            "Core ratios keyed by name. None = undefined (e.g. PE when EPS <= 0). "
            "Keys: roe, roa, gross_margin, operating_margin, net_margin, "
            "debt_to_equity, current_ratio, asset_turnover, equity_multiplier, "
            "dupont_3factor, eps, pe_ratio, pb_ratio, earnings_yield, "
            "earnings_yield_spread, dividend_yield, piotroski_score"
        ),
    )

    # ── Preprocessing outputs (statement_standardizer.py) ────────────────────
    preprocessing: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Preprocessing derivatives. Keys: "
            "common_size_income (dict), common_size_balance (dict), "
            "yoy_changes (dict), qoq_changes (dict), "
            "revenue_growth_yoy (float|None), net_income_growth_yoy (float|None), "
            "directions (dict: metric -> 'improving'|'stable'|'deteriorating'|'insufficient_history')"
        ),
    )

    # ── Safety-floor alerts (sector_config.py) ───────────────────────────────
    # Business condition flags — separate from signal_coherence.
    # A distressed company has non-empty distress_flags AND high signal_coherence.
    distress_flags: List[str] = Field(
        default_factory=list,
        description=(
            "Safety-floor alerts from sector_config. Possible values: "
            "NEGATIVE_MARGIN_ALERT, HIGH_LEVERAGE_ALERT, LIQUIDITY_EMERGENCY, "
            "NEGATIVE_EQUITY_ALERT, PE_UNDEFINED, ZERO_REVENUE_PERIOD, "
            "PB_UNDERSTATED_HISTORICAL_COST, CONSOLIDATED_BLENDING, "
            "ROE_MARGIN_INCONSISTENCY, EARNINGS_YIELD_ATTRACTIVE, EARNINGS_YIELD_COMPRESSED"
        ),
    )

    # ── Quality scores ────────────────────────────────────────────────────────
    data_confidence: int = Field(
        default=0,
        ge=0,
        le=100,
        description="0–100: How much usable data is available? Unaffected by business condition.",
    )
    signal_coherence: int = Field(
        default=100,
        ge=0,
        le=100,
        description=(
            "0–100: Are the numbers mathematically consistent? "
            "Deductions only for: ROE/margin sign contradiction (−15), "
            "cross-source current_ratio divergence >30% (−15), "
            "DuPont identity failure (−10). "
            "NOT affected by distress conditions or missing optional fields."
        ),
    )

    # ── Interpretive fields ───────────────────────────────────────────────────
    # Deterministic path: set by heuristic (majority-of-directions).
    # CoT path (Phase 2A+): set by deep_thinking_llm.
    financial_health: str = Field(
        default="",
        description="'healthy' | 'concerning' | 'critical' | 'insufficient_data' (deterministic heuristic) or LLM assessment",
    )
    valuation_assessment: str = Field(
        default="",
        description="Free-text valuation assessment. Deterministic: '' (not assessed). CoT: LLM output.",
    )
    earnings_direction: str = Field(
        default="",
        description="'up' | 'down' | 'flat' | '' (empty = not predicted). Predicted by Thesis-CoT.",
    )
    earnings_direction_confidence: int = Field(
        default=0,
        ge=0,
        le=100,
        description="Confidence in earnings_direction prediction (0 = no prediction made).",
    )
    thesis_text: str = Field(
        default="",
        description="Full investment thesis from Thesis-CoT. Empty in deterministic mode.",
    )
    key_risks: List[str] = Field(
        default_factory=list,
        description="Key risks identified. Deterministic: EGX structural risks only. CoT: analyst-identified risks.",
    )

    # ── Pipeline metadata ─────────────────────────────────────────────────────
    pipeline_mode: str = Field(
        default="deterministic",
        description="'deterministic' | 'cot_partial' | 'cot_full'",
    )
    stages_completed: List[str] = Field(
        default_factory=list,
        description="Which CoT stages completed: ['data_cot', 'concept_cot', 'thesis_cot']",
    )

    model_config = {"extra": "allow"}
