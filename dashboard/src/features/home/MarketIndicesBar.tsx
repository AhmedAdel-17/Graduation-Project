import { useRef, useEffect, useState } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  Coins,
  DollarSign,
  Droplets,
  Minus,
  Newspaper,
  type LucideIcon,
} from "lucide-react";
import {
  useMarketIndices,
  useHeadlines,
  type MarketIndex,
  type Headline,
} from "../../hooks/useMarketIndices";
import { cn, formatNumber, formatPercent } from "../../lib/utils";

/* ─── Icon map ──────────────────────────────────────────────────────── */

const ICONS: Record<string, LucideIcon> = {
  egx30: Activity,
  gold: Coins,
  usd_egp: DollarSign,
  brent: Droplets,
  eur_egp: DollarSign,
  silver: Coins,
  sp500: BarChart3,
};

/* ─── Ticker item types ─────────────────────────────────────────────── */

type TickerItem =
  | { type: "index"; data: MarketIndex }
  | { type: "headline"; data: Headline };

/* ─── Single index chip ─────────────────────────────────────────────── */

function IndexChip({ idx }: { idx: MarketIndex }) {
  const Icon = ICONS[idx.key] ?? Activity;
  const ch = idx.change_pct;
  const dir = ch == null ? "flat" : ch > 0 ? "up" : ch < 0 ? "down" : "flat";
  const Arrow =
    dir === "up" ? ArrowUpRight : dir === "down" ? ArrowDownRight : Minus;
  const changeClass =
    dir === "up"
      ? "text-emerald-600 dark:text-emerald-400"
      : dir === "down"
      ? "text-rose-600 dark:text-rose-400"
      : "text-stone-500";
  const decimals = idx.kind === "index" ? 0 : 2;

  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap">
      <Icon className="h-3.5 w-3.5 text-stone-400 dark:text-[var(--ink-3)]" aria-hidden />
      <span className="text-[12px] font-semibold text-[var(--ink-2)] uppercase tracking-wide">
        {idx.label}
      </span>
      <span className="display-num text-[13px] font-semibold text-ink">
        {idx.value != null ? formatNumber(idx.value, decimals) : "—"}
      </span>
      <span className={cn("inline-flex items-center gap-0.5 text-[11px] font-semibold mono", changeClass)}>
        <Arrow className="h-3 w-3" aria-hidden />
        {ch != null ? formatPercent(ch) : "—"}
      </span>
    </span>
  );
}

/* ─── Single headline chip ──────────────────────────────────────────── */

function HeadlineChip({ headline }: { headline: Headline }) {
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap max-w-[420px]">
      <Newspaper className="h-3.5 w-3.5 text-[var(--accent)] shrink-0" aria-hidden />
      <span className="text-[12px] text-[var(--ink-2)] truncate">
        {headline.title}
      </span>
    </span>
  );
}

/* ─── Dot separator ─────────────────────────────────────────────────── */

function Sep() {
  return (
    <span className="h-1 w-1 rounded-full bg-stone-300 dark:bg-[var(--ink-3)] shrink-0 mx-1" aria-hidden />
  );
}

/* ─── Main scrolling ticker ─────────────────────────────────────────── */

export function MarketIndicesBar({ className }: { className?: string }) {
  const { data: idxData, isLoading: idxLoading } = useMarketIndices();
  const { data: hdData } = useHeadlines();
  const trackRef = useRef<HTMLDivElement>(null);
  const [dur, setDur] = useState(40);

  // Build the interleaved item list: indices first, then headlines
  const indices = (idxData?.indices ?? []).filter((i) => i.available);
  const headlines = hdData?.headlines ?? [];

  const items: TickerItem[] = [];
  // Interleave: index, headline, index, headline, ...
  const maxLen = Math.max(indices.length, headlines.length);
  for (let i = 0; i < maxLen; i++) {
    if (i < indices.length) items.push({ type: "index", data: indices[i] });
    if (i < headlines.length) items.push({ type: "headline", data: headlines[i] });
  }
  // Append any remaining
  for (let i = maxLen; i < indices.length; i++) {
    items.push({ type: "index", data: indices[i] });
  }
  for (let i = maxLen; i < headlines.length; i++) {
    items.push({ type: "headline", data: headlines[i] });
  }

  // Measure content width to set animation duration proportionally
  useEffect(() => {
    if (!trackRef.current) return;
    const w = trackRef.current.scrollWidth / 2; // half because content is duplicated
    // ~60px/s scroll speed
    setDur(Math.max(20, Math.round(w / 60)));
  }, [items.length]);

  if (idxLoading) {
    return (
      <div
        className={cn(
          "card overflow-hidden h-10 flex items-center px-4",
          className,
        )}
      >
        <div className="h-2.5 w-48 skeleton rounded" />
      </div>
    );
  }

  if (items.length === 0) return null;

  return (
    <section
      aria-label="Market ticker"
      className={cn(
        "card overflow-hidden h-10 relative group",
        className,
      )}
    >
      {/* Fade edges */}
      <div className="absolute left-0 top-0 bottom-0 w-8 z-10 bg-gradient-to-r from-white dark:from-[var(--paper)] to-transparent pointer-events-none" />
      <div className="absolute right-0 top-0 bottom-0 w-8 z-10 bg-gradient-to-l from-white dark:from-[var(--paper)] to-transparent pointer-events-none" />

      {/* Scrolling track */}
      <div
        ref={trackRef}
        className="flex items-center h-full ticker-scroll group-hover:[animation-play-state:paused]"
        style={{ animationDuration: `${dur}s` }}
      >
        {/* Render items twice for seamless loop */}
        {[0, 1].map((copy) => (
          <div key={copy} className="flex items-center gap-4 px-4 shrink-0">
            {items.map((item, i) => (
              <span key={`${copy}-${i}`} className="inline-flex items-center gap-4">
                {i > 0 && <Sep />}
                {item.type === "index" ? (
                  <IndexChip idx={item.data} />
                ) : (
                  <HeadlineChip headline={item.data} />
                )}
              </span>
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}
