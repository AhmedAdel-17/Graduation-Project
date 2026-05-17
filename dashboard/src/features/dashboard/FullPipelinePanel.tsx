import { useMemo } from "react";
import {
  ArrowRight,
  Loader2,
  X,
  RefreshCw,
} from "lucide-react";
import { useAgentStream } from "../../hooks/useAgentStream";
import { cn } from "../../lib/utils";

// Canonical agent chain (in execution order). The 4 analysts fan out in
// parallel; everything after runs sequentially.
const PIPELINE_STAGES = [
  { key: "Market Analyst",        short: "Market"       },
  { key: "Fundamentals Analyst",  short: "Fundamentals" },
  { key: "News Analyst",          short: "News"         },
  { key: "Social Analyst",        short: "Social"       },
  { key: "Bull Researcher",       short: "Bull"         },
  { key: "Bear Researcher",       short: "Bear"         },
  { key: "Research Manager",      short: "Judge"        },
  { key: "Trader",                short: "Trader"       },
  { key: "Risk Scorer",           short: "Risk Score"   },
  { key: "Merged Risk Debate",    short: "Risk Debate"  },
  { key: "Risk Judge",            short: "Risk Mgr"     },
] as const;

const INTERNAL_NODE_PREFIXES = ["Msg Clear", "tools_", "Analysts Sync"];

/* ──────────────────────────────────────────────────────────────────────
   Architecture graph — visualises the real LangGraph topology:
   4 analysts fan out in parallel  →  2 researchers debate in parallel
   →  Judge  →  Trader  →  Risk Score  →  Risk Debate  →  Risk Mgr
   ────────────────────────────────────────────────────────────────────── */

type StageStatus = "pending" | "in_progress" | "completed" | "error";

interface Node {
  key: string;       // matches nodeStatuses key
  label: string;
  x: number;
  y: number;
}

interface ColumnDef {
  title: string;
  x: number;         // header anchor x
  badge?: string;    // "PARALLEL" tag
}

// viewBox: 0 0 1120 320 — chosen so the diagram has comfortable breathing
// room and scales cleanly inside the panel via preserveAspectRatio.
const VB_W = 1120;
const VB_H = 320;
const NODE_W = 128;
const NODE_H = 38;

const COLUMNS: ColumnDef[] = [
  { title: "Analysts",   x: 90,   badge: "PARALLEL ×4" },
  { title: "Debate",     x: 310,  badge: "PARALLEL ×2" },
  { title: "Decide",     x: 500 },
  { title: "Execute",    x: 660 },
  { title: "Risk score", x: 820 },
  { title: "Risk debate",x: 980 },
];

const NODES: Node[] = [
  // Analysts column
  { key: "Market Analyst",       label: "Market",       x: 90,  y: 70  },
  { key: "Fundamentals Analyst", label: "Fundamentals", x: 90,  y: 130 },
  { key: "News Analyst",         label: "News",         x: 90,  y: 190 },
  { key: "Social Analyst",       label: "Social",       x: 90,  y: 250 },
  // Debate column
  { key: "Bull Researcher",      label: "Bull",         x: 310, y: 130 },
  { key: "Bear Researcher",      label: "Bear",         x: 310, y: 190 },
  // Decide
  { key: "Research Manager",     label: "Judge",        x: 500, y: 160 },
  // Execute
  { key: "Trader",               label: "Trader",       x: 660, y: 160 },
  // Risk chain
  { key: "Risk Scorer",          label: "Risk Score",   x: 820, y: 160 },
  { key: "Merged Risk Debate",   label: "Risk Debate",  x: 980, y: 160 },
  // Final — placed flowing out the right of the risk chain
  { key: "Risk Judge",           label: "Risk Mgr",     x: 980, y: 250 },
];

const EDGES: Array<[string, string]> = [
  // Each analyst feeds the implicit "Analysts Sync" → both researchers
  ["Market Analyst",       "Bull Researcher"],
  ["Market Analyst",       "Bear Researcher"],
  ["Fundamentals Analyst", "Bull Researcher"],
  ["Fundamentals Analyst", "Bear Researcher"],
  ["News Analyst",         "Bull Researcher"],
  ["News Analyst",         "Bear Researcher"],
  ["Social Analyst",       "Bull Researcher"],
  ["Social Analyst",       "Bear Researcher"],
  // Debate → Judge
  ["Bull Researcher", "Research Manager"],
  ["Bear Researcher", "Research Manager"],
  // Sequential downstream
  ["Research Manager",   "Trader"],
  ["Trader",             "Risk Scorer"],
  ["Risk Scorer",        "Merged Risk Debate"],
  ["Merged Risk Debate", "Risk Judge"],
];

function nodeStyle(s: StageStatus) {
  switch (s) {
    case "completed":
      return { fill: "var(--node-done-bg)", stroke: "var(--node-done-bg)", text: "var(--node-done-fg)" };
    case "in_progress":
      return { fill: "var(--node-bg)", stroke: "var(--node-done-bg)", text: "var(--node-fg)" };
    case "error":
      return { fill: "#fff1f2", stroke: "#fda4af", text: "#be123c" };
    default:
      return { fill: "var(--node-bg)", stroke: "var(--node-pending-border)", text: "var(--node-pending-fg)" };
  }
}

function edgePath(a: Node, b: Node) {
  const x1 = a.x + NODE_W / 2;
  const y1 = a.y;
  const x2 = b.x - NODE_W / 2;
  const y2 = b.y;
  const dx = Math.max(40, (x2 - x1) * 0.5);
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

function ArchitectureGraph({
  nodeStatuses,
}: {
  nodeStatuses: Record<string, string>;
}) {
  const statusOf = (k: string): StageStatus =>
    (nodeStatuses[k] as StageStatus) ?? "pending";
  const nodeMap = new Map(NODES.map((n) => [n.key, n]));

  return (
    <div
      className="w-full overflow-x-auto graph-theme
        [--node-bg:#ffffff] [--node-fg:#0a0a0b]
        [--node-pending-border:#e7e5e0] [--node-pending-fg:#78716c]
        [--node-done-bg:#0a0a0b] [--node-done-fg:#ffffff]
        [--edge:#e7e5e0] [--edge-active:#0a0a0b]
        dark:[--node-bg:var(--paper)]
        dark:[--node-fg:var(--ink)]
        dark:[--node-pending-border:var(--hairline)]
        dark:[--node-pending-fg:var(--ink-3)]
        dark:[--node-done-bg:#ffffff]
        dark:[--node-done-fg:#0a0a0b]
        dark:[--edge:var(--hairline)]
        dark:[--edge-active:#ffffff]"
    >
      <svg
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        className="w-full h-auto min-w-[760px] block"
        role="img"
        aria-label="Trading agents pipeline architecture"
      >
        <defs>
          <marker
            id="arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M0,0 L10,5 L0,10 z" fill="var(--edge)" />
          </marker>
          <marker
            id="arrow-active"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M0,0 L10,5 L0,10 z" fill="var(--edge-active)" />
          </marker>
        </defs>

        {/* Column headers */}
        {COLUMNS.map((c) => (
          <g key={c.title}>
            <text
              x={c.x}
              y={22}
              textAnchor="middle"
              className="fill-stone-400 dark:fill-[var(--ink-3)]"
              style={{
                fontSize: 9.5,
                fontWeight: 600,
                letterSpacing: "0.16em",
                textTransform: "uppercase",
                fontFamily: "Inter, sans-serif",
              }}
            >
              {c.title.toUpperCase()}
            </text>
            {c.badge && (
              <text
                x={c.x}
                y={36}
                textAnchor="middle"
                className="fill-emerald-600/80 dark:fill-emerald-400/80"
                style={{ fontSize: 8.5, fontWeight: 600, letterSpacing: "0.1em", fontFamily: "JetBrains Mono, monospace" }}
              >
                {c.badge}
              </text>
            )}
          </g>
        ))}

        {/* Parallel-lane backdrops to communicate concurrency */}
        {/* Analyst band */}
        <rect
          x={90 - NODE_W / 2 - 14}
          y={50}
          width={NODE_W + 28}
          height={250 - 50 + NODE_H / 2 + 12}
          rx={14}
          className="fill-emerald-500/[0.035] dark:fill-emerald-400/[0.04] stroke-emerald-500/15 dark:stroke-emerald-400/20"
          strokeDasharray="2 4"
        />
        {/* Exact debate band */}
        <rect
          x={310 - NODE_W / 2 - 14}
          y={110}
          width={NODE_W + 28}
          height={190 - 110 + NODE_H / 2 + 12}
          rx={14}
          className="fill-amber-500/[0.04] dark:fill-amber-400/[0.05] stroke-amber-500/20 dark:stroke-amber-400/20"
          strokeDasharray="2 4"
        />

        {/* Edges */}
        {EDGES.map(([from, to]) => {
          const a = nodeMap.get(from);
          const b = nodeMap.get(to);
          if (!a || !b) return null;
          const aDone = statusOf(from) === "completed";
          const bActive = statusOf(to) === "in_progress" || statusOf(to) === "completed";
          const active = aDone && bActive;
          return (
            <path
              key={`${from}->${to}`}
              d={edgePath(a, b)}
              fill="none"
              stroke={active ? "var(--edge-active)" : "var(--edge)"}
              strokeWidth={active ? 1.6 : 1}
              markerEnd={active ? "url(#arrow-active)" : "url(#arrow)"}
              className="transition-[stroke,stroke-width] duration-300"
            />
          );
        })}

        {/* Nodes */}
        {NODES.map((n) => {
          const s = statusOf(n.key);
          const st = nodeStyle(s);
          const x = n.x - NODE_W / 2;
          const y = n.y - NODE_H / 2;
          return (
            <g key={n.key} className="transition-opacity">
              {s === "in_progress" && (
                <rect
                  x={x - 4}
                  y={y - 4}
                  width={NODE_W + 8}
                  height={NODE_H + 8}
                  rx={12}
                  fill="none"
                  stroke={st.stroke}
                  strokeWidth={1}
                  opacity={0.25}
                >
                  <animate
                    attributeName="opacity"
                    values="0.05;0.4;0.05"
                    dur="1.6s"
                    repeatCount="indefinite"
                  />
                </rect>
              )}
              <rect
                x={x}
                y={y}
                width={NODE_W}
                height={NODE_H}
                rx={9}
                fill={st.fill}
                stroke={st.stroke}
                strokeWidth={s === "in_progress" ? 1.4 : 1}
                className="transition-[fill,stroke] duration-200"
              />
              <text
                x={n.x}
                y={n.y + 4}
                textAnchor="middle"
                fill={st.text}
                style={{
                  fontSize: 12,
                  fontWeight: 500,
                  fontFamily: "Inter, sans-serif",
                }}
              >
                {n.label}
              </text>
            </g>
          );
        })}
      </svg>

      {/* Legend */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-1 pt-3 text-[10.5px] text-stone-500 dark:text-[var(--ink-3)]">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm border border-stone-300 dark:border-[var(--hairline)] bg-white dark:bg-[var(--paper)]" />
          Pending
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm border border-stone-900 dark:border-white bg-white dark:bg-[var(--paper)]" />
          Running
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm bg-stone-900 dark:bg-white" />
          Completed
        </span>
        <span className="inline-flex items-center gap-1.5 ml-auto">
          <span className="inline-block w-3 h-px border-t border-dashed border-emerald-500/60" />
          Parallel fan-out
        </span>
      </div>
    </div>
  );
}

// Compact pill list for the "extras" surfaced from the stream
// (unrecognized nodes that aren't in the canonical graph yet).
function ExtraChip({ name, status }: { name: string; status: string | undefined }) {
  const cls =
    status === "completed"
      ? "bg-stone-900 text-white border-stone-900 dark:bg-white dark:text-stone-900 dark:border-white"
      : status === "in_progress"
      ? "bg-white text-ink border-stone-900/70 dark:bg-[var(--paper)] dark:text-[var(--ink)] dark:border-white/70"
      : status === "error"
      ? "bg-rose-50 text-rose-700 border-rose-200"
      : "bg-white text-ink-3 border-stone-200 dark:bg-[var(--paper)] dark:border-[var(--hairline)]";
  return (
    <span className={cn("inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[10.5px]", cls)}>
      {status === "in_progress" && <Loader2 className="h-2.5 w-2.5 animate-spin" />}
      {name}
    </span>
  );
}

export interface FullPipelinePanelProps {
  ticker: string;
  tradeDate?: string;
}

export function FullPipelinePanel({ ticker, tradeDate }: FullPipelinePanelProps) {
  const { events, nodeStatuses, status, error, start, cancel, reset } =
    useAgentStream();

  const isRunning = status === "connecting" || status === "streaming";
  const isDone = status === "complete";
  const isError = status === "error";

  const finalDecision = useMemo(() => {
    const completeEvt = [...events].reverse().find((e) => e.type === "complete");
    if (!completeEvt) return null;
    const d = completeEvt.data as Record<string, unknown> | undefined;
    return (d?.final_trade_decision as string) || "";
  }, [events]);

  const elapsedSec = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const d = events[i].data as Record<string, unknown> | undefined;
      if (d && typeof d.elapsed_sec === "number") return d.elapsed_sec as number;
    }
    return null;
  }, [events]);

  // Progress: how many canonical stages have completed?
  const completed = PIPELINE_STAGES.filter(
    (s) => nodeStatuses[s.key] === "completed"
  ).length;
  const total = PIPELINE_STAGES.length;

  // Surface any unrecognized real nodes (e.g. Risk Veto) as extra chips.
  const extras = useMemo(() => {
    const known = new Set<string>(PIPELINE_STAGES.map((s) => s.key));
    return Object.keys(nodeStatuses).filter(
      (k) =>
        !known.has(k) &&
        k !== "System" &&
        !INTERNAL_NODE_PREFIXES.some((p) => k.startsWith(p))
    );
  }, [nodeStatuses]);

  function onRun() {
    const td = tradeDate || new Date().toISOString().slice(0, 10);
    start({
      ticker,
      trade_date: td,
      selected_analysts: ["market", "social", "news", "fundamentals"],
      max_debate_rounds: 1,
      max_risk_rounds: 1,
    });
  }

  return (
    <section
      className="rounded-2xl border border-stone-200/80 bg-white dark:bg-[var(--paper)] dark:border-[var(--hairline)] overflow-hidden"
    >
      {/* ── Header row — same visual weight as the run-bar above ─────── */}
      <div className="flex items-center gap-4 px-4 py-3 border-b border-stone-200/70 dark:border-[var(--hairline)]">
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <div>
            <div className="text-[10.5px] uppercase tracking-[0.16em] text-stone-500 dark:text-[var(--ink-3)] leading-none">
              Full pipeline
            </div>
            <div className="text-[14px] font-semibold text-ink leading-snug mt-1">
              11-agent TradingAgents graph
            </div>
          </div>
          {/* ETA / progress pill */}
          <span className="ml-2 inline-flex items-center gap-1.5 text-[11px] text-ink-3 px-2 py-1 rounded-full bg-stone-50 dark:bg-white/5 border border-stone-200/70 dark:border-[var(--hairline)]">
            {isRunning
              ? <>
                  <Loader2 className="h-3 w-3 animate-spin" />
                  {elapsedSec !== null ? `${elapsedSec}s` : "running"} · {completed}/{total}
                </>
              : isDone
              ? <>Complete · {completed}/{total}</>
              : isError
              ? <span className="text-rose-700 dark:text-rose-300">Error</span>
              : <>~ 2–5 min · {total} stages</>}
          </span>
        </div>

        <div className="flex items-center gap-2">
          {(isDone || isError || status === "closed") && (
            <button
              onClick={reset}
              className="inline-flex items-center gap-1.5 h-9 px-3 rounded-lg text-[12.5px] text-ink-2
                border border-stone-200 hover:bg-stone-50
                dark:border-[var(--hairline)] dark:hover:bg-[var(--hairline)]"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Reset
            </button>
          )}
          {isRunning ? (
            <button
              onClick={cancel}
              className="inline-flex items-center gap-1.5 h-9 px-4 rounded-lg text-[13px] font-medium
                bg-rose-50 text-rose-700 hover:bg-rose-100 border border-rose-200
                dark:bg-rose-900/20 dark:text-rose-300 dark:border-rose-900/40 dark:hover:bg-rose-900/30"
            >
              <X className="h-3.5 w-3.5" />
              Cancel
            </button>
          ) : (
            <button
              onClick={onRun}
              className={cn(
                "inline-flex items-center gap-1.5 h-9 px-4 rounded-lg text-[13px] font-medium",
                "bg-stone-900 hover:bg-stone-800 text-white",
                "dark:bg-white dark:text-stone-900 dark:hover:bg-stone-100"
              )}
            >
              Run pipeline
              <ArrowRight className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* ── Architecture graph — real topology, parallel lanes called out ── */}
      <div className="px-4 sm:px-5 py-4">
        <ArchitectureGraph nodeStatuses={nodeStatuses} />
        {extras.length > 0 && (
          <div className="mt-3 pt-3 border-t border-stone-200/70 dark:border-[var(--hairline)] flex flex-wrap items-center gap-1.5">
            <span className="text-[10px] uppercase tracking-[0.16em] text-stone-400 dark:text-[var(--ink-3)] mr-1">
              Extra nodes
            </span>
            {extras.map((k) => (
              <ExtraChip key={k} name={k} status={nodeStatuses[k]} />
            ))}
          </div>
        )}
      </div>

      {/* ── Final decision — only appears when there's something to show ── */}
      {finalDecision && (
        <div className="border-t border-stone-200/70 dark:border-[var(--hairline)] px-5 py-4 bg-stone-50/60 dark:bg-white/[0.03]">
          <div className="text-[10.5px] uppercase tracking-[0.16em] text-stone-500 dark:text-[var(--ink-3)] mb-2">
            Final trade decision
          </div>
          <pre className="text-[12.5px] text-ink whitespace-pre-wrap leading-relaxed font-sans">
            {finalDecision}
          </pre>
        </div>
      )}

      {/* ── Inline error ─────────────────────────────────────────────── */}
      {isError && (
        <div className="border-t border-rose-200 dark:border-rose-900/40 px-5 py-3 bg-rose-50 dark:bg-rose-900/20 text-[12px] text-rose-700 dark:text-rose-300">
          Stream error — {error ?? "unknown"}
        </div>
      )}
    </section>
  );
}
