import { AlertTriangle, Inbox, Loader2, RotateCw } from "lucide-react";
import { cn } from "../../../lib/utils";

// Consistent loading / empty / error states across every admin screen.

export function AdminLoading({
  label = "Loading…",
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <div
      className={cn("card p-10 flex flex-col items-center justify-center gap-2.5", className)}
      role="status"
      aria-live="polite"
    >
      <Loader2 className="h-5 w-5 text-stone-400 dark:text-[var(--ink-3)] animate-spin" aria-hidden />
      <span className="text-[12.5px] text-stone-500 dark:text-[var(--ink-3)]">{label}</span>
    </div>
  );
}

export function AdminError({
  title = "Couldn’t load data",
  message = "The backend request failed. Check that the API server is running.",
  onRetry,
  className,
}: {
  title?: string;
  message?: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      className={cn("card p-10 flex flex-col items-center justify-center text-center gap-2.5", className)}
      role="alert"
    >
      <div className="h-11 w-11 rounded-xl border border-red-200 dark:border-red-900/40 bg-red-50 dark:bg-red-900/20 flex items-center justify-center">
        <AlertTriangle className="h-5 w-5 text-red-500" aria-hidden />
      </div>
      <h2 className="text-[14px] font-semibold text-ink">{title}</h2>
      <p className="text-[12.5px] text-stone-500 dark:text-[var(--ink-3)] max-w-sm">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-1 inline-flex items-center gap-1.5 h-8 px-3 rounded-md text-[12px] font-medium border border-stone-200 text-stone-600 hover:bg-stone-100 dark:border-[var(--hairline)] dark:text-[var(--ink-2)] dark:hover:bg-white/5"
        >
          <RotateCw className="h-3.5 w-3.5" aria-hidden /> Retry
        </button>
      )}
    </div>
  );
}

export function AdminEmpty({
  title,
  description,
  icon,
  className,
}: {
  title: string;
  description?: string;
  icon?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("card p-10 flex flex-col items-center justify-center text-center gap-2", className)}>
      <div className="h-11 w-11 rounded-xl border border-stone-200 dark:border-[var(--hairline)] bg-stone-50 dark:bg-white/[0.03] flex items-center justify-center mb-1">
        {icon ?? <Inbox className="h-5 w-5 text-stone-400 dark:text-[var(--ink-3)]" aria-hidden />}
      </div>
      <h2 className="text-[14px] font-semibold text-ink">{title}</h2>
      {description && (
        <p className="text-[12.5px] text-stone-500 dark:text-[var(--ink-3)] max-w-sm">{description}</p>
      )}
    </div>
  );
}
