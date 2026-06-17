import { useMemo } from "react";
import { cn } from "../../../lib/utils";
import {
  CATALOG_NODES,
  PHASE_LABELS,
  type AgentPhase,
  type CatalogNode,
} from "../../../data/agent-catalog";
import { AgentStatusBadge } from "../shared/AgentStatusBadge";
import { STATUS_META, type AgentRunState } from "../shared/status";
import { formatDuration, type ExecutionSnapshot } from "./executionModel";

function fmtClock(ms: number | null): string {
  if (ms === null) return "";
  try {
    return new Date(ms).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return "";
  }
}

export function ExecutionTimeline({
  snapshot,
  now,
  isLive,
  onSelect,
}: {
  snapshot: ExecutionSnapshot;
  now: number;
  isLive: boolean;
  onSelect?: (node: CatalogNode) => void;
}) {
  // Expected nodes mapped to their catalog entry, preserving catalog (phase) order.
  const rows = useMemo(() => {
    const expected = new Set(snapshot.expectedNodes);
    return CATALOG_NODES.filter((n) => n.wsNode && expected.has(n.wsNode));
  }, [snapshot.expectedNodes]);

  const grouped = useMemo(() => {
    const out: { phase: AgentPhase; nodes: CatalogNode[] }[] = [];
    for (const n of rows) {
      let b = out.find((g) => g.phase === n.phase);
      if (!b) {
        b = { phase: n.phase, nodes: [] };
        out.push(b);
      }
      b.nodes.push(n);
    }
    return out;
  }, [rows]);

  return (
    <ol className="flex flex-col gap-4" aria-live="polite" aria-label="Execution timeline">
      {grouped.map((group) => (
        <li key={group.phase} className="flex flex-col gap-1.5">
          <p className="text-[10px] font-semibold uppercase tracking-[0.15em] text-stone-400 dark:text-[var(--ink-3)] px-1">
            {PHASE_LABELS[group.phase]}
          </p>
          <ul className="flex flex-col gap-1">
            {group.nodes.map((node) => {
              const tm = node.wsNode ? snapshot.timings[node.wsNode] : undefined;
              const state: AgentRunState = tm?.state ?? "idle";
              const liveDur =
                state === "running" && tm?.startedAt
                  ? now - tm.startedAt
                  : tm?.durationMs ?? null;
              const m = STATUS_META[state];
              const clickable = Boolean(onSelect);
              return (
                <li key={node.id}>
                  <button
                    type="button"
                    disabled={!clickable}
                    onClick={() => onSelect?.(node)}
                    className={cn(
                      "w-full flex items-center justify-between gap-2 px-2.5 py-2 rounded-md border text-left transition-colors",
                      state === "running"
                        ? "border-blue-200 bg-blue-50/60 dark:border-sky-900/40 dark:bg-sky-900/10"
                        : "border-transparent hover:bg-stone-100/70 dark:hover:bg-white/[0.04]",
                      clickable ? "cursor-pointer" : "cursor-default"
                    )}
                  >
                    <span className="flex items-center gap-2 min-w-0">
                      <span className={cn("h-1.5 w-1.5 rounded-full shrink-0", m.dot, m.pulse && "anim-pulse-dot")} aria-hidden />
                      <span className="text-[12.5px] text-ink truncate">{node.label}</span>
                    </span>
                    <span className="flex items-center gap-2.5 shrink-0">
                      {tm?.startedAt && (
                        <span className="text-[10.5px] text-stone-400 dark:text-[var(--ink-3)] num hidden sm:inline">
                          {fmtClock(tm.startedAt)}
                        </span>
                      )}
                      {liveDur !== null && (
                        <span className="text-[11px] font-medium text-stone-500 dark:text-[var(--ink-2)] num">
                          {formatDuration(liveDur)}
                        </span>
                      )}
                      <AgentStatusBadge state={state} showDot={false} />
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </li>
      ))}
      {!isLive && snapshot.completedCount === 0 && (
        <li className="text-[12px] text-stone-400 dark:text-[var(--ink-3)] px-1">
          No run in progress. Start a run to populate the timeline.
        </li>
      )}
    </ol>
  );
}
