export interface TradeRecord {
  date: string;
  ticker?: string;
  action: 'BUY' | 'SELL';
  shares: number;
  close_price?: number;
  exec_price: number;
  value: number;
  commission: number;
  realized_pnl: number;
  confidence?: number;
  reasoning?: string;
  // Backtrader trades use "pnl" and "comm"
  pnl?: number;
  comm?: number;
  price?: number;
}

export interface DailyPortfolio {
  date: string;
  portfolio_value: number;
}

export interface BenchmarkPoint {
  date: string;
  price: number;
  value: number;
}

export interface BacktestMetrics {
  'Total Return': string;
  'Benchmark Return'?: string;
  Alpha?: string;
  'Win Rate': string;
  'Max Drawdown': string;
  'Sharpe Ratio': string;
  'Calmar Ratio': string;
  'Total Trades': number | string;
  'Total Commissions': string;
  'Final Portfolio': string;
  [key: string]: string | number | undefined;
}

export interface BacktestSession {
  session_id: string;
  ticker: string;
  engine: 'llm_multi_agent' | 'classical_technical';
  metrics: BacktestMetrics;
  total_trades: number;
}

export interface BacktestDetail {
  session: string;
  engine?: string;
  metrics: BacktestMetrics;
  trades: TradeRecord[];
  daily_portfolio: DailyPortfolio[];
  benchmark_history?: BenchmarkPoint[];
  cost_model?: Record<string, string>;
}

export interface EngineData {
  session_id: string;
  metrics: BacktestMetrics;
  trades: TradeRecord[];
  daily_portfolio: DailyPortfolio[];
  benchmark_history?: BenchmarkPoint[];
}

export interface CompareResult {
  ticker: string;
  llm: EngineData | null;
  bt: EngineData | null;
}
