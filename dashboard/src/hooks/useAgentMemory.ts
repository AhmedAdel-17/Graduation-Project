import { useQueries, useQuery } from "@tanstack/react-query";
import { endpoints } from "../services/api";
import type {
  MemoryAgent,
  MemorySearchResponse,
  ReflectionsResponse,
} from "../services/api/types";

// Default minimum-similarity threshold mirrors
// tradingagents.default_config.memory_min_similarity (0.30).
export const DEFAULT_MIN_SIMILARITY = 0.3;

export const MEMORY_AGENTS: MemoryAgent[] = [
  "bull_memory",
  "bear_memory",
  "trader_memory",
  "invest_judge_memory",
  "risk_manager_memory",
];

/**
 * Fan-out top-K memory search across all five agent collections in one shot.
 * Each query is keyed independently so they cache + refetch on their own
 * but render together.
 */
export function useAgentMemoryFanout(params: {
  ticker: string | null;
  k?: number;
  minSimilarity?: number | null;
  agents?: MemoryAgent[];
}) {
  const agents = params.agents ?? MEMORY_AGENTS;
  const k = params.k ?? 3;
  const min = params.minSimilarity ?? DEFAULT_MIN_SIMILARITY;

  return useQueries({
    queries: agents.map((agent) => ({
      queryKey: ["memorySearch", agent, params.ticker, k, min],
      queryFn: () =>
        endpoints.memorySearch(agent, {
          ticker: params.ticker ?? undefined,
          k,
          // null skips the threshold; the endpoint accepts that gracefully.
          ...(min !== null ? { min_similarity: min } : {}),
        }),
      enabled: Boolean(params.ticker),
      staleTime: 60_000,
      retry: 0,
    })),
    combine: (results) => ({
      isLoading: results.some((r) => r.isLoading),
      isError: results.some((r) => r.isError),
      byAgent: Object.fromEntries(
        agents.map((agent, i) => [agent, results[i].data ?? null])
      ) as Record<MemoryAgent, MemorySearchResponse | null>,
    }),
  });
}

/**
 * Recent reflections across all five collections, filtered by ticker.
 */
export function useReflections(
  ticker: string | null,
  options?: { limit?: number }
) {
  return useQuery<ReflectionsResponse>({
    queryKey: ["reflections", ticker, options?.limit ?? 20],
    queryFn: () =>
      endpoints.reflections({
        ticker: ticker ?? undefined,
        limit: options?.limit ?? 20,
      }),
    enabled: Boolean(ticker),
    staleTime: 60_000,
    retry: 0,
  });
}
