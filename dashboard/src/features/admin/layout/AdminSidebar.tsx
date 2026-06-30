import { NavLink } from "react-router-dom";
import { cn } from "../../../lib/utils";
import { ADMIN_NAV } from "./adminNav";

export function AdminSidebarContent({ onItemClick }: { onItemClick?: () => void }) {
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center h-14 px-4 border-b border-stone-200/80 dark:border-[var(--hairline)]">
        <div className="flex items-center gap-2.5 min-w-0">
          <img
            src="/brand/stockhive-icon-64.png"
            alt="StockHive"
            className="h-8 w-8 shrink-0 rounded-md object-contain"
          />
          <div className="leading-tight min-w-0">
            <div className="display text-[14px] font-semibold tracking-tight truncate">
              StockHive
            </div>
            <div className="text-[10.5px] text-stone-500 dark:text-[var(--ink-3)] mt-0.5 truncate">
              Admin &amp; Monitoring
            </div>
          </div>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto py-4 px-3">
        <div className="eyebrow px-2 mb-2 text-stone-400 dark:text-[var(--ink-3)]">
          Monitoring
        </div>
        <ul className="space-y-0.5">
          {ADMIN_NAV.map(({ to, label, desc, icon: Icon, end }) => (
            <li key={to}>
              <NavLink
                to={to}
                end={end}
                onClick={onItemClick}
                className={({ isActive }) =>
                  cn(
                    "relative flex items-center gap-3 px-2.5 rounded-md text-[13px] font-medium transition-colors duration-150 min-h-[44px] py-1.5",
                    isActive
                      ? "text-stone-900 dark:text-white bg-stone-100 dark:bg-white/[0.06]"
                      : "text-stone-600 dark:text-[var(--ink-2)] hover:text-stone-900 dark:hover:text-white hover:bg-stone-100/70 dark:hover:bg-white/[0.04]"
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    {isActive && (
                      <span
                        className="absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-r-full bg-stone-900 dark:bg-white"
                        aria-hidden
                      />
                    )}
                    <Icon
                      className={cn(
                        "h-[16px] w-[16px] shrink-0",
                        isActive
                          ? "text-stone-900 dark:text-white"
                          : "text-stone-500 dark:text-[var(--ink-3)]"
                      )}
                      aria-hidden
                    />
                    <span className="flex-1 min-w-0">
                      <span className="block truncate">{label}</span>
                      <span className="block text-[10.5px] text-stone-400 dark:text-[var(--ink-3)] mt-0.5 truncate">
                        {desc}
                      </span>
                    </span>
                  </>
                )}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      <div className="border-t border-stone-200/80 dark:border-[var(--hairline)] px-4 py-3.5">
        <div className="flex items-center gap-1.5 text-[10.5px] font-medium tracking-wider uppercase text-stone-500 dark:text-[var(--ink-3)]">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 anim-pulse-dot" />
          Read-only
        </div>
        <div className="text-[11px] text-stone-500 dark:text-[var(--ink-3)] mt-1">
          Observability only · no trading actions
        </div>
      </div>
    </div>
  );
}
