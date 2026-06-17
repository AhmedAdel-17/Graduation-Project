import type { HoldingsTableBlock } from "../../../services/api/portfolioTypes";
import type { Loc } from "../format";
import { fmtEgp, fmtInt, fmtPct } from "../format";
import { BlockCard } from "./BlockCard";

const SIGNAL_CLASS: Record<string, string> = {
  BUY: "text-emerald-600 dark:text-emerald-400",
  SELL: "text-rose-600 dark:text-rose-400",
  HOLD: "text-stone-400 dark:text-[var(--ink-3)]",
};

export function HoldingsTable({ block, loc = "en" }: { block: HoldingsTableBlock; loc?: Loc }) {
  const rows = block.rows ?? [];
  return (
    <BlockCard title="Holdings">
      <div className="overflow-x-auto">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-stone-400 dark:text-[var(--ink-3)] text-left">
              <th className="font-medium py-1.5 pr-2">Ticker</th>
              <th className="font-medium py-1.5 px-2 text-right">Shares</th>
              <th className="font-medium py-1.5 px-2 text-right">Price</th>
              <th className="font-medium py-1.5 px-2 text-right">Value</th>
              <th className="font-medium py-1.5 px-2 text-right">Weight</th>
              <th className="font-medium py-1.5 px-2 text-right">P&L</th>
              <th className="font-medium py-1.5 pl-2 text-right">Signal</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.ticker} className="border-t border-stone-100 dark:border-white/[0.05]">
                <td dir="auto" className="num py-1.5 pr-2 text-ink">{r.ticker}</td>
                <td className="num py-1.5 px-2 text-right text-ink-2">{fmtInt(r.shares, loc)}</td>
                <td className="num py-1.5 px-2 text-right text-ink-2">{fmtEgp(r.price, loc, 2)}</td>
                <td className="num py-1.5 px-2 text-right text-ink">{fmtEgp(r.market_value_egp, loc)}</td>
                <td className="num py-1.5 px-2 text-right text-ink-2">{fmtPct(r.weight_pct, { loc })}</td>
                <td className={"num py-1.5 px-2 text-right " + ((r.unrealized_pnl_egp ?? 0) >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400")}>
                  {r.unrealized_pnl_egp != null ? fmtEgp(r.unrealized_pnl_egp, loc) : "—"}
                </td>
                <td className={"num py-1.5 pl-2 text-right font-medium " + (r.signal_label ? SIGNAL_CLASS[r.signal_label] : "text-stone-300")}>
                  {r.signal_label ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </BlockCard>
  );
}
