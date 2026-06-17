"""P2 tests for natural-language what-if interpretation."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.whatif import WhatIfInterpreter


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def invoke(self, _messages):
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


CASES = [
    ("What if I sell all Fawry?", {"ops": [{"op": "CLOSE_POSITION", "ticker": "Fawry"}], "label": "Sell Fawry"}, s.ClosePositionOp),
    ("What if I add 50000 EGP?", {"ops": [{"op": "ADD_CASH", "amount_egp": 50000}], "label": "Add cash"}, s.AddCashOp),
    ("What if I reduce risk by 20%?", {"ops": [{"op": "TARGET_RISK_DELTA", "vol_delta_pct": -20}], "label": "Risk -20%"}, s.TargetRiskDeltaOp),
    ("What if COMI becomes 10%?", {"ops": [{"op": "SET_POSITION_WEIGHT", "ticker": "CIB", "weight_pct": 10}], "label": "CIB to 10%"}, s.SetPositionWeightOp),
    ("What if I exclude banks?", {"ops": [{"op": "EXCLUDE_SECTOR", "sector": "BANKS"}], "label": "Exclude banks"}, s.ExcludeSectorOp),
    ("What if I get more aggressive?", {"ops": [{"op": "OVERRIDE_POLICY", "field": "risk_tolerance", "value": "high"}], "label": "More aggressive"}, s.OverridePolicyOp),
]


@pytest.mark.parametrize("text,payload,op_cls", CASES)
def test_whatif_examples_to_expected_patches(text, payload, op_cls):
    result = WhatIfInterpreter(llm=FakeLLM(payload)).interpret(text)

    assert result.patch is not None
    assert result.clarification is None
    assert isinstance(result.patch.ops[0], op_cls)


def test_whatif_resolves_aliases_against_registry():
    payload = {"ops": [{"op": "CLOSE_POSITION", "ticker": "Fawry"}], "reference": "baseline"}
    result = WhatIfInterpreter(llm=FakeLLM(payload)).interpret("sell Fawry")

    assert isinstance(result.patch.ops[0], s.ClosePositionOp)
    assert result.patch.ops[0].ticker == "FWRY.CA"
    assert result.patch.reference == "baseline"


def test_whatif_unknown_ticker_becomes_clarification():
    payload = {"ops": [{"op": "CLOSE_POSITION", "ticker": "Unknown Co"}], "label": "Sell mystery"}
    result = WhatIfInterpreter(llm=FakeLLM(payload)).interpret("sell Unknown Co")

    assert result.patch is None
    assert result.clarification is not None
    assert "ticker:Unknown Co" in result.clarification.missing


def test_whatif_ambiguous_empty_ops_becomes_clarification():
    payload = {"ops": [], "clarification": "Which holding do you want to sell?"}
    result = WhatIfInterpreter(llm=FakeLLM(payload)).interpret("what if I sell it?")

    assert result.patch is None
    assert result.clarification.question == "Which holding do you want to sell?"


def test_whatif_invalid_policy_override_rejected():
    payload = {"ops": [{"op": "OVERRIDE_POLICY", "field": "risk_tolerance", "value": "moon"}]}
    result = WhatIfInterpreter(llm=FakeLLM(payload)).interpret("make risk moon")

    assert result.patch is None
    assert result.clarification is not None
    assert "policy:risk_tolerance" in result.clarification.missing
