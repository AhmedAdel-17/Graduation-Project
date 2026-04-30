"""
Stage 2: Concept CoT — Scoped LLM Interpretation for EGX Fundamental Analyst.

Uses quick_thinking_llm to interpret the Stage 1 evidence pack and produce
a structured concept-level assessment. This stage answers:
  - What is the actual financial health of this company (LLM judgment, not heuristic)?
  - Which metrics are most significant and why?
  - What are the key risk factors the data reveals?
  - What is the growth/value signal from this data?

The LLM is constrained to the evidence pack — it must not hallucinate ratios
or reference data not in the pack. All statements must be grounded.

Output is validated before Stage 3 (Thesis CoT) is invoked.
If validation fails, the pipeline uses the Stage 1 evidence pack alone
to drive Stage 3, or falls back to the deterministic output.

Evidence class: DS [P7] Kim et al. 2024 — scoped interpretation reduces
LLM hallucination by constraining reasoning to provided evidence.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Required keys in the concept output JSON for validation
_REQUIRED_CONCEPT_KEYS = {
    "financial_health",
    "key_metrics_discussion",
    "risk_factors",
    "growth_signal",
}

_VALID_HEALTH_VALUES = {"healthy", "concerning", "critical", "insufficient_data"}


_SYSTEM_PROMPT = """\
You are a senior Fundamental Analyst specializing in Egyptian Exchange (EGX) equities.
You have been given a structured evidence pack for one EGX-listed company.
Your task is to interpret the evidence and produce a structured concept assessment.

CRITICAL RULES:
1. Only reference numbers and signals that appear in the evidence pack. Do not invent data.
2. Apply sector-specific interpretation: banks have structurally high D/E; real estate PB is understated.
3. Ignore any distress flag that is explicitly sector-inapplicable (the pack will tell you which apply).
4. EGX-specific context: EGP currency exposure, ±10% daily price limits, no short selling.
5. Output ONLY valid JSON — no prose before or after the JSON block.

OUTPUT FORMAT (strict JSON):
{
  "financial_health": "<healthy|concerning|critical|insufficient_data>",
  "financial_health_rationale": "<1-2 sentences explaining why>",
  "key_metrics_discussion": "<2-4 sentences on the most informative metrics>",
  "standout_signals": ["<signal1>", "<signal2>"],
  "risk_factors": ["<risk1>", "<risk2>", "<risk3>"],
  "growth_signal": "<positive|neutral|negative|mixed>",
  "growth_signal_rationale": "<1-2 sentences>",
  "valuation_read": "<cheap|fair|expensive|insufficient_data>",
  "valuation_rationale": "<1 sentence>",
  "coherence_notes": "<note on any data quality concerns, or 'none'>",
  "analyst_note": "<any additional observation not captured above, or 'none'>"
}
"""


def build_concept_prompt(evidence_narrative: str, ticker: str, sector: str) -> str:
    """
    Build the user-turn prompt for Stage 2.

    The system prompt provides role and rules. The user prompt provides
    the evidence narrative from Stage 1.
    """
    return (
        f"Analyze the following evidence pack for {ticker} (sector: {sector.upper()}) "
        f"and return the JSON assessment.\n\n"
        f"{evidence_narrative}\n\n"
        f"Return only the JSON object. No prose. No markdown fences."
    )


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """
    Extract the first JSON object from LLM response text.

    Handles:
      - Raw JSON response
      - JSON wrapped in ```json ... ``` markdown fences
      - JSON with leading/trailing prose
    """
    if not text:
        return None

    # Try stripping markdown fences
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding a bare JSON object
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass

    # Try the full text
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return None


def validate_concept_output(parsed: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate Stage 2 output before passing it to Stage 3.

    Validation fails if:
      1. Required keys are missing
      2. financial_health is not one of the valid values
      3. risk_factors is not a list

    Returns (is_valid, list_of_error_strings).
    """
    errors: List[str] = []

    missing = _REQUIRED_CONCEPT_KEYS - set(parsed.keys())
    if missing:
        errors.append(f"concept output missing required keys: {missing}")

    health = parsed.get("financial_health", "")
    if health not in _VALID_HEALTH_VALUES:
        errors.append(
            f"financial_health='{health}' is not one of {_VALID_HEALTH_VALUES}"
        )

    risks = parsed.get("risk_factors")
    if not isinstance(risks, list):
        errors.append(f"risk_factors must be a list, got {type(risks).__name__}")

    return (len(errors) == 0), errors


def run_concept_cot(
    llm: Any,
    evidence_pack: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Run Stage 2: Concept CoT using quick_thinking_llm.

    Args:
      llm: LangChain LLM instance (quick_thinking_llm)
      evidence_pack: output of data_cot.build_evidence_pack()

    Returns dict with:
      All parsed concept fields (financial_health, key_metrics_discussion, etc.)
      _stage2_raw: raw LLM response text
      _valid: bool
      _validation_errors: list[str]

    On any exception, returns a failure dict with _valid=False.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    ticker = evidence_pack.get("ticker", "")
    sector = evidence_pack.get("sector", "operational")
    narrative = evidence_pack.get("narrative", "")

    user_prompt = build_concept_prompt(narrative, ticker, sector)

    try:
        response = llm.invoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ])
        raw_text = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        logger.warning("concept_cot: LLM call failed: %s", e)
        return {
            "_stage2_raw": "",
            "_valid": False,
            "_validation_errors": [f"LLM call failed: {e}"],
        }

    parsed = _extract_json(raw_text)
    if parsed is None:
        logger.warning("concept_cot: could not parse JSON from LLM response")
        return {
            "_stage2_raw": raw_text,
            "_valid": False,
            "_validation_errors": ["could not parse JSON from LLM response"],
        }

    valid, errors = validate_concept_output(parsed)
    if not valid:
        logger.warning("concept_cot: validation failed: %s", errors)

    result = {**parsed}
    result["_stage2_raw"] = raw_text
    result["_valid"] = valid
    result["_validation_errors"] = errors

    return result
