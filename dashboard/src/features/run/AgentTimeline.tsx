import { StatusPill, type NodeStatus } from "../../components/ui/StatusPill";
import { cn } from "../../lib/utils";
import { useT } from "../../lib/i18n";

interface NodeSpec {
  id: string;
  groupKey: string;
  gate?: (selected: string[]) => boolean;
}

// Mirrors the node names emitted by server/api_server.py:1243-1281
// `get_node_from_chunk`. Selected-analyst gates skip rows the user
// excluded from the run.
const NODES: NodeSpec[] = [
  { id: "Market Analyst",       groupKey: "run.group.analysts", gate: (s) => s.includes("market") },
  { id: "Social Analyst",       groupKey: "run.group.analysts", gate: (s) => s.includes("social") },
  { id: "News Analyst",         groupKey: "run.group.analysts", gate: (s) => s.includes("news") },
  { id: "Fundamentals Analyst", groupKey: "run.group.analysts", gate: (s) => s.includes("fundamentals") },
  { id: "Bull Researcher",      groupKey: "run.group.research" },
  { id: "Bear Researcher",      groupKey: "run.group.research" },
  { id: "Research Manager",     groupKey: "run.group.research" },
  { id: "Trader",               groupKey: "run.group.execution" },
  { id: "Risky Analyst",        groupKey: "run.group.risk" },
  { id: "Safe Analyst",         groupKey: "run.group.risk" },
  { id: "Neutral Analyst",      groupKey: "run.group.risk" },
  { id: "Risk Judge",           groupKey: "run.group.risk" },
];

export function AgentTimeline({
  selectedAnalysts,
  nodeStatuses,
  activeNode,
  className,
}: {
  selectedAnalysts: string[];
  nodeStatuses: Record<string, NodeStatus>;
  activeNode?: string | null;
  className?: string;
}) {
  const t = useT();
  const visible = NODES.filter((n) =>
    n.gate ? n.gate(selectedAnalysts) : true
  );

  // Group by groupKey while preserving order.
  const groups: { key: string; nodes: NodeSpec[] }[] = [];
  for (const node of visible) {
    let bucket = groups.find((g) => g.key === node.groupKey);
    if (!bucket) {
      bucket = { key: node.groupKey, nodes: [] };
      groups.push(bucket);
    }
    bucket.nodes.push(node);
  }

  return (
    <ol
      className={cn("flex flex-col gap-4", className)}
      aria-live="polite"
      aria-label={t("run.timeline.aria")}
    >
      {groups.map((group) => (
        <li key={group.key} className="flex flex-col gap-1.5">
          <p className="text-[10px] font-semibold uppercase tracking-[0.15em] text-fg-subtle px-1">
            {t(group.key)}
          </p>
          <ul className="flex flex-col gap-1">
            {group.nodes.map((node) => {
              const status: NodeStatus = nodeStatuses[node.id] ?? "idle";
              const isActive = activeNode === node.id;
              return (
                <li
                  key={node.id}
                  className={cn(
                    "flex items-center justify-between gap-2 px-2.5 py-2 rounded-md border text-xs",
                    isActive
                      ? "bg-ink-800/80 border-line-strong"
                      : "bg-transparent border-transparent hover:bg-ink-800/40",
                    status === "completed" && !isActive && "text-fg",
                    status === "idle" && "text-fg-subtle"
                  )}
                >
                  <span className="truncate">{node.id}</span>
                  <StatusPill status={status} />
                </li>
              );
            })}
          </ul>
        </li>
      ))}
    </ol>
  );
}
