// Static layout + React Flow node/edge construction for the Agent Architecture
// Monitor. Positions are deterministic (column per phase, stacked vertically),
// so the graph never shuffles between renders. Live status + metrics are
// injected by the page; this module only owns geometry + wiring.

import type { Edge, Node } from "@xyflow/react";
import {
  CATALOG_EDGES,
  CATALOG_NODES,
  PHASE_ORDER,
  type CatalogNode,
} from "../../../data/agent-catalog";
import type { AgentRunState } from "../shared/status";

export interface AgentNodeData extends Record<string, unknown> {
  node: CatalogNode;
  state: AgentRunState;
  durationMs: number | null;
  lastRunAt: string | null;
  executions: number | null;
  avgConfidence: number | null;
  onOpen: (node: CatalogNode) => void;
}

export type AgentFlowNode = Node<AgentNodeData, "agent">;

const COL_GAP = 250;
const ROW_GAP = 104;

/** Deterministic {x,y} per catalog node id. */
export function computeLayout(): Record<string, { x: number; y: number }> {
  const byPhase = PHASE_ORDER.map((phase) =>
    CATALOG_NODES.filter((n) => n.phase === phase)
  );
  const maxRows = Math.max(...byPhase.map((c) => c.length));
  const pos: Record<string, { x: number; y: number }> = {};

  byPhase.forEach((nodes, phaseIdx) => {
    const startY = ((maxRows - nodes.length) / 2) * ROW_GAP;
    nodes.forEach((n, rowIdx) => {
      pos[n.id] = { x: phaseIdx * COL_GAP, y: startY + rowIdx * ROW_GAP };
    });
  });
  return pos;
}

const LAYOUT = computeLayout();

export interface NodeRuntime {
  state: AgentRunState;
  durationMs: number | null;
  lastRunAt: string | null;
  executions: number | null;
  avgConfidence: number | null;
}

export function buildFlowNodes(
  runtimeOf: (node: CatalogNode) => NodeRuntime,
  onOpen: (node: CatalogNode) => void
): AgentFlowNode[] {
  return CATALOG_NODES.map((node) => {
    const rt = runtimeOf(node);
    return {
      id: node.id,
      type: "agent",
      position: LAYOUT[node.id] ?? { x: 0, y: 0 },
      data: { node, onOpen, ...rt },
      // Custom node renders its own handles; keep RF defaults off.
      draggable: true,
      selectable: true,
    } satisfies AgentFlowNode;
  });
}

export function buildFlowEdges(stateById: Record<string, AgentRunState>): Edge[] {
  return CATALOG_EDGES.map((e) => {
    const srcState = stateById[e.from];
    const dstState = stateById[e.to];
    // Animate an edge while data is flowing across it: source done, target live;
    // or source currently running.
    const active =
      srcState === "running" ||
      (srcState === "completed" && (dstState === "running" || dstState === "waiting"));
    const failed = srcState === "failed";
    return {
      id: `${e.from}-${e.to}`,
      source: e.from,
      target: e.to,
      type: "smoothstep",
      animated: active,
      style: {
        strokeWidth: active ? 2 : 1.5,
        stroke: failed
          ? "#ef4444"
          : active
          ? "#3b82f6"
          : "var(--hairline-2, #cbd5e1)",
      },
    } satisfies Edge;
  });
}
