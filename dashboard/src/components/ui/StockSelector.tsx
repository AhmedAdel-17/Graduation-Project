import React, { useMemo, useState, useRef, useEffect } from "react";
import { cn } from "../../lib/utils";
import { Search, ChevronDown, LineChart } from "lucide-react";
import { useTickers } from "../../hooks/useTickers";
import { Spinner } from "./Spinner";

export interface StockSelectorProps {
  value: string;
  onChange: (ticker: string) => void;
  className?: string;
  label?: string;
  placeholder?: string;
  disabled?: boolean;
}

export function StockSelector({
  value,
  onChange,
  className,
  label,
  placeholder = "Search EGX tickers…",
  disabled = false,
}: StockSelectorProps) {
  const { data: tickers = [], isLoading } = useTickers();
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
    function onDocClick(e: MouseEvent) {
      if (!rootRef.current) return;
      if (!rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const selected = tickers.find((t) => t.ticker === value);

  return (
    <div className={cn("flex flex-col gap-1.5", className)} ref={rootRef}>
      {label && (
        <label className="text-xs font-medium text-fg-muted">{label}</label>
      )}
      <div className="relative">
        <button
          type="button"
          onClick={() => !disabled && setOpen((v) => !v)}
          disabled={disabled}
          className={cn(
            "w-full flex items-center justify-between gap-2 rounded-lg bg-ink-800/80",
            "border border-line hover:border-line-strong transition-colors",
            "px-3 h-10 text-sm text-left",
            "disabled:opacity-50 disabled:cursor-not-allowed",
            open && "border-brand-500/50"
          )}
        >
          <span className="flex items-center gap-2 min-w-0">
            <LineChart className="h-4 w-4 text-fg-muted shrink-0" />
            <span className="font-mono tabular-nums text-fg truncate">
              {value || "Select ticker"}
            </span>
            {selected?.name && selected.name !== selected.ticker && (
              <span className="text-fg-muted text-xs truncate hidden sm:inline">
                — {selected.name}
              </span>
            )}
          </span>
          <ChevronDown
            className={cn(
              "h-4 w-4 text-fg-muted transition-transform",
              open && "rotate-180"
            )}
          />
        </button>

        {open && (
          <div className="absolute z-50 mt-1 w-full rounded-lg border border-line-strong bg-ink-800 shadow-lg animate-fade-in">
            <div className="flex items-center gap-2 px-3 h-10 border-b border-line">
              <Search className="h-4 w-4 text-fg-muted" />
              <input
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={placeholder}
                className="flex-1 bg-transparent focus:outline-none text-sm text-fg placeholder:text-fg-subtle"
              />
              {isLoading && <Spinner className="text-fg-muted" />}
            </div>

            <div className="max-h-72 overflow-y-auto py-1">
              {filtered.length === 0 ? (
                <div className="px-3 py-6 text-center text-xs text-fg-muted">
                  No tickers match "{query}"
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
                        "w-full flex items-center justify-between gap-2 px-3 py-2 text-sm text-left",
                        "hover:bg-ink-700/80 transition-colors",
                        active && "bg-brand-500/10"
                      )}
                    >
                      <span className="font-mono tabular-nums text-fg">
                        {t.ticker}
                      </span>
                      <span className="text-xs text-fg-muted truncate ml-2">
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
    </div>
  );
}
