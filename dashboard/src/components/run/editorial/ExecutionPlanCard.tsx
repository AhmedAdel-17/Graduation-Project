/**
 * Trader execution plan card — "Execution Plan" section.
 *
 *   ┌──────────────────────────────────────────────────────────────────┐
 *   │ [💼]  TRADE CONSTRUCTION              [MEDIUM CONVICTION]         │
 *   │       Portfolio manager                                           │
 *   │                                                                   │
 *   │  ACTION    ENTRY   TARGET   STOP-LOSS   RISK PROFILE              │
 *   │  SELL      134.51  130.00   138.50      MEDIUM                    │
 *   │                                                                   │
 *   │  Sell COMI at the current price of 134.51 EGP, as the short-term  │
 *   │  momentum is decisively bearish...                                │
 *   └──────────────────────────────────────────────────────────────────┘
 */
import { Briefcase } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Decision } from "@/types/api";

interface Props {
  executionPlan?: any;
  currentPrice?: number;
  /** Final decision from the Risk Manager (may differ from Trader's proposal). */
  finalDecision?: Decision;
}

export function ExecutionPlanCard({ executionPlan, currentPrice, finalDecision }: Props) {
  // The TradingAgentsGraph wraps the plan in {execution_plan: {...}} sometimes
  const ep = executionPlan?.execution_plan ?? executionPlan ?? {};
  const traderDecision: Decision | undefined = ep.decision;

  // Headline decision = Risk Manager's final call. Trader's proposal is secondary.
  const decision: Decision | undefined = finalDecision ?? traderDecision;
  const vetoed =
    !!finalDecision &&
    !!traderDecision &&
    finalDecision !== traderDecision;
  const conviction = (ep.conviction || "").toUpperCase();
  const entry = ep.entry_logic || {};
  const entryZone = entry.entry_zone || {};
  const exit = ep.exit_logic || {};
  const risk = ep.risk_management || ep.risk_controls || {};
  const sizing = ep.position_sizing || {};

  const entryPrice =
    entryZone.limit_price ?? entryZone.price_range_low ?? currentPrice;
  const targetPrice =
    exit?.take_profit?.target_1?.price ??
    exit?.take_profit?.price ??
    null;
  const stopLossPrice =
    exit?.stop_loss?.price ?? exit?.stop_loss_price ?? null;

  const rationale =
    ep.rationale || executionPlan?.rationale || executionPlan?.final_recommendation || "";

  const gradient =
    decision === "BUY"
      ? "from-emerald-100 via-amber-50 to-amber-100"
      : decision === "SELL"
      ? "from-amber-100 via-amber-50 to-orange-200"
      : "from-amber-100 via-amber-50 to-lime-100";

  const convictionClass =
    conviction === "HIGH"
      ? "bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300"
      : conviction === "LOW"
      ? "bg-zinc-100 text-zinc-600 dark:bg-zinc-900/40 dark:text-zinc-400"
      : "bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300";

  if (!executionPlan) {
    return (
      <div className="relative overflow-hidden rounded-2xl border border-border bg-card p-6 shadow-sm">
        <div className={cn("absolute inset-x-0 top-0 h-1 bg-gradient-to-r", gradient)} />
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
            <Briefcase className="h-4 w-4" />
          </div>
          <div>
            <div className="text-[11px] font-medium uppercase tracking-[0.15em] text-muted-foreground">
              Trade construction
            </div>
            <h3 className="font-serif text-xl font-semibold leading-tight tracking-tight">
              Portfolio manager
            </h3>
          </div>
        </div>
        <p className="mt-5 text-sm italic text-muted-foreground">
          The Trader hasn't issued an execution plan yet.
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
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
              <Briefcase className="h-4 w-4" />
            </div>
            <div>
              <div className="text-[11px] font-medium uppercase tracking-[0.15em] text-muted-foreground">
                Trade construction
              </div>
              <h3 className="font-serif text-xl font-semibold leading-tight tracking-tight">
                Portfolio manager
              </h3>
            </div>
          </div>
          <div className="flex flex-col items-end gap-1.5">
            {vetoed && (
              <span className="shrink-0 rounded-full bg-rose-100 px-2.5 py-1 text-[10px] font-semibold tracking-wider text-rose-700 dark:bg-rose-950/40 dark:text-rose-300">
                RISK MANAGER OVERRODE → {finalDecision}
              </span>
            )}
            {conviction && (
              <span className={cn(
                "shrink-0 rounded-full px-2.5 py-1 text-[10px] font-semibold tracking-wider",
                convictionClass,
              )}>
                {conviction} CONVICTION
              </span>
            )}
          </div>
        </div>

        {/* Metric strip — when vetoed to HOLD, suppress entry/target/stop */}
        <div className="mt-6 grid grid-cols-2 gap-x-4 gap-y-4 rounded-xl border border-border bg-secondary/30 p-4 md:grid-cols-5">
          <PlanMetric label="Action" value={decision ?? "—"} valueClass={
            decision === "BUY" ? "text-emerald-700 dark:text-emerald-400"
            : decision === "SELL" ? "text-rose-700 dark:text-rose-400"
            : "text-amber-700 dark:text-amber-400"
          } pill />
          {decision === "HOLD" ? (
            <>
              <PlanMetric label="Entry" value="N/A" />
              <PlanMetric label="Target" value="N/A" />
              <PlanMetric label="Stop loss" value="N/A" />
              <PlanMetric label="Risk profile" value={vetoed ? "VETOED" : "—"} />
            </>
          ) : (
            <>
              <PlanMetric label={`Entry${entry.order_type ? ` (${entry.order_type})` : " (spot)"}`} value={fmtEgp(entryPrice)} />
              <PlanMetric label="Target" value={fmtEgp(targetPrice)} />
              <PlanMetric label="Stop loss" value={fmtEgp(stopLossPrice)} valueClass={stopLossPrice ? "text-rose-700 dark:text-rose-400" : ""} />
              <PlanMetric label="Risk profile" value={inferRiskProfile(sizing, stopLossPrice, entryPrice)} />
            </>
          )}
        </div>

        {/* Rationale */}
        {rationale && (
          <p className="mt-5 text-sm leading-relaxed text-foreground/85">
            {truncate(stripJson(rationale), 800)}
          </p>
        )}
      </div>
    </div>
  );
}

function PlanMetric({
  label,
  value,
  valueClass,
  pill,
}: {
  label: string;
  value: string;
  valueClass?: string;
  pill?: boolean;
}) {
  return (
    <div>
      <div className="text-[10px] font-medium uppercase tracking-[0.15em] text-muted-foreground">
        {label}
      </div>
      {pill ? (
        <span
          className={cn(
            "mt-1 inline-flex rounded-md px-2 py-0.5 font-mono text-xs font-semibold tracking-wider",
            value === "BUY" && "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300",
            value === "SELL" && "bg-rose-100 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300",
            value === "HOLD" && "bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300",
          )}
        >
          {value}
        </span>
      ) : (
        <div className={cn("mt-1 font-mono text-sm tabular", valueClass)}>{value}</div>
      )}
    </div>
  );
}

function fmtEgp(v: any): string {
  if (v == null) return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return `${n.toFixed(2)} EGP`;
}

function inferRiskProfile(sizing: any, stop: any, entry: any): string {
  // Heuristic — if user data isn't structured, just say MEDIUM
  if (!stop || !entry) return "MEDIUM";
  const n = Number(stop), e = Number(entry);
  if (Number.isNaN(n) || Number.isNaN(e) || e === 0) return "MEDIUM";
  const dist = Math.abs((n - e) / e);
  if (dist < 0.04) return "LOW";
  if (dist > 0.08) return "HIGH";
  return "MEDIUM";
}

function stripJson(text: string): string {
  return text.replace(/```[\s\S]*?```/g, "").replace(/\{[\s\S]*?\}/, "").trim();
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}
