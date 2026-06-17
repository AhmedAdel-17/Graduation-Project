// Shared visual helpers for the Portfolio Assistant chart blocks.

// Categorical palette for allocation slices / series. Tuned to read in both
// light and dark themes; cash gets a muted neutral so it never competes.
export const SERIES_COLORS = [
  "#2563eb", // blue
  "#0891b2", // cyan
  "#7c3aed", // violet
  "#db2777", // pink
  "#ea580c", // orange
  "#16a34a", // green
  "#ca8a04", // amber
  "#475569", // slate
];

export const CASH_COLOR = "#a8a29e"; // stone-400

export function sliceColor(index: number, isCash = false): string {
  return isCash ? CASH_COLOR : SERIES_COLORS[index % SERIES_COLORS.length];
}

/**
 * Map a blended agent signal tone in [-1, 1] to a background color
 * (red = bearish → neutral grey → green = bullish). Returns an rgba string.
 */
export function toneColor(tone: number | null | undefined): string {
  if (tone == null) return "rgba(120,113,108,0.18)"; // neutral stone
  const t = Math.max(-1, Math.min(1, tone));
  if (t >= 0) {
    // grey → green
    const a = 0.15 + 0.45 * t;
    return `rgba(22,163,74,${a.toFixed(3)})`;
  }
  const a = 0.15 + 0.45 * -t;
  return `rgba(220,38,38,${a.toFixed(3)})`;
}

/** Tailwind text class for a delta sign (improvement is context-specific, so
 *  callers decide whether up is good; this only colors by sign). */
export function deltaClass(value: number, goodWhenNegative = false): string {
  const good = goodWhenNegative ? value < 0 : value > 0;
  if (value === 0) return "text-stone-500 dark:text-[var(--ink-3)]";
  return good
    ? "text-emerald-600 dark:text-emerald-400"
    : "text-rose-600 dark:text-rose-400";
}
