import type { BeforeAfterBlock } from "../../../services/api/portfolioTypes";
import type { Loc } from "../format";
import { fmtPct } from "../format";
import { BlockCard } from "./BlockCard";
import { deltaClass } from "./palette";

/**
 * Per-ticker before→after weight with a diverging Δ bar. Custom (not recharts)
 * so the paired before/after bars and the signed delta read cleanly at small
 * sizes inside a chat bubble.
 */
export function BeforeAfterChart({ block, loc = "en" }: { block: BeforeAfterBlock; loc?: Loc }) {
  const entries = block.data.entries ?? [];
  const maxW = Math.max(1, ...entries.flatMap((e) => [e.before_pct, e.after_pct]));

  return (
    <BlockCard title="Before → After">
      <div className="space-y-3">
        {entries.map((e) => (
          <div key={e.ticker} className="grid grid-cols-[64px_1fr_56px] items-center gap-2">
            <span dir="auto" className="num text-[12px] text-ink-2 truncate">{e.ticker}</span>
            <div className="space-y-1">
              <Bar pct={e.before_pct} max={maxW} tone="before" />
              <Bar pct={e.after_pct} max={maxW} tone="after" />
            </div>
            <span className={`num text-[12px] text-right font-medium ${deltaClass(e.delta_pct)}`}>
              {fmtPct(e.delta_pct, { loc, signed: true })}
            </span>
          </div>
        ))}
      </div>
      <div className="mt-3 flex items-center gap-3 text-[10.5px] text-stone-400 dark:text-[var(--ink-3)]">
        <span className="flex items-center gap-1"><i className="h-2 w-2 rounded-sm bg-stone-300 dark:bg-stone-600" /> before</span>
        <span className="flex items-center gap-1"><i className="h-2 w-2 rounded-sm bg-blue-500" /> after</span>
      </div>
    </BlockCard>
  );
}

function Bar({ pct, max, tone }: { pct: number; max: number; tone: "before" | "after" }) {
  const w = `${Math.max(2, (pct / max) * 100)}%`;
  return (
    <div className="h-2 rounded-full bg-stone-100 dark:bg-white/[0.04] overflow-hidden">
      <div
        className={tone === "after" ? "h-full bg-blue-500" : "h-full bg-stone-300 dark:bg-stone-600"}
        style={{ width: w }}
      />
    </div>
  );
}
