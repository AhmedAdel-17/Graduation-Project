import { Languages } from "lucide-react";
import { Markdown } from "./Markdown";
import { SectionedMarkdown } from "./SectionedMarkdown";
import { useTranslatedText } from "../../hooks/useTranslatedText";
import { useLocale, useT } from "../../lib/i18n";
import { cn } from "../../lib/utils";

/**
 * Renders dynamic agent prose (theses, verdicts, reasoning) with on-demand
 * translation to Egyptian Arabic. In English it is a transparent pass-through to
 * <Markdown> / <SectionedMarkdown>. In Arabic it shows a small "auto-translated"
 * badge, translates via the cache-first backend, and dims the text with a
 * "Translating…" hint while the first translation is in flight.
 */
export function TranslatedMarkdown({
  children,
  variant = "paper",
  sectioned = false,
}: {
  children: string;
  variant?: "dark" | "paper";
  sectioned?: boolean;
}) {
  const locale = useLocale();
  const t = useT();
  const { text, isTranslating } = useTranslatedText(children);

  const body = sectioned ? (
    <SectionedMarkdown variant={variant}>{text}</SectionedMarkdown>
  ) : (
    <Markdown variant={variant}>{text}</Markdown>
  );

  if (locale !== "ar") return body;

  return (
    <div>
      <div
        className={cn(
          "mb-2 inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10.5px] font-medium",
          isTranslating
            ? "border-stone-200 text-stone-500 dark:border-[var(--hairline)] dark:text-[var(--ink-3)]"
            : "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-900/20 dark:text-emerald-300"
        )}
      >
        <Languages className="h-3 w-3" aria-hidden />
        {isTranslating ? t("common.translating") : t("common.autoTranslated")}
      </div>
      <div className={cn(isTranslating && "opacity-60 transition-opacity")}>{body}</div>
    </div>
  );
}
