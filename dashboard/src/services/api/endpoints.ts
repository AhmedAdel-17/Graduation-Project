import { api } from "./client";
import type {
  BacktestCompareResponse,
  BacktestDetail,
  BacktestListResponse,
  ConfigResponse,
  ConfigUpdateRequest,
  ConfigUpdateResponse,
  FingerprintsResponse,
  HealthResponse,
  MemoryAgent,
  MemoryEntriesResponse,
  MemorySearchResponse,
  PredictionResult,
  PromptsResponse,
  ReflectionsResponse,
  ResultsListResponse,
  RlDecisionsResponse,
  RlStatusResponse,
  RunBacktestRequest,
  RunBacktestResponse,
  RunBtRequest,
  SessionTraceResponse,
  StockDataResponse,
  TickersResponse,
} from "./types";

export const endpoints = {
  health: () => api.get<HealthResponse>("/health"),

  // Read-only system config (LLM tiers, vendors, RL flag, risk limits)
  config: () => api.get<ConfigResponse>("/config"),

  // Mutate config (Settings page). Keys are merged server-side.
  updateConfig: (req: ConfigUpdateRequest) =>
    api.put<ConfigUpdateResponse>("/config", req),

  // EGX ticker catalogue
  tickers: () => api.get<TickersResponse>("/test/egx-tickers"),

  // Quick prediction (fast single-LLM summary, ~10-30s)
  runPrediction: (ticker?: string) =>
    api.post<PredictionResult>("/test/random-egx", ticker ? { ticker } : {}),

  // Full multi-agent pipeline (TradingAgentsGraph end-to-end, 3-8 min)
  runFullPipeline: (ticker: string) =>
    api.post<PredictionResult>("/analyze-full", { ticker }),

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

  // Past analysis sessions (legacy audit-log index)
  listResults: () => api.get<ResultsListResponse>("/results"),

  // Reasoning trace for a single session (Postgres preferred, JSONL fallback)
  sessionTrace: (sessionId: string) =>
    api.get<SessionTraceResponse>(
      `/sessions/${encodeURIComponent(sessionId)}/trace`
    ),

  // Memory + reflection surfaces (PR5)
  memorySearch: (
    agent: MemoryAgent,
    params?: {
      ticker?: string;
      q?: string;
      k?: number;
      min_similarity?: number;
    }
  ) => {
    const q = new URLSearchParams();
    if (params?.ticker) q.set("ticker", params.ticker);
    if (params?.q) q.set("q", params.q);
    if (params?.k !== undefined) q.set("k", String(params.k));
    if (params?.min_similarity !== undefined)
      q.set("min_similarity", String(params.min_similarity));
    const qs = q.toString();
    return api.get<MemorySearchResponse>(
      `/memory/${encodeURIComponent(agent)}/search${qs ? `?${qs}` : ""}`
    );
  },

  memoryEntries: (
    agent: MemoryAgent,
    params?: { ticker?: string; limit?: number }
  ) => {
    const q = new URLSearchParams();
    if (params?.ticker) q.set("ticker", params.ticker);
    if (params?.limit !== undefined) q.set("limit", String(params.limit));
    const qs = q.toString();
    return api.get<MemoryEntriesResponse>(
      `/memory/${encodeURIComponent(agent)}/entries${qs ? `?${qs}` : ""}`
    );
  },

  reflections: (params?: { ticker?: string; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.ticker) q.set("ticker", params.ticker);
    if (params?.limit !== undefined) q.set("limit", String(params.limit));
    const qs = q.toString();
    return api.get<ReflectionsResponse>(`/reflections${qs ? `?${qs}` : ""}`);
  },

  // Backtesting
  listBacktests: () => api.get<BacktestListResponse>("/backtests"),
  getBacktest: (sessionId: string) =>
    api.get<BacktestDetail>(
      `/backtests/${encodeURIComponent(sessionId)}`
    ),
  compareBacktests: (ticker: string) =>
    api.get<BacktestCompareResponse>(
      `/backtests/compare/${encodeURIComponent(ticker)}`
    ),
  runBacktest: (req: RunBacktestRequest) =>
    api.post<RunBacktestResponse>("/backtests/run", req),
  runBtBenchmark: (req: RunBtRequest) =>
    api.post<RunBacktestResponse>("/backtests/run-bt", req),

  // Diagnostics — prompt catalog + fingerprint drift (PR9)
  diagnosticsPrompts: () =>
    api.get<PromptsResponse>("/diagnostics/prompts"),
  diagnosticsFingerprints: (days?: number) =>
    api.get<FingerprintsResponse>(
      `/diagnostics/fingerprints${days ? `?days=${days}` : ""}`
    ),

  // RL meta-policy (PR7)
  rlStatus: () => api.get<RlStatusResponse>("/rl/status"),
  rlDecisions: (params?: {
    ticker?: string;
    session_id?: string;
    limit?: number;
  }) => {
    const q = new URLSearchParams();
    if (params?.ticker) q.set("ticker", params.ticker);
    if (params?.session_id) q.set("session_id", params.session_id);
    if (params?.limit !== undefined) q.set("limit", String(params.limit));
    const qs = q.toString();
    return api.get<RlDecisionsResponse>(`/rl/decisions${qs ? `?${qs}` : ""}`);
  },
};
