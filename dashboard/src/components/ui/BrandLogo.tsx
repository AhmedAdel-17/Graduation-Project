import { cn } from "../../lib/utils";

/**
 * StockHive brand mark — a hexagon enclosing an interlocking "S" formed by two
 * counter-rotating bars (navy) with a green ribbon threading through them.
 * Recreated as inline SVG so it stays crisp at any size and themes cleanly.
 */
export function BrandLogo({
  className,
  title = "StockHive",
}: {
  className?: string;
  title?: string;
}) {
  return (
    <svg
      viewBox="0 0 48 48"
      className={className}
      role="img"
      aria-label={title}
      fill="none"
    >
      <title>{title}</title>
      {/* Hexagon shell */}
      <path
        d="M24 2.5 44.3 13.75 V37.25 L24 48.5 3.7 37.25 V13.75 Z"
        fill="none"
        stroke="var(--brand-navy, #14284A)"
        strokeWidth="3.2"
        strokeLinejoin="round"
      />
      {/* Two interlocking bars — the stylised S / candlesticks */}
      <path
        d="M31.5 12 C 22.5 13.5, 20.5 21, 25.5 24"
        stroke="var(--brand-navy, #14284A)"
        strokeWidth="4.4"
        strokeLinecap="round"
      />
      <path
        d="M22.5 36 C 31.5 34.5, 33.5 27, 28.5 24"
        stroke="var(--brand-navy, #14284A)"
        strokeWidth="4.4"
        strokeLinecap="round"
      />
      {/* Vertical accents (the H uprights) */}
      <path d="M17.5 24 V 35.5" stroke="var(--brand-navy, #14284A)" strokeWidth="4.4" strokeLinecap="round" />
      <path d="M30.5 12.5 V 24" stroke="var(--brand-navy, #14284A)" strokeWidth="4.4" strokeLinecap="round" />
      {/* Green ribbon threading the S */}
      <path
        d="M27 9.5 C 19 15, 22 22, 27 24.5 C 32 27, 29 34, 21 39"
        stroke="var(--brand-green, #2FA35B)"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}
