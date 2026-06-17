import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ChevronLeft, ChevronRight, Loader2, Search, X } from "lucide-react";
import { AdminShell } from "../layout/AdminShell";
import { AdminCard } from "../shared/AdminCard";
import { AdminEmpty, AdminError, AdminLoading } from "../shared/AdminState";
import { SignalBadge } from "../shared/SignalBadge";
import type { SignalKind } from "../shared/signal";
import { useResultsIndex } from "../../../hooks/useSessionTrace";
import { useRunRows, type RunStatus } from "./useRunRows";
import { formatDuration } from "../live/executionModel";
import { cn } from "../../../lib/utils";

const PAGE_SIZE = 20;
const REC_OPTIONS: (SignalKind | "ALL")[] = ["ALL", "BUY", "SELL", "HOLD"];
const STATUS_OPTIONS: (RunStatus | "ALL")[] = ["ALL", "completed", "veto", "no_data"];

const STATUS_LABEL: Record<RunStatus, string> = {
  completed: "Completed",
  veto: "Risk veto",
  no_data: "No data",
  loading: "Loading",
};
const STATUS_CLASS: Record<RunStatus, string> = {
  completed: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-900/20 dark:text-emerald-300 dark:border-emerald-900/40",
  veto: "bg-red-50 text-red-700 border-red-200 dark:bg-red-900/20 dark:text-red-300 dark:border-red-900/40",
  no_data: "bg-stone-100 text-stone-500 border-stone-200 dark:bg-white/[0.04] dark:text-[var(--ink-3)] dark:border-[var(--hairline)]",
  loading: "bg-blue-50 text-blue-600 border-blue-200 dark:bg-sky-900/20 dark:text-sky-300 dark:border-sky-900/40",
};

const fieldCls =
  "h-9 rounded-md border border-stone-200 bg-white text-[13px] text-ink px-2.5 focus:outline-none focus:ring-2 focus:ring-blue-500/40 dark:bg-[var(--paper)] dark:border-[var(--hairline)]";

export function RunExplorerPage() {
  const navigate = useNavigate();
  const index = useResultsIndex();
  const allSessions = useMemo(() => index.data?.sessions ?? [], [index.data]);

  const [ticker, setTicker] = useState("ALL");
  const [rec, setRec] = useState<SignalKind | "ALL">("ALL");
  const [status, setStatus] = useState<RunStatus | "ALL">("ALL");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);

  const tickers = useMemo(
    () => Array.from(new Set(allSessions.map((s) => s.ticker))).sort(),
    [allSessions]
  );

  // Cheap filters (run on the index, no enrichment needed).
  const cheapFiltered = useMemo(() => {
    return allSessions.filter((s) => {
      if (ticker !== "ALL" && s.ticker !== ticker) return false;
      if (from && s.trade_date < from) return false;
      if (to && s.trade_date > to) return false;
      if (search) {
        const q = search.toLowerCase();
        if (
          !s.session_id.toLowerCase().includes(q) &&
          !s.ticker.toLowerCase().includes(q)
        )
          return false;
      }
      return true;
    });
  }, [allSessions, ticker, from, to, search]);

  const pageCount = Math.max(1, Math.ceil(cheapFiltered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageSessions = cheapFiltered.slice(
    safePage * PAGE_SIZE,
    safePage * PAGE_SIZE + PAGE_SIZE
  );

  // Enrich only the current page (one trace query each, cache-shared).
  const rows = useRunRows(pageSessions);

  // Expensive filters (recommendation/status) refine the loaded page.
  const visibleRows = rows.filter((r) => {
    if (rec !== "ALL" && r.recommendation !== rec) return false;
    if (status !== "ALL" && r.status !== status) return false;
    return true;
  });

  const anyExpensiveFilter = rec !== "ALL" || status !== "ALL";
  const resetFilters = () => {
    setTicker("ALL");
    setRec("ALL");
    setStatus("ALL");
    setFrom("");
    setTo("");
    setSearch("");
    setPage(0);
  };
  const hasFilters =
    ticker !== "ALL" || rec !== "ALL" || status !== "ALL" || from || to || search;

  return (
    <AdminShell title="Run Explorer" subtitle="All historical analysis runs">
      <div className="flex flex-col gap-5">
        {/* Filters */}
        <AdminCard className="p-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="relative flex-1 min-w-[180px]">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-stone-400 dark:text-[var(--ink-3)]" aria-hidden />
              <input
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  setPage(0);
                }}
                placeholder="Search run id or ticker…"
                className={cn(fieldCls, "w-full pl-8")}
              />
            </div>
            <Field label="Ticker">
              <select value={ticker} onChange={(e) => { setTicker(e.target.value); setPage(0); }} className={fieldCls}>
                <option value="ALL">All</option>
                {tickers.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </Field>
            <Field label="Recommendation">
              <select value={rec} onChange={(e) => setRec(e.target.value as SignalKind | "ALL")} className={fieldCls}>
                {REC_OPTIONS.map((r) => (
                  <option key={r} value={r}>{r === "ALL" ? "All" : r}</option>
                ))}
              </select>
            </Field>
            <Field label="Status">
              <select value={status} onChange={(e) => setStatus(e.target.value as RunStatus | "ALL")} className={fieldCls}>
                {STATUS_OPTIONS.map((s) => (
                  <option key={s} value={s}>{s === "ALL" ? "All" : STATUS_LABEL[s as RunStatus]}</option>
                ))}
              </select>
            </Field>
            <Field label="From">
              <input type="date" value={from} onChange={(e) => { setFrom(e.target.value); setPage(0); }} className={cn(fieldCls, "num")} />
            </Field>
            <Field label="To">
              <input type="date" value={to} onChange={(e) => { setTo(e.target.value); setPage(0); }} className={cn(fieldCls, "num")} />
            </Field>
            {hasFilters && (
              <button
                type="button"
                onClick={resetFilters}
                className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md text-[12px] font-medium border border-stone-200 text-stone-600 hover:bg-stone-100 dark:border-[var(--hairline)] dark:text-[var(--ink-2)] dark:hover:bg-white/5"
              >
                <X className="h-3.5 w-3.5" aria-hidden /> Clear
              </button>
            )}
          </div>
          {anyExpensiveFilter && (
            <p className="mt-2 text-[11px] text-stone-400 dark:text-[var(--ink-3)]">
              Recommendation / status filters apply to the loaded page (rows are enriched per page).
            </p>
          )}
        </AdminCard>

        {/* Table */}
        {index.isError ? (
          <AdminError title="Couldn’t load runs" onRetry={() => index.refetch()} />
        ) : index.isLoading ? (
          <AdminLoading label="Loading runs…" />
        ) : allSessions.length === 0 ? (
          <AdminEmpty
            title="No runs recorded"
            description="Run an analysis to populate the audit history."
          />
        ) : (
          <AdminCard className="overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-stone-200 dark:border-[var(--hairline)] text-[10.5px] uppercase tracking-wider text-stone-400 dark:text-[var(--ink-3)]">
                    <Th>Run ID</Th>
                    <Th>Date</Th>
                    <Th>Ticker</Th>
                    <Th>Recommendation</Th>
                    <Th className="text-right">Confidence</Th>
                    <Th className="text-right">Duration</Th>
                    <Th>Status</Th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.map((r) => (
                    <tr
                      key={r.sessionId}
                      onClick={() => navigate(`/admin/traces?session=${encodeURIComponent(r.sessionId)}`)}
                      className="border-b border-stone-100 dark:border-[var(--hairline)] hover:bg-stone-50 dark:hover:bg-white/[0.02] cursor-pointer transition-colors"
                    >
                      <Td className="font-mono text-[11.5px] text-stone-500 dark:text-[var(--ink-3)]">
                        <Link
                          to={`/admin/traces?session=${encodeURIComponent(r.sessionId)}`}
                          onClick={(e) => e.stopPropagation()}
                          className="rounded hover:text-ink hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40"
                        >
                          {r.sessionId.slice(0, 12)}…
                        </Link>
                      </Td>
                      <Td className="num">{r.tradeDate}</Td>
                      <Td className="font-medium num">{r.ticker}</Td>
                      <Td>
                        {r.status === "loading" ? (
                          <span className="text-stone-300 dark:text-stone-600">—</span>
                        ) : (
                          <SignalBadge signal={r.recommendation} />
                        )}
                      </Td>
                      <Td className="text-right num">
                        {r.confidence !== null ? `${Math.round(r.confidence * 100)}%` : "—"}
                      </Td>
                      <Td className="text-right num">{formatDuration(r.durationMs)}</Td>
                      <Td>
                        <span
                          className={cn(
                            "inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[10.5px] font-semibold uppercase tracking-wider",
                            STATUS_CLASS[r.status]
                          )}
                        >
                          {r.status === "loading" && <Loader2 className="h-3 w-3 animate-spin" aria-hidden />}
                          {STATUS_LABEL[r.status]}
                        </span>
                      </Td>
                    </tr>
                  ))}
                  {visibleRows.length === 0 && (
                    <tr>
                      <td colSpan={7} className="p-8 text-center text-[13px] text-stone-400 dark:text-[var(--ink-3)]">
                        No runs match the current filters on this page.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
          {cheapFiltered.length > PAGE_SIZE && (
            <div className="flex items-center justify-between gap-3 px-4 py-3 border-t border-stone-200 dark:border-[var(--hairline)]">
              <span className="text-[11.5px] text-stone-500 dark:text-[var(--ink-3)] num">
                {safePage * PAGE_SIZE + 1}–{Math.min((safePage + 1) * PAGE_SIZE, cheapFiltered.length)} of {cheapFiltered.length}
              </span>
              <div className="flex items-center gap-1.5">
                <PageBtn label="Previous page" disabled={safePage === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>
                  <ChevronLeft className="h-4 w-4" aria-hidden />
                </PageBtn>
                <span className="text-[12px] num text-stone-500 dark:text-[var(--ink-3)]">
                  {safePage + 1} / {pageCount}
                </span>
                <PageBtn label="Next page" disabled={safePage >= pageCount - 1} onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}>
                  <ChevronRight className="h-4 w-4" aria-hidden />
                </PageBtn>
              </div>
            </div>
          )}
          </AdminCard>
        )}
      </div>
    </AdminShell>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[10.5px] uppercase tracking-wider text-stone-500 dark:text-[var(--ink-3)]">{label}</span>
      {children}
    </label>
  );
}

function Th({ children, className }: { children: React.ReactNode; className?: string }) {
  return <th scope="col" className={cn("px-4 py-2.5 font-semibold", className)}>{children}</th>;
}
function Td({ children, className }: { children: React.ReactNode; className?: string }) {
  return <td className={cn("px-4 py-3 text-[12.5px] text-ink", className)}>{children}</td>;
}
function PageBtn({
  children,
  disabled,
  onClick,
  label,
}: {
  children: React.ReactNode;
  disabled?: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      className="h-7 w-7 inline-flex items-center justify-center rounded-md border border-stone-200 text-stone-600 hover:bg-stone-100 disabled:opacity-40 disabled:cursor-not-allowed dark:border-[var(--hairline)] dark:text-[var(--ink-2)] dark:hover:bg-white/5"
    >
      {children}
    </button>
  );
}
