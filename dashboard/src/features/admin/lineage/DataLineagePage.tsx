import { useEffect, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Boxes,
  ChevronRight,
  Database,
  Filter,
  Newspaper,
  Share2,
  Sparkles,
  TrendingUp,
  Users,
} from "lucide-react";
import { AdminShell } from "../layout/AdminShell";
import { AdminCard } from "../shared/AdminCard";
import { JsonTree } from "../shared/JsonTree";
import { useResultsIndex, useSessionTrace } from "../../../hooks/useSessionTrace";
import {
  LINEAGE_STAGES,
  LINEAGE_TRACKS,
  trackMetrics,
  type LineageStage,
  type LineageTrack,
  type SourceType,
} from "./lineageModel";
import { cn } from "../../../lib/utils";

const STAGE_ICON: Record<LineageStage, React.ReactNode> = {
  Source: <Database className="h-3.5 w-3.5" aria-hidden />,
  Collection: <Boxes className="h-3.5 w-3.5" aria-hidden />,
  Cleaning: <Filter className="h-3.5 w-3.5" aria-hidden />,
  Transformation: <Share2 className="h-3.5 w-3.5" aria-hidden />,
  Enrichment: <Sparkles className="h-3.5 w-3.5" aria-hidden />,
  Consumption: <Users className="h-3.5 w-3.5" aria-hidden />,
};

const TRACK_ICON: Record<SourceType, React.ReactNode> = {
  market: <TrendingUp className="h-4 w-4" aria-hidden />,
  fundamentals: <Database className="h-4 w-4" aria-hidden />,
  news: <Newspaper className="h-4 w-4" aria-hidden />,
  social: <Users className="h-4 w-4" aria-hidden />,
};

export function DataLineagePage() {
  const [params, setParams] = useSearchParams();
  const index = useResultsIndex();
  const sessions = useMemo(() => index.data?.sessions ?? [], [index.data]);
  const urlSession = params.get("session");
  const selectedId = urlSession ?? sessions[0]?.session_id ?? null;

  useEffect(() => {
    if (!urlSession && sessions[0]?.session_id) {
      setParams({ session: sessions[0].session_id }, { replace: true });
    }
  }, [urlSession, sessions, setParams]);

  const trace = useSessionTrace(selectedId);
  const session = trace.data?.session ?? null;
  const dataQuality =
    (session?.data_quality as Record<string, unknown> | null) ?? null;
  const collectedAt = session?.created_at ?? null;

  return (
    <AdminShell title="Data Lineage Explorer" subtitle="Source → collection → consumption">
      <div className="flex flex-col gap-5">
        {/* Session picker */}
        <AdminCard className="p-4">
          <div className="flex flex-col sm:flex-row sm:items-center gap-3">
            <label className="flex flex-col gap-1.5 flex-1 min-w-0">
              <span className="text-[10.5px] uppercase tracking-wider text-stone-500 dark:text-[var(--ink-3)]">
                Run (for per-run data quality)
              </span>
              <select
                value={selectedId ?? ""}
                onChange={(e) => setParams({ session: e.target.value })}
                disabled={sessions.length === 0}
                className="h-9 rounded-md border border-stone-200 bg-white text-[13px] text-ink px-2.5 focus:outline-none focus:ring-2 focus:ring-blue-500/40 dark:bg-[var(--paper)] dark:border-[var(--hairline)] disabled:opacity-50"
              >
                {sessions.length === 0 && <option value="">No runs</option>}
                {sessions.map((s) => (
                  <option key={s.session_id} value={s.session_id}>
                    {s.ticker} · {s.trade_date} · {s.session_id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </label>
            {collectedAt && (
              <span className="text-[12px] text-stone-500 dark:text-[var(--ink-3)]">
                Collected{" "}
                <span className="num font-medium text-ink">
                  {new Date(collectedAt).toLocaleString(undefined, {
                    month: "short",
                    day: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              </span>
            )}
            {index.isError && (
              <span className="text-[12px] text-red-600 dark:text-red-400">
                Couldn’t load the run list — showing the static pipeline only.
              </span>
            )}
          </div>
        </AdminCard>

        {/* Lineage tracks */}
        <div className="flex flex-col gap-4">
          {LINEAGE_TRACKS.map((track) => (
            <TrackRow
              key={track.id}
              track={track}
              metrics={trackMetrics(track, dataQuality)}
            />
          ))}
        </div>

        {/* Raw per-run data quality */}
        {dataQuality && Object.keys(dataQuality).length > 0 && (
          <AdminCard>
            <div className="px-4 py-3 border-b border-stone-200/80 dark:border-[var(--hairline)] flex items-center gap-2">
              <Database className="h-4 w-4 text-stone-500 dark:text-[var(--ink-3)]" aria-hidden />
              <span className="text-[13px] font-semibold text-ink">Run data quality</span>
            </div>
            <div className="p-4">
              <JsonTree data={dataQuality} defaultExpandDepth={2} maxHeight="40vh" />
            </div>
          </AdminCard>
        )}
      </div>
    </AdminShell>
  );
}

function TrackRow({
  track,
  metrics,
}: {
  track: LineageTrack;
  metrics: { key: string; value: string }[];
}) {
  return (
    <AdminCard className="p-4">
      <div className="flex items-center gap-2.5 mb-3">
        <span className="h-8 w-8 shrink-0 rounded-lg bg-stone-100 dark:bg-white/[0.04] text-stone-600 dark:text-[var(--ink-2)] flex items-center justify-center">
          {TRACK_ICON[track.id]}
        </span>
        <div className="min-w-0">
          <div className="text-[13px] font-semibold text-ink">{track.label}</div>
          <div className="text-[11.5px] text-stone-500 dark:text-[var(--ink-3)] truncate">
            {track.sourceType}
          </div>
        </div>
        {metrics.length > 0 && (
          <div className="ml-auto flex flex-wrap gap-1.5 justify-end">
            {metrics.map((m) => (
              <span
                key={m.key}
                className="px-2 py-0.5 rounded-md text-[10.5px] num bg-emerald-50 text-emerald-700 border border-emerald-200 dark:bg-emerald-900/20 dark:text-emerald-300 dark:border-emerald-900/40"
                title={`${m.key} (from this run's data_quality)`}
              >
                {m.key}: {m.value}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Stage pipeline */}
      <div className="flex flex-col lg:flex-row lg:items-stretch gap-2">
        {LINEAGE_STAGES.map((stage, i) => (
          <div key={stage} className="flex items-stretch gap-2 flex-1 min-w-0">
            <div className="flex-1 min-w-0 rounded-lg border border-stone-200 dark:border-[var(--hairline)] bg-stone-50/60 dark:bg-white/[0.02] px-2.5 py-2">
              <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-stone-400 dark:text-[var(--ink-3)]">
                <span className={cn(stage === "Consumption" && "text-blue-500")}>{STAGE_ICON[stage]}</span>
                {stage}
              </div>
              <div className="text-[11.5px] text-ink mt-1 leading-snug">
                {track.stages[stage]}
              </div>
            </div>
            {i < LINEAGE_STAGES.length - 1 && (
              <div className="hidden lg:flex items-center text-stone-300 dark:text-stone-600">
                <ChevronRight className="h-4 w-4" aria-hidden />
              </div>
            )}
          </div>
        ))}
      </div>
    </AdminCard>
  );
}
