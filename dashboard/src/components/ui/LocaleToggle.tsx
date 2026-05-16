import { Languages } from "lucide-react";
import { cn } from "../../lib/utils";
import { setLocale, useLocale, useT, type Locale } from "../../lib/i18n";

const options: { value: Locale; label: string }[] = [
  { value: "en", label: "EN" },
  { value: "ar", label: "AR" },
];

export function LocaleToggle({ className }: { className?: string }) {
  const locale = useLocale();
  const t = useT();
  return (
    <div
      className={cn(
        "inline-flex items-center gap-1 h-7 rounded-md border border-line bg-ink-800/70 px-1",
        className
      )}
      role="group"
      aria-label={t("topbar.locale")}
    >
      <Languages className="h-3 w-3 text-fg-muted me-0.5 ms-1" aria-hidden />
      {options.map((opt) => {
        const active = locale === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => setLocale(opt.value)}
            aria-pressed={active}
            className={cn(
              "px-2 h-5 rounded-sm text-[10px] font-semibold tracking-wider transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50",
              active
                ? "bg-brand-500/15 text-brand-400"
                : "text-fg-muted hover:text-fg"
            )}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
