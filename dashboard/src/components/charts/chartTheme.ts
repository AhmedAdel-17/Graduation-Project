import type { DeepPartial, ChartOptions } from "lightweight-charts";

export const chartTheme: DeepPartial<ChartOptions> = {
  layout: {
    background: { color: "transparent" },
    textColor: "#8b94a7",
    fontFamily:
      "Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
    fontSize: 11,
  },
  grid: {
    vertLines: { color: "rgba(255,255,255,0.03)" },
    horzLines: { color: "rgba(255,255,255,0.04)" },
  },
  rightPriceScale: {
    borderColor: "#1f2633",
  },
  timeScale: {
    borderColor: "#1f2633",
    secondsVisible: false,
    timeVisible: false,
  },
  crosshair: {
    vertLine: {
      color: "rgba(124,156,255,0.4)",
      width: 1,
      style: 0,
      labelBackgroundColor: "#262d3d",
    },
    horzLine: {
      color: "rgba(124,156,255,0.4)",
      width: 1,
      style: 0,
      labelBackgroundColor: "#262d3d",
    },
  },
};

// Light theme — warm-paper UI variant used by the Home screen price panel.
export const lightChartTheme: DeepPartial<ChartOptions> = {
  layout: {
    background: { color: "transparent" },
    textColor: "#78716C",
    fontFamily:
      "Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
    fontSize: 11,
  },
  grid: {
    vertLines: { color: "rgba(15,15,15,0.04)" },
    horzLines: { color: "rgba(15,15,15,0.055)" },
  },
  rightPriceScale: {
    borderColor: "#E7E5E4",
  },
  timeScale: {
    borderColor: "#E7E5E4",
    secondsVisible: false,
    timeVisible: false,
  },
  crosshair: {
    vertLine: {
      color: "rgba(120,113,108,0.45)",
      width: 1,
      style: 0,
      labelBackgroundColor: "#44403C",
    },
    horzLine: {
      color: "rgba(120,113,108,0.45)",
      width: 1,
      style: 0,
      labelBackgroundColor: "#44403C",
    },
  },
};

export const colors = {
  up: "#10b981",
  down: "#f43f5e",
  line: "#7c9cff",
  accent: "#34d399",
  volume: "rgba(124,156,255,0.4)",
};
