// Derives per-node timing + global progress from the raw WS event stream.
// Pure functions — no React — so they can be unit-tested and reused by both
// the Agent Monitor (live graph) and the Live Execution page.

import type { AgentUpdate } from "../../../services/api/wsClient";
import { fromNodeStatus, type AgentRunState } from "../shared/status";
import { ANALYST_WS_NODES } from "../../../data/agent-catalog";

// Downstream nodes that always run once the analysts finish (single debate +
// single risk round, as the dashboard always requests).
export const DOWNSTREAM_WS_NODES = [
  "Bull Researcher",
  "Bear Researcher",
  "Research Manager",
  "Trader",
  "Risky Analyst",
  "Safe Analyst",
  "Neutral Analyst",
  "Risk Judge",
];

export interface NodeTiming {
  wsNode: string;
  startedAt: number | null;
  endedAt: number | null;
  durationMs: number | null;
  state: AgentRunState;
}

export interface ExecutionSnapshot {
  timings: Record<string, NodeTiming>;
  expectedNodes: string[];
  totalCount: number;
  completedCount: number;
  failedCount: number;
  runningNode: string | null;
  percent: number;
  elapsedMs: number | null;
  etaMs: number | null;
}

function ts(iso: string | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
}

export function expectedNodesFor(selectedAnalysts: string[]): string[] {
  const analysts = selectedAnalysts
    .map((a) => ANALYST_WS_NODES[a])
    .filter((x): x is string => Boolean(x));
  return [...analysts, ...DOWNSTREAM_WS_NODES];
}

/** Build a per-node timing map from the ordered event list. */
export function buildTimings(events: AgentUpdate[]): Record<string, NodeTiming> {
  const out: Record<string, NodeTiming> = {};
  for (const ev of events) {
    if (!ev.node || ev.node === "System") continue;
    const t = ts(ev.timestamp);
    const existing =
      out[ev.node] ??
      ({
        wsNode: ev.node,
        startedAt: null,
        endedAt: null,
        durationMs: null,
        state: "idle",
      } as NodeTiming);

    if (ev.status === "in_progress") {
      if (existing.startedAt === null) existing.startedAt = t;
      if (existing.state !== "completed" && existing.state !== "failed") {
        existing.state = "running";
      }
    } else if (ev.status === "completed" || ev.status === "error") {
      existing.endedAt = t;
      if (existing.startedAt === null) existing.startedAt = t;
      existing.state = fromNodeStatus(ev.status);
      if (existing.startedAt !== null && existing.endedAt !== null) {
        existing.durationMs = Math.max(0, existing.endedAt - existing.startedAt);
      }
    }
    out[ev.node] = existing;
  }
  return out;
}

export function deriveExecution(
  events: AgentUpdate[],
  selectedAnalysts: string[],
  isLive: boolean,
  now: number
): ExecutionSnapshot {
  const timings = buildTimings(events);
  const expectedNodes = expectedNodesFor(selectedAnalysts);
  const totalCount = expectedNodes.length;

  let completedCount = 0;
  let failedCount = 0;
  let runningNode: string | null = null;

  for (const node of expectedNodes) {
    const tm = timings[node];
    if (!tm) continue;
    if (tm.state === "completed") completedCount++;
    else if (tm.state === "failed") {
      failedCount++;
      completedCount++; // a failed node is "done" for progress purposes
    } else if (tm.state === "running") runningNode = node;
  }

  // Durations of finished nodes → average for ETA.
  const finishedDurations = expectedNodes
    .map((n) => timings[n]?.durationMs)
    .filter((d): d is number => typeof d === "number" && d > 0);
  const avgDur =
    finishedDurations.length > 0
      ? finishedDurations.reduce((a, b) => a + b, 0) / finishedDurations.length
      : null;

  const remaining = Math.max(0, totalCount - completedCount);
  const etaMs = isLive && avgDur ? Math.round(remaining * avgDur) : null;

  // Elapsed = from earliest start to now (live) or last end (done).
  const starts = Object.values(timings)
    .map((t) => t.startedAt)
    .filter((s): s is number => s !== null);
  const ends = Object.values(timings)
    .map((t) => t.endedAt)
    .filter((e): e is number => e !== null);
  const firstStart = starts.length ? Math.min(...starts) : null;
  const lastEnd = ends.length ? Math.max(...ends) : null;
  const elapsedMs =
    firstStart === null ? null : (isLive ? now : lastEnd ?? now) - firstStart;

  const percent =
    totalCount === 0 ? 0 : Math.round((completedCount / totalCount) * 100);

  return {
    timings,
    expectedNodes,
    totalCount,
    completedCount,
    failedCount,
    runningNode,
    percent,
    elapsedMs,
    etaMs,
  };
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s % 60);
  return `${m}m ${rem}s`;
}
