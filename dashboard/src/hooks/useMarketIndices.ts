import { useQuery } from "@tanstack/react-query";
import { api } from "../services/api/client";

export interface MarketIndex {
  key: string;
  label: string;
  kind: "index" | "commodity" | "fx" | string;
  value: number | null;
  change_pct: number | null;
  /** "intraday" = direct feed; "constituent_basket" = estimated proxy. */
  change_source?: string | null;
  available: boolean;
}

export interface MarketIndicesResponse {
  status: string;
  as_of_date: string;
  indices: MarketIndex[];
}

export function useMarketIndices() {
  return useQuery<MarketIndicesResponse>({
    queryKey: ["market-indices"],
    queryFn: () => api.get<MarketIndicesResponse>("/market/indices"),
    staleTime: 1000 * 60 * 5, // 5 minutes
    refetchOnWindowFocus: false,
  });
}
