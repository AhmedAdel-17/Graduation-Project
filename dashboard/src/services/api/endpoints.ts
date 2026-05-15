import { api } from "./client";
import type {
  BacktestCompareResponse,
  BacktestListResponse,
  HealthResponse,
  PredictionResult,
  RunBacktestRequest,
  RunBacktestResponse,
  RunBtRequest,
  StockDataResponse,
  TickersResponse,
} from "./types";

export const endpoints = {
  health: () => api.get<HealthResponse>("/health"),

  // EGX ticker catalogue
  tickers: () => api.get<TickersResponse>("/test/egx-tickers"),

  // Quick prediction (fast multi-agent summary)
  runPrediction: (ticker?: string) =>
    api.post<PredictionResult>("/test/random-egx", ticker ? { ticker } : {}),

  // Historical OHLCV
  stockData: (
    ticker: string,
    params?: { start_date?: string; end_date?: string; days?: number }
  ) => {
    const q = new URLSearchParams();
    if (params?.start_date) q.set("start_date", params.start_date);
    if (params?.end_date) q.set("end_date", params.end_date);
    if (params?.days) q.set("days", String(params.days));
    const qs = q.toString();
    return api.get<StockDataResponse>(
      `/stock/${encodeURIComponent(ticker)}${qs ? `?${qs}` : ""}`
    );
  },

  // Backtesting
  listBacktests: () => api.get<BacktestListResponse>("/backtests"),
  compareBacktests: (ticker: string) =>
    api.get<BacktestCompareResponse>(
      `/backtests/compare/${encodeURIComponent(ticker)}`
    ),
  runBacktest: (req: RunBacktestRequest) =>
    api.post<RunBacktestResponse>("/backtests/run", req),
  runBtBenchmark: (req: RunBtRequest) =>
    api.post<RunBacktestResponse>("/backtests/run-bt", req),
};
