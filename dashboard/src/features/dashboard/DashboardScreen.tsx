import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Briefcase,
  Clock,
  Gauge as GaugeIcon,
  Gavel,
  Sparkles,
  Target,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { useRunPrediction } from "../../hooks/usePrediction";
import { AgentCard, AgentCardSkeleton } from "../shared/AgentCard";
import { TickerPicker } from "../shared/TickerPicker";
import { cn, formatNumber, formatPercent } from "../../lib/utils";
import { MacroIndicators } from "./MacroIndicators";
import { FullPipelinePanel } from "./FullPipelinePanel";
import { getTickerMeta } from "../../data/egxTickerMeta";
import { TickerLogo } from "../../components/ui/TickerLogo";

export function DashboardScreen() {
  const [ticker, setTicker] = useState("COMI.CA");
  const runPrediction = useRunPrediction();

  const result = runPrediction.data;
  const rec = result?.recommendation;
  const price = result?.price;
  const current = price?.current;
  const target =
    rec?.target_price !== undefined && rec?.target_price !== null
      ? Number(rec.target_price)
      : undefined;

  const upside = useMemo(() => {
    if (typeof current === "number" && typeof target === "number" && current > 0) {
      return ((target - current) / current) * 100;
    }
    return undefined;
  }, [current, target]);

  const signal = (rec?.signal || "").toUpperCase();
  const confidence = (rec?.confidence || "").toUpperCase();
  const isLoading = runPrediction.isPending;
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

  return (
    <div className="space-y-10">
      {/* ─── Page header ─────────────────────────────────────────── */}
      <header className="flex items-end justify-between gap-6 flex-wrap">
        <div>
          <div className="eyebrow mb-3">Workspace · Analysis</div>
          <h1 className="display text-[40px] md:text-[44px] font-semibold leading-[1.05] text-ink">
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

      {/* ─── Macro snapshot ──────────────────────────────────────── */}
      <MacroIndicators />

      {/* ─── Run bar ─────────────────────────────────────────────── */}
      <div className="card-elevated p-2 flex items-center gap-2">
        <div className="flex-1 min-w-0">
          <TickerPicker value={ticker} onChange={setTicker} />
        </div>
        <button
          onClick={handleRun}
          disabled={isLoading}
          className={cn(
            "shrink-0 inline-flex items-center gap-2 h-12 px-5 rounded-xl",
            "bg-stone-900 hover:bg-stone-800 text-white text-[14px] font-medium",
            "transition-all duration-200 shadow-[0_4px_12px_-2px_rgba(0,0,0,0.18)]",
            "disabled:opacity-60 disabled:cursor-not-allowed"
          )}
        >
          {isLoading ? (
            <>
              <span className="h-2 w-2 rounded-full bg-emerald-400 anim-pulse-dot" />
              Running analysis…
            </>
          ) : (
            <>
              Quick analysis
              <ArrowRight className="h-4 w-4" />
            </>
          )}
        </button>
      </div>

      {/* ─── Full multi-agent pipeline (separate from quick analysis) ── */}
      <FullPipelinePanel ticker={ticker} />

      {/* ─── Empty ───────────────────────────────────────────────── */}
      {!isLoading && !hasResult && <EmptyHero />}

      {/* ─── Loading ─────────────────────────────────────────────── */}
      {isLoading && <LoadingHero />}

      {/* ─── Results ─────────────────────────────────────────────── */}
      {hasResult && (
        <div className="space-y-8">
          <CommandCenter
            ticker={result?.ticker || ticker}
            name={(result?.name as string | undefined) || ticker}
            signal={signal}
            confidence={confidence}
            current={current}
            target={target}
            upside={upside}
            horizon="≈ 14 trading days"
            horizonHuman="2 – 4 weeks"
          />

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
                rec?.stop_loss && current
                  ? [
                      {
                        label: "Downside to stop",
                        value: formatPercent(
                          ((Number(rec.stop_loss) - current) / current) * 100
                        ),
                      },
                      { label: "Stop", value: `${formatNumber(Number(rec.stop_loss))} EGP` },
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
              stop={rec?.stop_loss !== undefined && rec?.stop_loss !== null ? Number(rec.stop_loss) : undefined}
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
   Command Center — the result hero
   ════════════════════════════════════════════════════════════════════════════ */

function CommandCenter({
  ticker,
  name,
  signal,
  confidence,
  current,
  target,
  upside,
  horizon,
  horizonHuman,
}: {
  ticker: string;
  name: string;
  signal: string;
  confidence: string;
  current?: number;
  target?: number;
  upside?: number;
  horizon: string;
  horizonHuman: string;
}) {
  const dir =
    signal === "BUY" || signal === "STRONG_BUY"
      ? "up"
      : signal === "SELL" || signal === "STRONG_SELL"
      ? "down"
      : "flat";
  const accentBar =
    dir === "up"
      ? "from-emerald-500/0 via-emerald-500 to-teal-500"
      : dir === "down"
      ? "from-rose-500/0 via-rose-500 to-orange-500"
      : "from-stone-400/0 via-stone-500 to-stone-400";
  const haloColor =
    dir === "up"
      ? "bg-emerald-300"
      : dir === "down"
      ? "bg-rose-300"
      : "bg-stone-300";
  const signalChip =
    dir === "up"
      ? "bg-emerald-50 text-emerald-800 border-emerald-200"
      : dir === "down"
      ? "bg-rose-50 text-rose-800 border-rose-200"
      : "bg-stone-100 text-stone-700 border-stone-200";
  const upsideClass =
    upside === undefined
      ? "text-ink"
      : upside >= 0
      ? "text-emerald-700"
      : "text-rose-700";
  const targetClass =
    dir === "up"
      ? "text-emerald-800"
      : dir === "down"
      ? "text-rose-800"
      : "text-ink";
  const railColor =
    dir === "up"
      ? "bg-emerald-500"
      : dir === "down"
      ? "bg-rose-500"
      : "bg-stone-400";
  const Arrow =
    dir === "up" ? ArrowUpRight : dir === "down" ? ArrowDownRight : ArrowRight;

  return (
    <section className="relative overflow-hidden card-elevated grain anim-fade-up">
      {/* Soft tonal halo behind the card */}
      <div
        className={cn(
          "absolute -top-32 -right-24 h-[380px] w-[380px] rounded-full blur-3xl opacity-30 pointer-events-none",
          haloColor
        )}
      />
      {/* Gradient accent strip — matches AgentCard pattern */}
      <div className={cn("relative h-[3px] w-full bg-gradient-to-r", accentBar)} />

      <div className="relative grid grid-cols-1 lg:grid-cols-[1.1fr_1fr] gap-0">
        {/* Left half — identity + verdict */}
        <div className="p-8 lg:p-10 border-b lg:border-b-0 lg:border-r border-stone-200/80">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-[10.5px] tracking-[0.18em] uppercase text-stone-500">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 anim-pulse-dot" />
              Live run · just now
            </div>
            {/* Signal chip — promoted to header, sits "above and behind" the ticker. */}
            <span
              className={cn(
                "inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-[12px] font-semibold uppercase tracking-wider border",
                signalChip
              )}
            >
              <Arrow className="h-3.5 w-3.5" />
              {signal || "—"}
            </span>
          </div>

          <div className="mt-5 flex items-start gap-4">
            <TickerLogo ticker={ticker} size="lg" />
            <div className="min-w-0 flex-1">
              {(() => {
                const meta = getTickerMeta(ticker);
                return (
                  <>
                    <div className="display text-[34px] font-semibold leading-none tracking-tight text-ink">
                      {meta.symbol}
                    </div>
                    <div className="text-[13px] text-ink-2 mt-2 leading-snug truncate">
                      {meta.nameEn || name}
                    </div>
                    {meta.nameAr && meta.nameAr !== meta.symbol && (
                      <div
                        className="text-[12.5px] text-ink-3 leading-snug truncate mt-0.5"
                        dir="rtl"
                      >
                        {meta.nameAr}
                      </div>
                    )}
                    {confidence && (
                      <span className="mt-3 inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[11.5px] font-medium text-ink-2 border border-stone-200 bg-white">
                        <GaugeIcon className="h-3.5 w-3.5 text-stone-500" />
                        {confidence} confidence
                      </span>
                    )}
                  </>
                );
              })()}
            </div>
          </div>

          <div className="mt-8 grid grid-cols-2 gap-px bg-stone-200 rounded-2xl overflow-hidden border border-stone-200">
            <HeroTile
              label="Time horizon"
              value={horizonHuman}
              hint={horizon}
              icon={<Clock className="h-3.5 w-3.5" />}
            />
            <HeroTile
              label="Implied move"
              value={upside !== undefined ? formatPercent(upside) : "—"}
              hint={
                upside !== undefined
                  ? "current → target"
                  : "needs current price"
              }
              valueClass={upsideClass}
              icon={<Target className="h-3.5 w-3.5" />}
            />
          </div>
        </div>

        {/* Right half — price ladder on warm paper */}
        <div className="p-8 lg:p-10 flex flex-col justify-center gap-6 bg-gradient-to-br from-[#FBFAF6] to-white">
          <PriceRow
            eyebrow="Current price"
            big={current !== undefined ? formatNumber(current) : "—"}
            unit="EGP"
            tag="Spot"
          />
          <div className="relative pl-8">
            <div className="absolute left-2 top-0 bottom-0 w-px bg-gradient-to-b from-stone-300 via-stone-200 to-transparent" />
            <div
              className={cn(
                "absolute left-[3px] top-1 h-2 w-2 rounded-full ring-4 ring-white",
                railColor
              )}
            />
            <PriceRow
              eyebrow="Predicted price"
              big={target !== undefined ? formatNumber(target) : "—"}
              unit="EGP"
              tag={horizonHuman}
              bigClass={targetClass}
            />
          </div>
        </div>
      </div>
    </section>
  );
}

function HeroTile({
  label,
  value,
  hint,
  valueClass,
  icon,
}: {
  label: string;
  value: string;
  hint?: string;
  valueClass?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="bg-white p-5">
      <div className="flex items-center gap-1.5 text-[10.5px] tracking-[0.18em] uppercase text-stone-500">
        {icon}
        {label}
      </div>
      <div
        className={cn(
          "display-num text-[26px] font-semibold mt-2 text-ink",
          valueClass
        )}
      >
        {value}
      </div>
      {hint && <div className="text-[11px] text-stone-500 mt-1">{hint}</div>}
    </div>
  );
}

function PriceRow({
  eyebrow,
  big,
  unit,
  tag,
  bigClass,
}: {
  eyebrow: string;
  big: string;
  unit?: string;
  tag?: string;
  bigClass?: string;
}) {
  return (
    <div>
      <div className="flex items-center justify-between">
        <div className="eyebrow text-stone-500">{eyebrow}</div>
        {tag && (
          <span className="text-[10.5px] tracking-wider uppercase text-stone-500 border border-stone-200 bg-white px-2 py-0.5 rounded-full">
            {tag}
          </span>
        )}
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <span
          className={cn(
            "display-num text-[56px] leading-none font-semibold text-ink",
            bigClass
          )}
        >
          {big}
        </span>
        {unit && (
          <span className="text-[13px] text-stone-500 font-medium">{unit}</span>
        )}
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */

function SectionLabel({ index, label }: { index: string; label: string }) {
  return (
    <div className="flex items-center gap-3 mt-2">
      <span className="mono text-[11px] text-stone-400">{index}</span>
      <div className="eyebrow text-stone-600">{label}</div>
      <div className="h-px flex-1 bg-stone-200" />
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
      <div className="grid grid-cols-2 md:grid-cols-5 gap-px bg-stone-200 rounded-xl overflow-hidden border border-stone-200">
        {items.map((it) => (
          <div key={it.label} className="bg-white p-4">
            <div className="eyebrow text-stone-500">{it.label}</div>
            <div
              className={cn(
                "mt-1.5 mono text-[14px] font-semibold text-ink inline-flex items-center"
              )}
            >
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

/* ────────────────────────────────────────────────────────────────────────── */

function EmptyHero() {
  return (
    <div className="card overflow-hidden grain">
      <div className="px-10 py-14 text-center">
        <div className="inline-flex h-12 w-12 items-center justify-center rounded-xl border border-stone-200 bg-white text-stone-700 mb-5
          dark:border-[var(--hairline)] dark:bg-[var(--bg)] dark:text-[var(--ink-2)]">
          <Sparkles className="h-[18px] w-[18px]" />
        </div>
        <h3 className="display text-[22px] font-semibold text-ink">
          Ready when you are.
        </h3>
        <p className="text-[14px] text-ink-3 mt-2 max-w-md mx-auto leading-relaxed">
          Choose a ticker above and run the multi-agent analysis. Bull, bear,
          judge and portfolio manager will appear here within seconds.
        </p>
      </div>
    </div>
  );
}

function LoadingHero() {
  return (
    <div className="space-y-8">
      <div className="rounded-3xl skeleton h-[280px] border border-stone-200" />
      <div className="grid gap-5 md:grid-cols-2">
        <AgentCardSkeleton tone="bull" />
        <AgentCardSkeleton tone="bear" />
      </div>
      <AgentCardSkeleton tone="judge" />
      <AgentCardSkeleton tone="manager" />
    </div>
  );
}
