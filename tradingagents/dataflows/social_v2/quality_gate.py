"""Production signal-quality gate (permissive).

Ported from scripts/social_pipeline/v2/quality_gate.py.
"""

from __future__ import annotations

import re

MIN_WORDS = 3
MAX_WORDS = 150
ACTIONABLE_INTENTS = {"BUY", "SELL", "BULLISH", "BEARISH", "REACTION"}
DROP_CONTENT = {"QUESTION"}


def evaluate(text: str, intent: dict, content: dict) -> dict:
    if not text:
        return {"keep": False, "reason": "empty", "bucket": "hard_drop"}

    n_words = len(re.findall(r"\S+", text))
    if n_words < MIN_WORDS:
        return {"keep": False, "reason": f"too-short({n_words}w)", "bucket": "hard_drop"}
    if n_words > MAX_WORDS:
        return {"keep": False, "reason": f"too-long({n_words}w)", "bucket": "hard_drop"}

    label = content.get("label") or "OTHER"
    if label in DROP_CONTENT:
        return {"keep": False, "reason": f"content={label}", "bucket": "hard_drop"}

    intents = set(intent.get("intents") or [])
    if not intents or intents == {"NONE"}:
        return {"keep": True, "reason": "no-trading-intent(['NONE'])", "bucket": "downgraded"}
    if intents == {"HOLD"}:
        return {"keep": True, "reason": "hold-only", "bucket": "downgraded"}
    if not (intents & ACTIONABLE_INTENTS):
        return {
            "keep": True,
            "reason": f"non-actionable-intent({sorted(intents)})",
            "bucket": "downgraded",
        }
    if label == "ANALYSIS":
        return {"keep": True, "reason": "content=ANALYSIS", "bucket": "downgraded"}
    return {"keep": True, "reason": "ok", "bucket": "ok"}
