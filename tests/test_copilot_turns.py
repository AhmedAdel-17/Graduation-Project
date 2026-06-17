"""P3 golden-turn tests for ``PortfolioCopilotService``.

Stubbed boundary adapters (router / extraction / strategy / what-if / narrator)
make the conversations deterministic; the deterministic core (analytics, policy
compiler, optimizer, scenarios) runs for real. The signal resolver is wired to a
no-signal query so it degrades to neutral quant-priors offline.

Coverage (roadmap P3):
  1. describe → confirm → optimize
  2. objective mid-stream → policy v2 → re-optimize (confirmation gate)
  3. what-if → compose on active → adopt
  4. QA turn touches no engine
  5. off-topic redirect
  + statelessness: a fresh service instance resumes the conversation from the store.
"""

from __future__ import annotations

import pytest

from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.copilot_service import PortfolioCopilotService
from tradingagents.portfolio.events import PAEvent
from tradingagents.portfolio.extraction import ExtractionResult
from tradingagents.portfolio.narrator import NarrationResult
from tradingagents.portfolio.signals import SignalResolver
from tradingagents.portfolio.whatif import WhatIfResult
from tradingagents.portfolio.workspace_store import JsonFileWorkspaceStore

PRICES = {"ETEL.CA": 38.0, "FWRY.CA": 12.0}


# ---------------------------------------------------------------------------
# Stub adapters
# ---------------------------------------------------------------------------

class FakeRouter:
    """Pops a scripted intent set per ``classify`` call."""

    def __init__(self, script):
        self._script = list(script)

    def classify(self, text, digest=None):
        return self._script.pop(0)


class FakeExtraction:
    def __init__(self, snapshot, *, clarification=None):
        self._snapshot = snapshot
        self._clarification = clarification

    def extract(self, text, *, conversation_id=None, language="auto"):
        block = s.ExtractedPortfolioTableBlock(
            data=s.ExtractedPortfolioTableData(
                holdings=list(self._snapshot.holdings), cash_egp=self._snapshot.cash_egp))
        return ExtractionResult(snapshot=self._snapshot, block=block,
                                clarification=self._clarification)


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
    """Echoes the blocks it is handed so AssistantMessageEvent carries them."""

    def __init__(self):
        self.calls = 0

    def narrate(self, *, analytics=None, proposal=None, diff=None, blocks=None, language="en"):
        self.calls += 1
        return NarrationResult(text="narration", blocks=list(blocks or []),
                               block_refs=[b.type for b in (blocks or [])])


class CountingPriceProvider:
    def __init__(self, prices):
        self.prices = prices
        self.calls = 0

    def __call__(self, tickers):
        self.calls += 1
        return {t: self.prices[t] for t in tickers if t in self.prices}


def _baseline_snapshot():
    return s.PortfolioSnapshot(
        cash_egp=50000.0,
        holdings=[
            s.PortfolioHolding(ticker="ETEL.CA", shares=1000, avg_cost=35.0),
            s.PortfolioHolding(ticker="FWRY.CA", shares=2000, avg_cost=10.0),
        ],
    )


def _make_service(store, *, intents, narrator=None, extraction=None, strategy=None,
                  whatif=None, price_provider=None):
    return PortfolioCopilotService(
        store,
        router=FakeRouter(intents),
        extraction=extraction or FakeExtraction(_baseline_snapshot()),
        strategy=strategy or FakeStrategy(s.InvestmentPolicy.default_policy()),
        whatif=whatif or FakeWhatIf([]),
        narrator=narrator or FakeNarrator(),
        signal_resolver=SignalResolver(query_fn=lambda t: None, max_age_days=7),
        price_provider=price_provider or CountingPriceProvider(PRICES),
        returns_provider=lambda _t: None,                    # offline → diagonal Σ
        market_cap_provider=lambda _t: ({}, list(_t)),       # offline → current-weights prior
        ratios_provider=lambda _t: {},                       # offline → no evidence views
        sentiment_provider=lambda _t: {},                    # sentiment leg off in CI
        enable_redis=False,
    )


@pytest.fixture
def store(tmp_path):
    return JsonFileWorkspaceStore(base_dir=tmp_path / "chats")


def _event_types(ctx):
    return [e.type for e in ctx.events]


# ---------------------------------------------------------------------------
# Flow 1 — describe → confirm → optimize
# ---------------------------------------------------------------------------

def test_describe_confirm_optimize(store):
    cid = store.create_conversation(user_id="local")
    svc = _make_service(store, intents=[{s.Intent.DESCRIBE_PORTFOLIO}, {s.Intent.OPTIMIZE}])

    # describe → extraction table, no baseline persisted yet
    ctx1 = svc.handle_turn(cid, "I own 1000 Telecom Egypt and 2000 Fawry, 50k cash")
    assert "extraction" in _event_types(ctx1)
    assert store.get_latest_snapshot(cid) is None

    # confirm the table → baseline persisted + confirmed
    svc.confirm_snapshot(cid, _baseline_snapshot())
    baseline = store.get_latest_snapshot(cid)
    assert baseline is not None and baseline.confirmed_by_user is True

    # optimize → assistant message with chart blocks + persisted proposal
    ctx2 = svc.handle_turn(cid, "optimize my portfolio")
    assistant = [e for e in ctx2.events if isinstance(e, s.AssistantMessageEvent)]
    assert assistant and assistant[0].blocks
    ws = store.load_workspace(cid)
    assert ws.last_proposal_id is not None
    assert store.get_proposal(ws.last_proposal_id) is not None

    # domain trace events were persisted
    logged = {e["event_type"] for e in store.get_events(cid)}
    assert PAEvent.TURN_START in logged and PAEvent.PROPOSAL_READY in logged


def test_describe_prices_weight_only_table(store):
    # A percentage-described portfolio + a stated total value must show derived
    # share counts AND a per-share price in the confirmation table *before* the
    # user confirms (not bare em-dashes) — priced from the live price provider.
    cid = store.create_conversation()
    weight_snap = s.PortfolioSnapshot(
        total_value_egp=100000.0, cash_egp=50000.0,
        holdings=[
            s.PortfolioHolding(ticker="ETEL.CA", weight_pct=60),
            s.PortfolioHolding(ticker="FWRY.CA", weight_pct=40),
        ],
    )
    prices = CountingPriceProvider(PRICES)
    svc = _make_service(store, intents=[{s.Intent.DESCRIBE_PORTFOLIO}],
                        extraction=FakeExtraction(weight_snap), price_provider=prices)

    ctx = svc.handle_turn(cid, "60% Telecom Egypt and 40% Fawry, my portfolio is 100k, 50k cash")
    extraction = [e for e in ctx.events if isinstance(e, s.ExtractionEvent)]
    assert extraction, "expected an extraction event"
    rows = {h.ticker: h for h in extraction[0].blocks[0].data.holdings}

    assert prices.calls >= 1  # the table was priced
    assert rows["ETEL.CA"].avg_cost == PRICES["ETEL.CA"]
    assert rows["ETEL.CA"].shares == round(60000 / PRICES["ETEL.CA"])  # 60% of 100k / price
    assert rows["FWRY.CA"].avg_cost == PRICES["FWRY.CA"]
    assert rows["FWRY.CA"].shares == round(40000 / PRICES["FWRY.CA"])


def test_confirm_reconciles_weight_only_with_total(store):
    # A percentage-described portfolio + a stated total value must be converted to
    # integer shares at confirm (using live prices), so it can be optimized.
    cid = store.create_conversation()
    svc = _make_service(store, intents=[])
    snap = s.PortfolioSnapshot(
        total_value_egp=100000.0, cash_egp=50000.0,
        holdings=[
            s.PortfolioHolding(ticker="ETEL.CA", weight_pct=60),
            s.PortfolioHolding(ticker="FWRY.CA", weight_pct=40),
        ],
    )
    svc.confirm_snapshot(cid, snap)

    base = store.get_latest_snapshot(cid)
    by = {h.ticker: h for h in base.holdings}
    assert by["ETEL.CA"].shares == round(60000 / PRICES["ETEL.CA"])  # 60% of 100k / price
    assert by["FWRY.CA"].shares == round(40000 / PRICES["FWRY.CA"])

    # and it now optimizes (no anchor error)
    svc_opt = _make_service(store, intents=[{s.Intent.OPTIMIZE}])
    ctx = svc_opt.handle_turn(cid, "optimize")
    assert store.load_workspace(cid).last_proposal_id is not None
    assert any(isinstance(e, s.AssistantMessageEvent) and e.blocks for e in ctx.events)


def test_optimize_before_confirm_is_gated(store):
    cid = store.create_conversation()
    svc = _make_service(store, intents=[{s.Intent.OPTIMIZE}])

    ctx = svc.handle_turn(cid, "optimize")
    # gate fires: a message, no proposal
    assert any(isinstance(e, s.AssistantMessageEvent) for e in ctx.events)
    assert store.load_workspace(cid).last_proposal_id is None
    assert PAEvent.GATE_BLOCKED in {e["event_type"] for e in store.get_events(cid)}


def test_social_v2_sentiment_reader_is_cache_only_safe():
    """WHY: the default sentiment provider must NEVER trigger a scrape inside an
    optimize turn — cache-only, returns a dict (empty when nothing is warmed).
    PHASE: c."""
    from tradingagents.portfolio.copilot_service import social_v2_sector_sentiment
    out = social_v2_sector_sentiment(["COMI.CA"], allow_pipeline=False)
    assert isinstance(out, dict)


def test_policy_uninformed_helper():
    """WHY: the completeness gate must fire only when the user stated NO goal.
    PHASE: P4 (ask-when-vague)."""
    from tradingagents.portfolio.copilot_service import _policy_is_uninformed
    assert _policy_is_uninformed(s.InvestmentPolicy.default_policy())
    informed = s.InvestmentPolicy(objective=s.Objective.GROWTH,
                                  source_spans={"objective": "I want to grow my money"})
    assert not _policy_is_uninformed(informed)


def test_optimize_nudges_for_goal_when_policy_uninformed(store):
    """WHY: optimizing on a never-stated goal should still produce a proposal BUT
    proactively ask the user for goal/horizon/risk (the design's 'ask more
    questions when vague'). PHASE: P4."""
    cid = store.create_conversation()
    _make_service(store, intents=[]).confirm_snapshot(cid, _baseline_snapshot())
    svc = _make_service(store, intents=[{s.Intent.OPTIMIZE}])
    ctx = svc.handle_turn(cid, "optimize")
    msgs = [e for e in ctx.events if isinstance(e, s.AssistantMessageEvent)]
    assert store.load_workspace(cid).last_proposal_id is not None  # still optimizes
    assert msgs and ("goal" in msgs[-1].text.lower() or "هدف" in msgs[-1].text)
    assert PAEvent.CLARIFICATION in {e["event_type"] for e in store.get_events(cid)}


def test_optimize_with_numeric_goal_emits_feasibility_flag(store):
    """WHY: a stated numeric goal must produce a GOAL_FEASIBILITY verdict attached to
    the proposal (rendered via the policy-flags block). PHASE: a."""
    cid = store.create_conversation()
    svc0 = _make_service(store, intents=[])
    svc0.confirm_snapshot(cid, _baseline_snapshot())
    svc0.update_policy(cid, updates={"goal_target_amount_egp": 250000.0,
                                     "goal_horizon_months": 12}, confirm=True)
    svc = _make_service(store, intents=[{s.Intent.OPTIMIZE}])
    ctx = svc.handle_turn(cid, "optimize")
    pid = store.load_workspace(cid).last_proposal_id
    assert pid is not None
    prop = store.get_proposal(pid)
    assert "GOAL_FEASIBILITY" in {f.code for f in prop.policy_flags}


# ---------------------------------------------------------------------------
# Flow 2 — objective mid-stream → policy v2 → re-optimize
# ---------------------------------------------------------------------------

def test_objective_then_confirm_then_optimize(store):
    cid = store.create_conversation()
    svc0 = _make_service(store, intents=[])
    svc0.confirm_snapshot(cid, _baseline_snapshot())

    # objective → a new unconfirmed policy version
    low_risk = s.InvestmentPolicy(risk_tolerance=s.RiskTolerance.LOW, version=2,
                                  confirmed_by_user=False)
    svc1 = _make_service(store, intents=[{s.Intent.OBJECTIVE}],
                         strategy=FakeStrategy(low_risk))
    ctx_obj = svc1.handle_turn(cid, "make it safer")
    assert any(isinstance(e, s.PolicyUpdateEvent) for e in ctx_obj.events)
    assert store.get_latest_policy(cid).risk_tolerance == s.RiskTolerance.LOW

    # optimize against an unconfirmed policy change → gated
    svc2 = _make_service(store, intents=[{s.Intent.OPTIMIZE}])
    ctx_gate = svc2.handle_turn(cid, "optimize now")
    assert store.load_workspace(cid).last_proposal_id is None
    assert PAEvent.GATE_BLOCKED in {e["event_type"] for e in store.get_events(cid)}

    # confirm the policy, then optimize succeeds
    svc2.update_policy(cid, confirm=True)
    svc3 = _make_service(store, intents=[{s.Intent.OPTIMIZE}])
    ctx_opt = svc3.handle_turn(cid, "optimize now")
    assert any(isinstance(e, s.AssistantMessageEvent) and e.blocks for e in ctx_opt.events)
    assert store.load_workspace(cid).last_proposal_id is not None


# ---------------------------------------------------------------------------
# Flow 3 — what-if → compose on active → adopt
# ---------------------------------------------------------------------------

def test_whatif_compose_and_adopt(store):
    cid = store.create_conversation()
    svc0 = _make_service(store, intents=[])
    svc0.confirm_snapshot(cid, _baseline_snapshot())

    # what-if: sell all FWRY (vs baseline)
    patch1 = s.ScenarioPatch(ops=[s.ClosePositionOp(ticker="FWRY.CA")], reference="baseline",
                             label="Sell all FWRY")
    svc1 = _make_service(store, intents=[{s.Intent.WHAT_IF}], whatif=FakeWhatIf([WhatIfResult(patch=patch1)]))
    ctx1 = svc1.handle_turn(cid, "what if I sell all Fawry?")

    created = [e for e in ctx1.events if isinstance(e, s.ScenarioCreatedEvent)]
    assert created
    scen1_id = created[0].scenario.scenario_id
    ws = store.load_workspace(cid)
    assert ws.active_ref == str(scen1_id)
    # hypothetical framing on the blocks
    assistant1 = [e for e in ctx1.events if isinstance(e, s.AssistantMessageEvent)][0]
    assert assistant1.scenario_id == scen1_id
    assert all(b.is_hypothetical for b in assistant1.blocks)
    assert any(b.type == "scenario_compare" for b in assistant1.blocks)

    # compose on the active scenario: also add 50k cash
    patch2 = s.ScenarioPatch(ops=[s.AddCashOp(amount_egp=50000)], reference="active",
                             label="Add 50k")
    svc2 = _make_service(store, intents=[{s.Intent.WHAT_IF}], whatif=FakeWhatIf([WhatIfResult(patch=patch2)]))
    ctx2 = svc2.handle_turn(cid, "and what if I also add 50k?")
    scen2 = [e for e in ctx2.events if isinstance(e, s.ScenarioCreatedEvent)][0].scenario
    assert scen2.parent_scenario_id == scen1_id

    # adopt the active scenario → new baseline v2, scenario promoted, siblings discarded
    svc3 = _make_service(store, intents=[{s.Intent.ADOPT_SCENARIO}])
    svc3.handle_turn(cid, "yes, let's do that")

    new_baseline = store.get_latest_snapshot(cid)
    assert new_baseline.version == 2
    assert new_baseline.confirmed_by_user is True
    assert store.get_scenario(scen2.scenario_id).status == s.ScenarioStatus.PROMOTED
    assert store.load_workspace(cid).active_ref == "baseline"


# ---------------------------------------------------------------------------
# Flow 4 — QA touches no engine
# ---------------------------------------------------------------------------

def test_qa_turn_touches_no_engine(store):
    cid = store.create_conversation()
    svc0 = _make_service(store, intents=[])
    svc0.confirm_snapshot(cid, _baseline_snapshot())

    # one optimize to create a proposal to talk about
    opt_prices = CountingPriceProvider(PRICES)
    svc_opt = _make_service(store, intents=[{s.Intent.OPTIMIZE}], price_provider=opt_prices)
    svc_opt.handle_turn(cid, "optimize")
    assert store.load_workspace(cid).last_proposal_id is not None
    proposals_before = len(store._load(cid)["proposals"])

    # QA turn: narrator runs, no optimization
    qa_prices = CountingPriceProvider(PRICES)
    narrator = FakeNarrator()
    svc_qa = _make_service(store, intents=[{s.Intent.FOLLOW_UP_QA}],
                           narrator=narrator, price_provider=qa_prices)
    ctx = svc_qa.handle_turn(cid, "why did you suggest that?")

    assert narrator.calls == 1
    assert qa_prices.calls == 0  # never priced → no engine run
    assert len(store._load(cid)["proposals"]) == proposals_before
    assert PAEvent.QA in {e["event_type"] for e in store.get_events(cid)}
    assert any(isinstance(e, s.AssistantMessageEvent) for e in ctx.events)


# ---------------------------------------------------------------------------
# Flow 5 — off-topic redirect
# ---------------------------------------------------------------------------

def test_off_topic_redirect(store):
    cid = store.create_conversation()
    prices = CountingPriceProvider(PRICES)
    svc = _make_service(store, intents=[{s.Intent.OFF_TOPIC}], price_provider=prices)

    ctx = svc.handle_turn(cid, "what's the weather in Cairo?")
    assert any(isinstance(e, s.AssistantMessageEvent) for e in ctx.events)
    assert prices.calls == 0
    assert PAEvent.OFF_TOPIC in {e["event_type"] for e in store.get_events(cid)}


# ---------------------------------------------------------------------------
# Statelessness — a fresh instance resumes from the store
# ---------------------------------------------------------------------------

def test_service_is_stateless_across_instances(store):
    cid = store.create_conversation()

    svc_a = _make_service(store, intents=[{s.Intent.DESCRIBE_PORTFOLIO}])
    svc_a.handle_turn(cid, "I own Telecom Egypt and Fawry")
    svc_a.confirm_snapshot(cid, _baseline_snapshot())

    # brand-new service instance, same store → optimize resumes the conversation
    svc_b = _make_service(store, intents=[{s.Intent.OPTIMIZE}])
    ctx = svc_b.handle_turn(cid, "optimize")
    assert any(isinstance(e, s.AssistantMessageEvent) and e.blocks for e in ctx.events)
    assert store.load_workspace(cid).last_proposal_id is not None
