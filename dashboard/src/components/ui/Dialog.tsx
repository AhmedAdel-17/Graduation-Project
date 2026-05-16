import { useEffect, useId, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "../../lib/utils";
import { t } from "../../lib/i18n";

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  className,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  const titleId = useId();
  const descId = useId();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false);
    };
    window.addEventListener("keydown", onKey);
    const { overflow } = document.body.style;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
    };
  }, [open, onOpenChange]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={title ? titleId : undefined}
      aria-describedby={description ? descId : undefined}
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-ink-950/80 backdrop-blur-sm animate-fade-in"
      onClick={() => onOpenChange(false)}
    >
      <div
        className={cn(
          "relative w-full max-w-lg surface rounded-2xl shadow-card p-6",
          className
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          onClick={() => onOpenChange(false)}
          aria-label={t("common.close")}
          className="absolute top-3 end-3 h-7 w-7 rounded-md text-fg-muted hover:text-fg hover:bg-ink-800 inline-flex items-center justify-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
        {title && (
          <h2 id={titleId} className="text-sm font-semibold tracking-tight text-fg pe-8">
            {title}
          </h2>
        )}
        {description && (
          <p id={descId} className="mt-2 text-xs text-fg-muted">
            {description}
          </p>
        )}
        <div className={cn(title || description ? "mt-4" : undefined)}>{children}</div>
      </div>
    </div>
  );
}
