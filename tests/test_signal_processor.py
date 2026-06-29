"""Regression tests for tradingagents/graph/signal_processing.py.

Locks:
  - VETO → HOLD (highest priority)
  - JSON "action"/"decision" field extraction
  - FINAL TRANSACTION PROPOSAL marker
  - Bare BUY/SELL/HOLD keyword fallback
  - Empty/None input → HOLD
  - Case insensitivity
  - Documents known limitation: negation ("do not BUY") matches BUY

Pure-unit: no LLM, no network.
"""

from __future__ import annotations

import os
import sys

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tradingagents.graph.signal_processing import SignalProcessor


@pytest.fixture
def sp() -> SignalProcessor:
    return SignalProcessor()


# ─────────────────────────────────────────────────────────────────────────────
# Priority 1: VETO → HOLD
# ─────────────────────────────────────────────────────────────────────────────


class TestVeto:
    def test_veto_overrides_buy(self, sp):
        """VETO keyword takes precedence over any BUY signal."""
        assert sp.process_signal('{"action": "BUY"} but VETO applies') == "HOLD"

    def test_veto_case_insensitive(self, sp):
        assert sp.process_signal("veto this trade") == "HOLD"

    def test_veto_with_sell(self, sp):
        """VETO overrides SELL too."""
        assert sp.process_signal("SELL immediately but VETO") == "HOLD"


# ─────────────────────────────────────────────────────────────────────────────
# Priority 2: JSON action/decision field
# ─────────────────────────────────────────────────────────────────────────────


class TestJsonExtraction:
    def test_json_action_buy(self, sp):
        assert sp.process_signal('{"action": "BUY", "confidence": 0.8}') == "BUY"

    def test_json_action_sell(self, sp):
        assert sp.process_signal('{"action": "SELL"}') == "SELL"

    def test_json_action_hold(self, sp):
        assert sp.process_signal('{"action": "HOLD"}') == "HOLD"

    def test_json_decision_field(self, sp):
        """Both "action" and "decision" keys are recognized."""
        assert sp.process_signal('{"decision": "SELL"}') == "SELL"

    def test_json_case_insensitive(self, sp):
        assert sp.process_signal('{"action": "buy"}') == "BUY"


# ─────────────────────────────────────────────────────────────────────────────
# Priority 3: FINAL TRANSACTION PROPOSAL marker
# ─────────────────────────────────────────────────────────────────────────────


class TestProposalMarker:
    def test_final_proposal_buy(self, sp):
        text = "After analysis...\nFINAL TRANSACTION PROPOSAL: BUY\nDetails follow."
        assert sp.process_signal(text) == "BUY"

    def test_final_proposal_sell(self, sp):
        assert sp.process_signal("FINAL TRANSACTION PROPOSAL: SELL") == "SELL"

    def test_final_proposal_hold(self, sp):
        assert sp.process_signal("FINAL TRANSACTION PROPOSAL: HOLD") == "HOLD"

    def test_final_proposal_case_insensitive(self, sp):
        assert sp.process_signal("Final Transaction Proposal: sell") == "SELL"


# ─────────────────────────────────────────────────────────────────────────────
# Priority 4: Bare keyword
# ─────────────────────────────────────────────────────────────────────────────


class TestBareKeyword:
    def test_bare_buy(self, sp):
        assert sp.process_signal("I recommend BUY for this ticker") == "BUY"

    def test_bare_sell(self, sp):
        assert sp.process_signal("recommendation is to SELL") == "SELL"

    def test_bare_hold(self, sp):
        assert sp.process_signal("HOLD the position for now") == "HOLD"

    def test_first_keyword_wins(self, sp):
        """When multiple bare keywords exist, the first match wins."""
        result = sp.process_signal("BUY now or SELL later")
        assert result == "BUY"


# ─────────────────────────────────────────────────────────────────────────────
# Priority 5: Fallback → HOLD
# ─────────────────────────────────────────────────────────────────────────────


class TestFallback:
    def test_empty_string(self, sp):
        assert sp.process_signal("") == "HOLD"

    def test_none_input(self, sp):
        assert sp.process_signal(None) == "HOLD"

    def test_no_action_keywords(self, sp):
        assert sp.process_signal("This analysis is inconclusive.") == "HOLD"


# ─────────────────────────────────────────────────────────────────────────────
# Known limitations (document current behavior)
# ─────────────────────────────────────────────────────────────────────────────


class TestKnownLimitations:
    @pytest.mark.xfail(
        reason="Known bug: bare regex \\b(BUY|SELL|HOLD)\\b has no negation handling. "
               "Will pass once negation-aware parsing is added.",
        strict=True,
    )
    def test_negation_buy_should_not_match(self, sp):
        """'do not BUY' should NOT produce BUY — it should produce HOLD."""
        result = sp.process_signal("I would recommend you do not BUY this stock")
        assert result == "HOLD"

    @pytest.mark.xfail(
        reason="Known bug: bare regex \\b(BUY|SELL|HOLD)\\b has no negation handling. "
               "Will pass once negation-aware parsing is added.",
        strict=True,
    )
    def test_negation_sell_should_not_match(self, sp):
        """'do not SELL' should NOT produce SELL — it should produce HOLD."""
        result = sp.process_signal("do not SELL at this price")
        assert result == "HOLD"


# ─────────────────────────────────────────────────────────────────────────────
# Constructor
# ─────────────────────────────────────────────────────────────────────────────


class TestConstructor:
    def test_accepts_and_ignores_llm(self):
        """Constructor accepts an LLM argument for backward compatibility but ignores it."""
        sp = SignalProcessor(quick_thinking_llm="fake_llm")
        assert sp.process_signal("BUY") == "BUY"
