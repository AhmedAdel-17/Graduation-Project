import { useEffect } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, X } from "lucide-react";
import { cn } from "../../../lib/utils";
import { AgentStatusBadge } from "../shared/AgentStatusBadge";
import { formatDuration } from "../live/executionModel";
import { PHASE_LABELS, type CatalogNode } from "../../../data/agent-catalog";
import type { NodeRuntime } from "../agent-monitor/graphModel";

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="px-4 py-3.5 border-b border-stone-100 dark:border-[var(--hairline)]">
      <h4 className="text-[10.5px] font-semibold uppercase tracking-[0.15em] text-stone-400 dark:text-[var(--ink-3)] mb-2">
        {title}
      </h4>
      {children}
    </div>
  );
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1">
      <span className="text-[12px] text-stone-500 dark:text-[var(--ink-3)]">{label}</span>
      <span className="text-[12px] font-medium text-ink text-right num">{value}</span>
    </div>
  );
}

function Chips({ items }: { items: string[] }) {
  if (items.length === 0)
    return <span className="text-[12px] text-stone-400 dark:text-[var(--ink-3)]">—</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((it) => (
        <span
          key={it}
          className="px-2 py-0.5 rounded-md text-[11px] bg-stone-100 text-stone-600 border border-stone-200 dark:bg-white/[0.04] dark:text-[var(--ink-2)] dark:border-[var(--hairline)]"
        >
          {it}
        </span>
      ))}
    </div>
  );
}

export function AgentDetailDrawer({
  node,
  runtime,
  onClose,
}: {
  node: CatalogNode | null;
  runtime: NodeRuntime | null;
  onClose: () => void;
}) {
  const open = Boolean(node);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!node) return null;

  const lastRun = runtime?.lastRunAt
    ? new Date(runtime.lastRunAt).toLocaleString(undefined, {
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label={`${node.label} details`}>
      <div
        className="absolute inset-0 bg-stone-900/40 backdrop-blur-[2px] anim-fade-up"
        onClick={onClose}
        aria-hidden
      />
      <aside
        className={cn(
          "absolute right-0 top-0 h-full w-full sm:w-[420px] bg-white dark:bg-[var(--paper)]",
          "border-l border-stone-200 dark:border-[var(--hairline)] shadow-2xl overflow-y-auto anim-fade-up"
        )}
      >
        {/* Header */}
        <div className="sticky top-0 z-10 flex items-start justify-between gap-3 px-4 py-3.5 bg-white/90 dark:bg-[var(--paper)]/90 backdrop-blur border-b border-stone-200 dark:border-[var(--hairline)]">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h3 className="text-[15px] font-semibold text-ink truncate">{node.label}</h3>
              {runtime && <AgentStatusBadge state={runtime.state} />}
            </div>
            <p className="text-[11.5px] text-stone-500 dark:text-[var(--ink-3)] mt-0.5">
              {PHASE_LABELS[node.phase]} · {node.kind}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="h-7 w-7 inline-flex items-center justify-center rounded-md text-stone-500 hover:bg-stone-100 dark:text-[var(--ink-2)] dark:hover:bg-white/5 shrink-0"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>

        <Section title="Agent information">
          <p className="text-[12.5px] text-ink leading-relaxed">{node.description}</p>
          <p className="text-[12px] text-stone-500 dark:text-[var(--ink-3)] mt-1.5 leading-relaxed">
            {node.responsibility}
          </p>
          {node.promptId && (
            <div className="mt-2">
              <Field label="Prompt" value={`${node.promptId}`} />
              {node.agentName && <Field label="Agent name" value={node.agentName} />}
            </div>
          )}
        </Section>

        <Section title="Inputs">
          <Chips items={node.inputs} />
        </Section>
        <Section title="Outputs">
          <Chips items={node.outputs} />
        </Section>

        <Section title="Execution metrics">
          <p className="text-[11px] text-stone-400 dark:text-[var(--ink-3)] mb-1.5">
            From the audit DB (last 30 days). Empty until runs are recorded.
          </p>
          <Field
            label="Executions"
            value={runtime?.executions != null ? runtime.executions : "—"}
          />
          <Field
            label="Avg confidence"
            value={
              runtime?.avgConfidence != null
                ? `${Math.round(runtime.avgConfidence * 100)}%`
                : "—"
            }
          />
          <Field label="Last seen" value={lastRun} />
        </Section>

        <Section title="Latest run">
          <Field
            label="Status"
            value={runtime ? <AgentStatusBadge state={runtime.state} showDot={false} /> : "—"}
          />
          <Field label="When" value={lastRun} />
          <Field label="Duration" value={formatDuration(runtime?.durationMs ?? null)} />
        </Section>

        <div className="px-4 py-4">
          {node.agentName ? (
            <Link
              to="/admin/traces"
              onClick={onClose}
              className="inline-flex items-center gap-1.5 text-[12px] font-medium text-blue-600 dark:text-sky-400 hover:underline"
            >
              View full LLM request/response trace
              <ArrowRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
          ) : (
            <div className="flex items-center gap-1.5 text-[11px] text-stone-400 dark:text-[var(--ink-3)]">
              <ArrowRight className="h-3 w-3" aria-hidden />
              Deterministic stage — no LLM trace.
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
