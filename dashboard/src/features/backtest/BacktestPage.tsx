import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { AppShell } from "../../components/layout/AppShell";
import { Card, CardBody, CardHeader, CardTitle, CardDescription } from "../../components/ui/Card";
import { BacktestForm, type BacktestFormValues } from "./BacktestForm";
import { BacktestResult } from "./BacktestResult";
import { useAppStore } from "../../store/appStore";
import {
  useBacktestCompare,
  useBacktests,
  useRunBacktest,
} from "../../hooks/useBacktest";
import { Clock, Layers } from "lucide-react";
import { Skeleton } from "../../components/ui/Skeleton";

export function BacktestPage() {
  const ticker = useAppStore((s) => s.selectedTicker);
  const setTicker = useAppStore((s) => s.setSelectedTicker);
  const initialCapital = useAppStore((s) => s.initialCapital);
  const setInitialCapital = useAppStore((s) => s.setInitialCapital);

  const [polling, setPolling] = useState(false);
  const runBacktest = useRunBacktest();
  const { data: compare, refetch: refetchCompare, isFetching } = useBacktestCompare(
    ticker,
    !!ticker
  );
  const { data: list } = useBacktests(polling);

  // Poll every 5s while a run is in progress, then stop once a report shows up
  // newer than our snapshot
  const [lastSessionIdAtStart, setLastSessionIdAtStart] = useState<string | null>(null);
  const latestForTicker = useMemo(() => {
    if (!list?.sessions) return null;
    return (
      list.sessions.find(
        (s) =>
          s.ticker === ticker && s.engine === "llm_multi_agent"
      )?.session_id ?? null
    );
  }, [list, ticker]);

  useEffect(() => {
    if (!polling) return;
    if (latestForTicker && latestForTicker !== lastSessionIdAtStart) {
      setPolling(false);
      refetchCompare();
      toast.success("New backtest results available", { duration: 4000 });
    }
  }, [latestForTicker, lastSessionIdAtStart, polling, refetchCompare]);

  async function handleSubmit(values: BacktestFormValues) {
    try {
      setLastSessionIdAtStart(latestForTicker);
      await runBacktest.mutateAsync({
        ticker: values.ticker,
        start_date: values.start_date,
        end_date: values.end_date,
        interval: values.interval,
        initial_capital: values.initial_capital,
        selected_analysts: values.selected_analysts,
      });
      setPolling(true);
      toast.success(
        `Backtest started for ${values.ticker}. Results will appear when ready.`
      );
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to start backtest");
    }
  }

  return (
    <AppShell
      title="Backtesting"
      subtitle="Simulate how the multi-agent strategy would have performed historically"
    >
      <div className="grid gap-5 lg:grid-cols-[380px_minmax(0,1fr)]">
        {/* LEFT — Form */}
        <div className="space-y-5 lg:sticky lg:top-20 h-fit">
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Configure Simulation</CardTitle>
                <CardDescription>
                  Pick a stock, horizon, and starting capital. The backend will
                  replay the period with the multi-agent strategy.
                </CardDescription>
              </div>
            </CardHeader>
            <CardBody>
              <BacktestForm
                ticker={ticker}
                onTickerChange={setTicker}
                initialCapital={initialCapital}
                onInitialCapitalChange={setInitialCapital}
                onSubmit={handleSubmit}
                submitting={runBacktest.isPending}
              />

              {polling && (
                <div className="mt-4 rounded-lg border border-accent/30 bg-accent/10 text-accent px-3 py-2 text-xs flex items-center gap-2">
                  <Clock className="h-3.5 w-3.5 animate-pulse" />
                  Running backtest… polling for results.
                </div>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Layers className="h-4 w-4 text-fg-muted" />
                Recent runs
              </CardTitle>
            </CardHeader>
            <CardBody className="space-y-2 max-h-64 overflow-y-auto">
              {!list ? (
                <>
                  <Skeleton className="h-10" />
                  <Skeleton className="h-10" />
                </>
              ) : list.sessions.length === 0 ? (
                <p className="text-xs text-fg-muted">No runs yet.</p>
              ) : (
                list.sessions.slice(0, 8).map((s) => (
                  <button
                    key={s.session_id}
                    onClick={() => setTicker(s.ticker)}
                    className="w-full text-left rounded-md border border-line bg-ink-800/40 hover:bg-ink-800 px-3 py-2 transition-colors"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="num text-xs text-fg">{s.ticker}</span>
                      <span
                        className={
                          "text-[10px] uppercase tracking-wider " +
                          (s.engine === "llm_multi_agent"
                            ? "text-brand-400"
                            : "text-accent")
                        }
                      >
                        {s.engine === "llm_multi_agent" ? "LLM" : "BT"}
                      </span>
                    </div>
                    <p className="text-[11px] text-fg-muted truncate mt-0.5">
                      {s.total_trades} trades
                      {s.metrics?.total_return_pct !== undefined
                        ? ` · ${Number(s.metrics.total_return_pct).toFixed(2)}%`
                        : ""}
                    </p>
                  </button>
                ))
              )}
            </CardBody>
          </Card>
        </div>

        {/* RIGHT — Results */}
        <div>
          {isFetching && !compare ? (
            <Card>
              <CardBody className="space-y-3">
                <Skeleton className="h-6 w-56" />
                <div className="grid grid-cols-4 gap-3">
                  {Array.from({ length: 8 }).map((_, i) => (
                    <Skeleton key={i} className="h-20" />
                  ))}
                </div>
                <Skeleton className="h-[320px] w-full" />
              </CardBody>
            </Card>
          ) : (
            <BacktestResult data={compare} initialCapital={initialCapital} />
          )}
        </div>
      </div>
    </AppShell>
  );
}
