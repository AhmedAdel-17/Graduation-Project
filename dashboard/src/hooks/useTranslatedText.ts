import { useQuery } from "@tanstack/react-query";
import { translateText } from "../services/translationBatcher";
import { useLocale } from "../lib/i18n";

/**
 * On-demand translation of a single dynamic text/markdown string (agent prose)
 * to the active locale. English is the source of truth, so this is a no-op when
 * the locale is English or the text is empty — it returns the source instantly.
 *
 * When Arabic is active it calls the cache-first `/api/translate` backend. While
 * the (first-ever) translation is in flight the original English is shown with an
 * `isTranslating` flag so the caller can render a shimmer; the server caches by
 * content hash so any later view of the same text is instant. TanStack Query also
 * memoizes per (locale, source) within the session, and on error we fall back to
 * the source text so the UI never blanks.
 */
export function useTranslatedText(
  source: string | null | undefined
): { text: string; isTranslating: boolean } {
  const locale = useLocale();
  const src = source ?? "";
  const enabled = locale === "ar" && src.trim().length > 0;

  const query = useQuery({
    queryKey: ["translate", locale, src],
    queryFn: () => translateText(src, locale),
    enabled,
    staleTime: Infinity,
    gcTime: 60 * 60 * 1000,
    retry: 1,
  });

  if (!enabled) return { text: src, isTranslating: false };
  return { text: query.data ?? src, isTranslating: query.isLoading };
}
