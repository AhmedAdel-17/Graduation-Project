import { useCallback, useEffect, useRef, useState } from "react";
import {
  openAnalyzeStream,
  type AgentUpdate,
  type AnalyzeRequest,
  type NodeStatus,
  type StreamHandle,
} from "../services/api/wsClient";

export type StreamStatus =
  | "idle"
  | "connecting"
  | "streaming"
  | "complete"
  | "error"
  | "closed";

export interface UseAgentStreamResult {
  events: AgentUpdate[];
  lastEvent: AgentUpdate | null;
  nodeStatuses: Record<string, NodeStatus>;
  status: StreamStatus;
  error: string | null;
  lastMessageAt: number | null;
  start: (req: AnalyzeRequest) => void;
  cancel: () => void;
  reset: () => void;
}

// One run = one hook instance. Connection is torn down on unmount or cancel.
// Status transitions:
//   idle → connecting → streaming → complete | error | closed
export function useAgentStream(): UseAgentStreamResult {
  const [events, setEvents] = useState<AgentUpdate[]>([]);
  const [nodeStatuses, setNodeStatuses] = useState<Record<string, NodeStatus>>(
    {}
  );
  const [status, setStatus] = useState<StreamStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [lastMessageAt, setLastMessageAt] = useState<number | null>(null);

  const handleRef = useRef<StreamHandle | null>(null);

  const closeSocket = useCallback(() => {
    handleRef.current?.close();
    handleRef.current = null;
  }, []);

  useEffect(() => {
    return () => {
      closeSocket();
    };
  }, [closeSocket]);

  const start = useCallback(
    (req: AnalyzeRequest) => {
      // Tear down any in-flight stream before opening a new one.
      closeSocket();
      setEvents([]);
      setNodeStatuses({});
      setError(null);
      setLastMessageAt(null);
      setStatus("connecting");

      handleRef.current = openAnalyzeStream(req, {
        onOpen: () => setStatus("streaming"),
        onMessage: (update) => {
          setLastMessageAt(Date.now());
          setEvents((prev) => [...prev, update]);

          // Status precedence: error > completed > in_progress > idle.
          // Once a node is completed, in_progress events for it shouldn't
          // demote it back; an error overrides everything.
          setNodeStatuses((prev) => {
            const current = prev[update.node];
            if (current === "error") return prev;
            if (update.status === "error") {
              return { ...prev, [update.node]: "error" };
            }
            if (current === "completed" && update.status !== "completed") {
              return prev;
            }
            return { ...prev, [update.node]: update.status };
          });

          if (update.type === "complete") {
            setStatus("complete");
          } else if (update.type === "error") {
            const msg =
              (typeof update.data?.message === "string"
                ? (update.data.message as string)
                : null) ?? "Stream error";
            setError(msg);
            setStatus("error");
          }
        },
        onError: () => {
          setError((prev) => prev ?? "WebSocket connection error");
          setStatus((prev) =>
            prev === "complete" ? prev : "error"
          );
        },
        onClose: (info) => {
          setStatus((prev) => {
            if (prev === "complete" || prev === "error") return prev;
            if (info.wasClean) return "closed";
            // Unexpected closure: surface as error if no message at all
            // came through, otherwise treat as graceful close.
            return info.code === 1000 ? "closed" : "error";
          });
        },
      });
    },
    [closeSocket]
  );

  const cancel = useCallback(() => {
    closeSocket();
    setStatus("closed");
  }, [closeSocket]);

  const reset = useCallback(() => {
    closeSocket();
    setEvents([]);
    setNodeStatuses({});
    setError(null);
    setLastMessageAt(null);
    setStatus("idle");
  }, [closeSocket]);

  const lastEvent = events.length ? events[events.length - 1] : null;

  return {
    events,
    lastEvent,
    nodeStatuses,
    status,
    error,
    lastMessageAt,
    start,
    cancel,
    reset,
  };
}
