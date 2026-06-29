import { useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronDown,
  ChevronUp,
  Code2,
  Search,
  Shield,
  ShieldAlert,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { cn } from "../../lib/utils";
import type { DecisionExplanation, DecisionStep } from "../../services/api/types";

/* ═══════════════════════════════════════════════════════════════════════
   DecisionCard — shared recommendation story component

   Used by:
     - HomeScreen (from PredictionResult.decision_explanation)
     - LatestDecision hero (from ShadowRun.pipeline_audit.signal_chain)
     - RunDetailModal (same)

   Shows a user-friendly narrative of the recommendation chain, not raw
   agent output. The developer details (pipeline_audit, raw snapshots)
   live in an optional collapsible section.
   ═══════════════════════════════════════════════════════════════════════ */

// ── Public prop types ────────────────────────────────────────────────

interface DecisionCardProps {
  /** User-facing headline, e.g. "StockHive found a BUY setup, but …" */
  headline?: string;
  /** Ordered decision trail steps */
  steps?: DecisionStep[];
  /** Was the recommendation blocked by risk rules? */
  riskBlocked?: boolean;
  /** Variant controls visual weight */
  variant?: "hero" | "card" | "inline";
  /** Optional developer/audit payload — rendered in collapsible section */
  developerData?: Record<string, unknown> | null;
  /** Label for the developer section */
  developerLabel?: string;
}

// ── Fallback for old runs with no decision_explanation ────────────────

const FALLBACK_HEADLINE =
  "StockHive evaluated this ticker but no detailed decision trail is available for this recommendation.";

// ── Build from various sources ───────────────────────────────────────

/** Build DecisionCardProps from a PredictionResult's decision_explanation */
export function fromPredictionResult(
  explanation?: DecisionExplanation | null,
): Pick<DecisionCardProps, "headline" | "steps" | "riskBlocked"> {
  if (!explanation) {
    return { headline: undefined, steps: undefined, riskBlocked: false };
  }
  return {
    headline: explanation.headline,
    steps: explanation.steps,
    riskBlocked: explanation.risk_blocked,
  };
}

/** Build DecisionCardProps from a ShadowRun's pipeline_audit */
export function fromShadowRun(
  pipelineAudit?: Record<string, unknown> | null,
): Pick<DecisionCardProps, "headline" | "steps" | "riskBlocked"> {
  if (!pipelineAudit) {
    return { headline: undefined, steps: undefined, riskBlocked: false };
  }
  const chain = pipelineAudit.signal_chain as
    | Record<string, string | null>
    | undefined;
  const riskVeto = !!pipelineAudit.risk_veto;

  // Build steps from signal_chain
  const steps: DecisionStep[] = [];
  if (chain?.research_manager) {
    steps.push({
      agent: "Research Manager",
      signal: chain.research_manager,
      detail: "Evaluated bull/bear debate and market evidence",
    });
  }
  if (chain?.trader) {
    steps.push({
      agent: "Trader",
      signal: chain.trader,
      detail: "Built execution plan with position sizing",
    });
  }
  if (riskVeto) {
    steps.push({
      agent: "Risk Scorer",
      signal: "VETO",
      detail: "Deterministic risk check blocked the position",
    });
  } else if (chain?.risk_scorer === "PASS") {
    steps.push({
      agent: "Risk Manager",
      signal: chain.final || "HOLD",
      detail: "Approved after risk debate",
    });
  }

  // Build headline
  let headline: string | undefined;
  if (riskVeto && chain?.trader && chain.trader !== "HOLD") {
    headline = `StockHive found a ${chain.trader} setup, but recommended HOLD because risk rules blocked the position.`;
  } else if (chain?.final === "HOLD") {
    headline = "StockHive recommended HOLD based on the current analysis.";
  } else if (chain?.final) {
    headline = `StockHive recommended ${chain.final} after passing all risk checks.`;
  }

  return { headline, steps, riskBlocked: riskVeto };
}

// ── Main component ───────────────────────────────────────────────────

export function DecisionCard({
  headline,
  steps,
  riskBlocked,
  variant = "card",
  developerData,
  developerLabel = "Developer details",
}: DecisionCardProps) {
  const hasSteps = steps && steps.length > 0;
  const hasHeadline = !!headline;
  const isEmpty = !hasSteps && !hasHeadline;

  if (isEmpty && !developerData) return null;

  const isHero = variant === "hero";
  const isInline = variant === "inline";

  return (
    <div
      className={cn(
        isHero
          ? "" // hero variant is unstyled — parent provides the card wrapper
          : isInline
          ? ""
          : "card overflow-hidden anim-fade-up",
      )}
    >
      {/* ── Headline ──────────────────────────────────────────────── */}
      {(hasHeadline || isEmpty) && (
        <div
          className={cn(
            "flex items-start gap-2.5",
            isHero ? "px-0 py-0" : isInline ? "py-0" : "px-5 pt-4 pb-3",
          )}
        >
          {riskBlocked ? (
            <ShieldAlert className="h-4 w-4 mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" />
          ) : (
            <ShieldCheck className="h-4 w-4 mt-0.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
          )}
          <p
            className={cn(
              "text-[13px] leading-relaxed",
              isHero
                ? "text-white/80"
                : "text-ink-2",
            )}
          >
            {headline || FALLBACK_HEADLINE}
          </p>
        </div>
      )}

      {/* ── Decision trail ────────────────────────────────────────── */}
      {hasSteps && (
        <div
          className={cn(
            isHero ? "mt-3" : isInline ? "mt-2" : "px-5 pb-4 pt-1",
          )}
        >
          {!isHero && !isInline && (
            <div className="flex items-center gap-2 mb-3">
              <Search className="h-3.5 w-3.5 text-stone-400" />
              <span className="text-[11px] font-semibold uppercase tracking-wider text-stone-500">
                Decision path
              </span>
            </div>
          )}
          <ol className="space-y-0">
            {steps!.map((step, i) => (
              <StepRow
                key={i}
                step={step}
                isLast={i === steps!.length - 1}
                isHero={isHero}
              />
            ))}
          </ol>
        </div>
      )}

      {/* ── Developer details (collapsible) ───────────────────────── */}
      {developerData && (
        <DeveloperSection
          label={developerLabel}
          data={developerData}
          isHero={isHero}
        />
      )}
    </div>
  );
}

// ── Step row ─────────────────────────────────────────────────────────

function StepRow({
  step,
  isLast,
  isHero,
}: {
  step: DecisionStep;
  isLast: boolean;
  isHero: boolean;
}) {
  const isBuy = step.signal === "BUY" || step.signal === "STRONG_BUY";
  const isVeto = step.signal === "VETO";
  const isHold = step.signal === "HOLD";

  const Icon = isVeto ? XCircle : isHold && isLast ? Shield : isBuy ? Check : ArrowRight;
  const iconColor = isVeto
    ? "text-amber-600 dark:text-amber-400"
    : isBuy
    ? "text-emerald-600 dark:text-emerald-400"
    : "text-stone-400";
  const ringColor = isVeto
    ? "border-amber-300 dark:border-amber-700"
    : isBuy
    ? "border-emerald-300 dark:border-emerald-700"
    : "border-stone-200 dark:border-[var(--hairline)]";

  // Friendly agent label mapping
  const agentLabel = AGENT_LABELS[step.agent] || step.agent;

  return (
    <li className="flex items-start gap-3 relative">
      {/* Connector line */}
      {!isLast && (
        <span className="absolute left-[11px] top-[22px] bottom-0 w-px bg-stone-200 dark:bg-[var(--hairline)]" />
      )}
      {/* Icon dot */}
      <span
        className={cn(
          "flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full border bg-white dark:bg-[var(--paper)]",
          ringColor,
        )}
      >
        <Icon className={cn("h-3 w-3", iconColor)} />
      </span>
      {/* Content */}
      <div className={cn("pb-3 min-w-0", isLast && "pb-0")}>
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={cn(
              "text-[12.5px] font-semibold",
              isHero ? "text-white" : "text-ink",
            )}
          >
            {agentLabel}
          </span>
          <StepBadge signal={step.signal} isHero={isHero} />
        </div>
        <p
          className={cn(
            "text-[11.5px] mt-0.5 leading-relaxed",
            isHero ? "text-white/50" : "text-ink-3",
          )}
        >
          {step.detail}
        </p>
      </div>
    </li>
  );
}

// ── Step signal badge ────────────────────────────────────────────────

function StepBadge({ signal, isHero }: { signal: string; isHero: boolean }) {
  const s = signal.toUpperCase();
  const color =
    s === "BUY" || s === "STRONG_BUY"
      ? isHero
        ? "bg-emerald-500/30 text-emerald-100 border-emerald-400/30"
        : "bg-emerald-50 text-emerald-700 border-emerald-200"
      : s === "SELL" || s === "STRONG_SELL"
      ? isHero
        ? "bg-rose-500/30 text-rose-100 border-rose-400/30"
        : "bg-rose-50 text-rose-700 border-rose-200"
      : s === "VETO"
      ? isHero
        ? "bg-amber-500/30 text-amber-100 border-amber-400/30"
        : "bg-amber-50 text-amber-700 border-amber-200"
      : s === "PASS"
      ? isHero
        ? "bg-emerald-500/30 text-emerald-100 border-emerald-400/30"
        : "bg-emerald-50 text-emerald-700 border-emerald-200"
      : isHero
      ? "bg-white/10 text-white/70 border-white/20"
      : "bg-stone-50 text-stone-700 border-stone-200";

  return (
    <span
      className={cn(
        "px-1.5 py-0.5 rounded-md border text-[10px] font-semibold uppercase tracking-wide",
        color,
      )}
    >
      {signal}
    </span>
  );
}

// ── Friendly agent labels ────────────────────────────────────────────

const AGENT_LABELS: Record<string, string> = {
  "Research Manager": "Research found opportunity",
  "Trader": "Trader built a plan",
  "Risk Scorer": "Risk rules reviewed it",
  "Risk Manager": "Risk review approved",
};

// ── Developer details ────────────────────────────────────────────────

function DeveloperSection({
  label,
  data,
  isHero,
}: {
  label: string;
  data: Record<string, unknown>;
  isHero: boolean;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div
      className={cn(
        "border-t",
        isHero
          ? "border-white/10 mt-3"
          : "border-stone-200 dark:border-[var(--hairline)]",
      )}
    >
      <button
        onClick={() => setOpen(!open)}
        className={cn(
          "flex items-center gap-2 w-full px-5 py-2.5 text-[11px] font-medium uppercase tracking-wider transition-colors",
          isHero
            ? "text-white/40 hover:text-white/60"
            : "text-stone-400 hover:text-stone-600 dark:hover:text-[var(--ink-2)]",
        )}
      >
        <Code2 className="h-3 w-3" />
        {label}
        {open ? (
          <ChevronUp className="h-3 w-3 ml-auto" />
        ) : (
          <ChevronDown className="h-3 w-3 ml-auto" />
        )}
      </button>
      {open && (
        <div
          className={cn(
            "px-5 pb-4 text-[11px] leading-relaxed",
            isHero ? "text-white/40" : "text-ink-3",
          )}
        >
          <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono">
            {JSON.stringify(data, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
