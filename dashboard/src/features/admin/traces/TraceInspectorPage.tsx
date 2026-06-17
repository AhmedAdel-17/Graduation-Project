import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  Brain,
  ChevronsDownUp,
  ChevronsUpDown,
  Database,
  ListTree,
  Search,
} from "lucide-react";
import { AdminShell } from "../layout/AdminShell";
import { AdminCard } from "../shared/AdminCard";
import { AdminEmpty, AdminError, AdminLoading } from "../shared/AdminState";
import { TraceEventCard } from "./TraceEventCard";
import { ThinkingTimeline } from "./ThinkingTimeline";
import { useResultsIndex, useSessionTrace } from "../../../hooks/useSessionTrace";
import { cn } from "../../../lib/utils";
import type { SessionTraceEvent } from "../../../services/api/types";

type Tab = "trace" | "thinking";

function matchesQuery(ev: SessionTraceEvent, q: string): boolean {
  if (!q) return true;
  const hay = [
    ev.agent_name,
    ev.opinion_summary,
    ev.opinion_type,
    ev.structured_output ? JSON.stringify(ev.structured_output) : "",
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return hay.includes(q.toLowerCase());
}

export function TraceInspectorPage() {
  const [params, setParams] = useSearchParams();
  const index = useResultsIndex();
  const sessions = useMemo(() => index.data?.sessions ?? [], [index.data]);

  const urlSession = params.get("session");
  const selectedId = urlSession ?? sessions[0]?.session_id ?? null;

  // Keep the URL in sync once a default is resolved (so refresh/share works).
  useEffect(() => {
    if (!urlSession && sessions[0]?.session_id) {
      setParams({ session: sessions[0].session_id }, { replace: true });
    }
  }, [urlSession, sessions, setParams]);

  const trace = useSessionTrace(selectedId);
  const session = trace.data?.session ?? null;
  const events = useMemo(() => trace.data?.events ?? [], [trace.data]);
  const source = trace.data?.source ?? null;

  const [tab, setTab] = useState<Tab>("trace");
  const [query, setQuery] = useState("");
  const [expandAll, setExpandAll] = useState(false);

  const filtered = useMemo(
    () => events.filter((e) => matchesQuery(e, query)),
    [events, query]
  );

  const sessionFp =
    (session?.model_fingerprint as Record<string, unknown> | null) ?? null;

  return (
    <AdminShell
      title="LLM Trace Inspector"
      subtitle="Per-agent request / response traces"
      actions={
        source === "postgres" ? (
          <span className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[11px] bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-900/20 dark:text-emerald-300 dark:border-emerald-900/40">
            <Database className="h-3 w-3" aria-hidden /> postgres
          </span>
        ) : source === "jsonl" ? (
          <span className="hidden sm:inline-flex items-center px-2.5 py-1 rounded-full border text-[11px] bg-white text-stone-500 border-stone-200 dark:bg-[var(--paper)] dark:text-[var(--ink-3)] dark:border-[var(--hairline)]">
            jsonl
          </span>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-5">
        {/* Session picker + summary */}
        <AdminCard className="p-4">
          <div className="flex flex-col lg:flex-row lg:items-center gap-3">
            <label className="flex flex-col gap-1.5 flex-1 min-w-0">
              <span className="text-[10.5px] uppercase tracking-wider text-stone-500 dark:text-[var(--ink-3)]">
                Session
              </span>
              <select
                value={selectedId ?? ""}
                onChange={(e) => setParams({ session: e.target.value })}
                disabled={sessions.length === 0}
                className="h-9 rounded-md border border-stone-200 bg-white text-[13px] text-ink px-2.5 focus:outline-none focus:ring-2 focus:ring-blue-500/40 dark:bg-[var(--paper)] dark:border-[var(--hairline)] disabled:opacity-50"
              >
                {sessions.length === 0 && <option value="">No sessions</option>}
                {sessions.map((s) => (
                  <option key={s.session_id} value={s.session_id}>
                    {s.ticker} · {s.trade_date} · {s.session_id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </label>
            {session && (
              <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[12px]">
                <span className="text-stone-500 dark:text-[var(--ink-3)]">
                  Decision{" "}
                  <span className="font-semibold text-ink">
                    {session.final_decision ?? "—"}
                  </span>
                </span>
                {typeof session.confidence_overall === "number" && (
                  <span className="text-stone-500 dark:text-[var(--ink-3)]">
                    Confidence{" "}
                    <span className="font-semibold text-ink num">
                      {Math.round(session.confidence_overall * 100)}%
                    </span>
                  </span>
                )}
                {session.risk_veto && (
                  <span className="inline-flex items-center gap-1 text-red-600 dark:text-red-400 font-medium">
                    <AlertTriangle className="h-3 w-3" aria-hidden /> risk veto
                  </span>
                )}
                <span className="text-stone-400 dark:text-[var(--ink-3)] num">
                  {events.length} events
                </span>
              </div>
            )}
          </div>
        </AdminCard>

        {/* Tabs */}
        <div className="flex items-center gap-1 border-b border-stone-200 dark:border-[var(--hairline)]">
          <TabButton active={tab === "trace"} onClick={() => setTab("trace")} icon={<ListTree className="h-4 w-4" />}>
            Trace
          </TabButton>
          <TabButton active={tab === "thinking"} onClick={() => setTab("thinking")} icon={<Brain className="h-4 w-4" />}>
            Agent Thinking
          </TabButton>
        </div>

        {/* Error / loading / empty */}
        {index.isError || trace.isError ? (
          <AdminError
            title="Couldn’t load traces"
            onRetry={() => {
              index.refetch();
              trace.refetch();
            }}
          />
        ) : trace.isLoading ? (
          <AdminLoading label="Loading trace…" />
        ) : !selectedId || sessions.length === 0 ? (
          <AdminEmpty
            title="No sessions yet"
            description="Run an analysis (Live Execution or the main dashboard) to populate the audit trail."
          />
        ) : events.length === 0 ? (
          <AdminEmpty
            title="No agent events"
            description="This session has no recorded agent events."
          />
        ) : tab === "trace" ? (
          <>
            {/* Search + expand controls */}
            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-stone-400 dark:text-[var(--ink-3)]" aria-hidden />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search agents, summaries, structured output…"
                  className="w-full h-9 rounded-md border border-stone-200 bg-white text-[13px] text-ink pl-8 pr-3 focus:outline-none focus:ring-2 focus:ring-blue-500/40 dark:bg-[var(--paper)] dark:border-[var(--hairline)]"
                />
              </div>
              <button
                type="button"
                onClick={() => setExpandAll((v) => !v)}
                className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md text-[12px] font-medium border border-stone-200 text-stone-600 hover:bg-stone-100 dark:border-[var(--hairline)] dark:text-[var(--ink-2)] dark:hover:bg-white/5"
              >
                {expandAll ? <ChevronsDownUp className="h-3.5 w-3.5" aria-hidden /> : <ChevronsUpDown className="h-3.5 w-3.5" aria-hidden />}
                {expandAll ? "Collapse all" : "Expand all"}
              </button>
            </div>

            <div className="flex flex-col gap-2.5">
              {filtered.length === 0 ? (
                <AdminCard className="p-8 text-center text-[13px] text-stone-400 dark:text-[var(--ink-3)]">
                  No events match “{query}”.
                </AdminCard>
              ) : (
                filtered.map((ev, i) => (
                  <TraceEventCard
                    key={`${selectedId}-${i}-${expandAll}`}
                    event={ev}
                    index={i}
                    sessionFingerprint={sessionFp}
                    defaultOpen={expandAll || i === 0}
                  />
                ))
              )}
            </div>
          </>
        ) : (
          <AdminCard className="p-5">
            <ThinkingTimeline events={events} />
          </AdminCard>
        )}
      </div>
    </AdminShell>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 px-3 py-2 text-[13px] font-medium border-b-2 -mb-px transition-colors",
        active
          ? "border-stone-900 dark:border-white text-ink"
          : "border-transparent text-stone-500 dark:text-[var(--ink-3)] hover:text-ink"
      )}
    >
      {icon}
      {children}
    </button>
  );
}
