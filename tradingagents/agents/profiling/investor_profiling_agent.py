"""
Strategic Investment Profiling Agent for the TradingAgents framework.

Analyzes a user's onboarding interview to determine their "Investor Velocity"
and routing logic (trigger frequency, analysis priority).

Three categories:
  INTRADAY      — checks markets multiple times/day; quick gains; high risk.
  SWING         — checks daily; holds days/weeks; moderate risk.
  POSITION_6MO  — long-term, 6+ months, fundamentals-focused; low risk.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator

from tradingagents.default_config import DEFAULT_CONFIG

logger = logging.getLogger("tradingagents.investor_profiling")

# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

VALID_CATEGORIES = {"INTRADAY", "SWING", "POSITION_6MO"}

TRIGGER_MAP: Dict[str, str] = {
    "INTRADAY":     "*/10 * * * *",   # every 10 minutes during trading hours
    "SWING":        "30 14 * * 0-4",  # 14:30 EGT, Sun-Thu (EGX market close)
    "POSITION_6MO": "0 15 * * 0",     # Sunday 15:00 EGT, weekly
}

PRIORITY_MAP: Dict[str, List[str]] = {
    "INTRADAY":     ["Technical", "Sentiment", "Fundamental"],
    "SWING":        ["Technical", "Fundamental", "Sentiment"],
    "POSITION_6MO": ["Fundamental", "Sentiment", "Technical"],
}


class InvestorProfile(BaseModel):
    investor_category: str = Field(..., description="INTRADAY | SWING | POSITION_6MO")
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    trigger_frequency: str = Field(..., description="Cron expression for data refresh")
    analysis_priority: List[str] = Field(..., description="Ordered analyst priority list")
    reasoning: str = Field(..., description="Why this category was chosen")

    @field_validator("investor_category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        v = v.upper().strip()
        if v not in VALID_CATEGORIES:
            raise ValueError(f"investor_category must be one of {VALID_CATEGORIES}")
        return v


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are the Strategic Investment Profiling Agent for the TradingAgents EGX framework.
Your job is to classify a retail or institutional investor based on their onboarding interview.

CLASSIFICATION RULES
--------------------
INTRADAY
  - Checks markets multiple times per day
  - Seeks quick, short-term gains
  - High risk tolerance
  - Example statements: "I watch the screen all day", "I want to scalp",
    "I trade every day", "high risk is fine"

SWING
  - Checks markets once a day or a few times per week
  - Holds positions for days or weeks
  - Moderate risk tolerance
  - Example statements: "I check after work", "I want to hold for a few weeks",
    "I can handle some volatility"

POSITION_6MO
  - Long-term investor; holds for 6+ months, possibly years
  - Focus on company fundamentals and dividends
  - Low / conservative risk tolerance
  - Example statements: "I invest for the long run", "I care about dividends",
    "I don't want to check every day", "capital preservation"

OUTPUT FORMAT (strict JSON, no markdown fences, no extra keys)
--------------------------------------------------------------
{
  "investor_category": "INTRADAY" | "SWING" | "POSITION_6MO",
  "confidence_score": <float 0.0-1.0>,
  "trigger_frequency": "<cron expression>",
  "analysis_priority": ["<first>", "<second>", "<third>"],
  "reasoning": "<one or two sentences>"
}

CRON EXPRESSIONS TO USE
  INTRADAY     -> "*/10 * * * *"
  SWING        -> "30 14 * * 0-4"
  POSITION_6MO -> "0 15 * * 0"

ANALYSIS PRIORITY LISTS TO USE
  INTRADAY     -> ["Technical", "Sentiment", "Fundamental"]
  SWING        -> ["Technical", "Fundamental", "Sentiment"]
  POSITION_6MO -> ["Fundamental", "Sentiment", "Technical"]

Return ONLY valid JSON. No explanation outside the JSON object.
"""


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------

class InvestorProfilingAgent:
    """
    Classifies investors based on onboarding interview text.

    Usage
    -----
    agent = InvestorProfilingAgent()
    profile = agent.classify(interview_text="I check stocks every morning ...")
    # profile.investor_category -> "SWING"
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or DEFAULT_CONFIG
        self._llm = self._build_llm()

    # ------------------------------------------------------------------
    def _build_llm(self) -> Any:
        provider = self.config.get("llm_provider", "openai").lower()
        quick_model = self.config.get("quick_think_llm", "deepseek-chat")
        backend_url = self.config.get("backend_url", "https://api.deepseek.com")

        if provider in {"openai", "ollama", "openrouter"}:
            import os as _os
            _key = _os.getenv("DEEPSEEK_API_KEY")
            if not _key:
                raise RuntimeError(
                    "DEEPSEEK_API_KEY is not set. DeepSeek is the only configured "
                    "LLM backend for this project — see .env."
                )
            return ChatOpenAI(
                model=quick_model,
                base_url=backend_url,
                api_key=_key,
                temperature=0,
                seed=42,
            )
        if provider == "anthropic":
            # Anthropic does not support `seed`; temperature=0 is best available.
            return ChatAnthropic(
                model=quick_model,
                base_url=backend_url,
                temperature=0,
            )
        if provider == "google":
            # Google does not support `seed`; temperature=0 is best available.
            return ChatGoogleGenerativeAI(
                model=quick_model,
                temperature=0,
            )
        raise ValueError(f"Unsupported LLM provider: {provider}")

    # ------------------------------------------------------------------
    def classify(self, interview_text: str) -> InvestorProfile:
        """
        Classify an investor based on their interview transcript.

        Parameters
        ----------
        interview_text : str
            Raw text from the onboarding interview or questionnaire.

        Returns
        -------
        InvestorProfile
            Validated Pydantic model with category, cron trigger, priority list,
            confidence score, and reasoning.
        """
        if not interview_text or not interview_text.strip():
            raise ValueError("interview_text must not be empty")

        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=f"USER INTERVIEW TRANSCRIPT:\n\n{interview_text.strip()}"),
        ]

        logger.info("InvestorProfilingAgent: invoking LLM for classification")
        response = self._llm.invoke(messages)
        raw = response.content.strip()
        logger.debug("InvestorProfilingAgent raw response: %s", raw[:300])

        profile = self._parse_response(raw)
        logger.info(
            "InvestorProfilingAgent: category=%s confidence=%.2f",
            profile.investor_category,
            profile.confidence_score,
        )
        return profile

    # ------------------------------------------------------------------
    def _parse_response(self, raw: str) -> InvestorProfile:
        """Extract and validate JSON from LLM output."""
        # Strip markdown code fences if present
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

        # Try to extract the first JSON object
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            logger.error("InvestorProfilingAgent: no JSON found in LLM output: %s", raw[:500])
            return self._fallback_profile("Could not parse LLM output")

        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            logger.error("InvestorProfilingAgent: JSON decode error: %s", exc)
            return self._fallback_profile(f"JSON decode error: {exc}")

        # Enforce canonical cron / priority based on the returned category
        category = str(data.get("investor_category", "SWING")).upper().strip()
        if category not in VALID_CATEGORIES:
            logger.warning("InvestorProfilingAgent: unknown category '%s', defaulting to SWING", category)
            category = "SWING"

        data["investor_category"] = category
        data["trigger_frequency"] = TRIGGER_MAP[category]
        data["analysis_priority"] = PRIORITY_MAP[category]

        # Clamp confidence
        try:
            data["confidence_score"] = max(0.0, min(1.0, float(data.get("confidence_score", 0.7))))
        except (TypeError, ValueError):
            data["confidence_score"] = 0.7

        return InvestorProfile(**data)

    # ------------------------------------------------------------------
    @staticmethod
    def _fallback_profile(reason: str) -> InvestorProfile:
        """Conservative fallback when LLM output cannot be parsed."""
        return InvestorProfile(
            investor_category="SWING",
            confidence_score=0.3,
            trigger_frequency=TRIGGER_MAP["SWING"],
            analysis_priority=PRIORITY_MAP["SWING"],
            reasoning=f"Defaulted to SWING (parse failure): {reason}",
        )
