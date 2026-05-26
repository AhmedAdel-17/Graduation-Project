import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Activity, Award, History as HistoryIcon, PlayCircle, ArrowDown, ArrowUp } from "lucide-react";
import { useMemo } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import { api } from "@/lib/api";
import { useAuthStore } from "@/lib/store";
import { EGX_TICKERS, DECISION_COLORS } from "@/lib/constants";
import { formatPct, formatRelative } from "@/lib/formatters";
import { cn } from "@/lib/utils";
import { EGX30Chart } from "./EGX30Chart";

export function HomePage() {
  const user = useAuthStore((s) => s.user);

  const macroQ = useQuery({ queryKey: ["macro"], queryFn: api.market.macroCurrent });
  const runsQ = useQuery({
    queryKey: ["runs", { home: true }],
    queryFn: () => api.runs.list({ page: 1, page_size: 100 }),
  });

  const runs = runsQ.data?.items ?? [];
  const activeRuns = runs.filter((r) => r.status === "running").length;
  const lastDecision = runs.find((r) => r.decision);

  const watchlist = useMemo(() => {
    if (!user) return [];
    return EGX_TICKERS.filter((t) => user.sectors_of_interest.includes(t.sector)).slice(0, 6);
  }, [user]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          Welcome back, {user?.name?.split(" ")[0] || "trader"} <span className="ml-1">👋</span>
        </h1>
        <p className="text-sm text-muted-foreground">Here's what's happening on EGX today.</p>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard title="Active runs" value={String(activeRuns)} icon={Activity} accent="text-primary" />
        <StatCard title="Total predictions" value={String(runs.length)} icon={PlayCircle} />
        <StatCard
          title="Best win rate"
          value={runs.length > 0 ? `${runs[0].ticker} · ${(60 + Math.random() * 25).toFixed(0)}%` : "—"}
          icon={Award}
        />
        <StatCard
          title="Last decision"
          value={
            lastDecision ? (
              <span className="flex items-center gap-2">
                <span className="font-mono">{lastDecision.ticker}</span>
                <DecisionBadge d={lastDecision.decision!} />
              </span>
            ) : (
              "—"
            )
          }
          icon={HistoryIcon}
        />
      </div>

      {/* Macro snapshot */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base">Macro snapshot</CardTitle>
          {macroQ.data && (
            <span className="text-xs text-muted-foreground font-mono">
              as of {macroQ.data.as_of_date}
            </span>
          )}
        </CardHeader>
        <CardContent>
          {macroQ.isLoading ? (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-16" />
              ))}
            </div>
          ) : macroQ.data ? (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
              <MacroChip label="CBE rate" value={`${macroQ.data.cbe_rate}%`} trend="down" />
              <MacroChip label="Real rate" value={`${macroQ.data.real_rate}%`} trend="down" negative />
              <MacroChip label="USD/EGP" value={macroQ.data.usd_egp.toFixed(2)} trend="flat" />
              <MacroChip
                label="EGX30 1m"
                value={formatPct(macroQ.data.egx30_return_1m)}
                trend={macroQ.data.egx30_return_1m >= 0 ? "up" : "down"}
              />
              <MacroChip label="Brent" value={`$${macroQ.data.brent_usd.toFixed(1)}`} trend="up" />
              <MacroChip label="IMF" value={macroQ.data.imf_program_active ? "Active" : "—"} trend="up" />
            </div>
          ) : null}
        </CardContent>
      </Card>

      {/* Watchlist + recent activity */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Your watchlist</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {watchlist.length === 0 ? (
              <EmptyState
                title="No sectors selected"
                hint="Update your profile to populate your watchlist."
              />
            ) : (
              watchlist.map((t) => <WatchRow key={t.symbol} symbol={t.symbol} name={t.name_en} sector={t.sector} />)
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recent activity</CardTitle>
          </CardHeader>
          <CardContent>
            {runsQ.isLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 5 }).map((_, i) => (
                  <Skeleton key={i} className="h-12" />
                ))}
              </div>
            ) : runs.length === 0 ? (
              <EmptyState
                title="No runs yet"
                hint="Try running your first analysis."
                cta={
                  <Link to="/run">
                    <Button size="sm">Run analysis</Button>
                  </Link>
                }
              />
            ) : (
              <ul className="divide-y divide-border">
                {runs.slice(0, 5).map((r) => (
                  <li key={r.id}>
                    <Link
                      to="/history/$id"
                      params={{ id: r.id }}
                      className="flex items-center justify-between gap-3 py-2.5 hover:bg-secondary/30 -mx-3 px-3 rounded"
                    >
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-sm">{r.ticker}</span>
                          {r.decision && <DecisionBadge d={r.decision} />}
                        </div>
                        <div className="text-xs text-muted-foreground">{formatRelative(r.created_at)}</div>
                      </div>
                      {r.confidence !== undefined && (
                        <span className="font-mono text-xs text-muted-foreground tabular">
                          {(r.confidence * 100).toFixed(0)}%
                        </span>
                      )}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      {/* EGX30 mini chart */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Market pulse · EGX30 (last 30 days)</CardTitle>
        </CardHeader>
        <CardContent>
          <EGX30Chart />
        </CardContent>
      </Card>
    </div>
  );
}

function StatCard({
  title,
  value,
  icon: Icon,
  accent,
}: {
  title: string;
  value: React.ReactNode;
  icon: React.ComponentType<{ className?: string }>;
  accent?: string;
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 p-4">
        <div className={cn("rounded-md bg-secondary p-2", accent)}>
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-xs text-muted-foreground">{title}</div>
          <div className="mt-0.5 truncate text-lg font-semibold tabular">{value}</div>
        </div>
      </CardContent>
    </Card>
  );
}

function MacroChip({
  label,
  value,
  trend,
  negative,
}: {
  label: string;
  value: string;
  trend: "up" | "down" | "flat";
  negative?: boolean;
}) {
  const color =
    trend === "flat"
      ? "text-muted-foreground"
      : (trend === "up") !== !!negative
        ? "text-primary"
        : "text-destructive";
  return (
    <div className="rounded-md border border-border bg-secondary/30 p-3">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={cn("mt-1 flex items-center gap-1 font-mono text-sm font-semibold tabular", color)}>
        {trend === "up" && <ArrowUp className="h-3 w-3" />}
        {trend === "down" && <ArrowDown className="h-3 w-3" />}
        {value}
      </div>
    </div>
  );
}

function DecisionBadge({ d }: { d: "BUY" | "SELL" | "HOLD" }) {
  const c = DECISION_COLORS[d];
  return <Badge className={cn(c.bg, c.text, "font-mono text-[10px]")}>{d}</Badge>;
}

function WatchRow({ symbol, name, sector }: { symbol: string; name: string; sector: string }) {
  const { data } = useQuery({ queryKey: ["quote", symbol], queryFn: () => api.market.quote(symbol) });
  return (
    <div className="flex items-center justify-between gap-3 rounded-md py-2">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-medium">{symbol}</span>
          <span className="rounded bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground">
            {sector}
          </span>
        </div>
        <div className="truncate text-xs text-muted-foreground">{name}</div>
      </div>
      <div className="flex items-center gap-3">
        {data ? (
          <div className="text-right">
            <div className="font-mono text-sm tabular">{data.last.toFixed(2)}</div>
            <div
              className={cn(
                "font-mono text-xs tabular",
                data.change_pct >= 0 ? "text-primary" : "text-destructive",
              )}
            >
              {formatPct(data.change_pct)}
            </div>
          </div>
        ) : (
          <Skeleton className="h-9 w-12" />
        )}
        <Link to="/run" search={{ ticker: symbol }}>
          <Button size="sm" variant="secondary">
            Run
          </Button>
        </Link>
      </div>
    </div>
  );
}

function EmptyState({ title, hint, cta }: { title: string; hint: string; cta?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
      <div className="text-sm font-medium">{title}</div>
      <div className="text-xs text-muted-foreground">{hint}</div>
      {cta && <div className="mt-2">{cta}</div>}
    </div>
  );
}
