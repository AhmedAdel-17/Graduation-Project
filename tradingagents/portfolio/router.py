"""Intent router for Portfolio Assistant turns.

The router is a quick conversational LLM boundary. It emits a set of controlled
``schemas.Intent`` values; the service dispatcher stays deterministic after
that. A conservative keyword fallback keeps the copilot usable in tests and
provider-degraded demos.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.llm_boundary import build_boundary_llm, invoke_json, normalize_user_text

logger = logging.getLogger("tradingagents.portfolio.router")

ROUTER_PROMPT = """\
You route a Portfolio Assistant user message.

Return ONLY JSON:
{"intents": ["describe_portfolio" | "objective" | "optimize" | "what_if" | "adopt_scenario" | "follow_up_qa" | "off_topic", ...]}

Rules:
- Multi-label is allowed.
- describe_portfolio: user states holdings, cash, percentages, shares, prices.
- objective: user states goal, horizon, risk appetite, income preference, exclusions.
- optimize: user asks for a rebalancing proposal or best allocation.
- what_if: user asks hypothetical changes ("what if", "suppose", "tab law").
- adopt_scenario: user wants to accept/promote a scenario.
- follow_up_qa: portfolio-related question that needs no recalculation.
- off_topic: not about portfolio, EGX stocks, risk, goals, or scenarios.
"""


class IntentRouter:
    """Classify a message into a set of Portfolio Assistant intents."""

    def __init__(self, llm: Optional[Any] = None, config: Optional[dict[str, Any]] = None) -> None:
        self.config = config or DEFAULT_CONFIG
        self.llm = llm if llm is not None else build_boundary_llm(self.config)

    def classify(self, text: str, workspace_digest: s.WorkspaceDigest | str | None = None) -> set[s.Intent]:
        """Return one or more enum-constrained intents."""
        cleaned = normalize_user_text(text)
        digest = workspace_digest.model_dump(mode="json") if isinstance(workspace_digest, s.WorkspaceDigest) else workspace_digest
        try:
            payload = invoke_json(
                self.llm,
                ROUTER_PROMPT,
                f"WORKSPACE DIGEST:\n{digest or 'none'}\n\nUSER MESSAGE:\n{cleaned}",
                agent_name="portfolio.intent_router",
            )
            intents = {
                s.Intent(str(raw))
                for raw in payload.get("intents", [])
                if str(raw) in {intent.value for intent in s.Intent}
            }
            if intents:
                return intents
        except Exception as exc:
            logger.warning("IntentRouter LLM path failed, using fallback: %s", exc)
        return self._fallback(cleaned)

    @staticmethod
    def _fallback(text: str) -> set[s.Intent]:
        low = text.lower()
        intents: set[s.Intent] = set()
        if re.search(r"\b(what if|suppose|hypothetical|scenario|if i|sell all|add cash)\b", low) or any(
            term in text for term in ("لو", "افترض", "طب لو", "سيناريو")
        ):
            intents.add(s.Intent.WHAT_IF)
        if re.search(r"\b(adopt|accept|promote|make.*baseline|use this)\b", low) or any(
            term in text for term in ("اعتمد", "وافق", "خليه الأساسي")
        ):
            intents.add(s.Intent.ADOPT_SCENARIO)
        if re.search(r"\b(optimi[sz]e|rebalance|proposal|allocate|best portfolio|what should i buy)\b", low) or any(
            term in text for term in ("وزع", "اعادة توازن", "اقترح", "اشتري ايه")
        ):
            intents.add(s.Intent.OPTIMIZE)
        if re.search(r"\b(risk|safe|safer|safest|growth|income|dividend|horizon|months?|years?|exclude|avoid|objective)\b", low) or any(
            term in text for term in ("مخاطرة", "آمن", "امن", "نمو", "دخل", "توزيعات", "شهور", "سنة", "استبعد")
        ):
            intents.add(s.Intent.OBJECTIVE)
        if re.search(r"\b(own|holding|portfolio|shares?|stock|cash|egp|%|bought|avg|average)\b", low) or any(
            term in text for term in ("عندي", "محفظ", "سهم", "كاش", "جنيه", "اشتريت")
        ):
            intents.add(s.Intent.DESCRIBE_PORTFOLIO)
        if not intents:
            if re.search(r"\b(why|explain|show|tell me|compare|because)\b", low) or any(
                term in text for term in ("ليه", "اشرح", "قارن")
            ):
                intents.add(s.Intent.FOLLOW_UP_QA)
            else:
                intents.add(s.Intent.OFF_TOPIC)
        if s.Intent.OFF_TOPIC in intents and len(intents) > 1:
            intents.remove(s.Intent.OFF_TOPIC)
        return intents


__all__ = ["IntentRouter", "ROUTER_PROMPT"]
