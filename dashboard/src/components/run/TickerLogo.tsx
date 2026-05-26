import { useState } from "react";
import { cn } from "@/lib/utils";

/**
 * Ticker logo — renders the company's real logo from a locally-bundled image
 * at /logos/{SYMBOL}.png (downloaded at build/setup time so it works offline
 * and has no runtime dependency on any logo service). Falls back to a
 * deterministic colored monogram when no bundled logo exists.
 *
 * Bundled logos live in dashboard/public/logos/. To add one for a ticker that
 * currently shows a monogram, drop a square PNG named "{SYMBOL}.png" (e.g.
 * "FWRY.CA.png") into that folder and add the symbol to HAS_LOGO below.
 */

// Tickers that have a real bundled logo file. Anything not listed → monogram.
const HAS_LOGO = new Set<string>([
  "ABUK.CA", "ADIB.CA", "CICH.CA", "CIEB.CA", "COMI.CA", "DOMT.CA",
  "EAST.CA", "EFID.CA", "EMFD.CA", "ESRS.CA", "ETEL.CA", "JUFO.CA",
  "OCDI.CA", "ORAS.CA", "PHDC.CA", "RAYA.CA", "SWDY.CA", "TMGH.CA",
]);

const PALETTE = [
  { bg: "bg-emerald-500/15", fg: "text-emerald-600 dark:text-emerald-400" },
  { bg: "bg-sky-500/15",     fg: "text-sky-600 dark:text-sky-400" },
  { bg: "bg-violet-500/15",  fg: "text-violet-600 dark:text-violet-400" },
  { bg: "bg-amber-500/15",   fg: "text-amber-600 dark:text-amber-400" },
  { bg: "bg-rose-500/15",    fg: "text-rose-600 dark:text-rose-400" },
  { bg: "bg-teal-500/15",    fg: "text-teal-600 dark:text-teal-400" },
  { bg: "bg-indigo-500/15",  fg: "text-indigo-600 dark:text-indigo-400" },
  { bg: "bg-orange-500/15",  fg: "text-orange-600 dark:text-orange-400" },
  { bg: "bg-cyan-500/15",    fg: "text-cyan-600 dark:text-cyan-400" },
  { bg: "bg-fuchsia-500/15", fg: "text-fuchsia-600 dark:text-fuchsia-400" },
];

function colorFor(symbol: string) {
  let h = 0;
  for (let i = 0; i < symbol.length; i++) h = (h * 31 + symbol.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(h) % PALETTE.length];
}

const SIZES = {
  sm: { box: "h-5 w-5 text-[9px] rounded-[5px]", px: 20 },
  md: { box: "h-7 w-7 text-[11px] rounded-md", px: 28 },
  lg: { box: "h-9 w-9 text-xs rounded-lg", px: 36 },
} as const;

export function TickerLogo({
  symbol,
  size = "sm",
  className,
}: {
  symbol: string;
  size?: keyof typeof SIZES;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const s = SIZES[size];
  const sym = symbol.toUpperCase();
  const base = symbol.replace(/\.CA$/i, "");
  const initials =
    base.replace(/[^A-Za-z]/g, "").slice(0, 2).toUpperCase() || base.slice(0, 2);
  const c = colorFor(symbol);

  if (HAS_LOGO.has(sym) && !failed) {
    return (
      <span
        className={cn(
          "inline-flex shrink-0 items-center justify-center overflow-hidden bg-white",
          s.box,
          className,
        )}
        aria-hidden="true"
      >
        <img
          src={`/logos/${sym}.png`}
          alt=""
          width={s.px}
          height={s.px}
          loading="lazy"
          className="h-full w-full object-contain"
          onError={() => setFailed(true)}
        />
      </span>
    );
  }

  // Monogram fallback
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center justify-center font-semibold tracking-tight",
        s.box,
        c.bg,
        c.fg,
        className,
      )}
      aria-hidden="true"
    >
      {initials}
    </span>
  );
}
