"""Portfolio Assistant HTTP + WebSocket surface (roadmap P4, design §9).

A self-contained ``APIRouter`` that exposes ``PortfolioCopilotService`` — 12 REST
endpoints + ``WS /api/portfolio/chat/{id}`` — mounted on the main app in
``api_server.py`` (3 lines there). Keeping the surface here contains the growth
of ``api_server.py``.

Wiring & safety
---------------
* **No auth yet (MEMORY.md blocker).** Single-user local demo: ``user_id`` is
  hardcoded to ``"local"``. Token auth + per-user scoping is required before any
  non-local exposure — portfolios are sensitive personal financial data. The
  router is mounted only when ``pa_enabled`` (env ``PA_ENABLED``, default 1).
* **Confirmation gate (design §3/§5.2).** A structured what-if (which runs the
  optimizer) is rejected with **409** until a baseline snapshot is confirmed —
  the REST manifestation of "no optimization on an unconfirmed snapshot".
* **One active turn per conversation (finding #2).** A per-conversation
  ``asyncio.Lock`` in the WS handler serializes turns; the long single-writer
  semantics are cheaper to enforce now than to retrofit.
* **WS resilience.** A turn completes and persists server-side regardless of the
  socket; on disconnect the client simply re-fetches the thread via
  ``GET /conversations/{id}``. The real signal-refresh ``RunLauncher`` (reusing
  the ``TradingAgentsGraph``/``propagate`` machinery) is injected into the
  default service so stale signals trigger a background run.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.copilot_service import PortfolioCopilotService
from tradingagents.portfolio.signals import SignalResolver
from tradingagents.portfolio.workspace_store import get_workspace_store

logger = logging.getLogger("tradingagents.portfolio.api")

LOCAL_USER = "local"


# ---------------------------------------------------------------------------
# Opt-in bearer-token guard (contained hardening; NOT full per-user auth)
# ---------------------------------------------------------------------------
# Default-off so the local single-user demo is unaffected. When ``PA_API_TOKEN``
# is set, every portfolio route requires ``Authorization: Bearer <token>`` — a
# way to lock down ``/api/portfolio/*`` before any non-local exposure without
# touching the rest of api_server. This does NOT scope by user (`user_id` stays
# 'local'); real token auth + per-user scoping is the standing blocker
# (MEMORY.md). Browser WebSocket clients can't set headers directly, so when the
# token is enabled, front a reverse proxy that injects the header.

def require_auth(authorization: Optional[str] = Header(default=None)) -> None:
    token = os.getenv("PA_API_TOKEN")
    if not token:
        return  # opt-in: no token configured → no auth (local demo)
    if authorization != f"Bearer {token}":
        raise HTTPException(401, "missing or invalid bearer token for /api/portfolio")


router = APIRouter(prefix="/api/portfolio", tags=["portfolio"], dependencies=[Depends(require_auth)])


# ---------------------------------------------------------------------------
# Real signal-refresh launcher (reuses TradingAgentsGraph as the signal oracle)
# ---------------------------------------------------------------------------

class GraphRunLauncher:
    """Launch a per-ticker ``TradingAgentsGraph`` run; ``propagate`` writes the
    ``analysis_sessions`` row the ``SignalResolver`` later reads. Best-effort and
    heavy (minutes) — invoked from the resolver's bounded background thread."""

    def launch(self, ticker: str, trade_date: str) -> None:  # pragma: no cover - heavy/integration
        try:
            from tradingagents.dataflows.config import get_config
            from tradingagents.graph.trading_graph import TradingAgentsGraph
        except Exception as exc:
            logger.warning("RunLauncher import failed for %s: %s", ticker, exc)
            return
        try:
            graph = TradingAgentsGraph(config=get_config())
            graph.propagate(ticker, trade_date)
            logger.info("signal refresh run completed for %s @ %s", ticker, trade_date)
        except Exception as exc:
            logger.warning("signal refresh run failed for %s: %s", ticker, exc)


# ---------------------------------------------------------------------------
# Service singleton + dependency (overridable in tests)
# ---------------------------------------------------------------------------

_service: Optional[PortfolioCopilotService] = None


def _build_default_service() -> PortfolioCopilotService:
    store = get_workspace_store()
    resolver = SignalResolver(config=DEFAULT_CONFIG, run_launcher=GraphRunLauncher())
    return PortfolioCopilotService(store, signal_resolver=resolver)


def get_service() -> PortfolioCopilotService:
    """FastAPI dependency: the process-wide copilot service. Tests override this
    via ``app.dependency_overrides[get_service]``."""
    global _service
    if _service is None:
        _service = _build_default_service()
    return _service


def set_service(service: Optional[PortfolioCopilotService]) -> None:
    """Inject/replace the default service (used by api_server wiring + tests)."""
    global _service
    _service = service


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class CreateConversationRequest(BaseModel):
    language: str = "auto"
    title: Optional[str] = None


class PolicyPatchRequest(BaseModel):
    updates: dict[str, Any] = Field(default_factory=dict)
    confirm: bool = True


def _events_payload(ctx: s.TurnContext) -> list[dict]:
    return [e.model_dump(mode="json") for e in ctx.events]


def _require_conversation(service: PortfolioCopilotService, conversation_id: str) -> dict:
    conv = service.store.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(404, f"conversation not found: {conversation_id}")
    return conv


# ---------------------------------------------------------------------------
# REST — conversations
# ---------------------------------------------------------------------------

@router.post("/conversations")
def create_conversation(
    req: CreateConversationRequest, service: PortfolioCopilotService = Depends(get_service),
):
    cid = service.store.create_conversation(
        user_id=LOCAL_USER, language=req.language, title=req.title)
    return {"id": cid}


@router.get("/conversations")
def list_conversations(service: PortfolioCopilotService = Depends(get_service)):
    return {"conversations": service.store.list_conversations(user_id=LOCAL_USER)}


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str, service: PortfolioCopilotService = Depends(get_service),
):
    conv = _require_conversation(service, conversation_id)
    workspace = service.store.load_workspace(conversation_id)
    return {
        "conversation": conv,
        "messages": service.store.get_messages(conversation_id),
        "digest": workspace.digest().model_dump(mode="json") if workspace else None,
    }


@router.delete("/conversations/{conversation_id}")
def archive_conversation(
    conversation_id: str, service: PortfolioCopilotService = Depends(get_service),
):
    _require_conversation(service, conversation_id)
    service.store.archive_conversation(conversation_id)
    return {"status": "archived", "id": conversation_id}


@router.post("/conversations/{conversation_id}/confirm")
def confirm_snapshot(
    conversation_id: str, snapshot: s.PortfolioSnapshot,
    service: PortfolioCopilotService = Depends(get_service),
):
    _require_conversation(service, conversation_id)
    ctx = service.confirm_snapshot(conversation_id, snapshot)
    baseline = service.store.get_latest_snapshot(conversation_id)
    return {
        "events": _events_payload(ctx),
        "baseline": baseline.model_dump(mode="json") if baseline else None,
    }


@router.patch("/conversations/{conversation_id}/policy")
def patch_policy(
    conversation_id: str, req: PolicyPatchRequest,
    service: PortfolioCopilotService = Depends(get_service),
):
    _require_conversation(service, conversation_id)
    try:
        policy = service.update_policy(conversation_id, updates=req.updates, confirm=req.confirm)
    except Exception as exc:  # invalid field/value
        raise HTTPException(422, f"invalid policy update: {exc}")
    return {"policy": policy.model_dump(mode="json")}


# ---------------------------------------------------------------------------
# REST — scenarios
# ---------------------------------------------------------------------------

@router.get("/conversations/{conversation_id}/scenarios")
def list_scenarios(
    conversation_id: str, service: PortfolioCopilotService = Depends(get_service),
):
    _require_conversation(service, conversation_id)
    scenarios = service.store.get_scenarios(conversation_id)
    return {"scenarios": [sc.model_dump(mode="json") for sc in scenarios]}


@router.post("/conversations/{conversation_id}/scenarios")
def create_scenario(
    conversation_id: str, patch: s.ScenarioPatch,
    service: PortfolioCopilotService = Depends(get_service),
):
    """Structured what-if from UI controls (no interpreter LLM). Runs the
    optimizer on the fork → **409** until a baseline is confirmed (the gate)."""
    _require_conversation(service, conversation_id)
    workspace = service.store.load_workspace(conversation_id)
    if not (workspace and workspace.baseline and workspace.baseline.confirmed_by_user):
        raise HTTPException(
            409, "confirm a portfolio baseline before running a what-if / optimization")
    ctx = service.apply_what_if_patch(conversation_id, patch)
    last_proposal_id = (service.store.load_workspace(conversation_id) or workspace).last_proposal_id
    return {"events": _events_payload(ctx), "proposal_id": last_proposal_id}


@router.post("/scenarios/{scenario_id}/promote")
def promote_scenario(
    scenario_id: int, service: PortfolioCopilotService = Depends(get_service),
):
    scenario = service.store.get_scenario(scenario_id)
    if scenario is None or scenario.conversation_id is None:
        raise HTTPException(404, f"scenario not found: {scenario_id}")
    ctx = service.adopt_scenario(scenario.conversation_id, scenario_id)
    return {"events": _events_payload(ctx)}


@router.delete("/scenarios/{scenario_id}")
def discard_scenario(
    scenario_id: int, service: PortfolioCopilotService = Depends(get_service),
):
    scenario = service.store.get_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(404, f"scenario not found: {scenario_id}")
    service.store.update_scenario_status(scenario_id, s.ScenarioStatus.DISCARDED)
    return {"status": "discarded", "scenario_id": scenario_id}


# ---------------------------------------------------------------------------
# REST — proposals & analytics (audit / read-only)
# ---------------------------------------------------------------------------

@router.get("/proposals/{proposal_id}")
def get_proposal(
    proposal_id: int, service: PortfolioCopilotService = Depends(get_service),
):
    proposal = service.store.get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(404, f"proposal not found: {proposal_id}")
    return proposal.model_dump(mode="json")


@router.get("/snapshots/{snapshot_id}/analytics")
def snapshot_analytics(
    snapshot_id: int, service: PortfolioCopilotService = Depends(get_service),
):
    snapshot = service.store.get_snapshot(snapshot_id)
    if snapshot is None:
        raise HTTPException(404, f"snapshot not found: {snapshot_id}")
    try:
        analytics = service.compute_snapshot_analytics(snapshot)
    except ValueError as exc:
        raise HTTPException(503, f"could not price snapshot: {exc}")
    return analytics.model_dump(mode="json")


# ---------------------------------------------------------------------------
# WebSocket — conversational turns
# ---------------------------------------------------------------------------

# Per-conversation locks enforce a single active turn (finding #2). Event-loop
# bound asyncio.Locks are sufficient for the single-process demo server.
_conv_locks: dict[str, asyncio.Lock] = {}


def _conv_lock(conversation_id: str) -> asyncio.Lock:
    lock = _conv_locks.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        _conv_locks[conversation_id] = lock
    return lock


@router.websocket("/chat/{conversation_id}")
async def chat_ws(
    websocket: WebSocket, conversation_id: str,
    service: PortfolioCopilotService = Depends(get_service),
):
    await websocket.accept()
    if service.store.get_conversation(conversation_id) is None:
        await websocket.send_json(s.ErrorEvent(
            message=f"unknown conversation: {conversation_id}", recoverable=False
        ).model_dump(mode="json"))
        await websocket.close()
        return

    try:
        while True:
            data = await websocket.receive_json()
            async with _conv_lock(conversation_id):
                await _handle_ws_message(websocket, conversation_id, data, service)
    except WebSocketDisconnect:
        # The in-flight turn (if any) already completed + persisted server-side;
        # the client re-fetches the thread on reconnect. Nothing to clean up.
        logger.info("portfolio WS disconnected: %s", conversation_id)
    except Exception as exc:  # pragma: no cover — defensive socket boundary
        logger.exception("portfolio WS error for %s", conversation_id)
        try:
            await websocket.send_json(s.ErrorEvent(message=str(exc)).model_dump(mode="json"))
        except Exception:
            pass


async def _handle_ws_message(
    websocket: WebSocket, conversation_id: str, data: dict,
    service: PortfolioCopilotService,
) -> None:
    mtype = data.get("type")
    loop = asyncio.get_running_loop()
    try:
        if mtype == "user_message":
            text = str(data.get("text") or "")
            language = data.get("language", "auto")
            ctx = await loop.run_in_executor(
                None, lambda: service.handle_turn(conversation_id, text, language=language))
        elif mtype == "what_if":
            patch = s.ScenarioPatch.model_validate(data["patch"])
            ctx = await loop.run_in_executor(
                None, lambda: service.apply_what_if_patch(conversation_id, patch))
        elif mtype == "adopt_scenario":
            scenario_id = int(data["scenario_id"])
            ctx = await loop.run_in_executor(
                None, lambda: service.adopt_scenario(conversation_id, scenario_id))
        else:
            await websocket.send_json(s.ErrorEvent(
                message=f"unknown message type: {mtype}").model_dump(mode="json"))
            return
    except Exception as exc:
        await websocket.send_json(s.ErrorEvent(message=str(exc)).model_dump(mode="json"))
        return

    for event in ctx.events:
        await websocket.send_json(event.model_dump(mode="json"))
    workspace = service.store.load_workspace(conversation_id)
    await websocket.send_json(s.DoneEvent(
        proposal_id=workspace.last_proposal_id if workspace else None
    ).model_dump(mode="json"))


__all__ = ["router", "get_service", "set_service", "GraphRunLauncher", "LOCAL_USER"]
