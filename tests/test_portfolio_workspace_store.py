"""Round-trip tests for the Portfolio Assistant workspace store (roadmap P0).

Two layers:

1. **JSON-file backend** — fully hermetic (``tmp_path``), always runs. Exercises
   the entire ``WorkspaceStore`` lifecycle and the ``load_workspace`` assembly.

2. **Postgres backend** — runs against ``POSTGRES_URL`` (the user's Supabase) when
   reachable, else skips. It is **zero-trace**: every conversation it creates is
   registered and CASCADE-deleted in teardown, and a pre-sweep removes any stale
   ``pytest-*`` rows from a previously crashed run. Safe to point at a real DB.

The same ``_run_lifecycle`` body runs against both backends, so the two
implementations are held to one behavioral contract.
"""

from __future__ import annotations

import uuid

import pytest
from dotenv import load_dotenv

from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.workspace_store import (
    JsonFileWorkspaceStore,
    PostgresWorkspaceStore,
    WorkspaceStore,
)

TEST_USER = f"pytest-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Shared realistic data + lifecycle exercise
# ---------------------------------------------------------------------------

def _baseline() -> s.PortfolioSnapshot:
    return s.PortfolioSnapshot(
        cash_egp=50000.0,
        holdings=[
            s.PortfolioHolding(ticker="ETEL.CA", weight_pct=20, avg_cost=38, name_raw="Telecom Egypt"),
            s.PortfolioHolding(ticker="FWRY.CA", weight_pct=30, avg_cost=12, name_raw="Fawry"),
            s.PortfolioHolding(ticker="TMGH.CA", weight_pct=50, avg_cost=65, name_raw="Talaat Moustafa"),
        ],
        confirmed_by_user=True,
    )


def _scenario(base_snapshot_id: int | None) -> s.Scenario:
    derived = s.PortfolioSnapshot(
        cash_egp=100000.0,
        holdings=[s.PortfolioHolding(ticker="ETEL.CA", weight_pct=40, source=s.HoldingSource.DERIVED)],
    )
    return s.Scenario(
        base_snapshot_id=base_snapshot_id,
        patch=s.ScenarioPatch(
            ops=[s.ClosePositionOp(ticker="FWRY.CA"), s.AddCashOp(amount_egp=50000)],
            reference="active", label="Sell FWRY + 50k",
        ),
        derived_snapshot=derived,
        derived_policy=s.InvestmentPolicy(risk_tolerance=s.RiskTolerance.HIGH),
        input_set=s.PinnedInputSet(
            price_asof=s.PortfolioSnapshot().created_at, price_source="test",
            signal_session_ids={"ETEL.CA": "sess-1", "FWRY.CA": None}, covariance_hash="h1",
        ),
        status=s.ScenarioStatus.ACTIVE, label="Sell FWRY + 50k",
    )


def _run_lifecycle(store: WorkspaceStore, conversation_id: str) -> None:
    """The full store contract, asserted end-to-end. Backend-agnostic."""
    cid = conversation_id

    # --- conversation exists, defaults sane --------------------------------
    conv = store.get_conversation(cid)
    assert conv is not None and conv["active_ref"] == "baseline" and conv["archived"] is False

    # --- baseline snapshots version monotonically --------------------------
    sid1 = store.save_baseline_snapshot(cid, _baseline())
    s2 = _baseline()
    s2.cash_egp = 75000.0
    sid2 = store.save_baseline_snapshot(cid, s2)
    assert sid2 != sid1
    latest = store.get_latest_snapshot(cid)
    assert latest is not None and latest.version == 2 and latest.cash_egp == 75000.0
    assert latest.tickers == ["ETEL.CA", "FWRY.CA", "TMGH.CA"]
    # fetch-by-id reconstructs the first version intact
    got1 = store.get_snapshot(sid1)
    assert got1 is not None and got1.version == 1 and got1.cash_egp == 50000.0
    assert got1.holdings[0].name_raw == "Telecom Egypt"

    # --- policy versions + round-trip --------------------------------------
    store.save_policy(cid, s.InvestmentPolicy.default_policy())
    pol2 = s.InvestmentPolicy(
        objective=s.Objective.CAPITAL_PRESERVATION, risk_tolerance=s.RiskTolerance.LOW,
        horizon=s.Horizon.LT_6M, source_spans={"horizon": "saving for marriage in 6 months"},
        inferred_fields=["horizon"], confirmed_by_user=True, version=2,
    )
    store.save_policy(cid, pol2, compiler_version="v1", compiled_params={"risk_aversion": 8})
    latest_pol = store.get_latest_policy(cid)
    assert latest_pol is not None
    assert latest_pol.objective == s.Objective.CAPITAL_PRESERVATION
    assert latest_pol.source_spans["horizon"] == "saving for marriage in 6 months"

    # --- scenario round-trip incl typed patch ops --------------------------
    scid = store.save_scenario(cid, _scenario(sid2))
    got_sc = store.get_scenario(scid)
    assert got_sc is not None
    assert isinstance(got_sc.patch.ops[0], s.ClosePositionOp)
    assert got_sc.patch.ops[0].ticker == "FWRY.CA"
    assert isinstance(got_sc.patch.ops[1], s.AddCashOp)
    assert got_sc.derived_snapshot.cash_egp == 100000.0
    assert got_sc.derived_policy is not None and got_sc.derived_policy.risk_tolerance == s.RiskTolerance.HIGH
    assert got_sc.input_set is not None and got_sc.input_set.signal_session_ids["FWRY.CA"] is None

    # status filter + soft delete
    assert len(store.get_scenarios(cid, statuses=[s.ScenarioStatus.ACTIVE])) == 1
    store.update_scenario_status(scid, s.ScenarioStatus.DISCARDED)
    assert store.get_scenario(scid).status == s.ScenarioStatus.DISCARDED
    assert store.get_scenarios(cid, statuses=[s.ScenarioStatus.ACTIVE]) == []
    store.update_scenario_status(scid, s.ScenarioStatus.ACTIVE)  # restore for assembly test

    # --- proposal round-trip (scenario run) --------------------------------
    prop = s.OptimizationProposal(
        conversation_id=cid, scenario_id=scid, policy_version=2,
        solver_status=s.SolverStatus.OPTIMAL, engine_version="0.1.0",
        actions=[s.RebalanceAction(
            ticker="FWRY.CA", side=s.TradeSide.SELL, shares=100, price_used=12.0,
            est_value_egp=1200.0, current_weight_pct=30, target_weight_pct=0,
            signal_session_id="sess-9")],
        policy_flags=[s.PolicyFlag(code="CONCENTRATION_VS_RISK", detail="too concentrated")],
        inputs_audit={"prices": {"FWRY.CA": 12.0}},
    )
    pid = store.save_proposal(prop)
    got_prop = store.get_proposal(pid)
    assert got_prop is not None and got_prop.proposal_id == pid
    assert got_prop.scenario_id == scid and got_prop.snapshot_id is None
    assert got_prop.actions[0].side == s.TradeSide.SELL
    assert got_prop.policy_flags[0].code == "CONCENTRATION_VS_RISK"

    # --- messages with typed blocks ----------------------------------------
    mid = store.add_message(cid, "user", text="عندي ٢٠٪ في المصرية للاتصالات")
    store.add_message(cid, "assistant", text="here is your allocation", blocks=[
        s.AllocationDonutBlock(data=s.AllocationDonutData(
            slices=[s.AllocationSlice(label="Cash", value_egp=50000, weight_pct=25, is_cash=True)],
            total_egp=200000)),
    ])
    msgs = store.get_messages(cid)
    assert len(msgs) == 2 and msgs[0]["text_content"] == "عندي ٢٠٪ في المصرية للاتصالات"
    assert msgs[1]["blocks"][0]["type"] == "allocation_donut"
    assert isinstance(mid, int)

    # --- events ------------------------------------------------------------
    store.log_event(cid, "pa.turn.start", stage="extracting")
    store.log_event(cid, "pa.proposal_ready", stage="optimizing", payload={"proposal_id": pid})
    evs = store.get_events(cid)
    assert [e["event_type"] for e in evs] == ["pa.turn.start", "pa.proposal_ready"]
    assert evs[1]["payload"]["proposal_id"] == pid

    # --- pointers + workspace assembly -------------------------------------
    store.set_active_ref(cid, str(scid))
    store.set_last_proposal_id(cid, pid)
    ws = store.load_workspace(cid)
    assert ws is not None
    assert ws.baseline is not None and ws.baseline.version == 2          # latest baseline
    assert ws.policy.objective == s.Objective.CAPITAL_PRESERVATION       # latest policy
    assert ws.last_proposal_id == pid
    assert ws.active_ref == str(scid)
    active = ws.active_scenario()
    assert active is not None and active.scenario_id == scid             # navigation resolves
    # digest stays bounded + deterministic
    assert ws.digest().model_dump() == ws.digest().model_dump()
    assert ws.digest().baseline_tickers == ["ETEL.CA", "FWRY.CA", "TMGH.CA"]


# ---------------------------------------------------------------------------
# JSON backend (hermetic)
# ---------------------------------------------------------------------------

class TestJsonStore:
    def test_full_lifecycle(self, tmp_path):
        """WHY: the no-Postgres fallback must satisfy the same contract as PG.
        BUG: a JSON-store divergence would make local/dev runs behave differently
        from production. PHASE: P3 (service runs on whichever store is active)."""
        store = JsonFileWorkspaceStore(tmp_path)
        cid = store.create_conversation(user_id=TEST_USER, language="ar")
        _run_lifecycle(store, cid)

    def test_listing_and_archive(self, tmp_path):
        """WHY: conversation listing + soft delete back the history rail.
        BUG: archived threads leaking into the list, or list losing a thread.
        PHASE: P4 (GET /conversations)."""
        store = JsonFileWorkspaceStore(tmp_path)
        c1 = store.create_conversation(user_id=TEST_USER)
        c2 = store.create_conversation(user_id=TEST_USER)
        assert {c["id"] for c in store.list_conversations(user_id=TEST_USER)} == {c1, c2}
        store.archive_conversation(c1)
        assert {c["id"] for c in store.list_conversations(user_id=TEST_USER)} == {c2}
        assert {c["id"] for c in store.list_conversations(user_id=TEST_USER, include_archived=True)} == {c1, c2}

    def test_load_workspace_missing_returns_none(self, tmp_path):
        """WHY: loading an unknown conversation must be a clean None, not a crash.
        PHASE: P4 (404 handling)."""
        store = JsonFileWorkspaceStore(tmp_path)
        assert store.load_workspace("does-not-exist") is None

    def test_fresh_workspace_uses_default_policy(self, tmp_path):
        """WHY: a conversation with no saved policy must still load a usable,
        disclosed default policy. BUG: None policy NPEs the compiler. PHASE: P3."""
        store = JsonFileWorkspaceStore(tmp_path)
        cid = store.create_conversation(user_id=TEST_USER)
        ws = store.load_workspace(cid)
        assert ws is not None and ws.policy.objective == s.Objective.BALANCED
        assert ws.baseline is None and ws.active_ref == "baseline"


# ---------------------------------------------------------------------------
# Postgres backend (POSTGRES_URL / Supabase, zero-trace)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pg_store():
    """Yield a PostgresWorkspaceStore against POSTGRES_URL, or skip. Registers
    created conversations and CASCADE-deletes them (+ any stale pytest-* rows)
    so the real database is left exactly as found."""
    load_dotenv()
    from tradingagents.db import connection as db
    if not db.is_postgres_available():
        pytest.skip("POSTGRES_URL not set / Postgres unreachable")

    created: list[str] = []

    def _sweep():
        # remove rows from this run and any earlier crashed run (cascade)
        try:
            with db.cursor() as cur:
                cur.execute("DELETE FROM pa_conversations WHERE user_id LIKE 'pytest-%';")
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"cleanup failed: {exc}")

    _sweep()  # pre-sweep stragglers
    store = PostgresWorkspaceStore()
    yield store, created
    _sweep()  # post-sweep everything this run made


class TestPostgresStore:
    def test_full_lifecycle(self, pg_store):
        """WHY: prove the real Supabase round-trip end-to-end through the pa_*
        tables (JSONB columns, typed unions, version counters, FKs). BUG: a
        column/serialization mismatch only a live DB surfaces. PHASE: P3/P4."""
        store, created = pg_store
        cid = store.create_conversation(user_id=TEST_USER, language="ar")
        created.append(cid)
        _run_lifecycle(store, cid)

    def test_cascade_delete_is_clean(self, pg_store):
        """WHY: archiving/cleanup must not orphan child rows. BUG: a missing ON
        DELETE CASCADE would leave dangling messages/scenarios. PHASE: P4 (delete
        conversation), audit hygiene."""
        store, created = pg_store
        from tradingagents.db import connection as db
        cid = store.create_conversation(user_id=TEST_USER)
        store.save_baseline_snapshot(cid, _baseline())
        store.add_message(cid, "user", text="hi")
        with db.cursor() as cur:
            cur.execute("DELETE FROM pa_conversations WHERE id = %s;", (cid,))
        # children gone
        with db.cursor(dict_cursor=True) as cur:
            cur.execute("SELECT count(*) AS n FROM pa_messages WHERE conversation_id = %s;", (cid,))
            assert cur.fetchone()["n"] == 0
            cur.execute("SELECT count(*) AS n FROM pa_portfolio_snapshots WHERE conversation_id = %s;", (cid,))
            assert cur.fetchone()["n"] == 0
