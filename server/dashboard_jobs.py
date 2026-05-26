"""
In-process async job runner + pub/sub for the dashboard backend.

Each job (prediction or backtest) runs as an asyncio.Task in the background.
The WebSocket endpoint subscribes to the job's event queue and streams the
agent_start / agent_progress / agent_done / decision / complete / error
messages to the dashboard.

For MVP we keep everything in-process (no Redis). For production, swap the
pubsub layer for Redis pub/sub and the executor for Celery/RQ workers.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("dashboard.jobs")

# ---------------------------------------------------------------------------
# Per-job event queues (in-memory pub/sub)
# ---------------------------------------------------------------------------

# job_id -> list of subscriber asyncio.Queues
_subscribers: Dict[str, List[asyncio.Queue]] = {}
_subscribers_lock = threading.Lock()

# job_id -> list of recent messages (so late subscribers can catch up)
_replay: Dict[str, List[Dict[str, Any]]] = {}
_replay_lock = threading.Lock()

# job_id -> bool (True = job is done, no more messages coming)
_done_flags: Dict[str, bool] = {}

# Cancellation flags (job_id -> True if cancel requested)
_cancel_flags: Dict[str, bool] = {}

# Thread pool — backtests and predictions are CPU+IO heavy, run off the event loop
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dashboard-job")


def request_cancel(job_id: str) -> None:
    """Mark a job for cancellation. The runner checks this flag periodically."""
    _cancel_flags[job_id] = True


def is_cancelled(job_id: str) -> bool:
    return _cancel_flags.get(job_id, False)


def subscribe(job_id: str) -> asyncio.Queue:
    """
    Subscribe to a job's event stream. Returns a new asyncio.Queue.
    Replays any messages that were already emitted (so refreshing the page works).
    """
    q: asyncio.Queue = asyncio.Queue()

    # Replay buffer first
    with _replay_lock:
        for msg in _replay.get(job_id, []):
            q.put_nowait(msg)

    with _subscribers_lock:
        _subscribers.setdefault(job_id, []).append(q)

    # If the job already finished, push a sentinel
    if _done_flags.get(job_id):
        q.put_nowait({"type": "_end"})

    return q


def unsubscribe(job_id: str, q: asyncio.Queue) -> None:
    with _subscribers_lock:
        if job_id in _subscribers:
            try:
                _subscribers[job_id].remove(q)
            except ValueError:
                pass


def publish(job_id: str, message: Dict[str, Any]) -> None:
    """
    Push a message to all subscribers of `job_id` and record it for replay.

    Safe to call from any thread (it schedules onto each subscriber's loop).
    """
    # Stamp the message with a timestamp
    msg = {**message, "_ts": datetime.utcnow().isoformat() + "Z"}

    # Persist in replay buffer
    with _replay_lock:
        _replay.setdefault(job_id, []).append(msg)
        # Bound the buffer so a long backtest doesn't eat memory
        if len(_replay[job_id]) > 500:
            _replay[job_id] = _replay[job_id][-500:]

    # Fan out to subscribers
    with _subscribers_lock:
        queues = list(_subscribers.get(job_id, []))

    for q in queues:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            # Drop on full — subscriber is too slow; backpressure handled at edge
            logger.warning("Subscriber queue full for job %s; dropping message", job_id)


def mark_done(job_id: str) -> None:
    """Signal that no more messages will come for this job."""
    _done_flags[job_id] = True
    with _subscribers_lock:
        for q in _subscribers.get(job_id, []):
            try:
                q.put_nowait({"type": "_end"})
            except asyncio.QueueFull:
                pass


def clear_job(job_id: str) -> None:
    """Free memory for a finished job (call after subscribers have disconnected)."""
    with _replay_lock:
        _replay.pop(job_id, None)
    with _subscribers_lock:
        _subscribers.pop(job_id, None)
    _done_flags.pop(job_id, None)
    _cancel_flags.pop(job_id, None)


# ---------------------------------------------------------------------------
# Helper for emitting structured events
# ---------------------------------------------------------------------------

def emit_agent_start(job_id: str, agent: str) -> None:
    publish(job_id, {"type": "agent_start", "agent": agent})


def emit_agent_progress(job_id: str, agent: str, message: str) -> None:
    publish(job_id, {"type": "agent_progress", "agent": agent, "message": message})


def emit_agent_done(
    job_id: str,
    agent: str,
    summary: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    publish(job_id, {
        "type": "agent_done",
        "agent": agent,
        "summary": summary,
        "details": details or {},
    })


def emit_decision(
    job_id: str,
    action: str,
    confidence: float,
    rationale: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    publish(job_id, {
        "type": "decision",
        "action": action,
        "confidence": confidence,
        "rationale": rationale,
        "details": details or {},
    })


def emit_complete(job_id: str, run_id: str) -> None:
    publish(job_id, {"type": "complete", "run_id": run_id})


def emit_error(job_id: str, message: str, agent: Optional[str] = None) -> None:
    publish(job_id, {"type": "error", "agent": agent, "message": message})


# ---------------------------------------------------------------------------
# Background task launcher
# ---------------------------------------------------------------------------

def run_in_background(fn: Callable[..., None], *args, **kwargs) -> None:
    """Submit a function to the worker thread pool. Errors are caught + logged."""
    def _wrap():
        try:
            fn(*args, **kwargs)
        except Exception as exc:
            logger.exception("Background job failed: %s", exc)
            # Caller should also handle its own errors and publish them
    _executor.submit(_wrap)
