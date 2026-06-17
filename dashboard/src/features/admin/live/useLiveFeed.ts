import { useEffect, useMemo, useState } from "react";
import {
  openLiveFeed,
  type LiveFrame,
  type LiveSnapshotRun,
} from "../../../services/api/liveFeedClient";
import type { AgentUpdate, NodeStatus } from "../../../services/api/wsClient";
import type { AnalystKey } from "./useLiveRun";

export interface FeedRun {
  runId: string;
  ticker: string;
  source: string;
  status: "running" | "complete" | "error";
  startedAt: number;
  lastAt: number;
  analysts: AnalystKey[];
  events: AgentUpdate[];
}

const ALL_ANALYSTS: AnalystKey[] = ["market", "fundamentals", "news", "social"];

function frameToEvent(f: LiveFrame): AgentUpdate {
  const type: AgentUpdate["type"] =
    f.type === "complete" ? "complete" : f.type === "error" ? "error" : "agent_update";
  return {
    type,
    node: f.node,
    status: (f.status as NodeStatus) ?? "in_progress",
    state_keys: f.state_keys ?? [],
    data: f.data ?? {},
    timestamp: f.timestamp,
  };
}

function applyFrame(run: FeedRun | undefined, f: LiveFrame): FeedRun {
  const at = Date.parse(f.timestamp) || Date.now();
  const next: FeedRun =
    run ?? {
      runId: f.run_id,
      ticker: f.ticker ?? "",
      source: f.source ?? "unknown",
      status: "running",
      startedAt: at,
      lastAt: at,
      analysts: ALL_ANALYSTS,
      events: [],
    };

  const events = [...next.events, frameToEvent(f)];
  const merged: FeedRun = {
    ...next,
    events: events.length > 400 ? events.slice(events.length - 400) : events,
    lastAt: at,
    ticker: f.ticker ?? next.ticker,
  };

  if (f.type === "run_started") {
    merged.startedAt = at;
    const sel = f.data?.selected_analysts;
    if (Array.isArray(sel)) {
      merged.analysts = sel.filter((x): x is AnalystKey => typeof x === "string");
    }
  }
  if (f.type === "complete") merged.status = "complete";
  else if (f.type === "error") merged.status = "error";

  return merged;
}

function buildRun(snap: LiveSnapshotRun): FeedRun {
  let run: FeedRun | undefined;
  for (const f of snap.frames) run = applyFrame(run, f);
  if (!run) {
    run = {
      runId: snap.run_id,
      ticker: snap.meta.ticker ?? "",
      source: snap.meta.source ?? "unknown",
      status: (snap.status as FeedRun["status"]) ?? "running",
      startedAt: Date.parse(snap.meta.started_at ?? "") || Date.now(),
      lastAt: Date.now(),
      analysts: ALL_ANALYSTS,
      events: [],
    };
  } else {
    run.status = (snap.status as FeedRun["status"]) ?? run.status;
  }
  return run;
}

function pickActive(runs: Record<string, FeedRun>): FeedRun | null {
  const list = Object.values(runs);
  if (list.length === 0) return null;
  const running = list.filter((r) => r.status === "running");
  const pool = running.length > 0 ? running : list;
  return pool.reduce((a, b) => (b.lastAt > a.lastAt ? b : a));
}

// Read-only subscription to /api/admin/live. Surfaces the most relevant run
// (a running one, else the most recent) so the admin can watch a pipeline
// started from ANYWHERE — including the main dashboard.
export function useLiveFeed(): {
  activeRun: FeedRun | null;
  runs: Record<string, FeedRun>;
  connected: boolean;
} {
  const [runs, setRuns] = useState<Record<string, FeedRun>>({});
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const handle = openLiveFeed({
      onStatus: setConnected,
      onSnapshot: (snap) =>
        setRuns((prev) => {
          const next = { ...prev };
          for (const r of snap) next[r.run_id] = buildRun(r);
          return next;
        }),
      onFrame: (f) =>
        setRuns((prev) => ({ ...prev, [f.run_id]: applyFrame(prev[f.run_id], f) })),
    });
    return () => handle.close();
  }, []);

  const activeRun = useMemo(() => pickActive(runs), [runs]);
  return { activeRun, runs, connected };
}
