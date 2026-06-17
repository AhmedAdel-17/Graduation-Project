import { useMemo } from "react";
import { cn } from "../../../lib/utils";
import { catalogNodeByAgentName, PHASE_LABELS } from "../../../data/agent-catalog";
import type { SessionTraceEvent } from "../../../services/api/types";

// Pulls a few headline fields out of structured_output to present as a
// "decision summary" — intentionally NOT the raw chain-of-thought. We only
// surface fields the agent chose to emit as structured output.
const HEADLINE_KEYS = [
  "recommendation",
  "action",
  "signal",
  "verdict",
  "decision",
  "conviction",
  "conviction_level",
  "stance",
];
const LIST_KEYS = ["key_risks", "catalysts", "risks", "invalidation_triggers", "drivers"];

interface Highlight {
  label: string;
  value: string;
}

function highlightsOf(structured: Record<string, unknown> | null | undefined): Highlight[] {
  if (!structured) return [];
  const out: Highlight[] = [];
  for (const k of HEADLINE_KEYS) {
    const v = structured[k];
    if (v !== undefined && v !== null && typeof v !== "object") {
      out.push({ label: k.replace(/_/g, " "), value: String(v) });
    }
  }
  for (const k of LIST_KEYS) {
    const v = structured[k];
    if (Array.isArray(v) && v.length > 0) {
      out.push({ label: k.replace(/_/g, " "), value: `${v.length}` });
    }
  }
  return out.slice(0, 5);
}

export function ThinkingTimeline({ events }: { events: SessionTraceEvent[] }) {
  const steps = useMemo(
    () =>
      events
        .filter((e) => e.agent_name)
        .map((e, i) => {
          const node = catalogNodeByAgentName(e.agent_name ?? "");
          return {
            i,
            label: node?.label ?? e.agent_name ?? "Agent",
            phase: node ? PHASE_LABELS[node.phase] : null,
            summary: e.opinion_summary ?? null,
            highlights: highlightsOf(e.structured_output),
          };
        }),
    [events]
  );

  if (steps.length === 0) {
    return (
      <p className="text-[12.5px] text-stone-400 dark:text-[var(--ink-3)] px-1">
        No reasoning steps recorded for this session.
      </p>
    );
  }

  return (
    <ol className="relative flex flex-col gap-0">
      {steps.map((s, idx) => (
        <li key={s.i} className="relative flex gap-3 pb-5 last:pb-0">
          {/* Rail */}
          <div className="flex flex-col items-center">
            <span className="h-6 w-6 shrink-0 rounded-full border-2 border-blue-400 dark:border-sky-500 bg-white dark:bg-[var(--paper)] text-[11px] font-semibold text-blue-600 dark:text-sky-400 flex items-center justify-center num">
              {idx + 1}
            </span>
            {idx < steps.length - 1 && (
              <span className="w-px flex-1 bg-stone-200 dark:bg-[var(--hairline)] mt-1" aria-hidden />
            )}
          </div>
          {/* Body */}
          <div className="min-w-0 flex-1 pb-1">
            <div className="flex items-center gap-2">
              <span className="text-[13px] font-semibold text-ink">{s.label}</span>
              {s.phase && (
                <span className="text-[10px] uppercase tracking-wider text-stone-400 dark:text-[var(--ink-3)]">
                  {s.phase}
                </span>
              )}
            </div>
            {s.summary && (
              <p className="text-[12.5px] text-stone-600 dark:text-[var(--ink-2)] leading-relaxed mt-1">
                {s.summary}
              </p>
            )}
            {s.highlights.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {s.highlights.map((h) => (
                  <span
                    key={h.label}
                    className={cn(
                      "inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10.5px] border",
                      "bg-stone-50 text-stone-600 border-stone-200 dark:bg-white/[0.03] dark:text-[var(--ink-2)] dark:border-[var(--hairline)]"
                    )}
                  >
                    <span className="uppercase tracking-wider text-stone-400 dark:text-[var(--ink-3)]">
                      {h.label}
                    </span>
                    <span className="font-medium">{h.value}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
