import { useMemo, useState } from "react";
import { Layers } from "lucide-react";
import { cn, formatNumber, formatPercent } from "../../lib/utils";
import type { StockBar } from "../../services/api/types";

type PivotMethod = "classic" | "fib";
type Band = "r" | "p" | "s";

interface PivotLevel {
  name: string;
  value: number;
  band: Band;
}

function computePivots(bar: StockBar, method: PivotMethod): PivotLevel[] {
  const h = Number(bar.high);
  const l = Number(bar.low);
  const c = Number(bar.close);
  const p = (h + l + c) / 3;
  const range = h - l;

  if (method === "fib") {
    return [
      { name: "R3", value: p + range, band: "r" },
      { name: "R2", value: p + 0.618 * range, band: "r" },
      { name: "R1", value: p + 0.382 * range, band: "r" },
      { name: "Pivot", value: p, band: "p" },
      { name: "S1", value: p - 0.382 * range, band: "s" },
      { name: "S2", value: p - 0.618 * range, band: "s" },
      { name: "S3", value: p - range, band: "s" },
    ];
  }
  // Classic floor-trader pivots.
  return [
    { name: "R3", value: h + 2 * (p - l), band: "r" },
    { name: "R2", value: p + range, band: "r" },
    { name: "R1", value: 2 * p - l, band: "r" },
    { name: "Pivot", value: p, band: "p" },
    { name: "S1", value: 2 * p - h, band: "s" },
    { name: "S2", value: p - range, band: "s" },
    { name: "S3", value: l - 2 * (h - p), band: "s" },
  ];
}

const BAND_STYLE: Record<Band, { dot: string; chip: string; label: string }> = {
  r: {
    dot: "bg-rose-500",
    chip: "text-rose-700 bg-rose-50 border-rose-200",
    label: "Resistance",
  },
  p: {
    dot: "bg-stone-500",
    chip: "text-stone-700 bg-stone-100 border-stone-200",
    label: "Pivot",
  },
  s: {
    dot: "bg-emerald-500",
    chip: "text-emerald-700 bg-emerald-50 border-emerald-200",
    label: "Support",
  },
};

type Row =
  | { kind: "level"; level: PivotLevel }
  | { kind: "current"; value: number };

export function PivotLevels({
  bars,
  current,
}: {
  bars: StockBar[];
  current?: number;
}) {
  const [method, setMethod] = useState<PivotMethod>("classic");

  // Most recent completed session drives the pivots.
  const lastBar = useMemo(() => {
    if (!bars || bars.length === 0) return undefined;
    return [...bars].sort((a, b) =>
      a.date < b.date ? -1 : a.date > b.date ? 1 : 0
    )[bars.length - 1];
  }, [bars]);

  const levels = useMemo(
    () => (lastBar ? computePivots(lastBar, method) : []),
    [lastBar, method]
  );

  // Interleave the live price into the descending level ladder.
  const rows = useMemo<Row[]>(() => {
    const base: Row[] = levels.map((level) => ({ kind: "level", level }));
    if (current == null || levels.length === 0) return base;
    const out: Row[] = [];
    let placed = false;
    for (const r of base) {
      if (!placed && r.kind === "level" && current >= r.level.value) {
        out.push({ kind: "current", value: current });
        placed = true;
      }
      out.push(r);
    }
    if (!placed) out.push({ kind: "current", value: current });
    return out;
  }, [levels, current]);

  if (!lastBar) {
    return (
      <section className="card overflow-hidden anim-fade-up">
        <Header method={method} onMethod={setMethod} />
        <div className="px-5 py-8 text-center text-[13px] text-ink-3">
          No price history was returned for this run, so support and resistance
          levels can't be computed.
        </div>
      </section>
    );
  }

  return (
    <section className="card overflow-hidden anim-fade-up">
      <Header method={method} onMethod={setMethod} barDate={lastBar.date} />
      <div className="divide-y divide-stone-100 dark:divide-[var(--hairline)]">
        {rows.map((row) => {
          if (row.kind === "current") {
            return (
              <div
                key="live"
                className="flex items-center gap-3 px-5 py-2.5 bg-stone-900 text-white dark:bg-white dark:text-stone-900"
              >
                <span className="h-2 w-2 rounded-full bg-emerald-400 anim-pulse-dot shrink-0" />
                <span className="text-[11px] font-semibold tracking-[0.14em] uppercase">
                  Live price
                </span>
                <span className="display-num text-[15px] font-semibold ml-auto">
                  {formatNumber(row.value)} EGP
                </span>
              </div>
            );
          }
          const { level } = row;
          const style = BAND_STYLE[level.band];
          const dist =
            current != null && current > 0
              ? ((level.value - current) / current) * 100
              : undefined;
          return (
            <div
              key={level.name}
              className="flex items-center gap-3 px-5 py-2.5"
            >
              <span
                className={cn("h-2 w-2 rounded-full shrink-0", style.dot)}
                aria-hidden
              />
              <span
                className={cn(
                  "px-2 py-0.5 rounded-md text-[11px] font-semibold border w-[52px] text-center shrink-0",
                  style.chip
                )}
              >
                {level.name}
              </span>
              <span className="text-[11.5px] text-ink-3 hidden sm:inline">
                {style.label}
              </span>
              <span className="display-num text-[15px] font-semibold text-ink ml-auto">
                {formatNumber(level.value)}
              </span>
              <span
                className={cn(
                  "mono text-[11.5px] w-[64px] text-right shrink-0",
                  dist === undefined
                    ? "text-ink-3"
                    : dist >= 0
                    ? "text-emerald-700"
                    : "text-rose-700"
                )}
              >
                {dist !== undefined ? formatPercent(dist) : "—"}
              </span>
            </div>
          );
        })}
      </div>
      <div className="px-5 py-2.5 border-t border-stone-200/80 dark:border-[var(--hairline)] text-[11px] text-ink-3">
        {method === "classic"
          ? "Classic floor-trader pivots — derived from the prior session's high, low and close."
          : "Fibonacci pivots — 38.2% / 61.8% / 100% retracements of the prior session's range."}
      </div>
    </section>
  );
}

function Header({
  method,
  onMethod,
  barDate,
}: {
  method: PivotMethod;
  onMethod: (m: PivotMethod) => void;
  barDate?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3 px-5 pt-4 pb-3 border-b border-stone-200/80 dark:border-[var(--hairline)] flex-wrap">
      <div className="flex items-center gap-2">
        <Layers className="h-4 w-4 text-stone-500" />
        <span className="text-[14px] font-semibold text-ink">
          Support &amp; resistance
        </span>
        {barDate && (
          <span className="text-[11.5px] text-ink-3">· {barDate} session</span>
        )}
      </div>
      <div
        className="inline-flex items-center rounded-lg border border-stone-200 dark:border-[var(--hairline)] p-0.5"
        role="group"
        aria-label="Pivot method"
      >
        {(
          [
            { id: "classic", label: "Classic" },
            { id: "fib", label: "Fibonacci" },
          ] as const
        ).map((opt) => {
          const active = method === opt.id;
          return (
            <button
              key={opt.id}
              type="button"
              onClick={() => onMethod(opt.id)}
              aria-pressed={active}
              className={cn(
                "px-2.5 h-7 rounded-md text-[12px] font-medium transition-colors",
                active
                  ? "bg-stone-900 text-white dark:bg-white dark:text-stone-900"
                  : "text-ink-2 hover:bg-stone-100 dark:hover:bg-white/5"
              )}
            >
              {opt.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
