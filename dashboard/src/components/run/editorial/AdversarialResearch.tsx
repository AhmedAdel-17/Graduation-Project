/**
 * Side-by-side Bull / Bear researcher cards — the "Adversarial research" section.
 *
 *   ┌────────────────────────────────┬────────────────────────────────┐
 *   │ THE CONSTRUCTIVE CASE     [LONG]│ THE CAUTIONARY CASE   [RISK-OFF]│
 *   │ ↗ Bull researcher              │ ↘ Bear researcher              │
 *   │                                │                                │
 *   │ - bullet 1                     │ - bullet 1                     │
 *   │ - bullet 2                     │ - bullet 2                     │
 *   │                                │                                │
 *   │ IMPLIED UPSIDE   TARGET         │ DOWNSIDE TO STOP   STOP        │
 *   │   +15%           150 EGP        │   -3%              130 EGP     │
 *   └────────────────────────────────┴────────────────────────────────┘
 */
import { TrendingUp, TrendingDown } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  bull: any;
  bear: any;
  currentPrice?: number;
}

export function AdversarialResearch({ bull, bear, currentPrice }: Props) {
  return (
    <div className="grid gap-5 md:grid-cols-2">
      <ResearcherCard side="bull" data={bull} currentPrice={currentPrice} />
      <ResearcherCard side="bear" data={bear} currentPrice={currentPrice} />
    </div>
  );
}

function ResearcherCard({
  side,
  data,
  currentPrice,
}: {
  side: "bull" | "bear";
  data: any;
  currentPrice?: number;
}) {
  const isBull = side === "bull";

  const gradient = isBull
    ? "from-emerald-200 via-emerald-50 to-teal-100"
    : "from-rose-200 via-rose-50 to-orange-200";

  const icon = isBull ? <TrendingUp className="h-4 w-4" /> : <TrendingDown className="h-4 w-4" />;
  const iconBg = isBull
    ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-950/40 dark:text-emerald-400"
    : "bg-rose-50 text-rose-600 dark:bg-rose-950/40 dark:text-rose-400";

  const eyebrow = isBull ? "The constructive case" : "The cautionary case";
  const title = isBull ? "Bull researcher" : "Bear researcher";
  const badgeLabel = isBull ? "LONG THESIS" : "RISK-OFF THESIS";
  const badgeClass = isBull
    ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
    : "bg-rose-100 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300";

  // Build bullet points from the thesis data
  const bullets = isBull
    ? [
        ...(data?.key_catalysts || []),
        ...(data?.invalidation_conditions
          ? [`Invalidation: ${(data.invalidation_conditions[0] || "").slice(0, 200)}`]
          : []),
      ].slice(0, 5)
    : [...(data?.key_risks || [])].slice(0, 5);

  // Bottom metrics
  const upside = data?.upside_scenario || {};
  const baseUpPct = isBull ? upside.base_case_upside_pct : null;
  const downsidePct = !isBull && data?.downside_range
    ? computeDownsidePct(currentPrice, data.downside_range)
    : null;

  const bullTarget = isBull && currentPrice && baseUpPct != null
    ? currentPrice * (1 + baseUpPct / 100)
    : null;
  const bearStop = !isBull && data?.downside_range?.support_level_1
    ? Number(data.downside_range.support_level_1)
    : null;

  if (!data) {
    return (
      <div className="relative overflow-hidden rounded-2xl border border-border bg-card p-6 shadow-sm">
        <div className={cn("absolute inset-x-0 top-0 h-1 bg-gradient-to-r", gradient)} />
        <div className="flex items-center gap-3">
          <div className={cn("flex h-9 w-9 items-center justify-center rounded-xl", iconBg)}>
            {icon}
          </div>
          <div>
            <div className="text-[11px] font-medium uppercase tracking-[0.15em] text-muted-foreground">
              {eyebrow}
            </div>
            <h3 className="font-serif text-xl font-semibold tracking-tight">{title}</h3>
          </div>
        </div>
        <p className="mt-5 text-sm italic text-muted-foreground">
          No {side} thesis recorded for this run.
        </p>
      </div>
    );
  }

  return (
    <div className="relative overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
      <div className={cn("absolute inset-x-0 top-0 h-1 bg-gradient-to-r", gradient)} />
      <div className="p-6 md:p-7">
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className={cn("flex h-10 w-10 items-center justify-center rounded-xl", iconBg)}>
              {icon}
            </div>
            <div>
              <div className="text-[11px] font-medium uppercase tracking-[0.15em] text-muted-foreground">
                {eyebrow}
              </div>
              <h3 className="font-serif text-xl font-semibold leading-tight tracking-tight">
                {title}
              </h3>
            </div>
          </div>
          <span className={cn(
            "shrink-0 rounded-full px-2.5 py-1 text-[10px] font-semibold tracking-wider",
            badgeClass,
          )}>
            {badgeLabel}
          </span>
        </div>

        {/* Bullet list */}
        {bullets.length > 0 ? (
          <ul className="mt-5 space-y-2 text-sm leading-relaxed text-foreground/85">
            {bullets.map((b: string, i: number) => (
              <li key={i} className="flex gap-2">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-muted-foreground/50" />
                <span>{b}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-5 text-sm italic text-muted-foreground">
            No {isBull ? "bullish catalysts" : "bearish risks"} listed.
          </p>
        )}

        {/* Bottom metric strip */}
        <div className="mt-6 border-t border-border pt-4">
          <div className="grid grid-cols-2 gap-4">
            {isBull ? (
              <>
                <BottomMetric
                  label="Implied upside"
                  value={baseUpPct != null ? `${baseUpPct > 0 ? "+" : ""}${baseUpPct.toFixed(2)}%` : "—"}
                  valueClass={baseUpPct != null && baseUpPct < 0 ? "text-rose-700 dark:text-rose-400" : "text-emerald-700 dark:text-emerald-400"}
                />
                <BottomMetric
                  label="Target"
                  value={bullTarget != null ? `${bullTarget.toFixed(2)} EGP` : "—"}
                />
              </>
            ) : (
              <>
                <BottomMetric
                  label="Downside to stop"
                  value={downsidePct != null ? `${downsidePct > 0 ? "+" : ""}${downsidePct.toFixed(2)}%` : "—"}
                  valueClass={downsidePct != null && downsidePct < 0 ? "text-rose-700 dark:text-rose-400" : "text-emerald-700 dark:text-emerald-400"}
                />
                <BottomMetric
                  label="Stop"
                  value={bearStop != null ? `${bearStop.toFixed(2)} EGP` : "—"}
                />
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function BottomMetric({
  label,
  value,
  valueClass,
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div>
      <div className="text-[10px] font-medium uppercase tracking-[0.15em] text-muted-foreground">
        {label}
      </div>
      <div className={cn("mt-1 font-mono text-sm tabular", valueClass)}>{value}</div>
    </div>
  );
}

function computeDownsidePct(currentPrice: number | undefined, downside: any): number | null {
  if (!currentPrice || !downside) return null;
  const stop = Number(downside.support_level_1 || downside.support_level_2);
  if (!stop || Number.isNaN(stop)) return null;
  return ((stop - currentPrice) / currentPrice) * 100;
}
