// Conversational state for the Portfolio Assistant. Two interchangeable
// transports behind one API: a `mock` transport that replays fixture-backed
// scripts (zero backend, for development + the demo) and a `live` transport that
// speaks the P4 WebSocket protocol (`WS /api/portfolio/chat/{id}`) plus REST for
// confirm. ServerEvents are folded into a flat ChatMessage thread.

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ChatBlock,
  InvestmentPolicy,
  PortfolioSnapshot,
  ScenarioPatch,
  ScenarioRef,
  ServerEvent,
} from "../../../services/api/portfolioTypes";
import { portfolioApi, portfolioWsUrl } from "../../../services/api/portfolioEndpoints";
import { mockTurn } from "./mockTransport";

export type Transport = "mock" | "live";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  kind: "text" | "extraction" | "clarification" | "policy" | "error" | "system";
  text?: string;
  blocks?: ChatBlock[];
  policy?: InvestmentPolicy;
  inferredFields?: string[];
  scenarioId?: number | null;
}

const uid = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);

export interface PortfolioChat {
  messages: ChatMessage[];
  busy: boolean;
  status: string | null;
  connected: boolean;
  // Workspace view-model (derived from the same event stream in mock + live).
  policy: InvestmentPolicy | null;
  scenarios: ScenarioRef[];
  activeRef: string;
  send: (text: string) => void;
  sendWhatIf: (patch: ScenarioPatch) => void;
  adopt: (scenarioId: number) => void;
  discardScenario: (scenarioId: number) => void;
  setActiveRef: (ref: string) => void;
  patchPolicy: (updates: Record<string, unknown>, confirm?: boolean) => void;
  confirmSnapshot: (snapshot: PortfolioSnapshot) => void;
}

export function usePortfolioChat({
  transport,
  conversationId,
}: {
  transport: Transport;
  conversationId?: string;
}): PortfolioChat {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [connected, setConnected] = useState(transport === "mock");
  const [policy, setPolicy] = useState<InvestmentPolicy | null>(null);
  const [scenarios, setScenarios] = useState<ScenarioRef[]>([]);
  const [activeRef, setActiveRef] = useState<string>("baseline");

  const wsRef = useRef<WebSocket | null>(null);
  const pendingScenario = useRef<number | null>(null);
  const timers = useRef<number[]>([]);

  const pushSystem = useCallback((text: string) => {
    setMessages((prev) => [...prev, { id: uid(), role: "assistant", kind: "system", text }]);
  }, []);

  const pushAssistant = useCallback((m: Omit<ChatMessage, "id" | "role">) => {
    setMessages((prev) => [...prev, { id: uid(), role: "assistant", ...m }]);
  }, []);

  const processEvent = useCallback(
    (ev: ServerEvent) => {
      switch (ev.type) {
        case "status":
          setStatus(ev.detail ?? ev.stage);
          break;
        case "clarification":
          pushAssistant({ kind: "clarification", text: ev.question });
          break;
        case "extraction":
          pushAssistant({ kind: "extraction", blocks: ev.blocks ?? [] });
          break;
        case "policy_update":
          setPolicy(ev.policy);
          pushAssistant({
            kind: "policy",
            policy: ev.policy,
            inferredFields: ev.inferred_fields,
            text: "Here's the investor profile I inferred — confirm or edit it.",
          });
          break;
        case "policy_flags":
          // Surfaced inside proposal messages as a block; nothing standalone.
          break;
        case "scenario_created":
          pendingScenario.current = ev.scenario.scenario_id ?? null;
          setScenarios((prev) => [...prev, ev.scenario]);
          if (ev.scenario.scenario_id != null) setActiveRef(String(ev.scenario.scenario_id));
          break;
        case "assistant_message":
          pushAssistant({
            kind: "text",
            text: ev.text,
            blocks: ev.blocks ?? [],
            scenarioId: ev.scenario_id ?? pendingScenario.current,
          });
          pendingScenario.current = null;
          break;
        case "error":
          pushAssistant({ kind: "error", text: ev.message });
          setBusy(false);
          setStatus(null);
          break;
        case "done":
          setBusy(false);
          setStatus(null);
          break;
      }
    },
    [pushAssistant]
  );

  // --- live WebSocket lifecycle ------------------------------------------
  useEffect(() => {
    if (transport !== "live" || !conversationId) return;
    let closed = false;
    let retry = 0;
    let reconnectTimer: number | undefined;

    const connect = () => {
      if (closed) return;
      const ws = new WebSocket(portfolioWsUrl(conversationId));
      wsRef.current = ws;
      ws.onopen = () => {
        retry = 0;
        setConnected(true);
      };
      ws.onmessage = (e: MessageEvent<string>) => {
        try {
          processEvent(JSON.parse(e.data) as ServerEvent);
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (closed) return;
        retry += 1;
        reconnectTimer = window.setTimeout(connect, Math.min(10000, 500 * 2 ** Math.min(retry, 5)));
      };
      ws.onerror = () => {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      };
    };
    connect();

    return () => {
      closed = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      try {
        wsRef.current?.close(1000, "unmount");
      } catch {
        /* ignore */
      }
      wsRef.current = null;
    };
  }, [transport, conversationId, processEvent]);

  // Clear any pending mock timers on unmount.
  useEffect(() => () => timers.current.forEach((t) => window.clearTimeout(t)), []);

  const replayMock = useCallback(
    (events: ServerEvent[]) => {
      events.forEach((ev, i) => {
        const t = window.setTimeout(() => processEvent(ev), 220 * (i + 1));
        timers.current.push(t);
      });
    },
    [processEvent]
  );

  const pushUser = useCallback((text: string) => {
    setMessages((prev) => [...prev, { id: uid(), role: "user", kind: "text", text }]);
  }, []);

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || busy) return;
      pushUser(trimmed);
      setBusy(true);
      setStatus("Thinking…");
      if (transport === "mock") {
        replayMock(mockTurn(trimmed));
      } else if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "user_message", text: trimmed, language: "auto" }));
      } else {
        pushAssistant({ kind: "error", text: "Not connected — reconnecting…" });
        setBusy(false);
        setStatus(null);
      }
    },
    [busy, transport, pushUser, replayMock, pushAssistant]
  );

  const sendWhatIf = useCallback(
    (patch: ScenarioPatch) => {
      if (busy) return;
      pushUser(patch.label || "What-if scenario");
      setBusy(true);
      setStatus("Forking a scenario…");
      if (transport === "mock") {
        replayMock(mockTurn(patch.label || "what if"));
      } else if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "what_if", patch }));
      }
    },
    [busy, transport, pushUser, replayMock]
  );

  const adopt = useCallback(
    (scenarioId: number) => {
      if (busy) return;
      setBusy(true);
      // Promote locally: the adopted scenario becomes the baseline, siblings are
      // pruned, and the canvas returns to baseline (mirrors the server).
      setScenarios((prev) =>
        prev.map((s) => ({ ...s, status: s.scenario_id === scenarioId ? "promoted" : "discarded" }))
      );
      setActiveRef("baseline");
      if (transport === "mock") {
        replayMock(mockTurn("adopt"));
      } else if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "adopt_scenario", scenario_id: scenarioId }));
      }
    },
    [busy, transport, replayMock]
  );

  const discardScenario = useCallback(
    (scenarioId: number) => {
      setScenarios((prev) =>
        prev.map((s) => (s.scenario_id === scenarioId ? { ...s, status: "discarded" } : s))
      );
      setActiveRef((cur) => (cur === String(scenarioId) ? "baseline" : cur));
      if (transport === "live") {
        portfolioApi.discardScenario(scenarioId).catch(() => {/* best-effort */});
      }
    },
    [transport]
  );

  const patchPolicy = useCallback(
    (updates: Record<string, unknown>, confirm = true) => {
      const apply = (p: InvestmentPolicy) => {
        setPolicy(p);
        const summary = Object.entries(updates)
          .map(([k, v]) => `${k.replace(/_/g, " ")} → ${String(v).replace(/_/g, " ")}`)
          .join(", ");
        pushSystem(`Policy updated: ${summary}. The next optimization will use it.`);
      };
      if (transport === "live" && conversationId) {
        portfolioApi
          .patchPolicy(conversationId, updates, confirm)
          .then((res) => apply(res.policy))
          .catch((e) => pushAssistant({ kind: "error", text: String(e) }));
      } else {
        const base: InvestmentPolicy = policy ?? {
          objective: "balanced", risk_tolerance: "medium", horizon: "1_3y",
          version: 1, confirmed_by_user: false,
        };
        apply({ ...base, ...updates, confirmed_by_user: confirm, version: (base.version ?? 1) + 1 });
      }
    },
    [transport, conversationId, policy, pushSystem, pushAssistant]
  );

  const confirmSnapshot = useCallback(
    (snapshot: PortfolioSnapshot) => {
      if (transport === "mock") {
        pushAssistant({
          kind: "text",
          text: "Portfolio confirmed — ask me to optimize it or explore a what-if.",
        });
        return;
      }
      if (!conversationId) return;
      setBusy(true);
      portfolioApi
        .confirmSnapshot(conversationId, snapshot)
        .then((res) => res.events.forEach(processEvent))
        .catch((e) => pushAssistant({ kind: "error", text: String(e) }))
        .finally(() => setBusy(false));
    },
    [transport, conversationId, processEvent, pushAssistant]
  );

  return {
    messages, busy, status, connected,
    policy, scenarios, activeRef,
    send, sendWhatIf, adopt, discardScenario, setActiveRef, patchPolicy, confirmSnapshot,
  };
}
