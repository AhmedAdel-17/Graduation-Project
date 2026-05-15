import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatCurrency(
  value: number | null | undefined,
  currency = "EGP",
  fractionDigits = 2
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  const formatted = abs.toLocaleString("en-US", {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  });
  return `${sign}${formatted} ${currency}`;
}

export function formatNumber(
  value: number | null | undefined,
  fractionDigits = 2
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-US", {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  });
}

export function formatPercent(
  value: number | null | undefined,
  fractionDigits = 2,
  signed = true
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = signed && value > 0 ? "+" : "";
  return `${sign}${value.toFixed(fractionDigits)}%`;
}

export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

export function signalColor(signal?: string): string {
  const s = (signal || "").toUpperCase();
  if (s === "BUY" || s === "STRONG_BUY") return "text-up";
  if (s === "SELL" || s === "STRONG_SELL") return "text-down";
  return "text-fg-muted";
}

export function signalBg(signal?: string): string {
  const s = (signal || "").toUpperCase();
  if (s === "BUY" || s === "STRONG_BUY")
    return "bg-up/10 text-up border-up/30";
  if (s === "SELL" || s === "STRONG_SELL")
    return "bg-down/10 text-down border-down/30";
  return "bg-ink-700/60 text-fg-muted border-line";
}

export function confidenceColor(level?: string): string {
  const l = (level || "").toUpperCase();
  if (l === "HIGH") return "bg-brand-500/15 text-brand-400 border-brand-500/30";
  if (l === "LOW") return "bg-down/10 text-down border-down/30";
  return "bg-accent/10 text-accent border-accent/30";
}
