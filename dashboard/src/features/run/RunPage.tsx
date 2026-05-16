import { useEffect, useMemo, useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { Card, CardBody, CardHeader, CardTitle, CardDescription } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { Badge } from "../../components/ui/Badge";
import { StockSelector } from "../../components/ui/StockSelector";
import { EmptyState } from "../../components/ui/EmptyState";
import { cn } from "../../lib/utils";
import { useT } from "../../lib/i18n";
import { useAppStore } from "../../store/appStore";
import { useAgentStream } from "../../hooks/useAgentStream";
import { AgentTimeline } from "./AgentTimeline";
import { ReasoningCanvas } from "./ReasoningCanvas";
import { Play, Square, Wifi, WifiOff, AlertTriangle } from "lucide-react";

const ALL_ANALYSTS = ["market", "fundamentals", "news", "social"] as const;
type AnalystKey = (typeof ALL_ANALYSTS)[number];

function today(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function RunPage() {
  const t = useT();
  const { selectedTicker, setSelectedTicker } = useAppStore();
  const [tradeDate, setTradeDate] = useState<string>(today());
  const [analysts, setAnalysts] = useState<AnalystKey[]>([
    "market",
    "fundamentals",
  ]);
  const stream = useAgentStream();

  // Live "last message age" so the streaming-health ribbon updates without
  // waiting for the next event. Lazy initializer keeps the render pure.
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    if (stream.status !== "streaming") return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [stream.status]);

  const isRunning =
    stream.status === "connecting" || stream.status === "streaming";

  const toggleAnalyst = (key: AnalystKey) => {
    setAnalysts((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
  };

  const handleStart = () => {
    if (!selectedTicker || analysts.length === 0) return;
    stream.start({
      ticker: selectedTicker,
      trade_date: tradeDate,
      selected_analysts: analysts,
      max_debate_rounds: 1,
      max_risk_rounds: 1,
    });
  };

  const activeNode = useMemo(() => {
    // Most recent event with status="in_progress" wins; otherwise the latest
    // node we've heard from.
    for (let i = stream.events.length - 1; i >= 0; i--) {
      const ev = stream.events[i];
      if (ev.status === "in_progress") return ev.node;
    }
    return stream.lastEvent?.node ?? null;
  }, [stream.events, stream.lastEvent]);

  const lastAge =
    stream.lastMessageAt !== null
      ? Math.max(0, Math.floor((now - stream.lastMessageAt) / 1000))
      : null;

  return (
    <AppShell title={t("run.title")} subtitle={t("run.subtitle")}>
      <div className="flex flex-col gap-5">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>{t("run.controls.title")}</CardTitle>
              <CardDescription>{t("run.controls.desc")}</CardDescription>
            </div>
            <div className="flex items-center gap-2">
              {isRunning ? (
                <Button
                  variant="outline"
                  size="sm"
                  leftIcon={<Square className="h-3.5 w-3.5" aria-hidden />}
                  onClick={stream.cancel}
                >
                  {t("run.controls.cancel")}
                </Button>
              ) : (
                <Button
                  size="sm"
                  leftIcon={<Play className="h-3.5 w-3.5" aria-hidden />}
                  onClick={handleStart}
                  disabled={!selectedTicker || analysts.length === 0}
                >
                  {t("run.controls.start")}
                </Button>
              )}
            </div>
          </CardHeader>
          <CardBody>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <label className="flex flex-col gap-1.5">
                <span className="text-[11px] uppercase tracking-wider text-fg-muted">
                  {t("run.controls.ticker")}
                </span>
                <StockSelector
                  value={selectedTicker}
                  onChange={setSelectedTicker}
                  disabled={isRunning}
                />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-[11px] uppercase tracking-wider text-fg-muted">
                  {t("run.controls.date")}
                </span>
                <Input
                  type="date"
                  value={tradeDate}
                  onChange={(e) => setTradeDate(e.target.value)}
                  disabled={isRunning}
                />
              </label>
              <div className="flex flex-col gap-1.5">
                <span className="text-[11px] uppercase tracking-wider text-fg-muted">
                  {t("run.controls.analysts")}
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {ALL_ANALYSTS.map((key) => {
                    const on = analysts.includes(key);
                    return (
                      <button
                        key={key}
                        type="button"
                        onClick={() => toggleAnalyst(key)}
                        disabled={isRunning}
                        aria-pressed={on}
                        className={cn(
                          "h-8 px-3 rounded-md text-xs border transition-colors",
                          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50",
                          "disabled:opacity-50 disabled:cursor-not-allowed",
                          on
                            ? "bg-brand-500/15 text-brand-400 border-brand-500/30"
                            : "bg-ink-800 text-fg-muted border-line hover:text-fg"
                        )}
                      >
                        {t(`run.analyst.${key}`)}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          </CardBody>
        </Card>

        <StreamRibbon
          status={stream.status}
          error={stream.error}
          lastAgeSec={lastAge}
          eventCount={stream.events.length}
        />

        {stream.events.length === 0 && stream.status === "idle" ? (
          <Card>
            <CardBody>
              <EmptyState
                icon={<Play className="h-4 w-4" />}
                title={t("run.empty.title")}
                description={t("run.empty.desc")}
              />
            </CardBody>
          </Card>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-5">
            <ReasoningCanvas events={stream.events} />
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>{t("run.timeline.title")}</CardTitle>
                  <CardDescription>
                    {t("run.timeline.desc")}
                  </CardDescription>
                </div>
                <Badge tone="neutral">
                  {stream.events.length} {t("run.timeline.events")}
                </Badge>
              </CardHeader>
              <CardBody>
                <AgentTimeline
                  selectedAnalysts={analysts}
                  nodeStatuses={stream.nodeStatuses}
                  activeNode={activeNode}
                />
              </CardBody>
            </Card>
          </div>
        )}
      </div>
    </AppShell>
  );
}

function StreamRibbon({
  status,
  error,
  lastAgeSec,
  eventCount,
}: {
  status: ReturnType<typeof useAgentStream>["status"];
  error: string | null;
  lastAgeSec: number | null;
  eventCount: number;
}) {
  const t = useT();
  if (status === "idle") return null;

  if (status === "error") {
    return (
      <div
        role="alert"
        className="flex items-center gap-2 px-3 py-2 rounded-lg border border-down/30 bg-down/10 text-down text-xs"
      >
        <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span className="truncate">{error ?? t("common.error")}</span>
      </div>
    );
  }

  const live = status === "streaming" || status === "connecting";
  const stale = lastAgeSec !== null && lastAgeSec > 15;
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 px-3 py-2 rounded-lg border text-xs",
        live
          ? stale
            ? "border-amber-400/30 bg-amber-400/10 text-amber-300"
            : "border-accent/30 bg-accent/10 text-accent"
          : "border-line bg-ink-800/60 text-fg-muted"
      )}
    >
      <span className="inline-flex items-center gap-2">
        {live ? (
          <Wifi className="h-3.5 w-3.5" aria-hidden />
        ) : (
          <WifiOff className="h-3.5 w-3.5" aria-hidden />
        )}
        <span className="uppercase tracking-wider font-medium">
          {t(`run.stream.${status}`)}
        </span>
      </span>
      <span className="num">
        {eventCount} {t("run.timeline.events")}
        {lastAgeSec !== null && live && (
          <>
            <span className="mx-1.5 text-fg-subtle">·</span>
            {t("run.stream.lastMsg")} {lastAgeSec}s
          </>
        )}
      </span>
    </div>
  );
}
