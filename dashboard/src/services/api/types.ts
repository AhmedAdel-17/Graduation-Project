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

// One trading-style-specific recommendation produced by the Trader / Portfolio
// Manager. Prices are strings so they can be ranges ("144 - 146") or
// instructions ("Wait for 140-142"). Mirrors trader.py styled_recommendations.
export interface StyledRecommendation {
  style?: string;
  recommendation?: string; // BUY | HOLD | SELL | NO TRADE
  entry_zone?: string;
  target?: string;
  stop_loss?: string;
  holding_period?: string;
  confidence?: string;
  risk_level?: string;
  reasoning?: string;
}

export interface StyledRecommendations {
  swing?: StyledRecommendation;
  position?: StyledRecommendation;
  long_term?: StyledRecommendation;
  [k: string]: StyledRecommendation | undefined;
}

export interface Recommendation {
  signal?: Signal;
  confidence?: Confidence;
  risk?: string;
  time_horizon?: string | null;
  target_price?: number | null;
  stop_loss?: number | null;
  bull_case?: string;
  bear_case?: string;
  neutral_case?: string;
  rationale?: string;
  recommendation?: string;
  styled_recommendations?: StyledRecommendations | null;
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

/** Flat technical-panel row (values + verdicts + MA grid + summaries + pivot levels).
 *  Keys e.g. rsi_14, rsi_signal, stoch_k_9_6, sma_50, sma_50_signal, ind_summary,
 *  overall_summary, pivot_classic_P … See data/egx30_signals/README.md. */
export type TechnicalPanelData = Record<string, number | string | null>;

export interface TechnicalPanel {
  ticker?: string;
  as_of?: string;
  bars?: number;
  error?: string | null;
  panel?: TechnicalPanelData | null;
}

export interface PredictionResult {
  error?: string;
  session_id?: string;
  ticker?: string;
  name?: string;
  price?: PriceBlock;
  indicators?: IndicatorsBlock;
  recommendation?: Recommendation;
  price_history?: StockBar[];
  technical_panel?: TechnicalPanel | null;
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

// Post-hoc directional accuracy ("was each call right?"). Computed AFTER the
// backtest from realized forward moves; reporting-only, never fed to the agents
// (leak-safe). See scripts/backtester.py::_calculate_directional_accuracy.
export interface DirectionalAccuracy {
  horizon?: string;
  hold_band_pct?: number;
  evaluated_decisions?: number;
  overall_hit_rate?: number | null;
  actionable_hit_rate?: number | null;
  by_decision?: Record<string, { n: number; correct: number }>;
  note?: string;
  detail?: unknown[];
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
  /** "agent_error" / "data_fetch_failed" / etc. when the evaluation did NOT
   *  complete — such rows carry a placeholder HOLD and are not real verdicts. */
  decision_status?: string;
  error_class?: string;
  error_message?: string;
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
  /** Backtest window (first/last equity-curve bar). */
  start_date?: string | null;
  end_date?: string | null;
  /** When the user ran this backtest (parsed from the report filename). */
  ran_at?: string | null;
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
  /** 'live' | 'backtest' — history lists only live runs. */
  run_type?: string | null;
  /** Final verdict token (BUY / HOLD / SELL), when available. */
  final_decision?: string | null;
  confidence_overall?: number | null;
  risk_veto?: boolean | null;
}

export interface ResultsListResponse {
  sessions: ResultSessionSummary[];
}

// ── Decision-quality evaluation (thesis "skillful, not random") ────────────
// Emitted by tradingagents/backtest/decision_metrics.compute_decision_quality.
// Forward returns are computed post-hoc from the price series — leak-safe.
export interface DecisionQualityHorizon {
  horizon_days: number;
  n_evaluated: number;
  overall_hit_rate?: number | null;
  actionable_n: number;
  actionable_hit_rate?: number | null;
  actionable_ci_lo?: number | null;
  actionable_ci_hi?: number | null;
  actionable_binomial_p_vs_50pct?: number | null;
  information_coefficient?: number | null;
  ic_p_value?: number | null;
  by_decision?: Record<string, { n: number; correct: number; hit_rate?: number | null }>;
  confusion_matrix?: Record<string, { UP: number; FLAT: number; DOWN: number }>;
  base_rate_up?: number | null;
  baseline_always_buy_hit_rate?: number | null;
  baseline_random?: { mean: number; std: number; p95: number };
}

export interface DecisionQuality {
  method?: string;
  hold_band_pct?: number;
  primary_horizon_days?: number;
  n_decisions?: number;
  action_distribution?: Record<string, number>;
  horizons?: Record<string, DecisionQualityHorizon>;
  calibration_primary_horizon?: { bucket: string; n: number; hit_rate?: number | null }[];
  note?: string;
}

// One scored prediction (per evaluation date). session_id links to the full
// reasoning trace (/api/sessions/{session_id}/trace) — the dashboard opens it
// in the same live-style detail screen.
export interface BacktestPrediction {
  date: string;
  session_id?: string | null;
  decision: string;
  confidence?: number | null;
  correct?: boolean | null;
  realized_direction?: string | null;
  primary_horizon_days?: number;
  forward_return_5d?: number | null;
  forward_return_10d?: number | null;
  forward_return_20d?: number | null;
  [k: string]: unknown;
}

// Single-decision event study: Follow-the-AI vs EGX30 index over a window.
// Emitted by scripts/scenario_backtest.py.
export interface ScenarioComparison {
  ticker: string;
  start: string;
  end: string;
  decision: string;
  /** Raw directional view (BUY/SELL/HOLD) before EGX long-only rewrote SELL→HOLD. */
  predicted_direction?: string;
  session_id?: string | null;
  confidence?: number | null;
  rationale?: string | null;
  action_taken?: string;
  price_start?: number;
  price_end?: number;
  stock_return_pct?: number;
  follow_return_pct?: number;
  index_return_pct?: number | null;
  outperformance_pct?: number | null;
  followed_beat_index?: boolean | null;
  round_trip_cost_pct?: number;
}

// Disclosed run configuration (live_faithful vs tuned sensitivity profile).
export interface BacktestRunConfig {
  decision_profile?: string;
  decision_rfr_override?: number | null;
  initial_capital?: number;
  start_date?: string | null;
  end_date?: string | null;
  analysts?: string[] | null;
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
  // Post-hoc directional hit-rate ("was each call right?"); leak-safe.
  directional_accuracy?: DirectionalAccuracy;
  // Decision-quality block + per-prediction drill-down rows (thesis evidence).
  decision_quality?: DecisionQuality | null;
  predictions?: BacktestPrediction[];
  run_config?: BacktestRunConfig | null;
  scenario_comparison?: ScenarioComparison | null;
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
