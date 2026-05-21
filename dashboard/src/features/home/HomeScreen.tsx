import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  Activity,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Briefcase,
  Check,
  Gauge as GaugeIcon,
  Gavel,
  Loader2,
  Scale,
  Sparkles,
  Target,
  TrendingDown,
  TrendingUp,
  Zap,
} from "lucide-react";
import { useRunPrediction, useRunFullPipeline } from "../../hooks/usePrediction";
import { AgentCard, AgentCardSkeleton } from "../shared/AgentCard";
import { TickerPicker } from "../shared/TickerPicker";
import { PriceChart } from "../../components/charts/PriceChart";
import { cn, formatNumber, formatPercent } from "../../lib/utils";
import { getTickerMeta } from "../../data/egxTickerMeta";
import { TickerLogo } from "../../components/ui/TickerLogo";
import { MarketIndicesBar } from "./MarketIndicesBar";
import { PivotLevels } from "./PivotLevels";
import type { StockBar } from "../../services/api/types";

type Dir = "up" | "down" | "flat";

function dirOf(signal: string): Dir {
  if (signal === "BUY" || signal === "STRONG_BUY") return "up";
  if (signal === "SELL" || signal === "STRONG_SELL") return "down";
  return "flat";
}

function toNum(v: unknown): number | undefined {
  if (v === null || v === undefined || v === "") return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

export function HomeScreen() {
  const [ticker, setTicker] = useState("COMI.CA");
  const runPrediction = useRunPrediction();
  const runFullPipeline = useRunFullPipeline();

  // Show whichever result is most recent (full vs quick).
  const quickSubmittedAt = runPrediction.submittedAt ?? 0;
  const fullSubmittedAt = runFullPipeline.submittedAt ?? 0;
  const result =
    fullSubmittedAt >= quickSubmittedAt
      ? runFullPipeline.data ?? runPrediction.data
      : runPrediction.data ?? runFullPipeline.data;

  const rec = result?.recommendation;
  const price = result?.price;
  const indicators = result?.indicators;
  const current = price?.current;
  const target = toNum(rec?.target_price);
  const stop = toNum(rec?.stop_loss);
  const history = (result?.price_history as StockBar[] | undefined) ?? [];

  const upside = useMemo(() => {
    if (typeof current === "number" && typeof target === "number" && current > 0) {
      return ((target - current) / current) * 100;
    }
    return undefined;
  }, [current, target]);

  const downside = useMemo(() => {
    if (typeof current === "number" && typeof stop === "number" && current > 0) {
      return ((stop - current) / current) * 100;
    }
    return undefined;
  }, [current, stop]);

  const riskReward = useMemo(() => {
    if (upside !== undefined && downside !== undefined && downside !== 0) {
      return Math.abs(upside / downside);
    }
    return undefined;
  }, [upside, downside]);

  const signal = (rec?.signal || "").toUpperCase();
  const confidence = (rec?.confidence || "").toUpperCase();
  const isQuickLoading = runPrediction.isPending;
  const isFullLoading = runFullPipeline.isPending;
  const isLoading = isQuickLoading || isFullLoading;
  const hasResult = !!result && !result.error;

  async function handleRun() {
    try {
      const res = await runPrediction.mutateAsync(ticker);
      if (res?.error) {
        toast.error(res.error);
        return;
      }
      toast.success(`Analysis complete · ${res?.recommendation?.signal ?? "—"}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Analysis failed");
    }
  }

  async function handleRunFull() {
    try {
      toast.info("Full pipeline started — this takes 3-8 minutes");
      const res = await runFullPipeline.mutateAsync(ticker);
      if (res?.error) {
        toast.error(res.error);
        return;
      }
      if (res?.llm_error) {
        toast.warning(`Pipeline completed with errors: ${res.llm_error}`);
      } else {
        toast.success(`Full pipeline complete · ${res?.recommendation?.signal ?? "—"}`);
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Full pipeline failed");
    }
  }

  return (
    <div className="space-y-8">
      {/* ─── Market indices strip ────────────────────────────────── */}
      <MarketIndicesBar />

      {/* ─── Page header ─────────────────────────────────────────── */}
      <header className="flex items-end justify-between gap-6 flex-wrap">
        <div>
          <div className="eyebrow mb-3">Home · EGX research desk</div>
          <h1 className="display text-[36px] md:text-[42px] font-semibold leading-[1.05] text-ink">
            A second opinion,
            <br />
            from <span className="italic">four</span> minds at once.
          </h1>
          <p className="text-[14px] text-ink-3 mt-3 max-w-xl leading-relaxed">
            Run a full bull, bear, judge and portfolio review on any EGX-30
            ticker. Every thesis is traceable, sized to EGX risk limits, and
            ready in seconds.
          </p>
        </div>
      </header>

      {/* ─── Run bar ─────────────────────────────────────────────── */}
      <div>
        <div className="card-elevated p-2 flex items-center gap-2 flex-wrap sm:flex-nowrap">
          <div className="flex-1 min-w-[200px]">
            <TickerPicker value={ticker} onChange={setTicker} />
          </div>
          <button
            onClick={handleRun}
            disabled={isLoading}
            className={cn(
              "shrink-0 inline-flex items-center gap-2 h-12 px-5 rounded-xl",
              "bg-white text-ink border border-stone-200 hover:bg-stone-50 text-[14px] font-medium",
              "dark:bg-[var(--paper)] dark:border-[var(--hairline)] dark:hover:bg-white/5",
              "transition-all duration-200",
              "disabled:opacity-60 disabled:cursor-not-allowed"
            )}
          >
            {isQuickLoading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Quick…
              </>
            ) : (
              <>
                <Zap className="h-4 w-4" />
                Quick analysis
              </>
            )}
          </button>
          <button
            onClick={handleRunFull}
            disabled={isLoading}
            className={cn(
              "shrink-0 inline-flex items-center gap-2 h-12 px-5 rounded-xl",
              "bg-stone-900 hover:bg-stone-800 text-white text-[14px] font-medium",
              "transition-all duration-200 shadow-[0_4px_12px_-2px_rgba(0,0,0,0.18)]",
              "disabled:opacity-60 disabled:cursor-not-allowed"
            )}
          >
            {isFullLoading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Running full pipeline…
              </>
            ) : (
              <>
                Run full pipeline
                <ArrowRight className="h-4 w-4" />
              </>
            )}
          </button>
        </div>
        {/* Visible mode legend — replaces hover-only tooltips */}
        <div className="mt-2.5 flex flex-wrap items-center gap-x-6 gap-y-1.5 px-1.5 text-[12px] text-ink-3">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-stone-300" />
            <strong className="font-medium text-ink-2">Quick analysis</strong>
            <span>— single-LLM read, ready in ~30 seconds</span>
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-stone-900" />
            <strong className="font-medium text-ink-2">Run full pipeline</strong>
            <span>— four-agent debate, 3–8 minutes</span>
          </span>
        </div>
      </div>

      {/* ─── Empty ───────────────────────────────────────────────── */}
      {!isLoading && !hasResult && <EmptyHero />}

      {/* ─── Loading ─────────────────────────────────────────────── */}
      {isLoading && <LoadingHero mode={isFullLoading ? "full" : "quick"} />}

      {/* ─── Results ─────────────────────────────────────────────── */}
      {hasResult && (
        <div className="space-y-6">
          <QuoteHeader
            ticker={result?.ticker || ticker}
            name={(result?.name as string | undefined) || ticker}
            current={current}
            dailyChange={toNum(price?.daily_change)}
            weeklyChange={toNum(price?.weekly_change)}
            signal={signal}
          />

          {/* Chart + verdict — the trading-terminal core */}
          <div className="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
            <ChartPanel
              bars={history}
              current={current}
              target={target}
              stop={stop}
            />
            <VerdictPanel
              signal={signal}
              confidence={confidence}
              current={current}
              target={target}
              stop={stop}
              upside={upside}
              downside={downside}
              risk={rec?.risk as string | undefined}
            />
          </div>

          {/* Key levels — quote stats strip */}
          <KeyStats
            current={current}
            target={target}
            stop={stop}
            upside={upside}
            downside={downside}
            riskReward={riskReward}
            rsi={toNum(indicators?.rsi)}
            trend={indicators?.trend}
          />

          {/* Support & resistance pivots */}
          <PivotLevels bars={history} current={current} />

          {/* Bull + Bear */}
          <SectionLabel index="01" label="Adversarial research" />
          <div className="grid gap-5 md:grid-cols-2">
            <AgentCard
              tone="bull"
              icon={TrendingUp}
              agent="Bull researcher"
              role="The constructive case"
              chip="Long thesis"
              meta={
                target && current
                  ? [
                      { label: "Implied upside", value: formatPercent(upside ?? 0) },
                      { label: "Target", value: `${formatNumber(target)} EGP` },
                    ]
                  : undefined
              }
            >
              {rec?.bull_case || "No bullish thesis was returned for this run."}
            </AgentCard>
            <AgentCard
              tone="bear"
              icon={TrendingDown}
              agent="Bear researcher"
              role="The cautionary case"
              chip="Risk-off thesis"
              meta={
                stop && current
                  ? [
                      {
                        label: "Downside to stop",
                        value: formatPercent(downside ?? 0),
                      },
                      { label: "Stop", value: `${formatNumber(stop)} EGP` },
                    ]
                  : undefined
              }
            >
              {rec?.bear_case || "No bearish thesis was returned for this run."}
            </AgentCard>
          </div>

          {/* Judge */}
          <SectionLabel index="02" label="Debate resolution" />
          <AgentCard
            tone="judge"
            icon={Gavel}
            agent="Debate judge"
            role="Research manager verdict"
            chip={signal ? `Verdict: ${signal}` : "Verdict"}
            meta={[
              { label: "Signal", value: signal || "—" },
              { label: "Confidence", value: confidence || "—" },
              {
                label: "Risk profile",
                value: (rec?.risk as string | undefined) || "—",
              },
            ]}
          >
            {rec?.neutral_case ||
              rec?.rationale ||
              "No reconciled verdict was returned for this run."}
          </AgentCard>

          {/* Portfolio manager */}
          <SectionLabel index="03" label="Execution plan" />
          <AgentCard
            tone="manager"
            icon={Briefcase}
            agent="Portfolio manager"
            role="Trade construction"
            chip={confidence ? `${confidence} conviction` : "Trade plan"}
          >
            <ExecutionPlan
              signal={signal}
              current={current}
              target={target}
              stop={stop}
              risk={rec?.risk}
              rationale={rec?.recommendation || rec?.rationale}
            />
          </AgentCard>
        </div>
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════════════
   Quote header — instrument bar (symbol · price · change · signal)
   ════════════════════════════════════════════════════════════════════════════ */

function QuoteHeader({
  ticker,
  name,
  current,
  dailyChange,
  weeklyChange,
  signal,
}: {
  ticker: string;
  name: string;
  current?: number;
  dailyChange?: number;
  weeklyChange?: number;
  signal: string;
  }) {
  const meta = getTickerMeta(ticker);
  const dir = dirOf(signal);
  const dayDir: Dir =
    dailyChange === undefined ? "flat" : dailyChange >= 0 ? "up" : "down";
  const DayArrow =
    dayDir === "up" ? ArrowUpRight : dayDir === "down" ? ArrowDownRight : ArrowRight;
  const dayClass =
    dayDir === "up"
      ? "text-emerald-700 bg-emerald-50 border-emerald-200"
      : dayDir === "down"
      ? "text-rose-700 bg-rose-50 border-rose-200"
      : "text-stone-600 bg-stone-50 border-stone-200";
  const SigArrow =
    dir === "up" ? ArrowUpRight : dir === "down" ? ArrowDownRight : ArrowRight;
  const sigClass =
    dir === "up"
      ? "bg-emerald-600 text-white border-emerald-600"
      : dir === "down"
      ? "bg-rose-600 text-white border-rose-600"
      : "bg-stone-800 text-white border-stone-800";

  return (
    <section className="card-elevated grain overflow-hidden anim-fade-up">
      <div className="flex flex-wrap items-center gap-y-5 gap-x-8 p-5 md:p-6">
        {/* Identity */}
        <div className="flex items-center gap-4 min-w-0 flex-1">
          <TickerLogo ticker={ticker} size="lg" />
          <div className="min-w-0">
            <div className="flex items-center gap-2.5 flex-wrap">
              <span className="display text-[26px] font-semibold leading-none tracking-tight text-ink">
                {meta.symbol}
              </span>
              <span className="text-[10.5px] font-medium tracking-wider uppercase text-stone-500 border border-stone-200 rounded-full px-2 py-0.5">
                {meta.sector}
              </span>
            </div>
            <div className="text-[12.5px] text-ink-2 mt-1.5 leading-snug truncate">
              {meta.nameEn || name}
            </div>
            {meta.nameAr && meta.nameAr !== meta.symbol && (
              <div
                className="text-[12px] text-ink-3 leading-snug truncate"
                dir="rtl"
              >
                {meta.nameAr}
              </div>
            )}
          </div>
        </div>

        {/* Price + change */}
        <div className="flex items-end gap-4">
          <div>
            <div className="eyebrow text-stone-500 mb-1">Last price</div>
            <div className="flex items-baseline gap-1.5">
              <span className="display-num text-[40px] leading-none font-semibold text-ink">
                {current !== undefined ? formatNumber(current) : "—"}
              </span>
              <span className="text-[13px] text-stone-500 font-medium">EGP</span>
            </div>
          </div>
          <div className="flex flex-col gap-1.5 pb-0.5">
            <span
              className={cn(
                "inline-flex items-center gap-1 px-2 py-1 rounded-md border text-[12px] font-semibold mono",
                dayClass
              )}
            >
              <DayArrow className="h-3.5 w-3.5" />
              {dailyChange !== undefined ? formatPercent(dailyChange) : "—"}
              <span className="font-normal opacity-70">1D</span>
            </span>
            <span className="inline-flex items-center gap-1.5 text-[11.5px] text-ink-3 mono pl-0.5">
              {weeklyChange !== undefined ? formatPercent(weeklyChange) : "—"}
              <span className="opacity-70">1W</span>
            </span>
          </div>
        </div>

        {/* Signal */}
        <div className="flex flex-col items-end gap-1.5">
          <div className="eyebrow text-stone-500">Agent verdict</div>
          <span
            className={cn(
              "inline-flex items-center gap-1.5 px-4 py-2 rounded-xl text-[15px] font-semibold uppercase tracking-wide border",
              sigClass
            )}
          >
            <SigArrow className="h-4 w-4" />
            {signal || "—"}
          </span>
        </div>
      </div>
    </section>
  );
}

/* ════════════════════════════════════════════════════════════════════════════
   Chart panel — candlesticks with analyst target / stop overlays
   ════════════════════════════════════════════════════════════════════════════ */

function ChartPanel({
  bars,
  current,
  target,
  stop,
}: {
  bars: StockBar[];
  current?: number;
  target?: number;
  stop?: number;
}) {
  const priceLines = useMemo(() => {
    const lines: { price: number; color: string; title: string }[] = [];
    if (typeof target === "number") {
      lines.push({ price: target, color: "#059669", title: "Target" });
    }
    if (typeof stop === "number") {
      lines.push({ price: stop, color: "#e11d48", title: "Stop" });
    }
    return lines;
  }, [target, stop]);

  const hasBars = bars && bars.length > 0;

  return (
    <section className="card overflow-hidden anim-fade-up">
      <div className="flex items-center justify-between gap-3 px-5 pt-4 pb-3 border-b border-stone-200/80 dark:border-[var(--hairline)]">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-stone-500" />
          <span className="text-[14px] font-semibold text-ink">Price action</span>
          {hasBars && (
            <span className="text-[11.5px] text-ink-3">
              · last {bars.length} sessions
            </span>
          )}
        </div>
        <div className="flex items-center gap-3.5 text-[11px] text-ink-3">
          {typeof target === "number" && (
            <span className="inline-flex items-center gap-1.5">
              <span className="h-0 w-3.5 border-t-2 border-dashed border-emerald-600" />
              Target
            </span>
          )}
          {typeof stop === "number" && (
            <span className="inline-flex items-center gap-1.5">
              <span className="h-0 w-3.5 border-t-2 border-dashed border-rose-600" />
              Stop
            </span>
          )}
        </div>
      </div>
      <div className="p-3">
        {hasBars ? (
          <PriceChart
            bars={bars}
            priceLines={priceLines}
            light
            height={344}
            showVolume
          />
        ) : (
          <div className="h-[344px] flex flex-col items-center justify-center text-center px-6">
            <Activity className="h-6 w-6 text-stone-300 mb-3" />
            <div className="text-[13px] font-medium text-ink-2">
              No price history for this run
            </div>
            <div className="text-[12px] text-ink-3 mt-1 max-w-xs">
              The quick read returns price history; some tickers may lack a
              recent OHLCV series.
              {typeof current === "number" &&
                ` Last known price: ${formatNumber(current)} EGP.`}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

/* ════════════════════════════════════════════════════════════════════════════
   Verdict panel — the analyst rating box
   ════════════════════════════════════════════════════════════════════════════ */

function VerdictPanel({
  signal,
  confidence,
  current,
  target,
  stop,
  upside,
  downside,
  risk,
}: {
  signal: string;
  confidence: string;
  current?: number;
  target?: number;
  stop?: number;
  upside?: number;
  downside?: number;
  risk?: string;
}) {
  const dir = dirOf(signal);
  const head =
    dir === "up"
      ? "bg-gradient-to-br from-emerald-600 to-teal-600"
      : dir === "down"
      ? "bg-gradient-to-br from-rose-600 to-orange-600"
      : "bg-gradient-to-br from-stone-700 to-stone-800";
  const Arrow =
    dir === "up" ? ArrowUpRight : dir === "down" ? ArrowDownRight : ArrowRight;

  return (
    <section className="card overflow-hidden anim-fade-up flex flex-col">
      {/* Headline verdict */}
      <div className={cn("p-5 text-white relative overflow-hidden", head)}>
        <div className="eyebrow text-white/70">Recommended action</div>
        <div className="mt-2 flex items-center gap-2.5">
          <Arrow className="h-7 w-7" strokeWidth={2.4} />
          <span className="display text-[34px] font-semibold leading-none">
            {signal || "—"}
          </span>
        </div>
        <div className="mt-3 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-white/15 text-[11.5px] font-medium">
          <GaugeIcon className="h-3.5 w-3.5" />
          {confidence ? `${confidence} confidence` : "Confidence —"}
        </div>
      </div>

      {/* Thesis ladder */}
      <div className="p-5 space-y-3.5 flex-1">
        <LadderRow
          label="Entry (spot)"
          value={current !== undefined ? `${formatNumber(current)} EGP` : "—"}
        />
        <LadderRow
          label="Price target"
          value={target !== undefined ? `${formatNumber(target)} EGP` : "—"}
          badge={
            upside !== undefined
              ? {
                  text: formatPercent(upside),
                  tone: upside >= 0 ? "up" : "down",
                }
              : undefined
          }
        />
        <LadderRow
          label="Protective stop"
          value={stop !== undefined ? `${formatNumber(stop)} EGP` : "—"}
          badge={
            downside !== undefined
              ? { text: formatPercent(downside), tone: "down" }
              : undefined
          }
        />
        <div className="pt-3 border-t border-stone-200/80 dark:border-[var(--hairline)] flex items-center justify-between">
          <span className="eyebrow text-stone-500">Risk profile</span>
          <span className="text-[13px] font-medium text-ink capitalize">
            {risk || "—"}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="eyebrow text-stone-500">Time horizon</span>
          <span className="text-[13px] font-medium text-ink">2 – 4 weeks</span>
        </div>
      </div>
    </section>
  );
}

function LadderRow({
  label,
  value,
  badge,
}: {
  label: string;
  value: string;
  badge?: { text: string; tone: "up" | "down" };
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="eyebrow text-stone-500">{label}</span>
      <div className="flex items-center gap-2">
        <span className="display-num text-[16px] font-semibold text-ink">
          {value}
        </span>
        {badge && (
          <span
            className={cn(
              "px-1.5 py-0.5 rounded-md text-[11px] font-semibold mono border",
              badge.tone === "up"
                ? "text-emerald-700 bg-emerald-50 border-emerald-200"
                : "text-rose-700 bg-rose-50 border-rose-200"
            )}
          >
            {badge.text}
          </span>
        )}
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════════════
   Key stats — quote statistics strip
   ════════════════════════════════════════════════════════════════════════════ */

function KeyStats({
  current,
  target,
  stop,
  upside,
  downside,
  riskReward,
  rsi,
  trend,
}: {
  current?: number;
  target?: number;
  stop?: number;
  upside?: number;
  downside?: number;
  riskReward?: number;
  rsi?: number;
  trend?: string;
}) {
  const rsiZone =
    rsi === undefined
      ? undefined
      : rsi >= 70
      ? "Overbought"
      : rsi <= 30
      ? "Oversold"
      : "Neutral";

  const tiles: {
    label: string;
    value: string;
    hint?: string;
    valueClass?: string;
  }[] = [
    {
      label: "Current price",
      value: current !== undefined ? `${formatNumber(current)}` : "—",
      hint: "EGP · spot",
    },
    {
      label: "Price target",
      value: target !== undefined ? `${formatNumber(target)}` : "—",
      hint: "EGP",
      valueClass:
        upside !== undefined
          ? upside >= 0
            ? "text-emerald-700"
            : "text-rose-700"
          : undefined,
    },
    {
      label: "Implied upside",
      value: upside !== undefined ? formatPercent(upside) : "—",
      hint: "spot → target",
      valueClass:
        upside !== undefined
          ? upside >= 0
            ? "text-emerald-700"
            : "text-rose-700"
          : undefined,
    },
    {
      label: "Protective stop",
      value: stop !== undefined ? `${formatNumber(stop)}` : "—",
      hint:
        downside !== undefined ? `${formatPercent(downside)} to stop` : "EGP",
      valueClass: stop !== undefined ? "text-rose-700" : undefined,
    },
    {
      label: "Risk / reward",
      value: riskReward !== undefined ? `${riskReward.toFixed(1)} : 1` : "—",
      hint: "reward per unit risk",
    },
    {
      label: "RSI (14)",
      value: rsi !== undefined ? rsi.toFixed(0) : "—",
      hint: rsiZone,
    },
    {
      label: "Trend",
      value: trend ? trend : "—",
      hint: "indicator read",
    },
    {
      label: "Time horizon",
      value: "2 – 4 wk",
      hint: "≈ 14 sessions",
    },
  ];

  return (
    <section className="card overflow-hidden anim-fade-up">
      <div className="flex items-center gap-2 px-5 pt-4 pb-3 border-b border-stone-200/80 dark:border-[var(--hairline)]">
        <Scale className="h-4 w-4 text-stone-500" />
        <span className="text-[14px] font-semibold text-ink">Key levels</span>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-stone-200/80 dark:bg-[var(--hairline)]">
        {tiles.map((t) => (
          <div
            key={t.label}
            className="bg-white dark:bg-[var(--paper)] p-4"
          >
            <div className="eyebrow text-stone-500">{t.label}</div>
            <div
              className={cn(
                "display-num text-[20px] font-semibold mt-1.5 text-ink capitalize",
                t.valueClass
              )}
            >
              {t.value}
            </div>
            {t.hint && (
              <div className="text-[10.5px] text-stone-500 mt-0.5">{t.hint}</div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */

function SectionLabel({ index, label }: { index: string; label: string }) {
  return (
    <div className="flex items-center gap-3 pt-2">
      <span className="mono text-[11px] text-stone-400">{index}</span>
      <div className="eyebrow text-stone-600">{label}</div>
      <div className="h-px flex-1 bg-stone-200 dark:bg-[var(--hairline)]" />
    </div>
  );
}

function ExecutionPlan({
  signal,
  current,
  target,
  stop,
  risk,
  rationale,
}: {
  signal: string;
  current?: number;
  target?: number;
  stop?: number;
  risk?: string;
  rationale?: string;
}) {
  const items = [
    {
      label: "Action",
      value: signal || "—",
      accent:
        signal === "BUY" || signal === "STRONG_BUY"
          ? "text-emerald-700 bg-emerald-50 border-emerald-200"
          : signal === "SELL" || signal === "STRONG_SELL"
          ? "text-rose-700 bg-rose-50 border-rose-200"
          : "text-stone-700 bg-stone-50 border-stone-200",
    },
    {
      label: "Entry (spot)",
      value: current !== undefined ? `${formatNumber(current)} EGP` : "—",
    },
    {
      label: "Target",
      value: target !== undefined ? `${formatNumber(target)} EGP` : "—",
    },
    { label: "Stop loss", value: stop !== undefined ? `${formatNumber(stop)} EGP` : "—" },
    { label: "Risk profile", value: risk || "—" },
  ];

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-px bg-stone-200 rounded-xl overflow-hidden border border-stone-200 dark:bg-[var(--hairline)] dark:border-[var(--hairline)]">
        {items.map((it) => (
          <div key={it.label} className="bg-white dark:bg-[var(--paper)] p-4">
            <div className="eyebrow text-stone-500">{it.label}</div>
            <div className="mt-1.5 mono text-[14px] font-semibold text-ink inline-flex items-center">
              {it.accent ? (
                <span
                  className={cn(
                    "px-2 py-1 rounded-md border text-[12px]",
                    it.accent
                  )}
                >
                  {it.value}
                </span>
              ) : (
                it.value
              )}
            </div>
          </div>
        ))}
      </div>
      {rationale && (
        <p className="text-[14px] text-ink-2 leading-[1.65] whitespace-pre-wrap pt-1">
          {rationale}
        </p>
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════════════
   Empty + loading states
   ════════════════════════════════════════════════════════════════════════════ */

function EmptyHero() {
  return (
    <div className="card overflow-hidden grain">
      <div className="px-10 py-12 text-center">
        <div className="inline-flex h-12 w-12 items-center justify-center rounded-xl border border-stone-200 bg-white text-stone-700 mb-5
          dark:border-[var(--hairline)] dark:bg-[var(--bg)] dark:text-[var(--ink-2)]">
          <Sparkles className="h-[18px] w-[18px]" />
        </div>
        <h3 className="display text-[22px] font-semibold text-ink">
          Ready when you are.
        </h3>
        <p className="text-[14px] text-ink-3 mt-2 max-w-md mx-auto leading-relaxed">
          Pick a ticker above, then choose how deep to go. Results appear here
          the moment they're ready.
        </p>
        <div className="mt-7 grid gap-3 sm:grid-cols-2 max-w-xl mx-auto text-left">
          <div className="rounded-xl border border-stone-200 bg-white p-4 dark:border-[var(--hairline)] dark:bg-[var(--bg)]">
            <div className="flex items-center gap-2">
              <Zap className="h-4 w-4 text-stone-500" />
              <span className="text-[13px] font-semibold text-ink">Quick analysis</span>
            </div>
            <p className="text-[12px] text-ink-3 mt-1.5 leading-relaxed">
              A single-LLM snapshot — a fast bull/bear/neutral read in about 30
              seconds. Best for a first look.
            </p>
          </div>
          <div className="rounded-xl border border-stone-200 bg-white p-4 dark:border-[var(--hairline)] dark:bg-[var(--bg)]">
            <div className="flex items-center gap-2">
              <Gavel className="h-4 w-4 text-stone-500" />
              <span className="text-[13px] font-semibold text-ink">Full pipeline</span>
            </div>
            <p className="text-[12px] text-ink-3 mt-1.5 leading-relaxed">
              The complete four-agent debate with risk-sized execution plan.
              Takes 3–8 minutes — the deeper, traceable thesis.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function formatElapsed(seconds: number) {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

const PIPELINE_STAGES = [
  "Gathering market, fundamentals, news & social",
  "Bull vs. bear adversarial debate",
  "Debate judge reconciles the verdict",
  "Portfolio manager sizes & risk-checks the trade",
];

function LoadingHero({ mode }: { mode: "quick" | "full" }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const isFull = mode === "full";
  // Rough stage estimate so the wait feels alive — not a real progress signal.
  const activeStage = !isFull
    ? 0
    : elapsed < 75
    ? 0
    : elapsed < 165
    ? 1
    : elapsed < 240
    ? 2
    : 3;

  return (
    <div className="space-y-6">
      <div className="card-elevated p-6 md:p-7 anim-fade-up">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex items-start gap-3">
            <Loader2 className="h-5 w-5 mt-0.5 animate-spin text-stone-500" />
            <div>
              <div className="text-[15px] font-semibold text-ink">
                {isFull
                  ? "Running the full multi-agent pipeline"
                  : "Running a quick analysis"}
              </div>
              <p className="text-[12.5px] text-ink-3 mt-1 max-w-md leading-relaxed">
                {isFull
                  ? "Four agents are debating this ticker. This usually takes 3–8 minutes — you can leave this tab open and check back."
                  : "A single-LLM read of this ticker — usually ready in under a minute."}
              </p>
            </div>
          </div>
          <div className="text-right shrink-0">
            <div className="display-num text-[22px] font-semibold text-ink tabular-nums">
              {formatElapsed(elapsed)}
            </div>
            <div className="eyebrow text-stone-500">Elapsed</div>
          </div>
        </div>

        {isFull && (
          <ol className="mt-5 space-y-3 border-t border-stone-200 pt-5 dark:border-[var(--hairline)]">
            {PIPELINE_STAGES.map((stage, i) => {
              const state =
                i < activeStage
                  ? "done"
                  : i === activeStage
                  ? "active"
                  : "pending";
              return (
                <li key={stage} className="flex items-center gap-3 text-[13px]">
                  <span
                    className={cn(
                      "flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px] font-semibold",
                      state === "done"
                        ? "bg-emerald-500 border-emerald-500 text-white"
                        : state === "active"
                        ? "border-stone-900 text-stone-900"
                        : "border-stone-200 text-stone-400"
                    )}
                  >
                    {state === "done" ? <Check className="h-3 w-3" /> : i + 1}
                  </span>
                  <span
                    className={cn(
                      state === "pending" && "text-ink-3",
                      state === "done" && "text-ink-2",
                      state === "active" && "font-medium text-ink"
                    )}
                  >
                    {stage}
                  </span>
                  {state === "active" && (
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 anim-pulse-dot" />
                  )}
                </li>
              );
            })}
          </ol>
        )}
      </div>

      <div className="rounded-2xl skeleton h-[260px] border border-stone-200" />
      <div className="grid gap-5 md:grid-cols-2">
        <AgentCardSkeleton tone="bull" />
        <AgentCardSkeleton tone="bear" />
      </div>
      {isFull && (
        <>
          <AgentCardSkeleton tone="judge" />
          <AgentCardSkeleton tone="manager" />
        </>
      )}
    </div>
  );
}
