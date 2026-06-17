import { useQueries } from "@tanstack/react-query";
import { endpoints } from "../../../services/api";
import type {
  ResultSessionSummary,
  SessionTraceResponse,
} from "../../../services/api/types";
import { decisionToSignal, type SignalKind } from "../shared/signal";

export type RunStatus = "completed" | "veto" | "no_data" | "loading";

export interface RunRow {
  sessionId: string;
  ticker: string;
  tradeDate: string;
  timestamp: string;
  recommendation: SignalKind;
  confidence: number | null; // 0..1
  durationMs: number | null;
  events: number;
  status: RunStatus;
}

function durationOf(events: { logged_at?: string | null }[]): number | null {
  const ts = events
    .map((e) => (e.logged_at ? Date.parse(e.logged_at) : NaN))
    .filter((n) => !Number.isNaN(n));
  if (ts.length < 2) return null;
  return Math.max(...ts) - Math.min(...ts);
}

// Enrich a page of session summaries with their trace-derived recommendation,
// confidence, duration, and status. One trace query per row, cache-shared with
// the Trace Inspector (same queryKey), so navigating between them is instant.
export function useRunRows(sessions: ResultSessionSummary[]): RunRow[] {
  const results = useQueries({
    queries: sessions.map((s) => ({
      queryKey: ["sessionTrace", s.session_id],
      queryFn: () => endpoints.sessionTrace(s.session_id),
      staleTime: 60_000,
      retry: 0,
    })),
  });

  return sessions.map((s, i) => {
    const q = results[i];
    const base = {
      sessionId: s.session_id,
      ticker: s.ticker,
      tradeDate: s.trade_date,
      timestamp: s.timestamp,
    };
    if (q.isLoading) {
      return {
        ...base,
        recommendation: "UNKNOWN" as SignalKind,
        confidence: null,
        durationMs: null,
        events: 0,
        status: "loading" as RunStatus,
      };
    }
    const data = q.data as SessionTraceResponse | undefined;
    const session = data?.session ?? null;
    const events = data?.events ?? [];
    const hasData = Boolean(session) || events.length > 0;
    const status: RunStatus = !hasData
      ? "no_data"
      : session?.risk_veto
      ? "veto"
      : "completed";
    return {
      ...base,
      recommendation: decisionToSignal(session?.final_decision),
      confidence:
        typeof session?.confidence_overall === "number"
          ? session.confidence_overall
          : null,
      durationMs: durationOf(events),
      events: events.length,
      status,
    };
  });
}
