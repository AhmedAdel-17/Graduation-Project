import type {
  BacktestSession,
  BacktestDetail,
  CompareResult,
} from './types';

const BASE = '/api';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export const fetchSessions = (): Promise<{ sessions: BacktestSession[] }> =>
  request('/backtests');

export const fetchDetail = (sessionId: string): Promise<BacktestDetail> =>
  request(`/backtests/${encodeURIComponent(sessionId)}`);

export const fetchComparison = (ticker: string): Promise<CompareResult> =>
  request(`/backtests/compare/${encodeURIComponent(ticker)}`);

export const runLLMBacktest = (body: {
  ticker: string;
  start_date: string;
  end_date: string;
  interval?: number;
  initial_capital?: number;
  selected_analysts?: string[];
}) =>
  request('/backtests/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

export const runBTBacktest = (body: {
  ticker: string;
  start_date: string;
  end_date: string;
  initial_capital?: number;
}) =>
  request('/backtests/run-bt', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
