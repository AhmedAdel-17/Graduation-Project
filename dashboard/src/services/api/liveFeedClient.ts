// Read-only WebSocket client for /api/admin/live — the admin "watch any run"
// feed. Unlike wsClient (which STARTS a run), this only receives: a connect-time
// snapshot of active/recent runs, then a stream of per-run frames. Auto-reconnects
// since it's a long-lived passive subscription.

import type { NodeStatus } from "./wsClient";

export interface LiveFrame {
  run_id: string;
  ticker?: string | null;
  trade_date?: string | null;
  source?: "main" | "admin" | string;
  type: "run_started" | "agent_update" | "complete" | "error";
  node: string;
  status: NodeStatus | string;
  state_keys?: string[];
  data?: Record<string, unknown>;
  timestamp: string;
}

export interface LiveSnapshotRun {
  run_id: string;
  meta: {
    run_id: string;
    ticker?: string | null;
    trade_date?: string | null;
    source?: string;
    started_at?: string | null;
  };
  status: "running" | "complete" | "error" | string;
  frames: LiveFrame[];
}

export interface LiveFeedHandlers {
  onSnapshot: (runs: LiveSnapshotRun[]) => void;
  onFrame: (frame: LiveFrame) => void;
  onStatus?: (open: boolean) => void;
}

export interface LiveFeedHandle {
  close: () => void;
}

function resolveWsBase(): string {
  const base = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";
  if (/^https?:/i.test(base)) return base.replace(/^http/i, "ws");
  if (typeof window === "undefined") return `ws://localhost:8000${base}`;
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${base}`;
}

export function openLiveFeed(handlers: LiveFeedHandlers): LiveFeedHandle {
  let ws: WebSocket | null = null;
  let closed = false;
  let retry = 0;
  let reconnectTimer: number | undefined;

  const connect = () => {
    if (closed) return;
    ws = new WebSocket(`${resolveWsBase()}/admin/live`);

    ws.onopen = () => {
      retry = 0;
      handlers.onStatus?.(true);
    };
    ws.onmessage = (ev: MessageEvent<string>) => {
      let msg: unknown;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      const m = msg as { type?: string; runs?: LiveSnapshotRun[] };
      if (m.type === "snapshot" && Array.isArray(m.runs)) {
        handlers.onSnapshot(m.runs);
      } else {
        handlers.onFrame(msg as LiveFrame);
      }
    };
    ws.onclose = () => {
      handlers.onStatus?.(false);
      if (closed) return;
      // Exponential-ish backoff, capped at 10s.
      retry += 1;
      const delay = Math.min(10000, 500 * 2 ** Math.min(retry, 5));
      reconnectTimer = window.setTimeout(connect, delay);
    };
    ws.onerror = () => {
      try {
        ws?.close();
      } catch {
        /* ignore */
      }
    };
  };

  connect();

  return {
    close: () => {
      closed = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      try {
        ws?.close(1000, "client_done");
      } catch {
        /* ignore */
      }
    },
  };
}
