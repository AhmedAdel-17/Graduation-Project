import { useEffect, useMemo, useState } from "react";
import { useAgentStream } from "../../../hooks/useAgentStream";
import { deriveExecution, type ExecutionSnapshot } from "./executionModel";

export type AnalystKey = "market" | "fundamentals" | "news" | "social";

export interface LiveRunResult {
  stream: ReturnType<typeof useAgentStream>;
  snapshot: ExecutionSnapshot;
  now: number;
  isLive: boolean;
  start: (params: { ticker: string; tradeDate: string; analysts: AnalystKey[] }) => void;
  cancel: () => void;
  /** Analysts of the run currently being tracked (drives expected-node set). */
  analysts: AnalystKey[];
}

// Wraps useAgentStream and layers timing/progress derivation on top. One
// instance = one live run. Shared by the Agent Monitor and Live Execution page.
export function useLiveRun(): LiveRunResult {
  const stream = useAgentStream();
  const [analysts, setAnalysts] = useState<AnalystKey[]>(["market", "fundamentals"]);
  const [now, setNow] = useState<number>(() => Date.now());

  const isLive = stream.status === "connecting" || stream.status === "streaming";

  // Tick once a second while live so live durations / ETA update smoothly.
  useEffect(() => {
    if (!isLive) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [isLive]);

  const snapshot = useMemo(
    () => deriveExecution(stream.events, analysts, isLive, now),
    [stream.events, analysts, isLive, now]
  );

  const start: LiveRunResult["start"] = ({ ticker, tradeDate, analysts: a }) => {
    if (!ticker || a.length === 0) return;
    setAnalysts(a);
    setNow(Date.now());
    stream.start({
      ticker,
      trade_date: tradeDate,
      selected_analysts: a,
      max_debate_rounds: 1,
      max_risk_rounds: 1,
    });
  };

  return { stream, snapshot, now, isLive, start, cancel: stream.cancel, analysts };
}
