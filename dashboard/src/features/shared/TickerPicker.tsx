import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Search } from "lucide-react";
import { cn } from "../../lib/utils";
import { useTickers } from "../../hooks/useTickers";

interface Props {
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  className?: string;
  label?: string;
}

export function TickerPicker({ value, onChange, disabled, className, label }: Props) {
  const { data: tickers = [] } = useTickers();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toUpperCase();
    if (!q) return tickers;
    return tickers.filter(
      (t) =>
        t.ticker.toUpperCase().includes(q) ||
        (t.name || "").toUpperCase().includes(q)
    );
  }, [tickers, query]);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const selected = tickers.find((t) => t.ticker === value);

  return (
    <div className={cn("relative", className)} ref={rootRef}>
      {label && <div className="eyebrow mb-2">{label}</div>}
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "w-full flex items-center justify-between gap-3 h-12 px-4 rounded-xl",
          "bg-white border border-stone-200 hover:border-stone-300 transition-colors",
          "text-left disabled:opacity-50 disabled:cursor-not-allowed",
          open && "border-stone-900 ring-4 ring-stone-900/5"
        )}
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className="h-8 w-8 rounded-lg bg-stone-100 flex items-center justify-center shrink-0">
            <span className="display text-[12px] font-semibold text-stone-700">
              {(selected?.ticker || value || "—").slice(0, 2)}
            </span>
          </div>
          <div className="min-w-0">
            <div className="mono text-[14px] font-semibold text-ink leading-tight">
              {value || "Select"}
            </div>
            {selected?.name && (
              <div className="text-[11px] text-stone-500 truncate leading-tight mt-0.5">
                {selected.name}
              </div>
            )}
          </div>
        </div>
        <ChevronDown
          className={cn(
            "h-4 w-4 text-stone-400 shrink-0 transition-transform",
            open && "rotate-180"
          )}
        />
      </button>

      {open && (
        <div className="absolute z-50 mt-2 w-full rounded-xl border border-stone-200 bg-white shadow-[0_12px_32px_-8px_rgba(15,15,15,0.18)] overflow-hidden anim-fade-up">
          <div className="flex items-center gap-2 px-3 h-11 border-b border-stone-100">
            <Search className="h-4 w-4 text-stone-400" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search EGX-30…"
              className="flex-1 bg-transparent focus:outline-none text-sm text-ink placeholder:text-stone-400"
            />
          </div>
          <div className="max-h-72 overflow-y-auto py-1">
            {filtered.length === 0 ? (
              <div className="px-4 py-8 text-center text-xs text-stone-500">
                No matches for "{query}"
              </div>
            ) : (
              filtered.map((t) => {
                const active = t.ticker === value;
                return (
                  <button
                    key={t.ticker}
                    type="button"
                    onClick={() => {
                      onChange(t.ticker);
                      setOpen(false);
                      setQuery("");
                    }}
                    className={cn(
                      "w-full flex items-center justify-between gap-2 px-4 py-2.5 text-left transition-colors",
                      active ? "bg-stone-50" : "hover:bg-stone-50/60"
                    )}
                  >
                    <span className="mono text-[13px] font-semibold text-ink">
                      {t.ticker}
                    </span>
                    <span className="text-[11px] text-stone-500 truncate ml-3">
                      {t.name}
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
