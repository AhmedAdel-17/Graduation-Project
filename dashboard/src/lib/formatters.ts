import { format, parseISO } from "date-fns";

const egpFormatter = new Intl.NumberFormat("en-EG", {
  style: "currency",
  currency: "EGP",
  maximumFractionDigits: 0,
});
const egpFormatterPrecise = new Intl.NumberFormat("en-EG", {
  style: "currency",
  currency: "EGP",
  maximumFractionDigits: 2,
});

export function formatEGP(n: number, precise = false): string {
  if (!Number.isFinite(n)) return "—";
  return precise ? egpFormatterPrecise.format(n) : egpFormatter.format(n);
}

export function formatPct(n: number, digits = 2): string {
  if (!Number.isFinite(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(digits)}%`;
}

export function formatNumber(n: number, digits = 0): string {
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatDate(s: string | Date, fmt = "MMM d, yyyy"): string {
  try {
    const d = typeof s === "string" ? parseISO(s) : s;
    return format(d, fmt);
  } catch {
    return String(s);
  }
}

export function formatDateTime(s: string | Date): string {
  return formatDate(s, "MMM d, yyyy · HH:mm");
}

export function formatRelative(s: string): string {
  try {
    const d = parseISO(s);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return "just now";
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
    return formatDate(s);
  } catch {
    return s;
  }
}
