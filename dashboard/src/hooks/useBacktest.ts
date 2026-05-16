import { useMutation, useQuery } from "@tanstack/react-query";
import { endpoints } from "../services/api";
import type {
  BacktestCompareResponse,
  BacktestDetail,
  BacktestListResponse,
  RlDecisionsResponse,
  RlStatusResponse,
  RunBacktestRequest,
  RunBtRequest,
} from "../services/api/types";

export function useBacktests(autoRefetch = false) {
  return useQuery<BacktestListResponse>({
    queryKey: ["backtests"],
    queryFn: () => endpoints.listBacktests(),
    refetchInterval: autoRefetch ? 5_000 : false,
  });
}

export function useBacktestCompare(ticker: string | null, enabled = true) {
  return useQuery<BacktestCompareResponse>({
    queryKey: ["backtest-compare", ticker],
    queryFn: () => endpoints.compareBacktests(ticker as string),
    enabled: !!ticker && enabled,
    retry: 0,
  });
}

export function useRunBacktest() {
  return useMutation({
    mutationFn: (req: RunBacktestRequest) => endpoints.runBacktest(req),
  });
}

export function useRunBtBenchmark() {
  return useMutation({
    mutationFn: (req: RunBtRequest) => endpoints.runBtBenchmark(req),
  });
}

export function useBacktestDetail(sessionId: string | null | undefined) {
  return useQuery<BacktestDetail>({
    queryKey: ["backtest-detail", sessionId],
    queryFn: () => endpoints.getBacktest(sessionId as string),
    enabled: Boolean(sessionId),
    staleTime: 60_000,
    retry: 0,
  });
}

export function useRlStatus() {
  return useQuery<RlStatusResponse>({
    queryKey: ["rl-status"],
    queryFn: () => endpoints.rlStatus(),
    staleTime: 60_000,
    retry: 0,
  });
}

export function useRlDecisions(params?: {
  ticker?: string | null;
  sessionId?: string | null;
  limit?: number;
}) {
  return useQuery<RlDecisionsResponse>({
    queryKey: [
      "rl-decisions",
      params?.ticker ?? null,
      params?.sessionId ?? null,
      params?.limit ?? 50,
    ],
    queryFn: () =>
      endpoints.rlDecisions({
        ticker: params?.ticker ?? undefined,
        session_id: params?.sessionId ?? undefined,
        limit: params?.limit ?? 50,
      }),
    staleTime: 60_000,
    retry: 0,
  });
}
