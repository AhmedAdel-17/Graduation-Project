import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  Brain,
  LayoutDashboard,
} from "lucide-react";
import { AppShell } from "../../components/layout/AppShell";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { EmptyState } from "../../components/ui/EmptyState";
import { Skeleton } from "../../components/ui/Skeleton";
import { EquityCurve } from "../../components/charts";
import { MetricsCard } from "../../components/ui/MetricsCard";
import { JSONViewer } from "../../components/ui/JSONViewer";
import { useBacktestDetail, useRlDecisions } from "../../hooks/useBacktest";
import { useT } from "../../lib/i18n";
import {
  cn,
  formatCompact,
  formatCurrency,
  formatNumber,
  formatPercent,
} from "../../lib/utils";
import type {
  BacktestDetail,
  BacktestTrade,
  EquityPoint,
  RlDecision,
} from "../../services/api/types";

function asNumber(value: unknown): number | undefined {
  if (value === null || value === undefined) return undefined;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : undefined;
}

function toSeries(points: EquityPoint[] | undefined): { date: string; value: number }[] {
  if (!points || points.length === 0) return [];
  return points
    .map((p) => ({
      date: String(p.date ?? ""),
      value: Number(p.equity ?? p.value ?? p.close ?? 0),
    }))
    .filter((p) => p.date && Number.isFinite(p.value));
}

export function BacktestDetailPage() {
  const t = useT();
  const { runId = "" } = useParams<{ runId: string }>();
  const detailQuery = useBacktestDetail(runId || null);
  const detail = detailQuery.data ?? null;

  const ticker =
    (detail?.ticker as string | undefined) ??
    (detail?.metrics?.ticker as string | undefined);

  // Pull matching RL events for the session — graceful when Postgres is
  // unreachable (the endpoint returns empty + source="none").
  const rlQuery = useRlDecisions({
    sessionId: runId || null,
    limit: 200,
  });

  return (
    <AppShell
      title={t("backtest.detail.title")}
      subtitle={ticker ?? runId.slice(0, 18)}
    >
      <div className="flex flex-col gap-5">
        <div className="flex items-center gap-2">
          <Link
            to="/backtest"
            className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md text-xs text-fg-muted hover:text-fg border border-line hover:border-line-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
          >
            <ArrowLeft className="h-3 w-3" aria-hidden />
            {t("backtest.detail.back")}
          </Link>
          {ticker && (
            <Link
              to={`/workspace?ticker=${encodeURIComponent(ticker)}`}
              className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md text-xs text-fg-muted hover:text-fg border border-line hover:border-line-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
            >
              <LayoutDashboard className="h-3 w-3" aria-hidden />
              {t("sessionDetail.openInWorkspace")}
            </Link>
          )}
        </div>

        {detailQuery.isLoading ? (
          <Card>
            <CardBody>
              <Skeleton className="h-64 w-full rounded-lg" />
            </CardBody>
          </Card>
        ) : detailQuery.isError || !detail ? (
          <Card>
            <CardBody>
              <EmptyState
                icon={<AlertTriangle className="h-4 w-4" />}
                title={t("backtest.detail.notFound.title")}
                description={t("backtest.detail.notFound.desc")}
                action={
                  <Link
                    to="/backtest"
                    className="inline-flex items-center gap-1 text-xs text-brand-400 hover:text-brand-300"
                  >
                    <ArrowLeft className="h-3 w-3" aria-hidden />
                    {t("backtest.detail.notFound.cta")}
                  </Link>
                }
              />
            </CardBody>
          </Card>
        ) : (
          <DetailBody
            detail={detail}
            runId={runId}
            rlDecisions={rlQuery.data?.decisions ?? []}
            rlSource={rlQuery.data?.source ?? null}
          />
        )}
      </div>
    </AppShell>
  );
}

function DetailBody({
  detail,
  runId,
  rlDecisions,
  rlSource,
}: {
  detail: BacktestDetail;
  runId: string;
  rlDecisions: RlDecision[];
  rlSource: "postgres" | "none" | null;
}) {
  const t = useT();
  const metrics = detail.metrics ?? {};
  const totalReturn = asNumber(metrics.total_return_pct);
  const sharpe = asNumber(metrics.sharpe_ratio);
  const drawdown = asNumber(metrics.max_drawdown_pct);
  const winRate = asNumber(metrics.win_rate);
  const finalEquity = asNumber(metrics.final_equity);
  const totalTrades = asNumber(metrics.total_trades);
  const initialCapital = asNumber(metrics.initial_capital);
  const profitFactor = asNumber(metrics.profit_factor);

  const equity = toSeries(detail.daily_portfolio);
  const benchmark = toSeries(detail.benchmark_history);

  const trades = useMemo(
    () =>
      (detail.trades ?? []).filter(
        (t) => t.action || t.date || t.exec_price || t.price
      ),
    [detail.trades]
  );
  const tradesWithRL = useMemo(
    () =>
      trades.filter(
        (tr) =>
          tr.rl_meta_policy_enabled ||
          tr.rl_size_multiplier !== undefined ||
          tr.rl_action_index !== undefined
      ),
    [trades]
  );
  const showRlColumns = tradesWithRL.length > 0;

  return (
    <>
      <Card>
        <CardHeader>
          <div className="min-w-0">
            <CardTitle className="num truncate">
              {detail.ticker ?? "—"}
              {detail.engine && (
                <Badge
                  tone={
                    detail.engine === "classical_technical" ? "accent" : "brand"
                  }
                  className="ms-2"
                >
                  {detail.engine === "classical_technical" ? "BT" : "LLM"}
                </Badge>
              )}
            </CardTitle>
            <CardDescription>
              {detail.start_date && (
                <span className="num">{detail.start_date}</span>
              )}
              {detail.start_date && detail.end_date && " → "}
              {detail.end_date && (
                <span className="num">{detail.end_date}</span>
              )}
              <span className="ms-2 num text-fg-subtle">
                {runId.slice(0, 16)}…
              </span>
            </CardDescription>
          </div>
        </CardHeader>
        <CardBody>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <MetricsCard
              label={t("backtest.kpi.totalReturn")}
              value={formatPercent(totalReturn, 2)}
              tone={
                totalReturn === undefined
                  ? "default"
                  : totalReturn >= 0
                    ? "up"
                    : "down"
              }
              trend={
                totalReturn === undefined
                  ? undefined
                  : totalReturn >= 0
                    ? "up"
                    : "down"
              }
            />
            <MetricsCard
              label={t("backtest.kpi.sharpe")}
              value={formatNumber(sharpe, 2)}
              hint={t("backtest.kpi.sharpeHint")}
            />
            <MetricsCard
              label={t("backtest.kpi.drawdown")}
              value={formatPercent(drawdown, 2)}
              tone="down"
            />
            <MetricsCard
              label={t("backtest.kpi.winRate")}
              value={formatPercent(winRate, 1)}
              hint={`${totalTrades ?? 0} ${t("backtest.kpi.trades")}`}
            />
            <MetricsCard
              label={t("backtest.kpi.finalEquity")}
              value={formatCurrency(finalEquity)}
              tone="brand"
              hint={
                initialCapital !== undefined
                  ? `${t("backtest.kpi.from")} ${formatCurrency(initialCapital)}`
                  : undefined
              }
            />
            <MetricsCard
              label={t("backtest.kpi.profitFactor")}
              value={formatNumber(profitFactor, 2)}
            />
            <MetricsCard
              label={t("backtest.kpi.commissions")}
              value={formatCurrency(asNumber(metrics.total_commissions))}
            />
            <MetricsCard
              label={t("backtest.kpi.avgTrade")}
              value={formatPercent(asNumber(metrics.avg_trade_return), 2)}
            />
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("backtest.detail.equityTitle")}</CardTitle>
            <CardDescription>
              {benchmark.length > 0
                ? t("backtest.detail.equityWithBench")
                : t("backtest.detail.equityNoBench")}
            </CardDescription>
          </div>
          {finalEquity !== undefined && (
            <div className="text-end">
              <p className="text-[11px] uppercase tracking-wider text-fg-muted">
                {t("backtest.kpi.finalEquity")}
              </p>
              <p className="num text-lg font-semibold text-brand-400">
                {formatCurrency(finalEquity)}
              </p>
            </div>
          )}
        </CardHeader>
        <CardBody>
          {equity.length === 0 ? (
            <EmptyState
              icon={<BarChart3 className="h-4 w-4" />}
              title={t("backtest.detail.noEquity.title")}
              description={t("backtest.detail.noEquity.desc")}
            />
          ) : (
            <div className="ltr-island">
              <EquityCurve equity={equity} benchmark={benchmark} />
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("backtest.detail.tradesTitle")}</CardTitle>
            <CardDescription>
              {trades.length} {t("backtest.detail.tradesDesc")}
            </CardDescription>
          </div>
          {showRlColumns && (
            <Badge tone="brand">
              <Brain className="h-2.5 w-2.5" aria-hidden />
              {t("backtest.detail.rlBadge")}
            </Badge>
          )}
        </CardHeader>
        <CardBody className="p-0">
          {trades.length === 0 ? (
            <div className="p-5">
              <EmptyState
                icon={<BarChart3 className="h-4 w-4" />}
                title={t("backtest.detail.noTrades.title")}
                description={t("backtest.detail.noTrades.desc")}
              />
            </div>
          ) : (
            <TradeTable trades={trades} showRl={showRlColumns} />
          )}
        </CardBody>
      </Card>

      <RlActivityCard
        decisions={rlDecisions}
        source={rlSource}
        runId={runId}
      />
    </>
  );
}

function TradeTable({
  trades,
  showRl,
}: {
  trades: BacktestTrade[];
  showRl: boolean;
}) {
  const t = useT();
  return (
    <div className="overflow-x-auto">
      <table
        className="w-full text-xs"
        aria-label={t("backtest.detail.tradesAria")}
      >
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wider text-fg-muted border-b border-line">
            <th className="px-5 py-2.5">{t("backtest.trade.date")}</th>
            <th className="px-5 py-2.5">{t("backtest.trade.action")}</th>
            <th className="px-5 py-2.5 text-end">
              {t("backtest.trade.shares")}
            </th>
            <th className="px-5 py-2.5 text-end">
              {t("backtest.trade.price")}
            </th>
            <th className="px-5 py-2.5 text-end">
              {t("backtest.trade.value")}
            </th>
            <th className="px-5 py-2.5 text-end">
              {t("backtest.trade.pnl")}
            </th>
            <th className="px-5 py-2.5">{t("backtest.trade.signal")}</th>
            <th className="px-5 py-2.5 text-end">
              {t("backtest.trade.conf")}
            </th>
            {showRl && (
              <>
                <th
                  className="px-5 py-2.5 text-end"
                  title="rl_size_multiplier"
                >
                  {t("backtest.trade.rlMult")}
                </th>
                <th
                  className="px-5 py-2.5 text-end"
                  title="rl_action_index"
                >
                  {t("backtest.trade.rlAction")}
                </th>
              </>
            )}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {trades.map((tr, i) => {
            const pnl = asNumber(tr.realized_pnl ?? tr.pnl);
            const price = asNumber(tr.exec_price ?? tr.price ?? tr.close_price);
            const value = asNumber(
              tr.value ??
                (price !== undefined && tr.shares !== undefined
                  ? price * (tr.shares as number)
                  : undefined)
            );
            const action = String(tr.action ?? "").toUpperCase();
            const isBuy = action === "BUY";
            const isSell = action === "SELL";
            const conf = asNumber(tr.confidence);
            const rlMult = asNumber(tr.rl_size_multiplier);

            return (
              <tr
                key={`${tr.date ?? "?"}-${i}`}
                className="hover:bg-ink-800/40 transition-colors"
              >
                <td className="px-5 py-2 num text-fg">{tr.date ?? "—"}</td>
                <td className="px-5 py-2">
                  <Badge
                    tone={isBuy ? "up" : isSell ? "down" : "neutral"}
                  >
                    {action || "—"}
                  </Badge>
                </td>
                <td className="px-5 py-2 num text-end">
                  {tr.shares !== undefined
                    ? formatCompact(Number(tr.shares))
                    : "—"}
                </td>
                <td className="px-5 py-2 num text-end">
                  {price !== undefined ? price.toFixed(2) : "—"}
                </td>
                <td className="px-5 py-2 num text-end">
                  {value !== undefined ? formatCompact(value) : "—"}
                </td>
                <td
                  className={cn(
                    "px-5 py-2 num text-end",
                    pnl === undefined
                      ? "text-fg-subtle"
                      : pnl >= 0
                        ? "text-up"
                        : "text-down"
                  )}
                >
                  {pnl !== undefined ? formatCompact(pnl) : "—"}
                </td>
                <td className="px-5 py-2 text-fg-muted">
                  {tr.signal ?? "—"}
                </td>
                <td className="px-5 py-2 num text-end text-fg-muted">
                  {conf !== undefined ? `${Math.round(conf * 100)}%` : "—"}
                </td>
                {showRl && (
                  <>
                    <td
                      className={cn(
                        "px-5 py-2 num text-end",
                        rlMult !== undefined && rlMult < 1
                          ? "text-amber-300"
                          : "text-fg-muted"
                      )}
                    >
                      {rlMult !== undefined ? rlMult.toFixed(2) : "—"}
                    </td>
                    <td className="px-5 py-2 num text-end text-fg-subtle">
                      {tr.rl_action_index !== undefined
                        ? String(tr.rl_action_index)
                        : "—"}
                    </td>
                  </>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function RlActivityCard({
  decisions,
  source,
  runId,
}: {
  decisions: RlDecision[];
  source: "postgres" | "none" | null;
  runId: string;
}) {
  const t = useT();
  if (source === "none" && decisions.length === 0) {
    // Don't show the card at all when Postgres is unreachable — keeps the
    // page tidy on dev machines without a DB. The RL info on individual
    // trades still surfaces above.
    return null;
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>{t("backtest.detail.rlPanel.title")}</CardTitle>
          <CardDescription>
            {decisions.length} {t("backtest.detail.rlPanel.desc")}
          </CardDescription>
        </div>
        <Badge tone="brand">
          <Brain className="h-2.5 w-2.5" aria-hidden /> postgres
        </Badge>
      </CardHeader>
      <CardBody>
        {decisions.length === 0 ? (
          <p className="text-xs text-fg-muted">
            {t("backtest.detail.rlPanel.empty")}{" "}
            <span className="num text-fg-subtle">
              session={runId.slice(0, 12)}…
            </span>
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {decisions.slice(0, 5).map((d, i) => (
              <li
                key={i}
                className="rounded-md border border-line bg-ink-900/40 px-3 py-2"
              >
                <div className="flex items-center gap-2 flex-wrap text-[11px] mb-1.5">
                  {d.trade_date && (
                    <span className="num text-fg">{d.trade_date}</span>
                  )}
                  <span className="num text-fg-subtle">
                    {(d.session_id || "").slice(0, 10)}…
                  </span>
                  {d.opinion_summary && (
                    <span className="text-fg-muted">{d.opinion_summary}</span>
                  )}
                </div>
                {d.structured_output && (
                  <JSONViewer
                    data={d.structured_output as Record<string, unknown>}
                    defaultExpandDepth={1}
                  />
                )}
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}
