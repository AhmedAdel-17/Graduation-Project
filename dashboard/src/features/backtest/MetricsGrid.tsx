import type { BacktestMetrics } from "../../services/api/types";
import { MetricsCard } from "../../components/ui/MetricsCard";
import {
  formatCurrency,
  formatNumber,
  formatPercent,
} from "../../lib/utils";

function pct(v?: number): number | undefined {
  if (v === undefined || v === null || Number.isNaN(v)) return undefined;
  // Auto-detect if value is already percentage (>1) or a decimal (0-1)
  return Math.abs(v) > 1 ? v : v * 100;
}

export function MetricsGrid({
  metrics,
  initialCapital,
}: {
  metrics: BacktestMetrics;
  initialCapital?: number;
}) {
  const totalReturnPct =
    metrics.total_return_pct ?? pct(metrics.total_return);
  const final = metrics.final_equity;
  const start = metrics.initial_capital ?? initialCapital;
  const pnl =
    final !== undefined && start !== undefined ? final - start : undefined;

  const maxDD = metrics.max_drawdown_pct ?? pct(metrics.max_drawdown);
  const winRate = pct(metrics.win_rate);

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      <MetricsCard
        label="Profit / Loss"
        value={formatCurrency(pnl)}
        tone={pnl !== undefined ? (pnl >= 0 ? "up" : "down") : "default"}
        trend={pnl !== undefined ? (pnl >= 0 ? "up" : "down") : undefined}
        hint={start !== undefined ? `from ${formatCurrency(start)}` : undefined}
      />
      <MetricsCard
        label="ROI"
        value={formatPercent(totalReturnPct)}
        tone={
          totalReturnPct !== undefined
            ? totalReturnPct >= 0
              ? "up"
              : "down"
            : "default"
        }
        trend={
          totalReturnPct !== undefined
            ? totalReturnPct >= 0
              ? "up"
              : "down"
            : undefined
        }
        hint="Total return"
      />
      <MetricsCard
        label="Final Equity"
        value={formatCurrency(final)}
        tone="brand"
      />
      <MetricsCard
        label="Sharpe"
        value={formatNumber(metrics.sharpe_ratio, 2)}
        hint="Risk-adjusted return"
      />
      <MetricsCard
        label="Max Drawdown"
        value={formatPercent(maxDD)}
        tone="down"
      />
      <MetricsCard
        label="Win Rate"
        value={formatPercent(winRate, 1)}
        hint={`${metrics.total_trades ?? 0} trades`}
      />
      <MetricsCard
        label="Profit Factor"
        value={formatNumber(metrics.profit_factor, 2)}
      />
      <MetricsCard
        label="Avg Trade"
        value={formatPercent(pct(metrics.avg_trade_return), 2)}
      />
    </div>
  );
}
