// Data-lineage model: every data track from raw source → agent consumption,
// following the real pipeline (CLAUDE.md §2 + social_v2 7-stage flow). Static
// description; per-run metrics are hydrated from a session's data_quality blob
// where available.

export const LINEAGE_STAGES = [
  "Source",
  "Collection",
  "Cleaning",
  "Transformation",
  "Enrichment",
  "Consumption",
] as const;

export type LineageStage = (typeof LINEAGE_STAGES)[number];

export type SourceType = "market" | "fundamentals" | "news" | "social";

export interface LineageTrack {
  id: SourceType;
  label: string;
  sourceType: string;
  /** stage label keyed by canonical stage. */
  stages: Record<LineageStage, string>;
  consumers: string[];
  /** keys in session.data_quality that, if present, quantify this track. */
  dataQualityKeys: string[];
}

export const LINEAGE_TRACKS: LineageTrack[] = [
  {
    id: "market",
    label: "Market data",
    sourceType: "yfinance OHLCV",
    stages: {
      Source: "yfinance API (.CA tickers)",
      Collection: "DataGateway: cache → primary → fallback",
      Cleaning: "Symbol normalize + Pydantic schema validate",
      Transformation: "OHLCV alignment to trade_date",
      Enrichment: "RSI / MACD / Bollinger / SMA",
      Consumption: "Market Analyst",
    },
    consumers: ["Market Analyst"],
    dataQualityKeys: ["market", "ohlcv", "price", "indicators", "market_bars"],
  },
  {
    id: "fundamentals",
    label: "Financial statements",
    sourceType: "EGX statement CSVs",
    stages: {
      Source: "Local EGX CSVs (multi-period)",
      Collection: "data_loader multi-period read",
      Cleaning: "statement_standardizer",
      Transformation: "Common-size + YoY / QoQ",
      Enrichment: "14 ratios + sector config + scoring",
      Consumption: "Fundamentals Analyst",
    },
    consumers: ["Fundamentals Analyst"],
    dataQualityKeys: ["fundamentals", "statements", "financials"],
  },
  {
    id: "news",
    label: "News articles",
    sourceType: "RSS / NewsAPI / Google News",
    stages: {
      Source: "RSS / NewsAPI / Google News (AR + EN)",
      Collection: "Data Prefetcher (parallel)",
      Cleaning: "Dedup + EGX relevance gate",
      Transformation: "Arabic normalization (hamza / diacritics)",
      Enrichment: "Sentiment (FinBERT / CAMeLBERT-DA / XLM-R)",
      Consumption: "News Analyst",
    },
    consumers: ["News Analyst"],
    dataQualityKeys: ["news", "articles", "headlines"],
  },
  {
    id: "social",
    label: "Social posts",
    sourceType: "Facebook / Reddit / Telegram / Google News AR",
    stages: {
      Source: "Apify FB · Reddit · Telegram · Google News AR",
      Collection: "social_v2 SCRAPE",
      Cleaning: "RELEVANCE (Layer-0) + QUALITY GATE",
      Transformation: "ENRICH: entity / sector / event / intent",
      Enrichment: "SENTIMENT + layered AGGREGATE (index/sector/ticker)",
      Consumption: "Social Analyst",
    },
    consumers: ["Social Analyst"],
    dataQualityKeys: ["social", "posts", "sentiment", "social_v2"],
  },
];

/** Pull any numeric/string hints for a track out of a generic data_quality blob. */
export function trackMetrics(
  track: LineageTrack,
  dataQuality: Record<string, unknown> | null | undefined
): { key: string; value: string }[] {
  if (!dataQuality) return [];
  const out: { key: string; value: string }[] = [];
  for (const [k, v] of Object.entries(dataQuality)) {
    const lk = k.toLowerCase();
    if (!track.dataQualityKeys.some((dk) => lk.includes(dk))) continue;
    if (v === null || v === undefined) continue;
    if (typeof v === "object") {
      out.push({ key: k, value: `${Object.keys(v as object).length} fields` });
    } else {
      out.push({ key: k, value: String(v) });
    }
  }
  return out.slice(0, 4);
}
