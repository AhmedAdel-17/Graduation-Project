import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertOctagon,
  AlertTriangle,
  Bug,
  ChevronDown,
  Database,
  Info,
  Search,
} from "lucide-react";
import { AdminShell } from "../layout/AdminShell";
import { AdminCard } from "../shared/AdminCard";
import { useAdminErrors } from "../shared/useAdminData";
import { useHealth } from "../../../hooks/useDiagnostics";
import { cn } from "../../../lib/utils";
import type { AdminErrorRecord, ErrorSeverity } from "../../../services/api/adminTypes";

type Win = 7 | 30 | 90;

const SEVERITY_META: Record<string, { cls: string; icon: React.ReactNode }> = {
  critical: {
    cls: "bg-red-100 text-red-800 border-red-300 dark:bg-red-900/30 dark:text-red-200 dark:border-red-900/50",
    icon: <AlertOctagon className="h-3.5 w-3.5" aria-hidden />,
  },
  error: {
    cls: "bg-red-50 text-red-700 border-red-200 dark:bg-red-900/20 dark:text-red-300 dark:border-red-900/40",
    icon: <Bug className="h-3.5 w-3.5" aria-hidden />,
  },
  warning: {
    cls: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/20 dark:text-amber-300 dark:border-amber-900/40",
    icon: <AlertTriangle className="h-3.5 w-3.5" aria-hidden />,
  },
  info: {
    cls: "bg-blue-50 text-blue-700 border-blue-200 dark:bg-sky-900/20 dark:text-sky-300 dark:border-sky-900/40",
    icon: <Info className="h-3.5 w-3.5" aria-hidden />,
  },
};

function sevMeta(s: ErrorSeverity) {
  return SEVERITY_META[s] ?? SEVERITY_META.info;
}

export function ErrorCenterPage() {
  const [win, setWin] = useState<Win>(30);
  const { errors } = useAdminErrors(win);
  const health = useHealth(true);

  // Live system-level signals from /api/health degraded reasons.
  const systemErrors: AdminErrorRecord[] = useMemo(() => {
    const reasons = health.data?.diagnostics?.degraded_reasons ?? [];
    return reasons.map((r) => ({
      timestamp: health.data?.timestamp ?? null,
      agent: null,
      error_type: r,
      severity: "warning" as const,
      category: "system",
      message: `Subsystem degraded: ${r}`,
      suggested_resolution: "See /admin (Overview) and /api/health diagnostics.",
    }));
  }, [health.data]);

  const all = useMemo(() => {
    const merged = [...systemErrors, ...errors];
    return merged.sort((a, b) => String(b.timestamp ?? "").localeCompare(String(a.timestamp ?? "")));
  }, [systemErrors, errors]);

  const categories = useMemo(
    () => Array.from(new Set(all.map((e) => e.category))).sort(),
    [all]
  );

  const [query, setQuery] = useState("");
  const [severity, setSeverity] = useState<string>("ALL");
  const [category, setCategory] = useState<string>("ALL");

  const filtered = all.filter((e) => {
    if (severity !== "ALL" && e.severity !== severity) return false;
    if (category !== "ALL" && e.category !== category) return false;
    if (query) {
      const q = query.toLowerCase();
      const hay = [e.message, e.error_type, e.agent, e.ticker, e.category].filter(Boolean).join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  const counts = useMemo(() => {
    const c: Record<string, number> = { critical: 0, error: 0, warning: 0, info: 0 };
    for (const e of all) c[e.severity] = (c[e.severity] ?? 0) + 1;
    return c;
  }, [all]);

  const fieldCls =
    "h-9 rounded-md border border-stone-200 bg-white text-[13px] text-ink px-2.5 focus:outline-none focus:ring-2 focus:ring-blue-500/40 dark:bg-[var(--paper)] dark:border-[var(--hairline)]";

  return (
    <AdminShell
      title="Error Investigation Center"
      subtitle="Agent · LLM · API · database · timeout"
    >
      <div className="flex flex-col gap-5">
        {/* Severity summary */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {(["critical", "error", "warning", "info"] as const).map((s) => {
            const m = sevMeta(s);
            return (
              <AdminCard key={s} className="p-4">
                <div className="flex items-center justify-between">
                  <span className={cn("inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-[10.5px] font-semibold uppercase tracking-wider", m.cls)}>
                    {m.icon}
                    {s}
                  </span>
                </div>
                <div className="mt-2 text-[20px] font-semibold display-num text-ink num">{counts[s] ?? 0}</div>
              </AdminCard>
            );
          })}
        </div>

        {/* Filters */}
        <AdminCard className="p-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="relative flex-1 min-w-[200px]">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-stone-400 dark:text-[var(--ink-3)]" aria-hidden />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search errors…"
                className={cn(fieldCls, "w-full pl-8")}
              />
            </div>
            <label className="flex flex-col gap-1.5">
              <span className="text-[10.5px] uppercase tracking-wider text-stone-500 dark:text-[var(--ink-3)]">Severity</span>
              <select value={severity} onChange={(e) => setSeverity(e.target.value)} className={fieldCls}>
                <option value="ALL">All</option>
                {["critical", "error", "warning", "info"].map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[10.5px] uppercase tracking-wider text-stone-500 dark:text-[var(--ink-3)]">Category</span>
              <select value={category} onChange={(e) => setCategory(e.target.value)} className={fieldCls}>
                <option value="ALL">All</option>
                {categories.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </label>
            <div className="flex items-center gap-1.5">
              {[7, 30, 90].map((w) => (
                <button
                  key={w}
                  type="button"
                  onClick={() => setWin(w as Win)}
                  className={cn(
                    "h-9 px-2.5 rounded-md text-[12px] font-medium border transition-colors",
                    win === w
                      ? "bg-stone-900 text-white border-stone-900 dark:bg-white dark:text-stone-900 dark:border-white"
                      : "bg-white text-stone-600 border-stone-200 hover:bg-stone-100 dark:bg-[var(--paper)] dark:text-[var(--ink-2)] dark:border-[var(--hairline)]"
                  )}
                >
                  {w}d
                </button>
              ))}
            </div>
          </div>
        </AdminCard>

        {/* Error list */}
        {filtered.length === 0 ? (
          <AdminCard className="p-10 text-center">
            <p className="text-[14px] font-semibold text-ink">No errors</p>
            <p className="text-[12.5px] text-stone-500 dark:text-[var(--ink-3)] mt-1">
              No issues match the current filters in the selected window.
            </p>
          </AdminCard>
        ) : (
          <div className="flex flex-col gap-2.5">
            {filtered.map((e, i) => (
              <ErrorRow key={`${e.error_type}-${e.timestamp}-${i}`} error={e} />
            ))}
          </div>
        )}
      </div>
    </AdminShell>
  );
}

function ErrorRow({ error }: { error: AdminErrorRecord }) {
  const [open, setOpen] = useState(false);
  const m = sevMeta(error.severity);
  const when = error.timestamp
    ? new Date(error.timestamp).toLocaleString(undefined, {
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "—";
  const hasDetail = Boolean(error.suggested_resolution || error.stack_trace || error.session_id);

  return (
    <AdminCard className="overflow-hidden">
      <button
        type="button"
        onClick={() => hasDetail && setOpen((o) => !o)}
        className={cn(
          "w-full flex items-start gap-3 px-4 py-3 text-left",
          hasDetail && "hover:bg-stone-50 dark:hover:bg-white/[0.02] transition-colors"
        )}
      >
        <span className={cn("mt-0.5 inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-[10px] font-semibold uppercase tracking-wider shrink-0", m.cls)}>
          {m.icon}
          {error.severity}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-[12px] font-semibold text-ink">{error.error_type}</span>
            <span className="px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wider bg-stone-100 text-stone-500 dark:bg-white/[0.05] dark:text-[var(--ink-3)]">
              {error.category}
            </span>
            {error.agent && (
              <span className="text-[11px] text-stone-500 dark:text-[var(--ink-3)]">{error.agent}</span>
            )}
          </div>
          <p className="text-[12.5px] text-stone-600 dark:text-[var(--ink-2)] mt-1 leading-snug">
            {error.message}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-[10.5px] text-stone-400 dark:text-[var(--ink-3)] num hidden sm:inline">{when}</span>
          {hasDetail && <ChevronDown className={cn("h-4 w-4 text-stone-400 transition-transform", open && "rotate-180")} aria-hidden />}
        </div>
      </button>

      {open && hasDetail && (
        <div className="px-4 pb-3.5 pt-1 border-t border-stone-100 dark:border-[var(--hairline)] space-y-2.5">
          {(error.ticker || error.session_id) && (
            <div className="flex flex-wrap items-center gap-3 text-[11.5px] text-stone-500 dark:text-[var(--ink-3)] pt-2">
              {error.ticker && (
                <span>Ticker <span className="font-medium text-ink num">{error.ticker}</span></span>
              )}
              {error.session_id && (
                <Link
                  to={`/admin/traces?session=${encodeURIComponent(error.session_id)}`}
                  className="inline-flex items-center gap-1 text-blue-600 dark:text-sky-400 hover:underline"
                >
                  <Database className="h-3 w-3" aria-hidden /> {error.session_id.slice(0, 12)}…
                </Link>
              )}
            </div>
          )}
          {error.suggested_resolution && (
            <div>
              <div className="text-[10.5px] uppercase tracking-wider text-stone-400 dark:text-[var(--ink-3)] mb-1">
                Suggested resolution
              </div>
              <p className="text-[12.5px] text-ink leading-relaxed">{error.suggested_resolution}</p>
            </div>
          )}
          {error.stack_trace && (
            <pre className="text-[11px] font-mono bg-stone-50 dark:bg-black/20 border border-stone-200 dark:border-[var(--hairline)] rounded-md p-2.5 overflow-auto max-h-48 text-stone-600 dark:text-[var(--ink-2)]">
              {error.stack_trace}
            </pre>
          )}
        </div>
      )}
    </AdminCard>
  );
}
