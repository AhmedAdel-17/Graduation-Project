import React from "react";
import { cn } from "../../lib/utils";

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center py-12 px-6",
        className
      )}
    >
      {icon && (
        <div className="h-10 w-10 rounded-full bg-ink-800 border border-line flex items-center justify-center text-fg-muted mb-3">
          {icon}
        </div>
      )}
      <h4 className="text-sm font-semibold text-fg">{title}</h4>
      {description && (
        <p className="text-xs text-fg-muted mt-1 max-w-xs">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
