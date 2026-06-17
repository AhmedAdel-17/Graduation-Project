import { cn } from "../../../lib/utils";
import { STATUS_META, type AgentRunState } from "./status";

export function AgentStatusBadge({
  state,
  className,
  showDot = true,
}: {
  state: AgentRunState;
  className?: string;
  showDot?: boolean;
}) {
  const m = STATUS_META[state];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[10.5px] font-semibold uppercase tracking-wider",
        m.chip,
        className
      )}
      aria-label={`status: ${m.label}`}
    >
      {showDot && (
        <span
          className={cn("h-1.5 w-1.5 rounded-full", m.dot, m.pulse && "anim-pulse-dot")}
          aria-hidden
        />
      )}
      {m.label}
    </span>
  );
}
