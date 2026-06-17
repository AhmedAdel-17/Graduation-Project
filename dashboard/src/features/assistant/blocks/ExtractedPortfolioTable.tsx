import { AlertTriangle, HelpCircle } from "lucide-react";
import type { ExtractedPortfolioTableBlock } from "../../../services/api/portfolioTypes";
import type { Loc } from "../format";
import { fmtEgp, fmtPct } from "../format";
import { BlockCard } from "./BlockCard";

/**
 * Read-only render of the extracted-portfolio table (the editable confirmation
 * card with [Confirm]/[Edit] lives in ExtractionConfirmCard.tsx — this is the
 * historical/thread render of the same payload).
 */
export function ExtractedPortfolioTable({ block, loc = "en" }: { block: ExtractedPortfolioTableBlock; loc?: Loc }) {
  const { holdings = [], cash_egp = 0, unresolved_names = [], warnings = [] } = block.data;
  return (
    <BlockCard title="Extracted portfolio">
      <table className="w-full text-[12px]">
        <thead>
          <tr className="text-stone-400 dark:text-[var(--ink-3)] text-left">
            <th className="font-medium py-1.5 pr-2">Holding</th>
            <th className="font-medium py-1.5 px-2 text-right">Shares</th>
            <th className="font-medium py-1.5 px-2 text-right">Avg cost</th>
            <th className="font-medium py-1.5 pl-2 text-right">Weight</th>
          </tr>
        </thead>
        <tbody>
          {holdings.map((h, i) => (
            <tr key={`${h.ticker}-${i}`} className="border-t border-stone-100 dark:border-white/[0.05]">
              <td className="py-1.5 pr-2">
                <span dir="auto" className="num text-ink">{h.ticker}</span>
                {h.name_raw && h.name_raw !== h.ticker && (
                  <span dir="auto" className="text-stone-400 dark:text-[var(--ink-3)] ml-1.5">{h.name_raw}</span>
                )}
              </td>
              <td className="num py-1.5 px-2 text-right text-ink-2">{h.shares != null ? h.shares : "—"}</td>
              <td className="num py-1.5 px-2 text-right text-ink-2">{h.avg_cost != null ? fmtEgp(h.avg_cost, loc, 2) : "—"}</td>
              <td className="num py-1.5 pl-2 text-right text-ink-2">{h.weight_pct != null ? fmtPct(h.weight_pct, { loc }) : "—"}</td>
            </tr>
          ))}
          <tr className="border-t border-stone-100 dark:border-white/[0.05]">
            <td className="py-1.5 pr-2 text-ink-2">Cash</td>
            <td colSpan={3} className="num py-1.5 pl-2 text-right text-ink">{fmtEgp(cash_egp, loc)}</td>
          </tr>
        </tbody>
      </table>

      {unresolved_names.length > 0 && (
        <div className="mt-2.5 flex items-start gap-1.5 text-[11.5px] text-amber-700 dark:text-amber-300">
          <HelpCircle className="h-3.5 w-3.5 mt-px shrink-0" />
          <span dir="auto">Couldn't match: {unresolved_names.join("، ")}</span>
        </div>
      )}
      {warnings.map((w, i) => (
        <div key={i} className="mt-1.5 flex items-start gap-1.5 text-[11.5px] text-stone-500 dark:text-[var(--ink-3)]">
          <AlertTriangle className="h-3.5 w-3.5 mt-px shrink-0" />
          <span dir="auto">{w}</span>
        </div>
      ))}
    </BlockCard>
  );
}
