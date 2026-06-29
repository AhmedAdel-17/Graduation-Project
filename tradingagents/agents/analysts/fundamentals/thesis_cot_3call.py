"""
Stage 3 (3-call variant): Competing-Hypotheses H&P Investment Thesis.

Implements a genuine competing-hypotheses design via three sequential LLM calls:

  Call 1 — HYPOTHESES: Generate 2-4 competing, mutually exclusive hypotheses
           about the company's earnings trajectory. Each must be falsifiable
           and grounded in the evidence pack.

  Call 2 — EVIDENCE MAPPING: For EACH hypothesis, map specific evidence FOR
           and AGAINST from the evidence pack + Stage 2 concept output. Score
           each hypothesis on evidence support (0-100).

  Call 3 — SELECTION & THESIS: Given all hypotheses with their scored evidence,
           select the best-supported hypothesis and produce the full investment
           thesis with outlook, risk, and prediction.

Benefits over single-call:
  - Forces the LLM to consider multiple competing narratives before committing
  - Call 2 scores evidence for ALL hypotheses blindly (no prediction visible),
    reducing confirmation bias toward the first hypothesis generated
  - Call 3 must justify its selection against weaker alternatives
  - Full audit trail: rejected hypotheses + their evidence are preserved

The 3-call variant produces the SAME output dict shape as the original
``run_thesis_cot()`` so ``_build_full_report()`` needs no changes.

Gated by config key ``thesis_cot_mode``:
  - "single" (default): uses the original ``thesis_cot.run_thesis_cot()``
  - "3call": uses ``run_thesis_cot_3call()`` from this module

Evidence class:
  DS [P7] Kim et al. 2024 — H&P prompting structure
  [EI] — Competing hypotheses applied to EGX analyst mimicking
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from .thesis_cot import (
    _extract_json,
    _safe_confidence,
    _VALID_DIRECTIONS,
    _VALID_HEALTH,
    _VALID_VALUATION,
    _VALID_OUTLOOK,
    _VALID_RISK_LEVEL,
)

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_HYPOTHESES = """\
You are a senior Portfolio Manager specializing in Egyptian Exchange (EGX) equities.

YOUR TASK: Read the evidence pack and generate 2-4 COMPETING hypotheses about \
this company's earnings trajectory. The hypotheses must represent genuinely \
different views — they should not all predict the same direction.

RULES:
1. Each hypothesis must be a specific, falsifiable claim about future earnings.
2. Hypotheses must be mutually exclusive or at least meaningfully different \
   (e.g., one bullish, one bearish, one structural/flat).
3. Only reference data that appears in the evidence pack. No invented figures.
4. EGX context: EGP currency, ±10% daily limits, long-only, no leverage.
5. Sector-specific norms apply (e.g., bank D/E 5-10× is normal).
6. If PRIOR FUNDAMENTAL MEMORY CONTEXT is present, use as background only.
7. Output ONLY valid JSON. No prose before or after.

OUTPUT FORMAT (strict JSON):
{
  "hypotheses": [
    {
      "id": "H1",
      "direction": "<up|down|flat>",
      "statement": "<one falsifiable sentence>",
      "rationale": "<1-2 sentences explaining why this scenario is plausible>"
    },
    {
      "id": "H2",
      "direction": "<up|down|flat>",
      "statement": "<one falsifiable sentence>",
      "rationale": "<1-2 sentences>"
    }
  ]
}

Generate at least 2 and at most 4 hypotheses. They MUST include at least two \
different directions (e.g., one "up" and one "down" or "flat").
"""

_SYSTEM_PROMPT_EVIDENCE_MAP = """\
You are a senior Portfolio Manager specializing in Egyptian Exchange (EGX) equities.

YOUR TASK: For EACH hypothesis below, identify the strongest evidence FOR and \
AGAINST it from the evidence pack and concept assessment. Then assign an \
evidence_support_score (0-100) reflecting how well the available data supports \
that hypothesis.

RULES:
1. Evidence FOR must be specific data points that support the hypothesis.
2. Evidence AGAINST must genuinely challenge the hypothesis — not strawman objections.
3. Only cite numbers that appear in the provided evidence. No invented figures.
4. Score each hypothesis INDEPENDENTLY — do not let one score influence another.
5. A hypothesis with strong FOR evidence AND weak AGAINST evidence scores high (70-90).
6. A hypothesis with weak FOR evidence OR strong AGAINST evidence scores low (20-50).
7. EGX context: EGP currency, ±10% daily limits, long-only, no leverage.
8. Output ONLY valid JSON. No prose before or after.

OUTPUT FORMAT (strict JSON):
{
  "scored_hypotheses": [
    {
      "id": "H1",
      "evidence_for": ["<data point 1>", "<data point 2>"],
      "evidence_against": ["<data point 1>", "<data point 2>"],
      "evidence_support_score": <integer 0-100>,
      "score_rationale": "<1 sentence explaining the score>"
    },
    {
      "id": "H2",
      "evidence_for": ["<data point>"],
      "evidence_against": ["<data point>"],
      "evidence_support_score": <integer 0-100>,
      "score_rationale": "<1 sentence>"
    }
  ]
}

Score ALL hypotheses provided. Do not skip any.
"""

_SYSTEM_PROMPT_SELECT_THESIS = """\
You are a senior Portfolio Manager specializing in Egyptian Exchange (EGX) equities.

YOUR TASK: Given the competing hypotheses with their evidence scores, SELECT \
the best-supported hypothesis and produce the final investment thesis.

SELECTION RULES:
1. Choose the hypothesis with the highest evidence_support_score as your primary thesis.
2. If scores are within 10 points of each other, explain why you chose one over another.
3. The rejected hypotheses become your "key_risks" and "invalidation_conditions".
4. Your confidence should reflect the margin between the winning and runner-up scores.

CRITICAL RULES:
1. Only cite numbers from the evidence. No invented figures.
2. EGX context: EGP currency, ±10% daily limits, long-only, no leverage.
3. Sector-specific norms apply (e.g., bank D/E 5-10× is normal, not distress).

FUNDAMENTAL OUTLOOK — Assess the company's overall fundamental trajectory:
  - "bullish": weight of evidence supports improving or sustained strong earnings
  - "neutral": evidence is mixed, no clear directional signal
  - "bearish": weight of evidence points to deteriorating earnings or structural problems

DOWNSIDE RISK — Assess how likely earnings are to deteriorate, SEPARATELY from outlook:
  - "low": no major risk factors, healthy balance sheet, stable or improving margins
  - "moderate": some risk factors present but not severe
  - "high": multiple severe risk factors (negative margins, extreme leverage, deterioration)

EARNINGS DIRECTION — Based on the selected hypothesis:
  - Most EGX companies show annual earnings growth in most years. Only predict "down" or
    "flat" when the fundamental evidence strongly supports it.

  QUARTERLY MODE: Growth rates are QoQ. Apply:
    (a) If [NOTE] flags a near-zero crossing (QoQ > 200%): confidence <= 58.
    (b) If Net Income QoQ > +5% AND trend improving: predict "up", confidence 65-75.
    (c) If Net Income QoQ < -5% AND trend deteriorating: predict "down", confidence 65-75.
    (d) If signals conflict: trend directions as primary, confidence <= 58.

CONFIDENCE CALIBRATION — must reflect the hypothesis competition:
  - 75-85: winning hypothesis scored 75+ AND runner-up scored below 50
  - 60-70: winning hypothesis scored 60-75 OR runner-up is close (within 15 points)
  - 45-58: winning hypothesis scored below 60, or multiple hypotheses scored similarly

OUTPUT FORMAT (strict JSON):
{
  "selected_hypothesis_id": "<H1|H2|H3|H4>",
  "selection_rationale": "<2-3 sentences explaining why this hypothesis won>",
  "thesis_text": "<full investment thesis, 3-6 sentences, institutional quality>",
  "fundamental_outlook": "<bullish|neutral|bearish>",
  "downside_risk_level": "<low|moderate|high>",
  "earnings_direction": "<up|down|flat>",
  "earnings_direction_confidence": <integer 0-100>,
  "earnings_direction_rationale": "<1-2 sentences explaining the prediction>",
  "valuation_assessment": "<undervalued|fair_value|overvalued|insufficient_data>",
  "valuation_rationale": "<1-2 sentences>",
  "financial_health": "<healthy|concerning|critical|insufficient_data>",
  "key_risks": ["<risk from rejected hypotheses>", "<risk2>", "<risk3>"],
  "egx_specific_risks": ["<EGX-specific risk if any>"],
  "invalidation_conditions": ["<condition from rejected hypothesis that would flip the thesis>"]
}

Output ONLY valid JSON. No prose before or after.
"""


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_hypotheses_prompt(
    evidence_narrative: str,
    ticker: str,
    sector: str,
) -> str:
    """Build the user prompt for Call 1 (competing hypotheses generation)."""
    return (
        f"Generate 2-4 competing hypotheses about {ticker} "
        f"(sector: {sector.upper()}).\n\n"
        f"--- EVIDENCE PACK ---\n"
        f"{evidence_narrative}\n\n"
        f"Return only the JSON object. No prose. No markdown fences."
    )


def _build_evidence_map_prompt(
    hypotheses: List[Dict[str, str]],
    evidence_narrative: str,
    concept_output: Dict[str, Any],
    ticker: str,
    sector: str,
) -> str:
    """Build the user prompt for Call 2 (evidence mapping + scoring)."""
    health = concept_output.get("financial_health", "unknown")
    health_rationale = concept_output.get("financial_health_rationale", "")
    key_metrics = concept_output.get("key_metrics_discussion", "")
    growth_signal = concept_output.get("growth_signal", "unknown")
    growth_rationale = concept_output.get("growth_signal_rationale", "")
    valuation_read = concept_output.get("valuation_read", "unknown")
    valuation_rationale = concept_output.get("valuation_rationale", "")
    risks = concept_output.get("risk_factors", [])
    standout = concept_output.get("standout_signals", [])
    analyst_note = concept_output.get("analyst_note", "none")

    concept_summary = (
        f"STAGE 2 CONCEPT ASSESSMENT:\n"
        f"  Financial Health: {health} — {health_rationale}\n"
        f"  Key Metrics: {key_metrics}\n"
        f"  Growth Signal: {growth_signal} — {growth_rationale}\n"
        f"  Valuation Read: {valuation_read} — {valuation_rationale}\n"
        f"  Standout Signals: {', '.join(standout) if standout else 'none'}\n"
        f"  Risk Factors: {'; '.join(risks) if risks else 'none'}\n"
        f"  Analyst Note: {analyst_note}\n"
    )

    hypotheses_text = "\n".join(
        f"  {h['id']}: [{h.get('direction', '?')}] {h['statement']}"
        for h in hypotheses
    )

    return (
        f"Score evidence for each hypothesis about {ticker} "
        f"(sector: {sector.upper()}).\n\n"
        f"HYPOTHESES TO EVALUATE:\n{hypotheses_text}\n\n"
        f"--- EVIDENCE PACK ---\n"
        f"{evidence_narrative}\n\n"
        f"--- STAGE 2 CONCEPT ASSESSMENT ---\n"
        f"{concept_summary}\n\n"
        f"Map evidence FOR and AGAINST each hypothesis and assign scores. "
        f"Return only the JSON object. No prose. No markdown fences."
    )


def _build_selection_prompt(
    hypotheses: List[Dict[str, str]],
    scored_hypotheses: List[Dict[str, Any]],
    concept_output: Dict[str, Any],
    ticker: str,
    sector: str,
) -> str:
    """Build the user prompt for Call 3 (selection + thesis)."""
    # Format scored hypotheses with their evidence
    sections = []
    for sh in scored_hypotheses:
        h_id = sh.get("id", "?")
        # Find original statement
        statement = ""
        for h in hypotheses:
            if h.get("id") == h_id:
                statement = h.get("statement", "")
                break
        evidence_for = sh.get("evidence_for", [])
        evidence_against = sh.get("evidence_against", [])
        score = sh.get("evidence_support_score", 0)
        rationale = sh.get("score_rationale", "")

        sections.append(
            f"  {h_id} (score: {score}/100): {statement}\n"
            f"    FOR: {'; '.join(evidence_for) if evidence_for else 'none'}\n"
            f"    AGAINST: {'; '.join(evidence_against) if evidence_against else 'none'}\n"
            f"    Score rationale: {rationale}"
        )

    scored_text = "\n".join(sections)
    health = concept_output.get("financial_health", "unknown")
    growth_signal = concept_output.get("growth_signal", "unknown")

    return (
        f"Select the best-supported hypothesis and produce the final thesis "
        f"for {ticker} (sector: {sector.upper()}).\n\n"
        f"SCORED HYPOTHESES:\n{scored_text}\n\n"
        f"CONCEPT CONTEXT: health={health}, growth_signal={growth_signal}\n\n"
        f"Select the winning hypothesis and produce the thesis JSON. "
        f"No prose. No markdown fences."
    )


# ---------------------------------------------------------------------------
# Individual call runners
# ---------------------------------------------------------------------------

def _run_call_1_hypotheses(
    llm: Any,
    evidence_pack: Dict[str, Any],
) -> Dict[str, Any]:
    """Call 1: Generate 2-4 competing hypotheses.

    Returns dict with 'hypotheses' list, or failure dict with _valid=False.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    ticker = evidence_pack.get("ticker", "")
    sector = evidence_pack.get("sector", "operational")
    narrative = evidence_pack.get("narrative", "")

    user_prompt = _build_hypotheses_prompt(narrative, ticker, sector)

    try:
        response = llm.invoke([
            SystemMessage(content=_SYSTEM_PROMPT_HYPOTHESES),
            HumanMessage(content=user_prompt),
        ])
        raw_text = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        logger.warning("thesis_3call[call1]: LLM call failed: %s", e)
        return {"_valid": False, "_call1_raw": "", "_validation_errors": [f"Call 1 LLM failed: {e}"]}

    parsed = _extract_json(raw_text)
    if parsed is None:
        logger.warning("thesis_3call[call1]: could not parse JSON")
        return {"_valid": False, "_call1_raw": raw_text, "_validation_errors": ["Call 1: could not parse JSON"]}

    hypotheses = parsed.get("hypotheses", [])
    errors: List[str] = []

    if not isinstance(hypotheses, list) or len(hypotheses) < 2:
        errors.append(f"Call 1: need 2-4 hypotheses, got {len(hypotheses) if isinstance(hypotheses, list) else 0}")
    elif len(hypotheses) > 4:
        hypotheses = hypotheses[:4]  # silently truncate

    # Validate each hypothesis has required fields
    valid_hypotheses: List[Dict[str, str]] = []
    for i, h in enumerate(hypotheses if isinstance(hypotheses, list) else []):
        if not isinstance(h, dict):
            errors.append(f"Call 1: hypothesis {i} is not a dict")
            continue
        statement = h.get("statement", "").strip()
        if not statement:
            errors.append(f"Call 1: hypothesis {i} has empty statement")
            continue
        valid_hypotheses.append({
            "id": h.get("id", f"H{i+1}"),
            "direction": h.get("direction", "flat"),
            "statement": statement,
            "rationale": h.get("rationale", "").strip(),
        })

    if len(valid_hypotheses) < 2:
        errors.append(f"Call 1: fewer than 2 valid hypotheses after validation")

    # Check direction diversity
    directions = {h["direction"] for h in valid_hypotheses}
    if len(directions) < 2 and len(valid_hypotheses) >= 2:
        errors.append(
            f"Call 1: all hypotheses predict the same direction ({directions}); "
            "competing hypotheses must include at least 2 different directions"
        )

    return {
        "hypotheses": valid_hypotheses,
        "_valid": len(errors) == 0,
        "_call1_raw": raw_text,
        "_validation_errors": errors,
    }


def _run_call_2_evidence_map(
    llm: Any,
    evidence_pack: Dict[str, Any],
    concept_output: Dict[str, Any],
    hypotheses: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Call 2: Map and score evidence for each hypothesis.

    Returns dict with 'scored_hypotheses' list, or failure dict.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    ticker = evidence_pack.get("ticker", "")
    sector = evidence_pack.get("sector", "operational")
    narrative = evidence_pack.get("narrative", "")

    user_prompt = _build_evidence_map_prompt(
        hypotheses, narrative, concept_output, ticker, sector,
    )

    try:
        response = llm.invoke([
            SystemMessage(content=_SYSTEM_PROMPT_EVIDENCE_MAP),
            HumanMessage(content=user_prompt),
        ])
        raw_text = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        logger.warning("thesis_3call[call2]: LLM call failed: %s", e)
        return {"_valid": False, "_call2_raw": "", "_validation_errors": [f"Call 2 LLM failed: {e}"]}

    parsed = _extract_json(raw_text)
    if parsed is None:
        logger.warning("thesis_3call[call2]: could not parse JSON")
        return {"_valid": False, "_call2_raw": raw_text, "_validation_errors": ["Call 2: could not parse JSON"]}

    scored = parsed.get("scored_hypotheses", [])
    errors: List[str] = []

    if not isinstance(scored, list) or len(scored) == 0:
        errors.append("Call 2: scored_hypotheses is empty or not a list")
        return {
            "scored_hypotheses": [],
            "_valid": False,
            "_call2_raw": raw_text,
            "_validation_errors": errors,
        }

    # Validate each scored hypothesis
    hypothesis_ids = {h["id"] for h in hypotheses}
    valid_scored: List[Dict[str, Any]] = []

    for sh in scored:
        if not isinstance(sh, dict):
            continue
        h_id = sh.get("id", "")
        if h_id not in hypothesis_ids:
            errors.append(f"Call 2: unknown hypothesis id '{h_id}'")
            continue

        evidence_for = sh.get("evidence_for", [])
        evidence_against = sh.get("evidence_against", [])
        score = sh.get("evidence_support_score", 0)

        if not isinstance(evidence_for, list):
            evidence_for = []
        if not isinstance(evidence_against, list):
            evidence_against = []

        try:
            score = max(0, min(100, int(score)))
        except (TypeError, ValueError):
            score = 50

        valid_scored.append({
            "id": h_id,
            "evidence_for": evidence_for,
            "evidence_against": evidence_against,
            "evidence_support_score": score,
            "score_rationale": sh.get("score_rationale", ""),
        })

    # Check we scored all hypotheses
    scored_ids = {s["id"] for s in valid_scored}
    missing = hypothesis_ids - scored_ids
    if missing:
        errors.append(f"Call 2: hypotheses not scored: {missing}")

    if len(valid_scored) < 2:
        errors.append("Call 2: fewer than 2 hypotheses scored")

    return {
        "scored_hypotheses": valid_scored,
        "_valid": len(errors) == 0,
        "_call2_raw": raw_text,
        "_validation_errors": errors,
    }


def _run_call_3_select_thesis(
    llm: Any,
    hypotheses: List[Dict[str, str]],
    scored_hypotheses: List[Dict[str, Any]],
    concept_output: Dict[str, Any],
    ticker: str,
    sector: str,
) -> Dict[str, Any]:
    """Call 3: Select best hypothesis and produce final thesis.

    Returns dict with all thesis fields (same shape as original run_thesis_cot),
    or a failure dict with _valid=False.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    user_prompt = _build_selection_prompt(
        hypotheses, scored_hypotheses, concept_output, ticker, sector,
    )

    try:
        response = llm.invoke([
            SystemMessage(content=_SYSTEM_PROMPT_SELECT_THESIS),
            HumanMessage(content=user_prompt),
        ])
        raw_text = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        logger.warning("thesis_3call[call3]: LLM call failed: %s", e)
        return {"_valid": False, "_call3_raw": "", "_validation_errors": [f"Call 3 LLM failed: {e}"]}

    parsed = _extract_json(raw_text)
    if parsed is None:
        logger.warning("thesis_3call[call3]: could not parse JSON")
        return {"_valid": False, "_call3_raw": raw_text, "_validation_errors": ["Call 3: could not parse JSON"]}

    # Validate — same logic as original run_thesis_cot
    errors: List[str] = []

    direction = parsed.get("earnings_direction", "")
    if direction not in _VALID_DIRECTIONS:
        errors.append(f"earnings_direction='{direction}' not in {_VALID_DIRECTIONS}")
        parsed["earnings_direction"] = "flat"

    outlook = parsed.get("fundamental_outlook", "")
    if outlook and outlook not in _VALID_OUTLOOK:
        errors.append(f"fundamental_outlook='{outlook}' not in {_VALID_OUTLOOK}")
        parsed["fundamental_outlook"] = "neutral"
    elif not outlook:
        parsed["fundamental_outlook"] = {
            "up": "bullish", "flat": "neutral", "down": "bearish"
        }.get(parsed["earnings_direction"], "neutral")

    risk_level = parsed.get("downside_risk_level", "")
    if risk_level and risk_level not in _VALID_RISK_LEVEL:
        errors.append(f"downside_risk_level='{risk_level}' not in {_VALID_RISK_LEVEL}")
        parsed["downside_risk_level"] = "moderate"
    elif not risk_level:
        health_val = parsed.get("financial_health", "")
        parsed["downside_risk_level"] = {
            "healthy": "low", "concerning": "moderate",
            "critical": "high", "insufficient_data": "moderate",
        }.get(health_val, "moderate")

    health = parsed.get("financial_health", "")
    if health not in _VALID_HEALTH:
        errors.append(f"financial_health='{health}' not in {_VALID_HEALTH}")
        parsed["financial_health"] = "insufficient_data"

    valuation = parsed.get("valuation_assessment", "")
    if valuation not in _VALID_VALUATION:
        errors.append(f"valuation_assessment='{valuation}' not in {_VALID_VALUATION}")
        parsed["valuation_assessment"] = "insufficient_data"

    parsed["earnings_direction_confidence"] = _safe_confidence(
        parsed.get("earnings_direction_confidence", 0)
    )

    if not parsed.get("thesis_text", "").strip():
        errors.append("thesis_text is empty")

    if not isinstance(parsed.get("key_risks"), list):
        parsed["key_risks"] = []
        errors.append("key_risks was not a list — reset to []")

    result = {**parsed}
    result["_call3_raw"] = raw_text
    result["_valid"] = len(errors) == 0
    result["_validation_errors"] = errors

    if errors:
        logger.warning("thesis_3call[call3]: validation issues: %s", errors)

    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_thesis_cot_3call(
    llm: Any,
    evidence_pack: Dict[str, Any],
    concept_output: Dict[str, Any],
) -> Dict[str, Any]:
    """Run Stage 3 via three sequential LLM calls (competing-hypotheses H&P).

    Drop-in replacement for ``thesis_cot.run_thesis_cot()``. Produces the
    same output dict shape so ``_build_full_report()`` needs no changes.

    Flow:
      Call 1 — Generate 2-4 competing hypotheses (different directions)
      Call 2 — Map evidence FOR/AGAINST each; score each 0-100
      Call 3 — Select best-supported hypothesis; produce full thesis

    Fallback chain:
      - Call 1 fails → return failure dict (no hypotheses to evaluate)
      - Call 2 fails → return failure dict with hypotheses preserved
      - Call 3 fails → return partial dict with hypotheses + scores for audit

    All three calls use the same deep_thinking_llm with temperature=0 + seed=42
    (inherited from the LLM instance constructed in TradingAgentsGraph).
    """
    ticker = evidence_pack.get("ticker", "")
    sector = evidence_pack.get("sector", "operational")

    all_raw: Dict[str, str] = {}
    all_errors: List[str] = []

    # ── Call 1: Competing Hypotheses ─────────────────────────────────────────
    call1 = _run_call_1_hypotheses(llm, evidence_pack)
    all_raw["_call1_raw"] = call1.get("_call1_raw", "")

    if not call1.get("_valid"):
        all_errors.extend(call1.get("_validation_errors", []))
        return {
            "_stage3_raw": json.dumps(all_raw),
            "_valid": False,
            "_validation_errors": all_errors,
            "_thesis_mode": "3call",
        }

    hypotheses = call1["hypotheses"]
    logger.debug(
        "thesis_3call[%s]: Call 1 complete — %d competing hypotheses generated",
        ticker, len(hypotheses),
    )

    # ── Call 2: Evidence Mapping & Scoring ───────────────────────────────────
    call2 = _run_call_2_evidence_map(llm, evidence_pack, concept_output, hypotheses)
    all_raw["_call2_raw"] = call2.get("_call2_raw", "")

    if not call2.get("_valid"):
        all_errors.extend(call2.get("_validation_errors", []))
        return {
            "hypotheses": hypotheses,
            "_stage3_raw": json.dumps(all_raw),
            "_valid": False,
            "_validation_errors": all_errors,
            "_thesis_mode": "3call",
        }

    scored_hypotheses = call2["scored_hypotheses"]
    logger.debug(
        "thesis_3call[%s]: Call 2 complete — hypotheses scored: %s",
        ticker,
        {s["id"]: s["evidence_support_score"] for s in scored_hypotheses},
    )

    # ── Call 3: Selection & Thesis Synthesis ─────────────────────────────────
    call3 = _run_call_3_select_thesis(
        llm, hypotheses, scored_hypotheses, concept_output, ticker, sector,
    )
    all_raw["_call3_raw"] = call3.get("_call3_raw", "")

    # Build result with the same shape as original run_thesis_cot output
    result = {**call3}

    # Derive evidence_for / evidence_against from the SELECTED hypothesis
    selected_id = result.get("selected_hypothesis_id", "")
    selected_scored = next(
        (s for s in scored_hypotheses if s["id"] == selected_id),
        scored_hypotheses[0] if scored_hypotheses else {},
    )
    result["evidence_for"] = selected_scored.get("evidence_for", [])
    result["evidence_against"] = selected_scored.get("evidence_against", [])
    result["synthesis"] = result.get("selection_rationale", "")

    # Find the winning hypothesis statement
    selected_hypothesis = next(
        (h for h in hypotheses if h["id"] == selected_id),
        hypotheses[0] if hypotheses else {},
    )
    result["hypothesis"] = selected_hypothesis.get("statement", "")

    # Preserve full competing-hypotheses audit trail
    result["competing_hypotheses"] = hypotheses
    result["scored_hypotheses"] = scored_hypotheses

    # Combine raw outputs for audit
    result["_stage3_raw"] = json.dumps(all_raw)
    result["_thesis_mode"] = "3call"

    # Final validation errors
    result["_validation_errors"] = list(call3.get("_validation_errors", []))
    result["_valid"] = call3.get("_valid", False)

    if result["_valid"]:
        logger.debug(
            "thesis_3call[%s]: Call 3 complete — selected %s (score %d)",
            ticker, selected_id, selected_scored.get("evidence_support_score", 0),
        )
    else:
        logger.warning(
            "thesis_3call[%s]: Call 3 had validation issues: %s",
            ticker, result["_validation_errors"],
        )

    return result
