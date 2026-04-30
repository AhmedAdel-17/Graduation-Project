import React from "react";
import { cn } from "../../lib/utils";
import { ChevronDown } from "lucide-react";

export interface SelectProps
  extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  hint?: string;
  error?: string;
}

export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  function Select({ label, hint, error, className, id, children, ...props }, ref) {
    const autoId = React.useId();
    const selectId = id ?? autoId;
    return (
      <div className="flex flex-col gap-1.5">
        {label && (
          <label
            htmlFor={selectId}
            className="text-xs font-medium text-fg-muted"
          >
            {label}
          </label>
        )}
        <div
          className={cn(
            "relative rounded-lg bg-ink-800/80 border",
            error ? "border-down/60" : "border-line focus-within:border-brand-500/50"
          )}
        >
          <select
            ref={ref}
            id={selectId}
            className={cn(
              "w-full appearance-none bg-transparent pl-3 pr-9 h-10 text-sm text-fg",
              "focus:outline-none",
              className
            )}
            {...props}
          >
            {children}
          </select>
          <ChevronDown
            className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-fg-muted"
            aria-hidden
          />
        </div>
        {(hint || error) && (
          <p className={cn("text-xs", error ? "text-down" : "text-fg-subtle")}>
            {error || hint}
          </p>
        )}
      </div>
    );
  }
);
