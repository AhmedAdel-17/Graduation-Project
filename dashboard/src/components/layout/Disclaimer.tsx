import { useEffect, useState } from "react";
import { ShieldAlert, X } from "lucide-react";
import { useT } from "../../lib/i18n";

const STORAGE_KEY = "egx-dashboard-disclaimer-ack";

function readAck(): boolean {
  if (typeof window === "undefined") return true;
  return window.localStorage.getItem(STORAGE_KEY) === "1";
}

function writeAck() {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, "1");
}

export function DisclaimerModal() {
  const t = useT();
  // Lazy init reads localStorage once at mount; no effect needed.
  const [open, setOpen] = useState<boolean>(() => !readAck());

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (!open) return null;

  const dismiss = () => {
    writeAck();
    setOpen(false);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="disclaimer-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-ink-950/80 backdrop-blur-sm animate-fade-in"
    >
      <div className="relative w-full max-w-lg surface rounded-2xl shadow-card p-6">
        <button
          type="button"
          onClick={dismiss}
          aria-label={t("common.close")}
          className="absolute top-3 end-3 h-7 w-7 rounded-md text-fg-muted hover:text-fg hover:bg-ink-800 inline-flex items-center justify-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
        <div className="flex items-center gap-2 text-amber-400">
          <ShieldAlert className="h-5 w-5" aria-hidden />
          <h2 id="disclaimer-title" className="text-sm font-semibold tracking-tight">
            {t("disclaimer.title")}
          </h2>
        </div>
        <p className="mt-3 text-sm text-fg-muted leading-relaxed">
          {t("disclaimer.body")}
        </p>
        <div className="mt-5 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={dismiss}
            className="inline-flex items-center gap-2 h-9 px-4 rounded-lg bg-brand-500 hover:bg-brand-400 text-ink-950 text-sm font-semibold shadow-glow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
          >
            {t("disclaimer.acknowledge")}
          </button>
        </div>
      </div>
    </div>
  );
}

export function DisclaimerRibbon() {
  const t = useT();
  return (
    <div
      role="note"
      className="flex items-center gap-2 px-4 py-1.5 bg-amber-400/5 border-b border-amber-400/20 text-[11px] text-amber-300/90"
    >
      <ShieldAlert className="h-3 w-3 shrink-0" aria-hidden />
      <span className="truncate">{t("footer.disclaimer.short")}</span>
    </div>
  );
}
