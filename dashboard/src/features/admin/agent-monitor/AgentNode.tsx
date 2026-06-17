import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Bot, Cpu, Database, Flag, type LucideIcon } from "lucide-react";
import { cn } from "../../../lib/utils";
import { STATUS_META } from "../shared/status";
import { formatDuration } from "../live/executionModel";
import type { AgentFlowNode } from "./graphModel";
import type { NodeKind } from "../../../data/agent-catalog";

const KIND_ICON: Record<NodeKind, LucideIcon> = {
  source: Database,
  process: Cpu,
  agent: Bot,
  output: Flag,
};

function relTime(iso: string | null): string {
  if (!iso) return "never";
  const diff = Date.now() - Date.parse(iso);
  if (Number.isNaN(diff)) return "—";
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function AgentNodeInner({ data, selected }: NodeProps<AgentFlowNode>) {
  const { node, state, durationMs, lastRunAt, executions, avgConfidence, onOpen } = data;
  const m = STATUS_META[state];
  const Icon = KIND_ICON[node.kind];

  return (
    <button
      type="button"
      onClick={() => onOpen(node)}
      className={cn(
        "w-[186px] text-left rounded-xl border bg-white dark:bg-[var(--paper)] shadow-sm transition-all",
        "border-stone-200 dark:border-[var(--hairline)]",
        state === "running" && "ring-2 ring-blue-400/50 border-blue-300 dark:border-sky-700",
        state === "failed" && "border-red-300 dark:border-red-900/60",
        state === "completed" && "border-emerald-300/70 dark:border-emerald-900/50",
        selected && "ring-2 ring-stone-400/60"
      )}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-1.5 !w-1.5 !bg-stone-300 dark:!bg-stone-600 !border-0"
      />
      <div className="px-3 py-2.5">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "h-6 w-6 shrink-0 rounded-md flex items-center justify-center border",
              node.kind === "agent"
                ? "bg-blue-50 border-blue-100 text-blue-600 dark:bg-sky-900/20 dark:border-sky-900/40 dark:text-sky-300"
                : node.kind === "output"
                ? "bg-emerald-50 border-emerald-100 text-emerald-600 dark:bg-emerald-900/20 dark:border-emerald-900/40 dark:text-emerald-300"
                : "bg-stone-50 border-stone-100 text-stone-500 dark:bg-white/[0.04] dark:border-[var(--hairline)] dark:text-[var(--ink-3)]"
            )}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden />
          </span>
          <span className="text-[12px] font-semibold text-ink leading-tight truncate flex-1">
            {node.label}
          </span>
          <span
            className={cn("h-2 w-2 rounded-full shrink-0", m.dot, m.pulse && "anim-pulse-dot")}
            title={m.label}
            aria-label={`status: ${m.label}`}
          />
        </div>

        <div className="mt-1.5 flex items-center justify-between gap-2 text-[10px] text-stone-400 dark:text-[var(--ink-3)]">
          <span className={cn("uppercase tracking-wider font-medium", m.text)}>{m.label}</span>
          <span className="num">{formatDuration(durationMs)}</span>
        </div>

        <div className="mt-1 flex items-center justify-between gap-2 text-[9.5px] text-stone-400 dark:text-[var(--ink-3)]">
          <span className="num truncate">{relTime(lastRunAt)}</span>
          {executions !== null && executions > 0 && (
            <span className="num" title="executions / avg confidence">
              {executions}×{avgConfidence !== null ? ` · ${Math.round(avgConfidence * 100)}%` : ""}
            </span>
          )}
        </div>
      </div>
      <Handle
        type="source"
        position={Position.Right}
        className="!h-1.5 !w-1.5 !bg-stone-300 dark:!bg-stone-600 !border-0"
      />
    </button>
  );
}

export const AgentNode = memo(AgentNodeInner);
