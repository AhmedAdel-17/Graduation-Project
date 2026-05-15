// Mirrors server/api_server.py payload shapes.

export interface Ticker {
  ticker: string;
  name: string;
}

export interface TickersResponse {
  tickers: Ticker[];
}

export interface HealthResponse {
  status: string;
  timestamp: string;
  egx_tools: boolean;
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

export interface PredictionResult {
  error?: string;
  session_id?: string;
  ticker?: string;
  name?: string;
  price?: PriceBlock;
  indicators?: IndicatorsBlock;
  recommendation?: Recommendation;
  history?: StockBar[];
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
  avg_trade_return?: number;
  final_equity?: number;
  initial_capital?: number;
  [k: string]: number | undefined;
}

export interface BacktestTrade {
  date?: string;
  ticker?: string;
  action?: string;
  price?: number;
  quantity?: number;
  pnl?: number;
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
