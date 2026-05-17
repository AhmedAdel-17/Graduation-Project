import { useQuery } from "@tanstack/react-query";
import { endpoints } from "../../services/api";
import { Activity, Clock, Moon, Sun } from "lucide-react";
import { format } from "date-fns";
import { LocaleToggle } from "../ui/LocaleToggle";
import { useT } from "../../lib/i18n";
import { useTheme } from "../../hooks/useTheme";

export function TopBar({ title, subtitle }: { title: string; subtitle?: string }) {
  const t = useT();
  const { theme, toggle } = useTheme();
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: () => endpoints.health(),
    refetchInterval: 30_000,
    retry: 0,
  });

  const online = health?.status === "ok";

  return (
    <header
      className="sticky top-0 z-30 h-16 border-b border-line bg-white/90 backdrop-blur-md px-5 lg:px-8 flex items-center justify-between
        dark:bg-[var(--paper)]/85 dark:border-[var(--hairline)]"
    >
      <div className="min-w-0">
        <h1 className="text-base font-semibold tracking-tight text-fg truncate dark:text-[var(--ink)]">
          {title}
        </h1>
        {subtitle && (
          <p className="text-xs text-fg-muted truncate dark:text-[var(--ink-3)]">{subtitle}</p>
        )}
      </div>

      <div className="flex items-center gap-3">
        <div className="hidden sm:flex items-center gap-1.5 text-xs text-fg-muted dark:text-[var(--ink-3)]">
          <Clock className="h-3.5 w-3.5" aria-hidden />
          <span className="num">{format(new Date(), "MMM d, yyyy · HH:mm")}</span>
        </div>
        <LocaleToggle />
        <button
          type="button"
          onClick={toggle}
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          className="inline-flex items-center justify-center h-7 w-7 rounded-md border border-stone-200 hover:bg-stone-50
            text-stone-600 transition-colors
            dark:border-[var(--hairline)] dark:hover:bg-[var(--hairline)] dark:text-[var(--ink-2)]"
        >
          {theme === "dark" ? (
            <Sun className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <Moon className="h-3.5 w-3.5" aria-hidden />
          )}
        </button>
        <div
          className={
            "flex items-center gap-1.5 px-2.5 h-7 rounded-md border text-[11px] font-medium " +
            (online
              ? "bg-up/10 border-up/30 text-up"
              : "bg-down/10 border-down/30 text-down")
          }
          aria-live="polite"
        >
          <Activity className="h-3 w-3" aria-hidden />
          <span className="uppercase tracking-wider">
            {online ? t("topbar.api.online") : t("topbar.api.offline")}
          </span>
        </div>
      </div>
    </header>
  );
}
