// Portfolio Assistant wire types — hand-written mirror of
// tradingagents/portfolio/schemas.py (design v3, SCHEMA_VERSION 0.1.0).
//
// Field names are snake_case to match the JSON the FastAPI backend emits (pydantic
// does not alias). Fields that have a default on the Python side are marked optional
// here (they may be omitted in hand-authored payloads but are present when the
// backend serializes). Keep this file in lockstep with schemas.py — the fixture
// round-trip test (tests/test_portfolio_schemas.py::TestFixtures) fails if a fixture
// stops validating, and any structural drift should be caught in review.

// ---------------------------------------------------------------------------
// Enumerations
// ---------------------------------------------------------------------------

export type TradeSide = "BUY" | "SELL";
export type SignalLabel = "BUY" | "HOLD" | "SELL";
export type SignalSource = "agent" | "quant_prior";
export type HoldingSource = "extracted" | "confirmed" | "derived";
export type Objective =
  | "capital_preservation"
  | "income"
  | "balanced"
  | "growth"
  | "aggressive_growth";
export type RiskTolerance = "very_low" | "low" | "medium" | "high" | "very_high";
export type Horizon = "lt_6m" | "6_12m" | "1_3y" | "gt_3y";
export type SolverStatus = "optimal" | "infeasible_relaxed" | "heuristic_fallback";
export type FlagSeverity = "info" | "warning" | "critical";
export type ScenarioStatus = "active" | "promoted" | "discarded";
export type Intent =
  | "describe_portfolio"
  | "objective"
  | "optimize"
  | "what_if"
  | "adopt_scenario"
  | "follow_up_qa"
  | "off_topic";
export type EGXIndex = "EGX30" | "EGX70" | "EGX100";
export type Language = "en" | "ar" | "auto";

// ---------------------------------------------------------------------------
// Holdings & snapshots
// ---------------------------------------------------------------------------

export interface PortfolioHolding {
  ticker: string;
  shares?: number | null;
  avg_cost?: number | null;
  weight_pct?: number | null;
  source?: HoldingSource;
  name_raw?: string | null;
}

export interface PortfolioSnapshot {
  snapshot_id?: number | null;
  conversation_id?: string | null;
  version?: number;
  cash_egp?: number;
  total_value_egp?: number | null;
  holdings?: PortfolioHolding[];
  confirmed_by_user?: boolean;
  promoted_from_scenario?: number | null;
  created_at?: string;
}

// ---------------------------------------------------------------------------
// Policy
// ---------------------------------------------------------------------------

export interface InvestmentPolicy {
  objective?: Objective;
  risk_tolerance?: RiskTolerance;
  horizon?: Horizon;
  income_preference?: boolean;
  excluded_sectors?: string[];
  excluded_tickers?: string[];
  max_position_pct?: number | null;
  min_cash_egp?: number | null;
  goal_target_amount_egp?: number | null;
  goal_horizon_months?: number | null;
  monthly_contribution_egp?: number | null;
  notes?: string;
  source_spans?: Record<string, string>;
  inferred_fields?: string[];
  version?: number;
  confirmed_by_user?: boolean;
  created_at?: string;
}

export interface OptimizerParams {
  risk_aversion: number;
  max_position_pct: number;
  max_sector_pct: number;
  min_cash_pct: number;
  view_shrinkage: number;
  turnover_penalty: number;
  transaction_cost_pct: number;
  vol_target?: number | null;
  vol_ceiling?: number | null;
  income_tilt?: boolean;
  excluded_tickers?: string[];
  excluded_sectors?: string[];
  compiler_version: string;
  policy_version: number;
}

export interface PolicyFlag {
  code: string;
  severity?: FlagSeverity;
  detail: string;
  data?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Signals
// ---------------------------------------------------------------------------

export interface SignalView {
  ticker: string;
  label: SignalLabel;
  confidence: number;
  source?: SignalSource;
  session_id?: string | null;
  as_of?: string | null;
  age_days?: number | null;
  is_stale?: boolean;
}

// ---------------------------------------------------------------------------
// Proposal
// ---------------------------------------------------------------------------

export interface RebalanceAction {
  ticker: string;
  side: TradeSide;
  shares: number;
  price_used: number;
  est_value_egp: number;
  current_weight_pct: number;
  target_weight_pct: number;
  rationale?: string | null;
  signal_session_id?: string | null;
}

export interface OptimizationProposal {
  proposal_id?: number | null;
  conversation_id?: string | null;
  snapshot_id?: number | null;
  scenario_id?: number | null;
  policy_version: number;
  actions?: RebalanceAction[];
  current_weights?: Record<string, number>;
  target_weights?: Record<string, number>;
  expected_return_view_annual?: number | null;
  expected_vol_before?: number | null;
  expected_vol_after?: number | null;
  hhi_before?: number | null;
  hhi_after?: number | null;
  est_total_cost_egp?: number;
  est_turnover_pct?: number;
  solver_status: SolverStatus;
  policy_flags?: PolicyFlag[];
  inputs_audit?: Record<string, unknown>;
  engine_version: string;
  created_at?: string;
}

// ---------------------------------------------------------------------------
// Scenario operations (discriminated union on `op`)
// ---------------------------------------------------------------------------

export interface AddCashOp { op: "ADD_CASH"; amount_egp: number; }
export interface RemoveCashOp { op: "REMOVE_CASH"; amount_egp: number; }
export interface ClosePositionOp { op: "CLOSE_POSITION"; ticker: string; }
export interface ScalePositionOp { op: "SCALE_POSITION"; ticker: string; factor: number; }
export interface SetPositionWeightOp { op: "SET_POSITION_WEIGHT"; ticker: string; weight_pct: number; }
export interface ExcludeSectorOp { op: "EXCLUDE_SECTOR"; sector: string; }
export interface ExcludeTickerOp { op: "EXCLUDE_TICKER"; ticker: string; }
export interface OverridePolicyOp { op: "OVERRIDE_POLICY"; field: string; value: string | number | boolean; }
export interface TargetRiskDeltaOp { op: "TARGET_RISK_DELTA"; vol_delta_pct: number; }

export type ScenarioOp =
  | AddCashOp
  | RemoveCashOp
  | ClosePositionOp
  | ScalePositionOp
  | SetPositionWeightOp
  | ExcludeSectorOp
  | ExcludeTickerOp
  | OverridePolicyOp
  | TargetRiskDeltaOp;

export interface ScenarioPatch {
  ops: ScenarioOp[];
  reference?: "active" | "baseline";
  label?: string;
}

export interface PinnedInputSet {
  price_asof: string;
  price_source?: string;
  signal_session_ids?: Record<string, string | null>;
  covariance_hash?: string;
}

export interface Scenario {
  scenario_id?: number | null;
  conversation_id?: string | null;
  parent_scenario_id?: number | null;
  base_snapshot_id?: number | null;
  patch: ScenarioPatch;
  derived_snapshot: PortfolioSnapshot;
  derived_policy?: InvestmentPolicy | null;
  input_set?: PinnedInputSet | null;
  proposal_id?: number | null;
  status?: ScenarioStatus;
  label?: string;
  created_at?: string;
}

// ---------------------------------------------------------------------------
// Workspace
// ---------------------------------------------------------------------------

export interface ScenarioRef {
  scenario_id?: number | null;
  label?: string;
  parent_scenario_id?: number | null;
  status?: ScenarioStatus;
}

export interface PortfolioWorkspace {
  conversation_id: string;
  user_id?: string | null;
  language?: Language;
  baseline?: PortfolioSnapshot | null;
  policy?: InvestmentPolicy;
  scenarios?: Scenario[];
  active_ref?: string;
  last_proposal_id?: number | null;
  created_at?: string;
  updated_at?: string;
}

export interface WorkspaceDigest {
  baseline_summary: string;
  baseline_tickers?: string[];
  policy: InvestmentPolicy;
  scenarios?: ScenarioRef[];
  active_ref?: string;
  last_proposal_id?: number | null;
}

// ---------------------------------------------------------------------------
// Analytics
// ---------------------------------------------------------------------------

export interface HoldingAnalytics {
  ticker: string;
  shares: number;
  price: number;
  market_value_egp: number;
  weight_pct: number;
  avg_cost?: number | null;
  unrealized_pnl_egp?: number | null;
  unrealized_pnl_pct?: number | null;
  sector?: string;
  indices?: EGXIndex[];
  signal?: SignalView | null;
}

export interface PortfolioAnalytics {
  total_value_egp: number;
  invested_egp: number;
  cash_egp: number;
  cash_drag_pct: number;
  holdings?: HoldingAnalytics[];
  weights?: Record<string, number>;
  hhi: number;
  sector_exposure?: Record<string, number>;
  index_exposure?: Record<string, number>;
  portfolio_beta?: number | null;
  beta_is_proxy?: boolean;
  annual_vol?: number | null;
  min_history_excluded?: string[];
  price_asof?: string | null;
}

// ---------------------------------------------------------------------------
// Chat blocks (discriminated union on `type`)
// ---------------------------------------------------------------------------

interface BlockBase {
  is_hypothetical?: boolean;
  scenario_id?: number | null;
}

export interface AllocationSlice {
  label: string;
  ticker?: string | null;
  value_egp: number;
  weight_pct: number;
  is_cash?: boolean;
}
export interface AllocationDonutData {
  slices?: AllocationSlice[];
  total_egp: number;
}
export interface AllocationDonutBlock extends BlockBase {
  type: "allocation_donut";
  title?: string | null;
  data: AllocationDonutData;
}

export interface TreemapNode {
  label: string;
  ticker?: string | null;
  sector: string;
  value_egp: number;
  weight_pct: number;
  signal_tone?: number | null;
}
export interface SectorTreemapData {
  nodes?: TreemapNode[];
}
export interface SectorTreemapBlock extends BlockBase {
  type: "sector_treemap";
  data: SectorTreemapData;
}

export interface HoldingRow {
  ticker: string;
  shares: number;
  avg_cost?: number | null;
  price: number;
  market_value_egp: number;
  weight_pct: number;
  unrealized_pnl_egp?: number | null;
  signal_label?: SignalLabel | null;
}
export interface HoldingsTableBlock extends BlockBase {
  type: "holdings_table";
  rows?: HoldingRow[];
}

export interface ExtractedPortfolioTableData {
  holdings?: PortfolioHolding[];
  cash_egp?: number;
  total_value_egp?: number | null;
  unresolved_names?: string[];
  warnings?: string[];
}
export interface ExtractedPortfolioTableBlock extends BlockBase {
  type: "extracted_portfolio_table";
  data: ExtractedPortfolioTableData;
}

export interface BeforeAfterEntry {
  ticker: string;
  before_pct: number;
  after_pct: number;
  delta_pct: number;
}
export interface BeforeAfterData {
  entries?: BeforeAfterEntry[];
}
export interface BeforeAfterBlock extends BlockBase {
  type: "before_after";
  data: BeforeAfterData;
}

export interface ScenarioCompareData {
  reference?: "active" | "baseline";
  scenario_label?: string;
  metric_deltas?: Record<string, number>;
}
export interface ScenarioCompareBlock extends BlockBase {
  type: "scenario_compare";
  data: ScenarioCompareData;
}

export interface RebalanceActionsData {
  actions?: RebalanceAction[];
  est_total_cost_egp?: number;
  est_turnover_pct?: number;
}
export interface RebalanceActionsBlock extends BlockBase {
  type: "rebalance_actions";
  data: RebalanceActionsData;
}

export interface RiskPanelData {
  hhi_before?: number | null;
  hhi_after?: number | null;
  vol_before?: number | null;
  vol_after?: number | null;
  beta_before?: number | null;
  beta_after?: number | null;
  max_position_before?: number | null;
  max_position_after?: number | null;
  cash_pct_before?: number | null;
  cash_pct_after?: number | null;
  beta_is_proxy?: boolean;
}
export interface RiskPanelBlock extends BlockBase {
  type: "risk_panel";
  data: RiskPanelData;
}

export interface PolicyFlagsBlock extends BlockBase {
  type: "policy_flags";
  flags?: PolicyFlag[];
}

export interface SignalFreshnessBlock extends BlockBase {
  type: "signal_freshness";
  signals?: SignalView[];
}

export type ChatBlock =
  | AllocationDonutBlock
  | SectorTreemapBlock
  | HoldingsTableBlock
  | ExtractedPortfolioTableBlock
  | BeforeAfterBlock
  | ScenarioCompareBlock
  | RebalanceActionsBlock
  | RiskPanelBlock
  | PolicyFlagsBlock
  | SignalFreshnessBlock;

// ---------------------------------------------------------------------------
// WebSocket protocol — client → server
// ---------------------------------------------------------------------------

export interface UserMessageIn {
  type: "user_message";
  text: string;
  language?: Language;
}
export interface WhatIfIn {
  type: "what_if";
  patch: ScenarioPatch;
}
export interface AdoptScenarioIn {
  type: "adopt_scenario";
  scenario_id: number;
}
export type ClientEvent = UserMessageIn | WhatIfIn | AdoptScenarioIn;

// ---------------------------------------------------------------------------
// WebSocket protocol — server → client
// ---------------------------------------------------------------------------

export interface StatusEvent {
  type: "status";
  stage: string;
  detail?: string | null;
}
export interface ClarificationEvent {
  type: "clarification";
  question: string;
  missing?: string[];
}
export interface ExtractionEvent {
  type: "extraction";
  blocks?: ChatBlock[];
}
export interface PolicyUpdateEvent {
  type: "policy_update";
  policy: InvestmentPolicy;
  inferred_fields?: string[];
  requires_confirm?: boolean;
}
export interface PolicyFlagsEvent {
  type: "policy_flags";
  flags?: PolicyFlag[];
}
export interface ScenarioCreatedEvent {
  type: "scenario_created";
  scenario: ScenarioRef;
}
export interface AssistantMessageEvent {
  type: "assistant_message";
  text?: string;
  scenario_id?: number | null;
  blocks?: ChatBlock[];
}
export interface DoneEvent {
  type: "done";
  proposal_id?: number | null;
}
export interface ErrorEvent {
  type: "error";
  message: string;
  recoverable?: boolean;
}
export type ServerEvent =
  | StatusEvent
  | ClarificationEvent
  | ExtractionEvent
  | PolicyUpdateEvent
  | PolicyFlagsEvent
  | ScenarioCreatedEvent
  | AssistantMessageEvent
  | DoneEvent
  | ErrorEvent;
