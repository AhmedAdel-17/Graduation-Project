"""
redis_pubsub.py
===============
Redis pub/sub channel for real-time agent progress streaming.

Drops in alongside the existing FastAPI WebSocket endpoint in api_server.py.
Each agent publishes a progress event when it starts and finishes;
the WebSocket handler subscribes and forwards events to the browser dashboard.

Compatible with v17.4:
- Works with the parallel fan-out graph (all analysts run simultaneously)
- Works with DataPrefetcher (publishes prefetch start/end events)
- Works with merged risk debate (single node publishes one event)
- Falls back to a no-op when Redis is unavailable

Installation:
    pip install redis

Environment:
    REDIS_URL=redis://localhost:6379  (default)

Usage in agents (e.g. market_analyst.py):
    from redis_pubsub import AgentEventPublisher

    publisher = AgentEventPublisher(ticker)
    publisher.agent_started("market_analyst")
    # ... do analysis ...
    publisher.agent_finished("market_analyst", decision="bullish", confidence=0.72)

Usage in api_server.py WebSocket handler:
    from redis_pubsub import AgentEventSubscriber

    async with AgentEventSubscriber(ticker) as sub:
        async for event in sub:
            await ws.send_json(event)
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Any, AsyncIterator, Dict, Optional

logger = logging.getLogger("tradingagents.redis")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

# ── Optional Redis import: no-op if not installed ─────────────────────────────
try:
    import redis as _redis
    import redis.asyncio as _aredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.info(
        "redis package not installed. Install with: pip install redis\n"
        "Agent progress events will not be streamed to the dashboard."
    )


def _channel(ticker: str) -> str:
    """Pub/sub channel name for a ticker's analysis session."""
    return f"egx:analysis:{ticker.replace('.', '_')}"


# ── Synchronous publisher (used inside agent nodes / trading_graph.py) ────────

class AgentEventPublisher:
    """
    Synchronous publisher used inside LangGraph agent nodes.

    All methods are fire-and-forget: they log a warning on failure but
    never raise, so a Redis outage never breaks the agent pipeline.
    """

    def __init__(self, ticker: str, session_id: Optional[str] = None):
        self.ticker = ticker
        self.channel = _channel(ticker)
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self._r: Optional[Any] = None

        if REDIS_AVAILABLE:
            try:
                self._r = _redis.from_url(REDIS_URL, decode_responses=True)
                self._r.ping()
            except Exception as e:
                logger.warning("Redis unavailable (%s). Progress events disabled.", e)
                self._r = None

    def _publish(self, payload: Dict[str, Any]) -> None:
        if not self._r:
            return
        payload.setdefault("session_id", self.session_id)
        payload.setdefault("ticker", self.ticker)
        payload.setdefault("timestamp", datetime.now().isoformat())
        try:
            self._r.publish(self.channel, json.dumps(payload, default=str))
        except Exception as e:
            logger.warning("Redis publish failed: %s", e)

    # ── Convenience methods for each pipeline stage ───────────────────────────

    def prefetch_started(self):
        self._publish({"event": "prefetch_started", "stage": "prefetch",
                       "message": "Pre-fetching news and social data in parallel..."})

    def prefetch_finished(self, sources_populated: int = 0):
        self._publish({"event": "prefetch_finished", "stage": "prefetch",
                       "sources_populated": sources_populated,
                       "message": f"Pre-fetch complete ({sources_populated}/4 sources)"})

    def agent_started(self, agent_name: str):
        self._publish({"event": "agent_started", "agent": agent_name,
                       "stage": "analysis",
                       "message": f"{agent_name.replace('_', ' ').title()} started..."})

    def agent_finished(
        self,
        agent_name: str,
        decision: Optional[str] = None,
        confidence: Optional[float] = None,
        summary: Optional[str] = None,
    ):
        payload = {
            "event": "agent_finished",
            "agent": agent_name,
            "stage": "analysis",
            "message": f"{agent_name.replace('_', ' ').title()} complete",
        }
        if decision:
            payload["decision"] = decision
        if confidence is not None:
            payload["confidence"] = round(confidence, 3)
        if summary:
            payload["summary"] = summary[:300]  # cap for transport
        self._publish(payload)

    def debate_update(self, stage: str, round_num: int, speaker: str, excerpt: str = ""):
        self._publish({
            "event": "debate_update",
            "stage": stage,
            "round": round_num,
            "speaker": speaker,
            "excerpt": excerpt[:200],
            "message": f"[{stage}] {speaker} — round {round_num}",
        })

    def risk_event(self, approved: bool, veto_reason: Optional[str] = None):
        self._publish({
            "event": "risk_decision",
            "stage": "risk",
            "approved": approved,
            "veto_reason": veto_reason,
            "message": "Risk approved" if approved else f"Risk veto: {veto_reason}",
        })

    def final_decision(self, decision: str, confidence: Optional[float] = None):
        self._publish({
            "event": "final_decision",
            "stage": "complete",
            "decision": decision,
            "confidence": confidence,
            "message": f"Final decision: {decision}",
        })

    def error(self, agent_name: str, error_msg: str):
        self._publish({
            "event": "error",
            "agent": agent_name,
            "error": error_msg[:300],
            "message": f"Error in {agent_name}: {error_msg[:100]}",
        })


# ── Async subscriber (used in FastAPI WebSocket handler) ──────────────────────

class AgentEventSubscriber:
    """
    Async context manager that subscribes to a ticker's event channel
    and yields decoded event dicts.

    Usage in api_server.py:
        async with AgentEventSubscriber(ticker, timeout_seconds=300) as sub:
            async for event in sub:
                await websocket.send_json(event)
    """

    def __init__(self, ticker: str, timeout_seconds: int = 300):
        self.ticker = ticker
        self.channel = _channel(ticker)
        self.timeout = timeout_seconds
        self._client = None
        self._pubsub = None

    async def __aenter__(self):
        if REDIS_AVAILABLE:
            try:
                self._client = _aredis.from_url(REDIS_URL, decode_responses=True)
                self._pubsub = self._client.pubsub()
                await self._pubsub.subscribe(self.channel)
                logger.info("WebSocket subscribed to %s", self.channel)
            except Exception as e:
                logger.warning("Redis subscribe failed: %s", e)
                self._client = None
                self._pubsub = None
        return self

    async def __aexit__(self, *args):
        if self._pubsub:
            try:
                await self._pubsub.unsubscribe(self.channel)
                await self._pubsub.close()
            except Exception:
                pass
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass

    def __aiter__(self):
        return self

    async def __anext__(self) -> Dict[str, Any]:
        if not self._pubsub:
            raise StopAsyncIteration

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message and message["type"] == "message":
                    return json.loads(message["data"])
            except Exception as e:
                logger.warning("Redis receive error: %s", e)
                raise StopAsyncIteration

        raise StopAsyncIteration
