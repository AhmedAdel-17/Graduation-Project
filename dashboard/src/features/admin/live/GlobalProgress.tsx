import { CheckCircle2, Clock, Loader2, XCircle, Hourglass } from "lucide-react";
import { cn } from "../../../lib/utils";
import { WS_NODE_TO_CATALOG, catalogNodeById } from "../../../data/agent-catalog";
import { formatDuration, type ExecutionSnapshot } from "./executionModel";

function Stat({
  icon,
  value,
  label,
  cls,
}: {
  icon: React.ReactNode;
  value: string | number;
  label: string;
  cls: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className={cls}>{icon}</span>
      <span className="text-[13px] font-semibold text-ink num">{value}</span>
      <span className="text-[11px] text-stone-500 dark:text-[var(--ink-3)]">{label}</span>
    </div>
  );
}

export function GlobalProgress({
  snapshot,
  isLive,
}: {
  snapshot: ExecutionSnapshot;
  isLive: boolean;
}) {
  const { percent, completedCount, totalCount, failedCount, runningNode, elapsedMs, etaMs } =
    snapshot;
  const pending = Math.max(0, totalCount - completedCount);
  const runningLabel = runningNode
    ? catalogNodeById(WS_NODE_TO_CATALOG[runningNode])?.label ?? runningNode
    : null;

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between gap-3 mb-2.5">
        <div className="flex items-center gap-2 min-w-0">
          {isLive ? (
            <Loader2 className="h-4 w-4 text-blue-500 animate-spin shrink-0" aria-hidden />
          ) : (
            <CheckCircle2 className="h-4 w-4 text-stone-400 dark:text-[var(--ink-3)] shrink-0" aria-hidden />
          )}
          <span className="text-[13px] font-semibold text-ink truncate">
            {isLive
              ? runningLabel
                ? `Running · ${runningLabel}`
                : "Running…"
              : completedCount > 0
              ? "Run complete"
              : "Idle"}
          </span>
        </div>
        <span className="text-[18px] font-semibold display-num text-ink num">{percent}%</span>
      </div>

      {/* Progress bar */}
      <div
        className="h-2 w-full rounded-full bg-stone-100 dark:bg-white/[0.06] overflow-hidden"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={cn(
            "h-full rounded-full transition-all duration-500",
            failedCount > 0 ? "bg-amber-500" : "bg-emerald-500",
            isLive && "bg-blue-500"
          )}
          style={{ width: `${Math.max(2, percent)}%` }}
        />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2">
        <Stat
          icon={<CheckCircle2 className="h-3.5 w-3.5" />}
          value={completedCount - failedCount}
          label="completed"
          cls="text-emerald-500"
        />
        <Stat
          icon={<Hourglass className="h-3.5 w-3.5" />}
          value={pending}
          label="pending"
          cls="text-stone-400 dark:text-[var(--ink-3)]"
        />
        <Stat
          icon={<XCircle className="h-3.5 w-3.5" />}
          value={failedCount}
          label="failed"
          cls="text-red-500"
        />
        <Stat
          icon={<Clock className="h-3.5 w-3.5" />}
          value={formatDuration(elapsedMs)}
          label="elapsed"
          cls="text-stone-400 dark:text-[var(--ink-3)]"
        />
        {isLive && (
          <Stat
            icon={<Clock className="h-3.5 w-3.5" />}
            value={etaMs !== null ? `~${formatDuration(etaMs)}` : "—"}
            label="remaining"
            cls="text-blue-500"
          />
        )}
      </div>
    </div>
  );
}
