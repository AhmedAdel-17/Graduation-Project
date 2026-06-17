// REST surface for the Portfolio Assistant (mirrors server/portfolio_routes.py).
// The conversational turn stream uses the WebSocket client in
// features/assistant/hooks/usePortfolioChat.ts; these are the CRUD + audit calls.

import { api } from "./client";
import type {
  InvestmentPolicy,
  OptimizationProposal,
  PortfolioAnalytics,
  PortfolioSnapshot,
  Scenario,
  ScenarioPatch,
  ServerEvent,
  WorkspaceDigest,
} from "./portfolioTypes";

const BASE = "/portfolio";

export interface ConversationSummary {
  id: string;
  user_id?: string | null;
  language?: string;
  title?: string | null;
  active_ref?: string;
  last_proposal_id?: number | null;
  archived?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface ConversationThread {
  conversation: ConversationSummary;
  messages: {
    id: number;
    role: "user" | "assistant" | "system";
    text_content?: string | null;
    blocks?: unknown;
    scenario_id?: number | null;
    created_at?: string;
  }[];
  digest: WorkspaceDigest | null;
}

export const portfolioApi = {
  createConversation: (language = "auto", title?: string) =>
    api.post<{ id: string }>(`${BASE}/conversations`, { language, title }),

  listConversations: () =>
    api.get<{ conversations: ConversationSummary[] }>(`${BASE}/conversations`),

  getConversation: (id: string) =>
    api.get<ConversationThread>(`${BASE}/conversations/${id}`),

  archiveConversation: (id: string) =>
    api.delete<{ status: string; id: string }>(`${BASE}/conversations/${id}`),

  confirmSnapshot: (id: string, snapshot: PortfolioSnapshot) =>
    api.post<{ events: ServerEvent[]; baseline: PortfolioSnapshot | null }>(
      `${BASE}/conversations/${id}/confirm`,
      snapshot
    ),

  patchPolicy: (id: string, updates: Record<string, unknown>, confirm = true) =>
    api.patch<{ policy: InvestmentPolicy }>(`${BASE}/conversations/${id}/policy`, {
      updates,
      confirm,
    }),

  listScenarios: (id: string) =>
    api.get<{ scenarios: Scenario[] }>(`${BASE}/conversations/${id}/scenarios`),

  createScenario: (id: string, patch: ScenarioPatch) =>
    api.post<{ events: ServerEvent[]; proposal_id: number | null }>(
      `${BASE}/conversations/${id}/scenarios`,
      patch
    ),

  promoteScenario: (scenarioId: number) =>
    api.post<{ events: ServerEvent[] }>(`${BASE}/scenarios/${scenarioId}/promote`),

  discardScenario: (scenarioId: number) =>
    api.delete<{ status: string; scenario_id: number }>(`${BASE}/scenarios/${scenarioId}`),

  getProposal: (proposalId: number) =>
    api.get<OptimizationProposal>(`${BASE}/proposals/${proposalId}`),

  getSnapshotAnalytics: (snapshotId: number) =>
    api.get<PortfolioAnalytics>(`${BASE}/snapshots/${snapshotId}/analytics`),
};

/** Resolve the WS base from VITE_API_BASE, matching liveFeedClient.ts. */
export function portfolioWsUrl(conversationId: string): string {
  const base = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";
  let wsBase: string;
  if (/^https?:/i.test(base)) wsBase = base.replace(/^http/i, "ws");
  else if (typeof window === "undefined") wsBase = `ws://localhost:8000${base}`;
  else {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    wsBase = `${proto}//${window.location.host}${base}`;
  }
  return `${wsBase}/portfolio/chat/${conversationId}`;
}
