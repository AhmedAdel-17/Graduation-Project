import { useQuery } from "@tanstack/react-query";
import { endpoints } from "../services/api";
import type { Ticker } from "../services/api/types";

const FALLBACK_TICKERS: Ticker[] = [
  "COMI", "EAST", "FWRY", "TMGH", "HRHO", "ETEL",
  "ABUK", "ADIB", "EFIH", "EGAL", "MFPC", "CCAP",
  "SKPC", "AMOC", "ESRS", "ORWE", "HELI", "GBCO",
  "SWDY", "ORAS", "PHDC", "CIEB", "ISPH", "DSCW",
  "RMDA", "ARCC", "BTFH", "JUFO", "ORHD", "RAYA", "VLMR",
].map((t) => ({ ticker: `${t}.CA`, name: t }));

export function useTickers() {
  return useQuery({
    queryKey: ["tickers"],
    queryFn: async () => {
      try {
        const res = await endpoints.tickers();
        if (res?.tickers?.length) return res.tickers;
      } catch {
        // fall through
      }
      return FALLBACK_TICKERS;
    },
    staleTime: 60 * 60 * 1000, // 1 hour
  });
}
