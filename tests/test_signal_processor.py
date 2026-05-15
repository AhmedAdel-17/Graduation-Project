"""Unit tests for SignalProcessor regex extraction (Phase 1a).

These tests require NO live LLM. They validate that the deterministic
SignalProcessor correctly extracts BUY/SELL/HOLD from arbitrary text.
"""
import pytest
from tradingagents.graph.signal_processing import SignalProcessor


@pytest.fixture
def sp():
    return SignalProcessor()


class TestVetoPriority:
    """VETO takes highest priority — overrides any BUY/SELL found later."""

    def test_veto_returns_hold(self, sp):
        assert sp.process_signal("Risk assessment: VETO") == "HOLD"

    def test_veto_overrides_buy(self, sp):
        assert sp.process_signal('VETO\n{"action": "BUY"}') == "HOLD"

    def test_veto_case_insensitive(self, sp):
        assert sp.process_signal("The committee issued a veto.") == "HOLD"

    def test_veto_mid_text(self, sp):
        assert sp.process_signal("After review, VETO this trade immediately.") == "HOLD"


class TestJsonActionField:
    """JSON action/decision field takes second priority."""

    def test_json_action_buy(self, sp):
        assert sp.process_signal('some text {"action": "BUY"} more text') == "BUY"

    def test_json_decision_sell(self, sp):
        assert sp.process_signal('{"decision": "SELL", "conf": 0.8}') == "SELL"

    def test_json_fenced_hold(self, sp):
        text = '```json\n{"action": "HOLD", "confidence": 0.5}\n```'
        assert sp.process_signal(text) == "HOLD"

    def test_json_action_lowercase(self, sp):
        assert sp.process_signal('{"action": "buy"}') == "BUY"

    def test_json_decision_uppercase(self, sp):
        assert sp.process_signal('{"decision": "SELL"}') == "SELL"


class TestFinalTransactionProposal:
    """FINAL TRANSACTION PROPOSAL marker takes third priority."""

    def test_proposal_marker_buy(self, sp):
        assert sp.process_signal("FINAL TRANSACTION PROPOSAL: BUY") == "BUY"

    def test_proposal_marker_sell(self, sp):
        assert sp.process_signal("FINAL TRANSACTION PROPOSAL: SELL") == "SELL"

    def test_proposal_marker_bold(self, sp):
        assert sp.process_signal("FINAL TRANSACTION PROPOSAL: **SELL**") == "SELL"

    def test_proposal_marker_hold(self, sp):
        assert sp.process_signal("FINAL TRANSACTION PROPOSAL: HOLD") == "HOLD"


class TestBareKeyword:
    """Bare keyword fallback — lowest non-default priority."""

    def test_bare_buy(self, sp):
        assert sp.process_signal("I recommend we BUY this stock.") == "BUY"

    def test_bare_hold(self, sp):
        assert sp.process_signal("Overall, HOLD for now.") == "HOLD"

    def test_bare_sell(self, sp):
        assert sp.process_signal("Decision: SELL based on analysis.") == "SELL"


class TestFallback:
    """Defaults to HOLD when no signal is found."""

    def test_empty_string(self, sp):
        assert sp.process_signal("") == "HOLD"

    def test_no_signal(self, sp):
        assert sp.process_signal("The weather is nice today.") == "HOLD"

    def test_none_input(self, sp):
        assert sp.process_signal(None) == "HOLD"

    def test_whitespace_only(self, sp):
        assert sp.process_signal("   \n\t  ") == "HOLD"


class TestPriorityOrdering:
    """Higher-priority rules win over lower-priority ones."""

    def test_json_beats_proposal_marker(self, sp):
        text = '{"action": "SELL"}\nFINAL TRANSACTION PROPOSAL: BUY'
        assert sp.process_signal(text) == "SELL"

    def test_veto_beats_json(self, sp):
        text = 'VETO\n{"action": "BUY", "confidence": 0.9}'
        assert sp.process_signal(text) == "HOLD"


class TestLocalization:
    """Arabic text should not interfere with English signal extraction."""

    def test_arabic_text_with_buy(self, sp):
        assert sp.process_signal("السهم ممتاز BUY now") == "BUY"

    def test_arabic_text_with_json(self, sp):
        assert sp.process_signal('تحليل السهم: {"action": "SELL"}') == "SELL"

    def test_pure_arabic_no_signal(self, sp):
        # No English BUY/SELL/HOLD keyword — should fall back to HOLD
        result = sp.process_signal("السهم في وضع جيد للاستثمار على المدى الطويل")
        assert result == "HOLD"
