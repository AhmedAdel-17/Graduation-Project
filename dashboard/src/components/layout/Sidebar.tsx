import { NavLink } from "react-router-dom";
import { cn } from "../../lib/utils";
import { LineChart, FlaskConical, Terminal, CandlestickChart } from "lucide-react";

const nav = [
  {
    to: "/",
    label: "Prediction",
    icon: LineChart,
    end: true,
    description: "Live signals",
  },
  {
    to: "/backtest",
    label: "Backtesting",
    icon: FlaskConical,
    description: "Strategy simulation",
  },
  {
    to: "/backtest/advanced",
    label: "Advanced Backtest",
    icon: Terminal,
    description: "Script execution",
  },
];

export function Sidebar() {
  return (
    <aside className="hidden lg:flex flex-col w-64 shrink-0 border-r border-line bg-ink-900/40 backdrop-blur-sm">
      <div className="h-16 px-5 flex items-center gap-2.5 border-b border-line">
        <div className="h-8 w-8 rounded-lg bg-brand-500/15 border border-brand-500/30 flex items-center justify-center">
          <CandlestickChart className="h-4 w-4 text-brand-400" />
        </div>
        <div className="leading-tight">
          <p className="text-sm font-semibold text-fg">EGX Intel</p>
          <p className="text-[10px] uppercase tracking-[0.15em] text-fg-subtle">
            Trading Console
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
                  />
                  <span className="flex-1">
                    <span className="block font-medium">{item.label}</span>
                    <span className="block text-[11px] text-fg-subtle mt-0.5">
                      {item.description}
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
            Market
          </p>
          <p className="text-sm font-semibold text-fg mt-1">
            Egyptian Exchange
          </p>
          <p className="text-[11px] text-fg-muted mt-0.5">
            EGX • Long-only • ±10% daily
          </p>
        </div>
      </div>
    </aside>
  );
}
