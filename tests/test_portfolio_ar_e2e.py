"""P9 Arabic end-to-end + degradation drills for the Portfolio Assistant.

Service-level (stubbed adapters, JSON store — no Postgres, no LLM, no network):
exercises the Egyptian-Arabic conversation path including Eastern-numeral language
detection, an unlisted-name clarification, the AR-localized confirmation gate, and
the stale-signal → neutral quant-prior degradation disclosure.

The deep Arabic NLU itself lives in the P2 boundary adapters (separately tested
with AR fixtures); here we verify the *orchestration* speaks Arabic end-to-end.
"""

from __future__ import annotations

import pytest

from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.copilot_service import PortfolioCopilotService
from tradingagents.portfolio.extraction import ExtractionResult
from tradingagents.portfolio.narrator import NarrationResult
from tradingagents.portfolio.signals import SignalResolver
from tradingagents.portfolio.whatif import WhatIfResult
from tradingagents.portfolio.workspace_store import JsonFileWorkspaceStore

PRICES = {"ETEL.CA": 38.0, "FWRY.CA": 12.0}


def _has_arabic(text: str) -> bool:
    return any("؀" <= ch <= "ۿ" for ch in text or "")


class FakeRouter:
    def __init__(self, script):
        self._script = list(script)

    def classify(self, text, digest=None):
        return self._script.pop(0)


class FakeExtraction:
    def __init__(self, snapshot, clarification=None):
        self._snapshot = snapshot
        self._clarification = clarification

    def extract(self, text, *, conversation_id=None, language="auto"):
        block = s.ExtractedPortfolioTableBlock(
            data=s.ExtractedPortfolioTableData(
                holdings=list(self._snapshot.holdings),
                cash_egp=self._snapshot.cash_egp,
                unresolved_names=["بنك مصر"],
            )
        )
        return ExtractionResult(snapshot=self._snapshot, block=block, clarification=self._clarification)


class FakeStrategy:
    def __init__(self, policy):
        self._policy = policy

    def infer_policy(self, text, *, current_policy=None, version=None):
        return self._policy


class FakeWhatIf:
    def __init__(self, results):
        self._results = list(results)

    def interpret(self, text, digest=None, *, language="auto"):
        return self._results.pop(0)


class FakeNarrator:
    def narrate(self, *, analytics=None, proposal=None, diff=None, blocks=None, language="en"):
        return NarrationResult(text="تم" if language == "ar" else "ok", blocks=list(blocks or []))


def _baseline():
    return s.PortfolioSnapshot(
        cash_egp=50000.0,
        holdings=[
            s.PortfolioHolding(ticker="ETEL.CA", shares=1000, avg_cost=35.0, name_raw="المصرية للاتصالات"),
            s.PortfolioHolding(ticker="FWRY.CA", shares=2000, avg_cost=10.0, name_raw="فوري"),
        ],
    )


def _service(store, *, intents, strategy=None, whatif=None, query_fn=None):
    return PortfolioCopilotService(
        store,
        router=FakeRouter(intents),
        extraction=FakeExtraction(
            _baseline(),
            clarification=s.ClarificationEvent(question="قصدك بنك إيه؟", missing=["ticker:بنك مصر"]),
        ),
        strategy=strategy or FakeStrategy(s.InvestmentPolicy.default_policy()),
        whatif=whatif or FakeWhatIf([]),
        narrator=FakeNarrator(),
        signal_resolver=SignalResolver(query_fn=query_fn or (lambda t: None), max_age_days=7),
        price_provider=lambda ts: {t: PRICES[t] for t in ts if t in PRICES},
        returns_provider=lambda _t: None,
        market_cap_provider=lambda _t: ({}, list(_t)),
        ratios_provider=lambda _t: {},
        sentiment_provider=lambda _t: {},
        enable_redis=False,
    )


@pytest.fixture
def store(tmp_path):
    return JsonFileWorkspaceStore(base_dir=tmp_path / "chats")


def test_eastern_numeral_text_detects_arabic():
    # ٢٠٪ (Eastern Arabic numerals) must be seen as Arabic by language detection.
    assert _has_arabic("عندي ٢٠٪ في المصرية للاتصالات")


def test_arabic_describe_yields_clarification_for_unlisted_name(store):
    cid = store.create_conversation()
    svc = _service(store, intents=[{s.Intent.DESCRIBE_PORTFOLIO}])

    ctx = svc.handle_turn(cid, "عندي ٢٠٪ في المصرية للاتصالات وكام سهم فوري، و بنك مصر")

    assert ctx.language == "ar"
    assert any(isinstance(e, s.ExtractionEvent) for e in ctx.events)
    clar = [e for e in ctx.events if isinstance(e, s.ClarificationEvent)]
    assert clar and "ticker:بنك مصر" in clar[0].missing


def test_arabic_optimize_before_confirm_is_gated_in_arabic(store):
    cid = store.create_conversation()
    svc = _service(store, intents=[{s.Intent.OPTIMIZE}])

    ctx = svc.handle_turn(cid, "حسّن لي المحفظة")

    msg = [e for e in ctx.events if isinstance(e, s.AssistantMessageEvent)]
    assert msg, "gate should emit an assistant message"
    assert _has_arabic(msg[0].text), "gate message must be localized to Arabic"
    assert store.load_workspace(cid).last_proposal_id is None


def test_arabic_full_golden_conversation(store):
    cid = store.create_conversation()

    # describe (AR) → confirm
    svc_d = _service(store, intents=[{s.Intent.DESCRIBE_PORTFOLIO}])
    svc_d.handle_turn(cid, "عندي المصرية للاتصالات وفوري")
    svc_d.confirm_snapshot(cid, _baseline(), language="ar")
    assert store.get_latest_snapshot(cid).confirmed_by_user is True

    # objective (AR safer) → confirm policy
    low = s.InvestmentPolicy(risk_tolerance=s.RiskTolerance.LOW, version=2, confirmed_by_user=False)
    svc_o = _service(store, intents=[{s.Intent.OBJECTIVE}], strategy=FakeStrategy(low))
    ctx_o = svc_o.handle_turn(cid, "عايز اخليها آمن، فلوس فرح بعد ٦ شهور")
    assert any(isinstance(e, s.PolicyUpdateEvent) for e in ctx_o.events)
    svc_o.update_policy(cid, confirm=True)

    # optimize (AR) → proposal
    svc_p = _service(store, intents=[{s.Intent.OPTIMIZE}])
    ctx_p = svc_p.handle_turn(cid, "وزّع المحفظة")
    assert any(isinstance(e, s.AssistantMessageEvent) and e.blocks for e in ctx_p.events)
    assert store.load_workspace(cid).last_proposal_id is not None

    # what-if (AR "what if I sell all Fawry?") → scenario + compare
    patch = s.ScenarioPatch(ops=[s.ClosePositionOp(ticker="FWRY.CA")], reference="baseline", label="بيع فوري")
    svc_w = _service(store, intents=[{s.Intent.WHAT_IF}], whatif=FakeWhatIf([WhatIfResult(patch=patch)]))
    ctx_w = svc_w.handle_turn(cid, "طب لو بعت فوري كلها؟")
    created = [e for e in ctx_w.events if isinstance(e, s.ScenarioCreatedEvent)]
    assert created
    asst = [e for e in ctx_w.events if isinstance(e, s.AssistantMessageEvent)][0]
    assert any(b.type == "scenario_compare" and b.is_hypothetical for b in asst.blocks)


# ---------------------------------------------------------------------------
# Degradation drills
# ---------------------------------------------------------------------------

def test_stale_signals_degrade_to_quant_prior_and_disclose(store):
    """No signal store (query returns None) → optimize still produces a proposal,
    and the signal_freshness block discloses the neutral quant-prior fallback."""
    cid = store.create_conversation()
    svc0 = _service(store, intents=[])
    svc0.confirm_snapshot(cid, _baseline())

    svc = _service(store, intents=[{s.Intent.OPTIMIZE}], query_fn=lambda t: None)
    ctx = svc.handle_turn(cid, "optimize")

    asst = [e for e in ctx.events if isinstance(e, s.AssistantMessageEvent)][0]
    fresh = [b for b in asst.blocks if b.type == "signal_freshness"]
    assert fresh, "a signal_freshness block must be present"
    sigs = fresh[0].signals
    assert sigs and all(v.source == s.SignalSource.QUANT_PRIOR and v.is_stale for v in sigs)


def test_no_postgres_json_store_round_trips_full_workspace(store):
    """The no-Postgres JSON fallback persists baseline + policy + proposal so a
    fresh service instance resumes the conversation (degradation path = common)."""
    cid = store.create_conversation()
    svc_a = _service(store, intents=[])
    svc_a.confirm_snapshot(cid, _baseline())

    svc_b = _service(store, intents=[{s.Intent.OPTIMIZE}])  # brand-new instance
    svc_b.handle_turn(cid, "optimize")

    ws = store.load_workspace(cid)
    assert ws.baseline is not None and ws.baseline.confirmed_by_user
    assert ws.last_proposal_id is not None
    assert store.get_proposal(ws.last_proposal_id) is not None
