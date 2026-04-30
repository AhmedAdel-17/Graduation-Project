import { Card, CardBody, CardHeader, CardTitle, CardDescription } from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { MetricsGrid } from "./MetricsGrid";
import { EquityCurve } from "../../components/charts";
import type {
  BacktestCompareResponse,
  EquityPoint,
} from "../../services/api/types";
import { EmptyState } from "../../components/ui/EmptyState";
import { BarChart3, Trophy } from "lucide-react";
import { formatCurrency } from "../../lib/utils";

function toSeries(points: EquityPoint[] | undefined): { date: string; value: number }[] {
  if (!points || points.length === 0) return [];
  return points
    .map((p) => ({
      date: String(p.date ?? ""),
      value: Number(p.equity ?? p.value ?? p.close ?? 0),
    }))
    .filter((p) => p.date && Number.isFinite(p.value));
}

export function BacktestResult({
  data,
  initialCapital,
  title = "Results",
}: {
  data: BacktestCompareResponse | undefined;
  initialCapital?: number;
  title?: string;
}) {
  if (!data || (!data.llm && !data.bt)) {
    return (
      <Card>
        <CardBody>
          <EmptyState
            icon={<BarChart3 className="h-4 w-4" />}
            title="No backtest results yet"
            description="Run a backtest with the form on the left — results typically take 30–90 seconds."
          />
        </CardBody>
      </Card>
    );
  }

  const llm = data.llm;
  const bt = data.bt;

  // Prefer LLM results as primary
  const primary = llm ?? bt;
  const primaryLabel = llm ? "Multi-Agent LLM" : "Classical Technical";
  const primaryError =
    primary && typeof primary === "object" && "error" in (primary as Record<string, unknown>)
      ? String((primary as Record<string, unknown>).error || "")
      : "";
  const equity = toSeries(primary?.daily_portfolio);
  // Only use the LLM benchmark_history as the comparison line.
  // Falling back to bt?.daily_portfolio would duplicate the main equity curve.
  const benchmark = toSeries(llm?.benchmark_history as EquityPoint[] | undefined);

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2">
              <Trophy className="h-4 w-4 text-brand-400" />
              {title} — {data.ticker}
            </CardTitle>
            <CardDescription>
              Primary engine: {primaryLabel}
              {primary?.session_id && (
                <span className="ml-2 num text-fg-subtle">
                  · {primary.session_id.slice(-12)}
                </span>
              )}
            </CardDescription>
          </div>
          <div className="flex gap-2">
            {llm && <Badge tone="brand">LLM ready</Badge>}
            {bt && <Badge tone="accent">BT ready</Badge>}
          </div>
        </CardHeader>
        <CardBody>
          {primaryError ? (
            <EmptyState
              icon={<BarChart3 className="h-4 w-4" />}
              title="Backtest completed with an issue"
              description={primaryError}
            />
          ) : (
            <MetricsGrid
              metrics={primary!.metrics}
              initialCapital={initialCapital}
            />
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Equity curve</CardTitle>
            <CardDescription>
              Portfolio value over time
              {benchmark.length > 0 && " · dashed line = benchmark"}
            </CardDescription>
          </div>
          {primary?.metrics.final_equity !== undefined && (
            <div className="text-right">
              <p className="text-[11px] uppercase tracking-wider text-fg-muted">
                Final
              </p>
              <p className="num text-lg font-semibold text-brand-400">
                {formatCurrency(primary.metrics.final_equity)}
              </p>
            </div>
          )}
        </CardHeader>
        <CardBody>
          {equity.length > 0 && !primaryError ? (
            <EquityCurve equity={equity} benchmark={benchmark} />
          ) : (
            <EmptyState
              icon={<BarChart3 className="h-4 w-4" />}
              title={primaryError ? "No chart available" : "No equity data"}
              description={
                primaryError
                  ? "Fix the issue above, then re-run the backtest for this ticker."
                  : "The backtest completed but no portfolio timeseries was returned."
              }
            />
          )}
        </CardBody>
      </Card>

      {llm && bt && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Side-by-side comparison</CardTitle>
              <CardDescription>
                LLM multi-agent vs. classical technical benchmark
              </CardDescription>
            </div>
          </CardHeader>
          <CardBody>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wider text-fg-muted border-b border-line">
                    <th className="py-2">Metric</th>
                    <th className="py-2">LLM Multi-Agent</th>
                    <th className="py-2">Classical Technical</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  <CompareRow label="Total Return" llm={llm.metrics.total_return_pct} bt={bt.metrics.total_return_pct} suffix="%" />
                  <CompareRow label="Sharpe" llm={llm.metrics.sharpe_ratio} bt={bt.metrics.sharpe_ratio} />
                  <CompareRow label="Max Drawdown" llm={llm.metrics.max_drawdown_pct} bt={bt.metrics.max_drawdown_pct} suffix="%" lowerIsBetter />
                  <CompareRow label="Win Rate" llm={llm.metrics.win_rate} bt={bt.metrics.win_rate} suffix="%" />
                  <CompareRow label="Trades" llm={llm.metrics.total_trades} bt={bt.metrics.total_trades} />
                </tbody>
              </table>
            </div>
          </CardBody>
        </Card>
      )}
    </div>
  );
}

function CompareRow({
  label,
  llm,
  bt,
  suffix = "",
  lowerIsBetter = false,
}: {
  label: string;
  llm?: number;
  bt?: number;
  suffix?: string;
  lowerIsBetter?: boolean;
}) {
  const llmBetter =
    llm !== undefined && bt !== undefined
      ? lowerIsBetter
        ? llm <= bt
        : llm >= bt
      : false;
  const fmt = (v?: number) => (v === undefined || v === null ? "—" : `${Number(v).toFixed(2)}${suffix}`);
  return (
    <tr>
      <td className="py-2 text-fg-muted">{label}</td>
      <td className={"py-2 num " + (llmBetter ? "text-up font-semibold" : "text-fg")}>
        {fmt(llm)}
      </td>
      <td className={"py-2 num " + (!llmBetter && bt !== undefined ? "text-up font-semibold" : "text-fg")}>
        {fmt(bt)}
      </td>
    </tr>
  );
}
