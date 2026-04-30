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

from .schemas import FundamentalAnalysisReport
from .sector_config import SectorConfig
from .data_cot import build_evidence_pack, validate_evidence_pack
from .concept_cot import run_concept_cot
from .thesis_cot import run_thesis_cot

logger = logging.getLogger(__name__)


def run_cot_pipeline(
    quick_llm: Any,
    deep_llm: Any,
    report: FundamentalAnalysisReport,
    sector_cfg: SectorConfig,
) -> FundamentalAnalysisReport:
    """
    Run the three-stage CoT pipeline and return an enriched FundamentalAnalysisReport.

    Args:
      quick_llm: LangChain LLM for Stage 2 (quick_thinking_llm)
      deep_llm:  LangChain LLM for Stage 3 (deep_thinking_llm)
      report:    Deterministic FundamentalAnalysisReport from Phase 1A pipeline
      sector_cfg: SectorConfig for the ticker

    Returns:
      FundamentalAnalysisReport with CoT-enriched fields where available,
      falling back to deterministic values where stages failed.
    """
    ticker = report.ticker
    stages_completed: List[str] = []

    # ── Stage 1: Evidence Pack Assembly ───────────────────────────────────────
    try:
        evidence_pack = build_evidence_pack(report, sector_cfg)
    except Exception as e:
        logger.error("pipeline[%s]: Stage 1 (data_cot) crashed: %s", ticker, e)
        return report.model_copy(update={
            "pipeline_mode": "deterministic",
            "stages_completed": [],
        })

    if not evidence_pack.get("_valid"):
        errors = evidence_pack.get("_validation_errors", [])
        logger.info(
            "pipeline[%s]: Stage 1 validation failed (data insufficient): %s",
            ticker, errors,
        )
        return report.model_copy(update={
            "pipeline_mode": "deterministic",
            "stages_completed": [],
        })

    stages_completed.append("data_cot")
    logger.debug("pipeline[%s]: Stage 1 complete", ticker)

    # ── Stage 2: Concept CoT ──────────────────────────────────────────────────
    concept_output: dict = {}
    try:
        concept_output = run_concept_cot(quick_llm, evidence_pack)
    except Exception as e:
        logger.error("pipeline[%s]: Stage 2 (concept_cot) crashed: %s", ticker, e)
        return _build_partial_report(
            report=report,
            evidence_pack=evidence_pack,
            concept_output={},
            stages_completed=stages_completed,
        )

    if not concept_output.get("_valid"):
        errors = concept_output.get("_validation_errors", [])
        logger.warning(
            "pipeline[%s]: Stage 2 validation failed: %s", ticker, errors
        )
        return _build_partial_report(
            report=report,
            evidence_pack=evidence_pack,
            concept_output={},
            stages_completed=stages_completed,
        )

    stages_completed.append("concept_cot")
    logger.debug("pipeline[%s]: Stage 2 complete", ticker)

    # ── Stage 3: Thesis CoT ───────────────────────────────────────────────────
    thesis_output: dict = {}
    try:
        thesis_output = run_thesis_cot(deep_llm, evidence_pack, concept_output)
    except Exception as e:
        logger.error("pipeline[%s]: Stage 3 (thesis_cot) crashed: %s", ticker, e)
        return _build_partial_report(
            report=report,
            evidence_pack=evidence_pack,
            concept_output=concept_output,
            stages_completed=stages_completed,
        )

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
    return _build_full_report(
        report=report,
        evidence_pack=evidence_pack,
        concept_output=concept_output,
        thesis_output=thesis_output,
        stages_completed=stages_completed,
    )


# =============================================================================
# Report assembly helpers
# =============================================================================

def _build_partial_report(
    report: FundamentalAnalysisReport,
    evidence_pack: dict,
    concept_output: dict,
    stages_completed: List[str],
) -> FundamentalAnalysisReport:
    """
    Build a partially-enriched report when Stage 2 or Stage 3 failed.

    Applies concept_output fields if available, otherwise falls back
    to deterministic values. Stage 3 thesis fields remain empty.
    """
    updates: dict = {
        "pipeline_mode": "cot_partial",
        "stages_completed": stages_completed,
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
) -> FundamentalAnalysisReport:
    """
    Build the fully-enriched CoT report from all three stages.

    Field priority (highest → lowest):
      thesis_cot output > concept_cot output > deterministic heuristic

    Deterministic values (ratios, preprocessing, distress_flags,
    data_confidence, signal_coherence) are NEVER overwritten — they
    come from verified computation, not LLM output.
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

    # earnings_direction: thesis only (concept doesn't predict this)
    earnings_direction = thesis_output.get("earnings_direction", "")
    earnings_direction_confidence = thesis_output.get("earnings_direction_confidence", 0)

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

    return report.model_copy(update={
        "pipeline_mode": pipeline_mode,
        "stages_completed": stages_completed,
        "financial_health": financial_health,
        "earnings_direction": earnings_direction,
        "earnings_direction_confidence": earnings_direction_confidence,
        "valuation_assessment": valuation_assessment,
        "thesis_text": thesis_text,
        "key_risks": key_risks,
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
