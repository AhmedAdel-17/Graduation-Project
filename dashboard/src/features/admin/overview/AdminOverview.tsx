import { Link } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  CheckCircle2,
  Database,
  GitBranch,
  HardDrive,
  Radio,
  AlertTriangle,
} from "lucide-react";
import { AdminShell } from "../layout/AdminShell";
import { AdminCard } from "../shared/AdminCard";
import { useHealth } from "../../../hooks/useDiagnostics";
import { ADMIN_NAV } from "../layout/adminNav";
import { CATALOG_NODES } from "../../../data/agent-catalog";
import { cn } from "../../../lib/utils";
import type { HealthResponse } from "../../../services/api/types";

type Tone = "ok" | "warn" | "bad" | "muted";

const TONE: Record<Tone, string> = {
  ok: "text-emerald-600 dark:text-emerald-400",
  warn: "text-amber-600 dark:text-amber-400",
  bad: "text-red-600 dark:text-red-400",
  muted: "text-stone-500 dark:text-[var(--ink-3)]",
};

function StatTile({
  icon,
  label,
  value,
  tone = "muted",
  hint,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  tone?: Tone;
  hint?: string;
}) {
  return (
    <AdminCard className="p-4">
      <div className="flex items-center gap-2 text-stone-500 dark:text-[var(--ink-3)]">
        <span className="shrink-0">{icon}</span>
        <span className="text-[11px] font-medium uppercase tracking-wider truncate">
          {label}
        </span>
      </div>
      <div className={cn("mt-2 text-[20px] font-semibold display-num", TONE[tone])}>
        {value}
      </div>
      {hint && (
        <div className="mt-1 text-[11px] text-stone-400 dark:text-[var(--ink-3)] truncate">
          {hint}
        </div>
      )}
    </AdminCard>
  );
}

function deriveHealth(h: HealthResponse | undefined) {
  const d = h?.diagnostics;
  const degraded = d?.degraded ?? false;
  const sysTone: Tone = !h ? "muted" : degraded ? "warn" : "ok";
  const pg = d?.postgres;
  const redis = d?.redis;
  const mem = d?.memory;
  return {
    degraded,
    degradedReasons: d?.degraded_reasons ?? [],
    sysValue: !h ? "—" : degraded ? "Degraded" : "Healthy",
    sysTone,
    pgTone: (pg?.configured ? (pg.reachable ? "ok" : "bad") : "muted") as Tone,
    pgValue: !pg?.configured ? "Off" : pg.reachable ? "Connected" : "Unreachable",
    pgHint: pg?.purpose ?? "Audit + backtest persistence",
    redisTone: (redis?.configured ? (redis.reachable ? "ok" : "bad") : "muted") as Tone,
    redisValue: !redis?.configured ? "Off" : redis.reachable ? "Connected" : "Unreachable",
    redisHint: redis?.purpose ?? "WebSocket streaming",
    memTone: (mem ? "ok" : "muted") as Tone,
    memValue: mem?.vector_store ?? "—",
    memHint: mem?.chroma_persistent ? "Persistent" : mem?.backend ?? "vector memory",
  };
}

export function AdminOverview() {
  const { data, isLoading, isError } = useHealth(true);
  const v = deriveHealth(data);
  const agentCount = CATALOG_NODES.filter((n) => n.kind === "agent").length;

  return (
    <AdminShell
      title="Admin Overview"
      subtitle="Observability for the EGX multi-agent pipeline"
    >
      <div className="flex flex-col gap-6">
        {isError && (
          <div
            role="alert"
            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-red-200 bg-red-50 text-red-700 text-[12px] dark:border-red-900/40 dark:bg-red-900/20 dark:text-red-300"
          >
            <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
            <span>Couldn’t reach the API server — health metrics are unavailable.</span>
          </div>
        )}

        {/* Health summary */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <StatTile
            icon={<CheckCircle2 className="h-4 w-4" />}
            label="System"
            value={isLoading ? "…" : v.sysValue}
            tone={v.sysTone}
            hint={data?.egx_tools ? "EGX tools online" : undefined}
          />
          <StatTile
            icon={<Database className="h-4 w-4" />}
            label="Postgres"
            value={isLoading ? "…" : v.pgValue}
            tone={v.pgTone}
            hint={v.pgHint}
          />
          <StatTile
            icon={<Radio className="h-4 w-4" />}
            label="Redis"
            value={isLoading ? "…" : v.redisValue}
            tone={v.redisTone}
            hint={v.redisHint}
          />
          <StatTile
            icon={<HardDrive className="h-4 w-4" />}
            label="Memory"
            value={isLoading ? "…" : v.memValue}
            tone={v.memTone}
            hint={v.memHint}
          />
        </div>

        {/* Degraded reasons */}
        {v.degradedReasons.length > 0 && (
          <AdminCard className="p-4 border-amber-200 dark:border-amber-900/40">
            <div className="flex items-center gap-2 text-amber-600 dark:text-amber-400">
              <AlertTriangle className="h-4 w-4" aria-hidden />
              <span className="text-[12px] font-semibold uppercase tracking-wider">
                Degraded subsystems
              </span>
            </div>
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {v.degradedReasons.map((r) => (
                <li
                  key={r}
                  className="px-2 py-0.5 rounded-md text-[11px] font-mono bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-900/20 dark:text-amber-300 dark:border-amber-900/40"
                >
                  {r}
                </li>
              ))}
            </ul>
          </AdminCard>
        )}

        {/* Pipeline at a glance */}
        <AdminCard className="p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <GitBranch className="h-4 w-4 text-stone-500 dark:text-[var(--ink-3)]" aria-hidden />
              <span className="text-[13px] font-semibold text-ink">Pipeline</span>
            </div>
            <Link
              to="/admin/agent-monitor"
              className="inline-flex items-center gap-1 text-[12px] font-medium text-blue-600 dark:text-sky-400 hover:underline"
            >
              Open monitor <ArrowRight className="h-3 w-3" aria-hidden />
            </Link>
          </div>
          <p className="mt-1.5 text-[12.5px] text-stone-500 dark:text-[var(--ink-3)]">
            {CATALOG_NODES.length} stages · {agentCount} LLM agents · Data →
            Analysts → Debate → Trader → Risk → Recommendation.
          </p>
        </AdminCard>

        {/* Quick links */}
        <div>
          <div className="eyebrow px-1 mb-2 text-stone-400 dark:text-[var(--ink-3)]">
            Monitoring screens
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {ADMIN_NAV.filter((n) => n.to !== "/admin").map(({ to, label, desc, icon: Icon }) => (
              <Link key={to} to={to} className="group">
                <AdminCard className="p-4 h-full transition-colors hover:border-stone-300 dark:hover:border-[var(--hairline-2)]">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-center gap-2.5 min-w-0">
                      <div className="h-9 w-9 shrink-0 rounded-lg border border-stone-200 dark:border-[var(--hairline)] bg-stone-50 dark:bg-white/[0.03] flex items-center justify-center">
                        <Icon className="h-4 w-4 text-stone-600 dark:text-[var(--ink-2)]" aria-hidden />
                      </div>
                      <div className="min-w-0">
                        <div className="text-[13px] font-semibold text-ink truncate">{label}</div>
                        <div className="text-[11.5px] text-stone-500 dark:text-[var(--ink-3)] truncate">
                          {desc}
                        </div>
                      </div>
                    </div>
                    <ArrowRight className="h-4 w-4 text-stone-300 dark:text-stone-600 group-hover:text-stone-500 dark:group-hover:text-[var(--ink-2)] transition-colors shrink-0" aria-hidden />
                  </div>
                </AdminCard>
              </Link>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2 text-[11px] text-stone-400 dark:text-[var(--ink-3)]">
          <Activity className="h-3 w-3" aria-hidden />
          Read-only observability. No trading actions are triggered from this suite.
        </div>
      </div>
    </AdminShell>
  );
}
