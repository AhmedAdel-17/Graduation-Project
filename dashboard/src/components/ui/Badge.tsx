import React from "react";
import { cn } from "../../lib/utils";

export type BadgeTone =
  | "neutral"
  | "brand"
  | "up"
  | "down"
  | "accent"
  | "warning";

const tones: Record<BadgeTone, string> = {
  neutral: "bg-ink-700/70 text-fg-muted border-line",
  brand: "bg-brand-500/15 text-brand-400 border-brand-500/30",
  up: "bg-up/10 text-up border-up/30",
  down: "bg-down/10 text-down border-down/30",
  accent: "bg-accent/10 text-accent border-accent/30",
  warning: "bg-amber-400/10 text-amber-400 border-amber-400/30",
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
}

export function Badge({
  tone = "neutral",
  className,
  children,
  ...props
}: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-medium uppercase tracking-wide",
        tones[tone],
        className
      )}
      {...props}
    >
      {children}
    </span>
  );
}
