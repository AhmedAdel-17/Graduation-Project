export type SignalKind = "BUY" | "SELL" | "HOLD" | "UNKNOWN";

export function decisionToSignal(decision: string | null | undefined): SignalKind {
  const u = (decision ?? "").toUpperCase();
  if (u.includes("BUY")) return "BUY";
  if (u.includes("SELL")) return "SELL";
  if (u.includes("HOLD")) return "HOLD";
  return "UNKNOWN";
}

export const SIGNAL_CLASS: Record<SignalKind, string> = {
  BUY: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-900/20 dark:text-emerald-300 dark:border-emerald-900/40",
  SELL: "bg-red-50 text-red-700 border-red-200 dark:bg-red-900/20 dark:text-red-300 dark:border-red-900/40",
  HOLD: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/20 dark:text-amber-300 dark:border-amber-900/40",
  UNKNOWN: "bg-stone-100 text-stone-500 border-stone-200 dark:bg-white/[0.04] dark:text-[var(--ink-3)] dark:border-[var(--hairline)]",
};
