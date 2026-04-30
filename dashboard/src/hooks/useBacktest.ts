import { useMutation, useQuery } from "@tanstack/react-query";
import { endpoints } from "../services/api";
import type {
  BacktestCompareResponse,
  BacktestListResponse,
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
