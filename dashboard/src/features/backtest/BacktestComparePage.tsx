import { Link } from "react-router-dom";
import { ArrowLeft, GitCompare, Trophy } from "lucide-react";
import { AppShell } from "../../components/layout/AppShell";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { EmptyState } from "../../components/ui/EmptyState";
import { Badge } from "../../components/ui/Badge";
import { StockSelector } from "../../components/ui/StockSelector";
import { useAppStore } from "../../store/appStore";
import { useBacktestCompare } from "../../hooks/useBacktest";
import { useT } from "../../lib/i18n";
import { cn, formatNumber, formatPercent } from "../../lib/utils";

interface MetricRow {
  labelKey: string;
  field: string;
  unit?: "%" | "";
  lowerIsBetter?: boolean;
  decimals?: number;
}

const COMPARE_ROWS: MetricRow[] = [
  { labelKey: "backtest.kpi.totalReturn", field: "total_return_pct", unit: "%" },
  { labelKey: "backtest.kpi.sharpe", field: "sharpe_ratio", decimals: 2 },
  {
    labelKey: "backtest.kpi.drawdown",
    field: "max_drawdown_pct",
    unit: "%",
    lowerIsBetter: true,
  },
  { labelKey: "backtest.kpi.winRate", field: "win_rate", unit: "%" },
  { labelKey: "backtest.kpi.trades", field: "total_trades", decimals: 0 },
];

function asNumber(value: unknown): number | undefined {
  if (value === null || value === undefined) return undefined;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : undefined;
}

function fmt(value: number | undefined, unit: "%" | "" | undefined, decimals: number) {
  if (value === undefined) return "—";
  if (unit === "%") return formatPercent(value, decimals);
  return formatNumber(value, decimals);
}

export function BacktestComparePage() {
  const t = useT();
  const ticker = useAppStore((s) => s.selectedTicker);
  const setTicker = useAppStore((s) => s.setSelectedTicker);
  const { data, isLoading, isError } = useBacktestCompare(ticker, !!ticker);

  const llm = data?.llm ?? null;
  const bt = data?.bt ?? null;

  let verdict: "llm" | "bt" | "tie" | null = null;
  if (llm && bt) {
    let llmWins = 0;
    let btWins = 0;
    for (const row of COMPARE_ROWS) {
      const a = asNumber((llm.metrics as Record<string, unknown>)[row.field]);
      const b = asNumber((bt.metrics as Record<string, unknown>)[row.field]);
      if (a === undefined || b === undefined) continue;
      if (a === b) continue;
      const aBetter = row.lowerIsBetter ? a < b : a > b;
      if (aBetter) llmWins++;
      else btWins++;
    }
    verdict = llmWins === btWins ? "tie" : llmWins > btWins ? "llm" : "bt";
  }

  return (
    <AppShell
      title={t("backtest.compare.title")}
      subtitle={t("backtest.compare.subtitle")}
    >
      <div className="flex flex-col gap-5">
        <div className="flex items-center gap-2">
          <Link
            to="/backtest"
            className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md text-xs text-fg-muted hover:text-fg border border-line hover:border-line-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
          >
            <ArrowLeft className="h-3 w-3" aria-hidden />
            {t("backtest.compare.back")}
          </Link>
        </div>

        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                <GitCompare className="h-4 w-4 text-brand-400" aria-hidden />
                {t("backtest.compare.pickTicker")}
              </CardTitle>
              <CardDescription>
                {t("backtest.compare.pickTickerDesc")}
              </CardDescription>
            </div>
          </CardHeader>
          <CardBody>
            <div className="max-w-sm">
              <StockSelector value={ticker} onChange={setTicker} />
            </div>
          </CardBody>
        </Card>

        {isLoading ? (
          <Card>
            <CardBody>
              <Skeleton className="h-48 w-full rounded-lg" />
            </CardBody>
          </Card>
        ) : isError || !data || (!llm && !bt) ? (
          <Card>
            <CardBody>
              <EmptyState
                icon={<GitCompare className="h-4 w-4" />}
                title={t("backtest.compare.empty.title")}
                description={t("backtest.compare.empty.desc")}
                action={
                  <Link
                    to="/backtest/new"
                    className="inline-flex items-center gap-1 text-xs text-brand-400 hover:text-brand-300"
                  >
                    {t("backtest.compare.empty.cta")}
                  </Link>
                }
              />
            </CardBody>
          </Card>
        ) : (
          <Card>
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Trophy className="h-4 w-4 text-brand-400" aria-hidden />
                  {ticker}
                </CardTitle>
                <CardDescription>
                  {llm && bt
                    ? t("backtest.compare.bothPresent")
                    : t("backtest.compare.partial")}
                </CardDescription>
              </div>
              <div className="flex items-center gap-2">
                {llm && <Badge tone="brand">LLM</Badge>}
                {bt && <Badge tone="accent">BT</Badge>}
                {verdict && (
                  <Badge
                    tone={
                      verdict === "tie"
                        ? "neutral"
                        : verdict === "llm"
                          ? "brand"
                          : "accent"
                    }
                  >
                    {verdict === "tie"
                      ? t("backtest.compare.tie")
                      : verdict === "llm"
                        ? t("backtest.compare.llmWins")
                        : t("backtest.compare.btWins")}
                  </Badge>
                )}
              </div>
            </CardHeader>
            <CardBody className="p-0">
              <div className="overflow-x-auto">
                <table
                  className="w-full text-sm"
                  aria-label={t("backtest.compare.tableAria")}
                >
                  <thead>
                    <tr className="text-left text-[10px] uppercase tracking-wider text-fg-muted border-b border-line">
                      <th className="px-5 py-2.5">
                        {t("backtest.compare.col.metric")}
                      </th>
                      <th className="px-5 py-2.5 text-end">
                        {t("backtest.compare.col.llm")}
                      </th>
                      <th className="px-5 py-2.5 text-end">
                        {t("backtest.compare.col.bt")}
                      </th>
                      <th className="px-5 py-2.5 text-end">
                        {t("backtest.compare.col.winner")}
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {COMPARE_ROWS.map((row) => {
                      const a = asNumber(
                        (llm?.metrics as Record<string, unknown> | undefined)?.[
                          row.field
                        ]
                      );
                      const b = asNumber(
                        (bt?.metrics as Record<string, unknown> | undefined)?.[
                          row.field
                        ]
                      );
                      let winner: "llm" | "bt" | "tie" | null = null;
                      if (a !== undefined && b !== undefined && a !== b) {
                        winner = row.lowerIsBetter
                          ? a < b
                            ? "llm"
                            : "bt"
                          : a > b
                            ? "llm"
                            : "bt";
                      } else if (a !== undefined && b !== undefined) {
                        winner = "tie";
                      }
                      const decimals = row.decimals ?? 2;
                      return (
                        <tr key={row.field}>
                          <td className="px-5 py-2 text-fg-muted">
                            {t(row.labelKey)}
                          </td>
                          <td
                            className={cn(
                              "px-5 py-2 num text-end",
                              winner === "llm" ? "text-up font-semibold" : "text-fg"
                            )}
                          >
                            {fmt(a, row.unit, decimals)}
                          </td>
                          <td
                            className={cn(
                              "px-5 py-2 num text-end",
                              winner === "bt" ? "text-up font-semibold" : "text-fg"
                            )}
                          >
                            {fmt(b, row.unit, decimals)}
                          </td>
                          <td className="px-5 py-2 text-end">
                            {winner === "llm" && <Badge tone="brand">LLM</Badge>}
                            {winner === "bt" && <Badge tone="accent">BT</Badge>}
                            {winner === "tie" && <Badge tone="neutral">tie</Badge>}
                            {winner === null && (
                              <span className="text-fg-subtle">—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </CardBody>
          </Card>
        )}

        {(llm || bt) && (
          <div className="flex flex-wrap gap-2 text-xs">
            {llm?.session_id && (
              <Link
                to={`/backtest/${encodeURIComponent(llm.session_id)}`}
                className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md text-fg-muted hover:text-fg border border-line hover:border-line-strong"
              >
                {t("backtest.compare.openLlm")}
              </Link>
            )}
            {bt?.session_id && (
              <Link
                to={`/backtest/${encodeURIComponent(bt.session_id)}`}
                className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md text-fg-muted hover:text-fg border border-line hover:border-line-strong"
              >
                {t("backtest.compare.openBt")}
              </Link>
            )}
          </div>
        )}
      </div>
    </AppShell>
  );
}
