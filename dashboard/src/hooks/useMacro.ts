import { useQuery } from "@tanstack/react-query";
import { api } from "../services/api/client";

export interface MacroContext {
  cbe_policy_rate: number;
  usd_egp: number;
  usd_egp_1m_return: number | null;
  fx_trend: "stable" | "depreciating" | "appreciating" | "unknown";
  egx30_return_1m: number | null;
  egx30_trend: "bullish" | "bearish" | "neutral" | "unknown";
  tbill_yield_91d: number;
  egypt_cpi: number;
  real_rate: number;
  spread_vs_tbill: number;
  brent_usd: number | null;
  imf_program_active: boolean;
  imf_program_size_bn: number;
  as_of_date: string;
  data_sources: Record<string, string>;
}

export interface MacroResponse {
  status: string;
  macro_context: MacroContext;
}

export function useMacro() {
  return useQuery<MacroResponse>({
    queryKey: ["macro"],
    queryFn: () => api.get<MacroResponse>("/macro"),
    staleTime: 1000 * 60 * 15, // 15 minutes
    refetchOnWindowFocus: false,
  });
}
