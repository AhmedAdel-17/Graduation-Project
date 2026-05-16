import { NavLink } from "react-router-dom";
import { cn } from "../../lib/utils";
import {
  LayoutDashboard,
  FlaskConical,
  CandlestickChart,
  Compass,
  Cpu,
  History,
  Settings,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import { useT } from "../../lib/i18n";

interface NavItem {
  to: string;
  labelKey: string;
  descKey: string;
  icon: LucideIcon;
  end?: boolean;
}

const nav: NavItem[] = [
  {
    to: "/workspace",
    labelKey: "nav.workspace",
    descKey: "nav.workspace.desc",
    icon: LayoutDashboard,
  },
  {
    to: "/run",
    labelKey: "nav.run",
    descKey: "nav.run.desc",
    icon: Sparkles,
  },
  {
    to: "/sessions",
    labelKey: "nav.sessions",
    descKey: "nav.sessions.desc",
    icon: History,
  },
  {
    to: "/universe",
    labelKey: "nav.universe",
    descKey: "nav.universe.desc",
    icon: Compass,
  },
  {
    to: "/backtest",
    labelKey: "nav.backtest",
    descKey: "nav.backtest.desc",
    icon: FlaskConical,
  },
  {
    to: "/diagnostics",
    labelKey: "nav.diagnostics",
    descKey: "nav.diagnostics.desc",
    icon: Cpu,
  },
  {
    to: "/settings",
    labelKey: "nav.settings",
    descKey: "nav.settings.desc",
    icon: Settings,
  },
];

export function Sidebar() {
  const t = useT();
  return (
    <aside className="hidden lg:flex flex-col w-64 shrink-0 border-r border-line bg-ink-900/40 backdrop-blur-sm">
      <div className="h-16 px-5 flex items-center gap-2.5 border-b border-line">
        <div className="h-8 w-8 rounded-lg bg-brand-500/15 border border-brand-500/30 flex items-center justify-center">
          <CandlestickChart className="h-4 w-4 text-brand-400" aria-hidden />
        </div>
        <div className="leading-tight">
          <p className="text-sm font-semibold text-fg">{t("brand.name")}</p>
          <p className="text-[10px] uppercase tracking-[0.15em] text-fg-subtle">
            {t("brand.tagline")}
          </p>
        </div>
      </div>

      <nav className="flex-1 p-3 flex flex-col gap-1">
        {nav.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn(
                  "group flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm",
                  "transition-colors",
                  isActive
                    ? "bg-ink-800 text-fg border border-line-strong"
                    : "text-fg-muted hover:text-fg hover:bg-ink-800/70 border border-transparent"
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon
                    className={cn(
                      "h-4 w-4 shrink-0",
                      isActive ? "text-brand-400" : "text-fg-muted group-hover:text-fg"
                    )}
                    aria-hidden
                  />
                  <span className="flex-1">
                    <span className="block font-medium">{t(item.labelKey)}</span>
                    <span className="block text-[11px] text-fg-subtle mt-0.5">
                      {t(item.descKey)}
                    </span>
                  </span>
                </>
              )}
            </NavLink>
          );
        })}
      </nav>

      <div className="p-4 border-t border-line">
        <div className="rounded-lg bg-ink-800/70 border border-line px-3 py-3">
          <p className="text-[10px] uppercase tracking-[0.15em] text-fg-subtle">
            {t("sidebar.market")}
          </p>
          <p className="text-sm font-semibold text-fg mt-1">
            {t("sidebar.market.name")}
          </p>
          <p className="text-[11px] text-fg-muted mt-0.5">
            {t("sidebar.market.rules")}
          </p>
        </div>
      </div>
    </aside>
  );
}
