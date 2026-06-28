"""
server/live_hub.py
==================
Process-global, in-memory broadcast hub for live agent-run events.

Any analysis run (started from the main dashboard's /api/analyze-full, the
/api/analyze WebSocket, or anywhere else) publishes per-node frames here. Admin
clients connect to the /api/admin/live WebSocket, which subscribes to the hub
and forwards every frame — so a run launched from ANY surface is visible live in
the admin dashboard, regardless of who started it.

This is observability only: it never feeds back into the graph or trading logic,
and a hub failure never affects a run (publish is best-effort).

Thread-safety: the graph runs in a worker thread, so ``publish`` is called off
the event loop. It schedules each subscriber's queue put via
``loop.call_soon_threadsafe`` and guards shared state with a lock.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional

logger = logging.getLogger("tradingagents.live_hub")

# Per-run frame cap (keeps memory bounded for late subscribers replaying state).
_MAX_FRAMES_PER_RUN = 400
# How many runs to retain for the connect-time snapshot.
_MAX_RUNS = 8


class LiveHub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()
        # run_id -> {"meta": {...}, "frames": [...], "status": "running|complete|error"}
        self._runs: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

    # ── Loop wiring ───────────────────────────────────────────────────────
    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # ── Subscription (called from the event loop) ─────────────────────────
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def snapshot(self) -> List[Dict[str, Any]]:
        """Active + recent runs with their buffered frames, for a new client."""
        with self._lock:
            out: List[Dict[str, Any]] = []
            for run_id, rec in self._runs.items():
                out.append({
                    "run_id": run_id,
                    "meta": dict(rec["meta"]),
                    "status": rec["status"],
                    "frames": list(rec["frames"]),
                })
            return out

    # ── Publishing (may be called from a worker thread) ───────────────────
    def publish(self, frame: Dict[str, Any]) -> None:
        try:
            self._record(frame)
        except Exception as exc:  # pragma: no cover — never break a run
            logger.debug("live_hub record failed: %s", exc)

        loop = self._loop
        if loop is None:
            return
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                loop.call_soon_threadsafe(_safe_put, q, frame)
            except Exception:  # pragma: no cover
                pass

    def _record(self, frame: Dict[str, Any]) -> None:
        run_id = frame.get("run_id")
        if not run_id:
            return
        ftype = frame.get("type")
        with self._lock:
            rec = self._runs.get(run_id)
            if rec is None:
                rec = {
                    "meta": {
                        "run_id": run_id,
                        "ticker": frame.get("ticker"),
                        "trade_date": frame.get("trade_date"),
                        "source": frame.get("source", "unknown"),
                        "started_at": frame.get("timestamp"),
                    },
                    "frames": [],
                    "status": "running",
                }
                self._runs[run_id] = rec
                # Prune oldest runs.
                while len(self._runs) > _MAX_RUNS:
                    self._runs.popitem(last=False)

            frames = rec["frames"]
            frames.append(frame)
            if len(frames) > _MAX_FRAMES_PER_RUN:
                del frames[: len(frames) - _MAX_FRAMES_PER_RUN]

            if ftype == "complete":
                rec["status"] = "complete"
            elif ftype == "error":
                rec["status"] = "error"


def _safe_put(q: asyncio.Queue, frame: Dict[str, Any]) -> None:
    try:
        q.put_nowait(frame)
    except asyncio.QueueFull:  # pragma: no cover — slow consumer; drop frame
        pass


# Module singleton.
live_hub = LiveHub()
