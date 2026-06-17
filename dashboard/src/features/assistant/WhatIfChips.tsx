import { FlaskConical } from "lucide-react";
import type { ScenarioPatch } from "../../services/api/portfolioTypes";

/**
 * Suggested what-if chips under a proposal (design §10). They emit *structured*
 * ScenarioPatches — no interpreter LLM call — for discoverability of the copilot
 * behavior. Ticker-specific chips are derived from the current holdings.
 */
export function WhatIfChips({
  tickers,
  onWhatIf,
}: {
  tickers: string[];
  onWhatIf: (patch: ScenarioPatch) => void;
}) {
  const chips: { label: string; patch: ScenarioPatch }[] = [];

  for (const t of tickers.slice(0, 2)) {
    chips.push({
      label: `Sell all ${t}`,
      patch: { ops: [{ op: "CLOSE_POSITION", ticker: t }], reference: "baseline", label: `Sell all ${t}` },
    });
  }
  chips.push({
    label: "Add 50k cash",
    patch: { ops: [{ op: "ADD_CASH", amount_egp: 50000 }], reference: "active", label: "Add 50k cash" },
  });
  chips.push({
    label: "Reduce risk 20%",
    patch: { ops: [{ op: "TARGET_RISK_DELTA", vol_delta_pct: -20 }], reference: "active", label: "Reduce risk 20%" },
  });

  if (chips.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="inline-flex items-center gap-1 text-[11px] text-stone-400 dark:text-[var(--ink-3)]">
        <FlaskConical className="h-3 w-3" aria-hidden /> Try a what-if:
      </span>
      {chips.map((c) => (
        <button
          key={c.label}
          type="button"
          onClick={() => onWhatIf(c.patch)}
          className="px-2.5 py-1 rounded-full border border-dashed border-amber-300 text-[11.5px] text-amber-700 hover:bg-amber-50 dark:border-amber-700/50 dark:text-amber-300 dark:hover:bg-amber-900/20"
        >
          {c.label}
        </button>
      ))}
    </div>
  );
}
