import { useId } from "react";
import { cn } from "../../../lib/utils";

// Lightweight, dependency-free, theme-aware chart primitives for the
// Performance Analytics screen. SVG + CSS only (lightweight-charts is for
// financial time-series and is overkill for bars/donuts here).

export interface BarItem {
  label: string;
  value: number;
  /** Pre-formatted display value (defaults to value). */
  display?: string;
  tone?: "blue" | "emerald" | "amber" | "red" | "violet" | "stone";
}

const BAR_TONE: Record<NonNullable<BarItem["tone"]>, string> = {
  blue: "bg-blue-500",
  emerald: "bg-emerald-500",
  amber: "bg-amber-500",
  red: "bg-red-500",
  violet: "bg-violet-500",
  stone: "bg-stone-400 dark:bg-stone-500",
};

export function BarList({
  items,
  tone = "blue",
  className,
}: {
  items: BarItem[];
  tone?: NonNullable<BarItem["tone"]>;
  className?: string;
}) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {items.map((it) => (
        <div key={it.label} className="flex items-center gap-2.5">
          <span className="w-32 shrink-0 text-[11.5px] text-stone-600 dark:text-[var(--ink-2)] truncate">
            {it.label}
          </span>
          <div className="flex-1 h-2.5 rounded-full bg-stone-100 dark:bg-white/[0.06] overflow-hidden">
            <div
              className={cn("h-full rounded-full transition-all duration-500", BAR_TONE[it.tone ?? tone])}
              style={{ width: `${Math.max(2, (it.value / max) * 100)}%` }}
            />
          </div>
          <span className="w-16 shrink-0 text-right text-[11.5px] font-medium text-ink num">
            {it.display ?? it.value}
          </span>
        </div>
      ))}
      {items.length === 0 && (
        <p className="text-[12px] text-stone-400 dark:text-[var(--ink-3)]">No data.</p>
      )}
    </div>
  );
}

export interface TrendPoint {
  label: string;
  value: number;
}

export function TrendChart({
  points,
  height = 120,
  tone = "#3b82f6",
}: {
  points: TrendPoint[];
  height?: number;
  tone?: string;
}) {
  const gradId = useId();
  if (points.length === 0) {
    return <p className="text-[12px] text-stone-400 dark:text-[var(--ink-3)]">No data.</p>;
  }
  const W = 600;
  const H = height;
  const pad = 6;
  const max = Math.max(1, ...points.map((p) => p.value));
  const stepX = points.length > 1 ? (W - pad * 2) / (points.length - 1) : 0;
  const y = (v: number) => H - pad - (v / max) * (H - pad * 2);
  const x = (i: number) => pad + i * stepX;
  const line = points.map((p, i) => `${x(i)},${y(p.value)}`).join(" ");
  const area = `${pad},${H - pad} ${line} ${x(points.length - 1)},${H - pad}`;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height }} preserveAspectRatio="none" role="img">
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={tone} stopOpacity="0.25" />
          <stop offset="100%" stopColor={tone} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon points={area} fill={`url(#${gradId})`} />
      <polyline points={line} fill="none" stroke={tone} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      {points.map((p, i) => (
        <circle key={i} cx={x(i)} cy={y(p.value)} r="2" fill={tone}>
          <title>{`${p.label}: ${p.value}`}</title>
        </circle>
      ))}
    </svg>
  );
}

export interface DonutSlice {
  label: string;
  value: number;
  color: string;
}

export function DonutChart({ slices, size = 132 }: { slices: DonutSlice[]; size?: number }) {
  const total = slices.reduce((a, b) => a + b.value, 0);
  const r = size / 2 - 10;
  const cx = size / 2;
  const cy = size / 2;
  const circ = 2 * Math.PI * r;
  let offset = 0;

  return (
    <div className="flex items-center gap-4">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label="Decision mix">
        <circle cx={cx} cy={cy} r={r} fill="none" strokeWidth="12" className="stroke-stone-100 dark:stroke-white/[0.06]" />
        {total > 0 &&
          slices.map((s) => {
            const frac = s.value / total;
            const dash = frac * circ;
            const el = (
              <circle
                key={s.label}
                cx={cx}
                cy={cy}
                r={r}
                fill="none"
                stroke={s.color}
                strokeWidth="12"
                strokeDasharray={`${dash} ${circ - dash}`}
                strokeDashoffset={-offset}
                transform={`rotate(-90 ${cx} ${cy})`}
              >
                <title>{`${s.label}: ${s.value}`}</title>
              </circle>
            );
            offset += dash;
            return el;
          })}
        <text x={cx} y={cy - 2} textAnchor="middle" className="fill-ink text-[18px] font-semibold display-num">
          {total}
        </text>
        <text x={cx} y={cy + 14} textAnchor="middle" className="fill-stone-400 dark:fill-[var(--ink-3)] text-[9px] uppercase tracking-wider">
          runs
        </text>
      </svg>
      <ul className="flex flex-col gap-1.5">
        {slices.map((s) => (
          <li key={s.label} className="flex items-center gap-2 text-[12px]">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} aria-hidden />
            <span className="text-stone-600 dark:text-[var(--ink-2)]">{s.label}</span>
            <span className="font-medium text-ink num ml-auto">{s.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
