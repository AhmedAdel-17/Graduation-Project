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
} from "lucide-react";
import { useParams } from "react-router-dom";
import { useAuth } from "../../components/auth/AuthProvider";
import { useRunPrediction, useRunFullPipeline, usePastPrediction } from "../../hooks/usePrediction";
import { AgentCard, AgentCardSkeleton } from "../shared/AgentCard";
import { TickerPicker } from "../shared/TickerPicker";
import { PriceChart } from "../../components/charts/PriceChart";
import { cn, formatNumber, formatPercent } from "../../lib/utils";
import { useT, tEnum } from "../../lib/i18n";
import { useTranslatedText } from "../../hooks/useTranslatedText";
import { getTickerMeta } from "../../data/egxTickerMeta";
import { TickerLogo } from "../../components/ui/TickerLogo";
import { MarketIndicesBar } from "./MarketIndicesBar";
import { TopMovers } from "./TopMovers";
import { SectorPerformance } from "./SectorPerformance";
import { MacroIndicators } from "./MacroIndicators";
import { PivotLevels } from "./PivotLevels";
import { TechnicalPanelSection } from "../prediction/TechnicalPanelSection";
import { TranslatedMarkdown } from "../../components/ui/TranslatedMarkdown";
import type {
  StockBar,
  StyledRecommendations as StyledRecs,
  StyledRecommendation,
} from "../../services/api/types";

// Strip LLM noise from agent prose before rendering as markdown:
//  - Trailing ```json ... ``` blocks (structured thesis echo the LLM appends)
//  - "Bull Analyst: " / "Bear Analyst: " prefix the researchers prepend
//  - Leading "# TICKER — ..." H1 (redundant — the card header already names the agent)
function cleanAgentText(raw: string): string {
  let s = raw;
  // Remove the structured-thesis echo the LLM appends after the prose:
  //  1) fenced ```json … ``` blocks (anywhere — they're machine output, not prose)
  s = s.replace(/```json[\s\S]*?```/gi, "");
  //  2) any remaining trailing fenced code block, regardless of language tag
  s = s.replace(/\n*```[a-zA-Z]*\s*\n[\s\S]*?```\s*$/, "");
  //  3) a trailing BARE JSON object (same thesis, unfenced) — keyed off the
  //     structured-thesis field names so we never eat real prose
  s = s.replace(
    /\n+\{[\s\S]*?(?:"thesis_type"|"conviction_level"|"signal_summary"|"signal_integration_rationale")[\s\S]*\}\s*$/i,
    ""
  );
  // Strip "Bull Analyst: " / "Bear Analyst: " prefix
  s = s.replace(/^(?:Bull|Bear)\s+Analyst:\s*/i, "");
  // Strip leading H1 line ("# TICKER — ...") — the card header is enough
  s = s.replace(/^#\s+.+\n+/, "");
  // Drop a now-dangling trailing horizontal rule left behind by the JSON strip
  s = s.replace(/\n+\s*-{3,}\s*$/, "");
  return s.trim();
}

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
  const { sessionId } = useParams<{ sessionId: string }>();
  const t = useT();
  const { user } = useAuth();
  const [ticker, setTicker] = useState("COMI.CA");
  const runPrediction = useRunPrediction();
  const runFullPipeline = useRunFullPipeline();
  const pastPrediction = usePastPrediction(sessionId);

  // Sync the picker to the historical session's ticker when loaded
  useEffect(() => {
    if (pastPrediction.data?.ticker && pastPrediction.data.ticker !== ticker) {
      setTicker(pastPrediction.data.ticker);
    }
  }, [pastPrediction.data?.ticker]);

  // Show whichever result is most recent (full vs quick), or the past prediction if loading a specific session.
  const quickSubmittedAt = runPrediction.submittedAt ?? 0;
  const fullSubmittedAt = runFullPipeline.submittedAt ?? 0;
  
  const liveResult =
    fullSubmittedAt >= quickSubmittedAt
      ? runFullPipeline.data ?? runPrediction.data
      : runPrediction.data ?? runFullPipeline.data;
      
  const result = pastPrediction.data || liveResult;

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
  const isPastLoading = pastPrediction.isLoading;
  const isLoading = isQuickLoading || isFullLoading || isPastLoading;
  const hasResult = !!result && !result.error;

  function handleSelectFromMovers(t: string) {
    setTicker(t);
    if (typeof window !== "undefined") {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }

  async function handleRunFull() {
    try {
      toast.info(t("home.toast.started"));
      const res = await runFullPipeline.mutateAsync(ticker);
      if (res?.error) {
        toast.error(res.error);
        return;
      }
      if (res?.llm_error) {
        toast.warning(t("home.toast.withErrors", { error: res.llm_error }));
      } else {
        toast.success(t("home.toast.complete", { signal: res?.recommendation?.signal ?? "—" }));
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("home.toast.failed"));
    }
  }

  if (sessionId) {
    return (
      <div className="space-y-6">
        <header className="flex items-end justify-between gap-6 flex-wrap">
          <div>
            <div className="eyebrow mb-3">{t("home.past.eyebrow")}</div>
            <h1 className="display text-[32px] md:text-[36px] font-semibold leading-none text-ink">
              {t("home.past.title")}
            </h1>
            <p className="text-[14px] text-ink-3 mt-3 max-w-xl leading-relaxed">
              {t("home.past.desc")}
            </p>
          </div>
          <button
            onClick={() => window.history.back()}
            className="inline-flex items-center gap-2 h-10 px-4 rounded-xl border border-stone-200 text-[13px] font-medium hover:bg-stone-50"
          >
            {t("home.past.back")}
          </button>
        </header>

        {isPastLoading ? (
          <div className="py-24 flex flex-col items-center justify-center text-stone-500">
            <Loader2 className="h-6 w-6 animate-spin mb-4" />
            {t("home.past.loading")}
          </div>
        ) : !hasResult ? (
          <div className="py-24 flex flex-col items-center justify-center text-stone-500">
            {t("home.past.notFound")}
          </div>
        ) : (
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
                timeHorizon={rec?.time_horizon as string | undefined}
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
              timeHorizon={rec?.time_horizon as string | undefined}
            />

            {/* Support & resistance pivots */}
            <PivotLevels bars={history} current={current} />

            {/* Bull + Bear */}
            <SectionLabel index="01" label={t("home.section.research")} />
            <div className="grid gap-5 md:grid-cols-2">
              <AgentCard
                tone="bull"                icon={TrendingUp}
                agent={t("home.bull.agent")}
                role={t("home.bull.role")}
                chip={t("home.bull.chip")}
                meta={
                  target && current
                    ? [
                        { label: t("home.meta.impliedUpside"), value: formatPercent(upside ?? 0) },
                        { label: t("home.meta.target"), value: `${formatNumber(target)} EGP` },
                      ]
                    : undefined
                }
              >
                {rec?.bull_case ? (
                  <TranslatedMarkdown sectioned>{cleanAgentText(rec.bull_case)}</TranslatedMarkdown>
                ) : (
                  t("home.bull.empty")
                )}
              </AgentCard>
              <AgentCard
                tone="bear"                icon={TrendingDown}
                agent={t("home.bear.agent")}
                role={t("home.bear.role")}
                chip={t("home.bear.chip")}
                meta={
                  stop && current
                    ? [
                        {
                          label: t("home.meta.downsideToStop"),
                          value: formatPercent(downside ?? 0),
                        },
                        { label: t("home.meta.stop"), value: `${formatNumber(stop)} EGP` },
                      ]
                    : undefined
                }
              >
                {rec?.bear_case ? (
                  <TranslatedMarkdown sectioned>{cleanAgentText(rec.bear_case)}</TranslatedMarkdown>
                ) : (
                  t("home.bear.empty")
                )}
              </AgentCard>
            </div>

            {/* Judge */}
            <SectionLabel index="02" label={t("home.section.resolution")} />
            <AgentCard
              tone="judge"              icon={Gavel}
              agent={t("home.judge.agent")}
              role={t("home.judge.role")}
              chip={signal ? t("home.judge.chipWith", { signal }) : t("home.judge.chip")}
              meta={[
                { label: t("home.meta.signal"), value: signal || "—" },
                { label: t("home.meta.confidence"), value: confidence || "—" },
                {
                  label: t("home.meta.riskProfile"),
                  value: (rec?.risk as string | undefined) || "—",
                },
              ]}
            >
              {rec?.neutral_case || rec?.rationale ? (
                <TranslatedMarkdown sectioned>
                  {cleanAgentText((rec?.neutral_case || rec?.rationale) as string)}
                </TranslatedMarkdown>
              ) : (
                t("home.judge.empty")
              )}
            </AgentCard>

            {/* Portfolio manager */}
            <SectionLabel index="03" label={t("home.section.execution")} />
            <AgentCard
              tone="manager"
              icon={Briefcase}
              agent={t("home.manager.agent")}
              role={t("home.manager.role")}
              chip={confidence ? t("home.manager.chipWith", { level: confidence }) : t("home.manager.chip")}
            >
              <ExecutionPlan
                signal={signal}
                current={current}
                target={target}
                stop={stop}
                risk={rec?.risk}
                rationale={rec?.recommendation || rec?.rationale}
              />
              <StyledRecommendations styled={rec?.styled_recommendations} />
            </AgentCard>

            {/* Technical panel */}
            <SectionLabel index="04" label={t("home.section.technical")} />
            <TechnicalPanelSection data={result?.technical_panel} />
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {/* ─── Market indices strip ────────────────────────────────── */}
      <MarketIndicesBar />

      {/* ─── Welcome Message ─────────────────────────────────────── */}
      <div className="pt-4 pb-2 px-1">
        <h2 className="display text-[22px] md:text-[26px] font-semibold text-ink">
          {t("home.welcome", { name: user?.displayName || user?.email?.split('@')[0] || 'User' })}
        </h2>
      </div>

      {/* ─── Hero — brand headline + run bar ─────────────────────── */}
      <HeroPanel
        ticker={ticker}
        onChange={setTicker}
        onRun={handleRunFull}
        isLoading={isLoading}
      />

      {/* ─── Empty → market overview ─────────────────────────────── */}
      {!isLoading && !hasResult && (
        <MarketOverview onSelectTicker={handleSelectFromMovers} />
      )}

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
              timeHorizon={rec?.time_horizon as string | undefined}
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
            timeHorizon={rec?.time_horizon as string | undefined}
          />

          {/* Support & resistance pivots */}
          <PivotLevels bars={history} current={current} />

          {/* Bull + Bear */}
          <SectionLabel index="01" label={t("home.section.research")} />
          <div className="grid gap-5 md:grid-cols-2">
            <AgentCard
              tone="bull"              icon={TrendingUp}
              agent={t("home.bull.agent")}
              role={t("home.bull.role")}
              chip={t("home.bull.chip")}
              meta={
                target && current
                  ? [
                      { label: "Implied upside", value: formatPercent(upside ?? 0) },
                      { label: "Target", value: `${formatNumber(target)} EGP` },
                    ]
                  : undefined
              }
            >
              {rec?.bull_case ? (
                <TranslatedMarkdown sectioned>{cleanAgentText(rec.bull_case)}</TranslatedMarkdown>
              ) : (
                t("home.bull.empty")
              )}
            </AgentCard>
            <AgentCard
              tone="bear"              icon={TrendingDown}
              agent={t("home.bear.agent")}
              role={t("home.bear.role")}
              chip={t("home.bear.chip")}
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
              {rec?.bear_case ? (
                <TranslatedMarkdown sectioned>{cleanAgentText(rec.bear_case)}</TranslatedMarkdown>
              ) : (
                t("home.bear.empty")
              )}
            </AgentCard>
          </div>

          {/* Judge */}
          <SectionLabel index="02" label={t("home.section.resolution")} />
          <AgentCard
            tone="judge"            icon={Gavel}
            agent={t("home.judge.agent")}
            role={t("home.judge.role")}
            chip={signal ? t("home.judge.chipWith", { signal }) : t("home.judge.chip")}
            meta={[
              { label: t("home.meta.signal"), value: signal || "—" },
              { label: t("home.meta.confidence"), value: confidence || "—" },
              {
                label: t("home.meta.riskProfile"),
                value: (rec?.risk as string | undefined) || "—",
              },
            ]}
          >
            {rec?.neutral_case || rec?.rationale ? (
              <TranslatedMarkdown sectioned>
                {cleanAgentText((rec?.neutral_case || rec?.rationale) as string)}
              </TranslatedMarkdown>
            ) : (
              t("home.judge.empty")
            )}
          </AgentCard>

          {/* Portfolio manager */}
          <SectionLabel index="03" label={t("home.section.execution")} />
          <AgentCard
            tone="manager"
            icon={Briefcase}
            agent={t("home.manager.agent")}
            role={t("home.manager.role")}
            chip={confidence ? t("home.manager.chipWith", { level: confidence }) : t("home.manager.chip")}
          >
            <ExecutionPlan
              signal={signal}
              current={current}
              target={target}
              stop={stop}
              risk={rec?.risk}
              rationale={rec?.recommendation || rec?.rationale}
            />
            <StyledRecommendations styled={rec?.styled_recommendations} />
          </AgentCard>

          {/* Full Investing-style technical panel (12 indicators + verdicts +
              SMA/EMA grid + 5 pivot systems) — the same engine output that is
              fed to the market analyst agent. Renders nothing if unavailable.
              Placed last: it's reference detail, not part of the decision flow. */}
          <SectionLabel index="04" label={t("home.section.technical")} />
          <TechnicalPanelSection data={result?.technical_panel} />
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
  const t = useT();
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
            <div className="eyebrow text-stone-500 mb-1">{t("home.quote.lastPrice")}</div>
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
            <span className="inline-flex items-center gap-1.5 text-[11.5px] text-ink-3 mono ps-0.5">
              {weeklyChange !== undefined ? formatPercent(weeklyChange) : "—"}
              <span className="opacity-70">1W</span>
            </span>
          </div>
        </div>

        {/* Signal */}
        <div className="flex flex-col items-end gap-1.5">
          <div className="eyebrow text-stone-500">{t("home.quote.agentVerdict")}</div>
          <span
            className={cn(
              "inline-flex items-center gap-1.5 px-4 py-2 rounded-xl text-[15px] font-semibold uppercase tracking-wide border",
              sigClass
            )}
          >
            <SigArrow className="h-4 w-4" />
            {tEnum(t, "signal", signal)}
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
  const t = useT();
  const priceLines = useMemo(() => {
    const lines: { price: number; color: string; title: string }[] = [];
    if (typeof target === "number") {
      lines.push({ price: target, color: "#059669", title: t("home.chart.target") });
    }
    if (typeof stop === "number") {
      lines.push({ price: stop, color: "#e11d48", title: t("home.chart.stop") });
    }
    return lines;
  }, [target, stop, t]);

  const hasBars = bars && bars.length > 0;

  return (
    <section className="card overflow-hidden anim-fade-up">
      <div className="flex items-center justify-between gap-3 px-5 pt-4 pb-3 border-b border-stone-200/80 dark:border-[var(--hairline)]">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-stone-500" />
          <span className="text-[14px] font-semibold text-ink">{t("home.chart.title")}</span>
          {hasBars && (
            <span className="text-[11.5px] text-ink-3">
              {t("home.chart.sessions", { n: bars.length })}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3.5 text-[11px] text-ink-3">
          {typeof target === "number" && (
            <span className="inline-flex items-center gap-1.5">
              <span className="h-0 w-3.5 border-t-2 border-dashed border-emerald-600" />
              {t("home.chart.target")}
              <span className="mono font-semibold text-emerald-700">
                {formatNumber(target)}
              </span>
            </span>
          )}
          {typeof stop === "number" && (
            <span className="inline-flex items-center gap-1.5">
              <span className="h-0 w-3.5 border-t-2 border-dashed border-rose-600" />
              {t("home.chart.stop")}
              <span className="mono font-semibold text-rose-700">
                {formatNumber(stop)}
              </span>
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
              {t("home.chart.noHistory")}
            </div>
            <div className="text-[12px] text-ink-3 mt-1 max-w-xs">
              {t("home.chart.noHistoryDesc")}
              {typeof current === "number" &&
                t("home.chart.lastKnown", { price: formatNumber(current) })}
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
  timeHorizon,
}: {
  signal: string;
  confidence: string;
  current?: number;
  target?: number;
  stop?: number;
  upside?: number;
  downside?: number;
  risk?: string;
  timeHorizon?: string;
}) {
  const t = useT();
  const { text: horizonT } = useTranslatedText(timeHorizon);
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
        <div className="eyebrow text-white/70">{t("home.verdict.action")}</div>
        <div className="mt-2 flex items-center gap-2.5">
          <Arrow className="h-7 w-7" strokeWidth={2.4} />
          <span className="display text-[34px] font-semibold leading-none">
            {tEnum(t, "signal", signal)}
          </span>
        </div>
        <div className="mt-3 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-white/15 text-[11.5px] font-medium">
          <GaugeIcon className="h-3.5 w-3.5" />
          {confidence ? t("home.verdict.confidence", { level: tEnum(t, "conf", confidence) }) : t("home.verdict.confidenceNone")}
        </div>
      </div>

      {/* Thesis ladder */}
      <div className="p-5 space-y-3.5 flex-1">
        <LadderRow
          label={t("home.ladder.entry")}
          value={current !== undefined ? `${formatNumber(current)} EGP` : "—"}
        />
        <LadderRow
          label={t("home.ladder.target")}
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
          label={t("home.ladder.stop")}
          value={stop !== undefined ? `${formatNumber(stop)} EGP` : "—"}
          badge={
            downside !== undefined
              ? { text: formatPercent(downside), tone: "down" }
              : undefined
          }
        />
        <div className="pt-3 border-t border-stone-200/80 dark:border-[var(--hairline)] flex items-center justify-between">
          <span className="eyebrow text-stone-500">{t("home.ladder.risk")}</span>
          <span className="text-[13px] font-medium text-ink capitalize">
            {tEnum(t, "risk", risk)}
          </span>
        </div>
        <div className="flex items-center justify-between gap-3">
          <span className="eyebrow text-stone-500 shrink-0">{t("home.ladder.horizon")}</span>
          <span className="text-[13px] font-medium text-ink text-end" dir="auto">
            {horizonT || "—"}
          </span>
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
  timeHorizon,
}: {
  current?: number;
  target?: number;
  stop?: number;
  upside?: number;
  downside?: number;
  riskReward?: number;
  rsi?: number;
  trend?: string;
  timeHorizon?: string;
}) {
  const t = useT();
  const { text: horizonT } = useTranslatedText(timeHorizon);
  const rsiZone =
    rsi === undefined
      ? undefined
      : rsi >= 70
      ? t("home.rsi.overbought")
      : rsi <= 30
      ? t("home.rsi.oversold")
      : t("home.rsi.neutral");

  const tiles: {
    label: string;
    value: string;
    hint?: string;
    valueClass?: string;
  }[] = [
    {
      label: t("home.stats.current"),
      value: current !== undefined ? `${formatNumber(current)}` : "—",
      hint: t("home.stats.current.hint"),
    },
    {
      label: t("home.stats.target"),
      value: target !== undefined ? `${formatNumber(target)}` : "—",
      hint: t("home.stats.target.hint"),
      valueClass:
        upside !== undefined
          ? upside >= 0
            ? "text-emerald-700"
            : "text-rose-700"
          : undefined,
    },
    {
      label: t("home.stats.upside"),
      value: upside !== undefined ? formatPercent(upside) : "—",
      hint: t("home.stats.upside.hint"),
      valueClass:
        upside !== undefined
          ? upside >= 0
            ? "text-emerald-700"
            : "text-rose-700"
          : undefined,
    },
    {
      label: t("home.stats.stop"),
      value: stop !== undefined ? `${formatNumber(stop)}` : "—",
      hint:
        downside !== undefined
          ? t("home.stats.stop.hint", { pct: formatPercent(downside) })
          : t("home.stats.stop.hintEgp"),
      valueClass: stop !== undefined ? "text-rose-700" : undefined,
    },
    {
      label: t("home.stats.rr"),
      value: riskReward !== undefined ? `${riskReward.toFixed(1)} : 1` : "—",
      hint: t("home.stats.rr.hint"),
    },
    {
      label: t("home.stats.rsi"),
      value: rsi !== undefined ? rsi.toFixed(0) : "—",
      hint: rsiZone,
    },
    {
      label: t("home.stats.trend"),
      value: trend ? tEnum(t, "trend", trend) : "—",
      hint: t("home.stats.trend.hint"),
    },
    {
      label: t("home.stats.horizon"),
      value: horizonT || "—",
      hint: t("home.stats.horizon.hint"),
    },
  ];

  return (
    <section className="card overflow-hidden anim-fade-up">
      <div className="flex items-center gap-2 px-5 pt-4 pb-3 border-b border-stone-200/80 dark:border-[var(--hairline)]">
        <Scale className="h-4 w-4 text-stone-500" />
        <span className="text-[14px] font-semibold text-ink">{t("home.keyLevels")}</span>
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
  const t = useT();
  const items = [
    {
      label: t("home.exec.action"),
      value: tEnum(t, "signal", signal),
      accent:
        signal === "BUY" || signal === "STRONG_BUY"
          ? "text-emerald-700 bg-emerald-50 border-emerald-200"
          : signal === "SELL" || signal === "STRONG_SELL"
          ? "text-rose-700 bg-rose-50 border-rose-200"
          : "text-stone-700 bg-stone-50 border-stone-200",
    },
    {
      label: t("home.exec.entry"),
      value: current !== undefined ? `${formatNumber(current)} EGP` : "—",
    },
    {
      label: t("home.exec.target"),
      value: target !== undefined ? `${formatNumber(target)} EGP` : "—",
    },
    { label: t("home.exec.stop"), value: stop !== undefined ? `${formatNumber(stop)} EGP` : "—" },
    { label: t("home.exec.risk"), value: tEnum(t, "risk", risk) },
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
        <div className="pt-1">
          <TranslatedMarkdown sectioned>{cleanAgentText(rationale)}</TranslatedMarkdown>
        </div>
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════════════
   Per-trading-style recommendations (Swing / Position / Long-Term)
   ════════════════════════════════════════════════════════════════════════════ */

function recAccent(rec?: string) {
  const r = (rec || "").toUpperCase();
  if (r === "BUY" || r === "STRONG_BUY" || r === "ACCUMULATE")
    return "text-emerald-700 bg-emerald-50 border-emerald-200";
  if (r === "SELL" || r === "STRONG_SELL")
    return "text-rose-700 bg-rose-50 border-rose-200";
  return "text-stone-700 bg-stone-50 border-stone-200"; // HOLD / NO TRADE
}

function StyleCard({ rec }: { rec: StyledRecommendation }) {
  const t = useT();
  const { text: reasoningText } = useTranslatedText(rec.reasoning);
  const { text: entryT } = useTranslatedText(rec.entry_zone);
  const { text: targetT } = useTranslatedText(rec.target);
  const { text: stopT } = useTranslatedText(rec.stop_loss);
  const { text: holdingT } = useTranslatedText(rec.holding_period);
  const rows: { label: string; value?: string }[] = [
    { label: t("home.style.entry"), value: entryT },
    { label: t("home.style.target"), value: targetT },
    { label: t("home.style.stop"), value: stopT },
    { label: t("home.style.holding"), value: holdingT },
    { label: t("home.style.confidence"), value: rec.confidence ? tEnum(t, "conf", rec.confidence) : undefined },
    { label: t("home.style.riskLevel"), value: rec.risk_level ? tEnum(t, "risk", rec.risk_level) : undefined },
  ].filter((r) => r.value && r.value.toUpperCase() !== "N/A");

  return (
    <div className="rounded-xl border border-stone-200 bg-white p-4 dark:border-[var(--hairline)] dark:bg-[var(--paper)]">
      <div className="flex items-center justify-between gap-2">
        <div className="display text-[15px] font-semibold text-ink">
          {rec.style || "—"}
        </div>
        <span
          className={cn(
            "px-2 py-1 rounded-md border text-[12px] mono font-semibold",
            recAccent(rec.recommendation)
          )}
        >
          {rec.recommendation ? tEnum(t, "signal", rec.recommendation).toUpperCase() : "—"}
        </span>
      </div>
      <div className="mt-3 space-y-1.5">
        {rows.map((r) => (
          <div key={r.label} className="flex items-baseline justify-between gap-3">
            <span className="eyebrow text-stone-500 shrink-0">{r.label}</span>
            <span className="mono text-[13px] text-ink text-end" dir="auto">{r.value}</span>
          </div>
        ))}
      </div>
      {rec.reasoning && (
        <p className="mt-3 text-[13px] leading-relaxed text-ink-3" dir="auto">
          {reasoningText}
        </p>
      )}
    </div>
  );
}

function StyledRecommendations({ styled }: { styled?: StyledRecs | null }) {
  const t = useT();
  if (!styled) return null;
  // Fixed display order; only render styles the agent actually returned.
  const order: { key: string; fallback: string }[] = [
    { key: "swing", fallback: t("home.style.swing") },
    { key: "position", fallback: t("home.style.position") },
    { key: "long_term", fallback: t("home.style.longTerm") },
  ];
  const cards = order
    .map(({ key, fallback }) => {
      const rec = styled[key];
      if (!rec) return null;
      return { ...rec, style: rec.style || fallback };
    })
    .filter(Boolean) as StyledRecommendation[];

  if (cards.length === 0) return null;

  return (
    <div className="mt-6">
      <div className="eyebrow text-stone-500 mb-3">{t("home.style.title")}</div>
      <div className="grid gap-3 md:grid-cols-3">
        {cards.map((rec) => (
          <StyleCard key={rec.style} rec={rec} />
        ))}
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════════════
   Hero panel — brand headline + run bar (the primary call-to-action)
   ════════════════════════════════════════════════════════════════════════════ */

function HeroPanel({
  ticker,
  onChange,
  onRun,
  isLoading,
}: {
  ticker: string;
  onChange: (t: string) => void;
  onRun: () => void;
  isLoading: boolean;
}) {
  const t = useT();
  return (
    <section className="card-elevated grain anim-fade-up">
      {/* Brand accent bar */}
      <div className="h-1 w-full rounded-t-2xl bg-gradient-to-r from-[var(--brand-navy)] via-[var(--brand-green)] to-[var(--brand-navy)]" />
      <div className="p-6 md:p-8">
        <div className="eyebrow mb-3 text-[var(--brand-green)]">
          {t("home.hero.eyebrow")}
        </div>
        <h1 className="display text-[32px] md:text-[40px] font-semibold leading-[1.06] text-ink max-w-2xl whitespace-pre-line">
          {t("home.hero.title")}
        </h1>
        <p className="text-[14px] text-ink-3 mt-3.5 max-w-xl leading-relaxed">
          {t("home.hero.desc")}
        </p>

        {/* Run bar */}
        <div className="mt-6 flex flex-col gap-2">
          <div className="flex items-center gap-2 flex-wrap sm:flex-nowrap">
            <div className="flex-1 min-w-[200px]">
              <TickerPicker value={ticker} onChange={onChange} />
            </div>
            <button
              onClick={onRun}
              disabled={isLoading}
              className={cn(
                "shrink-0 inline-flex items-center gap-2 h-12 px-6 rounded-xl text-white text-[14px] font-semibold",
                "bg-[var(--brand-navy)] hover:brightness-110 transition-all duration-200",
                "shadow-[0_6px_16px_-4px_rgba(20,40,74,0.5)]",
                "disabled:opacity-60 disabled:cursor-not-allowed"
              )}
            >
              {isLoading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t("home.hero.loading")}
                </>
              ) : (
                <>
                  <Sparkles className="h-4 w-4" />
                  {t("home.hero.analyze")}
                  <ArrowRight className="h-4 w-4 ms-1 rtl:rotate-180" />
                </>
              )}
            </button>
          </div>
          <p className="px-1 text-[12px] text-ink-3 italic">
            {t("home.hero.caption")}
          </p>
        </div>
      </div>
    </section>
  );
}

/* ════════════════════════════════════════════════════════════════════════════
   Market overview — shown before an analysis is run (fills the home screen)
   ════════════════════════════════════════════════════════════════════════════ */

function MarketOverview({
  onSelectTicker,
}: {
  onSelectTicker?: (ticker: string) => void;
}) {
  const t = useT();
  return (
    <div className="space-y-6">
      <SectionLabel index="◆" label={t("home.section.pulse")} />
      <div className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
        <TopMovers onSelectTicker={onSelectTicker} />
        <SectorPerformance />
      </div>
      <MacroIndicators />
      <p className="text-center text-[12px] text-ink-3">
        {t("home.overview.caption")}
      </p>
    </div>
  );
}

function formatElapsed(seconds: number) {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

const PIPELINE_STAGE_KEYS = [
  "home.loading.stage1",
  "home.loading.stage2",
  "home.loading.stage3",
  "home.loading.stage4",
];

const FACTS_KEYS = [
  "home.loading.fact1", "home.loading.fact2", "home.loading.fact3", "home.loading.fact4", "home.loading.fact5",
  "home.loading.fact6", "home.loading.fact7", "home.loading.fact8", "home.loading.fact9", "home.loading.fact10",
  "home.loading.fact11", "home.loading.fact12", "home.loading.fact13", "home.loading.fact14", "home.loading.fact15",
  "home.loading.fact16", "home.loading.fact17", "home.loading.fact18", "home.loading.fact19", "home.loading.fact20",
  "home.loading.fact21", "home.loading.fact22", "home.loading.fact23", "home.loading.fact24", "home.loading.fact25",
];

function LoadingHero({ mode: _mode }: { mode: "quick" | "full" }) {
  const t = useT();
  const [elapsed, setElapsed] = useState(0);
  const [factIndex, setFactIndex] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const id = setInterval(() => {
      setFactIndex((prev) => (prev + 1) % FACTS_KEYS.length);
    }, 7000);
    return () => clearInterval(id);
  }, []);

  // Rough stage estimate so the wait feels alive — not a real progress signal.
  // Tuned for ~20 min full runs.
  const activeStage =
    elapsed < 180
      ? 0
      : elapsed < 600
      ? 1
      : elapsed < 900
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
                {t("home.loading.title")}
              </div>
              <p key={factIndex} className="text-[12.5px] text-ink-3 mt-1 max-w-md leading-relaxed anim-fade-up">
                <strong>{t("home.loading.factPrefix")}</strong>
                {t(FACTS_KEYS[factIndex])}
              </p>
            </div>
          </div>
          <div className="text-right shrink-0">
            <div className="display-num text-[22px] font-semibold text-ink tabular-nums">
              {formatElapsed(elapsed)}
            </div>
            <div className="eyebrow text-stone-500">{t("home.loading.elapsed")}</div>
          </div>
        </div>

        <ol className="mt-5 space-y-3 border-t border-stone-200 pt-5 dark:border-[var(--hairline)]">
            {PIPELINE_STAGE_KEYS.map((stageKey, i) => {
              const stage = t(stageKey);
              const state =
                i < activeStage
                  ? "done"
                  : i === activeStage
                  ? "active"
                  : "pending";
              return (
                <li key={stageKey} className="flex items-center gap-3 text-[13px]">
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
      </div>

      <div className="rounded-2xl skeleton h-[260px] border border-stone-200" />
      <div className="grid gap-5 md:grid-cols-2">
        <AgentCardSkeleton tone="bull" />
        <AgentCardSkeleton tone="bear" />
      </div>
      <AgentCardSkeleton tone="judge" />
      <AgentCardSkeleton tone="manager" />
    </div>
  );
}
