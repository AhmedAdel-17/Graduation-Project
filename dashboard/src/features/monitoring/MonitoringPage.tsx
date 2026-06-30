import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  Brain,
  CheckCircle2,
  Clock,
  Database,
  ExternalLink,
  Gauge,
  HardDrive,
  Info,
  LayoutDashboard,
  Loader2,
  Radio,
  RefreshCw,
  Server,
  XCircle,
  Zap,
} from "lucide-react";
import { endpoints } from "../../services/api/endpoints";
import type {
  DataFreshnessResponse,
  DataSourceFreshness,
  HealthResponse,
  MetricsSummary,
  SystemStatus,
} from "../../services/api/types";
import { cn } from "../../lib/utils";

/* ═══════════════════════════════════════════════════════════════════════
   Developer Monitoring Page
   ═══════════════════════════════════════════════════════════════════════ */

export function MonitoringPage() {
  const statusQ = useQuery({
    queryKey: ["system-status"],
    queryFn: () => endpoints.systemStatus(),
    refetchInterval: 15_000,
  });
  const healthQ = useQuery({
    queryKey: ["health"],
    queryFn: () => endpoints.health(),
    refetchInterval: 15_000,
  });
  const metricsQ = useQuery({
    queryKey: ["metrics-summary"],
    queryFn: () => endpoints.metricsSummary(),
    refetchInterval: 10_000,
  });
  const freshnessQ = useQuery({
    queryKey: ["data-freshness"],
    queryFn: () => endpoints.dataFreshness(),
    refetchInterval: 30_000,
  });

  return (
    <div className="space-y-8">
      {/* Page header */}
      <header>
        <div className="eyebrow mb-3">Developer</div>
        <h1 className="display text-[36px] md:text-[42px] font-semibold leading-[1.05] text-ink">
          Monitoring
        </h1>
        <p className="text-[14px] text-ink-3 mt-3 max-w-xl leading-relaxed">
          Live infrastructure health, data freshness, and pipeline activity.
          All values are from the running API server — counters reset on restart.
        </p>
      </header>

      {/* Service status grid */}
      <QuerySection
        query={statusQ}
        label="Service health"
        icon={Server}
        render={(status) => <ServiceGrid status={status} />}
        emptyText="Waiting for status check..."
      />

      {/* Data freshness */}
      <QuerySection
        query={freshnessQ}
        label="Data freshness"
        icon={HardDrive}
        render={(data) => <DataFreshnessPanel data={data} />}
        emptyText="Checking data sources..."
      />

      {/* External tool links */}
      <ToolLinks status={statusQ.data} />

      {/* Pipeline metrics */}
      <QuerySection
        query={metricsQ}
        label="Pipeline metrics"
        icon={Gauge}
        render={(metrics) => <PipelineMetrics metrics={metrics} />}
        emptyText="No pipeline activity since server started."
      />

      {/* Memory & health details */}
      <QuerySection
        query={healthQ}
        label="Memory & storage"
        icon={Brain}
        render={(health) => <HealthDetails health={health} />}
        emptyText="Waiting for health diagnostics..."
      />

      {/* LLM call breakdown */}
      <LLMCallsSection metrics={metricsQ.data} isLoading={metricsQ.isLoading} error={metricsQ.error} />

      {/* Last recommendation */}
      <LastRunSection status={statusQ.data} isLoading={statusQ.isLoading} />
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   Generic query section wrapper — handles loading / error / empty
   ═══════════════════════════════════════════════════════════════════════ */

function QuerySection<T>({
  query,
  label,
  icon: Icon,
  render,
  emptyText,
}: {
  query: { data?: T; isLoading: boolean; isError: boolean; error: unknown };
  label: string;
  icon: typeof Server;
  render: (data: T) => React.ReactNode;
  emptyText: string;
}) {
  return (
    <section>
      <div className="flex items-center gap-2 mb-4">
        <Icon className="h-4 w-4 text-stone-500" />
        <span className="text-[14px] font-semibold text-ink">{label}</span>
        {query.isLoading && !query.data && (
          <Loader2 className="h-3.5 w-3.5 text-stone-400 animate-spin" />
        )}
      </div>
      {query.isError ? (
        <ErrorCard message={`Failed to load ${label.toLowerCase()}.`} detail={String(query.error)} />
      ) : query.data ? (
        render(query.data)
      ) : query.isLoading ? (
        <LoadingCard />
      ) : (
        <EmptyCard text={emptyText} />
      )}
    </section>
  );
}

function LoadingCard() {
  return (
    <div className="card p-6 flex items-center gap-3">
      <Loader2 className="h-4 w-4 text-stone-400 animate-spin" />
      <span className="text-[13px] text-ink-3">Loading...</span>
    </div>
  );
}

function EmptyCard({ text }: { text: string }) {
  return (
    <div className="card p-6 flex items-center gap-3">
      <Info className="h-4 w-4 text-stone-400" />
      <span className="text-[13px] text-ink-3">{text}</span>
    </div>
  );
}

function ErrorCard({ message, detail }: { message: string; detail?: string }) {
  return (
    <div className="card p-4 border-rose-200 dark:border-rose-900/40">
      <div className="flex items-center gap-2 text-rose-700 dark:text-rose-400">
        <XCircle className="h-4 w-4 shrink-0" />
        <span className="text-[13px] font-medium">{message}</span>
      </div>
      {detail && (
        <p className="text-[11px] text-rose-600/70 dark:text-rose-400/60 mt-1 ml-6 font-mono truncate">
          {detail}
        </p>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   Service Status Grid
   ═══════════════════════════════════════════════════════════════════════ */

const OPTIONAL_SERVICES = new Set(["Grafana", "Prometheus", "Loki"]);

function ServiceGrid({ status }: { status: SystemStatus }) {
  const services = [
    { name: "API Server", icon: Server, ...status.api_server },
    { name: "Dashboard", icon: LayoutDashboard, ...status.dashboard },
    { name: "Redis", icon: Radio, ...status.redis, note: "WebSocket streaming" },
    { name: "Grafana", icon: Gauge, ...status.grafana },
    { name: "Prometheus", icon: Activity, ...status.prometheus },
    { name: "Loki", icon: Database, ...status.loki, note: "Log aggregation" },
  ];

  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        {services.map((svc) => (
          <ServiceCard key={svc.name} {...svc} />
        ))}
      </div>
      {/* Explain optional services if any are offline */}
      {services.some(
        (s) => OPTIONAL_SERVICES.has(s.name) && s.status !== "up"
      ) && (
        <div className="mt-3 card px-4 py-3 flex items-start gap-2.5 text-[12px] text-ink-3 leading-relaxed">
          <Info className="h-3.5 w-3.5 mt-0.5 shrink-0 text-stone-400" />
          <span>
            Grafana, Prometheus, and Loki are optional local monitoring tools.
            They require Docker:{" "}
            <code className="mono text-[11px] px-1 py-0.5 rounded bg-stone-100 dark:bg-white/5">
              cd monitoring && docker compose up -d
            </code>
          </span>
        </div>
      )}
    </>
  );
}

function ServiceCard({
  name,
  icon: Icon,
  status,
  note,
}: {
  name: string;
  icon: typeof Server;
  status: string;
  url?: string;
  login?: string;
  note?: string;
}) {
  const isUp = status === "up";
  const isOptional = OPTIONAL_SERVICES.has(name);

  return (
    <div
      className={cn(
        "card p-4 flex flex-col gap-3",
        isUp
          ? "border-emerald-200 dark:border-emerald-900/40"
          : isOptional
          ? "border-stone-200 dark:border-[var(--hairline)]"
          : "border-rose-200 dark:border-rose-900/40"
      )}
    >
      <div className="flex items-center justify-between">
        <Icon className="h-5 w-5 text-stone-400" />
        {isUp ? (
          <CheckCircle2 className="h-4 w-4 text-emerald-500" />
        ) : isOptional ? (
          <span className="text-[9px] font-medium text-stone-400 px-1.5 py-0.5 rounded bg-stone-100 dark:bg-white/5">
            Optional
          </span>
        ) : (
          <XCircle className="h-4 w-4 text-rose-500" />
        )}
      </div>
      <div>
        <div className="text-[13px] font-semibold text-ink">{name}</div>
        <div
          className={cn(
            "text-[11px] font-medium mt-0.5",
            isUp
              ? "text-emerald-600"
              : isOptional
              ? "text-stone-400"
              : "text-rose-600"
          )}
        >
          {isUp ? "Online" : isOptional ? "Not running" : "Offline"}
        </div>
        {note && (
          <div className="text-[10.5px] text-ink-3 mt-1">{note}</div>
        )}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   External Tool Links
   ═══════════════════════════════════════════════════════════════════════ */

function ToolLinks({ status }: { status?: SystemStatus | null }) {
  // URLs come from system-status (backend reads env vars); fall back to localhost
  const grafanaBase = status?.grafana.url || "http://localhost:3000";
  const prometheusBase = status?.prometheus.url || "http://localhost:9090";
  const apiBase = status?.api_server.url || "http://localhost:8000";

  const links = [
    {
      name: "Grafana Dashboard",
      url: `${grafanaBase}/d/tradingagents-overview/tradingagents-overview`,
      desc: "25-panel monitoring dashboard: pipeline, LLM latency, signals, node duration",
      login: "Default login: admin / admin",
      isUp: status?.grafana.status === "up",
    },
    {
      name: "Prometheus",
      url: prometheusBase,
      desc: "Raw metric queries and target status",
      isUp: status?.prometheus.status === "up",
    },
    {
      name: "API Metrics",
      url: `${apiBase}/metrics`,
      desc: "Prometheus exposition format — raw counters and histograms",
      isUp: status?.api_server.status === "up",
    },
    {
      name: "API Health",
      url: `${apiBase}/api/health`,
      desc: "Runtime diagnostics JSON (memory, Postgres, Redis)",
      isUp: status?.api_server.status === "up",
    },
  ];

  return (
    <section>
      <div className="flex items-center gap-2 mb-4">
        <ExternalLink className="h-4 w-4 text-stone-500" />
        <span className="text-[14px] font-semibold text-ink">
          Developer tools
        </span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {links.map((link) => (
          <a
            key={link.name}
            href={link.url}
            target="_blank"
            rel="noopener noreferrer"
            className="card p-4 hover:border-stone-300 dark:hover:border-white/10 transition-colors group"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-[13px] font-semibold text-ink group-hover:text-stone-900 dark:group-hover:text-white">
                {link.name}
              </span>
              <div className="flex items-center gap-1.5">
                {link.isUp !== undefined && (
                  <span
                    className={cn(
                      "h-2 w-2 rounded-full",
                      link.isUp ? "bg-emerald-500" : "bg-stone-300 dark:bg-stone-600"
                    )}
                  />
                )}
                <ExternalLink className="h-3.5 w-3.5 text-stone-400 group-hover:text-stone-600" />
              </div>
            </div>
            <p className="text-[11.5px] text-ink-3 leading-relaxed">
              {link.desc}
            </p>
            {link.login && (
              <div className="mt-2 text-[10.5px] text-ink-3 font-mono">
                {link.login}
              </div>
            )}
          </a>
        ))}
      </div>
    </section>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   Pipeline Metrics
   ═══════════════════════════════════════════════════════════════════════ */

function formatUptime(seconds?: number): string {
  if (!seconds) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function PipelineMetrics({ metrics }: { metrics: MetricsSummary }) {
  const tiles = [
    {
      label: "Server uptime",
      value: formatUptime(metrics.server_uptime_seconds),
      icon: RefreshCw,
    },
    {
      label: "Active sessions",
      value: metrics.active_sessions?.toString() ?? "0",
      icon: Activity,
    },
    {
      label: "Pipeline runs",
      value: metrics.pipeline_runs?.toString() ?? "0",
      icon: Zap,
    },
    {
      label: "Avg duration",
      value: metrics.avg_pipeline_seconds
        ? `${metrics.avg_pipeline_seconds}s`
        : "—",
      icon: Clock,
    },
    {
      label: "WebSockets",
      value: metrics.active_websockets?.toString() ?? "0",
      icon: Radio,
    },
  ];

  const signalCounts = metrics.signals ?? {};
  const totalSignals = Object.values(signalCounts).reduce(
    (a, b) => a + b,
    0
  );

  // Check if this is a fresh server (all zeros)
  const allZero =
    !metrics.pipeline_runs &&
    !metrics.active_sessions &&
    totalSignals === 0;

  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {tiles.map((t) => {
          const Icon = t.icon;
          return (
            <div key={t.label} className="card p-4">
              <div className="flex items-center gap-2 mb-2">
                <Icon className="h-4 w-4 text-stone-400" />
                <span className="eyebrow text-stone-500">{t.label}</span>
              </div>
              <div className="text-[24px] font-semibold text-ink tabular-nums">
                {t.value}
              </div>
            </div>
          );
        })}
      </div>

      {allZero && (
        <div className="mt-3 card px-4 py-3 flex items-center gap-2.5 text-[12px] text-ink-3">
          <Info className="h-3.5 w-3.5 shrink-0 text-stone-400" />
          <span>
            No pipeline runs recorded yet. Counters are in-memory and reset
            when the API server restarts. Run a recommendation to see activity.
          </span>
        </div>
      )}

      {/* Signal distribution */}
      {totalSignals > 0 && (
        <div className="mt-3 card p-4">
          <div className="eyebrow text-stone-500 mb-3">
            Signal distribution ({totalSignals} total)
          </div>
          <div className="flex gap-3">
            {Object.entries(signalCounts).map(([sig, count]) => {
              const pct = ((count / totalSignals) * 100).toFixed(0);
              const color =
                sig === "BUY"
                  ? "bg-emerald-500"
                  : sig === "SELL"
                  ? "bg-rose-500"
                  : "bg-stone-500";
              return (
                <div key={sig} className="flex items-center gap-2">
                  <span className={cn("h-2.5 w-2.5 rounded-full", color)} />
                  <span className="text-[12px] font-medium text-ink uppercase">
                    {sig}
                  </span>
                  <span className="text-[12px] text-ink-3 tabular-nums">
                    {count} ({pct}%)
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   Health Details
   ═══════════════════════════════════════════════════════════════════════ */

function HealthDetails({ health }: { health: HealthResponse }) {
  const diag = health.diagnostics;
  if (!diag) {
    return (
      <EmptyCard text="Health diagnostics not available from the API." />
    );
  }

  const memory = diag.memory;
  const postgres = diag.postgres;
  const redis = diag.redis;

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
      {/* Chroma */}
      {memory && (
        <div className="card p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-[13px] font-semibold text-ink">
              ChromaDB
            </span>
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          </div>
          <div className="space-y-1.5 text-[12px]">
            <Row label="Backend" value={memory.backend} />
            <Row label="Persistent" value={memory.chroma_persistent ? "Yes" : "No"} />
            <Row
              label="Total docs"
              value={memory.chroma_total_documents?.toString() ?? "—"}
            />
            <Row
              label="Min similarity"
              value={memory.min_similarity?.toString() ?? "0.30"}
            />
            <Row
              label="Retrieval"
              value={memory.retrieval_mode === "vector" ? "Vector embeddings" : "BM25 keyword search"}
            />
            {memory.chroma_collection_counts && (
              <div className="mt-2 pt-2 border-t border-stone-100 dark:border-[var(--hairline)]">
                <span className="eyebrow text-stone-500">Collections</span>
                <div className="mt-1 space-y-0.5">
                  {Object.entries(memory.chroma_collection_counts).map(
                    ([name, count]) => (
                      <Row key={name} label={name} value={String(count)} />
                    )
                  )}
                </div>
              </div>
            )}
            {/* Retrieval mode notice — data-driven, not hardcoded */}
            {memory.embeddings_active === false && (
              <div className="mt-2 pt-2 border-t border-stone-100 dark:border-[var(--hairline)]">
                <div className="flex items-start gap-1.5 text-stone-500 dark:text-stone-400">
                  <Info className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                  <span className="text-[11px]">
                    Vector embeddings are not active. Memory retrieval uses BM25
                    keyword matching. To enable vector search, configure an
                    embedding provider (Ollama or OpenAI).
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Postgres */}
      <div className="card p-4">
        <div className="flex items-center justify-between mb-3">
          <span className="text-[13px] font-semibold text-ink">
            Postgres
          </span>
          {postgres ? (
            postgres.configured ? (
              postgres.reachable ? (
                <CheckCircle2 className="h-4 w-4 text-emerald-500" />
              ) : (
                <XCircle className="h-4 w-4 text-rose-500" />
              )
            ) : (
              <span className="text-[10px] text-ink-3 px-1.5 py-0.5 rounded bg-stone-100 dark:bg-white/5">
                Not configured
              </span>
            )
          ) : (
            <span className="text-[10px] text-ink-3 px-1.5 py-0.5 rounded bg-stone-100 dark:bg-white/5">
              Unknown
            </span>
          )}
        </div>
        <div className="space-y-1.5 text-[12px]">
          {postgres ? (
            <>
              <Row
                label="Configured"
                value={postgres.configured ? "Yes" : "No"}
              />
              <Row
                label="Reachable"
                value={
                  postgres.reachable == null
                    ? "N/A"
                    : postgres.reachable
                    ? "Yes"
                    : "No"
                }
              />
              <Row label="Purpose" value={postgres.purpose || "audit/backtest"} />
            </>
          ) : (
            <span className="text-[11px] text-ink-3">
              No Postgres info from health endpoint.
            </span>
          )}
        </div>
      </div>

      {/* Redis */}
      <div className="card p-4">
        <div className="flex items-center justify-between mb-3">
          <span className="text-[13px] font-semibold text-ink">
            Redis
          </span>
          {redis ? (
            redis.reachable ? (
              <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            ) : redis.configured ? (
              <XCircle className="h-4 w-4 text-rose-500" />
            ) : (
              <span className="text-[10px] text-ink-3 px-1.5 py-0.5 rounded bg-stone-100 dark:bg-white/5">
                Not configured
              </span>
            )
          ) : (
            <span className="text-[10px] text-ink-3 px-1.5 py-0.5 rounded bg-stone-100 dark:bg-white/5">
              Unknown
            </span>
          )}
        </div>
        <div className="space-y-1.5 text-[12px]">
          {redis ? (
            <>
              <Row
                label="Configured"
                value={redis.configured ? "Yes" : "No"}
              />
              <Row
                label="Package"
                value={redis.package_available ? "Installed" : "Missing"}
              />
              <Row
                label="Reachable"
                value={redis.reachable ? "Yes" : "No"}
              />
              <Row label="Purpose" value={redis.purpose || "WebSocket streaming"} />
            </>
          ) : (
            <span className="text-[11px] text-ink-3">
              No Redis info from health endpoint.
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-stone-500">{label}</span>
      <span className="text-ink-2 font-medium text-right">{value}</span>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   LLM Calls Panel (with empty state)
   ═══════════════════════════════════════════════════════════════════════ */

function LLMCallsSection({
  metrics,
  isLoading,
  error,
}: {
  metrics?: MetricsSummary | null;
  isLoading: boolean;
  error: unknown;
}) {
  const calls = metrics?.llm_calls;
  const total = calls
    ? Object.values(calls).reduce((a, b) => a + b, 0)
    : 0;

  return (
    <section>
      <div className="flex items-center gap-2 mb-4">
        <Zap className="h-4 w-4 text-stone-500" />
        <span className="text-[14px] font-semibold text-ink">
          LLM calls by agent
        </span>
        {total > 0 && (
          <span className="text-[12px] text-ink-3">({total} total)</span>
        )}
      </div>
      {error ? (
        <ErrorCard message="Failed to load LLM metrics." />
      ) : isLoading && !metrics ? (
        <LoadingCard />
      ) : !calls || total === 0 ? (
        <div className="card px-4 py-5 flex items-center gap-2.5 text-[12px] text-ink-3">
          <Info className="h-3.5 w-3.5 shrink-0 text-stone-400" />
          <span>
            No LLM calls recorded since server started. Run a recommendation to
            see per-agent call counts. Metrics are collected via LangChain
            callbacks and reset on server restart.
          </span>
        </div>
      ) : (
        <div className="card overflow-hidden">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-stone-200/80 dark:bg-[var(--hairline)]">
            {Object.entries(calls)
              .sort((a, b) => b[1] - a[1])
              .map(([agent, count]) => {
                const pct = total > 0 ? (count / total) * 100 : 0;
                return (
                  <div
                    key={agent}
                    className="bg-white dark:bg-[var(--paper)] p-4"
                  >
                    <div className="eyebrow text-stone-500">
                      {agent.replace(/_/g, " ")}
                    </div>
                    <div className="text-[18px] font-semibold text-ink mt-1 tabular-nums">
                      {count}
                    </div>
                    <div className="mt-2 h-1.5 rounded-full bg-stone-100 dark:bg-white/5 overflow-hidden">
                      <div
                        className="h-full rounded-full bg-stone-600 dark:bg-white/30"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                );
              })}
          </div>
        </div>
      )}
    </section>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   Data Freshness Panel
   ═══════════════════════════════════════════════════════════════════════ */

const STATUS_STYLE: Record<string, { dot: string; text: string }> = {
  Fresh: { dot: "bg-emerald-500", text: "text-emerald-700 dark:text-emerald-400" },
  "Fresh (market closed)": {
    dot: "bg-emerald-400",
    text: "text-emerald-600 dark:text-emerald-400",
  },
  Stale: { dot: "bg-amber-500", text: "text-amber-700 dark:text-amber-400" },
  Missing: { dot: "bg-rose-500", text: "text-rose-700 dark:text-rose-400" },
  Available: { dot: "bg-sky-500", text: "text-sky-700 dark:text-sky-400" },
};

function DataFreshnessPanel({ data }: { data: DataFreshnessResponse }) {
  const { market_status: market, summary, sources } = data;

  return (
    <>
      <div className="flex items-center gap-2 mb-3 -mt-4">
        <span className="text-[11px] text-ink-3 tabular-nums">
          {summary.fresh} fresh · {summary.stale} stale · {summary.missing}{" "}
          missing{summary.available ? ` · ${summary.available} available` : ""}
        </span>
      </div>

      {/* Market status banner */}
      <div className="card px-4 py-2.5 mb-3 flex items-center gap-2 text-[12px]">
        <span
          className={cn(
            "h-2 w-2 rounded-full",
            market.is_trading_hours ? "bg-emerald-500 anim-pulse-dot" : "bg-stone-400"
          )}
        />
        <span className="text-ink-2 font-medium">
          {market.is_trading_hours ? "EGX market open" : "EGX market closed"}
        </span>
        <span className="text-ink-3">
          — {market.current_time_cairo} · Last trading day:{" "}
          {market.last_trading_day}
        </span>
      </div>

      {/* Source cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        {sources.map((src) => (
          <FreshnessCard key={src.source} src={src} />
        ))}
      </div>
    </>
  );
}

function FreshnessCard({ src }: { src: DataSourceFreshness }) {
  const style = STATUS_STYLE[src.status] ?? STATUS_STYLE["Missing"];
  const sourceName: Record<string, string> = {
    "price/yfinance": "Price",
    fundamentals: "Fundamentals",
    news: "News",
    social: "Social",
    macro: "Macro",
    memory: "Memory",
  };

  const subSourceDot = (status: string) => {
    if (status.startsWith("Fresh")) return "bg-emerald-500";
    if (status === "Available") return "bg-sky-500";
    if (status === "Stale") return "bg-amber-500";
    return "bg-rose-500";
  };

  return (
    <div className="card p-4 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-[13px] font-semibold text-ink">
          {sourceName[src.source] ?? src.source}
        </span>
        <span className={cn("h-2.5 w-2.5 rounded-full", style.dot)} />
      </div>
      <div className={cn("text-[12px] font-medium", style.text)}>
        {src.status}
      </div>
      <div className="text-[11px] text-ink-3 tabular-nums">
        {src.age_human ?? "—"}
      </div>
      {src.label && (
        <div className="text-[10.5px] text-ink-3 italic">{src.label}</div>
      )}
      {/* Macro sub-sources breakdown */}
      {src.sub_sources && src.sub_sources.length > 0 && (
        <div className="space-y-1 pt-1 border-t border-stone-200/60 dark:border-[var(--hairline)]">
          {src.sub_sources.map((sub) => (
            <div key={sub.name} className="flex items-center gap-1.5 text-[10px]">
              <span className={cn("h-1.5 w-1.5 rounded-full shrink-0", subSourceDot(sub.status))} />
              <span className="text-ink-2 truncate" title={sub.note}>{sub.name}</span>
              <span className="text-ink-3 ml-auto whitespace-nowrap">{sub.age_human ?? "config"}</span>
            </div>
          ))}
        </div>
      )}
      {!src.sub_sources && src.timestamp_meaning && (
        <div className="text-[10px] text-ink-3 leading-tight" title={src.timestamp_meaning}>
          {src.timestamp_meaning}
        </div>
      )}
      {src.affected_tickers && src.affected_tickers.length > 0 && (
        <div className="text-[10.5px] text-amber-600 dark:text-amber-400 truncate">
          {src.affected_tickers.length} ticker
          {src.affected_tickers.length !== 1 ? "s" : ""} affected
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   Last Recommendation (with empty state)
   ═══════════════════════════════════════════════════════════════════════ */

function LastRunSection({
  status,
  isLoading,
}: {
  status?: SystemStatus | null;
  isLoading: boolean;
}) {
  const run = status?.last_shadow_run;

  return (
    <section>
      <div className="flex items-center gap-2 mb-4">
        <Clock className="h-4 w-4 text-stone-500" />
        <span className="text-[14px] font-semibold text-ink">
          Last recommendation
        </span>
      </div>
      {isLoading && !status ? (
        <LoadingCard />
      ) : run ? (
        <LastShadowRun run={run} />
      ) : (
        <EmptyCard text="No recommendations recorded yet. Run a full pipeline analysis from the Home page." />
      )}
    </section>
  );
}

function LastShadowRun({
  run,
}: {
  run: { id: string; ticker: string; signal: string; created_at: string };
}) {
  const sigColor =
    run.signal === "BUY"
      ? "text-emerald-700 bg-emerald-50 border-emerald-200"
      : run.signal === "SELL"
      ? "text-rose-700 bg-rose-50 border-rose-200"
      : "text-stone-700 bg-stone-50 border-stone-200";

  return (
    <div className="card p-4 flex items-center gap-4">
      <span className="text-[13px] font-medium text-ink">{run.ticker}</span>
      <span
        className={cn(
          "px-2 py-0.5 rounded-md border text-[11px] font-semibold uppercase",
          sigColor
        )}
      >
        {run.signal || "—"}
      </span>
      <span className="text-[12px] text-ink-3">
        {new Date(run.created_at).toLocaleString()}
      </span>
      <span className="text-[11px] text-ink-3 font-mono">{run.id}</span>
    </div>
  );
}
