import { useState } from "react";
import { cn } from "../../lib/utils";
import { getTickerMeta } from "../../data/egxTickerMeta";

interface Props {
  // Either an apiTicker (`COMI.CA`) or a bare symbol (`COMI`).
  ticker: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}

const SIZE_CLS: Record<NonNullable<Props["size"]>, string> = {
  sm: "h-8 w-8 text-[11px]",
  md: "h-12 w-12 text-[13px]",
  lg: "h-14 w-14 text-[15px]",
};

// Below this decoded pixel width a favicon is almost always a blank/placeholder
// tile (16px DuckDuckGo default), so we skip it and try the next source.
const MIN_USABLE_PX = 20;

// Free favicon/logo sources, tried in order. Each successive source is a
// fallback if the previous one 404s or returns a too-small tile. Branded
// initials underneath are the guaranteed-clean final state.
function logoSources(domain: string): string[] {
  const root = domain.replace(/^www\./, "");
  return [
    // unavatar aggregates Twitter avatars + favicons — biggest, cleanest icons.
    `https://unavatar.io/${root}?fallback=false`,
    // DuckDuckGo — good coverage; small/blank tiles are filtered by MIN_USABLE_PX.
    `https://icons.duckduckgo.com/ip3/${root}.ico`,
  ];
  // NB: Google's s2/favicons is intentionally omitted — for domains without a
  // real favicon it returns a generic globe, which reads worse than the clean
  // branded initials we fall back to.
}

export function TickerLogo({ ticker, size = "md", className }: Props) {
  const meta = getTickerMeta(ticker.includes(".") ? ticker : `${ticker}.CA`);
  const sources = meta.logoDomain ? logoSources(meta.logoDomain) : [];
  const [idx, setIdx] = useState(0);
  const [loaded, setLoaded] = useState(false);

  const sizeCls = SIZE_CLS[size];
  const src = idx < sources.length ? sources[idx] : null;

  return (
    <div
      className={cn(
        "relative rounded-xl flex items-center justify-center overflow-hidden shrink-0",
        "bg-[var(--brand-green-soft)] border border-stone-200",
        "dark:bg-[var(--hairline)] dark:border-[var(--hairline-2)]",
        sizeCls,
        className
      )}
      title={meta.nameEn}
    >
      {/* Base layer: branded initials — always present, so a chip never renders
          blank even when every logo source fails. */}
      <span className="display font-semibold text-[var(--brand-navy)] select-none leading-none">
        {meta.symbol.slice(0, 4)}
      </span>

      {/* Overlay: the real logo, revealed only once a source loads at a usable
          size. A too-small/blank tile advances to the next source instead. */}
      {src && (
        <img
          key={src}
          src={src}
          alt=""
          aria-hidden
          width={64}
          height={64}
          loading="lazy"
          onError={() => {
            setLoaded(false);
            setIdx((i) => i + 1);
          }}
          onLoad={(e) => {
            const img = e.currentTarget;
            if (Math.min(img.naturalWidth, img.naturalHeight) >= MIN_USABLE_PX) {
              setLoaded(true);
            } else {
              setLoaded(false);
              setIdx((i) => i + 1);
            }
          }}
          referrerPolicy="no-referrer"
          className={cn(
            "absolute inset-0 h-full w-full object-contain p-1.5 bg-white dark:bg-[var(--paper)] transition-opacity duration-200",
            loaded ? "opacity-100" : "opacity-0"
          )}
        />
      )}
    </div>
  );
}
