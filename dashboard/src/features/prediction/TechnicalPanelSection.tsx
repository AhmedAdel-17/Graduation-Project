import { useState } from "react";
import { Activity, Gauge, Layers } from "lucide-react";
import { cn, formatNumber } from "../../lib/utils";
import type { TechnicalPanel, TechnicalPanelData } from "../../services/api/types";

/* ─ helpers ─ */
type Tone = "up" | "down" | "warning" | "neutral";

function verdictTone(v: unknown): Tone {
  const s = String(v ?? "").toLowerCase();
  if (s === "buy" || s === "strong buy") return "up";
  if (s === "sell" || s === "strong sell") return "down";
  if (s === "high volatility") return "warning";
  return "neutral";
}

// Paper-theme chip classes — matches the AgentCard / PivotLevels palette.
const CHIP: Record<Tone, string> = {
  up: "text-emerald-700 bg-emerald-50 border-emerald-200 dark:bg-emerald-500/10 dark:border-emerald-500/20 dark:text-emerald-400",
  down: "text-rose-700 bg-rose-50 border-rose-200 dark:bg-rose-500/10 dark:border-rose-500/20 dark:text-rose-400",
  warning:
    "text-amber-700 bg-amber-50 border-amber-200 dark:bg-amber-500/10 dark:border-amber-500/20 dark:text-amber-400",
  neutral:
    "text-stone-600 bg-stone-100 border-stone-200 dark:bg-white/5 dark:border-[var(--hairline)] dark:text-ink-3",
};

function num(v: unknown): number | undefined {
  if (v === null || v === undefined) return undefined;
  const n = typeof v === "number" ? v : parseFloat(String(v));
  return Number.isFinite(n) ? n : undefined;
}
function fmt(v: unknown): string {
  const n = num(v);
  return n === undefined ? "—" : formatNumber(n);
}

function Verdict({
  label,
  className,
}: {
  label: unknown;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center px-2 py-0.5 rounded-md border text-[10.5px] font-semibold uppercase tracking-wide",
        CHIP[verdictTone(label)],
        className
      )}
    >
      {String(label ?? "—")}
    </span>
  );
}

/* The 12 indicators of the Investing.com "Technical" panel. */
const INDICATORS: { label: string; valueKey: string; signalKey: string }[] = [
  { label: "RSI (14)", valueKey: "rsi_14", signalKey: "rsi_signal" },
  { label: "STOCH (9,6)", valueKey: "stoch_k_9_6", signalKey: "stoch_signal" },
  { label: "STOCHRSI (14)", valueKey: "stochrsi_14", signalKey: "stochrsi_signal" },
  { label: "MACD (12,26)", valueKey: "macd", signalKey: "macd_signal_verdict" },
  { label: "ADX (14)", valueKey: "adx_14", signalKey: "adx_signal" },
  { label: "Williams %R", valueKey: "williams_r_14", signalKey: "williams_signal" },
  { label: "CCI (14)", valueKey: "cci_14", signalKey: "cci_signal" },
  { label: "ATR (14)", valueKey: "atr_14", signalKey: "atr_signal" },
  { label: "Highs/Lows (14)", valueKey: "high_14", signalKey: "highslows_signal" },
  { label: "Ultimate Oscillator", valueKey: "ultimate_osc", signalKey: "ultimate_signal" },
  { label: "ROC", valueKey: "roc_12", signalKey: "roc_signal" },
  { label: "Bull/Bear Power (13)", valueKey: "bull_bear_power_13", signalKey: "bullbear_signal" },
];

const MA_PERIODS = [5, 10, 20, 50, 100, 200];

const PIVOT_SYSTEMS = [
  { id: "classic", label: "Classic", levels: ["S3", "S2", "S1", "P", "R1", "R2", "R3"] },
  { id: "fib", label: "Fibonacci", levels: ["S3", "S2", "S1", "P", "R1", "R2", "R3"] },
  { id: "cam", label: "Camarilla", levels: ["S4", "S3", "S2", "S1", "P", "R1", "R2", "R3", "R4"] },
  { id: "woodie", label: "Woodie's", levels: ["S2", "S1", "P", "R1", "R2"] },
  { id: "demark", label: "DeMark's", levels: ["S1", "P", "R1"] },
] as const;

export function TechnicalPanelSection({ data }: { data?: TechnicalPanel | null }) {
  const [pivot, setPivot] = useState<(typeof PIVOT_SYSTEMS)[number]["id"]>("classic");

  if (!data || data.error || !data.panel) {
    return null; // silently hide if the panel couldn't be computed
  }
  const p: TechnicalPanelData = data.panel;

  return (
    <section className="card overflow-hidden anim-fade-up">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 px-5 pt-4 pb-3 border-b border-stone-200/80 dark:border-[var(--hairline)] flex-wrap">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-stone-500" />
          <span className="text-[14px] font-semibold text-ink">Technical panel</span>
          <span className="text-[11.5px] text-ink-3">
            · Investing-style summary{data.as_of ? ` · as of ${data.as_of}` : ""}
          </span>
        </div>
        <Verdict label={p.overall_summary} />
      </div>

      <div className="px-5 py-4 space-y-5">
        {/* Summary strip */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <SummaryTile
            icon={<Gauge className="h-3.5 w-3.5" />}
            title="Indicators"
            summary={p.ind_summary}
            counts={`${num(p.ind_buy) ?? 0} buy · ${num(p.ind_sell) ?? 0} sell · ${num(p.ind_neutral) ?? 0} neutral`}
          />
          <SummaryTile
            icon={<Layers className="h-3.5 w-3.5" />}
            title="Moving averages"
            summary={p.ma_summary}
            counts={`${num(p.ma_buy) ?? 0} buy · ${num(p.ma_sell) ?? 0} sell`}
          />
          <SummaryTile
            icon={<Activity className="h-3.5 w-3.5" />}
            title="Overall"
            summary={p.overall_summary}
            counts="indicators + MAs"
          />
        </div>

        {/* Indicators + Moving averages, side by side on wide screens */}
        <div className="grid gap-5 lg:grid-cols-2">
          {/* Indicators */}
          <div>
            <h4 className="eyebrow text-stone-500 mb-2">Technical Indicators</h4>
            <div className="rounded-xl border border-stone-200/80 dark:border-[var(--hairline)] divide-y divide-stone-100 dark:divide-[var(--hairline)] overflow-hidden">
              {INDICATORS.map((ind) => (
                <Row
                  key={ind.valueKey}
                  label={ind.label}
                  value={fmt(p[ind.valueKey])}
                  verdict={p[ind.signalKey]}
                />
              ))}
            </div>
          </div>

          {/* Moving averages */}
          <div>
            <h4 className="eyebrow text-stone-500 mb-2">Moving Averages</h4>
            <div className="rounded-xl border border-stone-200/80 dark:border-[var(--hairline)] divide-y divide-stone-100 dark:divide-[var(--hairline)] overflow-hidden">
              {MA_PERIODS.map((n) => (
                <div key={n} className="flex items-center gap-2 px-3 py-2 text-[12px]">
                  <span className="text-ink-3 w-10 shrink-0">MA{n}</span>
                  <span className="mono text-ink">{fmt(p[`sma_${n}`])}</span>
                  <Verdict label={`SMA ${String(p[`sma_${n}_signal`] ?? "")}`} className="ml-1" />
                  <Verdict label={`EMA ${String(p[`ema_${n}_signal`] ?? "")}`} className="ml-auto" />
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Pivot points */}
        <div>
          <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
            <h4 className="eyebrow text-stone-500">Pivot Points</h4>
            <div className="inline-flex items-center rounded-lg border border-stone-200 dark:border-[var(--hairline)] p-0.5">
              {PIVOT_SYSTEMS.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => setPivot(s.id)}
                  aria-pressed={pivot === s.id}
                  className={cn(
                    "px-2.5 h-7 rounded-md text-[11.5px] font-medium transition-colors",
                    pivot === s.id
                      ? "bg-stone-900 text-white dark:bg-white dark:text-stone-900"
                      : "text-ink-2 hover:bg-stone-100 dark:hover:bg-white/5"
                  )}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
            {(PIVOT_SYSTEMS.find((s) => s.id === pivot)?.levels ?? []).map((lvl) => {
              const key = `pivot_${pivot}_${lvl}`;
              const isR = lvl.startsWith("R");
              const isS = lvl.startsWith("S");
              return (
                <div
                  key={lvl}
                  className={cn(
                    "rounded-xl border px-2.5 py-1.5",
                    isR
                      ? "border-rose-200 bg-rose-50/70 dark:border-rose-500/20 dark:bg-rose-500/10"
                      : isS
                      ? "border-emerald-200 bg-emerald-50/70 dark:border-emerald-500/20 dark:bg-emerald-500/10"
                      : "border-stone-300 bg-stone-100/70 dark:border-[var(--hairline-2)] dark:bg-white/5"
                  )}
                >
                  <div className="text-[10px] uppercase tracking-wider text-ink-3">
                    {lvl === "P" ? "Pivot" : lvl}
                  </div>
                  <div className="display-num text-[14px] font-semibold text-ink">
                    {fmt(p[key])}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <p className="text-[11px] text-ink-3 border-t border-stone-200/80 dark:border-[var(--hairline)] pt-3">
          Values are exact and computed from daily OHLCV (look-ahead-safe); Buy/Sell/Neutral
          verdicts follow standard rules approximating the Investing.com panel. ATR is shown
          as volatility, not a directional signal.
        </p>
      </div>
    </section>
  );
}

function SummaryTile({
  icon,
  title,
  summary,
  counts,
}: {
  icon: React.ReactNode;
  title: string;
  summary: unknown;
  counts: string;
}) {
  return (
    <div className="rounded-xl border border-stone-200/80 dark:border-[var(--hairline)] bg-stone-50/60 dark:bg-white/[0.02] px-3.5 py-3">
      <div className="flex items-center gap-1.5 text-[11px] text-ink-3 mb-2">
        {icon}
        {title}
      </div>
      <Verdict label={summary} />
      <div className="text-[10.5px] text-ink-3 mt-2">{counts}</div>
    </div>
  );
}

function Row({
  label,
  value,
  verdict,
}: {
  label: string;
  value: string;
  verdict: unknown;
}) {
  return (
    <div className="flex items-center gap-2 px-3 py-2 text-[12px]">
      <span className="text-ink-3 flex-1 min-w-0 truncate">{label}</span>
      <span className="mono text-ink">{value}</span>
      <Verdict label={verdict} className="w-[64px]" />
    </div>
  );
}
