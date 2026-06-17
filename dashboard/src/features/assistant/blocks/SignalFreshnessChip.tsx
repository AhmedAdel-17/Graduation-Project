import { Clock } from "lucide-react";
import type { SignalFreshnessBlock, SignalView } from "../../../services/api/portfolioTypes";
import type { Loc } from "../format";
import { fmtNum, fmtPct } from "../format";
import { BlockCard } from "./BlockCard";

const LABEL_CLASS: Record<string, string> = {
  BUY: "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-300",
  SELL: "bg-rose-50 text-rose-700 dark:bg-rose-900/20 dark:text-rose-300",
  HOLD: "bg-stone-100 text-stone-600 dark:bg-white/[0.06] dark:text-[var(--ink-2)]",
};

export function SignalFreshnessChip({ block, loc = "en" }: { block: SignalFreshnessBlock; loc?: Loc }) {
  const signals = block.signals ?? [];
  return (
    <BlockCard title="Signals" icon={<Clock className="h-3.5 w-3.5" />}>
      <ul className="flex flex-wrap gap-2">
        {signals.map((sig) => (
          <Chip key={sig.ticker} sig={sig} loc={loc} />
        ))}
      </ul>
    </BlockCard>
  );
}

function Chip({ sig, loc }: { sig: SignalView; loc: Loc }) {
  const stale = sig.is_stale || sig.source === "quant_prior";
  return (
    <li className="inline-flex items-center gap-1.5 rounded-full border border-stone-200 dark:border-[var(--hairline)] pl-1 pr-2.5 py-0.5">
      <span className={`px-1.5 py-0.5 rounded-full text-[10.5px] font-semibold ${LABEL_CLASS[sig.label]}`}>
        {sig.label}
      </span>
      <span dir="auto" className="num text-[12px] text-ink">{sig.ticker}</span>
      <span className="num text-[11px] text-stone-400 dark:text-[var(--ink-3)]">
        {fmtPct(sig.confidence, { loc, frac: true, digits: 0 })}
      </span>
      <span
        className={
          "text-[10px] " +
          (stale ? "text-amber-600 dark:text-amber-400" : "text-stone-400 dark:text-[var(--ink-3)]")
        }
        title={sig.source === "quant_prior" ? "Degraded to a neutral quant prior" : undefined}
      >
        {stale ? "stale" : sig.age_days != null ? `${fmtNum(sig.age_days, loc, 0)}d` : "fresh"}
      </span>
    </li>
  );
}
