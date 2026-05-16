import { useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, Database } from "lucide-react";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { Gauge } from "../../components/ui/Gauge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/Tabs";
import { JSONViewer } from "../../components/ui/JSONViewer";
import { Markdown } from "../../components/ui/Markdown";
import { useT } from "../../lib/i18n";
import { cn } from "../../lib/utils";
import {
  AGENT_PROMPTS,
  agentOrderIndex,
  getAgentPromptMeta,
  phaseLabelKey,
  type AgentPhase,
} from "../../data/agent-prompts";
import type {
  SessionTraceEvent,
  SessionTraceSummary,
} from "../../services/api/types";

const PHASE_ORDER: AgentPhase[] = [
  "analysts",
  "research",
  "execution",
  "risk",
  "final",
  "meta",
];

interface EnrichedEvent extends SessionTraceEvent {
  phase: AgentPhase;
  promptId: string | null;
  promptVersion: string | null;
  parseFailed: boolean;
}

function enrich(event: SessionTraceEvent): EnrichedEvent {
  const meta = getAgentPromptMeta(event.agent_name);
  if (!meta) {
    return {
      ...event,
      phase: "meta",
      promptId: null,
      promptVersion: null,
      parseFailed: false,
    };
  }
  // Heuristic: any LLM agent that produced a summary but no structured_output
  // hit the regex fallback (MEMORY.md §N / Q-9).
  const parseFailed =
    meta.promptId !== null &&
    !event.structured_output &&
    !!(event.opinion_summary && event.opinion_summary.length > 0);
  return {
    ...event,
    phase: meta.phase,
    promptId: meta.promptId,
    promptVersion: meta.promptVersion,
    parseFailed,
  };
}

export interface ReasoningViewProps {
  session: SessionTraceSummary | null;
  events: SessionTraceEvent[];
  source: "postgres" | "jsonl" | "none" | null;
  /** Hide the top metadata bar when a parent already renders session info. */
  hideMetaBar?: boolean;
}

/**
 * Phase-grouped reasoning trace renderer. Stateless / data-driven so it can
 * be shared between Workspace > Reasoning (latest session) and
 * /sessions/:id (a specific session). Callers are responsible for fetching
 * data and handling loading + empty states.
 */
export function ReasoningView({
  session,
  events,
  source,
  hideMetaBar = false,
}: ReasoningViewProps) {
  const grouped = useMemo(() => {
    const enriched = events
      .map(enrich)
      .sort(
        (a, b) =>
          agentOrderIndex(a.agent_name ?? "") -
          agentOrderIndex(b.agent_name ?? "")
      );
    const groups: Record<AgentPhase, EnrichedEvent[]> = {
      analysts: [],
      research: [],
      execution: [],
      risk: [],
      final: [],
      meta: [],
    };
    for (const e of enriched) groups[e.phase].push(e);
    return groups;
  }, [events]);

  return (
    <div className="flex flex-col gap-4">
      {!hideMetaBar && session && (
        <SessionMetaBar
          traceDate={session.trade_date}
          sessionId={session.session_id}
          source={source}
          finalDecision={session.final_decision}
          riskVeto={session.risk_veto ?? false}
          confidence={session.confidence_overall ?? null}
        />
      )}
      {PHASE_ORDER.map((phase) => {
        const list = grouped[phase];
        if (list.length === 0) return null;
        return <PhaseGroup key={phase} phase={phase} events={list} />;
      })}
    </div>
  );
}

function SessionMetaBar({
  traceDate,
  sessionId,
  source,
  finalDecision,
  riskVeto,
  confidence,
}: {
  traceDate: string | null | undefined;
  sessionId: string | null | undefined;
  source: "postgres" | "jsonl" | "none" | null;
  finalDecision: string | null | undefined;
  riskVeto: boolean;
  confidence: number | null;
}) {
  const t = useT();
  return (
    <Card>
      <CardBody className="pt-4 pb-4">
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <span className="num text-fg">{traceDate ?? "—"}</span>
          {sessionId && (
            <span className="num text-fg-subtle truncate max-w-[160px]">
              {sessionId.slice(0, 12)}…
            </span>
          )}
          {source === "postgres" && (
            <Badge tone="brand">
              <Database className="h-2.5 w-2.5" aria-hidden /> postgres
            </Badge>
          )}
          {source === "jsonl" && <Badge tone="neutral">jsonl</Badge>}
          {finalDecision && (
            <Badge tone="accent" className="truncate max-w-[280px]">
              {finalDecision.slice(0, 80)}
            </Badge>
          )}
          {riskVeto && (
            <Badge tone="down">
              <AlertTriangle className="h-2.5 w-2.5" aria-hidden />{" "}
              {t("reasoning.meta.veto")}
            </Badge>
          )}
          {confidence !== null && (
            <span className="text-fg-muted">
              {t("reasoning.meta.confidence")}{" "}
              <span className="num text-fg">
                {Math.round(confidence * 100)}%
              </span>
            </span>
          )}
        </div>
      </CardBody>
    </Card>
  );
}

function PhaseGroup({
  phase,
  events,
}: {
  phase: AgentPhase;
  events: EnrichedEvent[];
}) {
  const t = useT();
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>{t(phaseLabelKey(phase))}</CardTitle>
          <CardDescription>{t(`agent.phase.${phase}.desc`)}</CardDescription>
        </div>
        <Badge tone="neutral">{events.length}</Badge>
      </CardHeader>
      <CardBody className="flex flex-col gap-2">
        {events.map((ev) => (
          <AgentEventRow
            key={`${ev.agent_name}-${ev.logged_at}`}
            event={ev}
          />
        ))}
      </CardBody>
    </Card>
  );
}

function AgentEventRow({ event }: { event: EnrichedEvent }) {
  const t = useT();
  const [open, setOpen] = useState(false);

  const meta = AGENT_PROMPTS.find((m) => m.agentName === event.agent_name);
  const displayLabel = meta ? t(meta.labelKey) : event.agent_name ?? "—";
  const confidence =
    typeof event.confidence_score === "number"
      ? Math.round(event.confidence_score * 100)
      : null;

  const fingerprint =
    event.model_fingerprint && typeof event.model_fingerprint === "object"
      ? (event.model_fingerprint as Record<string, unknown>)
      : null;
  const modelLabel =
    (fingerprint?.deep_think_llm as string) ||
    (fingerprint?.quick_think_llm as string) ||
    null;

  const hasStructured =
    event.structured_output &&
    typeof event.structured_output === "object" &&
    Object.keys(event.structured_output).length > 0;
  const hasReport =
    typeof event.opinion_summary === "string" &&
    event.opinion_summary.trim().length > 0;
  const hasFingerprint = fingerprint && Object.keys(fingerprint).length > 0;
  const defaultSubTab = hasReport
    ? "report"
    : hasStructured
      ? "json"
      : "fingerprint";

  return (
    <div className="rounded-lg border border-line bg-ink-900/40">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className={cn(
          "w-full flex items-center gap-3 px-3 py-2.5 text-start",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50",
          "hover:bg-ink-800/40 transition-colors"
        )}
      >
        <ChevronDown
          className={cn(
            "h-3.5 w-3.5 text-fg-subtle transition-transform shrink-0",
            open && "rotate-180"
          )}
          aria-hidden
        />
        <span className="text-sm font-semibold text-fg truncate flex-1">
          {displayLabel}
        </span>
        <div className="flex items-center gap-1.5 flex-wrap justify-end">
          {event.promptId && (
            <Badge tone="neutral" className="font-mono normal-case">
              {event.promptId} {event.promptVersion}
            </Badge>
          )}
          {event.parseFailed && (
            <Badge tone="warning">{t("reasoning.row.parseFailed")}</Badge>
          )}
          {confidence !== null && (
            <span className="num text-[10px] text-fg-muted">
              {confidence}%
            </span>
          )}
        </div>
      </button>

      {open && (
        <div className="border-t border-line px-3 py-3 flex flex-col gap-3">
          {confidence !== null && (
            <Gauge
              value={confidence}
              label={t("reasoning.row.confidence")}
              tone={confidence >= 70 ? "brand" : confidence >= 40 ? "accent" : "warning"}
              unit="%"
            />
          )}
          {modelLabel && (
            <p className="text-[11px] text-fg-subtle">
              <span className="uppercase tracking-wider">
                {t("reasoning.row.model")}
              </span>{" "}
              <span className="num text-fg-muted">{modelLabel}</span>
            </p>
          )}
          <Tabs defaultValue={defaultSubTab}>
            <TabsList>
              {hasReport && (
                <TabsTrigger value="report">
                  {t("reasoning.row.tab.report")}
                </TabsTrigger>
              )}
              {hasStructured && (
                <TabsTrigger value="json">
                  {t("reasoning.row.tab.json")}
                </TabsTrigger>
              )}
              {hasFingerprint && (
                <TabsTrigger value="fingerprint">
                  {t("reasoning.row.tab.fingerprint")}
                </TabsTrigger>
              )}
            </TabsList>

            {hasReport && (
              <TabsContent value="report">
                <Markdown>{event.opinion_summary as string}</Markdown>
              </TabsContent>
            )}

            {hasStructured && (
              <TabsContent value="json">
                <JSONViewer
                  data={event.structured_output as Record<string, unknown>}
                  defaultExpandDepth={1}
                />
              </TabsContent>
            )}

            {hasFingerprint && (
              <TabsContent value="fingerprint">
                <JSONViewer
                  data={fingerprint as Record<string, unknown>}
                  defaultExpandDepth={2}
                />
              </TabsContent>
            )}
          </Tabs>

          {meta?.notes && (
            <p className="text-[11px] text-fg-subtle italic">{meta.notes}</p>
          )}
        </div>
      )}
    </div>
  );
}
