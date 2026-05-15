import { useEffect, useRef } from "react";
import {
  createChart,
  AreaSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
  type AreaData,
  type LineData,
} from "lightweight-charts";
import { chartTheme, colors } from "./chartTheme";
import { cn } from "../../lib/utils";

function toTime(dateStr: string): UTCTimestamp {
  return Math.floor(new Date(`${dateStr}T00:00:00Z`).getTime() / 1000) as UTCTimestamp;
}

export interface EquityPointInput {
  date: string;
  value: number;
}

export interface EquityCurveProps {
  equity: EquityPointInput[];
  benchmark?: EquityPointInput[];
  height?: number;
  className?: string;
}

export function EquityCurve({
  equity,
  benchmark,
  height = 320,
  className,
}: EquityCurveProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const areaRef = useRef<ISeriesApi<"Area"> | null>(null);
  const benchRef = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      ...chartTheme,
      autoSize: true,
      height,
    });
    const area = chart.addSeries(AreaSeries, {
      lineColor: colors.accent,
      topColor: "rgba(52,211,153,0.35)",
      bottomColor: "rgba(52,211,153,0.02)",
      lineWidth: 2,
      priceLineVisible: false,
    });
    const bench = chart.addSeries(LineSeries, {
      color: colors.line,
      lineWidth: 1,
      lineStyle: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    chartRef.current = chart;
    areaRef.current = area;
    benchRef.current = bench;

    return () => {
      chart.remove();
      chartRef.current = null;
      areaRef.current = null;
      benchRef.current = null;
    };
  }, [height]);

  useEffect(() => {
    if (!areaRef.current) return;
    const sorted = [...equity].sort((a, b) =>
      a.date < b.date ? -1 : a.date > b.date ? 1 : 0
    );
    const data: AreaData<UTCTimestamp>[] = sorted.map((p) => ({
      time: toTime(p.date),
      value: Number(p.value),
    }));
    areaRef.current.setData(data);
    chartRef.current?.timeScale().fitContent();
  }, [equity]);

  useEffect(() => {
    if (!benchRef.current) return;
    if (!benchmark || benchmark.length === 0) {
      benchRef.current.setData([]);
      return;
    }
    const sorted = [...benchmark].sort((a, b) =>
      a.date < b.date ? -1 : a.date > b.date ? 1 : 0
    );
    const data: LineData<UTCTimestamp>[] = sorted.map((p) => ({
      time: toTime(p.date),
      value: Number(p.value),
    }));
    benchRef.current.setData(data);
  }, [benchmark]);

  return (
    <div
      ref={containerRef}
      className={cn("w-full", className)}
      style={{ height }}
    />
  );
}
