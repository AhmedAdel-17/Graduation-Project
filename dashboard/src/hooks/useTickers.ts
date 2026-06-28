import { useQuery } from "@tanstack/react-query";
import { endpoints } from "../services/api";
import type { Ticker } from "../services/api/types";

// EGX-30 universe — mirrors tradingagents/default_config.EGX_TICKERS. Used only when
// the /api/test/egx-tickers call fails; names are resolved from egxTickerMeta.ts.
const FALLBACK_TICKERS: Ticker[] = [
  "COMI", "ADIB",
  "TMGH", "HELI", "PHDC", "ORAS", "EMFD", "ORHD",
  "ABUK", "EAST", "EGAL", "EGCH", "ORWE", "AMOC",
  "MCQE", "ARCC", "ISPH", "RMDA", "GBCO",
  "ETEL", "FWRY", "EFIH", "RAYA", "OIH",
  "HRHO", "BTFH", "CCAP", "VLMR",
  "JUFO", "EFID",
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
