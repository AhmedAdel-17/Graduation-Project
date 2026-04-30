import { NavLink } from "react-router-dom";
import { LineChart, FlaskConical, Terminal } from "lucide-react";
import { cn } from "../../lib/utils";

const nav = [
  { to: "/", label: "Predict", icon: LineChart, end: true },
  { to: "/backtest", label: "Backtest", icon: FlaskConical },
  { to: "/backtest/advanced", label: "Advanced", icon: Terminal },
];

export function MobileNav() {
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
            <Icon className="h-3.5 w-3.5" />
            {item.label}
          </NavLink>
        );
      })}
    </nav>
  );
}
