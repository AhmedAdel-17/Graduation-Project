import React from "react";
import { cn } from "../../lib/utils";

export interface InputProps
  extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  hint?: string;
  error?: string;
  leftAddon?: React.ReactNode;
  rightAddon?: React.ReactNode;
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  function Input(
    { label, hint, error, leftAddon, rightAddon, className, id, ...props },
    ref
  ) {
    const autoId = React.useId();
    const inputId = id ?? autoId;
    return (
      <div className="flex flex-col gap-1.5">
        {label && (
          <label
            htmlFor={inputId}
            className="text-xs font-medium text-fg-muted"
          >
            {label}
          </label>
        )}
        <div
          className={cn(
            "flex items-stretch rounded-lg overflow-hidden bg-ink-800/80 border",
            error ? "border-down/60" : "border-line focus-within:border-brand-500/50"
          )}
        >
          {leftAddon && (
            <span className="flex items-center px-3 text-fg-muted text-xs border-r border-line bg-ink-900/50">
              {leftAddon}
            </span>
          )}
          <input
            ref={ref}
            id={inputId}
            className={cn(
              "flex-1 bg-transparent px-3 h-10 text-sm text-fg placeholder:text-fg-subtle",
              "focus:outline-none",
              className
            )}
            {...props}
          />
          {rightAddon && (
            <span className="flex items-center px-3 text-fg-muted text-xs border-l border-line bg-ink-900/50">
              {rightAddon}
            </span>
          )}
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
