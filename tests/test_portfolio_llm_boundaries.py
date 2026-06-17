"""P2 smoke tests for router, narrator, and conversational LLM plumbing."""

from __future__ import annotations

import json
from types import SimpleNamespace

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.narrator import AdvisorNarrator
from tradingagents.portfolio.router import IntentRouter


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def invoke(self, _messages):
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


def test_conversational_llm_defaults_to_nvidia_gpt_oss():
    assert DEFAULT_CONFIG["conversational_provider"] == "nvidia"
    assert "gpt-oss" in DEFAULT_CONFIG["conversational_llm"].lower()


def test_router_returns_enum_set_from_mocked_llm():
    router = IntentRouter(llm=FakeLLM({"intents": ["describe_portfolio", "objective"]}))
    intents = router.classify("I own CIB and want low risk")

    assert intents == {s.Intent.DESCRIBE_PORTFOLIO, s.Intent.OBJECTIVE}


def test_router_fallback_is_multilabel():
    intents = IntentRouter(llm=FakeLLM({"intents": []})).classify(
        "I own 20% CIB and want a safer rebalance proposal"
    )

    assert {s.Intent.DESCRIBE_PORTFOLIO, s.Intent.OBJECTIVE, s.Intent.OPTIMIZE} <= intents


def test_narrator_fallback_gives_reasons_and_next_steps():
    # When the LLM fails, the deterministic fallback must still tell the user WHY
    # and WHAT to do next — not a bare "here is the proposal".
    proposal = s.OptimizationProposal(
        snapshot_id=1, policy_version=1,
        actions=[
            s.RebalanceAction(ticker="ETEL.CA", side=s.TradeSide.SELL, shares=216,
                              price_used=90.69, est_value_egp=19589.0,
                              current_weight_pct=42.9, target_weight_pct=15.0,
                              rationale="Trimming overweight (43% → 15%) ... risk profile."),
            s.RebalanceAction(ticker="TMGH.CA", side=s.TradeSide.BUY, shares=5,
                              price_used=95.3, est_value_egp=477.0,
                              current_weight_pct=14.3, target_weight_pct=15.0,
                              rationale="Topping up ... diversify ... risk profile."),
        ],
        current_weights={"ETEL.CA": 42.9, "TMGH.CA": 14.3},
        target_weights={"ETEL.CA": 15.0, "TMGH.CA": 15.0},
        expected_return_view_annual=0.0, expected_vol_before=0.2, expected_vol_after=0.15,
        hhi_before=0.3, hhi_after=0.15, est_total_cost_egp=41.0, est_turnover_pct=29.4,
        solver_status=s.SolverStatus.OPTIMAL, engine_version="test",
    )

    class BoomLLM:
        def invoke(self, _):
            raise RuntimeError("llm down")

    res = AdvisorNarrator(llm=BoomLLM()).narrate(proposal=proposal, language="en")
    assert "ETEL.CA" in res.text and "43% to 15%" in res.text
    assert "Next steps" in res.text and "Adopt" in res.text and "T+2" in res.text
    assert "not an order" in res.text

    res_ar = AdvisorNarrator(llm=BoomLLM()).narrate(proposal=proposal, language="ar")
    assert "الخطوات الجاية" in res_ar.text and "Adopt" in res_ar.text


def test_narrator_orders_only_provided_blocks():
    blocks = [
        s.RiskPanelBlock(data=s.RiskPanelData(hhi_before=0.4)),
        s.AllocationDonutBlock(data=s.AllocationDonutData(slices=[], total_egp=1000)),
    ]
    narrator = AdvisorNarrator(llm=FakeLLM({
        "text": "Review this proposal.",
        "block_refs": ["allocation_donut", "risk_panel", "made_up_block"],
    }))
    result = narrator.narrate(blocks=blocks, language="en")

    assert result.text == "Review this proposal."
    assert [block.type for block in result.blocks] == ["allocation_donut", "risk_panel"]
