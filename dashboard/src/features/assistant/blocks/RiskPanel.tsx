import type { RiskPanelBlock, RiskPanelData } from "../../../services/api/portfolioTypes";
import type { Loc } from "../format";
import { fmtNum, fmtPct } from "../format";
import { BlockCard } from "./BlockCard";
import { deltaClass } from "./palette";

type Fmt = (v: number) => string;

export function RiskPanel({ block, loc = "en" }: { block: RiskPanelBlock; loc?: Loc }) {
  const d = block.data;
  const pct: Fmt = (v) => fmtPct(v, { loc, frac: true });
  const pctAbs: Fmt = (v) => fmtPct(v, { loc });
  const dec: Fmt = (v) => fmtNum(v, loc, 2);

  const rows: { label: string; before?: number | null; after?: number | null; fmt: Fmt; goodWhenNeg: boolean }[] = [
    { label: "Concentration (HHI)", before: d.hhi_before, after: d.hhi_after, fmt: dec, goodWhenNeg: true },
    { label: "Volatility (annual)", before: d.vol_before, after: d.vol_after, fmt: pct, goodWhenNeg: true },
    { label: "Beta vs EGX30", before: d.beta_before, after: d.beta_after, fmt: dec, goodWhenNeg: true },
    { label: "Max position", before: d.max_position_before, after: d.max_position_after, fmt: pctAbs, goodWhenNeg: true },
    { label: "Cash", before: d.cash_pct_before, after: d.cash_pct_after, fmt: pctAbs, goodWhenNeg: false },
  ];

  return (
    <BlockCard
      title="Risk"
      right={d.beta_is_proxy ? <span className="text-[10.5px] text-amber-600 dark:text-amber-400">proxy β</span> : undefined}
    >
      <div className="space-y-2">
        {rows.filter((r) => r.before != null || r.after != null).map((r) => (
          <Row key={r.label} {...r} />
        ))}
      </div>
    </BlockCard>
  );
}

function Row({ label, before, after, fmt, goodWhenNeg }: {
  label: string; before?: number | null; after?: number | null; fmt: Fmt; goodWhenNeg: boolean;
}) {
  const hasBoth = before != null && after != null;
  const delta = hasBoth ? (after as number) - (before as number) : null;
  return (
    <div className="grid grid-cols-[1fr_auto] items-center gap-2 text-[12.5px]">
      <span className="text-ink-2">{label}</span>
      <span className="num flex items-center gap-1.5">
        {before != null && <span className="text-stone-400 dark:text-[var(--ink-3)]">{fmt(before)}</span>}
        {hasBoth && <span className="text-stone-300 dark:text-[var(--ink-3)]">→</span>}
        {after != null && <span className="font-medium text-ink">{fmt(after)}</span>}
        {delta != null && delta !== 0 && (
          <span className={"text-[11px] " + deltaClass(delta, goodWhenNeg)}>
            ({fmt(Math.abs(delta))})
          </span>
        )}
      </span>
    </div>
  );
}

export type { RiskPanelData };
