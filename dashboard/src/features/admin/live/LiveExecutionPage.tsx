import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Radio, Wifi, WifiOff, Eye } from "lucide-react";
import { AdminShell } from "../layout/AdminShell";
import { AdminCard, AdminCardBody, AdminCardHeader } from "../shared/AdminCard";
import { RunControls } from "./RunControls";
import { GlobalProgress } from "./GlobalProgress";
import { ExecutionTimeline } from "./ExecutionTimeline";
import { useLiveRun } from "./useLiveRun";
import { useLiveFeed } from "./useLiveFeed";
import { deriveExecution } from "./executionModel";
import { cn } from "../../../lib/utils";
import { WS_NODE_TO_CATALOG, catalogNodeById } from "../../../data/agent-catalog";

function labelFor(node: string): string {
  if (node === "System") return "System";
  return catalogNodeById(WS_NODE_TO_CATALOG[node])?.label ?? node;
}

export function LiveExecutionPage() {
  const run = useLiveRun(); // manual run started from this page
  const feed = useLiveFeed(); // passive watch of runs started anywhere
  const { stream } = run;

  const manualActive = run.isLive || stream.events.length > 0;
  const watching = !manualActive && feed.activeRun != null;

  // Unified view — manual run takes precedence; otherwise the watched feed run.
  const events = watching ? feed.activeRun!.events : stream.events;
  const analysts = watching ? feed.activeRun!.analysts : run.analysts;
  const isLive = watching ? feed.activeRun!.status === "running" : run.isLive;
  const watchedTicker = watching ? feed.activeRun!.ticker : null;
  const watchedSource = watching ? feed.activeRun!.source : null;

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

  const lastAt = watching
    ? feed.activeRun!.lastAt
    : stream.lastMessageAt;
  const lastAge =
    lastAt !== null && lastAt !== undefined
      ? Math.max(0, Math.floor((now - lastAt) / 1000))
      : null;

  const recent = useMemo(() => [...events].slice(-40).reverse(), [events]);

  return (
    <AdminShell
      title="Live Execution"
      subtitle="Real-time multi-agent run tracking"
      actions={
        <span
          className={cn(
            "hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[11px]",
            isLive
              ? "bg-blue-50 text-blue-700 border-blue-200 dark:bg-sky-900/20 dark:text-sky-300 dark:border-sky-900/40"
              : feed.connected
              ? "bg-white text-stone-500 border-stone-200 dark:bg-[var(--paper)] dark:text-[var(--ink-3)] dark:border-[var(--hairline)]"
              : "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/20 dark:text-amber-300 dark:border-amber-900/40"
          )}
          title={feed.connected ? "Watching the live feed" : "Live feed disconnected"}
        >
          {isLive ? (
            <Wifi className="h-3 w-3" aria-hidden />
          ) : feed.connected ? (
            <Eye className="h-3 w-3" aria-hidden />
          ) : (
            <WifiOff className="h-3 w-3" aria-hidden />
          )}
          {isLive ? "Live" : feed.connected ? "Watching" : "Offline"}
        </span>
      }
    >
      <div className="flex flex-col gap-5">
        {/* Watching banner */}
        {watching && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg border border-blue-200 bg-blue-50 text-blue-700 text-[12px] dark:border-sky-900/40 dark:bg-sky-900/15 dark:text-sky-300">
            <Eye className="h-4 w-4 shrink-0" aria-hidden />
            <span>
              Watching a run{watchedTicker ? ` for ${watchedTicker}` : ""}
              {watchedSource === "main" ? " started from the main dashboard" : ""} ·{" "}
              {feed.activeRun!.status === "running" ? "in progress" : feed.activeRun!.status}
            </span>
          </div>
        )}

        <RunControls isLive={run.isLive} onStart={run.start} onCancel={run.cancel} />

        {stream.status === "error" && !watching && (
          <div
            role="alert"
            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-red-200 bg-red-50 text-red-700 text-[12px] dark:border-red-900/40 dark:bg-red-900/20 dark:text-red-300"
          >
            <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
            <span className="truncate">{stream.error ?? "Stream error"}</span>
          </div>
        )}

        {events.length === 0 ? (
          <AdminCard className="p-10 text-center">
            <p className="text-[14px] font-semibold text-ink">No run in progress</p>
            <p className="text-[12.5px] text-stone-500 dark:text-[var(--ink-3)] mt-1">
              Start a run above, or launch one from the main dashboard — it will appear here live.
            </p>
          </AdminCard>
        ) : (
          <>
            <GlobalProgress snapshot={snapshot} isLive={isLive} />

            <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-5">
              <AdminCard>
                <AdminCardHeader
                  title="Event stream"
                  description={`${events.length} frames`}
                  icon={<Radio className="h-4 w-4" />}
                  actions={
                    lastAge !== null && isLive ? (
                      <span
                        className={cn(
                          "text-[11px] num",
                          lastAge > 15
                            ? "text-amber-600 dark:text-amber-400"
                            : "text-stone-500 dark:text-[var(--ink-3)]"
                        )}
                      >
                        last frame {lastAge}s ago
                      </span>
                    ) : undefined
                  }
                />
                <AdminCardBody className="p-0">
                  <ul className="divide-y divide-stone-100 dark:divide-[var(--hairline)] max-h-[460px] overflow-y-auto">
                    {recent.map((ev, i) => (
                      <li key={`${ev.node}-${ev.timestamp}-${i}`} className="px-4 py-2 flex items-center gap-3">
                        <span
                          className={cn(
                            "h-1.5 w-1.5 rounded-full shrink-0",
                            ev.status === "error"
                              ? "bg-red-500"
                              : ev.status === "completed"
                              ? "bg-emerald-500"
                              : ev.status === "in_progress"
                              ? "bg-blue-500"
                              : "bg-stone-300 dark:bg-stone-600"
                          )}
                          aria-hidden
                        />
                        <span className="text-[12.5px] text-ink font-medium truncate flex-1">
                          {labelFor(ev.node)}
                        </span>
                        <span className="text-[10.5px] uppercase tracking-wider text-stone-400 dark:text-[var(--ink-3)] shrink-0">
                          {ev.status}
                        </span>
                        <span className="text-[10.5px] text-stone-400 dark:text-[var(--ink-3)] num shrink-0 hidden sm:inline">
                          {new Date(ev.timestamp).toLocaleTimeString(undefined, {
                            hour: "2-digit",
                            minute: "2-digit",
                            second: "2-digit",
                          })}
                        </span>
                      </li>
                    ))}
                  </ul>
                </AdminCardBody>
              </AdminCard>

              <AdminCard>
                <AdminCardHeader title="Timeline" description="Per-agent status" />
                <AdminCardBody>
                  <ExecutionTimeline snapshot={snapshot} now={now} isLive={isLive} />
                </AdminCardBody>
              </AdminCard>
            </div>
          </>
        )}
      </div>
    </AdminShell>
  );
}
