// Workspace digest (policy, scenario tree, active_ref) for the canvas pane.
// Live-only — pulls from GET /portfolio/conversations/{id}. `refresh` is called
// after a turn completes so the policy panel / scenario tabs stay in sync.

import { useCallback, useEffect, useState } from "react";
import { portfolioApi } from "../../../services/api/portfolioEndpoints";
import type { WorkspaceDigest } from "../../../services/api/portfolioTypes";

export function useWorkspace(conversationId?: string) {
  const [digest, setDigest] = useState<WorkspaceDigest | null>(null);
  const [loading, setLoading] = useState(false);

  // Manual refresh (e.g. after a turn). Setting state here is outside an effect.
  const refresh = useCallback(() => {
    if (!conversationId) return;
    setLoading(true);
    portfolioApi
      .getConversation(conversationId)
      .then((t) => setDigest(t.digest))
      .catch(() => setDigest(null))
      .finally(() => setLoading(false));
  }, [conversationId]);

  // Initial load on conversation change — state is only set in the async
  // callbacks (not synchronously in the effect body) to avoid cascading renders.
  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;
    portfolioApi
      .getConversation(conversationId)
      .then((t) => { if (!cancelled) setDigest(t.digest); })
      .catch(() => { if (!cancelled) setDigest(null); });
    return () => { cancelled = true; };
  }, [conversationId]);

  return { digest, loading, refresh };
}
