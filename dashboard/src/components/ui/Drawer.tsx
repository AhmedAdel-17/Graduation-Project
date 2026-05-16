import { useEffect, useId, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "../../lib/utils";
import { t, useLocale } from "../../lib/i18n";

export function Drawer({
  open,
  onOpenChange,
  side = "end",
  title,
  children,
  className,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  side?: "start" | "end";
  title?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  const titleId = useId();
  const locale = useLocale();
  const rtl = locale === "ar";

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  if (!open) return null;

  // Resolve logical side -> physical side based on locale direction.
  const physicalRight = side === "end" ? !rtl : rtl;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={title ? titleId : undefined}
      className="fixed inset-0 z-50 flex bg-ink-950/70 backdrop-blur-sm animate-fade-in"
      onClick={() => onOpenChange(false)}
    >
      <div
        className={cn(
          "h-full w-full max-w-md bg-ink-900/95 border-line shadow-card flex flex-col",
          physicalRight ? "ms-auto border-s" : "me-auto border-e",
          className
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between h-14 px-5 border-b border-line">
          {title ? (
            <h2 id={titleId} className="text-sm font-semibold tracking-tight text-fg">
              {title}
            </h2>
          ) : (
            <span />
          )}
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            aria-label={t("common.close")}
            className="h-7 w-7 rounded-md text-fg-muted hover:text-fg hover:bg-ink-800 inline-flex items-center justify-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>
        <div className="flex-1 overflow-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}
