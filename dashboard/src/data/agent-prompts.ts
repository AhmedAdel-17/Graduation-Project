// Maps the agent_name written by tradingagents.db.audit_writer into the
// matching prompt ID + version recorded in PROMPTS.md. The audit-writer
// emits 13 distinct agent_name values plus the post-graph RL meta-policy
// row. Keep this table in sync when PROMPTS.md versions bump.
//
// `phase` is the dashboard grouping; the order of insertion is also the
// canonical display order on the Reasoning tab.

export type AgentPhase =
  | "analysts"
  | "research"
  | "execution"
  | "risk"
  | "final"
  | "meta";

export interface AgentPromptMeta {
  agentName: string;       // matches agent_events.agent_name
  labelKey: string;        // i18n key for the human-readable label
  phase: AgentPhase;
  promptId: string | null; // e.g. "P-MARKET" — null when the agent is deterministic
  promptVersion: string | null;
  notes?: string;
}

export const AGENT_PROMPTS: AgentPromptMeta[] = [
  // Analysts — parallel fan-out
  {
    agentName: "market_analyst",
    labelKey: "agent.market",
    phase: "analysts",
    promptId: "P-MARKET",
    promptVersion: "v1",
  },
  {
    agentName: "sentiment_analyst",
    labelKey: "agent.sentiment",
    phase: "analysts",
    promptId: "P-SOCIAL-PRE",
    promptVersion: "v1",
    notes: "uses prefetched social pipeline output",
  },
  {
    agentName: "news_analyst",
    labelKey: "agent.news",
    phase: "analysts",
    promptId: "P-NEWS",
    promptVersion: "v1",
  },
  {
    agentName: "fundamentals_analyst",
    labelKey: "agent.fundamentals",
    phase: "analysts",
    promptId: "P-FUND-COT-3",
    promptVersion: "v2",
    notes: "deterministic Phase 1A/1B; LLM path falls back to P-FUND-COT-3",
  },
  // Research debate
  {
    agentName: "bull_researcher",
    labelKey: "agent.bull",
    phase: "research",
    promptId: "P-BULL",
    promptVersion: "v1",
  },
  {
    agentName: "bear_researcher",
    labelKey: "agent.bear",
    phase: "research",
    promptId: "P-BEAR",
    promptVersion: "v1",
  },
  {
    agentName: "research_manager",
    labelKey: "agent.research_manager",
    phase: "research",
    promptId: "P-RESMGR",
    promptVersion: "v2",
  },
  // Execution
  {
    agentName: "trader",
    labelKey: "agent.trader",
    phase: "execution",
    promptId: "P-TRADER-SYS",
    promptVersion: "v1",
  },
  // Risk debate (merged single call writes three sub-rows)
  {
    agentName: "risk_aggressive",
    labelKey: "agent.risk_aggressive",
    phase: "risk",
    promptId: "P-RISK-MERGED",
    promptVersion: "v1",
    notes: "Risky perspective inside merged risk debate",
  },
  {
    agentName: "risk_conservative",
    labelKey: "agent.risk_conservative",
    phase: "risk",
    promptId: "P-RISK-MERGED",
    promptVersion: "v1",
    notes: "Safe perspective inside merged risk debate",
  },
  {
    agentName: "risk_neutral",
    labelKey: "agent.risk_neutral",
    phase: "risk",
    promptId: "P-RISK-MERGED",
    promptVersion: "v1",
    notes: "Neutral perspective inside merged risk debate",
  },
  {
    agentName: "risk_manager",
    labelKey: "agent.risk_manager",
    phase: "risk",
    promptId: "P-RISKMGR",
    promptVersion: "v3",
  },
  // Final signal extraction + post-graph RL adjustment
  {
    agentName: "final",
    labelKey: "agent.final",
    phase: "final",
    promptId: null,
    promptVersion: null,
    notes: "regex extractor only — no LLM",
  },
  {
    agentName: "rl_meta_policy",
    labelKey: "agent.rl_meta",
    phase: "meta",
    promptId: null,
    promptVersion: null,
    notes: "offline CQL Q-network — feature_version rl_state_v1",
  },
];

const BY_NAME: Record<string, AgentPromptMeta> = Object.fromEntries(
  AGENT_PROMPTS.map((m) => [m.agentName, m])
);

export function getAgentPromptMeta(agentName: string | null | undefined): AgentPromptMeta | null {
  if (!agentName) return null;
  return BY_NAME[agentName] ?? null;
}

export function phaseLabelKey(phase: AgentPhase): string {
  return `agent.phase.${phase}`;
}

// Display index for ordering agents that come back from the trace in
// arbitrary order. Lower = earlier in the graph.
export function agentOrderIndex(agentName: string): number {
  const idx = AGENT_PROMPTS.findIndex((m) => m.agentName === agentName);
  return idx === -1 ? AGENT_PROMPTS.length : idx;
}
