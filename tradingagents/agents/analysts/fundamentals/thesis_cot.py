"""
Stage 3: Thesis CoT — H&P Investment Thesis for EGX Fundamental Analyst.

Uses deep_thinking_llm to synthesize Stage 1 (evidence pack) and Stage 2
(concept interpretation) into a full investment thesis using the
Hypothesis-and-Prediction (H&P) prompting structure.

H&P structure:
  1. State a falsifiable hypothesis about the company's trajectory
  2. Identify the 2-3 strongest evidence items FOR the hypothesis
  3. Identify the 2-3 strongest evidence items AGAINST (devil's advocate)
  4. Synthesize a final thesis that weighs both sides
  5. Predict earnings direction (up/down/flat) with explicit confidence

This structure reduces confirmation bias and forces the LLM to engage
with counter-evidence before reaching a conclusion.

The thesis stage always produces output (even if partial). It is never a
gating condition for the pipeline — a failed thesis falls back to the
concept_cot output without blocking the pipeline from returning results.

Evidence class:
  DS [P7] Kim et al. 2024 — H&P prompting structure
  [EI] — H&P applied to EGX analyst mimicking (engineering judgment)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """\
You are a senior Portfolio Manager specializing in Egyptian Exchange (EGX) equities.
You must write an institutional-quality investment thesis using the H&P (Hypothesis and Prediction) method.

H&P METHOD — follow these steps IN ORDER:
1. HYPOTHESIS: State one falsifiable hypothesis about this company's earnings trajectory
   (e.g. "My hypothesis is that [company] will see improving operating margins because...")
2. EVIDENCE FOR: List 2-3 specific data points from the evidence pack that support the hypothesis
3. EVIDENCE AGAINST: List 2-3 specific data points that challenge or complicate the hypothesis
4. SYNTHESIS: Weigh the evidence and arrive at a final investment thesis
5. ASSESSMENT: Assess the fundamental outlook and downside risk separately
6. PREDICTION: Predict earnings direction (up / down / flat) with a confidence score 0-100

CRITICAL RULES:
1. Only cite numbers that appear in the provided evidence. No invented figures.
2. The hypothesis must be falsifiable — a testable claim about future performance.
3. Evidence AGAINST must genuinely challenge the hypothesis, not strawman objections.
4. EGX context: EGP currency exposure, ±10% daily limits, long-only, no leverage.
5. Sector-specific norms apply (e.g., bank D/E 5-10× is normal, not distress).
6. If PRIOR FUNDAMENTAL MEMORY CONTEXT is present, use it only as prior context.
   Do not treat memory as current-period evidence and do not let it override the evidence pack.
7. Output ONLY valid JSON. No prose before or after the JSON block.
8. FUNDAMENTAL OUTLOOK — Assess the company's overall fundamental trajectory:
   - "bullish": the weight of evidence supports improving or sustained strong earnings
   - "neutral": evidence is mixed, no clear directional signal, or data is insufficient
   - "bearish": the weight of evidence points to deteriorating earnings or structural problems

   DOWNSIDE RISK — Assess how likely earnings are to deteriorate, SEPARATELY from outlook:
   - "low": no major risk factors, healthy balance sheet, stable or improving margins
   - "moderate": some risk factors present (leverage, margin compression, data gaps) but not severe
   - "high": multiple severe risk factors (negative margins, extreme leverage, clear deterioration trend)

   Outlook and risk are related but NOT identical. A company can be bullish-outlook with moderate
   risk (e.g., strong growth but rising leverage). A company can be bearish-outlook with low risk
   (e.g., flat/declining earnings but healthy balance sheet).

9. EARNINGS DIRECTION — After assessing outlook and risk, predict the most likely direction:
   - Consider the evidence holistically. Do not mechanically extrapolate last year's growth rate.
   - YoY growth rates in the evidence pack describe PAST performance. The prediction is about
     the FUTURE. Past growth is informative but does not automatically continue.
   - Risks and concerns should inform your prediction ONLY when they are severe enough and
     specific enough to actually reverse the earnings trend. General risks (e.g., "leverage is
     elevated") do not automatically make earnings go down.
   - Most EGX companies show annual earnings growth in most years. Only predict "down" or "flat"
     when the fundamental evidence strongly supports it — not merely because risks exist.

   HIGH-RATE REGIME GUIDANCE (P2):
   When the evidence pack shows inflation_regime="high" (CBE policy rate > 15%):
   - Negative earnings yield spread (EY < risk-free rate) is STRUCTURALLY COMMON for most
     EGX equities in this regime. It does not carry the same bearish signal as in normal-rate
     environments and should NOT be treated as a standalone veto of equity exposure.
   - For REAL ESTATE and HOLDINGS sectors: also evaluate pricing power under inflation,
     replacement cost dynamics, balance-sheet composition, and the informational flag
     PB_UNDERSTATED_HISTORICAL_COST (book value understated by historical-cost land valuation).
   - For all sectors: the relevant comparison in high-rate regimes shifts from "EY vs CBE rate"
     to "which companies benefit most from inflation, devaluation, or real asset repricing."
   - Treat the EARNINGS_YIELD_COMPRESSED flag as one data point among many, not as a
     dominant bearish signal. Weight growth trends, margin trajectory, and sector context
     at least equally.

   MOMENTUM & RELATIVE STRENGTH GUIDANCE (P3):
   When the evidence pack includes a PRICE MOMENTUM & RELATIVE STRENGTH section:
   - Momentum is POSITIVE EVIDENCE for continuation, not a guarantee. A stock
     with strong_up momentum AND improving fundamentals has a stronger bull case
     than one with improving fundamentals alone.
   - Relative strength (outperforming EGX30) indicates the market is already
     pricing in positive expectations. This supports a bullish thesis but also
     raises the bar for entry — is the good news already in the price?
   - volume_confirmed=true alongside positive momentum is a stronger signal
     than price movement alone.
   - Do NOT treat momentum as a standalone BUY signal. It is one factor among
     fundamentals, valuation, risk, and macro context.
   - If momentum is insufficient_history, do not penalize — simply note that
     price trend data is unavailable and rely on fundamental signals.

   QUARTERLY MODE: Growth rates are QoQ (current quarter vs prior quarter). Apply:
     (a) If the [NOTE] flags a near-zero crossing (QoQ magnitude > 200%), do NOT extrapolate.
         Use multi-period trend directions and margin levels. Assign confidence <= 58.
     (b) If Net Income QoQ > +5% AND trend is "improving" AND margins stable/improving:
         predict "up" with confidence 65-75.
     (c) If Net Income QoQ < -5% AND trend is "deteriorating" AND margins worsening:
         predict "down" with confidence 65-75.
     (d) If signals conflict or QoQ unavailable: use trend directions as primary, confidence <= 58.

10. CONFIDENCE CALIBRATION — vary confidence with evidence strength:
    - 75-85: revenue AND net income BOTH move in the same direction with > ±10% change
    - 60-70: momentum is present but signals are mixed or change is moderate (5-10%)
    - 45-58: evidence is genuinely mixed, trend reversals are possible, or change is marginal
    This variation is required — do not assign the same confidence to every prediction.

OUTPUT FORMAT (strict JSON):
{
  "hypothesis": "<one falsifiable hypothesis sentence>",
  "evidence_for": ["<data point 1>", "<data point 2>"],
  "evidence_against": ["<data point 1>", "<data point 2>"],
  "synthesis": "<2-4 sentences synthesizing the evidence into a thesis>",
  "thesis_text": "<full investment thesis, 3-6 sentences, institutional quality>",
  "fundamental_outlook": "<bullish|neutral|bearish>",
  "downside_risk_level": "<low|moderate|high>",
  "earnings_direction": "<up|down|flat>",
  "earnings_direction_confidence": <integer 0-100>,
  "earnings_direction_rationale": "<1-2 sentences explaining the prediction>",
  "valuation_assessment": "<undervalued|fair_value|overvalued|insufficient_data>",
  "valuation_rationale": "<1-2 sentences>",
  "financial_health": "<healthy|concerning|critical|insufficient_data>",
  "key_risks": ["<risk1>", "<risk2>", "<risk3>"],
  "egx_specific_risks": ["<EGX-specific risk if any, else omit>"],
  "invalidation_conditions": ["<condition that would invalidate this thesis>"]
}
"""

_VALID_DIRECTIONS = {"up", "down", "flat"}
_VALID_HEALTH = {"healthy", "concerning", "critical", "insufficient_data"}
_VALID_VALUATION = {"undervalued", "fair_value", "overvalued", "insufficient_data"}
_VALID_OUTLOOK = {"bullish", "neutral", "bearish"}
_VALID_RISK_LEVEL = {"low", "moderate", "high"}


def build_thesis_prompt(
    evidence_narrative: str,
    concept_output: Dict[str, Any],
    ticker: str,
    sector: str,
) -> str:
    """
    Build the user-turn prompt for Stage 3.

    Combines Stage 1 evidence narrative with Stage 2 concept interpretation
    so the deep_thinking_llm has the full picture before writing the thesis.
    """
    # Format Stage 2 summary for injection
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
        f"STAGE 2 CONCEPT ASSESSMENT (quick analyst interpretation):\n"
        f"  Financial Health: {health} — {health_rationale}\n"
        f"  Key Metrics: {key_metrics}\n"
        f"  Growth Signal: {growth_signal} — {growth_rationale}\n"
        f"  Valuation Read: {valuation_read} — {valuation_rationale}\n"
        f"  Standout Signals: {', '.join(standout) if standout else 'none'}\n"
        f"  Risk Factors: {'; '.join(risks) if risks else 'none'}\n"
        f"  Analyst Note: {analyst_note}\n"
    )

    return (
        f"Write an H&P investment thesis for {ticker} (sector: {sector.upper()}).\n\n"
        f"--- STAGE 1 EVIDENCE PACK ---\n"
        f"{evidence_narrative}\n\n"
        f"--- STAGE 2 CONCEPT ASSESSMENT ---\n"
        f"{concept_summary}\n\n"
        f"Now apply the H&P method and return only the JSON object. No prose. No markdown fences."
    )


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Extract the first JSON object from LLM response text."""
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


def _safe_confidence(val: Any) -> int:
    """Clamp earnings_direction_confidence to 0–100 int."""
    try:
        return max(0, min(100, int(val)))
    except (TypeError, ValueError):
        return 0


def run_thesis_cot(
    llm: Any,
    evidence_pack: Dict[str, Any],
    concept_output: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Run Stage 3: Thesis CoT using deep_thinking_llm.

    Args:
      llm: LangChain LLM instance (deep_thinking_llm)
      evidence_pack: output of data_cot.build_evidence_pack()
      concept_output: output of concept_cot.run_concept_cot()

    Returns dict with all thesis fields plus:
      _stage3_raw: raw LLM response text
      _valid: bool
      _validation_errors: list[str]

    This stage never blocks the pipeline. A parse failure returns a
    partial dict with _valid=False, allowing the orchestrator to fall
    back gracefully with whatever was parsed.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    ticker = evidence_pack.get("ticker", "")
    sector = evidence_pack.get("sector", "operational")
    narrative = evidence_pack.get("narrative", "")

    user_prompt = build_thesis_prompt(narrative, concept_output, ticker, sector)

    try:
        response = llm.invoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ])
        raw_text = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        logger.warning("thesis_cot: LLM call failed: %s", e)
        return {
            "_stage3_raw": "",
            "_valid": False,
            "_validation_errors": [f"LLM call failed: {e}"],
        }

    parsed = _extract_json(raw_text)
    if parsed is None:
        logger.warning("thesis_cot: could not parse JSON from LLM response")
        return {
            "_stage3_raw": raw_text,
            "_valid": False,
            "_validation_errors": ["could not parse JSON from LLM response"],
        }

    errors: List[str] = []

    # Validate and normalize fields
    direction = parsed.get("earnings_direction", "")
    if direction not in _VALID_DIRECTIONS:
        errors.append(
            f"earnings_direction='{direction}' not in {_VALID_DIRECTIONS}"
        )
        parsed["earnings_direction"] = "flat"

    # Validate fundamental_outlook (new Phase B field)
    outlook = parsed.get("fundamental_outlook", "")
    if outlook and outlook not in _VALID_OUTLOOK:
        errors.append(f"fundamental_outlook='{outlook}' not in {_VALID_OUTLOOK}")
        parsed["fundamental_outlook"] = "neutral"
    elif not outlook:
        # Default if LLM omits it: infer from direction
        parsed["fundamental_outlook"] = {
            "up": "bullish", "flat": "neutral", "down": "bearish"
        }.get(parsed["earnings_direction"], "neutral")

    # Validate downside_risk_level (new Phase B field)
    risk_level = parsed.get("downside_risk_level", "")
    if risk_level and risk_level not in _VALID_RISK_LEVEL:
        errors.append(f"downside_risk_level='{risk_level}' not in {_VALID_RISK_LEVEL}")
        parsed["downside_risk_level"] = "moderate"
    elif not risk_level:
        # Default if LLM omits it: infer from health
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

    # Normalize confidence
    parsed["earnings_direction_confidence"] = _safe_confidence(
        parsed.get("earnings_direction_confidence", 0)
    )

    # Ensure thesis_text is present
    if not parsed.get("thesis_text", "").strip():
        errors.append("thesis_text is empty")

    # Ensure key_risks is a list
    if not isinstance(parsed.get("key_risks"), list):
        parsed["key_risks"] = []
        errors.append("key_risks was not a list — reset to []")

    result = {**parsed}
    result["_stage3_raw"] = raw_text
    result["_valid"] = len(errors) == 0
    result["_validation_errors"] = errors

    if errors:
        logger.warning("thesis_cot: validation issues (non-blocking): %s", errors)

    return result
