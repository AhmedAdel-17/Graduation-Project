import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AdminShell } from "../layout/AdminShell";
import { RunControls } from "../live/RunControls";
import { GlobalProgress } from "../live/GlobalProgress";
import { useLiveRun } from "../live/useLiveRun";
import { useLiveFeed } from "../live/useLiveFeed";
import { deriveExecution } from "../live/executionModel";
import { useTheme } from "../../../hooks/useTheme";
import { useAgentAggregates } from "../shared/useAdminData";
import { AgentDetailDrawer } from "../details/AgentDetailDrawer";
import { AgentNode } from "./AgentNode";
import { buildFlowNodes, buildFlowEdges, type NodeRuntime } from "./graphModel";
import {
  CATALOG_NODES,
  type CatalogNode,
} from "../../../data/agent-catalog";
import { deriveLiveStates } from "./deriveStates";
import type { AgentRunState } from "../shared/status";

const nodeTypes = { agent: AgentNode };

export function AgentMonitorPage() {
  const { theme } = useTheme();
  const run = useLiveRun(); // manual run started here
  const feed = useLiveFeed(); // passive watch of runs started anywhere
  const { stream } = run;
  const [selected, setSelected] = useState<CatalogNode | null>(null);

  // Real per-agent aggregates from the audit DB (executions / confidence /
  // last-seen). Empty when Postgres is unavailable — no fabricated values.
  const { byNodeId } = useAgentAggregates(30);

  // Unified view: manual run takes precedence; otherwise the watched feed run
  // (so a pipeline launched from the main dashboard animates this graph too).
  const manualActive = run.isLive || stream.events.length > 0;
  const watching = !manualActive && feed.activeRun != null;
  const events = watching ? feed.activeRun!.events : stream.events;
  const analysts = watching ? feed.activeRun!.analysts : run.analysts;
  const isLive = watching ? feed.activeRun!.status === "running" : run.isLive;
  const hasRun = manualActive || watching;

  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    if (!isLive) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [isLive]);

  const snapshot = useMemo(
    () => deriveExecution(events, analysts, isLive, now),
    [events, analysts, isLive, now]
  );

  const onOpen = useCallback((node: CatalogNode) => setSelected(node), []);

  // Per-node state across the whole graph (streaming + derived/inferred).
  const stateById = useMemo<Record<string, AgentRunState>>(
    () => deriveLiveStates(snapshot, isLive),
    [snapshot, isLive]
  );

  const runtimeOf = useCallback(
    (node: CatalogNode): NodeRuntime => {
      const agg = byNodeId[node.id];
      const tm = node.wsNode ? snapshot.timings[node.wsNode] : undefined;
      const state = hasRun ? stateById[node.id] ?? "idle" : "idle";
      const liveDur =
        state === "running" && tm?.startedAt ? now - tm.startedAt : tm?.durationMs ?? null;
      const lastRunAt = tm?.endedAt
        ? new Date(tm.endedAt).toISOString()
        : agg?.lastSeen ?? null;
      return {
        state,
        durationMs: liveDur,
        lastRunAt,
        executions: agg?.executions ?? null,
        avgConfidence: agg?.avgConfidence ?? null,
      };
    },
    [byNodeId, snapshot.timings, stateById, now, hasRun]
  );

  const nodes = useMemo(() => buildFlowNodes(runtimeOf, onOpen), [runtimeOf, onOpen]);
  const edges: Edge[] = useMemo(() => buildFlowEdges(stateById), [stateById]);

  return (
    <AdminShell
      title="Pipeline Graph"
      subtitle="Interactive map of the EGX multi-agent pipeline"
    >
      <div className="flex flex-col gap-5">
        {watching && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg border border-blue-200 bg-blue-50 text-blue-700 text-[12px] dark:border-sky-900/40 dark:bg-sky-900/15 dark:text-sky-300">
            <span className="h-1.5 w-1.5 rounded-full bg-blue-500 anim-pulse-dot" aria-hidden />
            <span>
              Watching a run{feed.activeRun!.ticker ? ` for ${feed.activeRun!.ticker}` : ""}
              {feed.activeRun!.source === "main" ? " started from the main dashboard" : ""}.
            </span>
          </div>
        )}
        <RunControls isLive={run.isLive} onStart={run.start} onCancel={run.cancel} />
        {hasRun && <GlobalProgress snapshot={snapshot} isLive={isLive} />}

        <div className="card overflow-hidden">
          <div
            className="h-[calc(100vh-300px)] min-h-[480px] w-full bg-[var(--bg)]"
            aria-label="Agent architecture graph"
          >
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              colorMode={theme}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable
              fitView
              fitViewOptions={{ padding: 0.15 }}
              minZoom={0.3}
              maxZoom={1.8}
              proOptions={{ hideAttribution: false }}
            >
              <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
              <Controls showInteractive={false} />
              <MiniMap
                pannable
                zoomable
                nodeStrokeWidth={2}
                nodeColor={(n) => {
                  const s = (n.data as { state?: AgentRunState } | undefined)?.state;
                  if (s === "running") return "#3b82f6";
                  if (s === "completed") return "#10b981";
                  if (s === "failed") return "#ef4444";
                  return "#cbd5e1";
                }}
                className="!bg-white/70 dark:!bg-[var(--paper)]/70"
              />
            </ReactFlow>
          </div>
        </div>

        <Legend total={CATALOG_NODES.length} />
      </div>

      <AgentDetailDrawer
        node={selected}
        runtime={selected ? runtimeOf(selected) : null}
        onClose={() => setSelected(null)}
      />
    </AdminShell>
  );
}

function Legend({ total }: { total: number }) {
  const items: { c: string; l: string }[] = [
    { c: "bg-stone-300 dark:bg-stone-600", l: "Idle" },
    { c: "bg-amber-400", l: "Waiting" },
    { c: "bg-blue-500", l: "Running" },
    { c: "bg-emerald-500", l: "Completed" },
    { c: "bg-red-500", l: "Failed" },
  ];
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 text-[11px] text-stone-500 dark:text-[var(--ink-3)]">
      <div className="flex flex-wrap items-center gap-3">
        {items.map((i) => (
          <span key={i.l} className="inline-flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-full ${i.c}`} aria-hidden />
            {i.l}
          </span>
        ))}
      </div>
      <span>{total} pipeline stages · click any node for details</span>
    </div>
  );
}
