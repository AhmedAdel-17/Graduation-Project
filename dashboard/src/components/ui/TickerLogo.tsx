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

// Free favicon/logo sources, tried in order. Each successive source is a
// fallback if the previous one 404s — covers ~90% of EGX-listed companies.
// Initials box is the guaranteed-clean final fallback.
function logoSources(domain: string): string[] {
  // Strip subdomain so `www.foo.com` and `foo.com` both resolve.
  const root = domain.replace(/^www\./, "");
  return [
    // unavatar aggregates Twitter avatars + favicons + Clearbit-legacy data.
    // Bigger, higher-quality icons than raw favicons for many companies.
    `https://unavatar.io/${root}?fallback=false`,
    // DuckDuckGo's icon service — covers many sites Google missed.
    `https://icons.duckduckgo.com/ip3/${root}.ico`,
    // Google favicon — broadest coverage, lowest quality (32–64 px).
    `https://www.google.com/s2/favicons?domain=${root}&sz=128`,
    `https://www.google.com/s2/favicons?domain=${root}&sz=64`,
  ];
}

export function TickerLogo({ ticker, size = "md", className }: Props) {
  const meta = getTickerMeta(ticker.includes(".") ? ticker : `${ticker}.CA`);
  const sources = meta.logoDomain ? logoSources(meta.logoDomain) : [];
  const [idx, setIdx] = useState(0);

  const exhausted = idx >= sources.length;
  const sizeCls = SIZE_CLS[size];

  return (
    <div
      className={cn(
        "rounded-xl flex items-center justify-center overflow-hidden shrink-0",
        "bg-stone-100 border border-stone-200",
        "dark:bg-[var(--hairline)] dark:border-[var(--hairline-2)]",
        sizeCls,
        className
      )}
      title={meta.nameEn}
    >
      {!exhausted ? (
        <img
          key={sources[idx]}
          src={sources[idx]}
          alt={`${meta.symbol} logo`}
          width={64}
          height={64}
          loading="lazy"
          onError={() => setIdx((i) => i + 1)}
          className="h-full w-full object-contain p-1.5"
          referrerPolicy="no-referrer"
        />
      ) : (
        <span className="display font-semibold text-stone-700 dark:text-[var(--ink-2)]">
          {meta.symbol.slice(0, 4)}
        </span>
      )}
    </div>
  );
}
