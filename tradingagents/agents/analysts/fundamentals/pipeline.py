"""
Phase 2A CoT Pipeline Orchestrator for EGX Fundamental Analyst.

Runs the three-stage Chain-of-Thought pipeline:
  Stage 1 (data_cot)    — deterministic evidence pack assembly
  Stage 2 (concept_cot) — quick_thinking_llm scoped interpretation
  Stage 3 (thesis_cot)  — deep_thinking_llm H&P investment thesis

Fallback chain (any stage failure → graceful degradation):
  Stage 1 fails → return deterministic output unchanged
    (pipeline_mode="deterministic", stages_completed=[])
  Stage 2 fails → return Stage 1 + deterministic health heuristic
    (pipeline_mode="cot_partial", stages_completed=["data_cot"])
  Stage 3 fails → return Stage 1 + Stage 2 concept without full thesis
    (pipeline_mode="cot_partial", stages_completed=["data_cot", "concept_cot"])
  All succeed → full CoT report
    (pipeline_mode="cot_full", stages_completed=["data_cot", "concept_cot", "thesis_cot"])

The pipeline always returns a FundamentalAnalysisReport. No exceptions propagate
to the caller — all failures are caught and logged.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from tradingagents.dataflows.config import get_config

from .schemas import FundamentalAnalysisReport, FundamentalsQualityStatus
from .sector_config import SectorConfig
from .data_cot import build_evidence_pack, validate_evidence_pack
from .concept_cot import run_concept_cot
from .thesis_cot import run_thesis_cot
from .thesis_cot_3call import run_thesis_cot_3call
from .calibration import calibrate_earnings_direction
from .memory_manager import FundamentalMemoryManager

logger = logging.getLogger(__name__)


def run_cot_pipeline(
    quick_llm: Any,
    deep_llm: Any,
    report: FundamentalAnalysisReport,
    sector_cfg: SectorConfig,
    freq: str = "annual",
    use_memory: Optional[bool] = None,
    memory_manager: Optional[FundamentalMemoryManager] = None,
    source_run_id: Optional[str] = None,
    momentum_pack: Optional[dict] = None,
) -> FundamentalAnalysisReport:
    """
    Run the three-stage CoT pipeline and return an enriched FundamentalAnalysisReport.

    Args:
      quick_llm: LangChain LLM for Stage 2 (quick_thinking_llm)
      deep_llm:  LangChain LLM for Stage 3 (deep_thinking_llm)
      report:    Deterministic FundamentalAnalysisReport from Phase 1A pipeline
      sector_cfg: SectorConfig for the ticker
      freq: "annual" or "quarterly" analysis mode
      use_memory: Optional Phase 3 memory flag. Defaults to config value.
      memory_manager: Optional injected manager for tests or custom storage.
      source_run_id: Optional audit trace id for memory writes.

    Returns:
      FundamentalAnalysisReport with CoT-enriched fields where available,
      falling back to deterministic values where stages failed.
    """
    ticker = report.ticker
    stages_completed: List[str] = []
    cfg = get_config()
    memory_enabled = cfg.get("use_fundamental_memory", False) if use_memory is None else bool(use_memory)
    manager = memory_manager
    prior_memory_context: Optional[str] = None

    if memory_enabled:
        try:
            manager = manager or FundamentalMemoryManager()
            prior_memory_context = manager.build_prior_context(
                ticker=ticker,
                frequency=freq,
                query=f"{ticker} {report.fiscal_period} {report.sector}",
                as_of_date=report.analysis_date,
            )
        except Exception as e:
            logger.warning("pipeline[%s]: Phase 3 memory retrieval failed: %s", ticker, e)
            prior_memory_context = "PRIOR FUNDAMENTAL MEMORY CONTEXT\nNo prior memory available for this ticker/frequency."

    # ── Stage 1: Evidence Pack Assembly ───────────────────────────────────────
    try:
        evidence_pack = build_evidence_pack(
            report,
            sector_cfg,
            freq=freq,
            prior_memory_context=prior_memory_context if memory_enabled else None,
            momentum_pack=momentum_pack,
        )
    except Exception as e:
        logger.error("pipeline[%s]: Stage 1 (data_cot) crashed: %s", ticker, e)
        deterministic = report.model_copy(update={
            "pipeline_mode": "deterministic",
            "stages_completed": [],
            "quality_status": FundamentalsQualityStatus(
                level="deterministic_only",
                reasons=[f"Stage 1 (data_cot) crashed: {e}"],
                data_available=True,
                enrichment_attempted=True,
                enrichment_succeeded=False,
            ),
            "effective_confidence": min(report.data_confidence, int(report.data_confidence * 0.6)),
        })
        _record_memory_safely(manager, memory_enabled, deterministic, freq, source_run_id)
        return deterministic

    if not evidence_pack.get("_valid"):
        errors = evidence_pack.get("_validation_errors", [])
        logger.info(
            "pipeline[%s]: Stage 1 validation failed (data insufficient): %s",
            ticker, errors,
        )
        deterministic = report.model_copy(update={
            "pipeline_mode": "deterministic",
            "stages_completed": [],
            "quality_status": FundamentalsQualityStatus(
                level="deterministic_only",
                reasons=[f"Stage 1 validation failed: {errors}"],
                data_available=True,
                enrichment_attempted=True,
                enrichment_succeeded=False,
            ),
            "effective_confidence": min(report.data_confidence, int(report.data_confidence * 0.6)),
        })
        _record_memory_safely(manager, memory_enabled, deterministic, freq, source_run_id)
        return deterministic

    stages_completed.append("data_cot")
    logger.debug("pipeline[%s]: Stage 1 complete", ticker)

    # ── Stage 2: Concept CoT ──────────────────────────────────────────────────
    concept_output: dict = {}
    try:
        concept_output = run_concept_cot(quick_llm, evidence_pack)
    except Exception as e:
        logger.error("pipeline[%s]: Stage 2 (concept_cot) crashed: %s", ticker, e)
        partial = _build_partial_report(
            report=report,
            evidence_pack=evidence_pack,
            concept_output={},
            stages_completed=stages_completed,
            failure_reason=f"Stage 2 (concept_cot) crashed: {e}",
        )
        _record_memory_safely(manager, memory_enabled, partial, freq, source_run_id)
        return partial

    if not concept_output.get("_valid"):
        errors = concept_output.get("_validation_errors", [])
        logger.warning(
            "pipeline[%s]: Stage 2 validation failed: %s", ticker, errors
        )
        partial = _build_partial_report(
            report=report,
            evidence_pack=evidence_pack,
            concept_output={},
            stages_completed=stages_completed,
            failure_reason=f"Stage 2 validation failed: {errors}",
        )
        _record_memory_safely(manager, memory_enabled, partial, freq, source_run_id)
        return partial

    stages_completed.append("concept_cot")
    logger.debug("pipeline[%s]: Stage 2 complete", ticker)

    # ── Stage 3: Thesis CoT ───────────────────────────────────────────────────
    thesis_mode = cfg.get("thesis_cot_mode", "single")
    _thesis_runner = run_thesis_cot_3call if thesis_mode == "3call" else run_thesis_cot
    thesis_output: dict = {}
    try:
        thesis_output = _thesis_runner(deep_llm, evidence_pack, concept_output)
    except Exception as e:
        logger.error("pipeline[%s]: Stage 3 (thesis_cot) crashed: %s", ticker, e)
        partial = _build_partial_report(
            report=report,
            evidence_pack=evidence_pack,
            concept_output=concept_output,
            stages_completed=stages_completed,
            failure_reason=f"Stage 3 (thesis_cot) crashed: {e}",
        )
        _record_memory_safely(manager, memory_enabled, partial, freq, source_run_id)
        return partial

    # Stage 3 failures are non-blocking (thesis is enrichment, not gating)
    # We log the issues but still include whatever was parsed.
    if not thesis_output.get("_valid"):
        logger.warning(
            "pipeline[%s]: Stage 3 validation issues (partial thesis): %s",
            ticker, thesis_output.get("_validation_errors", []),
        )
    else:
        stages_completed.append("thesis_cot")

    logger.debug("pipeline[%s]: Stage 3 complete", ticker)

    # ── Assemble Final Report ─────────────────────────────────────────────────
    final_report = _build_full_report(
        report=report,
        evidence_pack=evidence_pack,
        concept_output=concept_output,
        thesis_output=thesis_output,
        stages_completed=stages_completed,
        freq=freq,
    )
    _record_memory_safely(manager, memory_enabled, final_report, freq, source_run_id)
    return final_report


# =============================================================================
# Report assembly helpers
# =============================================================================

def _build_partial_report(
    report: FundamentalAnalysisReport,
    evidence_pack: dict,
    concept_output: dict,
    stages_completed: List[str],
    failure_reason: str = "",
) -> FundamentalAnalysisReport:
    """
    Build a partially-enriched report when Stage 2 or Stage 3 failed.

    Applies concept_output fields if available, otherwise falls back
    to deterministic values. Stage 3 thesis fields remain empty.
    """
    # "partial" if at least one CoT stage succeeded; "deterministic_only" if none did
    has_any_cot = len(stages_completed) > 0
    quality_level = "partial" if has_any_cot else "deterministic_only"

    reasons = []
    if failure_reason:
        reasons.append(failure_reason)
    missing_stages = {"data_cot", "concept_cot", "thesis_cot"} - set(stages_completed)
    if missing_stages:
        reasons.append(f"Missing stages: {', '.join(sorted(missing_stages))}")

    # Penalize effective_confidence: partial gets 80% of data_confidence,
    # deterministic_only (all stages failed) gets 60%
    penalty_factor = 0.8 if has_any_cot else 0.6
    effective_conf = min(report.data_confidence, int(report.data_confidence * penalty_factor))

    updates: dict = {
        "pipeline_mode": "cot_partial",
        "stages_completed": stages_completed,
        "quality_status": FundamentalsQualityStatus(
            level=quality_level,
            reasons=reasons,
            data_available=True,
            enrichment_attempted=True,
            enrichment_succeeded=False,
        ),
        "effective_confidence": effective_conf,
    }

    # If concept_cot succeeded, apply its financial_health assessment
    if concept_output.get("_valid") and concept_output.get("financial_health"):
        updates["financial_health"] = concept_output["financial_health"]

        # Enrich key_risks with concept-identified risks
        concept_risks = concept_output.get("risk_factors", [])
        if concept_risks:
            combined_risks = _merge_risks(report.key_risks, concept_risks)
            updates["key_risks"] = combined_risks

    return report.model_copy(update=updates)


def _build_full_report(
    report: FundamentalAnalysisReport,
    evidence_pack: dict,
    concept_output: dict,
    thesis_output: dict,
    stages_completed: List[str],
    freq: str = "annual",
) -> FundamentalAnalysisReport:
    """
    Build the fully-enriched CoT report from all three stages.

    Field priority (highest → lowest):
      thesis_cot output > concept_cot output > deterministic heuristic

    Deterministic values (ratios, preprocessing, distress_flags,
    data_confidence, signal_coherence) are NEVER overwritten — they
    come from verified computation, not LLM output.

    Signal calibration (Phase B):
      After extracting the LLM's raw direction, apply deterministic
      calibration to produce the final earnings_direction.
    """
    pipeline_mode = (
        "cot_full" if "thesis_cot" in stages_completed else "cot_partial"
    )

    # financial_health: thesis > concept > deterministic heuristic
    financial_health = (
        thesis_output.get("financial_health")
        or concept_output.get("financial_health")
        or report.financial_health
    )
    # Validate the value is legal
    if financial_health not in {"healthy", "concerning", "critical", "insufficient_data"}:
        financial_health = report.financial_health

    # Raw earnings_direction from LLM (before calibration)
    raw_direction = thesis_output.get("earnings_direction", "")
    raw_earnings_direction_confidence = thesis_output.get("earnings_direction_confidence", 0)

    # New Phase B fields from LLM
    fundamental_outlook = thesis_output.get("fundamental_outlook", "")
    downside_risk_level = thesis_output.get("downside_risk_level", "")

    # Apply deterministic calibration (Phase 3: cap confidence by data quality)
    cal = calibrate_earnings_direction(
        fundamental_outlook=fundamental_outlook,
        downside_risk_level=downside_risk_level,
        raw_earnings_direction=raw_direction,
        earnings_direction_confidence=raw_earnings_direction_confidence,
        freq=freq,
        data_confidence=report.data_confidence,
        sector=report.sector,
        de_ratio=report.ratios.get("debt_to_equity") if report.ratios else None,
        risk_free_rate=getattr(report, "risk_free_rate_value", None),
    )

    # earnings_direction = calibrated direction (backward compatible)
    earnings_direction = cal.calibrated_direction
    # Public confidence = calibrated (capped) value; raw preserved separately
    earnings_direction_confidence = cal.calibrated_confidence

    # valuation_assessment: thesis > concept
    valuation_assessment = (
        thesis_output.get("valuation_assessment")
        or concept_output.get("valuation_read")
        or ""
    )

    # thesis_text: thesis only
    thesis_text = thesis_output.get("thesis_text", "")

    # key_risks: merge thesis risks + existing structural risks
    thesis_risks = thesis_output.get("key_risks", [])
    egx_risks = thesis_output.get("egx_specific_risks", [])
    all_thesis_risks = thesis_risks + [r for r in egx_risks if r not in thesis_risks]

    # Merge with the deterministic EGX structural risks
    key_risks = _merge_risks(report.key_risks, all_thesis_risks)

    # Competing-hypotheses audit trail (3-call mode only; empty for single-call)
    competing_hypotheses = thesis_output.get("competing_hypotheses", [])
    scored_hypotheses = thesis_output.get("scored_hypotheses", [])
    selected_hypothesis_id = thesis_output.get("selected_hypothesis_id", "")

    # Quality status: "full" if all 3 stages completed, "partial" otherwise
    is_full = pipeline_mode == "cot_full"
    quality = FundamentalsQualityStatus(
        level="full" if is_full else "partial",
        reasons=[] if is_full else [f"Missing stages: {', '.join({'data_cot', 'concept_cot', 'thesis_cot'} - set(stages_completed))}"],
        data_available=True,
        enrichment_attempted=True,
        enrichment_succeeded=is_full,
    )
    # effective_confidence: full data_confidence when enrichment succeeded,
    # 80% when partial (some interpretive context present)
    effective_conf = report.data_confidence if is_full else min(
        report.data_confidence, int(report.data_confidence * 0.8)
    )

    return report.model_copy(update={
        "pipeline_mode": pipeline_mode,
        "stages_completed": stages_completed,
        "financial_health": financial_health,
        # Quality/degradation tracking
        "quality_status": quality,
        "effective_confidence": effective_conf,
        # Calibrated direction is the public earnings_direction
        "earnings_direction": earnings_direction,
        "earnings_direction_confidence": earnings_direction_confidence,
        # Raw LLM confidence before data_confidence calibration
        "raw_earnings_direction_confidence": raw_earnings_direction_confidence,
        # Phase B signal fields
        "fundamental_outlook": fundamental_outlook,
        "downside_risk_level": downside_risk_level,
        "raw_earnings_direction": raw_direction,
        "calibrated_earnings_direction": cal.calibrated_direction,
        "calibration_policy": cal.policy,
        "signal_calibration_notes": cal.notes,
        # Other enrichment fields
        "valuation_assessment": valuation_assessment,
        "thesis_text": thesis_text,
        "key_risks": key_risks,
        # Competing-hypotheses audit trail (3-call H&P)
        "competing_hypotheses": competing_hypotheses,
        "scored_hypotheses": scored_hypotheses,
        "selected_hypothesis_id": selected_hypothesis_id,
    })


def _merge_risks(structural_risks: List[str], llm_risks: List[str]) -> List[str]:
    """
    Merge LLM-identified risks with structural EGX risks.

    LLM risks come first (company-specific). Structural EGX risks are
    appended if not already covered by the LLM risks.

    Deduplication is case-insensitive substring matching.
    """
    result = list(llm_risks)
    llm_text = " ".join(llm_risks).lower()

    for structural_risk in structural_risks:
        # Check if structural risk is already covered by LLM output
        key_words = structural_risk.lower().split()[:3]
        covered = any(word in llm_text for word in key_words if len(word) > 4)
        if not covered:
            result.append(structural_risk)

    return result


def _record_memory_safely(
    memory_manager: Optional[FundamentalMemoryManager],
    memory_enabled: bool,
    report: FundamentalAnalysisReport,
    freq: str,
    source_run_id: Optional[str],
) -> None:
    """Write Phase 3 reflection without allowing memory failures to affect output."""
    if not memory_enabled or memory_manager is None:
        return
    try:
        memory_manager.record_reflection(
            report=report,
            frequency=freq,
            source_run_id=source_run_id,
        )
    except Exception as e:
        logger.warning("pipeline[%s]: Phase 3 memory write failed: %s", report.ticker, e)
