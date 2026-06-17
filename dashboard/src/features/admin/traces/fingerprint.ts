// Helpers for reading the model_fingerprint blob written by
// tradingagents/db/audit_writer.py (per-event: provider/model/temperature/seed;
// session-level: deep_think_llm/quick_think_llm/temperature/seed).

export interface ModelInfo {
  provider: string | null;
  model: string | null;
  temperature: string | null;
  seed: string | null;
}

function str(v: unknown): string | null {
  if (v === null || v === undefined) return null;
  return String(v);
}

export function extractModel(
  eventFp: Record<string, unknown> | null | undefined,
  sessionFp: Record<string, unknown> | null | undefined
): ModelInfo {
  const fp = eventFp ?? {};
  const sfp = sessionFp ?? {};
  const model =
    str(fp.model) ??
    str(fp.deep_think_llm) ??
    str(fp.quick_think_llm) ??
    str(sfp.deep_think_llm) ??
    str(sfp.quick_think_llm);
  return {
    provider: str(fp.provider) ?? str(sfp.provider),
    model,
    temperature: str(fp.temperature) ?? str(sfp.temperature),
    seed: str(fp.seed) ?? str(sfp.seed),
  };
}
