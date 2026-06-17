// Static topology of the EGX multi-agent pipeline, used by the Admin
// Monitoring Suite (Agent Architecture Monitor, Data Lineage, Agent Details).
//
// This is the AUTHORITATIVE frontend description of the graph. It mirrors the
// real LangGraph wiring documented in CLAUDE.md §2 and the live node names the
// backend streams over WS /api/analyze (see AgentTimeline.tsx:14-27 and
// server/api_server.py get_node_from_chunk). It is a description only — it does
// NOT drive any trading logic.
//
// `wsNode` binds a catalog node to the live status frame emitted during a run.
// `agentName` matches agent_events.agent_name for trace lookups (Phase 2).

export type AgentPhase =
  | "sources"
  | "data"
  | "analysts"
  | "research"
  | "execution"
  | "risk"
  | "decision"
  | "output";

export type NodeKind = "source" | "process" | "agent" | "output";

export interface CatalogNode {
  /** Unique id — also the React Flow node id. */
  id: string;
  label: string;
  kind: NodeKind;
  phase: AgentPhase;
  /** Live WS `node` name (when this node streams real-time status). */
  wsNode?: string;
  /** agent_events.agent_name for the LLM trace inspector. */
  agentName?: string;
  /** PROMPTS.md prompt id (null = deterministic, no LLM). */
  promptId?: string | null;
  description: string;
  responsibility: string;
  inputs: string[];
  outputs: string[];
}

export interface CatalogEdge {
  from: string;
  to: string;
  label?: string;
}

export const PHASE_ORDER: AgentPhase[] = [
  "sources",
  "data",
  "analysts",
  "research",
  "execution",
  "risk",
  "decision",
  "output",
];

export const PHASE_LABELS: Record<AgentPhase, string> = {
  sources: "Data Sources",
  data: "Data Layer",
  analysts: "Analyst Team",
  research: "Research Debate",
  execution: "Execution",
  risk: "Risk Management",
  decision: "Decision",
  output: "Recommendation",
};

export const CATALOG_NODES: CatalogNode[] = [
  // ── Data Sources ────────────────────────────────────────────────────────
  {
    id: "src-market",
    label: "Market Data",
    kind: "source",
    phase: "sources",
    description: "yfinance OHLCV + technical indicators (RSI/MACD/BB/SMA).",
    responsibility: "Primary price + volume feed for EGX .CA tickers.",
    inputs: ["ticker", "trade_date"],
    outputs: ["OHLCV bars", "indicators"],
  },
  {
    id: "src-fundamentals",
    label: "Fundamentals CSV",
    kind: "source",
    phase: "sources",
    description: "Local EGX financial-statement CSVs (multi-period).",
    responsibility: "Income / balance / cashflow statements per issuer.",
    inputs: ["ticker"],
    outputs: ["financial statements"],
  },
  {
    id: "src-news",
    label: "News Feeds",
    kind: "source",
    phase: "sources",
    description: "RSS / NewsAPI / Google News fallback chain.",
    responsibility: "Headline + article ingestion (AR + EN).",
    inputs: ["ticker", "trade_date"],
    outputs: ["news items"],
  },
  {
    id: "src-social",
    label: "Social Sources",
    kind: "source",
    phase: "sources",
    description: "Apify Facebook + Reddit + Telegram + Google News AR.",
    responsibility: "Retail + macro social signal (social_v2 pipeline).",
    inputs: ["ticker", "trade_date"],
    outputs: ["posts"],
  },

  // ── Data Layer ──────────────────────────────────────────────────────────
  {
    id: "data-gateway",
    label: "Data Gateway",
    kind: "process",
    phase: "data",
    description: "cache → primary → fallback vendor routing.",
    responsibility: "Normalizes symbols, validates schemas, serves analysts.",
    inputs: ["OHLCV bars", "financial statements"],
    outputs: ["validated market + fundamentals data"],
  },
  {
    id: "prefetch",
    label: "Data Prefetcher",
    kind: "process",
    phase: "data",
    description: "Parallel news + social pre-fetch (saves ~2 LLM calls/run).",
    responsibility: "Warms news/social caches before analysts run.",
    inputs: ["news items", "posts"],
    outputs: ["prefetched news", "prefetched social"],
  },
  {
    id: "sentiment",
    label: "Sentiment Engine",
    kind: "process",
    phase: "data",
    description: "FinBERT (EN) / CAMeLBERT-DA (AR dialect) / XLM-R router.",
    responsibility: "Bilingual sentiment scoring + index-level aggregation.",
    inputs: ["prefetched news", "prefetched social"],
    outputs: ["sentiment signals"],
  },

  // ── Analyst Team ────────────────────────────────────────────────────────
  {
    id: "market-analyst",
    label: "Market Analyst",
    kind: "agent",
    phase: "analysts",
    wsNode: "Market Analyst",
    agentName: "market_analyst",
    promptId: "P-MARKET",
    description: "Reads price action + indicators, emits a technical view.",
    responsibility: "Trend / momentum / support-resistance read.",
    inputs: ["validated market data"],
    outputs: ["market report"],
  },
  {
    id: "fundamentals-analyst",
    label: "Fundamentals Analyst",
    kind: "agent",
    phase: "analysts",
    wsNode: "Fundamentals Analyst",
    agentName: "fundamentals_analyst",
    promptId: "P-FUND",
    description: "3-stage CoT pipeline over EGX statements (Phase 1A/1B).",
    responsibility: "Ratio analysis, sector-aware health + valuation thesis.",
    inputs: ["validated fundamentals data"],
    outputs: ["fundamentals report"],
  },
  {
    id: "news-analyst",
    label: "News Analyst",
    kind: "agent",
    phase: "analysts",
    wsNode: "News Analyst",
    agentName: "news_analyst",
    promptId: "P-NEWS",
    description: "Synthesizes news flow into a catalyst/risk view.",
    responsibility: "Event + headline impact assessment.",
    inputs: ["prefetched news"],
    outputs: ["news report"],
  },
  {
    id: "social-analyst",
    label: "Social Analyst",
    kind: "agent",
    phase: "analysts",
    wsNode: "Social Analyst",
    agentName: "sentiment_analyst",
    promptId: "P-SOCIAL-PRE",
    description: "Index/sector-level social sentiment read (social_v2).",
    responsibility: "Retail mood + macro-event sentiment context.",
    inputs: ["sentiment signals", "prefetched social"],
    outputs: ["social report"],
  },

  // ── Analyst aggregation barrier ─────────────────────────────────────────
  {
    id: "analysts-sync",
    label: "Analyst Aggregation",
    kind: "process",
    phase: "analysts",
    description: "Fan-in barrier — waits for all selected analysts.",
    responsibility: "Collects analyst reports before the debate starts.",
    inputs: ["market report", "fundamentals report", "news report", "social report"],
    outputs: ["analyst bundle"],
  },

  // ── Research Debate ─────────────────────────────────────────────────────
  {
    id: "bull",
    label: "Bull Researcher",
    kind: "agent",
    phase: "research",
    wsNode: "Bull Researcher",
    agentName: "bull_researcher",
    promptId: "P-BULL",
    description: "Builds the bullish thesis (uses bull_memory).",
    responsibility: "Strongest evidence-backed long case.",
    inputs: ["analyst bundle"],
    outputs: ["bull thesis"],
  },
  {
    id: "bear",
    label: "Bear Researcher",
    kind: "agent",
    phase: "research",
    wsNode: "Bear Researcher",
    agentName: "bear_researcher",
    promptId: "P-BEAR",
    description: "Builds the bearish thesis (uses bear_memory).",
    responsibility: "Strongest evidence-backed short/avoid case.",
    inputs: ["analyst bundle", "bull thesis"],
    outputs: ["bear thesis"],
  },
  {
    id: "research-manager",
    label: "Research Manager",
    kind: "agent",
    phase: "research",
    wsNode: "Research Manager",
    agentName: "research_manager",
    promptId: "P-RESEARCH-MGR",
    description: "Judges the two-sided debate, emits an investment decision.",
    responsibility: "Verdict + conviction from bull vs bear.",
    inputs: ["bull thesis", "bear thesis"],
    outputs: ["investment decision"],
  },

  // ── Execution ───────────────────────────────────────────────────────────
  {
    id: "trader",
    label: "Trader",
    kind: "agent",
    phase: "execution",
    wsNode: "Trader",
    agentName: "trader",
    promptId: "P-TRADER",
    description: "Turns the decision into a liquidity-aware execution plan.",
    responsibility: "Position sizing, entries, stops, take-profit.",
    inputs: ["investment decision"],
    outputs: ["execution plan"],
  },

  // ── Risk Management ─────────────────────────────────────────────────────
  {
    id: "risky",
    label: "Risky Analyst",
    kind: "agent",
    phase: "risk",
    wsNode: "Risky Analyst",
    agentName: "risky_analyst",
    promptId: "P-RISK-AGGR",
    description: "Aggressive perspective in the risk debate.",
    responsibility: "Argues for higher conviction / size.",
    inputs: ["execution plan"],
    outputs: ["aggressive risk view"],
  },
  {
    id: "safe",
    label: "Safe Analyst",
    kind: "agent",
    phase: "risk",
    wsNode: "Safe Analyst",
    agentName: "safe_analyst",
    promptId: "P-RISK-CONS",
    description: "Conservative perspective in the risk debate.",
    responsibility: "Argues for caution / reduced exposure.",
    inputs: ["execution plan"],
    outputs: ["conservative risk view"],
  },
  {
    id: "neutral",
    label: "Neutral Analyst",
    kind: "agent",
    phase: "risk",
    wsNode: "Neutral Analyst",
    agentName: "neutral_analyst",
    promptId: "P-RISK-NEUT",
    description: "Balanced perspective in the risk debate.",
    responsibility: "Weighs aggressive vs conservative arguments.",
    inputs: ["execution plan"],
    outputs: ["neutral risk view"],
  },
  {
    id: "risk-judge",
    label: "Risk Judge",
    kind: "agent",
    phase: "risk",
    wsNode: "Risk Judge",
    agentName: "risk_manager",
    promptId: "P-RISK-MGR",
    description: "Deterministic VETO checks + LLM risk judgment.",
    responsibility: "Final approval/veto under EGX hard limits.",
    inputs: ["aggressive risk view", "conservative risk view", "neutral risk view"],
    outputs: ["final_trade_decision"],
  },

  // ── Decision ────────────────────────────────────────────────────────────
  {
    id: "signal",
    label: "Signal Processor",
    kind: "process",
    phase: "decision",
    description: "Regex extraction (no LLM) of the directional action.",
    responsibility: "Maps the verdict to BUY / SELL / HOLD.",
    inputs: ["final_trade_decision"],
    outputs: ["signal"],
  },

  // ── Recommendation ──────────────────────────────────────────────────────
  {
    id: "final",
    label: "Final Recommendation",
    kind: "output",
    phase: "output",
    description: "Human-reviewed BUY / HOLD / SELL thesis.",
    responsibility: "The deliverable surfaced to the PM/trader.",
    inputs: ["signal", "execution plan"],
    outputs: ["recommendation"],
  },
];

export const CATALOG_EDGES: CatalogEdge[] = [
  // sources → data layer
  { from: "src-market", to: "data-gateway" },
  { from: "src-fundamentals", to: "data-gateway" },
  { from: "src-news", to: "prefetch" },
  { from: "src-social", to: "prefetch" },
  { from: "prefetch", to: "sentiment" },
  // data layer → analysts
  { from: "data-gateway", to: "market-analyst" },
  { from: "data-gateway", to: "fundamentals-analyst" },
  { from: "prefetch", to: "news-analyst" },
  { from: "sentiment", to: "news-analyst" },
  { from: "sentiment", to: "social-analyst" },
  // analysts → aggregation
  { from: "market-analyst", to: "analysts-sync" },
  { from: "fundamentals-analyst", to: "analysts-sync" },
  { from: "news-analyst", to: "analysts-sync" },
  { from: "social-analyst", to: "analysts-sync" },
  // research debate (linearized per MEMORY.md §AA)
  { from: "analysts-sync", to: "bull" },
  { from: "bull", to: "bear" },
  { from: "bear", to: "research-manager" },
  // execution
  { from: "research-manager", to: "trader" },
  // risk debate
  { from: "trader", to: "risky" },
  { from: "trader", to: "safe" },
  { from: "trader", to: "neutral" },
  { from: "risky", to: "risk-judge" },
  { from: "safe", to: "risk-judge" },
  { from: "neutral", to: "risk-judge" },
  // decision → output
  { from: "risk-judge", to: "signal" },
  { from: "signal", to: "final" },
];

/** Catalog nodes that stream a live status frame, keyed by their WS node name. */
export const WS_NODE_TO_CATALOG: Record<string, string> = CATALOG_NODES.reduce(
  (acc, n) => {
    if (n.wsNode) acc[n.wsNode] = n.id;
    return acc;
  },
  {} as Record<string, string>
);

export function catalogNodeById(id: string): CatalogNode | undefined {
  return CATALOG_NODES.find((n) => n.id === id);
}

export function catalogNodeByAgentName(agentName: string): CatalogNode | undefined {
  return CATALOG_NODES.find((n) => n.agentName === agentName);
}

/** WS node names gated by analyst selection (skipped when not selected). */
export const ANALYST_WS_NODES: Record<string, string> = {
  market: "Market Analyst",
  fundamentals: "Fundamentals Analyst",
  news: "News Analyst",
  social: "Social Analyst",
};
