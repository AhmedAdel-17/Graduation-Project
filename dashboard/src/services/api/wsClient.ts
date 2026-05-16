// WebSocket plumbing for the FastAPI streaming endpoint (server/api_server.py:1085).
//
// Server protocol:
//   client → server : single JSON message { ticker, trade_date, selected_analysts,
//                                           max_debate_rounds, max_risk_rounds }
//   server → client : stream of AgentUpdate frames until type === "complete"
//                     or type === "error".
//
// We don't reconnect mid-stream: the graph is stateful and the server-side
// `active_analyses[ticker]` lock means a fresh connect would be rejected
// anyway. Clients should restart the run instead.

export type NodeStatus = "idle" | "in_progress" | "completed" | "error";

export interface AgentUpdate {
  type: "agent_update" | "complete" | "error";
  node: string;
  status: NodeStatus;
  state_keys: string[];
  data: Record<string, unknown>;
  timestamp: string;
}

export interface AnalyzeRequest {
  ticker: string;
  trade_date: string;
  selected_analysts: string[];
  max_debate_rounds: number;
  max_risk_rounds: number;
}

function resolveWsBase(): string {
  const base = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";
  if (/^https?:/i.test(base)) {
    // Absolute http(s) base — swap protocol.
    return base.replace(/^http/i, "ws");
  }
  // Relative base — bolt onto the current page origin.
  if (typeof window === "undefined") return `ws://localhost:8000${base}`;
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${base}`;
}

export function analyzeStreamUrl(): string {
  return `${resolveWsBase()}/analyze`;
}

export interface StreamHandlers {
  onMessage: (update: AgentUpdate) => void;
  onOpen?: () => void;
  onClose?: (info: { code: number; reason: string; wasClean: boolean }) => void;
  onError?: (err: Event) => void;
}

export interface StreamHandle {
  send: (msg: Record<string, unknown>) => void;
  close: () => void;
  readyState: () => number;
}

// Thin wrapper over WebSocket. Keeps parsing logic and lifecycle in one place
// so React hooks don't need to babysit the low-level events.
export function openAnalyzeStream(
  request: AnalyzeRequest,
  handlers: StreamHandlers
): StreamHandle {
  const ws = new WebSocket(analyzeStreamUrl());

  ws.onopen = () => {
    handlers.onOpen?.();
    try {
      ws.send(JSON.stringify(request));
    } catch (err) {
      // The error path will fire ws.onerror immediately after.
      console.error("[wsClient] failed to send analysis request", err);
    }
  };

  ws.onmessage = (ev: MessageEvent<string>) => {
    let parsed: AgentUpdate;
    try {
      parsed = JSON.parse(ev.data) as AgentUpdate;
    } catch {
      // Server should always emit JSON, but stay defensive.
      console.warn("[wsClient] non-JSON frame ignored", ev.data);
      return;
    }
    handlers.onMessage(parsed);
  };

  ws.onerror = (err) => {
    handlers.onError?.(err);
  };

  ws.onclose = (ev) => {
    handlers.onClose?.({
      code: ev.code,
      reason: ev.reason,
      wasClean: ev.wasClean,
    });
  };

  return {
    send: (msg) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
    },
    close: () => {
      if (
        ws.readyState === WebSocket.OPEN ||
        ws.readyState === WebSocket.CONNECTING
      ) {
        ws.close(1000, "client_cancel");
      }
    },
    readyState: () => ws.readyState,
  };
}
