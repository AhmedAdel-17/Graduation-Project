// Mirrors server/api_server.py payload shapes.

export interface ConfigResponse {
  config: Record<string, unknown>;
  available_vendors?: Record<string, string[]>;
  tool_categories?: Record<string, string[]>;
  risk_limits?: Record<string, unknown>;
}

export interface Ticker {
  ticker: string;
  name: string;
}

export interface TickersResponse {
  tickers: Ticker[];
}

export interface HealthMemoryBlock {
  backend: string;
  vector_store: string;
  postgres_vector_required: boolean;
  chroma_persist_dir?: string | null;
  chroma_persistent?: boolean | null;
  chroma_collection_counts?: Record<string, number> | null;
  chroma_total_documents?: number | null;
  seeded?: Record<string, boolean>;
  min_similarity?: number;
  embeddings_active?: boolean;
  retrieval_mode?: string;
}

export interface HealthPostgresBlock {
  configured: boolean;
  reachable: boolean | null;
  purpose: string;
  audit_write_lag_seconds?: number | null;
  backtest_runs_count?: number | null;
}

export interface HealthRedisBlock {
  configured: boolean;
  package_available: boolean;
  reachable: boolean | null;
  purpose: string;
}

export interface HealthResponse {
  status: string;
  timestamp: string;
  egx_tools: boolean;
  diagnostics?: {
    memory?: HealthMemoryBlock;
    postgres?: HealthPostgresBlock;
    redis?: HealthRedisBlock;
    degraded: boolean;
    degraded_reasons: string[];
  };
}

export interface StockBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface StockDataResponse {
  ticker: string;
  start_date: string;
  end_date: string;
  data: {
    symbol?: string;
    total_records?: number;
    data?: StockBar[];
    avg_daily_volume?: number;
  } | StockBar[] | string;
}

export type Signal = "BUY" | "SELL" | "HOLD" | "STRONG_BUY" | "STRONG_SELL" | string;
export type Confidence = "HIGH" | "MEDIUM" | "LOW" | string;

export interface Recommendation {
  signal?: Signal;
  confidence?: Confidence;
  risk?: string;
  target_price?: number | null;
  stop_loss?: number | null;
  bull_case?: string;
  bear_case?: string;
  neutral_case?: string;
  rationale?: string;
  recommendation?: string;
}

export interface PriceBlock {
  current?: number;
  daily_change?: number;
  weekly_change?: number;
  source?: string;
}

export interface IndicatorsBlock {
  sma_5?: number | string;
  sma_10?: number | string;
  rsi?: number | string;
  trend?: string;
}

export interface DecisionStep {
  agent: string;
  signal: string;
  detail: string;
}

export interface DecisionExplanation {
  headline: string;
  steps: DecisionStep[];
  risk_blocked: boolean;
}

export interface PredictionResult {
  error?: string;
  session_id?: string;
  ticker?: string;
  name?: string;
  price?: PriceBlock;
  indicators?: IndicatorsBlock;
  recommendation?: Recommendation;
  decision_explanation?: DecisionExplanation;
  price_history?: StockBar[];
  llm_error?: string | null;
  status?: string;
  // Allow arbitrary extra keys
  [k: string]: unknown;
}

export interface BacktestMetrics {
  total_return?: number;
  total_return_pct?: number;
  annualized_return?: number;
  sharpe_ratio?: number;
  max_drawdown?: number;
  max_drawdown_pct?: number;
  win_rate?: number;
  profit_factor?: number;
  total_trades?: number;
  // Number of closed (SELL) trades — used by the dashboard to decide
  // whether realised-PnL / win-rate metrics are meaningful in the summary.
  closed_trades?: number;
  // Buy-and-hold of the same ticker for the same window (always available).
  buyhold_return_pct?: number;
  // EGX30 buy-and-hold return for the same window (when CSV available).
  benchmark_return_pct?: number;
  // Strategy return − benchmark return (always derived from the two above).
  alpha_pct?: number;
  avg_trade_return?: number;
  final_equity?: number;
  initial_capital?: number;
  [k: string]: number | undefined;
}

// Structured EGX30 alignment block emitted by
// BacktestingEngine._align_benchmark_to_strategy(). Present on
// BacktestDetail.benchmark when the JSON report carries it.
export interface BenchmarkBlock {
  name?: string;
  source?: string;
  first_aligned_date?: string;
  last_aligned_date?: string;
  n_aligned_days?: number;
  coverage_pct?: number;
  total_return_pct?: number;
  annualized_return_pct?: number | null;
  alpha_pct?: number;
  tracking_error_pct?: number | null;
  note?: string;
  error?: string;
  [k: string]: unknown;
}

// Trader's structured exit plan attached to each BUY (and passed through
// on SELL). Shape mirrors execution_plan.exit_logic from the agent graph.
export interface ExitPlan {
  take_profit?: Record<string, { price?: number; pct_of_position?: number }>;
  stop_loss?: { price?: number; type?: string; note?: string };
  time_stop?: string;
  invalidation_triggers?: string[];
  conviction?: string;
}

// Compact bull/bear thesis structure surfaced in audit_log entries. Built
// by scripts/backtester._summarize_thesis().
export interface ThesisSummary {
  conviction_level?: string;
  time_horizon?: string;
  alignment_score?: string;
  catalysts?: string[];
  invalidation?: string[];
  base_case_upside_pct?: number;
  downside_risk_pct?: number;
}

// One audit_log entry per evaluation date. Carries the per-date agent
// text the dashboard renders in the Bull / Bear / Risk cards.
export interface BacktestAuditEntry {
  date?: string;
  price?: number;
  parsed_decision?: string;
  decision_path?: string;
  confidence?: number;
  risk_action?: string;
  risk_approved?: boolean | null;
  risk_violations?: number;
  critical_violations?: number;
  execution_plan_decision?: string;
  reasoning_score?: number;
  llm_calls?: number;
  trade_time_s?: number;
  risk_judge_text?: string | null;
  bull_thesis_summary?: ThesisSummary | null;
  bear_thesis_summary?: ThesisSummary | null;
  bear_thesis_present?: boolean;
  /** Per-date "why" — present for every evaluation, including HOLD dates. */
  reasoning?: string | null;
  /** One-line rationale extracted from the debate judge's verdict JSON. */
  judge_rationale?: string | null;
  [k: string]: unknown;
}

export interface BacktestTrade {
  date?: string;
  ticker?: string;
  action?: string;
  price?: number;
  quantity?: number;
  shares?: number;
  exec_price?: number;
  close_price?: number;
  value?: number;
  commission?: number;
  realized_pnl?: number;
  pnl?: number;
  signal?: string;
  // Structured exit plan from the trader's execution_plan.
  exit_plan?: ExitPlan | null;
  confidence?: number;
  reasoning?: string;
  // PR C of MEMORY.md §4b — present only when rl_meta_policy_enabled.
  rl_meta_policy_enabled?: boolean;
  rl_size_multiplier?: number;
  rl_action_index?: number;
  rl_model_fingerprint?: Record<string, unknown> | string;
  rl_feature_version?: string;
  [k: string]: unknown;
}

export interface EquityPoint {
  date: string;
  equity?: number;
  value?: number;
  close?: number;
  [k: string]: unknown;
}

export interface BacktestSession {
  session_id: string;
  ticker: string;
  engine: "llm_multi_agent" | "classical_technical";
  metrics: BacktestMetrics;
  total_trades: number;
}

export interface BacktestListResponse {
  sessions: BacktestSession[];
}

export interface BacktestCompareResponse {
  ticker: string;
  llm: {
    session_id: string;
    metrics: BacktestMetrics;
    trades: BacktestTrade[];
    daily_portfolio: EquityPoint[];
    benchmark_history: EquityPoint[];
  } | null;
  bt: {
    session_id: string;
    metrics: BacktestMetrics;
    trades: BacktestTrade[];
    daily_portfolio: EquityPoint[];
  } | null;
}

export interface RunBacktestRequest {
  ticker: string;
  start_date: string;
  end_date: string;
  interval?: number;
  initial_capital?: number;
  selected_analysts?: string[];
}

export interface RunBtRequest {
  ticker: string;
  start_date: string;
  end_date: string;
  initial_capital?: number;
}

export interface RunBacktestResponse {
  status: string;
  message: string;
}

// ── /api/sessions/{id}/trace ───────────────────────────────────────────────

export interface SessionTraceSummary {
  session_id?: string | null;
  ticker?: string | null;
  trade_date?: string | null;
  market?: string | null;
  final_decision?: string | null;
  risk_veto?: boolean | null;
  confidence_overall?: number | null;
  confidence_scores?: Record<string, unknown> | null;
  execution_plan?: Record<string, unknown> | null;
  risk_assessment?: Record<string, unknown> | null;
  data_quality?: Record<string, unknown> | null;
  full_state?: Record<string, unknown> | null;
  model_fingerprint?: Record<string, unknown> | null;
  user_id?: string | null;
  created_at?: string | null;
}

export interface SessionTraceEvent {
  event_type?: string | null;
  agent_name?: string | null;
  opinion_type?: string | null;
  opinion_summary?: string | null;
  confidence_score?: number | null;
  structured_output?: Record<string, unknown> | null;
  model_fingerprint?: Record<string, unknown> | null;
  logged_at?: string | null;
}

export interface SessionTraceResponse {
  source: "postgres" | "jsonl" | "none";
  session: SessionTraceSummary | null;
  events: SessionTraceEvent[];
}

// ── /api/results (legacy index of audit-log sessions) ──────────────────────

export interface ResultSessionSummary {
  ticker: string;
  session_id: string;
  trade_date: string;
  market: string;
  timestamp: string;
}

export interface ResultsListResponse {
  sessions: ResultSessionSummary[];
}

// ── /api/backtests/{session_id} (PR7) ──────────────────────────────────────

export interface BacktestDetail {
  session_id: string;
  ticker?: string;
  start_date?: string;
  end_date?: string;
  engine?: string;
  metrics: BacktestMetrics;
  trades: BacktestTrade[];
  daily_portfolio: EquityPoint[];
  benchmark_history?: EquityPoint[];
  // Same-ticker buy-and-hold equity curve (initial_capital * price/price_0).
  // Used by the Scenario Comparison section.
  buyhold_history?: EquityPoint[];
  // Structured EGX30 alignment block (see BenchmarkBlock).
  benchmark?: BenchmarkBlock;
  audit_log?: unknown[];
  cost_model?: Record<string, unknown>;
  [k: string]: unknown;
}

// ── /api/rl/status and /api/rl/decisions (PR7) ─────────────────────────────

export interface RlStatusResponse {
  enabled: boolean;
  model_path: string | null;
  loaded: boolean;
  feature_version: string | null;
  model_fingerprint: Record<string, unknown> | null;
}

export interface RlDecision {
  session_id: string;
  event_type: string;
  agent_name: string;
  opinion_summary: string | null;
  confidence_score: number | null;
  structured_output: Record<string, unknown> | null;
  model_fingerprint: Record<string, unknown> | null;
  logged_at: string | null;
  ticker: string | null;
  trade_date: string | null;
}

export interface RlDecisionsResponse {
  decisions: RlDecision[];
  source: "postgres" | "none";
  reason?: string;
  ticker?: string | null;
  session_id?: string | null;
  total?: number;
}

// ── /api/memory/{agent}/search and /entries (PR5) ──────────────────────────

export type MemoryAgent =
  | "bull_memory"
  | "bear_memory"
  | "trader_memory"
  | "invest_judge_memory"
  | "risk_manager_memory";

export interface MemoryMetadata {
  ticker?: string;
  trade_date?: string;
  memory_type?: string;
  agent_name?: string;
  outcome?: unknown;
  confidence?: number;
}

export interface MemoryMatch {
  matched_situation: string;
  recommendation: string;
  similarity_score: number;
  metadata: MemoryMetadata;
}

export interface MemorySearchResponse {
  agent_name: MemoryAgent;
  query: string;
  ticker: string | null;
  k: number;
  min_similarity: number | null;
  results: MemoryMatch[];
}

export interface MemoryEntry {
  id: string | null;
  situation: string;
  recommendation: string;
  metadata: MemoryMetadata;
  seeded: boolean;
}

export interface MemoryEntriesResponse {
  agent_name: MemoryAgent;
  source: "chroma" | "bm25";
  ticker: string | null;
  total: number;
  entries: MemoryEntry[];
}

// ── /api/reflections (PR5) ─────────────────────────────────────────────────

export interface Reflection {
  agent_name: MemoryAgent;
  situation: string;
  recommendation: string;
  ticker: string | null;
  trade_date: string | null;
  outcome: unknown;
  confidence: number | null;
}

export interface ReflectionsResponse {
  ticker: string | null;
  total: number;
  reflections: Reflection[];
}

// ── /api/diagnostics/prompts and /api/diagnostics/fingerprints (PR9) ───────

export interface PromptEntry {
  id: string;
  title: string;
  line: number;
}

export interface PromptsResponse {
  source: "prompts_md" | "none";
  path: string;
  total: number;
  prompts: PromptEntry[];
}

export interface FingerprintRow {
  fingerprint: Record<string, unknown> | null;
  fingerprint_text: string;
  first_seen: string | null;
  last_seen: string | null;
  event_count: number;
}

export interface FingerprintDailyCount {
  day: string | null;
  events: number;
  distinct: number;
}

export interface FingerprintsResponse {
  source: "postgres" | "none";
  reason?: string;
  days: number;
  total_events: number;
  distinct_fingerprints: number;
  fingerprints: FingerprintRow[];
  daily_counts: FingerprintDailyCount[];
}

// ── /api/config (PUT) ──────────────────────────────────────────────────────

export interface ConfigUpdateRequest {
  backend_url?: string;
  backend_api_key?: string;
  deep_think_model?: string;
  quick_think_model?: string;
  target_market?: string;
  data_vendors?: Record<string, string>;
  online_tools?: boolean;
}

export interface ConfigUpdateResponse {
  status: string;
  applied: string[];
}

// ─── Profiles & Shadow Runs ─────────────────────────────────────────────

export interface InvestorProfile {
  id: string;
  name: string;
  risk_tolerance: string;
  investment_horizon: string;
  capital_size: number;
  max_position_pct: number;
  sector_preferences: string[];
  sector_exclusions: string[];
  trading_style: string;
  benchmark_target: string;
  investor_category: string;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface ShadowRun {
  id: string;
  ticker: string;
  trade_date: string;
  signal: string;
  confidence_overall: number | null;
  confidence_technical: number | null;
  confidence_fundamental: number | null;
  confidence_sentiment: number | null;
  latest_price: number | null;
  price_date: string;
  judge_decision_summary: string;
  bull_thesis_summary: string;
  bear_thesis_summary: string;
  trader_plan_summary: string;
  risk_rationale_summary: string;
  macro_context: Record<string, unknown> | null;
  market_breadth: Record<string, unknown> | null;
  memory_status: Record<string, unknown> | null;
  warnings: string[];
  errors: string[];
  model_provider: string;
  model_name: string;
  duration_seconds: number | null;
  status: string;
  backtest_mode: boolean;
  profile_id: string;
  report_path: string;
  log_path: string;
  records_dir: string;
  investor_context_snapshot?: Record<string, unknown> | null;
  pipeline_audit?: {
    investor_context_injected?: boolean;
    portfolio_value_used?: number | null;
    position_cap?: {
      profile_cap_pct?: number | null;
      system_cap_pct?: number;
      liquidity_target_pct?: number | null;
      liquidity_max_pct?: number | null;
      policy_cap_pct?: number;
      policy_cap_source?: string;
      effective_cap_pct?: number;
    };
    trader_decision?: string | null;
    risk_action?: string | null;
    risk_veto?: boolean;
    signal_chain?: {
      research_manager?: string | null;
      trader?: string | null;
      risk_scorer?: string;
      final?: string;
    };
  } | null;
  created_at: string;
}

export interface FundamentalsQualityStatus {
  level: "full" | "partial" | "deterministic_only" | "unavailable";
  reasons: string[];
  data_available: boolean;
  enrichment_attempted: boolean;
  enrichment_succeeded: boolean;
}

export interface ServiceStatus {
  status: "up" | "down" | "unknown";
  url: string;
  login?: string;
}

export interface SystemStatus {
  api_server: ServiceStatus;
  dashboard: ServiceStatus;
  grafana: ServiceStatus;
  prometheus: ServiceStatus;
  loki: ServiceStatus;
  redis: ServiceStatus;
  last_shadow_run: {
    id: string;
    ticker: string;
    signal: string;
    created_at: string;
  } | null;
}

// ─── Data Freshness ───────────────────────────────────────────────────

export interface MacroSubSource {
  name: string;
  status: string;
  age_human: string | null;
  note: string;
}

export interface DataSourceFreshness {
  source: string;
  latest_timestamp: string | null;
  age_seconds: number | null;
  age_human: string | null;
  status: "Fresh" | "Fresh (market closed)" | "Stale" | "Missing" | "Available";
  label?: string | null;
  timestamp_meaning?: string | null;
  detail?: string;
  affected_tickers?: string[] | null;
  cached_tickers?: number;
  total_files?: number;
  total_documents?: number;
  collections?: Record<string, number>;
  sub_sources?: MacroSubSource[];
  error?: string;
}

export interface MarketStatus {
  is_trading_hours: boolean;
  last_trading_day: string;
  current_time_cairo: string;
  note: string;
}

export interface DataFreshnessSummary {
  total_sources: number;
  fresh: number;
  stale: number;
  missing: number;
  available: number;
}

export interface DataFreshnessResponse {
  checked_at: string;
  market_status: MarketStatus;
  summary: DataFreshnessSummary;
  sources: DataSourceFreshness[];
}

export interface MetricsSummary {
  active_sessions?: number;
  active_websockets?: number;
  pipeline_runs?: number;
  pipeline_duration_total?: number;
  avg_pipeline_seconds?: number;
  server_uptime_seconds?: number;
  llm_calls?: Record<string, number>;
  signals?: Record<string, number>;
  error?: string;
}
