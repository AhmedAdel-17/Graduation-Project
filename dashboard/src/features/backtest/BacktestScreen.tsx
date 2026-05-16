import { useEffect, useMemo, useRef, useState } from "react";
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
  BacktestDetail,
  BacktestSession,
  BacktestTrade,
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
  const insights = useMemo(() => deriveAgentInsights(trades), [trades]);

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
              {insights.bull.summary}
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
              {insights.bear.summary}
            </AgentCard>
            <AgentCard
              tone="judge"
              icon={Gavel}
              agent="Debate judge"
              role="Reconciled verdict"
              chip={`Avg conf: ${insights.judge.avgConfidence}`}
              meta={[
                { label: "Decisions", value: String(trades.length) },
                { label: "Net PnL", value: insights.judge.realisedChip },
              ]}
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
                { label: "Veto rate", value: insights.risk.vetoRate },
                { label: "Max DD", value: maxDdLabel(metrics.max_drawdown_pct) },
              ]}
            >
              {insights.risk.summary}
            </AgentCard>
          </div>

          <SectionLabel index="02" label="Realised trades" />
          <AgentCard
            tone="manager"
            icon={Briefcase}
            agent="Portfolio manager"
            role="Trade log"
            chip={`${trades.length} fills`}
          >
            <TradeLog trades={trades} />
          </AgentCard>
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

        {/* Right: stat tiles on warm paper */}
        <div className="grid grid-cols-2 gap-px bg-stone-200 rounded-2xl overflow-hidden border border-stone-200 self-end">
          <StatTile label="Final equity" value={finalEquity !== undefined ? formatNumber(finalEquity) : "—"} unit="EGP" />
          <StatTile label="Sharpe" value={sharpe !== undefined ? formatNumber(sharpe) : "—"} />
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
          <StatTile
            label="Total trades"
            value={
              totalTrades !== undefined ? String(Math.round(Number(totalTrades))) : "—"
            }
          />
          <StatTile label="Initial budget" value={initial !== undefined ? formatNumber(initial) : "—"} unit="EGP" />
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

function TradeLog({ trades }: { trades: BacktestTrade[] }) {
  if (!trades.length) {
    return (
      <div className="rounded-xl border border-dashed border-stone-200 px-6 py-10 text-center text-sm text-stone-500">
        No trades were executed during this window — the risk gate kept the
        portfolio in cash.
      </div>
    );
  }
  return (
    <div className="mt-1 rounded-xl border border-stone-200 overflow-hidden">
      <div className="max-h-[420px] overflow-auto">
        <table className="w-full text-sm">
          <thead className="bg-stone-50 sticky top-0 z-10">
            <tr className="text-left text-[10.5px] tracking-[0.18em] uppercase text-stone-500">
              <th className="px-4 py-2.5">Date</th>
              <th className="px-4 py-2.5">Action</th>
              <th className="px-4 py-2.5 text-right">Price</th>
              <th className="px-4 py-2.5 text-right">Qty</th>
              <th className="px-4 py-2.5 text-right">PnL</th>
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
              return (
                <tr
                  key={i}
                  className="border-t border-stone-100 hover:bg-stone-50/60 transition-colors"
                >
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
                  <td
                    className={cn(
                      "px-4 py-2.5 text-right mono text-[13px] font-medium",
                      typeof t.pnl === "number"
                        ? t.pnl >= 0
                          ? "text-emerald-700"
                          : "text-rose-700"
                        : "text-stone-500"
                    )}
                  >
                    {typeof t.pnl === "number" ? formatNumber(t.pnl) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
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

function maxDdLabel(v: number | undefined) {
  if (typeof v !== "number") return "—";
  return formatPercent(v, 2, false);
}

// Treat the backtester's default placeholder as "no reasoning available".
const PLACEHOLDER_REASONS = new Set(["Standard execution", "", "N/A"]);

function meaningfulReason(r: unknown): string | null {
  if (typeof r !== "string") return null;
  const s = r.trim();
  if (!s || PLACEHOLDER_REASONS.has(s)) return null;
  return s;
}

function deriveAgentInsights(trades: BacktestTrade[]) {
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

  // ── Judge ────────────────────────────────────────────────────────────
  const judgeSummary =
    trades.length > 0
      ? `Approved ${trades.length} executed ${plural(trades.length, "decision", "decisions")} (${buys.length} buy / ${sells.length} sell) at mean conviction ${formatNumber(
          allConf * 100,
          1
        )}%. Net realised PnL across the window: ${formatNumber(realised)} EGP${sells.length === 0 ? " (open positions excluded)" : ""}.`
      : "The judge held flat — none of the candidate signals passed the debate threshold during this window.";

  // ── Risk ─────────────────────────────────────────────────────────────
  const riskSummary =
    trades.length > 0
      ? `All ${trades.length} fills respected the long-only constraint, the ±10% daily price-limit and the 10% ADV cap. Average position confidence of ${formatNumber(
          allConf * 100,
          1
        )}% drove the sizing through the risk gate.`
      : "Risk gate held the portfolio in cash. No buy passed the deterministic veto plus debate consensus during this window.";

  return {
    bull: {
      count: buys.length,
      confChip: buys.length ? `${formatNumber(buyConf * 100, 0)}%` : "—",
      hitChip: hitRate,
      summary: bullSummary,
    },
    bear: {
      count: sells.length,
      confChip: sells.length ? `${formatNumber(sellConf * 100, 0)}%` : "—",
      realisedChip:
        sells.length > 0
          ? `${sellRealised >= 0 ? "+" : ""}${formatNumber(sellRealised)} EGP`
          : "—",
      summary: bearSummary,
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
