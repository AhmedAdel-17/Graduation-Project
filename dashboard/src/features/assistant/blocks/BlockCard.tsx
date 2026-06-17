import type { ReactNode } from "react";
import { cn } from "../../../lib/utils";

/** Consistent card chrome for every chat block. */
export function BlockCard({
  title,
  icon,
  right,
  children,
  className,
}: {
  title?: ReactNode;
  icon?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-xl border border-stone-200 bg-white dark:border-[var(--hairline)] dark:bg-[var(--paper)]",
        className
      )}
    >
      {(title || right) && (
        <div className="flex items-center gap-2 px-3.5 h-10 border-b border-stone-200/70 dark:border-[var(--hairline)]">
          {icon && <span className="text-stone-400 dark:text-[var(--ink-3)]">{icon}</span>}
          {title && (
            <span className="eyebrow text-stone-500 dark:text-[var(--ink-3)] truncate">
              {title}
            </span>
          )}
          {right && <span className="ml-auto">{right}</span>}
        </div>
      )}
      <div className="p-3.5">{children}</div>
    </div>
  );
}
