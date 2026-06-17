import type { ReactNode } from "react";
import { FlaskConical } from "lucide-react";

/**
 * Wraps a what-if block in unmistakable "scenario" styling: a dashed amber
 * frame, a flask badge and a faint watermark. Confusing a hypothetical with an
 * adopted plan is the single worst UX failure this feature can have (design
 * §10 / risk register), so the treatment is deliberately loud.
 */
export function HypotheticalFrame({
  label,
  children,
}: {
  label?: string;
  children: ReactNode;
}) {
  return (
    <div className="relative rounded-xl border border-dashed border-amber-300 dark:border-amber-700/60 bg-amber-50/30 dark:bg-amber-900/[0.06] p-1.5">
      <div className="flex items-center gap-1.5 px-2 pt-1 pb-1.5">
        <FlaskConical className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400" aria-hidden />
        <span className="text-[11px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">
          Scenario{label ? ` · ${label}` : ""}
        </span>
      </div>
      <div className="relative">
        <span
          className="pointer-events-none absolute right-2 top-1 text-[34px] font-black uppercase tracking-tight text-amber-500/[0.07] dark:text-amber-300/[0.06] select-none"
          aria-hidden
        >
          Scenario
        </span>
        {children}
      </div>
    </div>
  );
}
