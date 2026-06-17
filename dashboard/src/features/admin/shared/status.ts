// Unified agent execution state for the Admin Monitoring Suite.
// Maps the WS NodeStatus (idle | in_progress | completed | error) onto the
// five states the spec asks for, adding a derived "waiting" state for nodes
// that have not started yet while a run is in flight.

import type { NodeStatus } from "../../../services/api/wsClient";

export type AgentRunState =
  | "idle"
  | "waiting"
  | "running"
  | "completed"
  | "failed";

export interface StatusMeta {
  label: string;
  /** Text + border colour classes (theme-aware). */
  text: string;
  /** Soft chip background classes (theme-aware). */
  chip: string;
  /** Solid dot colour. */
  dot: string;
  pulse?: boolean;
}

export const STATUS_META: Record<AgentRunState, StatusMeta> = {
  idle: {
    label: "Idle",
    text: "text-stone-500 dark:text-[var(--ink-3)]",
    chip: "bg-stone-100 text-stone-600 border-stone-200 dark:bg-white/[0.04] dark:text-[var(--ink-3)] dark:border-[var(--hairline)]",
    dot: "bg-stone-300 dark:bg-stone-600",
  },
  waiting: {
    label: "Waiting",
    text: "text-amber-600 dark:text-amber-400",
    chip: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/20 dark:text-amber-300 dark:border-amber-900/40",
    dot: "bg-amber-400",
  },
  running: {
    label: "Running",
    text: "text-blue-600 dark:text-sky-400",
    chip: "bg-blue-50 text-blue-700 border-blue-200 dark:bg-sky-900/20 dark:text-sky-300 dark:border-sky-900/40",
    dot: "bg-blue-500",
    pulse: true,
  },
  completed: {
    label: "Completed",
    text: "text-emerald-600 dark:text-emerald-400",
    chip: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-900/20 dark:text-emerald-300 dark:border-emerald-900/40",
    dot: "bg-emerald-500",
  },
  failed: {
    label: "Failed",
    text: "text-red-600 dark:text-red-400",
    chip: "bg-red-50 text-red-700 border-red-200 dark:bg-red-900/20 dark:text-red-300 dark:border-red-900/40",
    dot: "bg-red-500",
  },
};

export function fromNodeStatus(s: NodeStatus | undefined): AgentRunState {
  switch (s) {
    case "in_progress":
      return "running";
    case "completed":
      return "completed";
    case "error":
      return "failed";
    default:
      return "idle";
  }
}
