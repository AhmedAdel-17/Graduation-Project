import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { createChart, AreaSeries, type IChartApi } from "lightweight-charts";
import { api } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";

export function EGX30Chart({ days = 30, height = 220 }: { days?: number; height?: number }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ["egx30-history", days],
    queryFn: () => api.market.egx30History(days),
  });

  useEffect(() => {
    if (!containerRef.current || !data) return;
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height,
      layout: {
        background: { color: "transparent" },
        textColor: "#a1a1aa",
        fontFamily: "JetBrains Mono, monospace",
      },
      grid: {
        vertLines: { color: "#27272a" },
        horzLines: { color: "#27272a" },
      },
      rightPriceScale: { borderColor: "#27272a" },
      timeScale: { borderColor: "#27272a", timeVisible: false },
      crosshair: { mode: 1 },
    });
    chartRef.current = chart;
    const series = chart.addSeries(AreaSeries, {
      lineColor: "#10B981",
      topColor: "rgba(16, 185, 129, 0.3)",
      bottomColor: "rgba(16, 185, 129, 0.0)",
      lineWidth: 2,
    });
    series.setData(data.map((d) => ({ time: d.date, value: d.close })));
    chart.timeScale().fitContent();

    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      chart.applyOptions({ width: w });
    });
    ro.observe(containerRef.current);
    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [data, height]);

  if (isLoading) return <Skeleton style={{ height }} className="w-full" />;
  return <div ref={containerRef} style={{ height }} className="w-full" />;
}
