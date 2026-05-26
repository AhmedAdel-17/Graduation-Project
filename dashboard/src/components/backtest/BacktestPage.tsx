import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { format, subMonths, differenceInDays } from "date-fns";
import { toast } from "sonner";
import { Loader2, PlayCircle } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Slider } from "@/components/ui/slider";
import { Skeleton } from "@/components/ui/skeleton";
import { Progress } from "@/components/ui/progress";

import { TickerCombobox } from "@/components/run/TickerCombobox";
import { DatePicker } from "@/components/ui/date-picker";
import { api } from "@/lib/api";
import { formatEGP, formatPct, formatRelative } from "@/lib/formatters";
import { cn } from "@/lib/utils";
import type { WSMessage } from "@/types/api";

const ANALYSTS = ["Market", "Fundamentals", "News", "Social"];

export function BacktestPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();

  const today = format(new Date(), "yyyy-MM-dd");
  const [ticker, setTicker] = useState("COMI.CA");
  const [start, setStart] = useState(format(subMonths(new Date(), 6), "yyyy-MM-dd"));
  const [end, setEnd] = useState(today);
  const [interval, setInterval] = useState(14);
  const [capital, setCapital] = useState(1_000_000);
  const [analysts, setAnalysts] = useState<string[]>(ANALYSTS);

  const [job, setJob] = useState<{ id: string; logs: string[] } | null>(null);

  const days = Math.max(1, differenceInDays(new Date(end), new Date(start)));
  const totalSteps = Math.max(1, Math.floor(days / interval));
  const estMin = Math.max(1, Math.round(totalSteps * 0.05));

  // Validation — surface a friendly message instead of a 400 from the API
  const dateError =
    new Date(start) > new Date(end)
      ? "Start date must be on or before end date"
      : new Date(end) > new Date(today)
        ? "End date can't be in the future"
        : null;

  const start_ = useMutation({
    mutationFn: () =>
      api.backtests.start({
        ticker,
        start_date: start,
        end_date: end,
        interval_days: interval,
        capital,
        analysts,
      }),
    onSuccess: ({ job_id }) => setJob({ id: job_id, logs: [] }),
    onError: (e: any) => toast.error(e?.message || "Failed to start backtest"),
  });

  // Job WS
  useEffect(() => {
    if (!job) return;
    const ws = api.jobSocket(job.id);
    const off = ws.on((m: WSMessage) => {
      if (m.type === "agent_progress") {
        setJob((cur) => (cur ? { ...cur, logs: [...cur.logs.slice(-30), m.message] } : cur));
      } else if (m.type === "complete") {
        toast.success("Backtest complete");
        qc.invalidateQueries({ queryKey: ["backtests"] });
        navigate({ to: "/backtest/$id", params: { id: m.run_id } });
      }
    });
    return () => { off(); ws.close(); };
  }, [job?.id, qc, navigate]);

  const listQ = useQuery({ queryKey: ["backtests"], queryFn: () => api.backtests.list({ page: 1 }) });
  const items = listQ.data?.items ?? [];

  const progressPct = job ? Math.min(100, (job.logs.length / totalSteps) * 100) : 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Backtest</h1>
        <p className="text-sm text-muted-foreground">
          Replay the multi-agent system across historical decision dates.
        </p>
      </div>

      {!job ? (
        <Card>
          <CardHeader><CardTitle className="text-base">Configuration</CardTitle></CardHeader>
          <CardContent className="space-y-5">
            <div className="space-y-2">
              <Label>Ticker</Label>
              <TickerCombobox value={ticker} onChange={setTicker} />
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label>Start date</Label>
                <DatePicker
                  value={start}
                  onChange={(v) => {
                    setStart(v);
                    // If user picks a start AFTER the current end, push end forward
                    if (new Date(v) > new Date(end)) setEnd(v);
                  }}
                  maxDate={end || today}
                />
              </div>
              <div className="space-y-2">
                <Label>End date</Label>
                <DatePicker
                  value={end}
                  onChange={setEnd}
                  minDate={start}
                  maxDate={today}
                />
              </div>
            </div>
            {dateError && (
              <p className="text-xs text-destructive">{dateError}</p>
            )}
            <div className="space-y-2">
              <Label>Interval: {interval} days</Label>
              <Slider
                value={[interval]}
                min={7}
                max={30}
                step={7}
                onValueChange={(v) => setInterval(v[0])}
              />
            </div>
            <div className="space-y-2">
              <Label>Initial capital (EGP)</Label>
              <Input
                type="number"
                value={capital}
                step={10_000}
                onChange={(e) => setCapital(Number(e.target.value))}
              />
            </div>
            <div className="space-y-2">
              <Label>Analysts</Label>
              <div className="flex flex-wrap gap-3">
                {ANALYSTS.map((a) => {
                  const active = analysts.includes(a);
                  return (
                    <label key={a} className="flex cursor-pointer items-center gap-2 text-sm">
                      <Checkbox
                        checked={active}
                        onCheckedChange={(v) =>
                          setAnalysts(v ? [...analysts, a] : analysts.filter((x) => x !== a))
                        }
                      />
                      {a}
                    </label>
                  );
                })}
              </div>
            </div>
            <div className="flex items-center justify-between rounded-md border border-border bg-secondary/30 p-3">
              <span className="text-xs text-muted-foreground">
                Estimated runtime: <span className="font-mono">~{estMin} min</span> · {totalSteps} trade dates
              </span>
              <Button onClick={() => start_.mutate()} disabled={start_.isPending || !!dateError}>
                {start_.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <PlayCircle className="mr-2 h-4 w-4" />}
                Start backtest
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Backtest in progress</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <div className="mb-1 flex justify-between text-xs">
                <span className="text-muted-foreground">
                  {Math.min(job.logs.length, totalSteps)} / {totalSteps} trade dates
                </span>
                <span className="font-mono tabular">{progressPct.toFixed(0)}%</span>
              </div>
              <Progress value={progressPct} />
            </div>
            <div className="rounded-md border border-border bg-secondary/30 p-3 font-mono text-xs">
              <div className="mb-2 text-[10px] uppercase tracking-wider text-muted-foreground">
                Decision stream
              </div>
              <div className="max-h-48 space-y-1 overflow-auto">
                {job.logs.length === 0 && (
                  <div className="text-muted-foreground">Waiting for first decision…</div>
                )}
                {job.logs.map((l, i) => (
                  <div key={i} className="text-muted-foreground">{l}</div>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      <div>
        <h2 className="mb-3 text-base font-semibold">Saved backtests</h2>
        {listQ.isLoading ? (
          <div className="grid gap-3 md:grid-cols-3">
            {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-32" />)}
          </div>
        ) : items.length === 0 ? (
          <Card><CardContent className="p-8 text-center text-sm text-muted-foreground">
            No backtests yet.
          </CardContent></Card>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
            {items.map((b) => (
              <Card
                key={b.id}
                onClick={() => navigate({ to: "/backtest/$id", params: { id: b.id } })}
                className="cursor-pointer transition-colors hover:border-primary/40 hover:bg-secondary/40"
              >
                <CardContent className="p-4">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-sm font-medium">{b.ticker}</span>
                    <span className="text-xs text-muted-foreground">{formatRelative(b.created_at)}</span>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {b.start_date} → {b.end_date}
                  </div>
                  <div className={cn(
                    "mt-3 font-mono text-xl font-bold tabular",
                    b.metrics.total_return_pct >= 0 ? "text-primary" : "text-destructive",
                  )}>
                    {formatPct(b.metrics.total_return_pct)}
                  </div>
                  <Button
                    size="sm"
                    variant="secondary"
                    className="mt-3 w-full"
                    onClick={(e) => {
                      e.stopPropagation();
                      navigate({ to: "/backtest/$id", params: { id: b.id } });
                    }}
                  >
                    Open
                  </Button>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
