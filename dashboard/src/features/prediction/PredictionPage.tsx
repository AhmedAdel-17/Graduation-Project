import { useState } from "react";
import { toast } from "sonner";
import { Play, AlertTriangle, Sparkles, Info, CandlestickChart } from "lucide-react";
import { AppShell } from "../../components/layout/AppShell";
import { StockSelector } from "../../components/ui/StockSelector";
import { Button } from "../../components/ui/Button";
import { Card, CardBody, CardHeader, CardTitle, CardDescription } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { EmptyState } from "../../components/ui/EmptyState";
import { PriceChart } from "../../components/charts";
import { useAppStore } from "../../store/appStore";
import { useRunPrediction } from "../../hooks/usePrediction";
import { useStockData } from "../../hooks/useStockData";
import { PredictionCard } from "./PredictionCard";
import { ThesisPanel } from "./ThesisPanel";

export function PredictionPage() {
  const ticker = useAppStore((s) => s.selectedTicker);
  const setTicker = useAppStore((s) => s.setSelectedTicker);
  const [lookback, setLookback] = useState(90);

  const { data: bars, isLoading: barsLoading, error: barsError, refetch: refetchBars } =
    useStockData(ticker, { days: lookback });
  const runPrediction = useRunPrediction();

  const result = runPrediction.data;
  const rec = result?.recommendation;

  const target =
    rec?.target_price !== undefined && rec?.target_price !== null
      ? Number(rec.target_price)
      : undefined;

  const lastBar = bars && bars.length > 0 ? bars[bars.length - 1] : undefined;
  const predictions =
    target && lastBar
      ? [
          { date: lastBar.date, value: Number(lastBar.close) },
          // Project 14 trading days ahead
          {
            date: addDays(lastBar.date, 14),
            value: target,
          },
        ]
      : undefined;

  async function handleRun() {
    try {
      const res = await runPrediction.mutateAsync(ticker);
      if (res?.error) {
        toast.error(res.error);
        return;
      }
      toast.success(`Prediction complete: ${res?.recommendation?.signal ?? "—"}`);
      // Refresh price history after prediction
      refetchBars();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Prediction failed";
      toast.error(msg);
    }
  }

  return (
    <AppShell
      title="Prediction Dashboard"
      subtitle="Generate multi-agent trading signals for EGX-listed equities"
    >
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        {/* LEFT — Chart + Controls */}
        <div className="flex flex-col gap-5">
          <Card>
            <CardHeader>
              <div className="flex-1 min-w-0">
                <CardTitle className="flex items-center gap-2">
                  <CandlestickChart className="h-4 w-4 text-brand-400" />
                  Price history & projection
                </CardTitle>
                <CardDescription>
                  {ticker} · last {lookback} days · close / high-low / volume
                </CardDescription>
              </div>
              <div className="flex items-center gap-2">
                {[30, 90, 180, 365].map((d) => (
                  <button
                    key={d}
                    onClick={() => setLookback(d)}
                    className={
                      "h-7 px-2.5 text-[11px] rounded-md border transition-colors " +
                      (lookback === d
                        ? "bg-brand-500/15 text-brand-400 border-brand-500/30"
                        : "bg-ink-800/60 text-fg-muted border-line hover:text-fg")
                    }
                  >
                    {d}d
                  </button>
                ))}
              </div>
            </CardHeader>
            <CardBody>
              {barsError ? (
                <EmptyState
                  icon={<AlertTriangle className="h-4 w-4 text-down" />}
                  title="Failed to load price data"
                  description={
                    barsError instanceof Error ? barsError.message : "Unknown error"
                  }
                  action={
                    <Button variant="secondary" size="sm" onClick={() => refetchBars()}>
                      Retry
                    </Button>
                  }
                />
              ) : barsLoading ? (
                <Skeleton className="h-[360px] w-full" />
              ) : bars && bars.length > 0 ? (
                <PriceChart bars={bars} predictions={predictions} />
              ) : (
                <EmptyState
                  icon={<Info className="h-4 w-4" />}
                  title="No price data"
                  description="Try a different ticker or lookback window."
                />
              )}
            </CardBody>
          </Card>

          {runPrediction.isPending ? (
            <Card>
              <CardBody className="space-y-3 py-6">
                <Skeleton className="h-6 w-40" />
                <div className="grid grid-cols-4 gap-3">
                  {Array.from({ length: 4 }).map((_, i) => (
                    <Skeleton key={i} className="h-20" />
                  ))}
                </div>
                <p className="text-xs text-fg-muted flex items-center gap-2">
                  <Sparkles className="h-3.5 w-3.5 text-brand-400" />
                  Agents analyzing {ticker} — technicals, fundamentals, sentiment…
                </p>
              </CardBody>
            </Card>
          ) : result && !result.error ? (
            <>
              <PredictionCard result={result} />
              {rec && <ThesisPanel rec={rec} />}
            </>
          ) : (
            <EmptyPrediction />
          )}
        </div>

        {/* RIGHT — Controls */}
        <div className="lg:sticky lg:top-20 h-fit flex flex-col gap-5">
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Run New Prediction</CardTitle>
                <CardDescription>
                  The multi-agent pipeline aggregates technical, fundamental, and sentiment signals.
                </CardDescription>
              </div>
            </CardHeader>
            <CardBody className="space-y-4">
              <StockSelector
                label="Ticker"
                value={ticker}
                onChange={setTicker}
              />

              <Button
                size="lg"
                className="w-full"
                leftIcon={<Play className="h-4 w-4" />}
                loading={runPrediction.isPending}
                onClick={handleRun}
                disabled={!ticker}
              >
                {runPrediction.isPending ? "Running analysis…" : "Run Prediction"}
              </Button>

              {runPrediction.error && (
                <div className="text-xs rounded-md border border-down/30 bg-down/10 text-down px-3 py-2">
                  {(runPrediction.error as Error).message}
                </div>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>How it works</CardTitle>
            </CardHeader>
            <CardBody className="text-xs text-fg-muted space-y-2 leading-relaxed">
              <p>
                <span className="text-fg font-medium">1. Price fetch.</span> Latest OHLCV
                pulled from Yahoo Finance via the EGX gateway, with live price from Mubasher
                when available.
              </p>
              <p>
                <span className="text-fg font-medium">2. Bull / Bear debate.</span> Two LLM
                researchers argue opposing theses based on indicators and fundamentals.
              </p>
              <p>
                <span className="text-fg font-medium">3. Neutral judge.</span> Synthesizes
                the debate and emits signal, confidence, target, and stop-loss.
              </p>
              <p className="pt-2 border-t border-line mt-3">
                EGX constraints enforced: long-only, no leverage, ±10% daily price limit.
              </p>
            </CardBody>
          </Card>
        </div>
      </div>
    </AppShell>
  );
}

function EmptyPrediction() {
  return (
    <Card>
      <CardBody>
        <EmptyState
          icon={<Sparkles className="h-4 w-4 text-brand-400" />}
          title="No prediction yet"
          description={
            "Pick a ticker on the right and click Run Prediction to generate a trading signal."
          }
        />
      </CardBody>
    </Card>
  );
}

function addDays(dateStr: string, days: number): string {
  const d = new Date(`${dateStr}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}
