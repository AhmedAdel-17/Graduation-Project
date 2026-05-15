# TradingAgents/graph/signal_processing.py

import re
import json


class SignalProcessor:
    """Processes trading signals to extract actionable decisions via regex (no LLM)."""

    # Ordered extraction patterns — checked in priority order
    _VETO_RE = re.compile(r"\bVETO\b", re.IGNORECASE)
    _JSON_ACTION_RE = re.compile(
        r'"(?:action|decision)"\s*:\s*"(BUY|SELL|HOLD)"', re.IGNORECASE
    )
    _PROPOSAL_RE = re.compile(
        r"FINAL\s+TRANSACTION\s+PROPOSAL\s*:?\s*(BUY|SELL|HOLD)", re.IGNORECASE
    )
    _BARE_RE = re.compile(r"\b(BUY|SELL|HOLD)\b", re.IGNORECASE)

    def __init__(self, quick_thinking_llm=None):
        """Accept (and ignore) an LLM argument so callers need no changes."""
        pass

    def process_signal(self, full_signal: str) -> str:
        """
        Extract BUY / SELL / HOLD from a trading signal text without an LLM call.

        Priority:
        1. VETO keyword → HOLD
        2. JSON field  "action" or "decision"
        3. "FINAL TRANSACTION PROPOSAL: <ACTION>" marker
        4. First bare BUY / SELL / HOLD word
        5. Fallback → HOLD

        Args:
            full_signal: Complete trading signal text

        Returns:
            "BUY", "SELL", or "HOLD"
        """
        if not full_signal:
            return "HOLD"

        # 1. Veto check — deterministic override
        if self._VETO_RE.search(full_signal):
            return "HOLD"

        # 2. JSON action/decision field
        m = self._JSON_ACTION_RE.search(full_signal)
        if m:
            return m.group(1).upper()

        # 3. Explicit proposal marker
        m = self._PROPOSAL_RE.search(full_signal)
        if m:
            return m.group(1).upper()

        # 4. First bare keyword
        m = self._BARE_RE.search(full_signal)
        if m:
            return m.group(1).upper()

        # 5. Safe fallback
        return "HOLD"
