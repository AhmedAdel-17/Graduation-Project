import { useQuery } from "@tanstack/react-query";
import { endpoints } from "../services/api";
import type {
  ResultSessionSummary,
  SessionTraceResponse,
} from "../services/api/types";

/**
 * Fetch the reasoning trace for one analysis session.
 * Returns null `data` while disabled (no session_id) — callers should
 * branch on `data` rather than `isLoading` alone.
 */
export function useSessionTrace(sessionId: string | null | undefined) {
  return useQuery<SessionTraceResponse>({
    queryKey: ["sessionTrace", sessionId],
    queryFn: () => endpoints.sessionTrace(sessionId as string),
    enabled: Boolean(sessionId),
    staleTime: 60_000,
    retry: 0,
  });
}

/**
 * Pull the index of audit-logged sessions. Used by the Workspace Overview
 * tab to discover the most recent session for a given ticker.
 */
export function useResultsIndex(options?: { staleMs?: number }) {
  return useQuery({
    queryKey: ["resultsIndex"],
    queryFn: () => endpoints.listResults(),
    staleTime: options?.staleMs ?? 30_000,
    retry: 0,
  });
}

/**
 * Convenience helper: from a results index + a ticker, return the most
 * recent session summary. Callers can chain into `useSessionTrace`.
 */
export function pickLatestForTicker(
  sessions: ResultSessionSummary[] | undefined,
  ticker: string
): ResultSessionSummary | null {
  if (!sessions || sessions.length === 0) return null;
  const matching = sessions.filter((s) => s.ticker === ticker);
  if (matching.length === 0) return null;
  // The /api/results endpoint already sorts by timestamp desc, but we
  // re-sort defensively so a stale cached index doesn't mislead us.
  return matching.sort((a, b) => b.timestamp.localeCompare(a.timestamp))[0];
}

/**
 * One-stop hook used by every Workspace tab: index → latest summary →
 * trace. Saves each tab from re-implementing the same three-line chain.
 */
export function useLatestSessionTrace(ticker: string) {
  const index = useResultsIndex();
  const summary = pickLatestForTicker(index.data?.sessions, ticker);
  const trace = useSessionTrace(summary?.session_id ?? null);
  return {
    loading: index.isLoading || trace.isLoading,
    error: index.error || trace.error,
    summary,
    source: trace.data?.source ?? null,
    session: trace.data?.session ?? null,
    events: trace.data?.events ?? [],
  };
}
