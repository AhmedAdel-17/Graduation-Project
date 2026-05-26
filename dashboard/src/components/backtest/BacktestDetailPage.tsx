import { useEffect, useRef } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { createChart, AreaSeries, LineSeries } from "lightweight-charts";
import { toast } from "sonner";
import { ArrowLeft, Trash2, TrendingUp, TrendingDown } from "lucide-react";

import { Route as BTDetailRoute } from "@/routes/_authenticated/backtest.$id";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { formatEGP, formatPct } from "@/lib/formatters";
import { DECISION_COLORS } from "@/lib/constants";
import { cn } from "@/lib/utils";

export function BacktestDetailPage() {
  const { id } = BTDetailRoute.useParams();
  const navigate = useNavigate();
  const { data, isLoading } = useQuery({
    queryKey: ["backtest", id],
    queryFn: () => api.backtests.get(id),
  });

  const del = useMutation({
    mutationFn: () => api.backtests.delete(id),
    onSuccess: () => { toast.success("Backtest deleted"); navigate({ to: "/backtest" }); },
  });

  const chartRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!chartRef.current || !data) return;
    const chart = createChart(chartRef.current, {
      width: chartRef.current.clientWidth,
      height: 300,
      layout: { background: { color: "transparent" }, textColor: "#a1a1aa", fontFamily: "JetBrains Mono, monospace" },
      grid: { vertLines: { color: "#27272a" }, horzLines: { color: "#27272a" } },
      rightPriceScale: { borderColor: "#27272a" },
      timeScale: { borderColor: "#27272a" },
    });

    // Strategy equity (green area)
    const strategy = chart.addSeries(AreaSeries, {
      lineColor: "#10B981",
      topColor: "rgba(16,185,129,0.3)",
      bottomColor: "rgba(16,185,129,0)",
      lineWidth: 2,
      title: "Strategy",
    });
    strategy.setData(data.equity_curve.map((d) => ({ time: d.date, value: d.value })));

    // EGX30 benchmark (gray line overlay) — only the points that have benchmark data
    const benchPoints = data.equity_curve
      .filter((d) => d.benchmark != null)
      .map((d) => ({ time: d.date, value: d.benchmark as number }));
    if (benchPoints.length > 0) {
      const bench = chart.addSeries(LineSeries, {
        color: "#94a3b8",
        lineWidth: 2,
        lineStyle: 2, // dashed
        title: "EGX30",
      });
      bench.setData(benchPoints);
    }

    chart.timeScale().fitContent();
    const ro = new ResizeObserver((e) => chart.applyOptions({ width: e[0]?.contentRect.width ?? 0 }));
    ro.observe(chartRef.current);
    return () => { ro.disconnect(); chart.remove(); };
  }, [data]);

  if (isLoading) return <Skeleton className="h-96 w-full" />;
  if (!data) return <div className="text-sm text-muted-foreground">Backtest not found.</div>;

  const m = data.metrics;
  const benchmarkAvailable = m.benchmark_available ?? (m.benchmark_return_pct != null);

  const stats = [
    { label: "Total return", value: formatPct(m.total_return_pct), pos: m.total_return_pct >= 0 },
    { label: "EGX30 return", value: benchmarkAvailable ? formatPct(m.benchmark_return_pct ?? 0) : "—", pos: (m.benchmark_return_pct ?? 0) >= 0 },
    { label: "Buy & Hold", value: formatPct(m.buy_hold_return_pct), pos: m.buy_hold_return_pct >= 0 },
    { label: "α vs Buy&Hold", value: formatPct(m.strategy_alpha_pct ?? 0), pos: (m.strategy_alpha_pct ?? 0) >= 0 },
    { label: "Sharpe", value: m.sharpe.toFixed(2), pos: m.sharpe >= 1 },
    { label: "Max DD", value: formatPct(m.max_drawdown_pct), pos: false },
    { label: "Win rate", value: formatPct(m.win_rate_pct, 1) },
    { label: "Trades", value: String(m.total_trades) },
    { label: "Final", value: formatEGP(m.final_portfolio) },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <Link to="/backtest" className="text-xs text-muted-foreground hover:text-foreground">
            <ArrowLeft className="mr-1 inline h-3 w-3" /> Back
          </Link>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">
            <span className="font-mono">{data.ticker}</span>{" "}
            <span className="text-base font-normal text-muted-foreground">
              · {data.start_date} → {data.end_date}
            </span>
          </h1>
        </div>
        <Button variant="outline" size="sm" onClick={() => del.mutate()}>
          <Trash2 className="mr-1 h-3 w-3" /> Delete
        </Button>
      </div>

      {/* ALPHA HERO — the headline metric the prof wants */}
      <AlphaHero
        alpha={m.alpha_pct}
        strategyReturn={m.total_return_pct}
        benchmarkReturn={m.benchmark_return_pct ?? 0}
        available={benchmarkAvailable}
      />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between text-base">
            <span>Equity curve</span>
            <span className="flex items-center gap-4 text-xs font-normal">
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-0.5 w-4 bg-[#10B981]" /> Strategy
              </span>
              {benchmarkAvailable && (
                <span className="flex items-center gap-1.5 text-muted-foreground">
                  <span className="inline-block h-0.5 w-4 border-t-2 border-dashed border-[#94a3b8]" /> EGX30
                </span>
              )}
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div ref={chartRef} style={{ height: 300 }} />
          {!benchmarkAvailable && (
            <p className="mt-2 text-xs text-muted-foreground">
              EGX30 overlay unavailable — add <code className="font-mono">EGX 30 Historical Data.csv</code> to the
              project root to benchmark against the index.
            </p>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-3 gap-3 md:grid-cols-5 lg:grid-cols-9">
        {stats.map((s) => (
          <Card key={s.label}>
            <CardContent className="p-3">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{s.label}</div>
              <div
                className={cn(
                  "mt-1 font-mono text-base font-semibold tabular",
                  s.pos === true && "text-primary",
                  s.pos === false && "text-destructive",
                )}
              >
                {s.value}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* ALPHA DECAY — how the signal's edge evolves with holding period */}
      <AlphaDecayCard decay={m.alpha_decay} />

      <Card>
        <CardHeader><CardTitle className="text-base">Trade log</CardTitle></CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Action</TableHead>
                <TableHead className="text-right">Price</TableHead>
                <TableHead className="text-right">Shares</TableHead>
                <TableHead className="text-right">P&L</TableHead>
                <TableHead className="text-right">20d fwd</TableHead>
                <TableHead>Outcome</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.trades.map((t, i) => {
                const c = DECISION_COLORS[t.action];
                return (
                  <TableRow key={i}>
                    <TableCell className="font-mono text-xs">{t.date}</TableCell>
                    <TableCell>
                      <Badge className={cn(c.bg, c.text, "font-mono text-[10px]")}>{t.action}</Badge>
                    </TableCell>
                    <TableCell className="text-right font-mono tabular">{t.price.toFixed(2)}</TableCell>
                    <TableCell className="text-right font-mono tabular">{t.shares}</TableCell>
                    <TableCell className={cn(
                      "text-right font-mono tabular",
                      t.realized_pnl > 0 && "text-primary",
                      t.realized_pnl < 0 && "text-destructive",
                    )}>
                      {t.realized_pnl ? formatEGP(t.realized_pnl, true) : "—"}
                    </TableCell>
                    <TableCell className={cn(
                      "text-right font-mono tabular",
                      (t.forward_return_20d ?? 0) > 0 && "text-primary",
                      (t.forward_return_20d ?? 0) < 0 && "text-destructive",
                    )}>
                      {t.forward_return_20d !== undefined ? formatPct(t.forward_return_20d * 100, 1) : "—"}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{t.outcome}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

/**
 * Headline alpha banner — strategy vs EGX30. This is the single most important
 * number for an academic evaluation: did the AI beat the index?
 */
function AlphaHero({
  alpha,
  strategyReturn,
  benchmarkReturn,
  available,
}: {
  alpha: number;
  strategyReturn: number;
  benchmarkReturn: number;
  available: boolean;
}) {
  const beat = alpha >= 0;

  if (!available) {
    return (
      <Card className="border-dashed">
        <CardContent className="flex items-center gap-3 p-5">
          <div className="text-sm text-muted-foreground">
            <span className="font-medium text-foreground">Alpha vs EGX30 not computed.</span>{" "}
            Drop <code className="font-mono">EGX 30 Historical Data.csv</code> into the project root
            and re-run to benchmark against the index.
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className={cn("border", beat ? "border-primary/40" : "border-destructive/40")}>
      <CardContent className="grid gap-4 p-6 md:grid-cols-[1.4fr_1fr_1fr]">
        {/* Alpha — big */}
        <div className="flex items-center gap-4">
          <div
            className={cn(
              "flex h-14 w-14 items-center justify-center rounded-2xl",
              beat ? "bg-primary/15 text-primary" : "bg-destructive/15 text-destructive",
            )}
          >
            {beat ? <TrendingUp className="h-7 w-7" /> : <TrendingDown className="h-7 w-7" />}
          </div>
          <div>
            <div className="text-[11px] font-medium uppercase tracking-[0.15em] text-muted-foreground">
              Alpha vs EGX30
            </div>
            <div
              className={cn(
                "font-mono text-4xl font-bold tabular",
                beat ? "text-primary" : "text-destructive",
              )}
            >
              {formatPct(alpha)}
            </div>
            <div className="text-xs text-muted-foreground">
              {beat ? "Outperformed the index" : "Underperformed the index"}
            </div>
          </div>
        </div>

        {/* Strategy return */}
        <div className="flex flex-col justify-center border-t border-border pt-3 md:border-l md:border-t-0 md:pl-5 md:pt-0">
          <div className="text-[11px] font-medium uppercase tracking-[0.15em] text-muted-foreground">
            Strategy return
          </div>
          <div className={cn("mt-1 font-mono text-2xl font-semibold tabular", strategyReturn >= 0 ? "text-primary" : "text-destructive")}>
            {formatPct(strategyReturn)}
          </div>
        </div>

        {/* EGX30 return */}
        <div className="flex flex-col justify-center border-t border-border pt-3 md:border-l md:border-t-0 md:pl-5 md:pt-0">
          <div className="text-[11px] font-medium uppercase tracking-[0.15em] text-muted-foreground">
            EGX30 return
          </div>
          <div className="mt-1 font-mono text-2xl font-semibold tabular text-muted-foreground">
            {formatPct(benchmarkReturn)}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * Alpha-decay curve — average signed forward return at increasing holding
 * horizons (1/5/10/20/40 trading days), for BUY signals and all signals.
 *
 * A healthy signal rises then plateaus/decays. A signal whose return keeps
 * climbing means you're exiting too early; one that decays to zero means the
 * edge is short-lived. This is a POST-HOC diagnostic (uses future prices on
 * purpose) — NOT a tradeable return.
 */
function AlphaDecayCard({
  decay,
}: {
  decay?: {
    horizons: number[];
    all: (number | null)[];
    buy: (number | null)[];
    sell: (number | null)[];
    n_buy: number;
    n_sell: number;
  };
}) {
  if (!decay || !decay.horizons?.length) return null;

  // Build the series we'll plot: prefer BUY (most meaningful), always show "all"
  const series: { label: string; color: string; data: (number | null)[] }[] = [];
  if (decay.n_buy > 0) series.push({ label: `BUY (n=${decay.n_buy})`, color: "#10B981", data: decay.buy });
  if (decay.n_sell > 0) series.push({ label: `SELL (n=${decay.n_sell})`, color: "#ef4444", data: decay.sell });
  series.push({ label: "All signals", color: "#94a3b8", data: decay.all });

  // Y-axis range across all plotted values
  const allVals = series.flatMap((s) => s.data).filter((v): v is number => v != null);
  if (allVals.length === 0) return null;
  const maxAbs = Math.max(0.5, ...allVals.map((v) => Math.abs(v)));
  const yMax = maxAbs * 1.15;

  const W = 560, H = 220, padL = 44, padR = 16, padT = 16, padB = 32;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const n = decay.horizons.length;
  const x = (i: number) => padL + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const y = (v: number) => padT + plotH / 2 - (v / yMax) * (plotH / 2);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between text-base">
          <span>Alpha decay</span>
          <span className="flex items-center gap-4 text-xs font-normal">
            {series.map((s) => (
              <span key={s.label} className="flex items-center gap-1.5">
                <span className="inline-block h-0.5 w-4" style={{ background: s.color }} />
                <span className="text-muted-foreground">{s.label}</span>
              </span>
            ))}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 240 }}>
          {/* Zero line */}
          <line x1={padL} y1={y(0)} x2={W - padR} y2={y(0)} stroke="#3f3f46" strokeWidth="1" />
          {/* Y labels */}
          <text x={padL - 6} y={y(yMax) + 4} textAnchor="end" fontSize="10" fill="#a1a1aa" fontFamily="monospace">+{yMax.toFixed(1)}%</text>
          <text x={padL - 6} y={y(0) + 4} textAnchor="end" fontSize="10" fill="#a1a1aa" fontFamily="monospace">0%</text>
          <text x={padL - 6} y={y(-yMax) + 4} textAnchor="end" fontSize="10" fill="#a1a1aa" fontFamily="monospace">−{yMax.toFixed(1)}%</text>
          {/* X labels + ticks */}
          {decay.horizons.map((h, i) => (
            <text key={h} x={x(i)} y={H - padB + 18} textAnchor="middle" fontSize="10" fill="#a1a1aa" fontFamily="monospace">+{h}d</text>
          ))}
          {/* Series lines + points */}
          {series.map((s) => {
            const pts = s.data
              .map((v, i) => (v == null ? null : `${x(i)},${y(v)}`))
              .filter(Boolean)
              .join(" ");
            return (
              <g key={s.label}>
                <polyline points={pts} fill="none" stroke={s.color} strokeWidth="2" />
                {s.data.map((v, i) =>
                  v == null ? null : (
                    <circle key={i} cx={x(i)} cy={y(v)} r="3" fill={s.color} />
                  ),
                )}
              </g>
            );
          })}
        </svg>
        <p className="mt-2 text-xs text-muted-foreground">
          Average signed forward return by holding period. Post-hoc signal-quality
          diagnostic (uses future prices) — not a tradeable return.
        </p>
      </CardContent>
    </Card>
  );
}
