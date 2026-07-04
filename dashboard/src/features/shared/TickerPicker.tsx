import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, Search } from "lucide-react";
import { cn } from "../../lib/utils";
import { useT } from "../../lib/i18n";
import { useTickers } from "../../hooks/useTickers";
import {
  EGX_SECTOR_ORDER,
  getTickerMeta,
  type EgxSector,
  type EgxTickerMeta,
} from "../../data/egxTickerMeta";
import { TickerLogo } from "../../components/ui/TickerLogo";

interface Props {
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  className?: string;
  label?: string;
}

export function TickerPicker({ value, onChange, disabled, className, label }: Props) {
  const tr = useT();
  const { data: tickers = [] } = useTickers();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  // Menu is portaled to <body> with fixed positioning so no ancestor's
  // overflow/stacking context can ever clip it.
  const [pos, setPos] = useState<{
    top: number;
    left: number;
    width: number;
    dropUp: boolean;
    maxH: number;
  } | null>(null);

  const updatePos = useCallback(() => {
    const el = buttonRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const gap = 8;
    // Always open downward. It's portaled with a high z-index, so it renders
    // over the sections below rather than being clipped by them; we just cap the
    // list height to the space beneath the trigger so it scrolls internally
    // instead of running off the bottom of the viewport.
    const spaceBelow = window.innerHeight - r.bottom - gap - 8;
    const maxH = Math.max(160, Math.min(420, spaceBelow - 56));
    setPos({
      top: r.bottom + gap,
      left: r.left,
      width: r.width,
      dropUp: false,
      maxH,
    });
  }, []);

  useEffect(() => {
    if (!open) return;
    updatePos();
    window.addEventListener("scroll", updatePos, true);
    window.addEventListener("resize", updatePos);
    return () => {
      window.removeEventListener("scroll", updatePos, true);
      window.removeEventListener("resize", updatePos);
    };
  }, [open, updatePos]);

  // Enrich the backend ticker list with English/Arabic names + sector.
  const enriched: EgxTickerMeta[] = useMemo(
    () => tickers.map((t) => getTickerMeta(t.ticker)),
    [tickers]
  );

  // Filter against symbol, English name, AND Arabic name.
  const filtered = useMemo(() => {
    const q = query.trim();
    if (!q) return enriched;
    const qLower = q.toLowerCase();
    return enriched.filter(
      (t) =>
        t.symbol.toLowerCase().includes(qLower) ||
        t.nameEn.toLowerCase().includes(qLower) ||
        t.nameAr.includes(q) // Arabic — case-insensitive doesn't apply
    );
  }, [enriched, query]);

  // Group filtered results by sector, preserving the canonical sector order.
  const grouped = useMemo(() => {
    const map = new Map<EgxSector, EgxTickerMeta[]>();
    for (const t of filtered) {
      const arr = map.get(t.sector) ?? [];
      arr.push(t);
      map.set(t.sector, arr);
    }
    return EGX_SECTOR_ORDER.map((sector) => ({
      sector,
      items: (map.get(sector) ?? []).sort((a, b) =>
        a.symbol.localeCompare(b.symbol)
      ),
    })).filter((g) => g.items.length > 0);
  }, [filtered]);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      const t = e.target as Node;
      if (rootRef.current?.contains(t)) return;
      if (menuRef.current?.contains(t)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const selected = enriched.find((t) => t.apiTicker === value);
  const selectedDisplay = selected ?? getTickerMeta(value);

  return (
    <div className={cn("relative", className)} ref={rootRef}>
      {label && <div className="eyebrow mb-2">{label}</div>}
      <button
        ref={buttonRef}
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "w-full flex items-center justify-between gap-3 h-12 px-4 rounded-xl",
          "bg-white border border-stone-200 hover:border-stone-300 transition-colors",
          "dark:bg-[var(--paper)] dark:border-[var(--hairline)] dark:hover:border-[var(--hairline-2)]",
          "text-left disabled:opacity-50 disabled:cursor-not-allowed",
          open && "border-stone-900 ring-4 ring-stone-900/5 dark:border-[var(--ink-2)] dark:ring-white/5"
        )}
      >
        <div className="flex items-center gap-3 min-w-0">
          <TickerLogo ticker={selectedDisplay.apiTicker} size="sm" />
          <div className="min-w-0">
            <div className="mono text-[14px] font-semibold text-ink leading-tight">
              {selectedDisplay.symbol || tr("ticker.select")}
            </div>
            {selectedDisplay.nameEn && (
              <div className="text-[11px] text-stone-500 dark:text-[var(--ink-3)] truncate leading-tight mt-0.5">
                {selectedDisplay.nameEn}
                {selectedDisplay.nameAr && selectedDisplay.nameAr !== selectedDisplay.symbol && (
                  <span className="ms-2" dir="rtl">· {selectedDisplay.nameAr}</span>
                )}
              </div>
            )}
          </div>
        </div>
        <ChevronDown
          className={cn(
            "h-4 w-4 text-stone-400 dark:text-[var(--ink-3)] shrink-0 transition-transform",
            open && "rotate-180"
          )}
        />
      </button>

      {open && pos && createPortal(
        <div
          ref={menuRef}
          style={{
            position: "fixed",
            top: pos.dropUp ? undefined : pos.top,
            bottom: pos.dropUp ? window.innerHeight - pos.top : undefined,
            left: pos.left,
            width: pos.width,
            zIndex: 9999,
          }}
          className="rounded-xl border border-stone-200 bg-white shadow-[0_12px_32px_-8px_rgba(15,15,15,0.18)] overflow-hidden anim-fade-up
          dark:bg-[var(--paper)] dark:border-[var(--hairline)] dark:shadow-[0_12px_40px_-12px_rgba(0,0,0,0.6)]">
          <div className="flex items-center gap-2 px-3 h-11 border-b border-stone-100 dark:border-[var(--hairline)]">
            <Search className="h-4 w-4 text-stone-400 dark:text-[var(--ink-3)]" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={tr("ticker.search")}
              className="flex-1 bg-transparent focus:outline-none text-sm text-ink placeholder:text-stone-400 dark:placeholder:text-[var(--ink-3)]"
            />
          </div>
          <div className="overflow-y-auto py-1" style={{ maxHeight: pos.maxH }}>
            {grouped.length === 0 ? (
              <div className="px-4 py-8 text-center text-xs text-stone-500 dark:text-[var(--ink-3)]">
                {tr("ticker.noMatches", { query })}
              </div>
            ) : (
              grouped.map((group) => (
                <div key={group.sector}>
                  <div className="px-4 pt-3 pb-1 text-[10px] uppercase tracking-wide text-stone-400 font-semibold sticky top-0
                    bg-white dark:bg-[var(--paper)] dark:text-[var(--ink-3)]">
                    {group.sector}
                  </div>
                  {group.items.map((t) => {
                    const active = t.apiTicker === value;
                    return (
                      <button
                        key={t.apiTicker}
                        type="button"
                        onClick={() => {
                          onChange(t.apiTicker);
                          setOpen(false);
                          setQuery("");
                        }}
                        className={cn(
                          "w-full flex items-center gap-3 px-4 py-2 text-left transition-colors",
                          active
                            ? "bg-stone-50 dark:bg-white/5"
                            : "hover:bg-stone-50/60 dark:hover:bg-white/5"
                        )}
                      >
                        <TickerLogo ticker={t.apiTicker} size="sm" />
                        <span className="mono text-[13px] font-semibold text-ink w-14 shrink-0">
                          {t.symbol}
                        </span>
                        <span className="text-[12px] text-stone-600 dark:text-[var(--ink-2)] truncate flex-1">
                          {t.nameEn}
                        </span>
                        <span
                          className="text-[12px] text-stone-500 dark:text-[var(--ink-3)] truncate text-right shrink-0 max-w-[40%]"
                          dir="rtl"
                        >
                          {t.nameAr}
                        </span>
                      </button>
                    );
                  })}
                </div>
              ))
            )}
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
