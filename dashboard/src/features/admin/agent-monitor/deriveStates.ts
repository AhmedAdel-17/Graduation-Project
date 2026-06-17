// Derives a status for EVERY catalog node during/after a run — not just the
// nodes that stream live frames. Streaming nodes come straight from the WS
// timings; the deterministic stages around them (data sources, gateway,
// aggregation barrier, signal processor, final recommendation) are *inferred*
// from the streaming states so the whole graph animates end-to-end. Inference
// is presentational only; it never feeds back into the pipeline.

import { CATALOG_NODES } from "../../../data/agent-catalog";
import type { AgentRunState } from "../shared/status";
import type { ExecutionSnapshot } from "../live/executionModel";

const ANALYST_IDS = [
  "market-analyst",
  "fundamentals-analyst",
  "news-analyst",
  "social-analyst",
];

const DONE: AgentRunState[] = ["completed", "failed"];

export function deriveLiveStates(
  snapshot: ExecutionSnapshot,
  isLive: boolean
): Record<string, AgentRunState> {
  const out: Record<string, AgentRunState> = {};
  const expected = new Set(snapshot.expectedNodes);

  // 1) Streaming nodes — straight from timings.
  for (const node of CATALOG_NODES) {
    if (!node.wsNode) continue;
    const tm = snapshot.timings[node.wsNode];
    if (tm) out[node.id] = tm.state;
    else out[node.id] = isLive && expected.has(node.wsNode) ? "waiting" : "idle";
  }

  const stateOf = (id: string): AgentRunState => out[id] ?? "idle";
  const anyAnalystStarted = ANALYST_IDS.some((id) => stateOf(id) !== "idle");
  // Only analysts that are part of this run (selected) gate the barrier.
  const selectedAnalysts = ANALYST_IDS.filter((id) => {
    const n = CATALOG_NODES.find((c) => c.id === id);
    return n?.wsNode ? expected.has(n.wsNode) : false;
  });
  const allAnalystsDone =
    selectedAnalysts.length > 0 &&
    selectedAnalysts.every((id) => DONE.includes(stateOf(id)));

  const upstreamConsumed = anyAnalystStarted;

  const inferUpstream = (): AgentRunState =>
    upstreamConsumed ? "completed" : isLive ? "running" : "idle";

  // 2) Data sources + data layer — consumed once any analyst begins.
  for (const id of [
    "src-market",
    "src-fundamentals",
    "src-news",
    "src-social",
    "data-gateway",
    "prefetch",
    "sentiment",
  ]) {
    out[id] = inferUpstream();
  }

  // 3) Analyst aggregation barrier.
  out["analysts-sync"] = allAnalystsDone
    ? "completed"
    : anyAnalystStarted
    ? "running"
    : isLive
    ? "waiting"
    : "idle";

  // 4) Signal processor — follows the risk judge.
  const riskJudge = stateOf("risk-judge");
  out["signal"] = DONE.includes(riskJudge)
    ? "completed"
    : riskJudge === "running"
    ? "running"
    : isLive
    ? "waiting"
    : "idle";

  // 5) Final recommendation — once the signal is resolved.
  out["final"] = out["signal"] === "completed" ? "completed" : isLive ? "waiting" : "idle";

  return out;
}
