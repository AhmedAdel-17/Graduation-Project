import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import { cn } from "../../lib/utils";

export type NodeStatus = "idle" | "in_progress" | "completed" | "error";

const cfg: Record<
  NodeStatus,
  { label: string; cls: string; Icon: React.ComponentType<{ className?: string }> }
> = {
  idle: {
    label: "idle",
    cls: "bg-ink-700/60 text-fg-muted border-line",
    Icon: Circle,
  },
  in_progress: {
    label: "running",
    cls: "bg-accent/10 text-accent border-accent/30",
    Icon: Loader2,
  },
  completed: {
    label: "done",
    cls: "bg-up/10 text-up border-up/30",
    Icon: CheckCircle2,
  },
  error: {
    label: "error",
    cls: "bg-down/10 text-down border-down/30",
    Icon: XCircle,
  },
};

export function StatusPill({
  status,
  label,
  className,
}: {
  status: NodeStatus;
  label?: string;
  className?: string;
}) {
  const c = cfg[status];
  const Icon = c.Icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[10px] font-semibold uppercase tracking-wider",
        c.cls,
        className
      )}
      aria-label={`status: ${label ?? c.label}`}
    >
      <Icon
        className={cn(
          "h-3 w-3",
          status === "in_progress" && "animate-spin"
        )}
        aria-hidden
      />
      {label ?? c.label}
    </span>
  );
}
