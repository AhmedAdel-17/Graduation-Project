import type { ScenarioCompareBlock } from "../../../services/api/portfolioTypes";
import type { Loc } from "../format";
import { fmtDeltaPct, fmtNum } from "../format";
import { BlockCard } from "./BlockCard";
import { deltaClass } from "./palette";

// How to render + interpret each known metric delta. `goodWhenNeg` only drives
// color (down = de-risking for vol/hhi); cash/return are shown neutral-signed.
const METRICS: Record<string, { label: string; kind: "frac_pct" | "abs_pct" | "decimal"; goodWhenNeg: boolean }> = {
  vol: { label: "Volatility", kind: "frac_pct", goodWhenNeg: true },
  hhi: { label: "Concentration", kind: "decimal", goodWhenNeg: true },
  cash_pct: { label: "Cash", kind: "abs_pct", goodWhenNeg: false },
  expected_return_view: { label: "Return (model view)", kind: "frac_pct", goodWhenNeg: false },
};

export function ScenarioCompare({ block, loc = "en" }: { block: ScenarioCompareBlock; loc?: Loc }) {
  const d = block.data;
  const deltas = Object.entries(d.metric_deltas ?? {});

  return (
    <BlockCard
      title={`Δ vs ${d.reference ?? "baseline"}`}
      right={d.scenario_label ? <span className="text-[11px] text-amber-600 dark:text-amber-400">{d.scenario_label}</span> : undefined}
    >
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {deltas.map(([key, value]) => {
          const meta = METRICS[key] ?? { label: key, kind: "decimal" as const, goodWhenNeg: false };
          const text =
            meta.kind === "frac_pct" ? fmtDeltaPct(value, loc, true)
            : meta.kind === "abs_pct" ? fmtDeltaPct(value, loc, false)
            : `${value > 0 ? "+" : ""}${fmtNum(value, loc, 2)}`;
          return (
            <div key={key} className="rounded-lg border border-stone-200/70 dark:border-[var(--hairline)] px-2.5 py-2">
              <div className="text-[10.5px] text-stone-500 dark:text-[var(--ink-3)] truncate">{meta.label}</div>
              <div className={`num text-[14px] font-semibold ${deltaClass(value, meta.goodWhenNeg)}`}>{text}</div>
            </div>
          );
        })}
      </div>
      <p className="mt-2.5 text-[10.5px] text-stone-400 dark:text-[var(--ink-3)]">
        Deltas are measured against the pinned {d.reference ?? "baseline"} inputs — a hypothetical, not advice.
      </p>
    </BlockCard>
  );
}
