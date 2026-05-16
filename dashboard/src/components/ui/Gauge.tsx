import { cn } from "../../lib/utils";

export type GaugeTone = "brand" | "up" | "down" | "accent" | "warning";

const toneClass: Record<GaugeTone, { bar: string; text: string }> = {
  brand: { bar: "bg-brand-500", text: "text-brand-400" },
  up: { bar: "bg-up", text: "text-up" },
  down: { bar: "bg-down", text: "text-down" },
  accent: { bar: "bg-accent", text: "text-accent" },
  warning: { bar: "bg-amber-400", text: "text-amber-400" },
};

// Horizontal gauge — value in [0, max]. Use for confidence, data completeness,
// signal coherence, etc. Renders a label row + clamped progress bar.
export function Gauge({
  value,
  max = 100,
  label,
  hint,
  tone = "brand",
  decimals = 0,
  unit,
  className,
}: {
  value: number | undefined | null;
  max?: number;
  label?: string;
  hint?: string;
  tone?: GaugeTone;
  decimals?: number;
  unit?: string;
  className?: string;
}) {
  const v =
    typeof value === "number" && Number.isFinite(value) ? value : null;
  const pct = v === null ? 0 : Math.max(0, Math.min(100, (v / max) * 100));
  const display = v === null ? "—" : v.toFixed(decimals) + (unit ?? "");
  const styles = toneClass[tone];

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      {(label || hint) && (
        <div className="flex items-baseline justify-between gap-2">
          {label && (
            <span className="text-[11px] uppercase tracking-wider text-fg-muted">
              {label}
            </span>
          )}
          <span className={cn("num text-xs font-semibold", styles.text)}>
            {display}
          </span>
        </div>
      )}
      <div
        role="progressbar"
        aria-valuenow={v ?? undefined}
        aria-valuemin={0}
        aria-valuemax={max}
        className="relative h-1.5 w-full rounded-full bg-ink-800 overflow-hidden"
      >
        <div
          className={cn("absolute top-0 start-0 h-full rounded-full", styles.bar)}
          style={{ width: `${pct}%` }}
        />
      </div>
      {hint && <p className="text-[10px] text-fg-subtle">{hint}</p>}
    </div>
  );
}
