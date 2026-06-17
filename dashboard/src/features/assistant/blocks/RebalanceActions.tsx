import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import type { RebalanceActionsBlock } from "../../../services/api/portfolioTypes";
import type { Loc } from "../format";
import { fmtEgp, fmtInt, fmtPct } from "../format";
import { BlockCard } from "./BlockCard";

export function RebalanceActions({ block, loc = "en" }: { block: RebalanceActionsBlock; loc?: Loc }) {
  const actions = block.data.actions ?? [];

  return (
    <BlockCard
      title="Rebalancing proposal"
      right={
        <span className="text-[11px] text-stone-400 dark:text-[var(--ink-3)]">
          cost ≈ {fmtEgp(block.data.est_total_cost_egp ?? 0, loc)} · turnover {fmtPct(block.data.est_turnover_pct ?? 0, { loc })}
        </span>
      }
    >
      {actions.length === 0 ? (
        <p className="text-[12.5px] text-stone-500 dark:text-[var(--ink-3)] py-2">
          No trades — the portfolio is already aligned with the policy.
        </p>
      ) : (
        <ul className="space-y-2">
          {actions.map((a, i) => {
            const isBuy = a.side === "BUY";
            return (
              <li
                key={`${a.ticker}-${i}`}
                className="flex items-center gap-3 rounded-lg border border-stone-200/80 dark:border-[var(--hairline)] px-3 py-2"
              >
                <span
                  className={
                    "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-semibold " +
                    (isBuy
                      ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-300"
                      : "bg-rose-50 text-rose-700 dark:bg-rose-900/20 dark:text-rose-300")
                  }
                >
                  {isBuy ? <ArrowUpRight className="h-3 w-3" /> : <ArrowDownRight className="h-3 w-3" />}
                  {a.side}
                </span>
                <div className="min-w-0">
                  <div className="num text-[13px] font-medium text-ink">
                    {fmtInt(a.shares, loc)} <span dir="auto">{a.ticker}</span>
                    <span className="text-stone-400 dark:text-[var(--ink-3)] font-normal"> @ {fmtEgp(a.price_used, loc, 2)}</span>
                  </div>
                  {a.rationale && (
                    <div dir="auto" className="text-[11.5px] text-stone-500 dark:text-[var(--ink-3)] leading-snug line-clamp-2">{a.rationale}</div>
                  )}
                </div>
                <div className="ml-auto text-right shrink-0">
                  <div className="num text-[12.5px] text-ink">≈ {fmtEgp(a.est_value_egp, loc)}</div>
                  <div className="num text-[11px] text-stone-400 dark:text-[var(--ink-3)]">
                    {fmtPct(a.current_weight_pct, { loc })} → {fmtPct(a.target_weight_pct, { loc })}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <p className="mt-2.5 text-[10.5px] text-stone-400 dark:text-[var(--ink-3)]">
        A proposal for review — not an order. Quantities are integer shares at the priced level.
      </p>
    </BlockCard>
  );
}
