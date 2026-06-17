import React from "react";
import { cn } from "../../../lib/utils";

// Theme-aware surface primitives for the admin suite. Built on the live
// `.card` token system (CSS-var driven) so dark + light both work — unlike the
// older ui/Card which uses fixed light-only Tailwind tokens.

export function AdminCard({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("card", className)} {...props}>
      {children}
    </div>
  );
}

export function AdminCardHeader({
  title,
  description,
  icon,
  actions,
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  icon?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-start justify-between gap-4 px-4 py-3.5 border-b border-stone-200/80 dark:border-[var(--hairline)]",
        className
      )}
    >
      <div className="flex items-start gap-2.5 min-w-0">
        {icon && (
          <div className="mt-0.5 text-stone-500 dark:text-[var(--ink-3)] shrink-0">
            {icon}
          </div>
        )}
        <div className="min-w-0">
          <h3 className="text-[13px] font-semibold tracking-tight text-ink truncate">
            {title}
          </h3>
          {description && (
            <p className="text-[11.5px] text-stone-500 dark:text-[var(--ink-3)] mt-0.5">
              {description}
            </p>
          )}
        </div>
      </div>
      {actions && <div className="shrink-0 flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function AdminCardBody({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("px-4 py-4", className)} {...props}>
      {children}
    </div>
  );
}
