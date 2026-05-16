import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ChevronRight,
  FlaskConical,
  GitCompare,
  Plus,
  Search,
  X,
} from "lucide-react";
import { AppShell } from "../../components/layout/AppShell";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { Skeleton } from "../../components/ui/Skeleton";
import { EmptyState } from "../../components/ui/EmptyState";
import { Button } from "../../components/ui/Button";
import { useBacktests } from "../../hooks/useBacktest";
import { useT } from "../../lib/i18n";
import { cn, formatNumber, formatPercent } from "../../lib/utils";
import type { BacktestSession } from "../../services/api/types";

type EngineFilter = "all" | "llm" | "bt";

function asNumber(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

export function BacktestLabPage() {
  const t = useT();
  const { data, isLoading } = useBacktests();
  const [query, setQuery] = useState("");
  const [engine, setEngine] = useState<EngineFilter>("all");

  const filtered = useMemo<BacktestSession[]>(() => {
    const all = data?.sessions ?? [];
    const needle = query.trim().toLowerCase();
    return all.filter((s) => {
      if (engine === "llm" && s.engine !== "llm_multi_agent") return false;
      if (engine === "bt" && s.engine !== "classical_technical") return false;
      if (!needle) return true;
      return (
        s.ticker.toLowerCase().includes(needle) ||
        s.session_id.toLowerCase().includes(needle)
      );
    });
  }, [data, query, engine]);

  return (
    <AppShell
      title={t("backtest.lab.title")}
      subtitle={t("backtest.lab.subtitle")}
    >
      <div className="flex flex-col gap-5">
        <div className="flex flex-wrap items-center gap-2">
          <Link to="/backtest/new">
            <Button leftIcon={<Plus className="h-4 w-4" aria-hidden />}>
              {t("backtest.lab.newCta")}
            </Button>
          </Link>
          <Link to="/backtest/compare">
            <Button
              variant="outline"
              leftIcon={<GitCompare className="h-4 w-4" aria-hidden />}
            >
              {t("backtest.lab.compareCta")}
            </Button>
          </Link>
          <span className="ms-auto text-xs text-fg-muted">
            {filtered.length} / {data?.sessions?.length ?? 0}{" "}
            {t("backtest.lab.shownSuffix")}
          </span>
        </div>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>{t("backtest.lab.list.title")}</CardTitle>
              <CardDescription>
                {t("backtest.lab.list.desc")}
              </CardDescription>
            </div>
          </CardHeader>
          <CardBody className="flex flex-col gap-3">
            <div className="grid grid-cols-1 md:grid-cols-[1fr_220px] gap-2.5">
              <div className="flex items-center gap-1.5 h-9 px-3 rounded-md bg-ink-800/80 border border-line focus-within:border-brand-500/50">
                <Search
                  className="h-3.5 w-3.5 text-fg-subtle shrink-0"
                  aria-hidden
                />
                <input
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={t("backtest.lab.searchPlaceholder")}
                  className="flex-1 bg-transparent text-sm placeholder:text-fg-subtle focus:outline-none"
                />
                {query && (
                  <button
                    type="button"
                    onClick={() => setQuery("")}
                    aria-label={t("common.dismiss")}
                    className="h-5 w-5 inline-flex items-center justify-center text-fg-subtle hover:text-fg"
                  >
                    <X className="h-3 w-3" aria-hidden />
                  </button>
                )}
              </div>

              <div
                role="group"
                aria-label={t("backtest.lab.engineFilter")}
                className="flex items-center gap-1 h-9 px-1 rounded-md bg-ink-800/80 border border-line"
              >
                {(
                  [
                    ["all", "backtest.lab.engine.all"],
                    ["llm", "backtest.lab.engine.llm"],
                    ["bt", "backtest.lab.engine.bt"],
                  ] as const
                ).map(([key, labelKey]) => {
                  const active = engine === key;
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setEngine(key)}
                      aria-pressed={active}
                      className={cn(
                        "flex-1 h-6 text-[11px] font-medium rounded-sm",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50",
                        active
                          ? "bg-brand-500/15 text-brand-400"
                          : "text-fg-muted hover:text-fg"
                      )}
                    >
                      {t(labelKey)}
                    </button>
                  );
                })}
              </div>
            </div>

            {isLoading ? (
              <Skeleton className="h-48 w-full rounded-md" />
            ) : filtered.length === 0 ? (
              <EmptyState
                icon={<FlaskConical className="h-4 w-4" />}
                title={t("backtest.lab.empty.title")}
                description={t("backtest.lab.empty.desc")}
                action={
                  <Link
                    to="/backtest/new"
                    className="inline-flex items-center gap-1 text-xs text-brand-400 hover:text-brand-300"
                  >
                    <Plus className="h-3 w-3" aria-hidden />
                    {t("backtest.lab.newCta")}
                  </Link>
                }
              />
            ) : (
              <div className="overflow-x-auto -mx-5">
                <table
                  className="w-full text-sm"
                  aria-label={t("backtest.lab.list.aria")}
                >
                  <thead>
                    <tr className="text-left text-[10px] uppercase tracking-wider text-fg-muted border-b border-line">
                      <th className="px-5 py-2.5">
                        {t("backtest.col.ticker")}
                      </th>
                      <th className="px-5 py-2.5">
                        {t("backtest.col.engine")}
                      </th>
                      <th className="px-5 py-2.5 text-end">
                        {t("backtest.col.return")}
                      </th>
                      <th className="px-5 py-2.5 text-end">
                        {t("backtest.col.sharpe")}
                      </th>
                      <th className="px-5 py-2.5 text-end">
                        {t("backtest.col.trades")}
                      </th>
                      <th className="px-5 py-2.5 num text-fg-subtle">
                        {t("backtest.col.session")}
                      </th>
                      <th className="px-5 py-2.5 w-10" aria-label="open" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {filtered.map((row) => {
                      const ret = asNumber(row.metrics?.total_return_pct);
                      const sharpe = asNumber(row.metrics?.sharpe_ratio);
                      return (
                        <tr
                          key={row.session_id}
                          className="hover:bg-ink-800/40 transition-colors"
                        >
                          <td className="px-5 py-2.5">
                            <Link
                              to={`/workspace?ticker=${encodeURIComponent(row.ticker)}`}
                              className="num text-fg hover:text-brand-400"
                            >
                              {row.ticker}
                            </Link>
                          </td>
                          <td className="px-5 py-2.5">
                            <Badge
                              tone={
                                row.engine === "llm_multi_agent"
                                  ? "brand"
                                  : "accent"
                              }
                            >
                              {row.engine === "llm_multi_agent" ? "LLM" : "BT"}
                            </Badge>
                          </td>
                          <td
                            className={cn(
                              "px-5 py-2.5 num text-end",
                              ret === null
                                ? "text-fg-subtle"
                                : ret >= 0
                                  ? "text-up"
                                  : "text-down"
                            )}
                          >
                            {formatPercent(ret, 2)}
                          </td>
                          <td className="px-5 py-2.5 num text-end text-fg">
                            {formatNumber(sharpe, 2)}
                          </td>
                          <td className="px-5 py-2.5 num text-end text-fg-muted">
                            {row.total_trades}
                          </td>
                          <td className="px-5 py-2.5 num text-fg-subtle truncate max-w-[180px]">
                            {row.session_id.slice(0, 18)}…
                          </td>
                          <td className="px-5 py-2.5 text-end">
                            <Link
                              to={`/backtest/${encodeURIComponent(row.session_id)}`}
                              className="inline-flex items-center justify-end h-7 w-7 rounded-md text-fg-muted hover:text-fg hover:bg-ink-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
                              aria-label={t("backtest.col.openDetail")}
                            >
                              <ChevronRight
                                className="h-3.5 w-3.5"
                                aria-hidden
                              />
                            </Link>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardBody>
        </Card>
      </div>
    </AppShell>
  );
}
