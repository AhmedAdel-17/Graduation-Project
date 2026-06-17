import { useMemo, useState } from "react";
import { Activity, Cpu, Gauge, Info, Layers } from "lucide-react";
import { AdminShell } from "../layout/AdminShell";
import { AdminCard } from "../shared/AdminCard";
import { BarList, DonutChart, TrendChart, type BarItem } from "./Charts";
import { useAdminMetrics } from "../shared/useAdminData";
import { catalogNodeByAgentName } from "../../../data/agent-catalog";
import { cn } from "../../../lib/utils";

type Win = 7 | 30 | 90;
type Gran = "daily" | "weekly" | "monthly";

const DECISION_COLOR: Record<string, string> = {
  BUY: "#10b981",
  HOLD: "#f59e0b",
  SELL: "#ef4444",
  OTHER: "#94a3b8",
};

function aggregate(daily: { day: string | null; runs: number }[], gran: Gran) {
  if (gran === "daily") return daily.map((d) => ({ label: d.day ?? "", value: d.runs }));
  const buckets = new Map<string, number>();
  for (const d of daily) {
    if (!d.day) continue;
    let key = d.day;
    if (gran === "monthly") key = d.day.slice(0, 7);
    else {
      const dt = new Date(d.day);
      const onejan = new Date(dt.getFullYear(), 0, 1);
      const week = Math.ceil(((dt.getTime() - onejan.getTime()) / 86400000 + onejan.getDay() + 1) / 7);
      key = `${dt.getFullYear()}-W${String(week).padStart(2, "0")}`;
    }
    buckets.set(key, (buckets.get(key) ?? 0) + d.runs);
  }
  return Array.from(buckets.entries()).map(([label, value]) => ({ label, value }));
}

function ChartCard({
  title,
  icon,
  children,
  className,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <AdminCard className={cn("flex flex-col", className)}>
      <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-stone-200/80 dark:border-[var(--hairline)]">
        <div className="flex items-center gap-2">
          <span className="text-stone-500 dark:text-[var(--ink-3)]">{icon}</span>
          <span className="text-[13px] font-semibold text-ink">{title}</span>
        </div>
      </div>
      <div className="p-4 flex-1">{children}</div>
    </AdminCard>
  );
}

export function PerformancePage() {
  const [win, setWin] = useState<Win>(30);
  const [gran, setGran] = useState<Gran>("daily");
  const { metrics, available, isLoading } = useAdminMetrics(win);

  const agentRows = useMemo(
    () =>
      metrics.per_agent.map((a) => {
        const name = a.agent_name ?? "unknown";
        return {
          label: catalogNodeByAgentName(name)?.label ?? name,
          executions: a.executions,
          avgConfidence: a.avg_confidence,
        };
      }),
    [metrics.per_agent]
  );

  const trend = useMemo(() => aggregate(metrics.daily_runs, gran), [metrics.daily_runs, gran]);

  const decisionSlices = Object.entries(metrics.decision_counts)
    .filter(([, v]) => v > 0)
    .map(([label, value]) => ({ label, value, color: DECISION_COLOR[label] ?? "#94a3b8" }));

  const execBars: BarItem[] = agentRows.map((r) => ({ label: r.label, value: r.executions, tone: "blue" }));
  const confBars: BarItem[] = agentRows
    .filter((r) => r.avgConfidence !== null)
    .map((r) => ({
      label: r.label,
      value: Math.round((r.avgConfidence ?? 0) * 100),
      display: `${Math.round((r.avgConfidence ?? 0) * 100)}%`,
      tone: "emerald",
    }));

  const winBtn = (w: Win) =>
    cn(
      "h-8 px-3 rounded-md text-[12px] font-medium border transition-colors",
      win === w
        ? "bg-stone-900 text-white border-stone-900 dark:bg-white dark:text-stone-900 dark:border-white"
        : "bg-white text-stone-600 border-stone-200 hover:bg-stone-100 dark:bg-[var(--paper)] dark:text-[var(--ink-2)] dark:border-[var(--hairline)]"
    );

  return (
    <AdminShell title="Performance Analytics" subtitle="Runs · decisions · agent activity">
      <div className="flex flex-col gap-5">
        {/* Window selector */}
        <div className="flex items-center gap-2">
          <span className="text-[11px] uppercase tracking-wider text-stone-500 dark:text-[var(--ink-3)]">Window</span>
          {[7, 30, 90].map((w) => (
            <button key={w} type="button" onClick={() => setWin(w as Win)} className={winBtn(w as Win)}>
              {w}d
            </button>
          ))}
          {isLoading && <span className="text-[11px] text-stone-400 dark:text-[var(--ink-3)]">loading…</span>}
        </div>

        {!available && !isLoading && (
          <div className="flex items-start gap-2 px-3 py-2.5 rounded-lg border border-stone-200 dark:border-[var(--hairline)] bg-stone-50 dark:bg-white/[0.02] text-[12px] text-stone-600 dark:text-[var(--ink-2)]">
            <Info className="h-4 w-4 mt-0.5 shrink-0 text-stone-400 dark:text-[var(--ink-3)]" aria-hidden />
            <span>
              No audit data available. These analytics read the Postgres audit tables
              (<span className="font-mono">analysis_sessions</span> / <span className="font-mono">agent_events</span>) —
              run analyses with <span className="font-mono">POSTGRES_URL</span> configured to populate them.
            </span>
          </div>
        )}

        {/* KPI tiles (all real) */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <KpiTile icon={<Activity className="h-4 w-4" />} label="Total runs" value={metrics.total_runs.toLocaleString()} />
          <KpiTile icon={<Layers className="h-4 w-4" />} label="Agent events" value={metrics.total_events.toLocaleString()} />
          <KpiTile
            icon={<Gauge className="h-4 w-4" />}
            label="Avg confidence"
            value={metrics.avg_confidence !== null ? `${Math.round(metrics.avg_confidence * 100)}%` : "—"}
          />
          <KpiTile icon={<Cpu className="h-4 w-4" />} label="Active agents" value={String(agentRows.length)} />
        </div>

        {/* Trend + decision mix */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <ChartCard title="Runs over time" icon={<Activity className="h-4 w-4" />} className="lg:col-span-2">
            <div className="flex items-center gap-1.5 mb-3">
              {(["daily", "weekly", "monthly"] as Gran[]).map((g) => (
                <button
                  key={g}
                  type="button"
                  onClick={() => setGran(g)}
                  className={cn(
                    "h-7 px-2.5 rounded-md text-[11.5px] capitalize border transition-colors",
                    gran === g
                      ? "bg-stone-100 text-ink border-stone-200 dark:bg-white/[0.06] dark:border-[var(--hairline)]"
                      : "bg-transparent text-stone-500 border-transparent hover:bg-stone-100/70 dark:text-[var(--ink-3)] dark:hover:bg-white/[0.04]"
                  )}
                >
                  {g}
                </button>
              ))}
            </div>
            <TrendChart points={trend} />
          </ChartCard>

          <ChartCard title="Decision mix" icon={<Layers className="h-4 w-4" />}>
            {decisionSlices.length > 0 ? (
              <DonutChart slices={decisionSlices} />
            ) : (
              <p className="text-[12px] text-stone-400 dark:text-[var(--ink-3)]">No decisions recorded.</p>
            )}
          </ChartCard>
        </div>

        {/* Agent activity (real) */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <ChartCard title="Agent executions" icon={<Cpu className="h-4 w-4" />}>
            <BarList items={execBars} tone="blue" />
          </ChartCard>
          <ChartCard title="Agent avg confidence" icon={<Gauge className="h-4 w-4" />}>
            <BarList items={confBars} tone="emerald" />
          </ChartCard>
        </div>

        <p className="text-[11px] text-stone-400 dark:text-[var(--ink-3)]">
          All figures are read from the audit DB. Latency, token usage, and cost are intentionally not
          shown — per-call usage is not yet persisted to <span className="font-mono">agent_events</span>;
          they will appear here once that instrumentation lands.
        </p>
      </div>
    </AdminShell>
  );
}

function KpiTile({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <AdminCard className="p-4">
      <div className="flex items-center gap-2 text-stone-500 dark:text-[var(--ink-3)]">
        <span>{icon}</span>
        <span className="text-[11px] font-medium uppercase tracking-wider">{label}</span>
      </div>
      <div className="mt-2 text-[20px] font-semibold display-num text-ink num">{value}</div>
    </AdminCard>
  );
}
