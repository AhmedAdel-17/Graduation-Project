import { useState, useCallback } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "../../../lib/utils";

export function CopyButton({
  value,
  label = "Copy",
  className,
}: {
  value: string;
  label?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  const onClick = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard blocked (insecure context) — silently ignore */
    }
  }, [value]);

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] font-medium border transition-colors",
        "border-stone-200 text-stone-600 hover:bg-stone-100 hover:text-stone-900",
        "dark:border-[var(--hairline)] dark:text-[var(--ink-2)] dark:hover:bg-white/5 dark:hover:text-white",
        className
      )}
      aria-label={copied ? "Copied" : label}
    >
      {copied ? (
        <Check className="h-3 w-3 text-emerald-500" aria-hidden />
      ) : (
        <Copy className="h-3 w-3" aria-hidden />
      )}
      {copied ? "Copied" : label}
    </button>
  );
}
