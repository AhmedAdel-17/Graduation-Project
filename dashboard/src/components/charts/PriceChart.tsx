import { useEffect, useRef } from "react";
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
  type CandlestickData,
  type HistogramData,
  type LineData,
} from "lightweight-charts";
import { chartTheme, colors } from "./chartTheme";
import type { StockBar } from "../../services/api/types";
import { cn } from "../../lib/utils";

function toTime(dateStr: string): UTCTimestamp {
  // "YYYY-MM-DD" → UTC seconds at midnight
  const t = Math.floor(new Date(`${dateStr}T00:00:00Z`).getTime() / 1000);
  return t as UTCTimestamp;
}

export interface PricePrediction {
  date: string;
  value: number;
}

export interface PriceChartProps {
  bars: StockBar[];
  predictions?: PricePrediction[];
  showVolume?: boolean;
  height?: number;
  className?: string;
}

export function PriceChart({
  bars,
  predictions,
  showVolume = true,
  height = 360,
  className,
}: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const predRef = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      ...chartTheme,
      autoSize: true,
      height,
    });

    const candle = chart.addSeries(CandlestickSeries, {
      upColor: colors.up,
      downColor: colors.down,
      borderUpColor: colors.up,
      borderDownColor: colors.down,
      wickUpColor: colors.up,
      wickDownColor: colors.down,
      priceLineVisible: false,
      lastValueVisible: true,
    });

    let vol: ISeriesApi<"Histogram"> | null = null;
    if (showVolume) {
      vol = chart.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "",
        color: colors.volume,
      });
      vol.priceScale().applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });
    }

    const pred = chart.addSeries(LineSeries, {
      color: colors.line,
      lineWidth: 2,
      lineStyle: 2, // dashed
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
    });

    chartRef.current = chart;
    candleRef.current = candle;
    volRef.current = vol;
    predRef.current = pred;

    return () => {
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volRef.current = null;
      predRef.current = null;
    };
  }, [height, showVolume]);

  useEffect(() => {
    if (!candleRef.current) return;
    if (!bars || bars.length === 0) {
      candleRef.current.setData([]);
      if (volRef.current) volRef.current.setData([]);
      return;
    }
    const sorted = [...bars].sort((a, b) =>
      a.date < b.date ? -1 : a.date > b.date ? 1 : 0
    );

    const cData: CandlestickData<UTCTimestamp>[] = sorted.map((b) => ({
      time: toTime(b.date),
      open: Number(b.open),
      high: Number(b.high),
      low: Number(b.low),
      close: Number(b.close),
    }));
    candleRef.current.setData(cData);

    if (volRef.current) {
      const vData: HistogramData<UTCTimestamp>[] = sorted.map((b) => ({
        time: toTime(b.date),
        value: Number(b.volume) || 0,
        color:
          b.close >= b.open
            ? "rgba(16,185,129,0.35)"
            : "rgba(244,63,94,0.35)",
      }));
      volRef.current.setData(vData);
    }

    chartRef.current?.timeScale().fitContent();
  }, [bars]);

  useEffect(() => {
    if (!predRef.current) return;
    if (!predictions || predictions.length === 0) {
      predRef.current.setData([]);
      return;
    }
    const sorted = [...predictions].sort((a, b) =>
      a.date < b.date ? -1 : a.date > b.date ? 1 : 0
    );
    const data: LineData<UTCTimestamp>[] = sorted.map((p) => ({
      time: toTime(p.date),
      value: Number(p.value),
    }));
    predRef.current.setData(data);
  }, [predictions]);

  return (
    <div
      ref={containerRef}
      className={cn("w-full", className)}
      style={{ height }}
    />
  );
}
