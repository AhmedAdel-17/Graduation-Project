"""Domain trace events for the Portfolio Assistant copilot (roadmap P3).

The copilot is a plain service, not a LangGraph, so it does not get
node-transition traces "for free". Instead every meaningful step emits a
*domain* trace event (``pa.policy_compiled``, ``pa.scenario_forked``, …) in the
same envelope shape the admin Trace Inspector already consumes — but on a
per-conversation channel, because copilot events cannot reuse ``agent_events``
(hard FK to ``analysis_sessions``) nor ``redis_pubsub.AgentEventPublisher``
(ticker-scoped). See roadmap finding #1.

Two sinks, both best-effort and non-raising:

* **Persistence** — appended to ``pa_events`` via the injected ``WorkspaceStore``
  (``log_event``), so a turn is fully reconstructable after the fact.
* **Live fan-out** — published on the Redis channel ``pa:<conversation_id>`` for
  the (future, P9) admin-trace adapter and any live WS listener. A Redis outage
  or a missing ``redis`` package degrades silently to persistence-only.

These trace events are distinct from the ``schemas.ServerEvent`` stream the WS
sends to the browser: trace events are the *audit/observability* record of how a
turn was computed; ServerEvents are the user-facing chat payloads. The copilot
emits both.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("tradingagents.portfolio.events")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")


# ---------------------------------------------------------------------------
# Event-type vocabulary (stable codes — referenced by tests + the admin adapter)
# ---------------------------------------------------------------------------

class PAEvent:
    """Stable ``event_type`` codes for copilot domain trace events."""

    TURN_START = "pa.turn.start"
    ROUTED = "pa.routed"
    EXTRACTED = "pa.extracted"
    SNAPSHOT_CONFIRMED = "pa.snapshot_confirmed"
    POLICY_INFERRED = "pa.policy_inferred"
    POLICY_COMPILED = "pa.policy_compiled"
    SIGNALS_RESOLVED = "pa.signals_resolved"
    PROPOSAL_READY = "pa.proposal_ready"
    SCENARIO_FORKED = "pa.scenario_forked"
    SCENARIO_ADOPTED = "pa.scenario_adopted"
    CLARIFICATION = "pa.clarification"
    GATE_BLOCKED = "pa.gate_blocked"
    QA = "pa.qa"
    OFF_TOPIC = "pa.off_topic"
    TURN_DONE = "pa.turn.done"
    ERROR = "pa.error"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pa_channel(conversation_id: str) -> str:
    """Per-conversation Redis pub/sub channel (roadmap finding #1)."""
    return f"pa:{conversation_id}"


class PortfolioEventEmitter:
    """Emits copilot domain trace events to Postgres + Redis (both best-effort).

    Construct one per turn (cheap). ``message_id`` ties every event of a turn to
    the originating ``pa_messages`` row when known. Neither sink ever raises into
    the caller — observability must not break a turn.
    """

    def __init__(
        self,
        conversation_id: str,
        *,
        store: Any = None,
        message_id: Optional[int] = None,
        enable_redis: bool = True,
    ) -> None:
        self.conversation_id = conversation_id
        self.store = store
        self.message_id = message_id
        self.channel = pa_channel(conversation_id)
        self.events: list[dict] = []  # in-memory mirror (handy for tests/debug)
        self._redis: Any = None
        if enable_redis:
            self._redis = _make_redis_client()

    def emit(
        self,
        event_type: str,
        *,
        stage: Optional[str] = None,
        payload: Optional[dict] = None,
        message_id: Optional[int] = None,
    ) -> dict:
        """Record one domain event; persist + publish (both best-effort)."""
        envelope = {
            "event_type": event_type,
            "stage": stage,
            "conversation_id": self.conversation_id,
            "message_id": message_id if message_id is not None else self.message_id,
            "payload": payload or {},
            "timestamp": _utcnow_iso(),
        }
        self.events.append(envelope)
        self._persist(envelope)
        self._publish(envelope)
        return envelope

    # --- sinks -------------------------------------------------------------
    def _persist(self, envelope: dict) -> None:
        if self.store is None:
            return
        try:
            self.store.log_event(
                self.conversation_id,
                envelope["event_type"],
                stage=envelope["stage"],
                payload=envelope["payload"],
                message_id=envelope["message_id"],
            )
        except Exception as exc:  # pragma: no cover — observability must not break
            logger.warning("pa_events persist failed (%s): %s", envelope["event_type"], exc)

    def _publish(self, envelope: dict) -> None:
        if self._redis is None:
            return
        try:
            self._redis.publish(self.channel, json.dumps(envelope, default=str))
        except Exception as exc:  # pragma: no cover
            logger.warning("pa_events publish failed (%s): %s", envelope["event_type"], exc)


def _make_redis_client() -> Any:
    """Best-effort synchronous Redis client, or ``None`` when unavailable."""
    try:
        import redis as _redis
    except Exception:
        return None
    try:
        client = _redis.from_url(REDIS_URL, decode_responses=True)
        client.ping()
        return client
    except Exception as exc:  # pragma: no cover — redis down/unreachable
        logger.info("portfolio events: Redis unavailable (%s); persistence-only", exc)
        return None


__all__ = ["PAEvent", "PortfolioEventEmitter", "pa_channel"]
