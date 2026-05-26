/**
 * Hero card — top of the editorial-style Run page.
 *
 * Layout:
 *   ┌─────────────────────────────────────┬───────────────────────────┐
 *   │ LIVE RUN · JUST NOW                 │ CURRENT PRICE        SPOT │
 *   │                                     │   134.51 EGP              │
 *   │ [COMI]  COMI                        │                           │
 *   │         COMI.CA                     │ PREDICTED PRICE   2-4 WK  │
 *   │  [SELL]  [MEDIUM confidence]        │   130.00 EGP              │
 *   │                                     │                           │
 *   │  TIME HORIZON   IMPLIED MOVE        │                           │
 *   │  2–4 weeks      -3.35%              │                           │
 *   └─────────────────────────────────────┴───────────────────────────┘
 *
 * Has a gradient bar at the top whose colour reflects the decision direction.
 */
import { ArrowDownRight, ArrowUpRight, Minus, Target, Gauge } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Decision } from "@/types/api";
import { formatRelative } from "@/lib/formatters";

interface Props {
  ticker: string;
  decision?: { action: Decision; confidence: number };
  currentPrice?: number;
  targetPrice?: number;
  timeHorizon?: string;
  tradingDays?: number;
  createdAt?: string;
  status?: "running" | "done" | "failed";
}

export function HeroCard({
  ticker,
  decision,
  currentPrice,
  targetPrice,
  timeHorizon = "2 – 4 weeks",
  tradingDays = 14,
  createdAt,
  status = "running",
}: Props) {
  const action = decision?.action;
  const conf = decision?.confidence ? Math.round(decision.confidence * 100) : null;
  const confLabel = conf == null ? null : conf >= 75 ? "HIGH" : conf >= 50 ? "MEDIUM" : "LOW";

  // For HOLD the target IS the current price → implied move is 0%
  // For BUY/SELL with a target → compute the % move
  // Otherwise (no decision yet) → null (shown as "—")
  const impliedMovePct =
    action === "HOLD"
      ? 0
      : currentPrice && targetPrice
        ? ((targetPrice - currentPrice) / currentPrice) * 100
        : null;

  const direction =
    action === "BUY" ? "bull" : action === "SELL" ? "bear" : "neutral";

  // The gradient bar at the top of the card
  const gradient =
    direction === "bull"
      ? "from-emerald-200 via-emerald-50 to-yellow-100"
      : direction === "bear"
      ? "from-rose-200 via-rose-50 to-orange-200"
      : "from-amber-100 via-amber-50 to-lime-100";

  return (
    <div className="relative overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
      {/* Gradient accent bar */}
      <div className={cn("absolute inset-x-0 top-0 h-1 bg-gradient-to-r", gradient)} />

      <div className="grid gap-0 md:grid-cols-[1.2fr_1fr]">
        {/* LEFT — ticker + decision */}
        <div className="space-y-5 p-6 md:p-7">
          {/* Status header */}
          <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.15em] text-muted-foreground">
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                status === "running" && "animate-pulse bg-emerald-500",
                status === "done" && "bg-emerald-500",
                status === "failed" && "bg-rose-500",
              )}
            />
            {status === "running" ? "Live run" : status === "done" ? "Completed run" : "Failed run"}
            {createdAt && (
              <>
                <span className="text-border">·</span>
                <span>{formatRelative(createdAt)}</span>
              </>
            )}
          </div>

          {/* Ticker block */}
          <div className="flex items-center gap-4">
            <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-2xl bg-secondary text-base font-semibold tracking-tight text-foreground">
              {ticker.replace(".CA", "").slice(0, 4)}
            </div>
            <div>
              <div className="text-xs font-medium uppercase tracking-[0.15em] text-muted-foreground">
                {ticker.replace(".CA", "")}
              </div>
              <h1 className="font-serif text-3xl font-semibold leading-tight tracking-tight md:text-4xl">
                {ticker}
              </h1>
              {action && (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <DecisionPill action={action} />
                  {confLabel && <ConfidencePill label={confLabel} />}
                </div>
              )}
            </div>
          </div>

          {/* Metric strip */}
          <div className="grid grid-cols-2 overflow-hidden rounded-xl border border-border">
            <Metric
              icon={<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>}
              label="Time horizon"
              value={timeHorizon}
              sub={`≈ ${tradingDays} trading days`}
            />
            <Metric
              icon={<Target className="h-3.5 w-3.5" />}
              label="Implied move"
              value={impliedMovePct != null ? formatSignedPct(impliedMovePct) : "—"}
              valueClass={
                impliedMovePct != null
                  ? impliedMovePct > 0 ? "text-emerald-700 dark:text-emerald-400"
                  : impliedMovePct < 0 ? "text-rose-700 dark:text-rose-400"
                  : ""
                  : ""
              }
              sub="current → target"
              bordered
            />
          </div>
        </div>

        {/* RIGHT — prices */}
        <div className="border-t border-border bg-secondary/30 p-6 md:border-l md:border-t-0 md:p-7">
          <PriceRow
            label="Current price"
            badge="SPOT"
            value={currentPrice}
            currency="EGP"
          />
          <div className="my-5 h-px bg-border/60" />
          <PriceRow
            label="Predicted price"
            badge={timeHorizon.toUpperCase().replace(/\s/g, " ")}
            value={targetPrice}
            currency="EGP"
            accent={direction}
            fallbackHint={
              action === "HOLD"
                ? "no expected move — hold cash position"
                : !action
                  ? "awaiting risk manager"
                  : undefined
            }
          />
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────

function DecisionPill({ action }: { action: Decision }) {
  if (action === "BUY") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-100/80 px-3 py-1 text-xs font-semibold tracking-wider text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">
        <ArrowUpRight className="h-3.5 w-3.5" />
        BUY
      </span>
    );
  }
  if (action === "SELL") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-rose-100/80 px-3 py-1 text-xs font-semibold tracking-wider text-rose-700 dark:bg-rose-950/40 dark:text-rose-300">
        <ArrowDownRight className="h-3.5 w-3.5" />
        SELL
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-100/80 px-3 py-1 text-xs font-semibold tracking-wider text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
      <Minus className="h-3.5 w-3.5" />
      HOLD
    </span>
  );
}

function ConfidencePill({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1 text-[11px] font-medium tracking-wider text-muted-foreground">
      <Gauge className="h-3.5 w-3.5" />
      {label} confidence
    </span>
  );
}

function Metric({
  icon,
  label,
  value,
  sub,
  bordered,
  valueClass,
}: {
  icon?: React.ReactNode;
  label: string;
  value: string;
  sub?: string;
  bordered?: boolean;
  valueClass?: string;
}) {
  return (
    <div className={cn("space-y-1 p-4", bordered && "border-l border-border")}>
      <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
        {icon}
        <span>{label}</span>
      </div>
      <div className={cn("font-serif text-xl font-semibold tracking-tight", valueClass)}>
        {value}
      </div>
      {sub && <div className="text-[11px] text-muted-foreground">{sub}</div>}
    </div>
  );
}

function PriceRow({
  label,
  badge,
  value,
  currency,
  accent,
  fallbackHint,
}: {
  label: string;
  badge: string;
  value?: number;
  currency: string;
  accent?: "bull" | "bear" | "neutral";
  fallbackHint?: string;
}) {
  const valueClass =
    accent === "bull" ? "text-emerald-700 dark:text-emerald-400"
    : accent === "bear" ? "text-rose-800 dark:text-rose-400"
    : "";
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <div className="flex items-center gap-2">
          {accent && (
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                accent === "bull" ? "bg-emerald-500"
                : accent === "bear" ? "bg-rose-500"
                : "bg-amber-500",
              )}
            />
          )}
          <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
            {label}
          </div>
        </div>
        <div className="mt-2 flex items-baseline gap-2">
          <span className={cn("font-serif text-4xl font-semibold tracking-tight tabular md:text-5xl", valueClass)}>
            {value != null ? value.toFixed(2) : "—"}
          </span>
          <span className="text-xs text-muted-foreground">{currency}</span>
        </div>
        {value == null && fallbackHint && (
          <div className="mt-1 text-[11px] italic text-muted-foreground">{fallbackHint}</div>
        )}
      </div>
      <span className="rounded-full border border-border bg-card px-2.5 py-1 text-[10px] font-medium tracking-wider text-muted-foreground">
        {badge}
      </span>
    </div>
  );
}

function formatSignedPct(n: number): string {
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}
