import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import {
  AlertCircle,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Briefcase,
  Calendar,
  CircleDollarSign,
  Gavel,
  Hash,
  Shield,
  TrendingDown,
  TrendingUp,
  Wallet,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { endpoints } from "../../services/api";
import { useRunBacktest } from "../../hooks/useBacktest";
import type {
  BacktestAuditEntry,
  BacktestDetail,
  BacktestSession,
  BacktestTrade,
  BenchmarkBlock,
  ExitPlan,
  ThesisSummary,
} from "../../services/api/types";
import { AgentCard, AgentCardSkeleton } from "../shared/AgentCard";
import { TickerPicker } from "../shared/TickerPicker";
import { DatePicker } from "../shared/DatePicker";
import { cn, formatNumber, formatPercent } from "../../lib/utils";

const DEFAULT_ANALYSTS = ["market", "fundamentals", "news", "social"];

function todayIso() {
  const t = new Date();
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`;
}

export function BacktestScreen() {
  const [ticker, setTicker] = useState("COMI.CA");
  const TODAY = todayIso();
  const [startDate, setStartDate] = useState("2024-01-01");
  const [endDate, setEndDate] = useState("2024-04-01");
  const [budget, setBudget] = useState(100_000);

  // Date validation
  const dateError = (() => {
    if (!startDate || !endDate) return null;
    if (startDate > TODAY) return "Start date can't be in the future.";
    if (endDate > TODAY) return "End date can't be in the future.";
    if (startDate >= endDate) return "Start date must be before end date.";
    return null;
  })();

  const runBacktest = useRunBacktest();
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const baselineRef = useRef<Set<string>>(new Set());
  const startedAtRef = useRef<number>(0);
  const [waiting, setWaiting] = useState(false);

  const listQuery = useQuery({
    queryKey: ["backtests-poll"],
    queryFn: () => endpoints.listBacktests(),
    enabled: waiting,
    refetchInterval: waiting ? 4_000 : false,
  });

  useEffect(() => {
    if (!waiting) return;
    const sessions = listQuery.data?.sessions ?? [];
    const candidate = sessions.find(
      (s: BacktestSession) =>
        s.ticker === ticker &&
        s.engine === "llm_multi_agent" &&
        !baselineRef.current.has(s.session_id)
    );
    if (candidate) {
      setActiveSession(candidate.session_id);
      setWaiting(false);
      toast.success("Backtest complete");
    } else if (Date.now() - startedAtRef.current > 15 * 60 * 1000) {
      setWaiting(false);
      toast.error("Backtest timed out waiting for results");
    }
  }, [listQuery.data, ticker, waiting]);

  const detailQuery = useQuery<BacktestDetail>({
    queryKey: ["backtest-detail", activeSession],
    queryFn: () => endpoints.getBacktest(activeSession as string),
    enabled: !!activeSession,
    staleTime: 60_000,
  });

  const detail = detailQuery.data;
  const metrics = detail?.metrics ?? {};
  const trades = (detail?.trades ?? []) as BacktestTrade[];
  // audit_log carries the per-date agent text (risk_judge_text, bull/bear
  // thesis summaries). The dashboard uses the most-recent non-empty entries
  // to render the agent cards with real content instead of templated copy.
  const auditLog = useMemo(
    () => ((detail?.audit_log as BacktestAuditEntry[]) ?? []) as BacktestAuditEntry[],
    [detail?.audit_log]
  );
  const insights = useMemo(
    () => deriveAgentInsights(trades, auditLog),
    [trades, auditLog]
  );

  // closed_trades is the only honest signal for "do we have realised PnL?"
  // When zero, Max DD / Win Rate / Net PnL are mechanically 0 and confuse
  // readers — we hide them and surface open-position context instead.
  const closedTrades =
    typeof metrics.closed_trades === "number"
      ? Number(metrics.closed_trades)
      : (trades.filter((t) => (t.action || "").toUpperCase() === "SELL").length);
  const hasClosedTrades = closedTrades > 0;
  const openBuys = trades.filter(
    (t) => (t.action || "").toUpperCase() === "BUY"
  ).length - closedTrades;
  const openPositionsCount = Math.max(0, openBuys);

  async function handleRun() {
    if (!ticker || !startDate || !endDate || !budget) {
      toast.error("Fill ticker, dates and budget");
      return;
    }
    if (dateError) {
      toast.error(dateError);
      return;
    }
    try {
      const list = await endpoints.listBacktests();
      baselineRef.current = new Set(
        (list?.sessions ?? []).map((s: BacktestSession) => s.session_id)
      );
      setActiveSession(null);
      await runBacktest.mutateAsync({
        ticker,
        start_date: startDate,
        end_date: endDate,
        initial_capital: budget,
        interval: 20,
        selected_analysts: DEFAULT_ANALYSTS,
      });
      startedAtRef.current = Date.now();
      setWaiting(true);
      toast.info("Backtest started — results will appear when ready");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to start backtest");
    }
  }

  const isLoading = runBacktest.isPending || waiting || detailQuery.isLoading;
  const hasResult = !!detail;

  const pnlPct =
    typeof metrics.total_return_pct === "number"
      ? Number(metrics.total_return_pct)
      : undefined;
  const finalEquity =
    typeof metrics.final_equity === "number"
      ? Number(metrics.final_equity)
      : undefined;
  const initial =
    typeof metrics.initial_capital === "number"
      ? Number(metrics.initial_capital)
      : budget;
  const pnlAbs =
    finalEquity !== undefined && initial !== undefined
      ? finalEquity - initial
      : undefined;

  return (
    <div className="space-y-10">
      {/* ─── Page header ─────────────────────────────────────────── */}
      <header>
        <div className="eyebrow mb-3">Workspace · Backtesting</div>
        <h1 className="display text-[40px] md:text-[44px] font-semibold leading-[1.05] text-ink">
          Replay the strategy,
          <br />
          <span className="italic">live-fire</span> against history.
        </h1>
        <p className="text-[14px] text-ink-3 mt-3 max-w-xl leading-relaxed">
          Pick a window and a starting budget. The same multi-agent pipeline
          runs across every interval, executes inside EGX limits, and reports a
          full attribution.
        </p>
      </header>

      {/* ─── Form panel ─────────────────────────────────────────── */}
      <ConfigPanel
        ticker={ticker}
        setTicker={setTicker}
        startDate={startDate}
        setStartDate={setStartDate}
        endDate={endDate}
        setEndDate={setEndDate}
        budget={budget}
        setBudget={setBudget}
        isLoading={isLoading}
        running={runBacktest.isPending}
        waiting={waiting}
        loadingDetail={detailQuery.isLoading}
        onRun={handleRun}
        today={TODAY}
        dateError={dateError}
      />

      {!isLoading && !hasResult && <EmptyState />}

      {isLoading && !hasResult && <LoadingState />}

      {hasResult && (
        <div className="space-y-8">
          <PnlHero
            ticker={detail?.ticker || ticker}
            startDate={(detail?.start_date as string | undefined) || startDate}
            endDate={(detail?.end_date as string | undefined) || endDate}
            initial={initial}
            finalEquity={finalEquity}
            pnlAbs={pnlAbs}
            pnlPct={pnlPct}
            sharpe={metrics.sharpe_ratio as number | undefined}
            maxDd={metrics.max_drawdown_pct as number | undefined}
            winRate={metrics.win_rate as number | undefined}
            totalTrades={metrics.total_trades as number | undefined}
            hasClosedTrades={hasClosedTrades}
            openPositions={openPositionsCount}
          />

          <SectionLabel index="01" label="Agent retrospective" />
          <div className="grid gap-5 md:grid-cols-2">
            <AgentCard
              tone="bull"
              icon={TrendingUp}
              agent="Bull researcher"
              role="Long-side conviction"
              chip={`${insights.bull.count} buys`}
              meta={[
                { label: "Avg confidence", value: insights.bull.confChip },
                { label: "Hit rate", value: insights.bull.hitChip },
              ]}
            >
              <div className="space-y-3">
                <div>{insights.bull.summary}</div>
                <ThesisDetail thesis={insights.bull.thesis} tone="bull" />
              </div>
            </AgentCard>
            <AgentCard
              tone="bear"
              icon={TrendingDown}
              agent="Bear researcher"
              role="Short-side caution"
              chip={`${insights.bear.count} sells`}
              meta={[
                { label: "Avg confidence", value: insights.bear.confChip },
                { label: "Realised", value: insights.bear.realisedChip },
              ]}
            >
              <div className="space-y-3">
                <div>{insights.bear.summary}</div>
                {insights.bear.thesis ? (
                  <ThesisDetail thesis={insights.bear.thesis} tone="bear" />
                ) : insights.bear.datesEvaluated > 0 ? (
                  <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[12.5px] text-amber-800">
                    <strong>Heads up:</strong> the bear researcher produced no
                    structured thesis on any of the{" "}
                    {insights.bear.datesEvaluated} evaluation date
                    {insights.bear.datesEvaluated === 1 ? "" : "s"} in this
                    run. The downstream debate had only the bull view to
                    weigh — open agent-pipeline issue, not a model verdict.
                  </div>
                ) : null}
              </div>
            </AgentCard>
            <AgentCard
              tone="judge"
              icon={Gavel}
              agent="Debate judge"
              role="Reconciled verdict"
              chip={`Avg conf: ${insights.judge.avgConfidence}`}
              meta={
                // Only surface realised PnL once a SELL closes a trade.
                // Otherwise the chip is mechanically 0 and confuses readers.
                hasClosedTrades
                  ? [
                      { label: "Decisions", value: String(trades.length) },
                      { label: "Realised PnL", value: insights.judge.realisedChip },
                    ]
                  : [
                      { label: "Decisions", value: String(trades.length) },
                      {
                        label: "Open positions",
                        value: openPositionsCount > 0 ? String(openPositionsCount) : "—",
                      },
                    ]
              }
            >
              {insights.judge.summary}
            </AgentCard>
            <AgentCard
              tone="risk"
              icon={Shield}
              agent="Risk manager"
              role="EGX constraint guardrail"
              chip={insights.risk.chip}
              meta={[
                {
                  label: "Decisions cleared",
                  value: trades.length > 0 ? String(trades.length) : "0",
                },
              ]}
            >
              <div className="space-y-3">
                <div>{insights.risk.summary}</div>
                {insights.risk.judgeText ? (
                  <RiskJudgeText text={insights.risk.judgeText} />
                ) : null}
              </div>
            </AgentCard>
          </div>

          {insights.decisions.total > 0 ? (
            <>
              <SectionLabel index="02" label="Decision log" />
              <DecisionTimeline decisions={insights.decisions} />
            </>
          ) : null}

          <SectionLabel
            index={insights.decisions.total > 0 ? "03" : "02"}
            label="Realised trades"
          />
          <AgentCard
            tone="manager"
            icon={Briefcase}
            agent="Portfolio manager"
            role="Trade log"
            chip={`${trades.length} fills`}
          >
            <TradeLog trades={trades} closedCount={closedTrades} />
          </AgentCard>

          <SectionLabel
            index={insights.decisions.total > 0 ? "04" : "03"}
            label="Scenario comparison"
          />
          <ScenarioComparison
            ticker={detail?.ticker || ticker}
            initial={initial}
            finalEquity={finalEquity}
            strategyReturnPct={pnlPct}
            buyholdReturnPct={metrics.buyhold_return_pct as number | undefined}
            benchmarkReturnPct={metrics.benchmark_return_pct as number | undefined}
            benchmarkBlock={detail?.benchmark}
          />
        </div>
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════════════
   Sub-components
   ════════════════════════════════════════════════════════════════════════════ */

function ConfigPanel({
  ticker,
  setTicker,
  startDate,
  setStartDate,
  endDate,
  setEndDate,
  budget,
  setBudget,
  isLoading,
  running,
  waiting,
  loadingDetail,
  onRun,
  today,
  dateError,
}: {
  ticker: string;
  setTicker: (v: string) => void;
  startDate: string;
  setStartDate: (v: string) => void;
  endDate: string;
  setEndDate: (v: string) => void;
  budget: number;
  setBudget: (v: number) => void;
  isLoading: boolean;
  running: boolean;
  waiting: boolean;
  loadingDetail: boolean;
  onRun: () => void;
  today: string;
  dateError: string | null;
}) {
  const buttonLabel = running
    ? "Submitting…"
    : waiting
    ? "Running backtest…"
    : loadingDetail
    ? "Loading results…"
    : "Run backtest";

  // Start can't go past end (or today, whichever is earlier).
  // End can't go past today, and not before start.
  const startMax = endDate && endDate < today ? endDate : today;

  return (
    <div className="card-elevated p-6">
      <div className="grid grid-cols-1 md:grid-cols-12 gap-5 items-end">
        <div className="md:col-span-4">
          <TickerPicker label="Ticker" value={ticker} onChange={setTicker} />
        </div>
        <Field
          className="md:col-span-3"
          label="Start date"
          icon={<Calendar className="h-3 w-3" />}
        >
          <DatePicker
            value={startDate}
            onChange={setStartDate}
            max={startMax}
          />
        </Field>
        <Field
          className="md:col-span-3"
          label="End date"
          icon={<Calendar className="h-3 w-3" />}
        >
          <DatePicker
            value={endDate}
            onChange={setEndDate}
            min={startDate || undefined}
            max={today}
          />
        </Field>
        <Field
          className="md:col-span-2"
          label="Budget (EGP)"
          icon={<CircleDollarSign className="h-3 w-3" />}
        >
          <input
            type="number"
            min={1000}
            step={1000}
            value={budget}
            onChange={(e) => setBudget(Number(e.target.value) || 0)}
            className="input-base mono"
          />
        </Field>
      </div>

      {/* Inline validation row */}
      {dateError && (
        <div className="mt-4 flex items-start gap-2.5 px-3.5 py-2.5 rounded-lg bg-rose-50 border border-rose-200">
          <AlertCircle className="h-4 w-4 text-rose-600 mt-0.5 shrink-0" />
          <div className="text-[13px] text-rose-800">{dateError}</div>
        </div>
      )}

      <div className="mt-6 pt-5 border-t border-stone-100 flex items-center justify-between gap-4 flex-wrap">
        <div className="text-[12px] text-stone-500 flex items-center gap-2">
          <Hash className="h-3.5 w-3.5" />
          Analysts:{" "}
          <span className="text-ink-2 font-medium">
            market · fundamentals · news · social
          </span>
        </div>
        <button
          onClick={onRun}
          disabled={isLoading || !!dateError}
          className={cn(
            "inline-flex items-center gap-2 h-11 px-5 rounded-xl",
            "bg-stone-900 hover:bg-stone-800 text-white text-[14px] font-medium",
            "transition-all shadow-[0_4px_12px_-2px_rgba(0,0,0,0.18)]",
            "disabled:opacity-60 disabled:cursor-not-allowed"
          )}
        >
          {isLoading ? (
            <>
              <span className="h-2 w-2 rounded-full bg-emerald-400 anim-pulse-dot" />
              {buttonLabel}
            </>
          ) : (
            <>
              {buttonLabel}
              <ArrowRight className="h-4 w-4" />
            </>
          )}
        </button>
      </div>

      <style>{`
        .input-base {
          width: 100%;
          height: 48px;
          padding: 0 14px;
          border-radius: 12px;
          border: 1px solid #E7E5E4;
          background: #FFF;
          font-size: 14px;
          color: var(--ink);
          transition: border-color .15s, box-shadow .15s;
        }
        .input-base:focus {
          outline: none;
          border-color: #0A0A0B;
          box-shadow: 0 0 0 4px rgba(10,10,11,0.05);
        }
      `}</style>
    </div>
  );
}

function Field({
  label,
  icon,
  children,
  className,
}: {
  label: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      <div className="eyebrow mb-2 flex items-center gap-1.5">
        {icon}
        {label}
      </div>
      {children}
    </div>
  );
}

function PnlHero({
  ticker,
  startDate,
  endDate,
  initial,
  finalEquity,
  pnlAbs,
  pnlPct,
  sharpe,
  maxDd,
  winRate,
  totalTrades,
  hasClosedTrades,
  openPositions,
}: {
  ticker: string;
  startDate?: string;
  endDate?: string;
  initial?: number;
  finalEquity?: number;
  pnlAbs?: number;
  pnlPct?: number;
  sharpe?: number;
  maxDd?: number;
  winRate?: number;
  totalTrades?: number;
  hasClosedTrades: boolean;
  openPositions: number;
}) {
  const profit = (pnlPct ?? 0) >= 0;
  const Arrow = profit ? ArrowUpRight : ArrowDownRight;
  const halo = profit ? "bg-emerald-300" : "bg-rose-300";
  const accentBar = profit
    ? "from-emerald-500/0 via-emerald-500 to-teal-500"
    : "from-rose-500/0 via-rose-500 to-orange-500";
  const chipClass = profit
    ? "bg-emerald-50 text-emerald-700 border-emerald-200"
    : "bg-rose-50 text-rose-700 border-rose-200";
  const pnlBigClass = profit ? "text-emerald-700" : "text-rose-700";

  return (
    <section className="relative overflow-hidden card-elevated grain anim-fade-up">
      <div
        className={cn(
          "absolute -top-32 -right-24 h-[400px] w-[400px] rounded-full blur-3xl opacity-30 pointer-events-none",
          halo
        )}
      />
      <div className={cn("relative h-[3px] w-full bg-gradient-to-r", accentBar)} />

      <div className="relative p-8 lg:p-10 grid grid-cols-1 lg:grid-cols-[1.2fr_1fr] gap-10">
        {/* Left: ticker + headline pnl */}
        <div>
          <div className="flex items-center gap-2 text-[10.5px] tracking-[0.18em] uppercase text-stone-500">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 anim-pulse-dot" />
            Backtest complete
          </div>
          <div className="mt-4 flex items-baseline gap-4 flex-wrap">
            <div className="display text-[34px] font-semibold tracking-tight text-ink">
              {ticker}
            </div>
            <div className="text-[12px] text-stone-500 mono">
              {startDate} → {endDate}
            </div>
          </div>

          <div className="mt-7 flex items-end gap-5">
            <div
              className={cn(
                "h-14 w-14 rounded-2xl border flex items-center justify-center shrink-0",
                chipClass
              )}
            >
              <Arrow className="h-6 w-6" />
            </div>
            <div>
              <div className="eyebrow text-stone-500">Total return</div>
              <div
                className={cn(
                  "display-num text-[68px] leading-none font-semibold mt-2",
                  pnlBigClass
                )}
              >
                {pnlPct !== undefined ? formatPercent(pnlPct) : "—"}
              </div>
              <div className="text-[14px] text-ink-2 mt-2">
                {pnlAbs !== undefined ? (
                  <>
                    <span
                      className={cn(
                        "font-medium mono",
                        profit ? "text-emerald-700" : "text-rose-700"
                      )}
                    >
                      {pnlAbs >= 0 ? "+" : ""}
                      {formatNumber(pnlAbs)} EGP
                    </span>
                    <span className="text-stone-500 ml-2">
                      from {initial !== undefined ? formatNumber(initial) : "—"}{" "}
                      EGP starting budget
                    </span>
                  </>
                ) : (
                  "—"
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Right: stat tiles on warm paper.
            We show Max DD + Win Rate only when at least one trade closed,
            because both are mechanically zero with open-only positions and
            give a misleading "the strategy failed" impression. When the
            window had no SELLs we swap in Open positions + Unrealised P&L
            so the user sees what actually happened. */}
        <div className="grid grid-cols-2 gap-px bg-stone-200 rounded-2xl overflow-hidden border border-stone-200 self-end">
          <StatTile
            label="Final equity"
            value={finalEquity !== undefined ? formatNumber(finalEquity) : "—"}
            unit="EGP"
          />
          <StatTile
            label="Sharpe"
            value={sharpe !== undefined ? formatNumber(sharpe) : "—"}
          />
          {hasClosedTrades ? (
            <>
              <StatTile
                label="Max drawdown"
                value={maxDd !== undefined ? formatPercent(maxDd, 2, false) : "—"}
                valueClass="text-rose-700"
              />
              <StatTile
                label="Win rate"
                value={winRate !== undefined ? formatPercent(winRate, 1, false) : "—"}
                valueClass="text-emerald-700"
              />
            </>
          ) : (
            <>
              <StatTile
                label="Open positions"
                value={openPositions > 0 ? String(openPositions) : "—"}
              />
              <StatTile
                label="Unrealised P&L"
                value={
                  pnlAbs !== undefined
                    ? `${pnlAbs >= 0 ? "+" : ""}${formatNumber(pnlAbs)}`
                    : "—"
                }
                unit="EGP"
                valueClass={
                  pnlAbs !== undefined && pnlAbs < 0
                    ? "text-rose-700"
                    : "text-emerald-700"
                }
              />
            </>
          )}
          <StatTile
            label="Total trades"
            value={
              totalTrades !== undefined ? String(Math.round(Number(totalTrades))) : "—"
            }
          />
          <StatTile
            label="Initial budget"
            value={initial !== undefined ? formatNumber(initial) : "—"}
            unit="EGP"
          />
        </div>
      </div>
    </section>
  );
}

function StatTile({
  label,
  value,
  unit,
  valueClass,
}: {
  label: string;
  value: string;
  unit?: string;
  valueClass?: string;
}) {
  return (
    <div className="bg-white p-5">
      <div className="eyebrow text-stone-500">{label}</div>
      <div className="mt-2 flex items-baseline gap-1.5">
        <span className={cn("display-num text-[22px] font-semibold text-ink", valueClass)}>
          {value}
        </span>
        {unit && <span className="text-[11px] text-stone-500">{unit}</span>}
      </div>
    </div>
  );
}

function SectionLabel({ index, label }: { index: string; label: string }) {
  return (
    <div className="flex items-center gap-3 mt-2">
      <span className="mono text-[11px] text-stone-400">{index}</span>
      <div className="eyebrow text-stone-600">{label}</div>
      <div className="h-px flex-1 bg-stone-200" />
    </div>
  );
}

function TradeLog({
  trades,
  closedCount,
}: {
  trades: BacktestTrade[];
  closedCount: number;
}) {
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  if (!trades.length) {
    return (
      <div className="rounded-xl border border-dashed border-stone-200 px-6 py-10 text-center text-sm text-stone-500">
        No trades were executed during this window — the risk gate kept the
        portfolio in cash.
      </div>
    );
  }
  const showRealisedColumn = closedCount > 0;

  return (
    <div className="mt-1 rounded-xl border border-stone-200 overflow-hidden">
      <div className="max-h-[520px] overflow-auto">
        <table className="w-full text-sm">
          <thead className="bg-stone-50 sticky top-0 z-10">
            <tr className="text-left text-[10.5px] tracking-[0.18em] uppercase text-stone-500">
              <th className="px-4 py-2.5 w-6"></th>
              <th className="px-4 py-2.5">Date</th>
              <th className="px-4 py-2.5">Action</th>
              <th className="px-4 py-2.5 text-right">Price</th>
              <th className="px-4 py-2.5 text-right">Qty</th>
              {showRealisedColumn && (
                <th className="px-4 py-2.5 text-right">Realised P&L</th>
              )}
            </tr>
          </thead>
          <tbody>
            {trades.map((t, i) => {
              const action = (t.action || "").toUpperCase();
              const actionClass =
                action === "BUY"
                  ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                  : action === "SELL"
                  ? "bg-rose-50 text-rose-700 border-rose-200"
                  : "bg-stone-50 text-stone-700 border-stone-200";
              const hasExitPlan =
                action === "BUY" &&
                t.exit_plan &&
                typeof t.exit_plan === "object";
              const isOpen = expandedIdx === i;
              const rowClickable = hasExitPlan;
              return (
                <Fragment key={i}>
                  <tr
                    className={cn(
                      "border-t border-stone-100 transition-colors",
                      rowClickable
                        ? "cursor-pointer hover:bg-stone-50"
                        : "hover:bg-stone-50/60"
                    )}
                    onClick={
                      rowClickable
                        ? () => setExpandedIdx(isOpen ? null : i)
                        : undefined
                    }
                  >
                    <td className="px-2 py-2.5 text-center text-[10px] text-stone-400">
                      {rowClickable ? (isOpen ? "▾" : "▸") : ""}
                    </td>
                    <td className="px-4 py-2.5 mono text-[12.5px] text-stone-600">
                      {t.date || "—"}
                    </td>
                    <td className="px-4 py-2.5">
                      <span
                        className={cn(
                          "inline-flex px-2 py-0.5 rounded-md border text-[11px] font-semibold tracking-wide",
                          actionClass
                        )}
                      >
                        {action || "—"}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-right mono text-[13px]">
                      {typeof t.price === "number" ? formatNumber(t.price) : "—"}
                    </td>
                    <td className="px-4 py-2.5 text-right mono text-[13px]">
                      {t.quantity ?? t.shares ?? "—"}
                    </td>
                    {showRealisedColumn && (
                      <td
                        className={cn(
                          "px-4 py-2.5 text-right mono text-[13px] font-medium",
                          action === "SELL" && typeof t.pnl === "number"
                            ? t.pnl >= 0
                              ? "text-emerald-700"
                              : "text-rose-700"
                            : "text-stone-500"
                        )}
                      >
                        {action === "SELL" && typeof t.pnl === "number"
                          ? formatNumber(t.pnl)
                          : action === "BUY"
                          ? "Open"
                          : "—"}
                      </td>
                    )}
                  </tr>
                  {rowClickable && isOpen && (
                    <tr className="border-t border-stone-100 bg-stone-50/40">
                      <td colSpan={showRealisedColumn ? 6 : 5} className="px-6 py-4">
                        <ExitPlanDetail
                          plan={t.exit_plan as ExitPlan}
                          entryPrice={typeof t.price === "number" ? t.price : undefined}
                          quantity={
                            typeof t.quantity === "number"
                              ? t.quantity
                              : typeof t.shares === "number"
                              ? t.shares
                              : undefined
                          }
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
        {trades.some((t) => t.exit_plan) && (
          <div className="bg-stone-50 px-4 py-2 text-[11px] text-stone-500 border-t border-stone-100">
            Click a BUY row to see the trader's planned exits.
          </div>
        )}
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */

function ThesisDetail({
  thesis,
  tone,
}: {
  thesis: ThesisSummary | null | undefined;
  tone: "bull" | "bear";
}) {
  if (!thesis) return null;
  const tags = [
    thesis.conviction_level && `Conviction ${thesis.conviction_level}`,
    thesis.time_horizon && `Horizon ${thesis.time_horizon}`,
    thesis.alignment_score && `Signal alignment ${thesis.alignment_score}`,
  ].filter(Boolean) as string[];

  const upsideRow =
    typeof thesis.base_case_upside_pct === "number" ||
    typeof thesis.downside_risk_pct === "number" ? (
      <div className="text-[12px] text-stone-600 flex gap-4 mt-2">
        {typeof thesis.base_case_upside_pct === "number" && (
          <span>
            <span className="text-stone-400">Base case upside:</span>{" "}
            <span className="text-emerald-700 font-medium mono">
              +{thesis.base_case_upside_pct}%
            </span>
          </span>
        )}
        {typeof thesis.downside_risk_pct === "number" && (
          <span>
            <span className="text-stone-400">Downside risk:</span>{" "}
            <span className="text-rose-700 font-medium mono">
              {thesis.downside_risk_pct}%
            </span>
          </span>
        )}
      </div>
    ) : null;

  return (
    <div className="rounded-lg border border-stone-200 bg-white/60 p-3 space-y-2">
      {tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {tags.map((t) => (
            <span
              key={t}
              className="inline-flex px-2 py-0.5 rounded-md bg-stone-100 text-stone-700 text-[10.5px] font-medium tracking-wide"
            >
              {t}
            </span>
          ))}
        </div>
      )}
      {thesis.catalysts && thesis.catalysts.length > 0 && (
        <div>
          <div className="eyebrow text-stone-500 mb-1">
            {tone === "bull" ? "Key catalysts" : "Key risks"}
          </div>
          <ul className="space-y-0.5 text-[12.5px] text-ink-2 leading-relaxed">
            {thesis.catalysts.slice(0, 4).map((c, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-stone-400 mt-0.5">•</span>
                <span>{c}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {thesis.invalidation && thesis.invalidation.length > 0 && (
        <div>
          <div className="eyebrow text-stone-500 mb-1">Invalidation triggers</div>
          <ul className="space-y-0.5 text-[12.5px] text-ink-2 leading-relaxed">
            {thesis.invalidation.slice(0, 3).map((c, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-stone-400 mt-0.5">•</span>
                <span>{c}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {upsideRow}
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */

function RiskJudgeText({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const TRUNC_AT = 600;
  const showToggle = text.length > TRUNC_AT;
  const display = expanded || !showToggle ? text : text.slice(0, TRUNC_AT) + "…";
  return (
    <div className="rounded-lg border border-stone-200 bg-white/60 p-3">
      <div className="eyebrow text-stone-500 mb-1.5">
        Risk judge — clause-by-clause review
      </div>
      <div className="text-[12.5px] text-ink-2 leading-relaxed whitespace-pre-wrap">
        {display}
      </div>
      {showToggle && (
        <button
          type="button"
          className="mt-2 text-[11.5px] font-medium text-stone-700 hover:text-ink"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "Show less" : "Show full analysis"}
        </button>
      )}
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */

interface DecisionRow {
  date: string;
  decision: string;
  confidence: number | null;
  reasoning: string | null;
}

interface DecisionSummary {
  timeline: DecisionRow[];
  holdCount: number;
  buyCount: number;
  sellCount: number;
  total: number;
}

function decisionBadgeClass(decision: string): string {
  switch (decision) {
    case "BUY":
      return "bg-emerald-100 text-emerald-800 border-emerald-200";
    case "SELL":
      return "bg-red-100 text-red-800 border-red-200";
    case "HOLD":
      return "bg-amber-100 text-amber-800 border-amber-200";
    default:
      return "bg-stone-100 text-stone-700 border-stone-200";
  }
}

/**
 * Per-date decision log. Makes a HOLD-only run legible: every checkpoint is
 * shown with an explicit decision badge and the agents' reasoning, so the
 * run reads as "the agents decided HOLD, here's why" rather than a silent
 * row of zeros.
 */
function DecisionTimeline({ decisions }: { decisions: DecisionSummary }) {
  const { timeline, holdCount, buyCount, sellCount, total } = decisions;
  const allHold = total > 0 && holdCount === total;
  return (
    <div className="rounded-lg border border-stone-200 bg-white p-4">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <span className="text-[13px] font-semibold text-ink">
          {total} {plural(total, "evaluation", "evaluations")}
        </span>
        <span className="text-stone-300">·</span>
        <span
          className={cn(
            "rounded-full border px-2 py-0.5 text-[11.5px] font-medium",
            decisionBadgeClass("HOLD")
          )}
        >
          {holdCount} HOLD
        </span>
        <span
          className={cn(
            "rounded-full border px-2 py-0.5 text-[11.5px] font-medium",
            decisionBadgeClass("BUY")
          )}
        >
          {buyCount} BUY
        </span>
        <span
          className={cn(
            "rounded-full border px-2 py-0.5 text-[11.5px] font-medium",
            decisionBadgeClass("SELL")
          )}
        >
          {sellCount} SELL
        </span>
      </div>
      {allHold ? (
        <div className="mb-3 text-[12.5px] text-ink-2 leading-relaxed">
          The agents reached a <strong>HOLD</strong> verdict at every
          checkpoint — a deliberate decision to stay in cash, not a missing
          signal. No trade fired because no BUY cleared the debate and risk
          gates (and with no open position, a bearish view resolves to HOLD —
          EGX is long-only).
        </div>
      ) : null}
      <ul className="space-y-2">
        {timeline.map((d, i) => (
          <li
            key={`${d.date}-${i}`}
            className="rounded-md border border-stone-200 bg-white/60 p-2.5"
          >
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  "rounded border px-1.5 py-0.5 text-[10.5px] font-semibold",
                  decisionBadgeClass(d.decision)
                )}
              >
                {d.decision}
              </span>
              <span className="text-[12px] font-medium text-ink">{d.date}</span>
              {d.confidence !== null ? (
                <span className="text-[11px] text-stone-500">
                  conf {formatNumber(d.confidence * 100, 0)}%
                </span>
              ) : null}
            </div>
            <div className="mt-1 text-[12.5px] text-ink-2 leading-relaxed">
              {d.reasoning ?? "No rationale captured for this checkpoint."}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */

function ExitPlanDetail({
  plan,
  entryPrice,
  quantity,
}: {
  plan: ExitPlan;
  entryPrice?: number;
  quantity?: number;
}) {
  const tpTargets = plan.take_profit
    ? Object.entries(plan.take_profit).sort(([a], [b]) => a.localeCompare(b))
    : [];
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {/* Take profit */}
      <div>
        <div className="eyebrow text-stone-500 mb-2">Take-profit ladder</div>
        {tpTargets.length === 0 ? (
          <div className="text-[12.5px] text-stone-500">
            No take-profit ladder declared.
          </div>
        ) : (
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="text-left text-[10.5px] tracking-[0.15em] uppercase text-stone-500">
                <th className="py-1">Tier</th>
                <th className="py-1 text-right">Price (EGP)</th>
                <th className="py-1 text-right">% of position</th>
                {typeof entryPrice === "number" && (
                  <th className="py-1 text-right">Gain</th>
                )}
              </tr>
            </thead>
            <tbody>
              {tpTargets.map(([tier, t]) => {
                const price = typeof t?.price === "number" ? t.price : undefined;
                const gainPct =
                  typeof price === "number" && typeof entryPrice === "number" && entryPrice > 0
                    ? ((price - entryPrice) / entryPrice) * 100
                    : undefined;
                return (
                  <tr key={tier} className="border-t border-stone-100">
                    <td className="py-1.5 capitalize">{tier.replace("_", " ")}</td>
                    <td className="py-1.5 text-right mono">
                      {typeof price === "number" ? formatNumber(price) : "—"}
                    </td>
                    <td className="py-1.5 text-right mono">
                      {typeof t?.pct_of_position === "number"
                        ? `${t.pct_of_position}%`
                        : "—"}
                    </td>
                    {typeof entryPrice === "number" && (
                      <td
                        className={cn(
                          "py-1.5 text-right mono font-medium",
                          gainPct !== undefined && gainPct >= 0
                            ? "text-emerald-700"
                            : "text-rose-700"
                        )}
                      >
                        {gainPct !== undefined ? formatPercent(gainPct) : "—"}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Stop loss + time stop + invalidation */}
      <div className="space-y-3">
        <div>
          <div className="eyebrow text-stone-500 mb-1">Stop loss</div>
          {plan.stop_loss?.price !== undefined ? (
            <div className="text-[12.5px] text-ink-2 leading-relaxed">
              <div>
                <span className="text-stone-400">Price:</span>{" "}
                <span className="mono font-medium text-rose-700">
                  {formatNumber(plan.stop_loss.price)} EGP
                </span>
                {plan.stop_loss.type ? (
                  <span className="ml-2 text-stone-400">
                    ({plan.stop_loss.type})
                  </span>
                ) : null}
                {typeof entryPrice === "number" && entryPrice > 0 && (
                  <span className="ml-2 mono text-stone-500">
                    {formatPercent(
                      ((plan.stop_loss.price - entryPrice) / entryPrice) * 100
                    )}
                  </span>
                )}
              </div>
              {plan.stop_loss.note && (
                <div className="text-[12px] text-stone-500 mt-1">
                  {plan.stop_loss.note}
                </div>
              )}
              {typeof quantity === "number" &&
                typeof entryPrice === "number" &&
                typeof plan.stop_loss.price === "number" && (
                  <div className="text-[12px] text-stone-500 mt-1">
                    Max loss:{" "}
                    <span className="mono">
                      {formatNumber(
                        (entryPrice - plan.stop_loss.price) * quantity
                      )}{" "}
                      EGP
                    </span>
                  </div>
                )}
            </div>
          ) : (
            <div className="text-[12.5px] text-stone-500">
              No stop loss declared.
            </div>
          )}
        </div>

        {plan.time_stop && (
          <div>
            <div className="eyebrow text-stone-500 mb-1">Time stop</div>
            <div className="text-[12.5px] text-ink-2 leading-relaxed">
              {plan.time_stop}
            </div>
          </div>
        )}

        {plan.invalidation_triggers && plan.invalidation_triggers.length > 0 && (
          <div>
            <div className="eyebrow text-stone-500 mb-1">
              Thesis-invalidation triggers
            </div>
            <ul className="space-y-0.5 text-[12.5px] text-ink-2 leading-relaxed">
              {plan.invalidation_triggers.slice(0, 5).map((c, i) => (
                <li key={i} className="flex gap-2">
                  <span className="text-stone-400 mt-0.5">•</span>
                  <span>{c}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */

function ScenarioComparison({
  ticker,
  initial,
  finalEquity,
  strategyReturnPct,
  buyholdReturnPct,
  benchmarkReturnPct,
  benchmarkBlock,
}: {
  ticker: string;
  initial?: number;
  finalEquity?: number;
  strategyReturnPct?: number;
  buyholdReturnPct?: number;
  benchmarkReturnPct?: number;
  benchmarkBlock?: BenchmarkBlock;
}) {
  // Build the three scenarios so we can rank them deterministically.
  // The Strategy row always exists. Same-ticker B&H is computed by the
  // backtester whenever there's at least one trading day. EGX30 is only
  // present when the local CSV (or yfinance) returned data.
  type Row = {
    key: string;
    title: string;
    subtitle: string;
    returnPct?: number;
    finalEquity?: number;
    pnlAbs?: number;
    note?: string;
    isStrategy: boolean;
  };

  const equityFor = (retPct?: number): number | undefined =>
    typeof retPct === "number" && typeof initial === "number"
      ? initial * (1 + retPct / 100)
      : undefined;

  const strategyFinal = finalEquity;
  const strategyPnl =
    typeof strategyFinal === "number" && typeof initial === "number"
      ? strategyFinal - initial
      : undefined;

  const buyholdFinal = equityFor(buyholdReturnPct);
  const buyholdPnl =
    typeof buyholdFinal === "number" && typeof initial === "number"
      ? buyholdFinal - initial
      : undefined;

  const benchFinal = equityFor(benchmarkReturnPct);
  const benchPnl =
    typeof benchFinal === "number" && typeof initial === "number"
      ? benchFinal - initial
      : undefined;

  const rows: Row[] = [
    {
      key: "strategy",
      title: "Multi-agent strategy",
      subtitle: `Active trades on ${ticker}`,
      returnPct: strategyReturnPct,
      finalEquity: strategyFinal,
      pnlAbs: strategyPnl,
      isStrategy: true,
    },
    {
      key: "buyhold",
      title: `Buy & hold ${ticker}`,
      subtitle: "Same ticker, no rebalancing",
      returnPct: buyholdReturnPct,
      finalEquity: buyholdFinal,
      pnlAbs: buyholdPnl,
      note:
        typeof buyholdReturnPct === "number"
          ? undefined
          : "Insufficient price data for this window.",
      isStrategy: false,
    },
    {
      key: "egx30",
      title: "EGX 30 buy & hold",
      subtitle: "Index proxy for the same window",
      returnPct: benchmarkReturnPct,
      finalEquity: benchFinal,
      pnlAbs: benchPnl,
      note:
        typeof benchmarkReturnPct === "number"
          ? undefined
          : benchmarkBlock?.error
          ? `EGX30 data unavailable: ${benchmarkBlock.error}`
          : "EGX30 benchmark not available — drop an `EGX 30 Historical Data.csv` in the project root to enable.",
      isStrategy: false,
    },
  ];

  // Rank by absolute EGP P&L when we have it (more intuitive than %).
  const ranked = rows.filter((r) => typeof r.pnlAbs === "number");
  ranked.sort((a, b) => (b.pnlAbs ?? 0) - (a.pnlAbs ?? 0));
  const winnerKey = ranked.length > 0 ? ranked[0].key : null;
  const strategyRow = rows.find((r) => r.key === "strategy");
  const strategyWon =
    winnerKey === "strategy" &&
    typeof strategyRow?.pnlAbs === "number" &&
    strategyRow.pnlAbs > 0;

  // Verdict line for the defense slide.
  const verdict = (() => {
    if (ranked.length < 2) {
      return "Not enough alternative scenarios with data to compare against.";
    }
    const top = ranked[0];
    const second = ranked[1];
    const margin =
      typeof top.pnlAbs === "number" && typeof second.pnlAbs === "number"
        ? top.pnlAbs - second.pnlAbs
        : 0;
    if (top.isStrategy && margin > 0) {
      return `The multi-agent strategy outperformed every benchmark by +${formatNumber(margin)} EGP over this window.`;
    }
    if (top.isStrategy) {
      return `The multi-agent strategy tied for best (margin ${formatNumber(margin)} EGP).`;
    }
    return `${top.title} delivered the best outcome; the multi-agent strategy left ${formatNumber(margin)} EGP on the table versus this alternative.`;
  })();

  return (
    <div className="space-y-4">
      <div
        className={cn(
          "card-elevated px-6 py-5 flex items-start gap-4",
          strategyWon ? "border-emerald-200" : "border-stone-200"
        )}
      >
        <div
          className={cn(
            "h-10 w-10 rounded-xl border flex items-center justify-center shrink-0",
            strategyWon
              ? "bg-emerald-50 text-emerald-700 border-emerald-200"
              : "bg-amber-50 text-amber-700 border-amber-200"
          )}
        >
          {strategyWon ? (
            <TrendingUp className="h-5 w-5" />
          ) : (
            <AlertCircle className="h-5 w-5" />
          )}
        </div>
        <div className="flex-1">
          <div className="eyebrow text-stone-500 mb-1">Defense verdict</div>
          <div className="text-[14.5px] text-ink leading-relaxed">{verdict}</div>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        {rows.map((row) => {
          const isWinner = row.key === winnerKey;
          const pct = row.returnPct;
          const profit = typeof pct === "number" && pct >= 0;
          const hasData = typeof pct === "number";
          return (
            <div
              key={row.key}
              className={cn(
                "card-elevated p-5 relative overflow-hidden flex flex-col",
                isWinner ? "ring-2 ring-emerald-300/60" : ""
              )}
            >
              {isWinner && (
                <div className="absolute top-2 right-2 inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-emerald-50 border border-emerald-200 text-emerald-700 text-[10px] font-semibold tracking-wide uppercase">
                  <ArrowUpRight className="h-3 w-3" />
                  Best outcome
                </div>
              )}
              <div className="eyebrow text-stone-500">{row.title}</div>
              <div className="text-[12px] text-stone-400 mt-0.5">{row.subtitle}</div>

              {hasData ? (
                <>
                  <div
                    className={cn(
                      "mt-4 display-num text-[34px] leading-none font-semibold",
                      profit ? "text-emerald-700" : "text-rose-700"
                    )}
                  >
                    {formatPercent(pct as number)}
                  </div>
                  <div className="text-[13px] text-ink-2 mt-2">
                    {row.pnlAbs !== undefined ? (
                      <>
                        <span
                          className={cn(
                            "font-medium mono",
                            (row.pnlAbs ?? 0) >= 0
                              ? "text-emerald-700"
                              : "text-rose-700"
                          )}
                        >
                          {(row.pnlAbs ?? 0) >= 0 ? "+" : ""}
                          {formatNumber(row.pnlAbs as number)} EGP
                        </span>
                        <span className="text-stone-500 ml-1.5">P&L</span>
                      </>
                    ) : null}
                  </div>
                  <div className="text-[12px] text-stone-500 mt-3 mono">
                    Final equity:{" "}
                    {row.finalEquity !== undefined
                      ? `${formatNumber(row.finalEquity)} EGP`
                      : "—"}
                  </div>
                </>
              ) : (
                <div className="mt-4 text-[13px] text-stone-500 leading-relaxed">
                  {row.note ?? "Data unavailable for this window."}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="card overflow-hidden grain">
      <div className="px-10 py-14 text-center">
        <div className="inline-flex h-14 w-14 items-center justify-center rounded-2xl bg-stone-900 text-white mb-5">
          <Wallet className="h-6 w-6" />
        </div>
        <h3 className="display text-[22px] font-semibold text-ink">
          No backtest yet.
        </h3>
        <p className="text-[14px] text-ink-3 mt-2 max-w-md mx-auto leading-relaxed">
          Configure the run above and press <strong>Run backtest</strong>. The
          full agent retrospective and trade log will land here.
        </p>
      </div>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="space-y-8">
      <div className="rounded-3xl skeleton h-[300px] border border-stone-200" />
      <div className="grid gap-5 md:grid-cols-2">
        <AgentCardSkeleton tone="bull" />
        <AgentCardSkeleton tone="bear" />
        <AgentCardSkeleton tone="judge" />
        <AgentCardSkeleton tone="risk" />
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */

// Treat the backtester's default placeholder as "no reasoning available".
const PLACEHOLDER_REASONS = new Set(["Standard execution", "", "N/A"]);

function meaningfulReason(r: unknown): string | null {
  if (typeof r !== "string") return null;
  const s = r.trim();
  if (!s || PLACEHOLDER_REASONS.has(s)) return null;
  return s;
}

function deriveAgentInsights(
  trades: BacktestTrade[],
  auditLog: BacktestAuditEntry[] = []
) {
  const buys = trades.filter((t) => (t.action || "").toUpperCase() === "BUY");
  const sells = trades.filter((t) => (t.action || "").toUpperCase() === "SELL");

  const buyConf = avg(buys.map((t) => Number(t.confidence) || 0));
  const sellConf = avg(sells.map((t) => Number(t.confidence) || 0));
  const allConf = avg(trades.map((t) => Number(t.confidence) || 0));

  const realised = trades.reduce(
    (acc, t) => acc + (typeof t.pnl === "number" ? t.pnl : 0),
    0
  );
  const sellRealised = sells.reduce(
    (acc, t) => acc + (typeof t.pnl === "number" ? t.pnl : 0),
    0
  );
  const wins = trades.filter(
    (t) => typeof t.pnl === "number" && t.pnl > 0
  ).length;

  // Hit rate = fraction of CLOSED trades that booked a positive PnL.
  // We only count sells because longs that haven't been closed yet have
  // no realised outcome — counting them dilutes the figure to "0%".
  const closed = sells.length;
  const hitRate = closed > 0 ? `${Math.round((wins / closed) * 100)}%` : "—";

  // ── Bull ─────────────────────────────────────────────────────────────
  const lastBuy = buys[buys.length - 1];
  const lastBuyReason = lastBuy ? meaningfulReason(lastBuy.reasoning) : null;
  const bullSummary = (() => {
    if (buys.length === 0) {
      return "No long entries cleared the debate threshold during this window. The bull case did not reach actionable conviction.";
    }
    const conv = `Average conviction ${formatNumber(buyConf * 100, 1)}%.`;
    const entryLine = lastBuy
      ? ` Last entry on ${lastBuy.date} at ${typeof lastBuy.price === "number" ? formatNumber(lastBuy.price) : "—"} EGP, sizing ${lastBuy.quantity ?? lastBuy.shares ?? "—"} shares.`
      : "";
    const reasonLine = lastBuyReason
      ? ` Thesis: ${lastBuyReason}`
      : sells.length === 0
      ? " The position is still open — no realised PnL booked yet."
      : "";
    return `Pushed for ${buys.length} long ${plural(buys.length, "entry", "entries")}. ${conv}${entryLine}${reasonLine}`;
  })();

  // ── Bear ─────────────────────────────────────────────────────────────
  const lastSell = sells[sells.length - 1];
  const lastSellReason = lastSell ? meaningfulReason(lastSell.reasoning) : null;
  const bearSummary = (() => {
    if (sells.length === 0 && buys.length === 0) {
      return "The bear case stayed muted alongside the bull case — no exits or entries were triggered. The portfolio sat in cash for the full window.";
    }
    if (sells.length === 0) {
      return `The bear case did not prevail this window: the agent held long through every checkpoint without trimming. ${buys.length} long ${plural(buys.length, "entry", "entries")} stayed open, so no downside was realised against the bull thesis.`;
    }
    const reasonLine = lastSellReason ? ` Last cautionary thesis: ${lastSellReason}` : "";
    return `Triggered ${sells.length} ${plural(sells.length, "exit", "exits")} — average conviction ${formatNumber(sellConf * 100, 1)}%. Realised PnL on exits: ${formatNumber(sellRealised)} EGP.${reasonLine}`;
  })();

  // ── Per-date decision timeline (covers HOLD dates too) ───────────────
  // A HOLD is a real, reasoned verdict — capture every evaluation so the UI
  // never renders an unexplained row of zeros.
  const decisions = auditLog
    .map((a) => ({
      date: typeof a.date === "string" ? a.date : "",
      decision: (String(a.parsed_decision || "").toUpperCase() || "—") as string,
      confidence: typeof a.confidence === "number" ? a.confidence : null,
      reasoning:
        meaningfulReason(a.judge_rationale) ?? meaningfulReason(a.reasoning),
    }))
    .filter((d) => d.date);
  const holdCount = decisions.filter((d) => d.decision === "HOLD").length;
  const buyDecisions = decisions.filter((d) => d.decision === "BUY").length;
  const sellDecisions = decisions.filter((d) => d.decision === "SELL").length;
  const latestJudgeRationale =
    [...decisions].reverse().find((d) => d.reasoning)?.reasoning ?? null;

  // ── Judge ────────────────────────────────────────────────────────────
  const judgeSummary =
    trades.length > 0
      ? `Approved ${trades.length} executed ${plural(trades.length, "decision", "decisions")} (${buys.length} buy / ${sells.length} sell) at mean conviction ${formatNumber(
          allConf * 100,
          1
        )}%. Net realised PnL across the window: ${formatNumber(realised)} EGP${sells.length === 0 ? " (open positions excluded)" : ""}.`
      : decisions.length > 0
      ? `The judge evaluated ${decisions.length} checkpoint${plural(
          decisions.length,
          "",
          "s"
        )} and returned ${
          holdCount === decisions.length
            ? `HOLD on all ${decisions.length}`
            : `${holdCount} HOLD / ${buyDecisions} BUY / ${sellDecisions} SELL`
        } — staying in cash is a deliberate verdict, not an absence of one.${
          latestJudgeRationale ? ` Latest rationale: ${latestJudgeRationale}` : ""
        }`
      : "The judge held flat — none of the candidate signals passed the debate threshold during this window.";

  // ── Risk ─────────────────────────────────────────────────────────────
  const riskSummary =
    trades.length > 0
      ? `All ${trades.length} fills respected the long-only constraint, the ±10% daily price-limit and the 10% ADV cap. Average position confidence of ${formatNumber(
          allConf * 100,
          1
        )}% drove the sizing through the risk gate.`
      : decisions.length > 0
      ? `Risk gate reviewed ${decisions.length} ${plural(
          decisions.length,
          "decision",
          "decisions"
        )} and cleared none for execution — the portfolio stayed 100% in cash. With no open position a SELL is not actionable (EGX long-only), so a bearish verdict resolves to HOLD.`
      : "Risk gate held the portfolio in cash. No buy passed the deterministic veto plus debate consensus during this window.";

  // ── Real agent text from audit_log ───────────────────────────────────
  // Walk newest-first and pick the latest non-empty payload for each agent.
  const reversed = [...auditLog].reverse();
  const lastBullSummary =
    (reversed.find((a) => a.bull_thesis_summary)?.bull_thesis_summary as
      | ThesisSummary
      | null
      | undefined) || null;
  const lastBearSummary =
    (reversed.find((a) => a.bear_thesis_summary)?.bear_thesis_summary as
      | ThesisSummary
      | null
      | undefined) || null;
  const lastRiskText =
    (reversed.find(
      (a) => typeof a.risk_judge_text === "string" && a.risk_judge_text.trim().length > 0
    )?.risk_judge_text as string | null | undefined) || null;
  // Did the bear researcher produce a structured thesis on ANY date?
  const bearEverPresent = auditLog.some((a) => a.bear_thesis_present === true);
  const auditDatesWithSignal = auditLog.filter(
    (a) => typeof a.parsed_decision === "string"
  ).length;

  return {
    bull: {
      count: buys.length,
      confChip: buys.length ? `${formatNumber(buyConf * 100, 0)}%` : "—",
      hitChip: hitRate,
      summary: bullSummary,
      thesis: lastBullSummary,
    },
    bear: {
      count: sells.length,
      confChip: sells.length ? `${formatNumber(sellConf * 100, 0)}%` : "—",
      realisedChip:
        sells.length > 0
          ? `${sellRealised >= 0 ? "+" : ""}${formatNumber(sellRealised)} EGP`
          : "—",
      summary: bearSummary,
      thesis: lastBearSummary,
      everPresent: bearEverPresent,
      datesEvaluated: auditDatesWithSignal,
    },
    judge: {
      avgConfidence: trades.length ? `${formatNumber(allConf * 100, 0)}%` : "—",
      realisedChip: `${realised >= 0 ? "+" : ""}${formatNumber(realised)} EGP`,
      summary: judgeSummary,
    },
    risk: {
      chip: trades.length > 0 ? "Within EGX limits" : "No exposure taken",
      vetoRate: trades.length > 0 ? "0%" : "100%",
      summary: riskSummary,
      judgeText: lastRiskText,
    },
    decisions: {
      timeline: decisions,
      holdCount,
      buyCount: buyDecisions,
      sellCount: sellDecisions,
      total: decisions.length,
    },
  };
}

function plural(n: number, one: string, many: string) {
  return n === 1 ? one : many;
}

function avg(xs: number[]) {
  if (!xs.length) return 0;
  return xs.reduce((a, b) => a + b, 0) / xs.length;
}
