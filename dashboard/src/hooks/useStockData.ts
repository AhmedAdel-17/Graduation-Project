import { useQuery } from "@tanstack/react-query";
import { endpoints } from "../services/api";
import type { StockBar, StockDataResponse } from "../services/api/types";

function extractBars(res: StockDataResponse | undefined): StockBar[] {
  if (!res) return [];
  const inner = res.data as StockDataResponse["data"];
  if (!inner) return [];
  if (Array.isArray(inner)) return inner as StockBar[];
  if (typeof inner === "string") return [];
  const arr = (inner as { data?: StockBar[] }).data;
  return Array.isArray(arr) ? arr : [];
}

export function useStockData(
  ticker: string | null | undefined,
  params?: { days?: number; start_date?: string; end_date?: string }
) {
  const enabled = !!ticker;
  return useQuery({
    queryKey: ["stock", ticker, params],
    enabled,
    queryFn: async () => {
      const res = await endpoints.stockData(ticker as string, params);
      return extractBars(res);
    },
  });
}
