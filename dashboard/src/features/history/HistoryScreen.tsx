import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Calendar,
  Clock,
  Database,
  FileSearch,
  Inbox,
  Minus,
  Search,
} from "lucide-react";
import { endpoints } from "../../services/api";
import type {
  BacktestSession,
  ResultSessionSummary,
  SessionTraceEvent,
} from "../../services/api/types";
import { useSessionTrace } from "../../hooks/useSessionTrace";
import { useBacktestDetail } from "../../hooks/useBacktest";
import { cn, formatNumber, formatPercent } from "../../lib/utils";

type Tab = "analyses" | "backtests";

export function HistoryScreen() {
  const [tab, setTab] = useState<Tab>("analyses");
  const [query, setQuery] = useState("");
  const [selectedAnalysis, setSelectedAnalysis] = useState<string | null>(null);
  const [selectedBacktest, setSelectedBacktest] = useState<string | null>(null);

  const analysesQ = useQuery({
    queryKey: ["history-results"],
    queryFn: () => endpoints.listResults(),
    enabled: tab === "analyses",
    staleTime: 30_000,
  });
  const backtestsQ = useQuery({
    queryKey: ["history-backtests"],
    queryFn: () => endpoints.listBacktests(),
    enabled: tab === "backtests",
    staleTime: 30_000,
  });

  const analyses = useMemo(() => {
    const list = analysesQ.data?.sessions ?? [];
    if (!query.trim()) return list;
    const q = query.toUpperCase();
    return list.filter(
      (s) => s.ticker.toUpperCase().includes(q) || s.session_id.includes(q)
    );
  }, [analysesQ.data, query]);

  const backtests = useMemo(() => {
    const list = backtestsQ.data?.sessions ?? [];
    if (!query.trim()) return list;
    const q = query.toUpperCase();
    return list.filter(
      (s) => s.ticker.toUpperCase().includes(q) || s.session_id.includes(q)
    );
  }, [backtestsQ.data, query]);

  const isLoading = tab === "analyses" ? analysesQ.isLoading : backtestsQ.isLoading;
  const count = tab === "analyses" ? analyses.length : backtests.length;

  return (
    <div className="space-y-10">
      {/* ─── Page header ─────────────────────────────────────────── */}
      <header>
        <div className="eyebrow mb-3">Workspace · History</div>
        <h1 className="display text-[40px] md:text-[44px] font-semibold leading-[1.05] text-ink">
          Every run, every verdict,
          <br />
          <span className="italic">searchable</span> and signed.
        </h1>
        <p className="text-[14px] text-ink-3 mt-3 max-w-xl leading-relaxed">
          Browse past analyses and backtests. Click any record to inspect the
          reasoning trace, the executed trades, and the model fingerprint.
        </p>
      </header>

      {/* ─── Tabs + search ───────────────────────────────────────── */}
      <div className="card-elevated p-2 flex items-center gap-2 flex-wrap">
        <div className="inline-flex p-1 rounded-xl bg-stone-100">
          <TabButton
            active={tab === "analyses"}
            onClick={() => {
              setTab("analyses");
              setSelectedBacktest(null);
            }}
            label="Analyses"
            count={analysesQ.data?.sessions?.length}
          />
          <TabButton
            active={tab === "backtests"}
            onClick={() => {
              setTab("backtests");
              setSelectedAnalysis(null);
            }}
            label="Backtests"
            count={backtestsQ.data?.sessions?.length}
          />
        </div>

        <div className="flex-1 min-w-[220px] relative">
          <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-stone-400" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by ticker or session id…"
            className="w-full h-12 pl-10 pr-4 rounded-xl border border-stone-200 bg-white text-[14px] focus:outline-none focus:border-stone-900 focus:ring-4 focus:ring-stone-900/5"
          />
        </div>

        <div className="text-[12px] text-stone-500 mono pr-2">
          {isLoading ? "loading…" : `${count} record${count === 1 ? "" : "s"}`}
        </div>
      </div>

      {/* ─── Split view ──────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.1fr] gap-6">
        {/* LEFT: list */}
        <div className="space-y-3">
          {isLoading && <ListSkeleton />}

          {!isLoading && tab === "analyses" && analyses.length === 0 && (
            <EmptyList kind="analyses" />
          )}
          {!isLoading && tab === "backtests" && backtests.length === 0 && (
            <EmptyList kind="backtests" />
          )}

          {!isLoading &&
            tab === "analyses" &&
            analyses.map((s) => (
              <AnalysisRow
                key={s.session_id}
                row={s}
                active={selectedAnalysis === s.session_id}
                onClick={() => setSelectedAnalysis(s.session_id)}
              />
            ))}

          {!isLoading &&
            tab === "backtests" &&
            backtests.map((s) => (
              <BacktestRow
                key={s.session_id}
                row={s}
                active={selectedBacktest === s.session_id}
                onClick={() => setSelectedBacktest(s.session_id)}
              />
            ))}
        </div>

        {/* RIGHT: detail */}
        <div className="lg:sticky lg:top-[5.5rem] self-start max-h-[calc(100vh-7rem)] overflow-auto">
          {tab === "analyses" ? (
            selectedAnalysis ? (
              <AnalysisDetail sessionId={selectedAnalysis} />
            ) : (
              <DetailEmpty
                title="Nothing selected"
                hint="Click an analysis on the left to inspect its full reasoning trace."
              />
            )
          ) : selectedBacktest ? (
            <BacktestDetailPanel sessionId={selectedBacktest} />
          ) : (
            <DetailEmpty
              title="Nothing selected"
              hint="Click a backtest on the left to inspect its trades and metrics."
            />
          )}
        </div>
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */

function TabButton({
  active,
  onClick,
  label,
  count,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count?: number;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "px-4 h-10 rounded-lg text-[13px] font-medium flex items-center gap-2 transition-all",
        active
          ? "bg-white text-ink shadow-[0_1px_2px_rgba(0,0,0,0.06)]"
          : "text-stone-500 hover:text-ink"
      )}
    >
      {label}
      {count !== undefined && (
        <span
          className={cn(
            "mono text-[11px] px-1.5 py-0.5 rounded",
            active ? "bg-stone-100 text-stone-600" : "bg-stone-200/70 text-stone-500"
          )}
        >
          {count}
        </span>
      )}
    </button>
  );
}

/* ── List rows ─────────────────────────────────────────────────────────── */

function AnalysisRow({
  row,
  active,
  onClick,
}: {
  row: ResultSessionSummary;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full text-left card overflow-hidden anim-fade-up transition-all",
        "hover:shadow-[0_4px_14px_-2px_rgba(15,15,15,0.08)] hover:-translate-y-px",
        active && "ring-2 ring-stone-900 border-stone-900"
      )}
    >
      <div className="px-5 py-4 flex items-center gap-4">
        <div className="h-10 w-10 rounded-xl bg-stone-100 flex items-center justify-center shrink-0">
          <span className="display text-[12px] font-semibold text-stone-700">
            {row.ticker.replace(".CA", "").slice(0, 4)}
          </span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2.5">
            <span className="mono text-[14px] font-semibold text-ink">
              {row.ticker}
            </span>
            <span className="text-[11.5px] text-stone-500">
              · {row.market}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-3 text-[11.5px] text-stone-500">
            <span className="flex items-center gap-1">
              <Calendar className="h-3 w-3" />
              {row.trade_date}
            </span>
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {formatRelativeTs(row.timestamp)}
            </span>
            <span className="mono truncate hidden md:inline text-stone-400">
              {short(row.session_id)}
            </span>
          </div>
        </div>
        <ArrowRight
          className={cn(
            "h-4 w-4 shrink-0 transition-colors",
            active ? "text-ink" : "text-stone-300"
          )}
        />
      </div>
    </button>
  );
}

function BacktestRow({
  row,
  active,
  onClick,
}: {
  row: BacktestSession;
  active: boolean;
  onClick: () => void;
}) {
  const ret = row.metrics?.total_return_pct;
  const profit = typeof ret === "number" ? ret >= 0 : null;
  const Arrow = profit === true ? ArrowUpRight : profit === false ? ArrowDownRight : Minus;
  const retChip =
    profit === true
      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
      : profit === false
      ? "bg-rose-50 text-rose-700 border-rose-200"
      : "bg-stone-50 text-stone-600 border-stone-200";

  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full text-left card overflow-hidden anim-fade-up transition-all",
        "hover:shadow-[0_4px_14px_-2px_rgba(15,15,15,0.08)] hover:-translate-y-px",
        active && "ring-2 ring-stone-900 border-stone-900"
      )}
    >
      <div className="px-5 py-4 flex items-center gap-4">
        <div className="h-10 w-10 rounded-xl bg-stone-100 flex items-center justify-center shrink-0">
          <span className="display text-[12px] font-semibold text-stone-700">
            {row.ticker.replace(".CA", "").slice(0, 4)}
          </span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2.5">
            <span className="mono text-[14px] font-semibold text-ink">
              {row.ticker}
            </span>
            <span className="text-[11.5px] text-stone-500">
              · {row.engine === "llm_multi_agent" ? "LLM multi-agent" : "Classical"}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-3 text-[11.5px] text-stone-500">
            <span>{row.total_trades} trades</span>
            {typeof row.metrics?.sharpe_ratio === "number" && (
              <span>Sharpe {formatNumber(row.metrics.sharpe_ratio)}</span>
            )}
            <span className="mono truncate hidden md:inline text-stone-400">
              {short(row.session_id)}
            </span>
          </div>
        </div>
        <span
          className={cn(
            "inline-flex items-center gap-1 px-2.5 py-1 rounded-full border text-[11.5px] font-semibold mono",
            retChip
          )}
        >
          <Arrow className="h-3 w-3" />
          {typeof ret === "number" ? formatPercent(ret, 2) : "—"}
        </span>
      </div>
    </button>
  );
}

/* ── Detail panels ─────────────────────────────────────────────────────── */

function AnalysisDetail({ sessionId }: { sessionId: string }) {
  const { data, isLoading } = useSessionTrace(sessionId);
  if (isLoading) return <DetailSkeleton />;
  if (!data || data.source === "none" || !data.session) {
    return (
      <DetailEmpty
        title="Trace unavailable"
        hint="No reasoning trace was recorded for this session."
      />
    );
  }
  const s = data.session;
  const decision = (s.final_decision || "").toUpperCase();
  const verdictUp = decision === "BUY" || decision === "STRONG_BUY";
  const verdictDown = decision === "SELL" || decision === "STRONG_SELL";
  const verdictTone = verdictUp
    ? "bg-emerald-50 text-emerald-800 border-emerald-200"
    : verdictDown
    ? "bg-rose-50 text-rose-800 border-rose-200"
    : "bg-stone-100 text-stone-700 border-stone-200";
  const accentBar = verdictUp
    ? "from-emerald-500/0 via-emerald-500 to-teal-500"
    : verdictDown
    ? "from-rose-500/0 via-rose-500 to-orange-500"
    : "from-indigo-500/0 via-indigo-500 to-violet-500";
  const halo = verdictUp
    ? "bg-emerald-300"
    : verdictDown
    ? "bg-rose-300"
    : "bg-indigo-200";

  const grouped = groupEvents(data.events);

  return (
    <div className="space-y-5 anim-fade-up">
      {/* Hero strip */}
      <div className="relative overflow-hidden card-elevated grain">
        <div className={cn("absolute -top-24 -right-24 h-[280px] w-[280px] rounded-full blur-3xl opacity-30 pointer-events-none", halo)} />
        <div className={cn("relative h-[3px] w-full bg-gradient-to-r", accentBar)} />
        <div className="relative p-7">
          <div className="flex items-center gap-2 text-[10.5px] tracking-[0.18em] uppercase text-stone-500">
            <FileSearch className="h-3 w-3" /> Analysis trace · {data.source}
          </div>
          <div className="mt-4 flex items-baseline gap-3 flex-wrap">
            <div className="display text-[28px] font-semibold tracking-tight text-ink">
              {s.ticker || "—"}
            </div>
            <div className="mono text-[12px] text-stone-500">
              {s.trade_date || ""}
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <span
              className={cn(
                "inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[11.5px] font-semibold uppercase tracking-wider border",
                verdictTone
              )}
            >
              {decision || "—"}
            </span>
            {typeof s.confidence_overall === "number" && (
              <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[11.5px] font-medium text-ink-2 border border-stone-200 bg-white">
                {formatNumber(s.confidence_overall * 100, 1)}% confidence
              </span>
            )}
            {s.risk_veto && (
              <span className="inline-flex items-center px-3 py-1 rounded-full text-[11.5px] font-medium text-rose-700 border border-rose-200 bg-rose-50">
                Risk veto
              </span>
            )}
          </div>
          <div className="mt-6 mono text-[11px] text-stone-500 truncate">
            session · {s.session_id}
          </div>
        </div>
      </div>

      {/* Agent events */}
      {grouped.length === 0 ? (
        <div className="card p-6 text-center text-sm text-stone-500">
          No agent events recorded for this session.
        </div>
      ) : (
        <div className="space-y-3">
          {grouped.map((g) => (
            <div key={g.agent} className="card overflow-hidden">
              <div className="px-5 py-3 border-b border-stone-100 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-stone-900" />
                  <div className="display text-[14px] font-semibold text-ink">
                    {prettyAgent(g.agent)}
                  </div>
                </div>
                <span className="mono text-[11px] text-stone-500">
                  {g.events.length} event{g.events.length === 1 ? "" : "s"}
                </span>
              </div>
              <div className="divide-y divide-stone-100">
                {g.events.map((e, i) => (
                  <div key={i} className="px-5 py-3">
                    <div className="flex items-center justify-between text-[11px] text-stone-500">
                      <span className="mono">{e.event_type || "event"}</span>
                      {typeof e.confidence_score === "number" && (
                        <span>conf {formatNumber(e.confidence_score * 100, 0)}%</span>
                      )}
                    </div>
                    {e.opinion_summary && (
                      <div className="mt-1.5 text-[13.5px] text-ink-2 leading-relaxed whitespace-pre-wrap">
                        {e.opinion_summary}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function BacktestDetailPanel({ sessionId }: { sessionId: string }) {
  const { data, isLoading } = useBacktestDetail(sessionId);
  if (isLoading) return <DetailSkeleton />;
  if (!data) {
    return (
      <DetailEmpty
        title="Backtest not found"
        hint="The report file may have been moved or deleted."
      />
    );
  }
  const m = data.metrics ?? {};
  const ret = m.total_return_pct as number | undefined;
  const profit = typeof ret === "number" ? ret >= 0 : null;
  const halo =
    profit === true ? "bg-emerald-300" : profit === false ? "bg-rose-300" : "bg-stone-200";
  const accentBar =
    profit === true
      ? "from-emerald-500/0 via-emerald-500 to-teal-500"
      : profit === false
      ? "from-rose-500/0 via-rose-500 to-orange-500"
      : "from-stone-400/0 via-stone-500 to-stone-400";
  const Arrow = profit === true ? ArrowUpRight : profit === false ? ArrowDownRight : Minus;
  const retClass =
    profit === true ? "text-emerald-700" : profit === false ? "text-rose-700" : "text-ink";

  return (
    <div className="space-y-5 anim-fade-up">
      <div className="relative overflow-hidden card-elevated grain">
        <div className={cn("absolute -top-24 -right-24 h-[280px] w-[280px] rounded-full blur-3xl opacity-30 pointer-events-none", halo)} />
        <div className={cn("relative h-[3px] w-full bg-gradient-to-r", accentBar)} />
        <div className="relative p-7">
          <div className="flex items-center gap-2 text-[10.5px] tracking-[0.18em] uppercase text-stone-500">
            <Database className="h-3 w-3" /> Backtest replay
          </div>
          <div className="mt-4 flex items-baseline gap-3 flex-wrap">
            <div className="display text-[28px] font-semibold tracking-tight text-ink">
              {data.ticker || "—"}
            </div>
            <div className="mono text-[12px] text-stone-500">
              {data.start_date || ""} → {data.end_date || ""}
            </div>
          </div>
          <div className="mt-5 flex items-end gap-4">
            <Arrow className={cn("h-5 w-5", retClass)} />
            <div>
              <div className="eyebrow text-stone-500">Total return</div>
              <div className={cn("display-num text-[40px] leading-none font-semibold mt-1", retClass)}>
                {typeof ret === "number" ? formatPercent(ret) : "—"}
              </div>
            </div>
          </div>
          <div className="mt-6 grid grid-cols-2 sm:grid-cols-3 gap-px bg-stone-200 rounded-2xl overflow-hidden border border-stone-200">
            <MiniTile label="Sharpe" value={typeof m.sharpe_ratio === "number" ? formatNumber(m.sharpe_ratio) : "—"} />
            <MiniTile label="Max DD" value={typeof m.max_drawdown_pct === "number" ? formatPercent(m.max_drawdown_pct, 2, false) : "—"} valueClass="text-rose-700" />
            <MiniTile label="Win rate" value={typeof m.win_rate === "number" ? formatPercent(m.win_rate, 1, false) : "—"} valueClass="text-emerald-700" />
            <MiniTile label="Trades" value={typeof m.total_trades === "number" ? String(Math.round(m.total_trades)) : "—"} />
            <MiniTile label="Final equity" value={typeof m.final_equity === "number" ? formatNumber(m.final_equity) : "—"} />
            <MiniTile label="Initial" value={typeof m.initial_capital === "number" ? formatNumber(m.initial_capital) : "—"} />
          </div>
          <div className="mt-5 mono text-[11px] text-stone-500 truncate">
            session · {data.session_id}
          </div>
        </div>
      </div>
    </div>
  );
}

function MiniTile({ label, value, valueClass }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="bg-white p-4">
      <div className="eyebrow text-stone-500">{label}</div>
      <div className={cn("mono text-[15px] font-semibold mt-1 text-ink", valueClass)}>
        {value}
      </div>
    </div>
  );
}

/* ── Generic helpers ───────────────────────────────────────────────────── */

function DetailEmpty({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="card overflow-hidden grain">
      <div className="px-8 py-14 text-center">
        <div className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-stone-100 text-stone-500 mb-4">
          <Inbox className="h-5 w-5" />
        </div>
        <h3 className="display text-[18px] font-semibold text-ink">{title}</h3>
        <p className="text-[13px] text-ink-3 mt-1.5 max-w-sm mx-auto leading-relaxed">
          {hint}
        </p>
      </div>
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="space-y-4">
      <div className="rounded-3xl skeleton h-[240px] border border-stone-200" />
      <div className="rounded-2xl skeleton h-32 border border-stone-200" />
    </div>
  );
}

function ListSkeleton() {
  return (
    <>
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="rounded-2xl skeleton h-[78px] border border-stone-200" />
      ))}
    </>
  );
}

function EmptyList({ kind }: { kind: "analyses" | "backtests" }) {
  return (
    <div className="card overflow-hidden grain">
      <div className="px-8 py-12 text-center">
        <div className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-stone-100 text-stone-500 mb-4">
          <Inbox className="h-5 w-5" />
        </div>
        <h3 className="display text-[18px] font-semibold text-ink">
          No {kind} yet
        </h3>
        <p className="text-[13px] text-ink-3 mt-1.5 max-w-sm mx-auto leading-relaxed">
          Once you {kind === "analyses" ? "run an analysis" : "run a backtest"},
          it will appear here for review.
        </p>
      </div>
    </div>
  );
}

/* ── Pure utils ────────────────────────────────────────────────────────── */

function groupEvents(events: SessionTraceEvent[]) {
  const byAgent = new Map<string, SessionTraceEvent[]>();
  for (const e of events) {
    const k = e.agent_name || "Unknown";
    if (!byAgent.has(k)) byAgent.set(k, []);
    byAgent.get(k)!.push(e);
  }
  return Array.from(byAgent.entries()).map(([agent, evs]) => ({
    agent,
    events: evs,
  }));
}

function prettyAgent(name: string) {
  return name
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function short(id: string) {
  if (!id) return "";
  if (id.length <= 16) return id;
  return id.slice(0, 8) + "…" + id.slice(-6);
}

function formatRelativeTs(ts: string) {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
  const diff = Date.now() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return d.toLocaleDateString();
}
