import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowDownRight,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Calendar,
  Check,
  Clock,
  Database,
  FileSearch,
  Inbox,
  Minus,
  Search,
  Target,
  X,
} from "lucide-react";
import { endpoints } from "../../services/api";
import type {
  BacktestDetail,
  BacktestPrediction,
  BacktestSession,
  DecisionQualityHorizon,
  ResultSessionSummary,
  ScenarioComparison,
  SessionTraceEvent,
} from "../../services/api/types";
import { useSessionTrace } from "../../hooks/useSessionTrace";
import { useBacktestDetail } from "../../hooks/useBacktest";
import { EquityCurve } from "../../components/charts";
import { cn, formatNumber, formatPercent } from "../../lib/utils";
import { useT, tEnum } from "../../lib/i18n";
import { TranslatedMarkdown } from "../../components/ui/TranslatedMarkdown";
import { TickerLogo } from "../../components/ui/TickerLogo";
import { getTickerMeta } from "../../data/egxTickerMeta";

type Tab = "analyses" | "backtests";

export function HistoryScreen() {
  const t = useT();
  const [tab, setTab] = useState<Tab>("analyses");
  const [query, setQuery] = useState("");
  const [selectedAnalysis, setSelectedAnalysis] = useState<string | null>(null);
  const [selectedBacktest, setSelectedBacktest] = useState<string | null>(null);
  const navigate = useNavigate();

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
        <div className="eyebrow mb-3">{t("history.eyebrow")}</div>
        <h1 className="display text-[40px] md:text-[44px] font-semibold leading-[1.05] text-ink">
          {t("history.pageTitle")}
        </h1>
        <p className="text-[14px] text-ink-3 mt-3 max-w-xl leading-relaxed">
          {t("history.desc")}
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
            label={t("history.tab.analyses")}
            count={analysesQ.data?.sessions?.length}
          />
          <TabButton
            active={tab === "backtests"}
            onClick={() => {
              setTab("backtests");
              setSelectedAnalysis(null);
            }}
            label={t("history.tab.backtests")}
            count={backtestsQ.data?.sessions?.length}
          />
        </div>

        <div className="flex-1 min-w-[220px] relative">
          <Search className="absolute start-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-stone-400" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("history.search")}
            className="w-full h-12 ps-10 pe-4 rounded-xl border border-stone-200 bg-white text-[14px] focus:outline-none focus:border-stone-900 focus:ring-4 focus:ring-stone-900/5"
          />
        </div>

        <div className="text-[12px] text-stone-500 mono pe-2">
          {isLoading ? t("history.loading") : t(count === 1 ? "history.record" : "history.records", { count })}
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
                onClick={() => navigate(`/prediction/${s.session_id}`)}
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
                title={t("history.none.title")}
                hint={t("history.none.hintAnalyses")}
              />
            )
          ) : selectedBacktest ? (
            <BacktestDetailPanel sessionId={selectedBacktest} />
          ) : (
            <DetailEmpty
              title={t("history.none.title")}
              hint={t("history.none.hintBacktests")}
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
  const t = useT();
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
        <TickerLogo ticker={row.ticker} size="md" />
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 min-w-0">
            <span className="mono text-[14px] font-semibold text-ink shrink-0">
              {getTickerMeta(row.ticker).symbol}
            </span>
            <span className="text-[12.5px] text-stone-600 dark:text-[var(--ink-2)] truncate">
              {getTickerMeta(row.ticker).nameEn}
            </span>
            <span className={cn(
              "text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded border shrink-0",
              row.run_type === "backtest"
                ? "bg-amber-50 text-amber-700 border-amber-100"
                : "bg-indigo-50 text-indigo-600 border-indigo-100"
            )}>
              {row.run_type === "backtest" ? t("history.badge.backtest") : t("history.badge.live")}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-3 text-[11.5px] text-stone-500">
            <span className="flex items-center gap-1">
              <Calendar className="h-3 w-3" />
              {row.trade_date}
            </span>
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {formatRelativeTs(row.timestamp, t)}
            </span>
            <span className="mono truncate hidden md:inline text-stone-400">
              {short(row.session_id)}
            </span>
          </div>
        </div>
        {row.final_decision ? (
          <span
            className={cn(
              "inline-flex items-center px-2.5 py-1 rounded-full border text-[11px] font-semibold uppercase tracking-wider mono shrink-0",
              verdictChip(row.final_decision)
            )}
          >
            {tEnum(t, "signal", row.final_decision)}
          </span>
        ) : (
          <ArrowRight
            className={cn(
              "h-4 w-4 shrink-0 transition-colors",
              active ? "text-ink" : "text-stone-300"
            )}
          />
        )}
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
  const t = useT();
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
        <TickerLogo ticker={row.ticker} size="md" />
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 min-w-0">
            <span className="mono text-[14px] font-semibold text-ink shrink-0">
              {getTickerMeta(row.ticker).symbol}
            </span>
            <span className="text-[12.5px] text-stone-600 dark:text-[var(--ink-2)] truncate">
              {getTickerMeta(row.ticker).nameEn}
            </span>
            <span className="text-[11.5px] text-stone-500 shrink-0">
              · {row.engine === "llm_multi_agent" ? t("history.engine.llm") : t("history.engine.classical")}
            </span>
            <span className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-100">
              {t("history.badge.backtest")}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-3 text-[11.5px] text-stone-500">
            {row.start_date && row.end_date && (
              <span className="flex items-center gap-1">
                <Calendar className="h-3 w-3" />
                {row.start_date} → {row.end_date}
              </span>
            )}
            {row.ran_at && (
              <span className="flex items-center gap-1">
                <Clock className="h-3 w-3" />
                {t("history.ran", { time: formatRelativeTs(row.ran_at, t) })}
              </span>
            )}
            <span>{t("history.trades", { count: row.total_trades })}</span>
            {typeof row.metrics?.sharpe_ratio === "number" && (
              <span>{t("history.sharpeLabel")} {formatNumber(row.metrics.sharpe_ratio)}</span>
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
  const t = useT();
  const { data, isLoading } = useSessionTrace(sessionId);
  if (isLoading) return <DetailSkeleton />;
  if (!data || data.source === "none" || !data.session) {
    return (
      <DetailEmpty
        title={t("history.trace.unavailable")}
        hint={t("history.trace.hint")}
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
        <div className={cn("absolute -top-24 -end-24 h-[280px] w-[280px] rounded-full blur-3xl opacity-30 pointer-events-none", halo)} />
        <div className={cn("relative h-[3px] w-full bg-gradient-to-r", accentBar)} />
        <div className="relative p-7">
          <div className="flex items-center gap-2 text-[10.5px] tracking-[0.18em] uppercase text-stone-500">
            <FileSearch className="h-3 w-3" /> {t("history.analysisTrace", { source: data.source ?? "" })}
          </div>
          <div className="mt-4 flex items-baseline gap-3 flex-wrap">
            <div className="display text-[28px] font-semibold tracking-tight text-ink">
              {s.ticker ? getTickerMeta(s.ticker).symbol : "—"}
            </div>
            {s.ticker && (
              <div className="text-[14px] text-stone-500">
                {getTickerMeta(s.ticker).nameEn}
              </div>
            )}
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
              {decision ? tEnum(t, "signal", decision) : "—"}
            </span>
            {typeof s.confidence_overall === "number" && (
              <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[11.5px] font-medium text-ink-2 border border-stone-200 bg-white">
                {t("history.confidence", { pct: formatNumber(s.confidence_overall * 100, 1) })}
              </span>
            )}
            {s.risk_veto && (
              <span className="inline-flex items-center px-3 py-1 rounded-full text-[11.5px] font-medium text-rose-700 border border-rose-200 bg-rose-50">
                {t("history.riskVeto")}
              </span>
            )}
          </div>
          <div className="mt-6 flex flex-col gap-3">
            <div className="mono text-[11px] text-stone-500 truncate">
              {t("history.session", { id: s.session_id ?? "" })}
            </div>
            <div>
              <button
                onClick={() => window.open(`/prediction/${s.session_id}`, '_blank')}
                className="inline-flex items-center gap-2 h-9 px-4 rounded-lg bg-stone-900 text-white text-[13px] font-medium transition-colors hover:bg-stone-800"
              >
                {t("history.viewFull")}
                <ArrowRight className="h-3.5 w-3.5 rtl:rotate-180" />
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Agent events */}
      {grouped.length === 0 ? (
        <div className="card p-6 text-center text-sm text-stone-500">
          {t("history.noEvents")}
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
                  {t("history.events", { count: g.events.length })}
                </span>
              </div>
              <div className="divide-y divide-stone-100">
                {g.events.map((e, i) => (
                  <div key={i} className="px-5 py-3">
                    <div className="flex items-center justify-between text-[11px] text-stone-500">
                      <span className="mono">{e.event_type || t("history.event")}</span>
                      {typeof e.confidence_score === "number" && (
                        <span>{t("history.conf", { pct: formatNumber(e.confidence_score * 100, 0) })}</span>
                      )}
                    </div>
                    {e.opinion_summary && (
                      <div className="mt-1.5">
                        <TranslatedMarkdown variant="paper">{e.opinion_summary}</TranslatedMarkdown>
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
  const t = useT();
  const { data, isLoading } = useBacktestDetail(sessionId);
  // When a single prediction is opened, we show its full reasoning trace
  // (the SAME component used for live runs) with a back button.
  const [openPrediction, setOpenPrediction] = useState<string | null>(null);

  if (isLoading) return <DetailSkeleton />;
  if (!data) {
    return (
      <DetailEmpty
        title={t("history.bt.notFound")}
        hint={t("history.bt.hint")}
      />
    );
  }

  if (openPrediction) {
    return (
      <div className="space-y-4 anim-fade-up">
        <button
          onClick={() => setOpenPrediction(null)}
          className="inline-flex items-center gap-1.5 text-[12.5px] font-medium text-stone-600 hover:text-ink"
        >
          <ArrowLeft className="h-3.5 w-3.5 rtl:rotate-180" /> {t("history.backToBacktest")}
        </button>
        {/* Full live-style reasoning trace for this single backtest prediction. */}
        <AnalysisDetail sessionId={openPrediction} />
      </div>
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

  const profile = data.run_config?.decision_profile;
  const equity = (data.daily_portfolio ?? [])
    .map((p) => ({ date: p.date, value: Number(p.equity ?? p.value ?? 0) }))
    .filter((p) => p.date && Number.isFinite(p.value) && p.value > 0);
  const bench = (data.benchmark_history ?? [])
    .map((p) => ({ date: p.date, value: Number(p.equity ?? p.value ?? 0) }))
    .filter((p) => p.date && Number.isFinite(p.value) && p.value > 0);

  return (
    <div className="space-y-5 anim-fade-up">
      <div className="relative overflow-hidden card-elevated grain">
        <div className={cn("absolute -top-24 -end-24 h-[280px] w-[280px] rounded-full blur-3xl opacity-30 pointer-events-none", halo)} />
        <div className={cn("relative h-[3px] w-full bg-gradient-to-r", accentBar)} />
        <div className="relative p-7">
          <div className="flex items-center gap-2 text-[10.5px] tracking-[0.18em] uppercase text-stone-500">
            <Database className="h-3 w-3" /> {t("history.btReplay")}
            {profile && (
              <span
                className={cn(
                  "ms-1 normal-case tracking-normal px-1.5 py-0.5 rounded text-[10px] font-semibold border",
                  profile === "tuned"
                    ? "bg-amber-50 text-amber-700 border-amber-200"
                    : "bg-stone-100 text-stone-600 border-stone-200"
                )}
                title={profile === "tuned" ? t("history.profile.tunedTitle") : t("history.profile.liveTitle")}
              >
                {profile === "tuned" ? t("history.profile.tuned") : t("history.profile.live")}
              </span>
            )}
          </div>
          <div className="mt-4 flex items-baseline gap-3 flex-wrap">
            <div className="display text-[28px] font-semibold tracking-tight text-ink">
              {data.ticker ? getTickerMeta(data.ticker).symbol : "—"}
            </div>
            {data.ticker && (
              <div className="text-[14px] text-stone-500">
                {getTickerMeta(data.ticker).nameEn}
              </div>
            )}
            <div className="mono text-[12px] text-stone-500">
              {data.start_date || ""} → {data.end_date || ""}
            </div>
          </div>
          <div className="mt-5 flex items-end gap-4">
            <Arrow className={cn("h-5 w-5", retClass)} />
            <div>
              <div className="eyebrow text-stone-500">{t("history.totalReturn")}</div>
              <div className={cn("display-num text-[40px] leading-none font-semibold mt-1", retClass)}>
                {typeof ret === "number" ? formatPercent(ret) : "—"}
              </div>
            </div>
          </div>
          <div className="mt-6 grid grid-cols-2 sm:grid-cols-3 gap-px bg-stone-200 rounded-2xl overflow-hidden border border-stone-200">
            <MiniTile label={t("history.mt.sharpe")} value={typeof m.sharpe_ratio === "number" ? formatNumber(m.sharpe_ratio) : "—"} />
            <MiniTile label={t("history.mt.sortino")} value={typeof m.sortino_ratio === "number" ? formatNumber(m.sortino_ratio) : "—"} />
            <MiniTile label={t("history.mt.maxdd")} value={typeof m.max_drawdown_pct === "number" ? formatPercent(m.max_drawdown_pct, 2, false) : "—"} valueClass="text-rose-700" />
            <MiniTile label={t("history.mt.alpha")} value={typeof m.alpha_pct === "number" ? formatPercent(m.alpha_pct, 2) : "—"} />
            <MiniTile label={t("history.mt.trades")} value={typeof m.total_trades === "number" ? String(Math.round(m.total_trades)) : "—"} />
            <MiniTile label={t("history.mt.finalEquity")} value={typeof m.final_equity === "number" ? formatNumber(m.final_equity) : "—"} />
          </div>
          <div className="mt-5 mono text-[11px] text-stone-500 truncate">
            {t("history.session", { id: data.session_id })}
          </div>
        </div>
      </div>

      {/* Two-scenario comparison (Follow the AI vs EGX30) */}
      {data.scenario_comparison && (
        <ScenarioComparisonCard sc={data.scenario_comparison} />
      )}

      {/* Equity curve vs EGX30 benchmark */}
      {equity.length > 1 && (
        <div className="card overflow-hidden">
          <div className="px-5 py-3 border-b border-stone-100 flex items-center justify-between">
            <div className="display text-[14px] font-semibold text-ink">{t("history.equity")}</div>
            <span className="text-[11px] text-stone-500">
              {t("history.strategy")} <span className="text-emerald-600">━</span>
              {bench.length > 1 && <> · EGX30 <span className="text-stone-400">┄</span></>}
            </span>
          </div>
          <div className="p-3">
            <EquityCurve equity={equity} benchmark={bench.length > 1 ? bench : undefined} height={240} />
          </div>
        </div>
      )}

      {/* Decision-quality summary (the "skillful, not random" evidence) */}
      <DecisionQualityCard detail={data} />

      {/* Per-prediction list — click to open the full reasoning trace */}
      <PredictionsList
        predictions={data.predictions ?? []}
        onOpen={(sid) => sid && setOpenPrediction(sid)}
      />
    </div>
  );
}

/* ── Scenario comparison (Follow the AI vs EGX30) ───────────────────────── */

function ScenarioComparisonCard({ sc }: { sc: ScenarioComparison }) {
  const t = useT();
  const follow = sc.follow_return_pct ?? 0;
  const index = sc.index_return_pct ?? null;
  const beat = sc.followed_beat_index;
  return (
    <div className="card overflow-hidden">
      <div className="px-5 py-3 border-b border-stone-100 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Target className="h-3.5 w-3.5 text-stone-500" />
          <div className="display text-[14px] font-semibold text-ink">
            {t("history.scenario.title")}
          </div>
        </div>
        {beat != null && (
          <span
            className={cn(
              "inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] font-semibold",
              beat
                ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                : "bg-rose-50 text-rose-700 border-rose-200"
            )}
          >
            {beat ? <Check className="h-3 w-3" /> : <X className="h-3 w-3" />}
            {beat ? t("history.scenario.beat") : t("history.scenario.lost")}
          </span>
        )}
      </div>
      <div className="p-5 space-y-4">
        <div className="text-[12.5px] text-ink-2">
          {t("history.scenario.pre", { start: sc.start })}{" "}
          <span
            className={cn(
              "inline-flex items-center px-1.5 py-0.5 rounded border text-[11px] font-semibold uppercase mono",
              verdictChip(sc.predicted_direction || sc.decision)
            )}
          >
            {tEnum(t, "signal", sc.predicted_direction || sc.decision)}
          </span>{" "}
          {t("history.scenario.post", { action: sc.action_taken ?? "", end: sc.end ?? "" })}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <ScenarioTile
            label={t("history.scenario.follow")}
            value={follow}
            sub={
              sc.decision === "BUY"
                ? t("history.scenario.stockCosts", { ticker: sc.ticker, pct: fmtPct(sc.stock_return_pct) })
                : t("history.scenario.cash")
            }
            highlight
          />
          <ScenarioTile
            label={t("history.scenario.index")}
            value={index}
            sub={t("history.scenario.benchmark")}
          />
        </div>
        {sc.outperformance_pct != null && (
          <div className="text-[12px] text-stone-600">
            {t("history.scenario.outperformance")}{" "}
            <span
              className={cn(
                "font-semibold mono",
                sc.outperformance_pct >= 0 ? "text-emerald-700" : "text-rose-700"
              )}
            >
              {sc.outperformance_pct >= 0 ? "+" : ""}
              {sc.outperformance_pct.toFixed(2)}%
            </span>
          </div>
        )}
        {sc.rationale && (
          <p className="text-[12px] text-ink-3 leading-relaxed border-s-2 border-stone-200 ps-3">
            {sc.rationale}
          </p>
        )}
      </div>
    </div>
  );
}

function ScenarioTile({
  label,
  value,
  sub,
  highlight,
}: {
  label: string;
  value: number | null;
  sub?: string;
  highlight?: boolean;
}) {
  const v = value;
  const cls = v == null ? "text-stone-400" : v >= 0 ? "text-emerald-700" : "text-rose-700";
  return (
    <div
      className={cn(
        "rounded-2xl border p-4",
        highlight ? "border-stone-900/15 bg-stone-50" : "border-stone-200 bg-white"
      )}
    >
      <div className="eyebrow text-stone-500">{label}</div>
      <div className={cn("display-num text-[28px] font-semibold leading-none mt-1.5", cls)}>
        {v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`}
      </div>
      {sub && <div className="text-[11px] text-stone-500 mt-1.5">{sub}</div>}
    </div>
  );
}

function fmtPct(v?: number): string {
  return v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

/* ── Decision-quality + predictions ────────────────────────────────────── */

function DecisionQualityCard({ detail }: { detail: BacktestDetail }) {
  const t = useT();
  const dq = detail.decision_quality;
  const primary = dq?.primary_horizon_days ?? 10;
  const h: DecisionQualityHorizon | undefined = dq?.horizons?.[String(primary)];
  if (!dq || !h || !h.n_evaluated) {
    return (
      <div className="card p-5">
        <div className="flex items-center gap-2 mb-1">
          <Target className="h-3.5 w-3.5 text-stone-500" />
          <div className="display text-[14px] font-semibold text-ink">{t("history.dq.title")}</div>
        </div>
        <p className="text-[12.5px] text-stone-500">
          {t("history.dq.none")}
        </p>
      </div>
    );
  }
  const hit = h.actionable_hit_rate;
  const random = h.baseline_random?.mean;
  const p = h.actionable_binomial_p_vs_50pct;
  const ci =
    h.actionable_ci_lo != null && h.actionable_ci_hi != null
      ? `${(h.actionable_ci_lo * 100).toFixed(0)}–${(h.actionable_ci_hi * 100).toFixed(0)}%`
      : null;
  const dist = dq.action_distribution ?? {};

  return (
    <div className="card overflow-hidden">
      <div className="px-5 py-3 border-b border-stone-100 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Target className="h-3.5 w-3.5 text-stone-500" />
          <div className="display text-[14px] font-semibold text-ink">
            {t("history.dq.titleH", { days: primary })}
          </div>
        </div>
        <span className="mono text-[11px] text-stone-500">{t("history.dq.scored", { count: h.n_evaluated })}</span>
      </div>
      <div className="p-5 space-y-4">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-stone-200 rounded-xl overflow-hidden border border-stone-200">
          <MiniTile
            label={t("history.dq.hitRate")}
            value={hit != null ? `${(hit * 100).toFixed(0)}%` : "—"}
            valueClass="text-emerald-700"
          />
          <MiniTile label={t("history.dq.ci")} value={ci ?? "—"} />
          <MiniTile
            label={t("history.dq.ic")}
            value={h.information_coefficient != null ? h.information_coefficient.toFixed(2) : "—"}
          />
          <MiniTile
            label={t("history.dq.vsRandom")}
            value={random != null ? `${(random * 100).toFixed(0)}%` : "—"}
          />
        </div>
        <div className="flex flex-wrap gap-2 text-[11.5px]">
          {p != null && (
            <span
              className={cn(
                "px-2 py-1 rounded-full border",
                p < 0.05
                  ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                  : "bg-stone-50 text-stone-600 border-stone-200"
              )}
            >
              {t("history.dq.binomial", { p: p < 0.001 ? "< 0.001" : `= ${p.toFixed(3)}` })}
            </span>
          )}
          <span className="px-2 py-1 rounded-full border border-stone-200 bg-stone-50 text-stone-600">
            {t("history.dq.dist", { buy: dist.BUY ?? 0, hold: dist.HOLD ?? 0, sell: dist.SELL ?? 0 })}
          </span>
        </div>
        {h.confusion_matrix && (
          <ConfusionMini cm={h.confusion_matrix} />
        )}
        <p className="text-[11px] text-stone-400 leading-relaxed">
          {t("history.dq.foot")}
        </p>
      </div>
    </div>
  );
}

function ConfusionMini({
  cm,
}: {
  cm: Record<string, { UP: number; FLAT: number; DOWN: number }>;
}) {
  const t = useT();
  const rows = ["BUY", "HOLD", "SELL"];
  const cols: ("UP" | "FLAT" | "DOWN")[] = ["UP", "FLAT", "DOWN"];
  return (
    <div>
      <div className="eyebrow text-stone-500 mb-1.5">{t("history.dq.confusion")}</div>
      <table className="w-full text-[11.5px] mono border border-stone-200 rounded-lg overflow-hidden">
        <thead>
          <tr className="bg-stone-50 text-stone-500">
            <th className="text-left px-2 py-1 font-medium"> </th>
            {cols.map((c) => (
              <th key={c} className="px-2 py-1 text-right font-medium">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r} className="border-t border-stone-100">
              <td className="px-2 py-1 text-stone-600 font-semibold">{r}</td>
              {cols.map((c) => {
                // Diagonal = correct: BUY→UP, HOLD→FLAT, SELL→DOWN.
                const correct =
                  (r === "BUY" && c === "UP") ||
                  (r === "HOLD" && c === "FLAT") ||
                  (r === "SELL" && c === "DOWN");
                const v = cm[r]?.[c] ?? 0;
                return (
                  <td
                    key={c}
                    className={cn(
                      "px-2 py-1 text-right",
                      v > 0 && correct
                        ? "bg-emerald-50 text-emerald-700 font-semibold"
                        : v > 0
                        ? "text-stone-600"
                        : "text-stone-300"
                    )}
                  >
                    {v}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PredictionsList({
  predictions,
  onOpen,
}: {
  predictions: BacktestPrediction[];
  onOpen: (sessionId: string | null | undefined) => void;
}) {
  const t = useT();
  if (!predictions.length) return null;
  return (
    <div className="card overflow-hidden">
      <div className="px-5 py-3 border-b border-stone-100 flex items-center justify-between">
        <div className="display text-[14px] font-semibold text-ink">
          {t("history.predictions", { count: predictions.length })}
        </div>
        <span className="text-[11px] text-stone-500">{t("history.clickInspect")}</span>
      </div>
      <div className="divide-y divide-stone-100">
        {predictions.map((p, i) => {
          const fwd = p.forward_return_10d ?? p.forward_return_5d ?? p.forward_return_20d;
          const clickable = Boolean(p.session_id);
          return (
            <button
              key={`${p.date}-${i}`}
              disabled={!clickable}
              onClick={() => onOpen(p.session_id)}
              className={cn(
                "w-full text-left px-5 py-3 flex items-center gap-3 transition-colors",
                clickable ? "hover:bg-stone-50 cursor-pointer" : "cursor-default opacity-80"
              )}
            >
              <span className="mono text-[12px] text-stone-500 w-[88px] shrink-0">{p.date}</span>
              <span
                className={cn(
                  "inline-flex items-center px-2 py-0.5 rounded-full border text-[10.5px] font-semibold uppercase tracking-wider mono shrink-0",
                  verdictChip(p.decision)
                )}
              >
                {tEnum(t, "signal", p.decision)}
              </span>
              {p.correct != null && (
                <span
                  className={cn(
                    "inline-flex items-center justify-center h-4 w-4 rounded-full shrink-0",
                    p.correct ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-700"
                  )}
                  title={p.correct ? t("history.callMatched") : t("history.callMissed")}
                >
                  {p.correct ? <Check className="h-3 w-3" /> : <X className="h-3 w-3" />}
                </span>
              )}
              <span className="flex-1" />
              {typeof fwd === "number" && (
                <span
                  className={cn(
                    "mono text-[11.5px]",
                    fwd > 0 ? "text-emerald-600" : fwd < 0 ? "text-rose-600" : "text-stone-500"
                  )}
                  title={t("history.fwdTitle")}
                >
                  {fwd > 0 ? "+" : ""}{fwd.toFixed(2)}%
                </span>
              )}
              {clickable ? (
                <ArrowRight className="h-3.5 w-3.5 text-stone-300 shrink-0 rtl:rotate-180" />
              ) : (
                <span className="text-[10px] text-stone-400 shrink-0">{t("history.noTrace")}</span>
              )}
            </button>
          );
        })}
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
  const t = useT();
  return (
    <div className="card overflow-hidden grain">
      <div className="px-8 py-12 text-center">
        <div className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-stone-100 text-stone-500 mb-4">
          <Inbox className="h-5 w-5" />
        </div>
        <h3 className="display text-[18px] font-semibold text-ink">
          {t(`history.empty.${kind}.title`)}
        </h3>
        <p className="text-[13px] text-ink-3 mt-1.5 max-w-sm mx-auto leading-relaxed">
          {t(`history.empty.${kind}.hint`)}
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

function verdictChip(decision: string) {
  const d = decision.toUpperCase();
  if (d === "BUY" || d === "STRONG_BUY")
    return "bg-emerald-50 text-emerald-700 border-emerald-200";
  if (d === "SELL" || d === "STRONG_SELL")
    return "bg-rose-50 text-rose-700 border-rose-200";
  return "bg-stone-100 text-stone-600 border-stone-200";
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

function formatRelativeTs(ts: string, t: (key: string, params?: Record<string, string | number>) => string) {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
  const diff = Date.now() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return t("history.time.now");
  if (mins < 60) return t("history.time.min", { n: mins });
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return t("history.time.hour", { n: hrs });
  const days = Math.floor(hrs / 24);
  if (days < 30) return t("history.time.day", { n: days });
  return d.toLocaleDateString();
}
