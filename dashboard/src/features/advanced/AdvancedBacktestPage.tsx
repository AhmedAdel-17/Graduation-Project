import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Play, Terminal, Info, Zap, Cpu } from "lucide-react";
import { AppShell } from "../../components/layout/AppShell";
import {
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  CardDescription,
} from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { StockSelector } from "../../components/ui/StockSelector";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { useAppStore } from "../../store/appStore";
import {
  useBacktestCompare,
  useBacktests,
  useRunBacktest,
  useRunBtBenchmark,
} from "../../hooks/useBacktest";
import { LogConsole, type LogLine } from "./LogConsole";
import { BacktestResult } from "../backtest/BacktestResult";
import { Skeleton } from "../../components/ui/Skeleton";

const ANALYST_OPTIONS = [
  { id: "market", label: "Market" },
  { id: "fundamentals", label: "Fundamentals" },
  { id: "news", label: "News" },
  { id: "social", label: "Social" },
];

function isoNow() {
  return new Date().toTimeString().slice(0, 8);
}

function daysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export function AdvancedBacktestPage() {
  const ticker = useAppStore((s) => s.selectedTicker);
  const setTicker = useAppStore((s) => s.setSelectedTicker);
  const initialCapital = useAppStore((s) => s.initialCapital);
  const setInitialCapital = useAppStore((s) => s.setInitialCapital);

  const [startDate, setStartDate] = useState(daysAgo(180));
  const [endDate, setEndDate] = useState(new Date().toISOString().slice(0, 10));
  const [interval, setInterval] = useState(7);
  const [analysts, setAnalysts] = useState<string[]>([
    "market",
    "fundamentals",
    "news",
    "social",
  ]);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [running, setRunning] = useState<null | "llm" | "bt">(null);

  const runBacktest = useRunBacktest();
  const runBt = useRunBtBenchmark();
  const { data: list, refetch: refetchList } = useBacktests(!!running);
  const { data: compare, refetch: refetchCompare, isError: compareError } = useBacktestCompare(ticker);

  const startSnapshotRef = useRef<string | null>(null);

  function append(level: LogLine["level"], message: string) {
    setLogs((prev) => [...prev, { ts: isoNow(), level, message }]);
  }

  const latestForTickerId = useMemo(() => {
    if (!list?.sessions) return null;
    const match = list.sessions.find(
      (s) =>
        s.ticker === ticker &&
        (running === "bt"
          ? s.engine === "classical_technical"
          : s.engine === "llm_multi_agent")
    );
    return match?.session_id ?? null;
  }, [list, ticker, running]);

  useEffect(() => {
    if (!running) return;
    if (latestForTickerId && latestForTickerId !== startSnapshotRef.current) {
      append("success", `Report emitted: ${latestForTickerId}`);
      append("info", "Fetching final metrics & equity curve…");
      setRunning(null);
      refetchCompare();
      toast.success("Backtest finished");
    }
  }, [latestForTickerId, running, refetchCompare]);

  // Heartbeat log every 5s while running
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => {
      append("debug", `Heartbeat — engine still processing ${ticker}…`);
      refetchList();
    }, 5000);
    return () => window.clearInterval(id);
  }, [running, ticker, refetchList]);

  function toggleAnalyst(id: string) {
    setAnalysts((a) =>
      a.includes(id) ? a.filter((x) => x !== id) : [...a, id]
    );
  }

  async function handleRunLLM() {
    if (!ticker) return;
    setLogs([]);
    append("info", `$ python scripts/backtester.py --ticker ${ticker} \\`);
    append("info", `    --start ${startDate} --end ${endDate} \\`);
    append(
      "info",
      `    --interval ${interval} --capital ${initialCapital} --analysts ${analysts.join(",")}`
    );
    append("info", "Dispatching to /api/backtests/run (background task)…");
    startSnapshotRef.current =
      list?.sessions.find(
        (s) => s.ticker === ticker && s.engine === "llm_multi_agent"
      )?.session_id ?? null;
    try {
      const res = await runBacktest.mutateAsync({
        ticker,
        start_date: startDate,
        end_date: endDate,
        interval,
        initial_capital: initialCapital,
        selected_analysts: analysts,
      });
      append("success", res.message ?? "Job accepted");
      append("info", "Polling backtest_results/ every 5s for completion…");
      setRunning("llm");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Request failed";
      append("error", msg);
      toast.error(msg);
    }
  }

  async function handleRunBT() {
    if (!ticker) return;
    setLogs([]);
    append("info", `$ python scripts/bt_benchmark.py --ticker ${ticker} \\`);
    append("info", `    --start ${startDate} --end ${endDate} --capital ${initialCapital}`);
    append("info", "Dispatching to /api/backtests/run-bt…");
    startSnapshotRef.current =
      list?.sessions.find(
        (s) => s.ticker === ticker && s.engine === "classical_technical"
      )?.session_id ?? null;
    try {
      const res = await runBt.mutateAsync({
        ticker,
        start_date: startDate,
        end_date: endDate,
        initial_capital: initialCapital,
      });
      append("success", res.message ?? "Job accepted");
      setRunning("bt");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Request failed";
      append("error", msg);
      toast.error(msg);
    }
  }

  return (
    <AppShell
      title="Advanced Backtesting"
      subtitle="Full script execution — configure analysts, interval, and date range"
    >
      <div className="grid gap-5 lg:grid-cols-[400px_minmax(0,1fr)]">
        {/* LEFT — Configuration */}
        <div className="space-y-5">
          <Card>
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Cpu className="h-4 w-4 text-brand-400" />
                  Script Parameters
                </CardTitle>
                <CardDescription>
                  These settings are passed to <span className="font-mono">scripts/backtester.py</span>.
                </CardDescription>
              </div>
            </CardHeader>
            <CardBody className="space-y-4">
              <StockSelector
                label="Stock"
                value={ticker}
                onChange={setTicker}
              />

              <div className="grid grid-cols-2 gap-3">
                <Input
                  label="Start"
                  type="date"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                />
                <Input
                  label="End"
                  type="date"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <Select
                  label="Rebalance interval"
                  value={interval}
                  onChange={(e) => setInterval(Number(e.target.value))}
                >
                  {[2, 3, 5, 7, 10, 14].map((d) => (
                    <option key={d} value={d}>
                      Every {d} days
                    </option>
                  ))}
                </Select>
                <Input
                  label="Initial capital"
                  type="number"
                  min={10_000}
                  step={10_000}
                  value={initialCapital}
                  onChange={(e) =>
                    setInitialCapital(Number(e.target.value) || 0)
                  }
                  rightAddon="EGP"
                />
              </div>

              <div>
                <label className="text-xs font-medium text-fg-muted mb-1.5 block">
                  Analyst agents
                </label>
                <div className="flex flex-wrap gap-2">
                  {ANALYST_OPTIONS.map((a) => {
                    const on = analysts.includes(a.id);
                    return (
                      <button
                        key={a.id}
                        type="button"
                        onClick={() => toggleAnalyst(a.id)}
                        className={
                          "h-8 px-3 rounded-md text-xs border transition-colors " +
                          (on
                            ? "bg-brand-500/15 border-brand-500/30 text-brand-400"
                            : "bg-ink-800/60 border-line text-fg-muted hover:text-fg")
                        }
                      >
                        {a.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="flex flex-col gap-2 pt-2">
                <Button
                  size="lg"
                  leftIcon={<Play className="h-4 w-4" />}
                  loading={runBacktest.isPending || running === "llm"}
                  onClick={handleRunLLM}
                  disabled={!ticker || analysts.length === 0 || !!running}
                >
                  Run Multi-Agent Backtest
                </Button>
                <Button
                  size="lg"
                  variant="outline"
                  leftIcon={<Zap className="h-4 w-4" />}
                  loading={runBt.isPending || running === "bt"}
                  onClick={handleRunBT}
                  disabled={!ticker || !!running}
                >
                  Run Classical Benchmark
                </Button>
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Info className="h-4 w-4 text-fg-muted" />
                Execution notes
              </CardTitle>
            </CardHeader>
            <CardBody className="text-xs text-fg-muted space-y-2 leading-relaxed">
              <p>
                Jobs run as background tasks inside the FastAPI server. The
                console on the right polls <span className="font-mono">/api/backtests</span> every
                5 seconds to detect when a new report file lands in{" "}
                <span className="font-mono">backtest_results/</span>.
              </p>
              <p>
                A full multi-agent run typically takes 2–10 minutes depending on
                horizon, interval, and active analysts.
              </p>
            </CardBody>
          </Card>
        </div>

        {/* RIGHT — Logs + Results */}
        <div className="space-y-5 min-w-0">
          <LogConsole
            lines={logs}
            title="Runtime console"
            emptyHint="Click Run Multi-Agent Backtest or Run Classical Benchmark to dispatch a job."
          />

          {compare ? (
            <BacktestResult
              data={compare}
              initialCapital={initialCapital}
              title="Latest results"
            />
          ) : running ? (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Terminal className="h-4 w-4 text-fg-muted" />
                  Processing…
                </CardTitle>
                <CardDescription>
                  Backtest for {ticker} is running. Results will appear when complete.
                </CardDescription>
              </CardHeader>
              <CardBody>
                <Skeleton className="h-[240px] w-full" />
              </CardBody>
            </Card>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Terminal className="h-4 w-4 text-fg-muted" />
                  {compareError ? "No results yet" : "Waiting for results"}
                </CardTitle>
                <CardDescription>
                  {compareError
                    ? `No backtest has been run for ${ticker} yet. Configure and run a job above.`
                    : `Results for ${ticker} will appear here once a job completes.`}
                </CardDescription>
              </CardHeader>
            </Card>
          )}
        </div>
      </div>
    </AppShell>
  );
}
