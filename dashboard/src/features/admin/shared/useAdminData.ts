import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { adminEndpoints } from "../../../services/api/adminEndpoints";
import type {
  AdminAgentMetric,
  AdminErrorRecord,
  AdminMetricsResponse,
} from "../../../services/api/adminTypes";
import { catalogNodeByAgentName } from "../../../data/agent-catalog";

// Real data only — no mock fallback. When Postgres is unavailable / empty the
// hooks return empty payloads and `available: false` so screens can render an
// honest "no data" / "connect the audit DB" state instead of fabricated numbers.

const EMPTY_METRICS: AdminMetricsResponse = {
  source: "none",
  days: 0,
  total_runs: 0,
  total_events: 0,
  avg_confidence: null,
  daily_runs: [],
  decision_counts: {},
  per_agent: [],
};

export function useAdminMetrics(days: number): {
  metrics: AdminMetricsResponse;
  available: boolean;
  isLoading: boolean;
  isError: boolean;
  refetch: () => void;
} {
  const q = useQuery<AdminMetricsResponse>({
    queryKey: ["admin", "metrics", days],
    queryFn: () => adminEndpoints.metrics(days),
    retry: 0,
    staleTime: 30_000,
  });
  const available = q.data?.source === "postgres";
  return {
    metrics: q.data ?? EMPTY_METRICS,
    available,
    isLoading: q.isLoading,
    isError: q.isError,
    refetch: () => q.refetch(),
  };
}

export interface AgentAggregate {
  executions: number;
  avgConfidence: number | null;
  lastSeen: string | null;
}

/** Real per-agent aggregates keyed by catalog node id (from agent_events). */
export function useAgentAggregates(days = 30): {
  byNodeId: Record<string, AgentAggregate>;
  available: boolean;
  isLoading: boolean;
} {
  const { metrics, available, isLoading } = useAdminMetrics(days);
  const byNodeId = useMemo(() => {
    const out: Record<string, AgentAggregate> = {};
    for (const a of metrics.per_agent as AdminAgentMetric[]) {
      if (!a.agent_name) continue;
      const node = catalogNodeByAgentName(a.agent_name);
      if (!node) continue;
      out[node.id] = {
        executions: a.executions,
        avgConfidence: a.avg_confidence,
        lastSeen: a.last_seen ?? null,
      };
    }
    return out;
  }, [metrics.per_agent]);
  return { byNodeId, available, isLoading };
}

export function useAdminErrors(days: number): {
  errors: AdminErrorRecord[];
  available: boolean;
  isLoading: boolean;
  isError: boolean;
  refetch: () => void;
} {
  const q = useQuery({
    queryKey: ["admin", "errors", days],
    queryFn: () => adminEndpoints.errors(days),
    retry: 0,
    staleTime: 30_000,
  });
  return {
    errors: q.data?.errors ?? [],
    available: q.data?.source === "postgres",
    isLoading: q.isLoading,
    isError: q.isError,
    refetch: () => q.refetch(),
  };
}
