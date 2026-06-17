"""P4 tests for the Portfolio Assistant REST + WebSocket surface.

A minimal FastAPI app mounts only ``server.portfolio_routes.router`` with the
copilot service overridden by a stubbed-adapter instance over a JSON-file store
(no Postgres, no LLM, no network). This keeps the API contract test fast and
deterministic while exercising the real routes, the confirmation gate (409),
soft-delete semantics, and a full WS conversation incl. disconnect/reconnect.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.copilot_service import PortfolioCopilotService
from tradingagents.portfolio.extraction import ExtractionResult
from tradingagents.portfolio.narrator import NarrationResult
from tradingagents.portfolio.signals import SignalResolver
from tradingagents.portfolio.whatif import WhatIfResult
from tradingagents.portfolio.workspace_store import JsonFileWorkspaceStore
from server import portfolio_routes as pr

PRICES = {"ETEL.CA": 38.0, "FWRY.CA": 12.0}


# --- stub adapters (mirror the P3 turn tests) ------------------------------

class FakeRouter:
    def __init__(self, script):
        self._script = list(script)

    def classify(self, text, digest=None):
        return self._script.pop(0) if self._script else {s.Intent.FOLLOW_UP_QA}


class FakeExtraction:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def extract(self, text, *, conversation_id=None, language="auto"):
        block = s.ExtractedPortfolioTableBlock(
            data=s.ExtractedPortfolioTableData(
                holdings=list(self._snapshot.holdings), cash_egp=self._snapshot.cash_egp))
        return ExtractionResult(snapshot=self._snapshot, block=block, clarification=None)


class FakeNarrator:
    def narrate(self, *, analytics=None, proposal=None, diff=None, blocks=None, language="en"):
        return NarrationResult(text="ok", blocks=list(blocks or []))


def _baseline_snapshot():
    return s.PortfolioSnapshot(
        cash_egp=50000.0,
        holdings=[
            s.PortfolioHolding(ticker="ETEL.CA", shares=1000, avg_cost=35.0),
            s.PortfolioHolding(ticker="FWRY.CA", shares=2000, avg_cost=10.0),
        ],
    )


def _make_service(store, *, intents=None, whatif=None):
    return PortfolioCopilotService(
        store,
        router=FakeRouter(intents or []),
        extraction=FakeExtraction(_baseline_snapshot()),
        narrator=FakeNarrator(),
        whatif=whatif,
        signal_resolver=SignalResolver(query_fn=lambda t: None, max_age_days=7),
        price_provider=lambda tickers: {t: PRICES[t] for t in tickers if t in PRICES},
        returns_provider=lambda _t: None,
        market_cap_provider=lambda _t: ({}, list(_t)),
        ratios_provider=lambda _t: {},
        sentiment_provider=lambda _t: {},
        enable_redis=False,
    )


@pytest.fixture
def ctx(tmp_path):
    store = JsonFileWorkspaceStore(base_dir=tmp_path / "chats")
    service = _make_service(store, intents=[{s.Intent.OPTIMIZE}])
    app = FastAPI()
    app.include_router(pr.router)
    app.dependency_overrides[pr.get_service] = lambda: service
    client = TestClient(app)
    return client, service, store


# ---------------------------------------------------------------------------
# REST CRUD
# ---------------------------------------------------------------------------

def test_conversation_crud_and_soft_delete(ctx):
    client, service, store = ctx

    cid = client.post("/api/portfolio/conversations", json={"language": "en"}).json()["id"]
    assert cid

    listed = client.get("/api/portfolio/conversations").json()["conversations"]
    assert any(c["id"] == cid for c in listed)

    got = client.get(f"/api/portfolio/conversations/{cid}")
    assert got.status_code == 200
    assert got.json()["digest"]["active_ref"] == "baseline"

    # soft delete → archived, drops out of the default list
    assert client.delete(f"/api/portfolio/conversations/{cid}").json()["status"] == "archived"
    listed_after = client.get("/api/portfolio/conversations").json()["conversations"]
    assert all(c["id"] != cid for c in listed_after)
    # but still retrievable directly (audit doctrine: soft-delete only)
    assert client.get(f"/api/portfolio/conversations/{cid}").status_code == 200


def test_unknown_conversation_404(ctx):
    client, _, _ = ctx
    assert client.get("/api/portfolio/conversations/does-not-exist").status_code == 404


def test_confirm_then_optimize_over_rest_path(ctx):
    client, service, store = ctx
    cid = client.post("/api/portfolio/conversations", json={}).json()["id"]

    snap = _baseline_snapshot().model_dump(mode="json")
    resp = client.post(f"/api/portfolio/conversations/{cid}/confirm", json=snap)
    assert resp.status_code == 200
    assert resp.json()["baseline"]["confirmed_by_user"] is True

    # snapshot analytics endpoint works once a baseline exists
    snapshot_id = store.get_latest_snapshot(cid).snapshot_id
    a = client.get(f"/api/portfolio/snapshots/{snapshot_id}/analytics")
    assert a.status_code == 200
    assert a.json()["total_value_egp"] > 0


# ---------------------------------------------------------------------------
# Confirmation gate (409)
# ---------------------------------------------------------------------------

def test_whatif_before_confirm_returns_409(ctx):
    client, _, _ = ctx
    cid = client.post("/api/portfolio/conversations", json={}).json()["id"]

    patch = {"ops": [{"op": "ADD_CASH", "amount_egp": 50000}], "reference": "baseline"}
    resp = client.post(f"/api/portfolio/conversations/{cid}/scenarios", json=patch)
    assert resp.status_code == 409


def test_structured_whatif_after_confirm(ctx):
    client, service, store = ctx
    cid = client.post("/api/portfolio/conversations", json={}).json()["id"]
    client.post(f"/api/portfolio/conversations/{cid}/confirm",
                json=_baseline_snapshot().model_dump(mode="json"))

    patch = {"ops": [{"op": "CLOSE_POSITION", "ticker": "FWRY.CA"}], "reference": "baseline",
             "label": "Sell FWRY"}
    resp = client.post(f"/api/portfolio/conversations/{cid}/scenarios", json=patch)
    assert resp.status_code == 200
    assert resp.json()["proposal_id"] is not None

    scenarios = client.get(f"/api/portfolio/conversations/{cid}/scenarios").json()["scenarios"]
    assert len(scenarios) == 1
    scen_id = scenarios[0]["scenario_id"]

    # promote → new baseline v2
    assert client.post(f"/api/portfolio/scenarios/{scen_id}/promote").status_code == 200
    assert store.get_latest_snapshot(cid).version == 2


def test_policy_patch_and_discard(ctx):
    client, service, store = ctx
    cid = client.post("/api/portfolio/conversations", json={}).json()["id"]
    client.post(f"/api/portfolio/conversations/{cid}/confirm",
                json=_baseline_snapshot().model_dump(mode="json"))

    resp = client.patch(f"/api/portfolio/conversations/{cid}/policy",
                        json={"updates": {"risk_tolerance": "low"}, "confirm": True})
    assert resp.status_code == 200
    assert resp.json()["policy"]["risk_tolerance"] == "low"
    assert resp.json()["policy"]["confirmed_by_user"] is True

    # invalid policy value → 422
    bad = client.patch(f"/api/portfolio/conversations/{cid}/policy",
                       json={"updates": {"risk_tolerance": "nonsense"}})
    assert bad.status_code == 422


def test_proposal_and_scenario_404s(ctx):
    client, _, _ = ctx
    assert client.get("/api/portfolio/proposals/999999").status_code == 404
    assert client.delete("/api/portfolio/scenarios/999999").status_code == 404
    assert client.post("/api/portfolio/scenarios/999999/promote").status_code == 404
    assert client.get("/api/portfolio/snapshots/999999/analytics").status_code == 404


# ---------------------------------------------------------------------------
# Opt-in bearer-token guard
# ---------------------------------------------------------------------------

def test_no_auth_by_default(ctx):
    # PA_API_TOKEN unset → local demo works without a token.
    client, _, _ = ctx
    assert client.get("/api/portfolio/conversations").status_code == 200


def test_auth_guard_enforced_when_token_set(ctx, monkeypatch):
    client, _, _ = ctx
    monkeypatch.setenv("PA_API_TOKEN", "s3cret")

    assert client.get("/api/portfolio/conversations").status_code == 401
    assert client.post("/api/portfolio/conversations", json={},
                       headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/api/portfolio/conversations",
                    headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200


# ---------------------------------------------------------------------------
# WebSocket conversation + disconnect/reconnect
# ---------------------------------------------------------------------------

def test_ws_full_conversation_and_reconnect(tmp_path):
    store = JsonFileWorkspaceStore(base_dir=tmp_path / "chats")
    # scripted: describe (first WS turn is a normal user message) then optimize
    service = _make_service(store, intents=[{s.Intent.OPTIMIZE}])
    app = FastAPI()
    app.include_router(pr.router)
    app.dependency_overrides[pr.get_service] = lambda: service
    client = TestClient(app)

    cid = client.post("/api/portfolio/conversations", json={}).json()["id"]
    # confirm a baseline via REST so the WS optimize turn has something to run
    client.post(f"/api/portfolio/conversations/{cid}/confirm",
                json=_baseline_snapshot().model_dump(mode="json"))

    with client.websocket_connect(f"/api/portfolio/chat/{cid}") as ws:
        ws.send_json({"type": "user_message", "text": "optimize", "language": "en"})
        events = []
        while True:
            msg = ws.receive_json()
            events.append(msg)
            if msg["type"] == "done":
                break

    types = [e["type"] for e in events]
    assert "assistant_message" in types
    assert types[-1] == "done"
    assert events[-1]["proposal_id"] is not None

    # "disconnect/reconnect": the turn persisted server-side; GET shows the
    # completed assistant message in the thread.
    thread = client.get(f"/api/portfolio/conversations/{cid}").json()
    roles = [m["role"] for m in thread["messages"]]
    assert "assistant" in roles


def test_ws_unknown_conversation_errors(tmp_path):
    store = JsonFileWorkspaceStore(base_dir=tmp_path / "chats")
    service = _make_service(store)
    app = FastAPI()
    app.include_router(pr.router)
    app.dependency_overrides[pr.get_service] = lambda: service
    client = TestClient(app)

    with client.websocket_connect("/api/portfolio/chat/nope") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["recoverable"] is False
