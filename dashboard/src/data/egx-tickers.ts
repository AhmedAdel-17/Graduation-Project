// Static EGX-30 metadata derived from default_config.EGX_TICKERS and
// tradingagents/sentiment/liquidity_tiers.py. Update when the universe
// expands (MEMORY.md §D). Sector keys mirror sentiment.taxonomy.SectorEnum.

export type Sector =
  | "banks"
  | "real_estate"
  | "industry"
  | "telecom_tech"
  | "financial_services"
  | "food_bev";

export type LiquidityTier = "MEGA" | "MID" | "SMALL";

export interface TickerMeta {
  ticker: string;
  sector: Sector;
  liquidity: LiquidityTier;
}

const META: Record<string, Omit<TickerMeta, "ticker">> = {
  // Banks
  "COMI.CA": { sector: "banks", liquidity: "MEGA" },
  "ADIB.CA": { sector: "banks", liquidity: "MID" },
  "CIEB.CA": { sector: "banks", liquidity: "MID" },
  "EXPA.CA": { sector: "banks", liquidity: "MID" },
  "HDBK.CA": { sector: "banks", liquidity: "MID" },
  "QNBA.CA": { sector: "banks", liquidity: "MID" },
  "SAUD.CA": { sector: "banks", liquidity: "MID" },
  // Real Estate
  "TMGH.CA": { sector: "real_estate", liquidity: "MEGA" },
  "HELI.CA": { sector: "real_estate", liquidity: "MID" },
  "PHDC.CA": { sector: "real_estate", liquidity: "MID" },
  "OCDI.CA": { sector: "real_estate", liquidity: "MID" },
  "ORAS.CA": { sector: "real_estate", liquidity: "MID" },
  "EMFD.CA": { sector: "real_estate", liquidity: "SMALL" },
  // Industry
  "EAST.CA": { sector: "industry", liquidity: "MID" },
  "ESRS.CA": { sector: "industry", liquidity: "MID" },
  "SWDY.CA": { sector: "industry", liquidity: "MID" },
  "ABUK.CA": { sector: "industry", liquidity: "MID" },
  "MFPC.CA": { sector: "industry", liquidity: "SMALL" },
  "EGAL.CA": { sector: "industry", liquidity: "SMALL" },
  "EGCH.CA": { sector: "industry", liquidity: "SMALL" },
  "EFIC.CA": { sector: "industry", liquidity: "SMALL" },
  // Telecom / Tech
  "ETEL.CA": { sector: "telecom_tech", liquidity: "MEGA" },
  "FWRY.CA": { sector: "telecom_tech", liquidity: "MID" },
  "EFIH.CA": { sector: "telecom_tech", liquidity: "MID" },
  "RAYA.CA": { sector: "telecom_tech", liquidity: "SMALL" },
  // Financial Services
  "HRHO.CA": { sector: "financial_services", liquidity: "MID" },
  "BTFH.CA": { sector: "financial_services", liquidity: "SMALL" },
  "CICH.CA": { sector: "financial_services", liquidity: "SMALL" },
  // Food & Bev
  "JUFO.CA": { sector: "food_bev", liquidity: "MID" },
  "EFID.CA": { sector: "food_bev", liquidity: "SMALL" },
  "DOMT.CA": { sector: "food_bev", liquidity: "MID" },
};

const SECTOR_LABEL_KEYS: Record<Sector, string> = {
  banks: "sector.banks",
  real_estate: "sector.real_estate",
  industry: "sector.industry",
  telecom_tech: "sector.telecom_tech",
  financial_services: "sector.financial_services",
  food_bev: "sector.food_bev",
};

export function getTickerMeta(ticker: string): TickerMeta | null {
  const m = META[ticker];
  if (!m) return null;
  return { ticker, ...m };
}

export function sectorLabelKey(sector: Sector): string {
  return SECTOR_LABEL_KEYS[sector];
}

// Canonical order — banks first, then real estate, industry, telecom, etc.
// Used by the Universe browser to render sections deterministically and
// drive the multi-select sector filter.
export const SECTOR_ORDER: Sector[] = [
  "banks",
  "real_estate",
  "industry",
  "telecom_tech",
  "financial_services",
  "food_bev",
];

export const LIQUIDITY_ORDER: LiquidityTier[] = ["MEGA", "MID", "SMALL"];

// All ticker metadata, sorted by sector then ticker. Stable order so the
// UniversePage doesn't shuffle rows between renders.
export function getAllTickerMeta(): TickerMeta[] {
  const out: TickerMeta[] = Object.entries(META).map(([ticker, m]) => ({
    ticker,
    ...m,
  }));
  out.sort((a, b) => {
    const sa = SECTOR_ORDER.indexOf(a.sector);
    const sb = SECTOR_ORDER.indexOf(b.sector);
    if (sa !== sb) return sa - sb;
    return a.ticker.localeCompare(b.ticker);
  });
  return out;
}
