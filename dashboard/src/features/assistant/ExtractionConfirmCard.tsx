import { useState } from "react";
import { Check, HelpCircle, Pencil } from "lucide-react";
import type {
  ExtractedPortfolioTableBlock,
  PortfolioHolding,
  PortfolioSnapshot,
} from "../../services/api/portfolioTypes";
import type { Loc } from "./format";
import { fmtEgp } from "./format";

interface Row {
  ticker: string;
  name_raw?: string | null;
  shares: string;
  avg_cost: string;
  weight_pct: string;
}

/**
 * The human-in-the-loop safety gate (design §10): the extracted portfolio table,
 * editable, with [Confirm]/[Edit]. On confirm it builds a PortfolioSnapshot and
 * hands it up — nothing reaches the optimizer until the user accepts this.
 */
export function ExtractionConfirmCard({
  block,
  loc = "en",
  onConfirm,
}: {
  block: ExtractedPortfolioTableBlock;
  loc?: Loc;
  onConfirm?: (snapshot: PortfolioSnapshot) => void;
}) {
  const init: Row[] = (block.data.holdings ?? []).map((h) => ({
    ticker: h.ticker,
    name_raw: h.name_raw,
    shares: h.shares != null ? String(h.shares) : "",
    avg_cost: h.avg_cost != null ? String(h.avg_cost) : "",
    weight_pct: h.weight_pct != null ? String(h.weight_pct) : "",
  }));
  const [rows, setRows] = useState<Row[]>(init);
  const [cash, setCash] = useState(String(block.data.cash_egp ?? 0));
  const [editing, setEditing] = useState(false);
  const [confirmed, setConfirmed] = useState(false);

  const num = (v: string): number | null => {
    const n = parseFloat(v.replace(/,/g, ""));
    return Number.isFinite(n) ? n : null;
  };

  const confirm = () => {
    const holdings: PortfolioHolding[] = rows.map((r) => ({
      ticker: r.ticker,
      shares: num(r.shares),
      avg_cost: num(r.avg_cost),
      weight_pct: num(r.weight_pct),
      source: "confirmed",
      name_raw: r.name_raw ?? null,
    }));
    setConfirmed(true);
    setEditing(false);
    onConfirm?.({
      cash_egp: num(cash) ?? 0,
      total_value_egp: block.data.total_value_egp ?? null,
      holdings,
      confirmed_by_user: true,
    });
  };

  const setCell = (i: number, key: keyof Row, v: string) =>
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, [key]: v } : r)));

  const cellCls = "num w-full bg-transparent text-right outline-none text-ink disabled:text-ink-2";

  return (
    <div className="rounded-xl border border-stone-200 bg-white dark:border-[var(--hairline)] dark:bg-[var(--paper)]">
      <div className="flex items-center gap-2 px-3.5 h-10 border-b border-stone-200/70 dark:border-[var(--hairline)]">
        <span className="eyebrow text-stone-500 dark:text-[var(--ink-3)]">Confirm your portfolio</span>
        {confirmed && (
          <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
            <Check className="h-3.5 w-3.5" /> Confirmed
          </span>
        )}
      </div>
      <div className="p-3.5">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-stone-400 dark:text-[var(--ink-3)] text-left">
              <th className="font-medium py-1.5 pr-2">Holding</th>
              <th className="font-medium py-1.5 px-2 text-right">Shares</th>
              <th className="font-medium py-1.5 px-2 text-right">Avg cost</th>
              <th className="font-medium py-1.5 pl-2 text-right">Weight %</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.ticker}-${i}`} className="border-t border-stone-100 dark:border-white/[0.05]">
                <td className="py-1.5 pr-2">
                  <span dir="auto" className="num text-ink">{r.ticker}</span>
                  {r.name_raw && r.name_raw !== r.ticker && (
                    <span dir="auto" className="text-stone-400 dark:text-[var(--ink-3)] ml-1.5">{r.name_raw}</span>
                  )}
                </td>
                <td className="py-1.5 px-2"><input disabled={!editing} className={cellCls} value={r.shares} onChange={(e) => setCell(i, "shares", e.target.value)} placeholder="—" /></td>
                <td className="py-1.5 px-2"><input disabled={!editing} className={cellCls} value={r.avg_cost} onChange={(e) => setCell(i, "avg_cost", e.target.value)} placeholder="—" /></td>
                <td className="py-1.5 pl-2"><input disabled={!editing} className={cellCls} value={r.weight_pct} onChange={(e) => setCell(i, "weight_pct", e.target.value)} placeholder="—" /></td>
              </tr>
            ))}
            <tr className="border-t border-stone-100 dark:border-white/[0.05]">
              <td className="py-1.5 pr-2 text-ink-2">Cash (EGP)</td>
              <td colSpan={3} className="py-1.5 pl-2">
                {editing ? (
                  <input className={cellCls} value={cash} onChange={(e) => setCash(e.target.value)} />
                ) : (
                  <div className="num text-right text-ink">{fmtEgp(num(cash) ?? 0, loc)}</div>
                )}
              </td>
            </tr>
          </tbody>
        </table>

        {(block.data.unresolved_names ?? []).length > 0 && (
          <div className="mt-2.5 flex items-start gap-1.5 text-[11.5px] text-amber-700 dark:text-amber-300">
            <HelpCircle className="h-3.5 w-3.5 mt-px shrink-0" />
            <span dir="auto">Couldn't match: {(block.data.unresolved_names ?? []).join("، ")}. Reply with the ticker to add it.</span>
          </div>
        )}

        {!confirmed && (
          <div className="mt-3 flex items-center gap-2">
            <button
              type="button"
              onClick={confirm}
              className="inline-flex items-center gap-1.5 px-3 h-8 rounded-lg bg-stone-900 text-white text-[12.5px] font-medium hover:bg-stone-800 dark:bg-white dark:text-stone-900 dark:hover:bg-stone-200"
            >
              <Check className="h-3.5 w-3.5" /> Confirm
            </button>
            <button
              type="button"
              onClick={() => setEditing((v) => !v)}
              className="inline-flex items-center gap-1.5 px-3 h-8 rounded-lg border border-stone-200 text-[12.5px] text-ink-2 hover:bg-stone-50 dark:border-[var(--hairline)] dark:hover:bg-white/5"
            >
              <Pencil className="h-3.5 w-3.5" /> {editing ? "Done editing" : "Edit"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
