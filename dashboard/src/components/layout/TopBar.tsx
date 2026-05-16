import { useQuery } from "@tanstack/react-query";
import { endpoints } from "../../services/api";
import { Activity, Clock } from "lucide-react";
import { format } from "date-fns";
import { LocaleToggle } from "../ui/LocaleToggle";
import { useT } from "../../lib/i18n";

export function TopBar({ title, subtitle }: { title: string; subtitle?: string }) {
  const t = useT();
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: () => endpoints.health(),
    refetchInterval: 30_000,
    retry: 0,
  });

  const online = health?.status === "ok";

  return (
    <header className="sticky top-0 z-30 h-16 border-b border-line bg-white/90 backdrop-blur-md px-5 lg:px-8 flex items-center justify-between">
      <div className="min-w-0">
        <h1 className="text-base font-semibold tracking-tight text-fg truncate">
          {title}
        </h1>
        {subtitle && (
          <p className="text-xs text-fg-muted truncate">{subtitle}</p>
        )}
      </div>

      <div className="flex items-center gap-3">
        <div className="hidden sm:flex items-center gap-1.5 text-xs text-fg-muted">
          <Clock className="h-3.5 w-3.5" aria-hidden />
          <span className="num">{format(new Date(), "MMM d, yyyy · HH:mm")}</span>
        </div>
        <LocaleToggle />
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
