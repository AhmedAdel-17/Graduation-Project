import { setLocale, useLocale, useT, type Locale } from "../../lib/i18n";
import { cn } from "../../lib/utils";

const options: { value: Locale; label: string }[] = [
  { value: "en", label: "EN" },
  { value: "ar", label: "ع" },
];

// Language switch styled to match the live Shell top strip (stone / warm theme).
// Segmented EN / AR pill. Switching locale flips the whole app to RTL via
// setLocale → document.documentElement.dir (see lib/i18n.ts).
export function LocaleToggle({ className }: { className?: string }) {
  const locale = useLocale();
  const t = useT();
  return (
    <div
      className={cn(
        "inline-flex items-center h-7 rounded-full border border-stone-200 bg-white p-0.5",
        "dark:bg-[var(--paper)] dark:border-[var(--hairline)]",
        className
      )}
      role="group"
      aria-label={t("shell.language")}
    >
      {options.map((opt) => {
        const active = locale === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => setLocale(opt.value)}
            aria-pressed={active}
            className={cn(
              "px-2 h-6 rounded-full text-[11px] font-semibold tracking-wide transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/40",
              active
                ? "bg-stone-900 text-white dark:bg-white dark:text-stone-900"
                : "text-stone-500 hover:text-stone-900 dark:text-[var(--ink-3)] dark:hover:text-white"
            )}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
