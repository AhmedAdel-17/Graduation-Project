"""Shared helpers for Portfolio Assistant LLM boundary adapters.

P2 keeps LLMs at the edges: routing, extraction, strategy, what-if parsing, and
narration. This module centralizes the fragile parts those adapters share:
deterministic LLM construction, JSON extraction/repair, Eastern digit handling,
and registry-gated EGX ticker resolution.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Iterable, Optional, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, TypeAdapter, ValidationError

from tradingagents.dataflows.social_v2 import entities
from tradingagents.agents.utils.llm_failover import build_conversational_llm, safe_invoke
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.portfolio import schemas as s
from tradingagents.utils.text_preprocessor import normalize_text

logger = logging.getLogger("tradingagents.portfolio.llm_boundary")

T = TypeVar("T", bound=BaseModel)

_ARABIC_DIGITS = str.maketrans({
    "\u0660": "0", "\u0661": "1", "\u0662": "2", "\u0663": "3", "\u0664": "4",
    "\u0665": "5", "\u0666": "6", "\u0667": "7", "\u0668": "8", "\u0669": "9",
    "\u06f0": "0", "\u06f1": "1", "\u06f2": "2", "\u06f3": "3", "\u06f4": "4",
    "\u06f5": "5", "\u06f6": "6", "\u06f7": "7", "\u06f8": "8", "\u06f9": "9",
    "\u066a": "%", "\u060c": ",",
})


def normalize_user_text(text: str) -> str:
    """Normalize AR/EN text and convert Eastern Arabic numerals to ASCII."""
    return normalize_text(text or "").translate(_ARABIC_DIGITS)


def response_text(response: Any) -> str:
    """Return text from a LangChain response, a test fake, or a raw string."""
    if response is None:
        return ""
    content = getattr(response, "content", response)
    if isinstance(content, list):
        return "\n".join(str(part) for part in content)
    return str(content)


def extract_json_object(raw: str) -> dict[str, Any]:
    """Extract the first JSON object from model output.

    Accepts plain JSON or fenced JSON. Raises ``ValueError`` on malformed output.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw or "", flags=re.IGNORECASE).strip()
    cleaned = cleaned.rstrip("`").strip()
    if not cleaned:
        raise ValueError("empty LLM response")

    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError("no JSON object found in LLM response")
    obj = json.loads(match.group(0))
    if not isinstance(obj, dict):
        raise ValueError("LLM JSON root must be an object")
    return obj


def invoke_json(
    llm: Any, system_prompt: str, user_prompt: str, *, agent_name: str,
    max_retries: int = 2, retry_delay_s: float = 1.5,
) -> dict[str, Any]:
    """Invoke an LLM through failover and parse a strict JSON object.

    The conversational boundary (NVIDIA-hosted) intermittently 500s; without
    retries a single hiccup made extraction return nothing and dump the whole user
    message as an unresolved ticker. We now retry transient failures (``safe_invoke``
    counts an empty/None response as a failure too, so a truncated reply is retried),
    and raise only when every attempt is exhausted so the caller can degrade gracefully.
    """
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

    def _attempt(_exc: Any = None) -> Any:  # fallback marker for safe_invoke
        return None

    last_text = ""
    for attempt in range(1 + max_retries):
        response = safe_invoke(
            llm, messages, fallback=_attempt, agent_name=agent_name,
            max_retries=0,  # we drive the retry loop here so we can re-parse JSON
        )
        last_text = response_text(response)
        if last_text.strip():
            try:
                return extract_json_object(last_text)
            except ValueError as exc:
                logger.warning("%s: JSON parse failed (attempt %d/%d): %s",
                               agent_name, attempt + 1, 1 + max_retries, exc)
        if attempt < max_retries:
            time.sleep(retry_delay_s)
    # Exhausted — surface a clear error so the adapter degrades gracefully.
    raise ValueError(f"{agent_name}: no parseable LLM response after {1 + max_retries} attempts")


def parse_model(raw: str, model: type[T], *, fallback: T) -> T:
    """Parse a Pydantic model from an LLM JSON response with conservative fallback."""
    try:
        return model.model_validate(extract_json_object(raw))
    except (ValueError, json.JSONDecodeError, ValidationError) as exc:
        logger.warning("Failed to parse %s from LLM output: %s", model.__name__, exc)
        return fallback


def validate_union(union: Any, payload: Any) -> Any:
    """Validate a Pydantic discriminated union from raw JSON."""
    return TypeAdapter(union).validate_python(payload)


def build_boundary_llm(config: Optional[dict[str, Any]] = None) -> Any:
    """Construct the configured conversational LLM for live adapter use."""
    cfg = config or DEFAULT_CONFIG
    return build_conversational_llm(cfg, seed=int(cfg.get("llm_seed", 42)))


def canonical_ticker(symbol: str) -> str:
    """Return SYMBOL.CA for a registry symbol or already-canonical ticker."""
    return s.normalize_ticker(symbol.upper().replace(".CA", ""))


def resolve_egx_symbol(term: str, *, context: str = "") -> Optional[str]:
    """Resolve a user/LLM term to a registry-backed canonical ``SYMBOL.CA``.

    The LLM may suggest a ticker or issuer name, but this function is the only
    authority. Unknown names return ``None`` and must become a clarification.
    """
    cleaned = normalize_user_text(term).strip()
    if not cleaned:
        return None

    bare = cleaned.upper().replace(".CA", "")
    if bare in entities.SYMBOL_REGISTRY:
        return canonical_ticker(bare)

    for symbol, meta in entities.SYMBOL_REGISTRY.items():
        all_aliases: set[str] = set()
        all_aliases.update(meta.get("alts", set()))
        all_aliases.update(meta.get("en", set()))
        all_aliases.update(meta.get("ar", set()))
        normalized_aliases = {normalize_user_text(a).lower() for a in all_aliases if a}
        if cleaned.lower() in normalized_aliases or bare in {
            a.upper().replace(".CA", "") for a in meta.get("alts", set())
        }:
            return canonical_ticker(symbol)

    # Fallback: extract entities from the TERM ALONE. ``context`` is deliberately
    # NOT mixed into the extraction text — doing so let the strongest entity
    # *elsewhere in the message* hijack an unrelated term (live-test 2026-06-15:
    # "اوراسكوم ديفيلوبمنت" resolved to ETEL.CA because Telecom Egypt was the
    # dominant mention in the surrounding sentence). For a financial action a
    # wrong company is far worse than a clarification, so resolve only when the
    # term itself maps to exactly one issuer; otherwise return None.
    _ = context  # reserved (callers still pass it); intentionally unused for matching
    symbols = {
        m.symbol for m in entities.extract(cleaned)
        if not m.symbol.startswith("EGX_")
    }
    if len(symbols) == 1:
        return canonical_ticker(next(iter(symbols)))
    return None


def registry_tickers() -> set[str]:
    """Canonical ticker universe exposed to tests and validators."""
    return {canonical_ticker(symbol) for symbol in entities.SYMBOL_REGISTRY}


def unresolved_question(names: Iterable[str], language: str = "auto") -> str:
    """Build a concise clarification question for unresolved issuer names."""
    unique = [n for n in dict.fromkeys(name.strip() for name in names if name and name.strip())]
    if not unique:
        return ""
    joined = ", ".join(unique[:5])
    if language == "ar":
        return f"لم أتعرف على الرمز لهذه الأسماء: {joined}. اكتب رمز السهم في EGX مثل COMI أو FWRY."
    return f"I could not match these names to EGX tickers: {joined}. Please provide the EGX ticker symbols."


__all__ = [
    "build_boundary_llm", "canonical_ticker", "extract_json_object", "invoke_json",
    "normalize_user_text", "parse_model", "registry_tickers", "resolve_egx_symbol",
    "response_text", "unresolved_question", "validate_union",
]
