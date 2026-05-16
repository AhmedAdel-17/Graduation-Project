import { useMemo, useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/Tabs";
import { JSONViewer } from "../../components/ui/JSONViewer";
import { Markdown } from "../../components/ui/Markdown";
import { StatusPill } from "../../components/ui/StatusPill";
import { EmptyState } from "../../components/ui/EmptyState";
import { cn } from "../../lib/utils";
import { useT } from "../../lib/i18n";
import { Sparkles, Activity } from "lucide-react";
import type { AgentUpdate } from "../../services/api/wsClient";

// Known markdown-bearing keys on agent state. When a chunk includes any of
// these we render the text under the Report tab; otherwise we fall back to
// raw JSON.
const REPORT_KEYS = [
  "market_report",
  "sentiment_report",
  "news_report",
  "fundamentals_report",
  "trader_investment_plan",
  "investment_plan",
  "final_trade_decision",
] as const;

function extractReport(event: AgentUpdate): { key: string; text: string } | null {
  for (const k of REPORT_KEYS) {
    const v = event.data?.[k];
    if (typeof v === "string" && v.trim().length > 0) {
      return { key: k, text: v };
    }
  }
  const msg = event.data?._message;
  if (typeof msg === "string" && msg.trim().length > 0) {
    return { key: "_message", text: msg };
  }
  return null;
}

function shortTime(iso: string): string {
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

export function ReasoningCanvas({
  events,
  className,
}: {
  events: AgentUpdate[];
  className?: string;
}) {
  const t = useT();
  // Reverse-chronological feed. The most recent event is always pre-selected.
  const ordered = useMemo(() => [...events].reverse(), [events]);
  const [selectedTs, setSelectedTs] = useState<string | null>(null);

  if (events.length === 0) {
    return (
      <div className={cn("surface rounded-xl p-6 min-h-[300px]", className)}>
        <EmptyState
          icon={<Sparkles className="h-4 w-4" />}
          title={t("run.canvas.empty.title")}
          description={t("run.canvas.empty.desc")}
        />
      </div>
    );
  }

  const selected =
    ordered.find((e) => e.timestamp === selectedTs) ?? ordered[0];
  const report = extractReport(selected);

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      <FeedRail
        ordered={ordered}
        selectedTs={selected.timestamp}
        onSelect={setSelectedTs}
      />
      <div className="surface rounded-xl p-4 ltr-island">
        <div className="flex items-center justify-between gap-2 mb-3">
          <div className="min-w-0">
            <p className="text-[10px] uppercase tracking-wider text-fg-subtle">
              {t("run.canvas.node")}
            </p>
            <h3 className="text-sm font-semibold text-fg truncate flex items-center gap-2">
              <Activity className="h-3.5 w-3.5 text-brand-400" aria-hidden />
              {selected.node}
            </h3>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <StatusPill status={selected.status} />
            <span className="text-[10px] num text-fg-subtle">
              {shortTime(selected.timestamp)}
            </span>
          </div>
        </div>

        <Tabs defaultValue={report ? "report" : "json"}>
          <TabsList>
            {report && (
              <TabsTrigger value="report">{t("run.canvas.tab.report")}</TabsTrigger>
            )}
            <TabsTrigger value="json">{t("run.canvas.tab.json")}</TabsTrigger>
            <TabsTrigger value="keys">{t("run.canvas.tab.keys")}</TabsTrigger>
          </TabsList>

          {report && (
            <TabsContent value="report">
              <p className="text-[10px] uppercase tracking-wider text-fg-subtle mb-2">
                {report.key}
              </p>
              <Markdown>{report.text}</Markdown>
            </TabsContent>
          )}

          <TabsContent value="json">
            <JSONViewer data={selected.data} defaultExpandDepth={1} />
          </TabsContent>

          <TabsContent value="keys">
            {selected.state_keys.length === 0 ? (
              <p className="text-xs text-fg-muted">
                {t("run.canvas.keys.empty")}
              </p>
            ) : (
              <ul className="flex flex-wrap gap-1.5">
                {selected.state_keys.map((k) => (
                  <li
                    key={k}
                    className="px-2 py-0.5 rounded-md text-[11px] num bg-ink-800 border border-line text-fg-muted"
                  >
                    {k}
                  </li>
                ))}
              </ul>
            )}
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}

function FeedRail({
  ordered,
  selectedTs,
  onSelect,
}: {
  ordered: AgentUpdate[];
  selectedTs: string;
  onSelect: (ts: string | null) => void;
}) {
  return (
    <div
      className="surface rounded-xl px-1 py-1 overflow-x-auto"
      role="tablist"
      aria-label="recent events"
    >
      <ul className="flex items-center gap-1 min-w-max">
        {ordered.slice(0, 12).map((ev) => {
          const active = ev.timestamp === selectedTs;
          return (
            <li key={ev.timestamp}>
              <button
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => onSelect(ev.timestamp)}
                className={cn(
                  "h-7 px-2.5 rounded-md text-[11px] font-medium inline-flex items-center gap-1.5",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50",
                  active
                    ? "bg-ink-800 text-fg border border-line-strong"
                    : "text-fg-muted hover:text-fg border border-transparent"
                )}
              >
                <span
                  className={cn(
                    "h-1.5 w-1.5 rounded-full shrink-0",
                    ev.status === "completed" && "bg-up",
                    ev.status === "in_progress" && "bg-accent animate-pulse",
                    ev.status === "error" && "bg-down",
                    ev.status === "idle" && "bg-fg-subtle"
                  )}
                  aria-hidden
                />
                <span className="truncate max-w-[120px]">{ev.node}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
