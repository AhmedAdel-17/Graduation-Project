import { useState } from "react";
import { Play, Square } from "lucide-react";
import { cn } from "../../../lib/utils";
import { useAppStore } from "../../../store/appStore";
import { getAllTickerMeta } from "../../../data/egx-tickers";
import type { AnalystKey } from "./useLiveRun";

const ALL_ANALYSTS: AnalystKey[] = ["market", "fundamentals", "news", "social"];
const TICKERS = getAllTickerMeta().map((t) => t.ticker);

function today(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function RunControls({
  isLive,
  onStart,
  onCancel,
}: {
  isLive: boolean;
  onStart: (p: { ticker: string; tradeDate: string; analysts: AnalystKey[] }) => void;
  onCancel: () => void;
}) {
  const { selectedTicker, setSelectedTicker } = useAppStore();
  const [tradeDate, setTradeDate] = useState<string>(today());
  const [analysts, setAnalysts] = useState<AnalystKey[]>(["market", "fundamentals"]);

  const toggle = (k: AnalystKey) =>
    setAnalysts((prev) =>
      prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]
    );

  const fieldCls =
    "h-9 rounded-md border border-stone-200 bg-white text-[13px] text-ink px-2.5 focus:outline-none focus:ring-2 focus:ring-blue-500/40 dark:bg-[var(--paper)] dark:border-[var(--hairline)] disabled:opacity-50";

  return (
    <div className="card p-4">
      <div className="flex flex-col lg:flex-row lg:items-end gap-3">
        <label className="flex flex-col gap-1.5 min-w-[140px]">
          <span className="text-[10.5px] uppercase tracking-wider text-stone-500 dark:text-[var(--ink-3)]">
            Ticker
          </span>
          <select
            value={selectedTicker}
            onChange={(e) => setSelectedTicker(e.target.value)}
            disabled={isLive}
            className={cn(fieldCls, "num")}
          >
            {TICKERS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-[10.5px] uppercase tracking-wider text-stone-500 dark:text-[var(--ink-3)]">
            Trade date
          </span>
          <input
            type="date"
            value={tradeDate}
            onChange={(e) => setTradeDate(e.target.value)}
            disabled={isLive}
            className={cn(fieldCls, "num")}
          />
        </label>

        <div className="flex flex-col gap-1.5 flex-1">
          <span className="text-[10.5px] uppercase tracking-wider text-stone-500 dark:text-[var(--ink-3)]">
            Analysts
          </span>
          <div className="flex flex-wrap gap-1.5">
            {ALL_ANALYSTS.map((k) => {
              const on = analysts.includes(k);
              return (
                <button
                  key={k}
                  type="button"
                  onClick={() => toggle(k)}
                  disabled={isLive}
                  aria-pressed={on}
                  className={cn(
                    "h-9 px-3 rounded-md text-[12px] border transition-colors capitalize disabled:opacity-50 disabled:cursor-not-allowed",
                    on
                      ? "bg-blue-50 text-blue-700 border-blue-200 dark:bg-sky-900/20 dark:text-sky-300 dark:border-sky-900/40"
                      : "bg-white text-stone-600 border-stone-200 hover:text-stone-900 dark:bg-[var(--paper)] dark:text-[var(--ink-2)] dark:border-[var(--hairline)]"
                  )}
                >
                  {k}
                </button>
              );
            })}
          </div>
        </div>

        <div className="flex items-end">
          {isLive ? (
            <button
              type="button"
              onClick={onCancel}
              className="inline-flex items-center gap-1.5 h-9 px-4 rounded-md text-[13px] font-medium border border-stone-200 text-stone-700 hover:bg-stone-100 dark:border-[var(--hairline)] dark:text-[var(--ink-2)] dark:hover:bg-white/5"
            >
              <Square className="h-3.5 w-3.5" aria-hidden /> Cancel
            </button>
          ) : (
            <button
              type="button"
              onClick={() => onStart({ ticker: selectedTicker, tradeDate, analysts })}
              disabled={analysts.length === 0}
              className="inline-flex items-center gap-1.5 h-9 px-4 rounded-md text-[13px] font-medium bg-stone-900 text-white hover:bg-stone-800 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-white dark:text-stone-900 dark:hover:bg-stone-100"
            >
              <Play className="h-3.5 w-3.5" aria-hidden /> Start run
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
