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


class FundamentalsQualityStatus(BaseModel):
    """
    Degradation status of the fundamentals analysis.

    Levels:
      full              — deterministic + all 3 CoT stages succeeded
      partial           — deterministic + some CoT stages succeeded (e.g. data_cot + concept_cot)
      deterministic_only — ratios/flags computed, no interpretive enrichment
      unavailable       — no financial data found for this ticker
    """
    level: str = Field(
        default="deterministic_only",
        description="'full' | 'partial' | 'deterministic_only' | 'unavailable'",
    )
    reasons: List[str] = Field(
        default_factory=list,
        description="Why enrichment failed, was skipped, or data is missing.",
    )
    data_available: bool = Field(
        default=True,
        description="Whether any financial statement data was found.",
    )
    enrichment_attempted: bool = Field(
        default=False,
        description="Whether CoT/LLM enrichment was attempted.",
    )
    enrichment_succeeded: bool = Field(
        default=False,
        description="Whether all attempted CoT stages succeeded.",
    )


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
        description=(
            "'up' | 'down' | 'flat' | '' (empty = not predicted). "
            "When calibration is active, this equals the calibrated direction. "
            "The raw LLM direction is stored in raw_earnings_direction."
        ),
    )
    earnings_direction_confidence: int = Field(
        default=0,
        ge=0,
        le=100,
        description=(
            "Confidence in earnings_direction prediction (0 = no prediction made). "
            "When confidence calibration is active, this is the calibrated value. "
            "The raw LLM confidence is stored in raw_earnings_direction_confidence."
        ),
    )
    raw_earnings_direction_confidence: int = Field(
        default=0,
        ge=0,
        le=100,
        description="Original LLM confidence before data_confidence calibration.",
    )
    pe_ratio_source: str = Field(
        default="",
        description=(
            "'trade_date_price' | 'csv_fallback' | 'unavailable'. "
            "Indicates whether P/E was computed from live trade-date price or stale CSV."
        ),
    )

    # ── Risk-free rate metadata ───────────────────────────────────────────────
    risk_free_rate_value: Optional[float] = Field(
        default=None,
        description="The risk-free rate used for earnings_yield_spread computation. None if not configured.",
    )
    risk_free_rate_source: str = Field(
        default="",
        description=(
            "'date_aware_cbe_policy_rate' — from local CSV, bounded by trade_date. "
            "'static_config_fallback' — from config['egx_risk_free_rate']; anachronistic for old backtests. "
            "'not_configured' — rate absent; earnings_yield_spread is None."
        ),
    )
    risk_free_rate_effective_date: str = Field(
        default="",
        description=(
            "ISO date when the looked-up CBE rate took effect. "
            "Empty when source is 'static_config_fallback' or 'not_configured'."
        ),
    )

    # ── Signal calibration fields (Phase B) ──────────────────────────────────
    fundamental_outlook: str = Field(
        default="",
        description=(
            "'bullish' | 'neutral' | 'bearish' | '' (empty = not assessed). "
            "LLM assessment of fundamental quality and trajectory."
        ),
    )
    downside_risk_level: str = Field(
        default="",
        description=(
            "'low' | 'moderate' | 'high' | '' (empty = not assessed). "
            "LLM assessment of how likely earnings will deteriorate."
        ),
    )
    raw_earnings_direction: str = Field(
        default="",
        description=(
            "'up' | 'down' | 'flat' | '' (empty = not predicted). "
            "Original LLM direction before calibration."
        ),
    )
    calibrated_earnings_direction: str = Field(
        default="",
        description=(
            "'up' | 'down' | 'flat' | '' (empty = not calibrated). "
            "Deterministic base-rate-aware direction derived from outlook + risk + confidence."
        ),
    )
    calibration_policy: str = Field(
        default="",
        description="Name/version of the calibration policy applied (e.g. 'v1_outlook_risk').",
    )
    signal_calibration_notes: List[str] = Field(
        default_factory=list,
        description="Audit trail of calibration decisions applied to this case.",
    )
    supplemental_context: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Optional manually supplied context used to address standalone signal limitations. "
            "Keys may include valuation_context, quality_of_earnings, narrative_events, "
            "macro_sector_context, and missing_categories. Empty when no supplemental CSV data exists."
        ),
    )

    # ── Thesis and risk fields ───────────────────────────────────────────────
    thesis_text: str = Field(
        default="",
        description="Full investment thesis from Thesis-CoT. Empty in deterministic mode.",
    )
    key_risks: List[str] = Field(
        default_factory=list,
        description="Key risks identified. Deterministic: EGX structural risks only. CoT: analyst-identified risks.",
    )

    # ── Competing-hypotheses audit trail (3-call H&P mode only) ────────────────
    competing_hypotheses: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "All hypotheses generated in Call 1 of the 3-call H&P pipeline. "
            "Each dict has: id, direction, statement, rationale. "
            "Empty when thesis_cot_mode='single'."
        ),
    )
    scored_hypotheses: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Evidence mapping and scores for each hypothesis from Call 2. "
            "Each dict has: id, evidence_for, evidence_against, evidence_support_score, score_rationale. "
            "Empty when thesis_cot_mode='single'."
        ),
    )
    selected_hypothesis_id: str = Field(
        default="",
        description="ID of the hypothesis selected in Call 3 (e.g. 'H1'). Empty when thesis_cot_mode='single'.",
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

    # ── Quality status (degradation tracking) ────────────────────────────────
    quality_status: FundamentalsQualityStatus = Field(
        default_factory=FundamentalsQualityStatus,
        description="Degradation status: full, partial, deterministic_only, or unavailable.",
    )
    effective_confidence: int = Field(
        default=0,
        ge=0,
        le=100,
        description=(
            "Usable confidence for downstream agents. Equals data_confidence when "
            "enrichment succeeded or was not attempted. Reduced when enrichment was "
            "attempted but failed (interpretive layer missing). 0 when data unavailable."
        ),
    )

    model_config = {"extra": "allow"}
