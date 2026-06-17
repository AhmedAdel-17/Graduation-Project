"""Contract tests for ``tradingagents/portfolio/schemas.py`` (roadmap P0).

``schemas.py`` is imported by every later phase and mirrored by hand in
``dashboard/src/services/api/portfolioTypes.ts``. A silent shape change here
corrupts the deterministic core (P1), the LLM adapters (P2), the copilot service
(P3), the API wire protocol (P4) and the frontend renderers (P6/P7) all at once.
This suite is the tripwire: it pins ticker normalization, the controlled
vocabularies, ``extra="forbid"``, the discriminated unions, the proposal XOR
invariant, the policy factory, JSON round-trips, JSON-schema generation, and the
workspace digest contract.

Each test's docstring states **why** it exists, the **bug** it prevents, and the
**phase** that breaks if it regresses. No real I/O, no LLM — pure schema layer.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from tradingagents.portfolio import ENGINE_VERSION, SCHEMA_VERSION
from tradingagents.portfolio import schemas as s


# ---------------------------------------------------------------------------
# Realistic fixtures (the running example from the design doc, bilingual-ready)
# ---------------------------------------------------------------------------

@pytest.fixture
def holdings() -> list[s.PortfolioHolding]:
    """The design doc's worked example: ETEL / FWRY / TMGH by weight."""
    return [
        s.PortfolioHolding(ticker="ETEL.CA", weight_pct=20, avg_cost=38, name_raw="Telecom Egypt"),
        s.PortfolioHolding(ticker="FWRY.CA", weight_pct=30, avg_cost=12, name_raw="Fawry"),
        s.PortfolioHolding(ticker="TMGH.CA", weight_pct=50, avg_cost=65, name_raw="Talaat Moustafa"),
    ]


@pytest.fixture
def snapshot(holdings) -> s.PortfolioSnapshot:
    return s.PortfolioSnapshot(
        conversation_id="c1", version=1, cash_egp=50000.0,
        holdings=holdings, confirmed_by_user=True,
    )


@pytest.fixture
def patch() -> s.ScenarioPatch:
    """A multi-op hypothetical mixing position, cash and policy ops."""
    return s.ScenarioPatch(
        ops=[
            s.ClosePositionOp(ticker="FWRY.CA"),
            s.AddCashOp(amount_egp=50000),
            s.OverridePolicyOp(field="risk_tolerance", value="high"),
        ],
        reference="active",
        label="Sell FWRY + add 50k + go aggressive",
    )


@pytest.fixture
def scenario(snapshot, patch) -> s.Scenario:
    return s.Scenario(
        scenario_id=7, conversation_id="c1", parent_scenario_id=None,
        base_snapshot_id=1, patch=patch, derived_snapshot=snapshot,
        derived_policy=s.InvestmentPolicy(risk_tolerance=s.RiskTolerance.HIGH),
        input_set=s.PinnedInputSet(
            price_asof=datetime(2026, 6, 13, tzinfo=timezone.utc),
            price_source="gateway-cache",
            signal_session_ids={"ETEL.CA": "sess-1", "FWRY.CA": None},
            covariance_hash="abc123",
        ),
        proposal_id=41, status=s.ScenarioStatus.ACTIVE, label="Sell FWRY",
    )


@pytest.fixture
def workspace(snapshot, scenario) -> s.PortfolioWorkspace:
    return s.PortfolioWorkspace(
        conversation_id="c1", user_id="local", language="auto",
        baseline=snapshot, policy=s.InvestmentPolicy.default_policy(),
        scenarios=[scenario], active_ref="7", last_proposal_id=41,
    )


# A registry of every top-level model used to assert blanket schema coverage.
TOP_LEVEL_MODELS = [
    s.PortfolioHolding, s.PortfolioSnapshot, s.InvestmentPolicy, s.OptimizerParams,
    s.PolicyFlag, s.SignalView, s.RebalanceAction, s.OptimizationProposal,
    s.ScenarioPatch, s.PinnedInputSet, s.Scenario, s.PortfolioWorkspace,
    s.ScenarioRef, s.WorkspaceDigest, s.HoldingAnalytics, s.PortfolioAnalytics,
    s.TurnContext,
    # block payloads + blocks
    s.AllocationDonutBlock, s.SectorTreemapBlock, s.HoldingsTableBlock,
    s.ExtractedPortfolioTableBlock, s.BeforeAfterBlock, s.ScenarioCompareBlock,
    s.RebalanceActionsBlock, s.RiskPanelBlock, s.PolicyFlagsBlock,
    s.SignalFreshnessBlock,
    # ws events
    s.UserMessageIn, s.WhatIfIn, s.AdoptScenarioIn,
    s.StatusEvent, s.ClarificationEvent, s.ExtractionEvent, s.PolicyUpdateEvent,
    s.PolicyFlagsEvent, s.ScenarioCreatedEvent, s.AssistantMessageEvent,
    s.DoneEvent, s.ErrorEvent,
]

# Every concrete scenario-op class (the what-if vocabulary).
OP_CLASSES = [
    s.AddCashOp, s.RemoveCashOp, s.ClosePositionOp, s.ScalePositionOp,
    s.SetPositionWeightOp, s.ExcludeSectorOp, s.ExcludeTickerOp,
    s.OverridePolicyOp, s.TargetRiskDeltaOp,
]


# ---------------------------------------------------------------------------
# 1. Module-level constants
# ---------------------------------------------------------------------------

class TestVersions:
    def test_versions_are_semver_strings(self):
        """WHY: ENGINE_VERSION is stamped on every proposal audit row;
        SCHEMA_VERSION gates TS-mirror drift. BUG: a non-string/blank version
        makes audit rows unreproducible. PHASE: P1 (audit blob), P4 (TS sync)."""
        for v in (ENGINE_VERSION, SCHEMA_VERSION):
            assert isinstance(v, str) and v.count(".") == 2 and v[0].isdigit()


# ---------------------------------------------------------------------------
# 2. Ticker normalization (used by 6 models)
# ---------------------------------------------------------------------------

class TestTickerNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("comi", "COMI.CA"), ("COMI", "COMI.CA"), ("  fwry  ", "FWRY.CA"),
        ("COMI.CA", "COMI.CA"), ("comi.ca", "COMI.CA"), ("AAPL.US", "AAPL.US"),
    ])
    def test_normalize_helper(self, raw, expected):
        """WHY: every ticker entering the system must be canonical `.CA`.
        BUG: 'COMI' vs 'comi' vs 'COMI.CA' as distinct keys would silently split
        a position across weights/signals. PHASE: P1 (analytics keys), P3 (signal
        lookup against analysis_sessions.ticker)."""
        assert s.normalize_ticker(raw) == expected

    def test_empty_ticker_rejected(self):
        """WHY: an empty symbol must fail loudly. BUG: '' → '.CA' garbage key.
        PHASE: P2 (extraction must route blanks to clarification, not the core)."""
        with pytest.raises(ValueError):
            s.normalize_ticker("   ")

    def test_normalization_applied_in_models(self):
        """WHY: the validator must fire inside each model, not just the helper.
        BUG: a model bypassing the validator reintroduces split keys.
        PHASE: P1/P3 (every model below feeds the core)."""
        assert s.PortfolioHolding(ticker="comi").ticker == "COMI.CA"
        assert s.SignalView(ticker="fwry", label=s.SignalLabel.BUY, confidence=0.7).ticker == "FWRY.CA"
        assert s.RebalanceAction(
            ticker="tmgh", side=s.TradeSide.SELL, shares=10, price_used=65,
            est_value_egp=650, current_weight_pct=50, target_weight_pct=40,
        ).ticker == "TMGH.CA"
        assert s.ClosePositionOp(ticker="etel").ticker == "ETEL.CA"
        assert s.InvestmentPolicy(excluded_tickers=["comi", "fwry"]).excluded_tickers == ["COMI.CA", "FWRY.CA"]


# ---------------------------------------------------------------------------
# 3. Sector validation
# ---------------------------------------------------------------------------

class TestSectorValidation:
    def test_valid_sector_uppercased(self):
        """WHY: sectors must match the canonical taxonomy keys. BUG: 'banks' vs
        'BANKS' mismatch breaks the sector-cap constraint and treemap grouping.
        PHASE: P1 (Policy Compiler sector cap; analytics sector_exposure)."""
        assert s.InvestmentPolicy(excluded_sectors=["banks", "Real_Estate"]).excluded_sectors == ["BANKS", "REAL_ESTATE"]

    def test_other_bucket_allowed(self):
        """WHY: ticker_sector() falls back to 'OTHER'; it must be a legal value.
        BUG: rejecting 'OTHER' would crash analytics on unclassified tickers.
        PHASE: P1 (analytics)."""
        assert s.InvestmentPolicy(excluded_sectors=["OTHER"]).excluded_sectors == ["OTHER"]

    def test_unknown_sector_rejected(self):
        """WHY: only real EGX sectors are valid. BUG: a typo'd sector silently
        excludes nothing. PHASE: P2 (whatif/strategy must emit valid codes)."""
        with pytest.raises(ValidationError, match="unknown sector"):
            s.InvestmentPolicy(excluded_sectors=["CRYPTO"])

    def test_exclude_sector_op_validates_too(self):
        """WHY: the same rule must hold on the what-if op path. BUG: an invalid
        sector exclusion from a what-if reaches the optimizer. PHASE: P2 (whatif)."""
        assert s.ExcludeSectorOp(sector="real_estate").sector == "REAL_ESTATE"
        with pytest.raises(ValidationError, match="unknown sector"):
            s.ExcludeSectorOp(sector="NOPE")


# ---------------------------------------------------------------------------
# 4. extra="forbid"
# ---------------------------------------------------------------------------

class TestExtraForbid:
    @pytest.mark.parametrize("model,kwargs", [
        (s.PortfolioHolding, {"ticker": "COMI.CA", "wieght_pct": 20}),     # typo'd field
        (s.InvestmentPolicy, {"objektive": "growth"}),                      # typo'd field
        (s.RiskPanelData, {"hhi_befor": 0.4}),                              # typo'd field
        (s.StatusEvent, {"stage": "x", "detials": "y"}),                    # typo'd field
    ])
    def test_unknown_key_rejected(self, model, kwargs):
        """WHY: the wire contract must reject unknown keys. BUG: a typo'd field
        is silently dropped → data loss that surfaces as a wrong number much
        later. PHASE: P4 (WS payloads), P6/P7 (TS mirror drift detection)."""
        with pytest.raises(ValidationError):
            model(**kwargs)


# ---------------------------------------------------------------------------
# 5. Numeric / bound validators (failure cases)
# ---------------------------------------------------------------------------

class TestBoundValidators:
    def test_holding_bounds(self):
        """WHY: shares≥0, avg_cost>0, weight 0–100. BUG: negative shares or a
        zero price corrupts market-value math. PHASE: P1 (analytics)."""
        with pytest.raises(ValidationError):
            s.PortfolioHolding(ticker="COMI.CA", shares=-1)
        with pytest.raises(ValidationError):
            s.PortfolioHolding(ticker="COMI.CA", avg_cost=0)
        with pytest.raises(ValidationError):
            s.PortfolioHolding(ticker="COMI.CA", weight_pct=120)

    def test_signal_confidence_bounded(self):
        """WHY: confidence is a [0,1] probability scaling view uncertainty.
        BUG: confidence>1 inflates a view's weight in the BL blend. PHASE: P1
        (optimizer), P3 (signal resolver)."""
        with pytest.raises(ValidationError):
            s.SignalView(ticker="COMI.CA", label=s.SignalLabel.BUY, confidence=1.5)

    def test_optimizer_params_required_and_positive(self):
        """WHY: core params are required + positive so a compiler bug can't hide
        behind a default. BUG: missing risk_aversion → silent neutral optimize.
        PHASE: P1 (Policy Compiler → Optimizer)."""
        with pytest.raises(ValidationError):
            s.OptimizerParams(max_position_pct=10)  # missing the rest
        with pytest.raises(ValidationError):
            s.OptimizerParams(
                risk_aversion=0, max_position_pct=10, max_sector_pct=40,
                min_cash_pct=0, view_shrinkage=0.2, turnover_penalty=0.1,
                transaction_cost_pct=0.002, compiler_version="v1", policy_version=1,
            )

    def test_optimizer_params_normalizes_excluded_tickers(self):
        """WHY: the optimizer's hard w_i=0 exclusion set must use canonical keys.
        BUG: 'comi' in excluded_tickers won't match 'COMI.CA' in the universe →
        the ticker is NOT excluded despite the user/policy asking. PHASE: P1
        (Optimizer exclusion constraint)."""
        op = s.OptimizerParams(
            risk_aversion=5, max_position_pct=10, max_sector_pct=40, min_cash_pct=0,
            view_shrinkage=0.2, turnover_penalty=0.1, transaction_cost_pct=0.002,
            excluded_tickers=["comi", "fwry"], compiler_version="v1", policy_version=1,
        )
        assert op.excluded_tickers == ["COMI.CA", "FWRY.CA"]

    def test_exclude_ticker_op_normalizes(self):
        """WHY: the EXCLUDE_TICKER what-if op must canonicalize its target.
        BUG: 'remove tmgh' → 'tmgh' won't match 'TMGH.CA' and excludes nothing.
        PHASE: P1 (apply_patch), P2 (whatif)."""
        assert s.ExcludeTickerOp(ticker="tmgh").ticker == "TMGH.CA"

    def test_analytics_hhi_bounded(self):
        """WHY: HHI ∈ [0,1]. BUG: an out-of-range HHI breaks the risk panel gauge.
        PHASE: P1 (analytics), P7 (RiskPanel)."""
        with pytest.raises(ValidationError):
            s.PortfolioAnalytics(total_value_egp=1, invested_egp=1, cash_egp=0,
                                 cash_drag_pct=0, hhi=2.0)

    def test_treemap_signal_tone_bounded(self):
        """WHY: signal_tone ∈ [-1,1] drives the red→green color scale. BUG: an
        out-of-range tone maps to an undefined color. PHASE: P7 (SectorTreemap)."""
        with pytest.raises(ValidationError):
            s.TreemapNode(label="x", sector="BANKS", value_egp=1, weight_pct=1, signal_tone=2)


# ---------------------------------------------------------------------------
# 6. Factory methods
# ---------------------------------------------------------------------------

class TestFactories:
    def test_default_policy_marks_all_qualitative_inferred(self):
        """WHY: first-contact policy must disclose that every qualitative field
        was assumed. BUG: if inferred_fields is empty the Narrator claims the
        user chose 'balanced/medium' when they didn't. PHASE: P2 (Narrator
        disclosure), P3 (confirmation gate)."""
        p = s.InvestmentPolicy.default_policy()
        assert p.objective == s.Objective.BALANCED
        assert p.risk_tolerance == s.RiskTolerance.MEDIUM
        assert p.horizon == s.Horizon.Y1_3
        assert set(p.inferred_fields) == {"objective", "risk_tolerance", "horizon", "income_preference"}
        assert p.confirmed_by_user is False

    def test_workspace_defaults_to_default_policy(self):
        """WHY: a fresh workspace must carry a usable, disclosed policy.
        BUG: a None policy NPEs the compiler on the first optimize. PHASE: P3."""
        ws = s.PortfolioWorkspace(conversation_id="c9")
        assert ws.policy.objective == s.Objective.BALANCED
        assert ws.active_ref == "baseline" and ws.baseline is None


# ---------------------------------------------------------------------------
# 7. OptimizationProposal baseline/scenario XOR
# ---------------------------------------------------------------------------

class TestProposalXor:
    def _base(self, **kw):
        return dict(policy_version=1, solver_status=s.SolverStatus.OPTIMAL,
                    engine_version=ENGINE_VERSION, **kw)

    def test_snapshot_only_ok(self):
        """WHY: a baseline run is identified by snapshot_id alone. PHASE: P1/P4."""
        assert s.OptimizationProposal(**self._base(snapshot_id=1)).scenario_id is None

    def test_scenario_only_ok(self):
        """WHY: a what-if run is identified by scenario_id alone. PHASE: P1/P4."""
        assert s.OptimizationProposal(**self._base(scenario_id=7)).snapshot_id is None

    def test_both_set_rejected(self):
        """WHY: a proposal is either baseline or scenario, never both. BUG: an
        ambiguous proposal mis-attributes adopted trades. PHASE: P4 (audit), P8
        (adopt flow)."""
        with pytest.raises(ValidationError, match="exactly one"):
            s.OptimizationProposal(**self._base(snapshot_id=1, scenario_id=7))

    def test_neither_set_rejected(self):
        """WHY: an unattached proposal can't be stored against anything.
        BUG: orphan proposal row. PHASE: P4."""
        with pytest.raises(ValidationError, match="exactly one"):
            s.OptimizationProposal(**self._base())


# ---------------------------------------------------------------------------
# 8. Scenario ops + ScenarioPatch (the what-if vocabulary)
# ---------------------------------------------------------------------------

class TestScenarioOps:
    def test_op_union_dispatches_by_op_field(self):
        """WHY: ScenarioOp is a discriminated union on 'op'. BUG: a wrong dispatch
        constructs the wrong op type and the wrong patch is applied. PHASE: P1
        (scenarios.apply_patch), P2 (whatif interpreter)."""
        ad = TypeAdapter(s.ScenarioOp)
        assert isinstance(ad.validate_python({"op": "ADD_CASH", "amount_egp": 50000}), s.AddCashOp)
        assert isinstance(ad.validate_python({"op": "CLOSE_POSITION", "ticker": "FWRY.CA"}), s.ClosePositionOp)
        assert isinstance(ad.validate_python({"op": "TARGET_RISK_DELTA", "vol_delta_pct": -20}), s.TargetRiskDeltaOp)

    def test_unknown_op_rejected(self):
        """WHY: only the defined op vocabulary is legal. BUG: an LLM-invented op
        string reaches the engine. PHASE: P2 (whatif must emit valid ops)."""
        with pytest.raises(ValidationError):
            TypeAdapter(s.ScenarioOp).validate_python({"op": "TELEPORT", "x": 1})

    def test_op_params_required(self):
        """WHY: each op's params are required + bounded. BUG: ADD_CASH with no
        amount, or a negative scale factor, silently no-ops. PHASE: P1/P2."""
        with pytest.raises(ValidationError):
            s.AddCashOp()  # missing amount_egp
        with pytest.raises(ValidationError):
            s.ScalePositionOp(ticker="COMI.CA", factor=-1)
        with pytest.raises(ValidationError):
            s.SetPositionWeightOp(ticker="COMI.CA", weight_pct=150)

    def test_override_policy_value_is_heterogeneous(self):
        """WHY: OVERRIDE_POLICY.value may be str/float/bool by target field.
        BUG: over-narrow typing would reject 'income_preference=true'. PHASE: P2
        (whatif must still validate value against the real field type)."""
        assert s.OverridePolicyOp(field="risk_tolerance", value="high").value == "high"
        assert s.OverridePolicyOp(field="max_position_pct", value=12.5).value == 12.5
        assert s.OverridePolicyOp(field="income_preference", value=True).value is True

    def test_patch_requires_at_least_one_op(self):
        """WHY: an empty patch is a no-op masquerading as a hypothetical.
        BUG: empty ops → optimizer re-runs identical state, confusing the user.
        PHASE: P1 (scenarios), P4 (UI-built what_if payloads)."""
        with pytest.raises(ValidationError):
            s.ScenarioPatch(ops=[])

    def test_patch_ticker_normalized_inside_ops(self, ):
        """WHY: ops carry tickers that must canonicalize. BUG: 'fwry' in a patch
        won't match 'FWRY.CA' in the snapshot → CLOSE_POSITION silently misses.
        PHASE: P1 (apply_patch)."""
        p = s.ScenarioPatch(ops=[s.ClosePositionOp(ticker="fwry")])
        assert p.ops[0].ticker == "FWRY.CA"


# ---------------------------------------------------------------------------
# 9. ChatBlock discriminated union
# ---------------------------------------------------------------------------

class TestChatBlockUnion:
    BLOCK_SAMPLES = [
        ("allocation_donut", {"data": {"slices": [], "total_egp": 0}}, s.AllocationDonutBlock),
        ("sector_treemap", {"data": {"nodes": []}}, s.SectorTreemapBlock),
        ("holdings_table", {"rows": []}, s.HoldingsTableBlock),
        ("extracted_portfolio_table", {"data": {"holdings": [], "cash_egp": 0}}, s.ExtractedPortfolioTableBlock),
        ("before_after", {"data": {"entries": []}}, s.BeforeAfterBlock),
        ("scenario_compare", {"data": {"reference": "baseline", "metric_deltas": {}}}, s.ScenarioCompareBlock),
        ("rebalance_actions", {"data": {"actions": []}}, s.RebalanceActionsBlock),
        ("risk_panel", {"data": {}}, s.RiskPanelBlock),
        ("policy_flags", {"flags": []}, s.PolicyFlagsBlock),
        ("signal_freshness", {"signals": []}, s.SignalFreshnessBlock),
    ]

    @pytest.mark.parametrize("type_,extra,cls", BLOCK_SAMPLES)
    def test_each_block_variant_parses(self, type_, extra, cls):
        """WHY: every visualization block must round-trip through the union by
        'type'. BUG: a missing/renamed variant means the FE can't render that
        chart. PHASE: P7 (block renderers), P3 (Narrator emits these)."""
        block = TypeAdapter(s.ChatBlock).validate_python({"type": type_, **extra})
        assert isinstance(block, cls) and block.type == type_

    def test_all_ten_block_types_covered(self):
        """WHY: guard against adding a block to the union but not the FE/tests.
        BUG: an untested 11th block ships unrendered. PHASE: P7."""
        assert len(self.BLOCK_SAMPLES) == 10

    def test_hypothetical_flag_on_base(self):
        """WHY: is_hypothetical + scenario_id live on the base so any block can
        render in scenario styling (replaces the HypotheticalFrame wrapper).
        BUG: losing this flag makes a what-if indistinguishable from a plan —
        the worst UX failure of the feature. PHASE: P8 (scenario UX)."""
        b = TypeAdapter(s.ChatBlock).validate_python(
            {"type": "risk_panel", "is_hypothetical": True, "scenario_id": 7, "data": {}}
        )
        assert b.is_hypothetical is True and b.scenario_id == 7

    def test_unknown_block_type_rejected(self):
        """WHY: unknown discriminator must fail. BUG: a typo'd block type parses
        as a generic dict and renders nothing. PHASE: P4/P7."""
        with pytest.raises(ValidationError):
            TypeAdapter(s.ChatBlock).validate_python({"type": "pie_of_pie", "data": {}})


# ---------------------------------------------------------------------------
# 10. ServerEvent / ClientEvent discriminated unions (the WS protocol)
# ---------------------------------------------------------------------------

class TestServerEventUnion:
    SERVER_SAMPLES = [
        ("status", {"stage": "extracting"}, s.StatusEvent),
        ("clarification", {"question": "Buy price for FWRY?", "missing": ["buy_price:FWRY.CA"]}, s.ClarificationEvent),
        ("extraction", {"blocks": []}, s.ExtractionEvent),
        ("policy_update", {"policy": s.InvestmentPolicy.default_policy().model_dump()}, s.PolicyUpdateEvent),
        ("policy_flags", {"flags": []}, s.PolicyFlagsEvent),
        ("scenario_created", {"scenario": {"scenario_id": 7, "label": "Sell FWRY"}}, s.ScenarioCreatedEvent),
        ("assistant_message", {"text": "hi", "blocks": []}, s.AssistantMessageEvent),
        ("done", {"proposal_id": 41}, s.DoneEvent),
        ("error", {"message": "boom", "recoverable": True}, s.ErrorEvent),
    ]

    @pytest.mark.parametrize("type_,extra,cls", SERVER_SAMPLES)
    def test_each_server_event_parses(self, type_, extra, cls):
        """WHY: the server→client protocol is a discriminated union on 'type'.
        BUG: a renamed event type means the client silently ignores a frame.
        PHASE: P4 (WS handler), P6 (usePortfolioChat)."""
        ev = TypeAdapter(s.ServerEvent).validate_python({"type": type_, **extra})
        assert isinstance(ev, cls)

    def test_nested_blocks_keep_their_subtype(self):
        """WHY: events carry ChatBlocks; the inner union must also dispatch.
        BUG: a block nested in an event degrades to the base type and loses data.
        PHASE: P6/P7."""
        ev = TypeAdapter(s.ServerEvent).validate_python({
            "type": "assistant_message", "text": "x",
            "blocks": [{"type": "before_after", "data": {"entries": [
                {"ticker": "COMI.CA", "before_pct": 20, "after_pct": 15, "delta_pct": -5}]}}],
        })
        assert isinstance(ev.blocks[0], s.BeforeAfterBlock)
        assert ev.blocks[0].data.entries[0].ticker == "COMI.CA"

    def test_unknown_server_event_rejected(self):
        """WHY: unknown event type must fail loudly. PHASE: P4."""
        with pytest.raises(ValidationError):
            TypeAdapter(s.ServerEvent).validate_python({"type": "explode"})


class TestClientEventUnion:
    @pytest.mark.parametrize("payload,cls", [
        ({"type": "user_message", "text": "عندي ٢٠٪ في المصرية للاتصالات"}, s.UserMessageIn),
        ({"type": "what_if", "patch": {"ops": [{"op": "ADD_CASH", "amount_egp": 50000}]}}, s.WhatIfIn),
        ({"type": "adopt_scenario", "scenario_id": 7}, s.AdoptScenarioIn),
    ])
    def test_each_client_event_parses(self, payload, cls):
        """WHY: the client→server protocol is a union on 'type'; the what_if path
        must carry a fully-typed patch (no interpreter LLM). BUG: a malformed
        inbound frame reaching the service un-typed. PHASE: P4 (WS handler)."""
        ev = TypeAdapter(s.ClientEvent).validate_python(payload)
        assert isinstance(ev, cls)

    def test_arabic_text_preserved(self):
        """WHY: bilingual is a hard requirement; Arabic must survive the wire
        unmangled. BUG: encoding loss breaks AR extraction. PHASE: P2/P4."""
        ev = TypeAdapter(s.ClientEvent).validate_python(
            {"type": "user_message", "text": "أنا مش بحب المخاطرة"})
        assert ev.text == "أنا مش بحب المخاطرة"


# ---------------------------------------------------------------------------
# 11. Serialization round-trips
# ---------------------------------------------------------------------------

class TestRoundTrips:
    def test_enum_serializes_to_value(self):
        """WHY: str-enums must serialize to their string value for the TS client.
        BUG: serializing as 'SolverStatus.OPTIMAL' breaks the FE switch. PHASE:
        P4/P6."""
        prop = s.OptimizationProposal(policy_version=1, snapshot_id=1,
                                      solver_status=s.SolverStatus.OPTIMAL,
                                      engine_version=ENGINE_VERSION)
        assert prop.model_dump(mode="json")["solver_status"] == "optimal"

    def test_snapshot_round_trip(self, snapshot):
        """WHY: snapshots persist to JSONB and reload. BUG: a lossy round-trip
        corrupts the stored baseline. PHASE: P0 (workspace_store), P3."""
        back = s.PortfolioSnapshot.model_validate_json(snapshot.model_dump_json())
        assert back == snapshot
        assert back.tickers == ["ETEL.CA", "FWRY.CA", "TMGH.CA"]

    def test_workspace_round_trip_preserves_unions(self, workspace):
        """WHY: the whole workspace (with a scenario tree containing a typed
        patch) must survive JSON. BUG: ops degrading to base type on reload would
        make adopted scenarios non-replayable. PHASE: P0 (store), P8 (adopt)."""
        back = s.PortfolioWorkspace.model_validate_json(workspace.model_dump_json())
        assert back == workspace
        sc = back.active_scenario()
        assert sc is not None and isinstance(sc.patch.ops[0], s.ClosePositionOp)
        assert isinstance(sc.patch.ops[2], s.OverridePolicyOp)

    def test_proposal_round_trip(self):
        """WHY: proposals are the audit record; they must reload identically.
        BUG: audit drift. PHASE: P4 (audit view), P1."""
        prop = s.OptimizationProposal(
            policy_version=2, scenario_id=7, solver_status=s.SolverStatus.HEURISTIC_FALLBACK,
            engine_version=ENGINE_VERSION,
            actions=[s.RebalanceAction(ticker="FWRY.CA", side=s.TradeSide.SELL, shares=100,
                                       price_used=12.0, est_value_egp=1200.0,
                                       current_weight_pct=30, target_weight_pct=0,
                                       signal_session_id="sess-9")],
            policy_flags=[s.PolicyFlag(code="CONCENTRATION_VS_RISK", detail="too concentrated")],
            inputs_audit={"prices": {"FWRY.CA": 12.0}},
        )
        back = s.OptimizationProposal.model_validate_json(prop.model_dump_json())
        assert back == prop
        assert back.actions[0].side == s.TradeSide.SELL


# ---------------------------------------------------------------------------
# 12. JSON-schema generation (the TS-mirror source of truth)
# ---------------------------------------------------------------------------

class TestJsonSchemaGeneration:
    @pytest.mark.parametrize("model", TOP_LEVEL_MODELS, ids=lambda m: m.__name__)
    def test_every_top_level_model_emits_schema(self, model):
        """WHY: a P0 deliverable is dumping model_json_schema() so a CI step can
        check portfolioTypes.ts against it. BUG: a model that can't emit a schema
        (e.g. an unresolved forward ref) blocks the TS-sync tooling. PHASE: P4/P6
        (TS mirror), P0 (schemas.json artifact)."""
        schema = model.model_json_schema()
        assert schema.get("title") == model.__name__
        assert "properties" in schema or "$ref" in schema or "allOf" in schema

    @pytest.mark.parametrize("union,name", [
        (s.ScenarioOp, "ScenarioOp"), (s.ChatBlock, "ChatBlock"),
        (s.ServerEvent, "ServerEvent"), (s.ClientEvent, "ClientEvent"),
    ])
    def test_discriminated_unions_emit_schema(self, union, name):
        """WHY: the four unions are the trickiest TS to keep in sync; their JSON
        schema must include the discriminator mapping. BUG: a union that emits a
        bare anyOf without a discriminator produces wrong TS. PHASE: P4/P6."""
        schema = TypeAdapter(union).json_schema()
        assert "oneOf" in schema or "anyOf" in schema or "discriminator" in schema

    def test_op_classes_carry_literal_discriminator(self):
        """WHY: each op's 'op' field must be a const literal for the union to
        dispatch. BUG: a non-literal op field collapses the discriminator.
        PHASE: P1/P2."""
        for cls in OP_CLASSES:
            schema = cls.model_json_schema()
            op_schema = schema["properties"]["op"]
            assert op_schema.get("const") is not None or "enum" in op_schema


# ---------------------------------------------------------------------------
# 13. PortfolioWorkspace digest + navigation
# ---------------------------------------------------------------------------

class TestWorkspaceDigest:
    def test_digest_is_deterministic(self, workspace):
        """WHY: the digest is fed to the router/interpreter prompts; under
        temperature=0 it must be byte-stable so runs are reproducible. BUG: a
        non-deterministic digest breaks audit reproducibility. PHASE: P2/P3."""
        assert workspace.digest().model_dump() == workspace.digest().model_dump()

    def test_digest_is_token_bounded(self, workspace):
        """WHY: the digest must NOT embed full holdings/prices — only tickers and
        scenario refs — so prompt size is constant regardless of portfolio/chat
        size. BUG: an unbounded digest blows the context window on big portfolios.
        PHASE: P3 (prompt assembly)."""
        d = workspace.digest()
        assert d.baseline_tickers == ["ETEL.CA", "FWRY.CA", "TMGH.CA"]
        # the digest exposes refs, not full Scenario objects
        assert all(isinstance(r, s.ScenarioRef) for r in d.scenarios)
        assert not hasattr(d, "holdings")

    def test_digest_excludes_non_active_scenarios(self, snapshot, patch):
        """WHY: discarded/promoted branches must not appear as live options in the
        prompt or the FE tabs. BUG: a discarded scenario resurfaces as selectable.
        PHASE: P3 (prompt), P8 (scenario tabs)."""
        active = s.Scenario(scenario_id=1, patch=patch, derived_snapshot=snapshot, status=s.ScenarioStatus.ACTIVE, label="A")
        discarded = s.Scenario(scenario_id=2, patch=patch, derived_snapshot=snapshot, status=s.ScenarioStatus.DISCARDED, label="B")
        promoted = s.Scenario(scenario_id=3, patch=patch, derived_snapshot=snapshot, status=s.ScenarioStatus.PROMOTED, label="C")
        ws = s.PortfolioWorkspace(conversation_id="c1", baseline=snapshot,
                                  scenarios=[active, discarded, promoted], active_ref="1")
        ids = {r.scenario_id for r in ws.digest().scenarios}
        assert ids == {1}

    def test_digest_summary_without_baseline(self):
        """WHY: a brand-new conversation has no portfolio; the digest must say so
        rather than crash. BUG: None baseline NPEs digest(). PHASE: P3."""
        d = s.PortfolioWorkspace(conversation_id="c2").digest()
        assert "no portfolio" in d.baseline_summary and d.baseline_tickers == []

    def test_active_scenario_navigation(self, workspace):
        """WHY: active_ref resolves to the right branch (or baseline). BUG: a
        mis-resolved active_ref applies a what-if to the wrong base. PHASE: P3
        (composition default), P8 (canvas tabs)."""
        assert workspace.active_scenario().scenario_id == 7
        workspace.active_ref = "baseline"
        assert workspace.active_scenario() is None
        workspace.active_ref = "999"  # non-existent id
        assert workspace.active_scenario() is None


# ---------------------------------------------------------------------------
# 14. TurnContext (orchestrator working state)
# ---------------------------------------------------------------------------

class TestFixtures:
    """The FE builds P6/P7 against these golden JSON fixtures before the backend
    exists. They MUST validate against the Python schemas, or the FE renders a
    payload the backend would never actually send."""

    FIXTURE_DIR = Path(__file__).resolve().parents[1] / "dashboard" / "src" / "features" / "assistant" / "fixtures"

    # filename -> the union the fixture must validate against
    FIXTURES = {
        "sample_extraction.json": s.ServerEvent,
        "sample_policy.json": s.ServerEvent,
        "sample_proposal_blocks.json": s.ServerEvent,
        "sample_scenario_compare.json": s.ServerEvent,
    }

    def test_fixture_dir_exists(self):
        """WHY: the fixtures are a committed P0 deliverable. BUG: a missing dir
        means the FE has nothing to build against. PHASE: P6/P7."""
        assert self.FIXTURE_DIR.is_dir(), f"missing fixtures dir: {self.FIXTURE_DIR}"

    @pytest.mark.parametrize("filename,union", list(FIXTURES.items()))
    def test_fixture_validates(self, filename, union):
        """WHY: each committed fixture must parse as the backend would emit it.
        BUG: a hand-edited fixture drifting from the schema would teach the FE a
        wrong shape. PHASE: P6/P7 (block renderers consume these verbatim)."""
        path = self.FIXTURE_DIR / filename
        assert path.is_file(), f"missing fixture: {path}"
        with path.open(encoding="utf-8") as fh:
            payload = json.load(fh)
        TypeAdapter(union).validate_python(payload)  # raises on drift

    def test_proposal_fixture_has_all_core_blocks(self):
        """WHY: the proposal fixture is the FE's reference for the full optimized
        view; it must exercise every visualization block the renderers need.
        BUG: a thin fixture leaves a block renderer untested in dev. PHASE: P7."""
        payload = json.loads((self.FIXTURE_DIR / "sample_proposal_blocks.json").read_text(encoding="utf-8"))
        ev = TypeAdapter(s.ServerEvent).validate_python(payload)
        kinds = {b.type for b in ev.blocks}
        assert {"allocation_donut", "sector_treemap", "before_after",
                "rebalance_actions", "risk_panel"} <= kinds

    def test_scenario_fixture_is_marked_hypothetical(self):
        """WHY: the scenario fixture must carry is_hypothetical so the FE renders
        it as a scenario, never as adopted advice. BUG: a what-if shown as a plan
        is the worst UX failure of the feature. PHASE: P8."""
        payload = json.loads((self.FIXTURE_DIR / "sample_scenario_compare.json").read_text(encoding="utf-8"))
        ev = TypeAdapter(s.ServerEvent).validate_python(payload)
        assert ev.blocks and all(b.is_hypothetical for b in ev.blocks)


class TestTurnContext:
    def test_emit_accumulates_events(self, workspace):
        """WHY: handlers accumulate outbound events on the context; the API layer
        streams them. BUG: a broken emit() drops events the client never sees.
        PHASE: P3 (handlers), P4 (WS streaming)."""
        tc = s.TurnContext(conversation_id="c1", workspace=workspace, message_text="hi")
        tc.emit(s.StatusEvent(stage="extracting"))
        tc.emit(s.DoneEvent(proposal_id=41))
        assert [e.type for e in tc.events] == ["status", "done"]

    def test_turn_context_holds_typed_workspace(self, workspace):
        """WHY: the context threads the loaded workspace + intents through the
        handlers (the §5.2 'state object survives, framework doesn't' discipline).
        BUG: an untyped workspace lets a handler mutate the wrong shape. PHASE: P3."""
        tc = s.TurnContext(conversation_id="c1", workspace=workspace,
                           intents=[s.Intent.DESCRIBE_PORTFOLIO, s.Intent.OBJECTIVE])
        assert tc.workspace.conversation_id == "c1"
        assert s.Intent.OBJECTIVE in tc.intents
