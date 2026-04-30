import React from "react";
import { cn } from "../../lib/utils";
import { TrendingUp, TrendingDown } from "lucide-react";

export interface MetricsCardProps {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  trend?: "up" | "down" | "flat";
  tone?: "default" | "up" | "down" | "brand";
  className?: string;
}

export function MetricsCard({
  label,
  value,
  hint,
  trend,
  tone = "default",
  className,
}: MetricsCardProps) {
  const toneClass = {
    default: "text-fg",
    up: "text-up",
    down: "text-down",
    brand: "text-brand-400",
  }[tone];

  return (
    <div
      className={cn(
        "surface rounded-xl px-4 py-4 shadow-card",
        "flex flex-col gap-1",
        className
      )}
    >
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-medium uppercase tracking-wider text-fg-muted">
          {label}
        </p>
        {trend === "up" && <TrendingUp className="h-3.5 w-3.5 text-up" />}
        {trend === "down" && <TrendingDown className="h-3.5 w-3.5 text-down" />}
      </div>
      <p className={cn("num text-xl font-semibold tracking-tight", toneClass)}>
        {value}
      </p>
      {hint && <p className="text-xs text-fg-muted truncate">{hint}</p>}
    </div>
  );
}
