import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  FlaskConical,
  Compass,
  Cpu,
  History,
  Settings,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import { cn } from "../../lib/utils";
import { useT } from "../../lib/i18n";

interface MobileNavItem {
  to: string;
  labelKey: string;
  icon: LucideIcon;
  end?: boolean;
}

const nav: MobileNavItem[] = [
  { to: "/workspace", labelKey: "nav.mobile.workspace", icon: LayoutDashboard },
  { to: "/run", labelKey: "nav.mobile.run", icon: Sparkles },
  { to: "/sessions", labelKey: "nav.mobile.sessions", icon: History },
  { to: "/universe", labelKey: "nav.mobile.universe", icon: Compass },
  { to: "/backtest", labelKey: "nav.mobile.backtest", icon: FlaskConical },
  { to: "/diagnostics", labelKey: "nav.mobile.diagnostics", icon: Cpu },
  { to: "/settings", labelKey: "nav.mobile.settings", icon: Settings },
];

export function MobileNav() {
  const t = useT();
  return (
    <nav className="lg:hidden border-b border-line bg-ink-900/60 px-3 py-2 flex items-center gap-1 overflow-x-auto">
      {nav.map((item) => {
        const Icon = item.icon;
        return (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs shrink-0 transition-colors",
                isActive
                  ? "bg-brand-500/15 text-brand-400 border border-brand-500/30"
                  : "text-fg-muted hover:text-fg border border-transparent"
              )
            }
          >
            <Icon className="h-3.5 w-3.5" aria-hidden />
            {t(item.labelKey)}
          </NavLink>
        );
      })}
    </nav>
  );
}
