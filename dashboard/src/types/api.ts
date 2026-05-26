export type Decision = "BUY" | "SELL" | "HOLD";
export type Sector =
  | "Banks"
  | "Real Estate"
  | "Industrial"
  | "Telecom"
  | "Financial Services"
  | "Food & Beverage";

export interface User {
  id: string;
  name: string;
  email: string;
  sectors_of_interest: Sector[];
  alerts_enabled?: boolean;
  created_at: string;
}

export interface Ticker {
  symbol: string;
  name_en: string;
  name_ar: string;
  sector: Sector;
}

export interface MacroSnapshot {
  cbe_rate: number;
  real_rate: number;
  usd_egp: number;
  fx_trend: string;
  egx30_trend: string;
  egx30_return_1m: number;
  brent_usd: number;
  imf_program_active: boolean;
  as_of_date: string;
}

export interface Quote {
  symbol: string;
  last: number;
  change_pct: number;
  volume: number;
  as_of_date: string;
}

export type AgentStatus = "pending" | "running" | "done" | "failed";

export interface AgentOutput {
  agent: string;
  status: AgentStatus;
  started_at?: string;
  finished_at?: string;
  duration_ms?: number;
  summary?: string;
  details?: Record<string, any>;
  error?: string;
}

export interface Run {
  id: string;
  user_id: string;
  type: "prediction";
  ticker: string;
  trade_date: string;
  portfolio_value: number;
  status: "running" | "done" | "failed";
  decision?: Decision;
  confidence?: number;
  rationale?: string;
  macro_context: Omit<MacroSnapshot, "egx30_return_1m" | "as_of_date">;
  agents: AgentOutput[];
  bull_thesis?: any;
  bear_thesis?: any;
  execution_plan?: any;
  risk_assessment?: any;
  created_at: string;
  finished_at?: string;
}

export interface Trade {
  date: string;
  action: Decision;
  price: number;
  shares: number;
  realized_pnl: number;
  forward_return_20d?: number;
  outcome: "WIN" | "LOSS" | "NEUTRAL" | "PENDING";
  run_id: string;
}

export interface Backtest {
  id: string;
  user_id: string;
  type: "backtest";
  ticker: string;
  start_date: string;
  end_date: string;
  interval_days: number;
  capital: number;
  analysts: string[];
  status: "running" | "done" | "failed";
  trades: Trade[];
  equity_curve: { date: string; value: number; benchmark?: number | null }[];
  metrics: {
    total_return_pct: number;
    benchmark_return_pct?: number;     // EGX30 return over the window
    alpha_pct: number;                 // vs EGX30 (headline)
    buy_hold_return_pct: number;
    strategy_alpha_pct?: number;       // vs buy & hold
    sharpe: number;
    calmar?: number;
    max_drawdown_pct: number;
    win_rate_pct: number;
    hit_rate_pct: number;
    total_trades: number;
    final_portfolio: number;
    total_commissions_egp?: number;
    benchmark_available?: boolean;
    alpha_decay?: {
      horizons: number[];
      all: (number | null)[];
      buy: (number | null)[];
      sell: (number | null)[];
      n_buy: number;
      n_sell: number;
    };
  };
  created_at: string;
  finished_at?: string;
}

export type WSMessage =
  | { type: "agent_start"; agent: string }
  | { type: "agent_progress"; agent: string; message: string }
  | {
      type: "agent_done";
      agent: string;
      summary: string;
      details?: Record<string, any>;
    }
  | {
      type: "decision";
      action: Decision;
      confidence: number;
      rationale: string;
      details?: Record<string, any>;
    }
  | { type: "complete"; run_id: string }
  | { type: "error"; agent?: string; message: string };
