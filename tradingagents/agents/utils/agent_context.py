"""Shared context-building helpers for LangGraph agent nodes.

These helpers were extracted **verbatim** from blocks that had been copy-pasted
across the bull/bear researchers, the research manager (CIO), the trader, and the
risk manager. Each helper reproduces the previous inline behaviour exactly so the
extraction is behaviour-preserving:

* ``build_memory_query`` / ``retrieve_past_memories`` / ``format_past_memories`` —
  the "concatenate the four analyst reports → ticker-filtered vector lookup →
  join each match's ``recommendation``" pattern. The similarity threshold is
  resolved by the *caller* and passed in, because the call sites legitimately
  resolve it differently (some read ``config``, some read ``state`` then fall
  back to ``DEFAULT_CONFIG``). Centralising the threshold is deliberately left
  out of this refactor.
* ``build_macro_section`` — the deterministic EGX macro overlay, with the same
  import-guarded fallback to an empty string.
* ``parse_fenced_json`` — pull the first ```` ```json ```` fenced block out of an
  LLM response and parse it, returning ``None`` on any failure.
* ``NO_SIGNAL_PHRASES`` / ``format_sentiment_section`` — the researcher-facing
  social-sentiment formatter shared (byte-for-byte) by the bull and bear nodes.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Past-decision memory retrieval
# ---------------------------------------------------------------------------

def build_memory_query(state: Dict[str, Any]) -> str:
    """Concatenate the four analyst reports into the memory-retrieval query.

    Identical to the inline ``curr_situation`` string built by every agent node:
    ``market`` / ``sentiment`` / ``news`` / ``fundamentals`` joined by blank lines.
    """
    return (
        f'{state.get("market_report", "")}\n\n'
        f'{state.get("sentiment_report", "")}\n\n'
        f'{state.get("news_report", "")}\n\n'
        f'{state.get("fundamentals_report", "")}'
    )


def retrieve_past_memories(
    state: Dict[str, Any],
    memory,
    *,
    min_similarity: float,
    n_matches: int = 2,
) -> List[Dict[str, Any]]:
    """Ticker-filtered vector lookup for past decisions.

    ``min_similarity`` is resolved by the caller (it differs across nodes).
    """
    ticker = state.get("company_of_interest", "")
    memory_where = {"ticker": ticker} if ticker else None
    return memory.get_memories(
        build_memory_query(state),
        n_matches=n_matches,
        where=memory_where,
        min_similarity=min_similarity,
    )


def format_past_memories(
    state: Dict[str, Any],
    memory,
    *,
    min_similarity: float,
    n_matches: int = 2,
) -> str:
    """Return the matched memories' ``recommendation`` text, joined by blank lines.

    Returns an empty string when nothing matches. Callers that want a placeholder
    (e.g. the trader's ``"No past memories found."``) apply it themselves, exactly
    as before.
    """
    past_memories = retrieve_past_memories(
        state, memory, min_similarity=min_similarity, n_matches=n_matches
    )
    return "".join(rec["recommendation"] + "\n\n" for rec in past_memories)


# ---------------------------------------------------------------------------
# Macro overlay
# ---------------------------------------------------------------------------

def build_macro_section(state: Dict[str, Any]) -> str:
    """Deterministic EGX macro overlay for prompts; empty string if unavailable.

    Mirrors the inline ``try/except`` import guard used by the research manager,
    trader, and risk manager.
    """
    try:
        from tradingagents.dataflows.macro_provider import (
            format_macro_context_for_prompt,
        )

        return format_macro_context_for_prompt(state.get("macro_context"))
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Structured-output parsing
# ---------------------------------------------------------------------------

_DEFAULT_JSON_FENCE = r"```json\s*(.*?)\s*```"


def parse_fenced_json(
    text: str,
    *,
    pattern: str = _DEFAULT_JSON_FENCE,
) -> Optional[Dict[str, Any]]:
    """Extract and parse the first ```` ```json ```` fenced block from ``text``.

    Returns the parsed object, or ``None`` when no block matches or the captured
    text is not valid JSON. Matches the prior inline behaviour (``re.DOTALL`` +
    ``json.loads`` guarded by ``JSONDecodeError``/``AttributeError``).
    """
    try:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
    except (json.JSONDecodeError, AttributeError):
        return None
    return None


# ---------------------------------------------------------------------------
# Researcher social-sentiment formatting (shared by bull + bear)
# ---------------------------------------------------------------------------

NO_SIGNAL_PHRASES = (
    "insufficient data — excluded",
    "social sentiment: insufficient",
    "layer_c_status: no_signal",
)


def format_sentiment_section(
    sentiment_report: str,
    blend_result: Optional[Dict[str, Any]],
) -> str:
    """Return a researcher-safe social-sentiment context string.

    If social sentiment was excluded (NO_SIGNAL), instructs the LLM to omit it
    entirely. Otherwise surfaces the blend modifiers (confidence/size multipliers)
    with an explicit note that they affect execution only, not thesis direction.

    Extracted verbatim from the identical ``_format_sentiment_section`` in the
    bull and bear researcher modules.
    """
    report_lower = (sentiment_report or "").lower()
    is_no_signal = any(phrase in report_lower for phrase in NO_SIGNAL_PHRASES)

    if is_no_signal:
        return (
            "EXCLUDED — insufficient social data. "
            "Do NOT reference, speculate about, or include social sentiment in your thesis."
        )

    conf_mult = 1.0
    size_mult = 1.0
    blend_audit = "no_blend"
    if isinstance(blend_result, dict):
        conf_mult = float(blend_result.get("confidence_multiplier", 1.0))
        size_mult = float(blend_result.get("position_size_multiplier", 1.0))
        blend_audit = str(blend_result.get("audit", "no_blend"))

    return (
        f"{sentiment_report or 'No social sentiment data.'}\n\n"
        f"Sentiment context modifiers (execution only — do NOT use to change thesis direction):\n"
        f"  confidence×{conf_mult:.2f}  |  position-size×{size_mult:.2f}\n"
        f"  [{blend_audit}]"
    )
