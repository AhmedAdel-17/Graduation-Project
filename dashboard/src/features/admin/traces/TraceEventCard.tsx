import { useState } from "react";
import { ChevronDown, Cpu, FileInput, MessageSquare } from "lucide-react";
import { cn } from "../../../lib/utils";
import { JsonTree } from "../shared/JsonTree";
import { CopyButton } from "../shared/CopyButton";
import { catalogNodeByAgentName, PHASE_LABELS } from "../../../data/agent-catalog";
import { AGENT_PROMPTS } from "../../../data/agent-prompts";
import { extractModel } from "./fingerprint";
import type { SessionTraceEvent } from "../../../services/api/types";

const PROMPT_BY_AGENT = new Map(AGENT_PROMPTS.map((p) => [p.agentName, p]));

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

function SubSection({
  icon,
  title,
  defaultOpen = false,
  actions,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  defaultOpen?: boolean;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-t border-stone-100 dark:border-[var(--hairline)]">
      <div className="flex items-center justify-between gap-2 px-3.5 py-2">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-stone-500 dark:text-[var(--ink-3)] hover:text-ink"
        >
          <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", !open && "-rotate-90")} aria-hidden />
          <span className="text-stone-400 dark:text-[var(--ink-3)]">{icon}</span>
          {title}
        </button>
        {open && actions}
      </div>
      {open && <div className="px-3.5 pb-3.5">{children}</div>}
    </div>
  );
}

function MetaRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1 text-[12px]">
      <span className="text-stone-500 dark:text-[var(--ink-3)]">{label}</span>
      <span className="font-medium text-ink num text-right">{value}</span>
    </div>
  );
}

export function TraceEventCard({
  event,
  index,
  sessionFingerprint,
  defaultOpen = false,
}: {
  event: SessionTraceEvent;
  index: number;
  sessionFingerprint: Record<string, unknown> | null;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const agentName = event.agent_name ?? "unknown";
  const node = catalogNodeByAgentName(agentName);
  const promptMeta = PROMPT_BY_AGENT.get(agentName);
  const label = node?.label ?? agentName;
  const model = extractModel(event.model_fingerprint, sessionFingerprint);
  const confidence =
    typeof event.confidence_score === "number"
      ? `${Math.round(event.confidence_score * 100)}%`
      : null;
  const hasStructured =
    event.structured_output && Object.keys(event.structured_output).length > 0;

  return (
    <div className="card overflow-hidden">
      {/* Header */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-3 px-3.5 py-3 text-left hover:bg-stone-50 dark:hover:bg-white/[0.02] transition-colors"
      >
        <span className="h-6 w-6 shrink-0 rounded-md bg-stone-100 dark:bg-white/[0.05] text-stone-500 dark:text-[var(--ink-3)] flex items-center justify-center text-[11px] font-semibold num">
          {index + 1}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-semibold text-ink truncate">{label}</span>
            {node && (
              <span className="text-[10px] uppercase tracking-wider text-stone-400 dark:text-[var(--ink-3)] hidden sm:inline">
                {PHASE_LABELS[node.phase]}
              </span>
            )}
          </div>
          {event.opinion_summary && (
            <p className="text-[11.5px] text-stone-500 dark:text-[var(--ink-3)] truncate mt-0.5">
              {event.opinion_summary}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2.5 shrink-0">
          {confidence && (
            <span className="text-[11px] font-medium text-stone-600 dark:text-[var(--ink-2)] num">{confidence}</span>
          )}
          <span className="text-[10.5px] text-stone-400 dark:text-[var(--ink-3)] num hidden sm:inline">
            {fmtTime(event.logged_at)}
          </span>
          <ChevronDown className={cn("h-4 w-4 text-stone-400 transition-transform", !open && "-rotate-90")} aria-hidden />
        </div>
      </button>

      {open && (
        <>
          {/* Request */}
          <SubSection icon={<FileInput className="h-3.5 w-3.5" />} title="Request" defaultOpen>
            <div className="space-y-2.5">
              <MetaRow
                label="Prompt"
                value={
                  promptMeta?.promptId
                    ? `${promptMeta.promptId} · ${promptMeta.promptVersion ?? "v?"}`
                    : node?.promptId ?? "deterministic"
                }
              />
              {node && node.inputs.length > 0 && (
                <div>
                  <div className="text-[11px] text-stone-500 dark:text-[var(--ink-3)] mb-1">Inputs</div>
                  <div className="flex flex-wrap gap-1.5">
                    {node.inputs.map((i) => (
                      <span
                        key={i}
                        className="px-2 py-0.5 rounded-md text-[11px] bg-stone-100 text-stone-600 border border-stone-200 dark:bg-white/[0.04] dark:text-[var(--ink-2)] dark:border-[var(--hairline)]"
                      >
                        {i}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              <p className="text-[11px] text-stone-400 dark:text-[var(--ink-3)] leading-relaxed">
                Full system + user prompt text is versioned in <span className="font-mono">PROMPTS.md</span> /
                the agent source and is not persisted per-call. Retrieved context / tools are shown in the
                parsed output when the agent records them.
              </p>
            </div>
          </SubSection>

          {/* Model metadata */}
          <SubSection icon={<Cpu className="h-3.5 w-3.5" />} title="Model metadata" defaultOpen>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-5">
              <MetaRow label="Provider" value={model.provider ?? "—"} />
              <MetaRow label="Model" value={model.model ?? "—"} />
              <MetaRow label="Temperature" value={model.temperature ?? "—"} />
              <MetaRow label="Seed" value={model.seed ?? "—"} />
            </div>
            <p className="mt-2 pt-2 border-t border-stone-100 dark:border-[var(--hairline)] text-[11px] text-stone-400 dark:text-[var(--ink-3)] leading-relaxed">
              Token counts &amp; cost are not captured — per-call usage is not yet persisted to
              <span className="font-mono"> agent_events</span>. Provider / model / temperature / seed
              above are the real recorded fingerprint.
            </p>
          </SubSection>

          {/* Response */}
          <SubSection
            icon={<MessageSquare className="h-3.5 w-3.5" />}
            title="Response"
            defaultOpen
            actions={
              event.opinion_summary ? (
                <CopyButton value={event.opinion_summary} label="Copy" />
              ) : undefined
            }
          >
            {event.opinion_summary && (
              <div className="mb-3">
                <div className="text-[11px] text-stone-500 dark:text-[var(--ink-3)] mb-1">Summary (raw)</div>
                <p className="text-[12.5px] text-ink leading-relaxed whitespace-pre-wrap">
                  {event.opinion_summary}
                </p>
              </div>
            )}
            <div>
              <div className="text-[11px] text-stone-500 dark:text-[var(--ink-3)] mb-1">Structured output (parsed)</div>
              {hasStructured ? (
                <JsonTree data={event.structured_output} defaultExpandDepth={1} maxHeight="40vh" />
              ) : (
                <p className="text-[12px] text-stone-400 dark:text-[var(--ink-3)]">No structured output recorded.</p>
              )}
            </div>
          </SubSection>
        </>
      )}
    </div>
  );
}
