import { NavLink } from "react-router-dom";
import {
  Activity,
  BarChart3,
  ChevronRight,
  CircleDot,
  Clock4,
} from "lucide-react";
import { cn } from "../../lib/utils";

const NAV = [
  {
    to: "/",
    label: "Analysis",
    sublabel: "Live multi-agent thesis",
    icon: Activity,
  },
  {
    to: "/backtest",
    label: "Backtesting",
    sublabel: "Replay against history",
    icon: BarChart3,
  },
  {
    to: "/history",
    label: "History",
    sublabel: "Past runs & traces",
    icon: Clock4,
  },
];

function todayLong() {
  return new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "short",
    day: "numeric",
  });
}

export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex bg-[var(--bg)] text-ink">
      {/* ─── Sidebar ─────────────────────────────────────────────── */}
      <aside className="hidden md:flex flex-col w-[260px] shrink-0 border-r border-stone-200 bg-[#FBFAF7] sticky top-0 h-screen">
        {/* Brand */}
        <div className="px-6 pt-7 pb-6 border-b border-stone-200/80">
          <div className="flex items-center gap-3">
            <div className="relative h-10 w-10 rounded-xl bg-gradient-to-br from-stone-900 to-stone-700 flex items-center justify-center shadow-[0_4px_12px_-2px_rgba(0,0,0,0.18)]">
              <div className="h-2 w-2 rounded-full bg-emerald-400 absolute -top-0.5 -right-0.5 ring-2 ring-[#FBFAF7] anim-pulse-dot" />
              <span className="display text-white text-base font-semibold">
                E
              </span>
            </div>
            <div className="leading-tight">
              <div className="display text-[15px] font-semibold tracking-tight">
                EGX Intelligence
              </div>
              <div className="text-[11px] text-stone-500 mt-0.5">
                Research console · v2
              </div>
            </div>
          </div>
        </div>

        {/* Nav */}
        <div className="px-3 py-5 flex-1">
          <div className="eyebrow px-3 mb-2">Workspaces</div>
          <nav className="space-y-0.5">
            {NAV.map(({ to, label, sublabel, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                end={to === "/"}
                className={({ isActive }) =>
                  cn(
                    "group flex items-center gap-3 px-3 py-2.5 rounded-xl transition-all duration-200",
                    isActive
                      ? "bg-white border border-stone-200/80 shadow-[0_1px_2px_rgba(0,0,0,0.03)]"
                      : "border border-transparent hover:bg-stone-100/70"
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <div
                      className={cn(
                        "h-8 w-8 rounded-lg flex items-center justify-center transition-colors",
                        isActive
                          ? "bg-stone-900 text-white"
                          : "bg-stone-200/60 text-stone-600 group-hover:bg-stone-200"
                      )}
                    >
                      <Icon className="h-4 w-4" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-[13px] font-medium text-ink leading-tight">
                        {label}
                      </div>
                      <div className="text-[11px] text-stone-500 leading-tight mt-0.5">
                        {sublabel}
                      </div>
                    </div>
                    {isActive && (
                      <ChevronRight className="h-3.5 w-3.5 text-stone-400" />
                    )}
                  </>
                )}
              </NavLink>
            ))}
          </nav>
        </div>

        {/* Footer card */}
        <div className="px-3 pb-5">
          <div className="rounded-xl border border-stone-200/80 bg-white px-4 py-3">
            <div className="flex items-center gap-2 text-[11px] text-stone-500">
              <CircleDot className="h-3 w-3 text-emerald-500 anim-pulse-dot" />
              <span>EGX market</span>
            </div>
            <div className="text-[13px] font-medium mt-1 text-ink">
              10:00 – 14:30 EGT
            </div>
            <div className="text-[11px] text-stone-500 mt-1">
              Long-only · EGP · T+2
            </div>
          </div>
        </div>
      </aside>

      {/* ─── Main column ─────────────────────────────────────────── */}
      <div className="flex-1 min-w-0 flex flex-col">
        {/* Top strip */}
        <div className="h-14 border-b border-stone-200 bg-[var(--bg)]/80 backdrop-blur-md sticky top-0 z-30">
          <div className="h-full px-8 flex items-center justify-between">
            <div className="text-[12px] text-stone-500">
              <span className="text-stone-400">›</span>{" "}
              <span className="text-ink-2">{todayLong()}</span>
            </div>
            <div className="flex items-center gap-3 text-[11px] text-stone-500">
              <span className="px-2.5 py-1 rounded-full border border-stone-200 bg-white">
                EGX-30
              </span>
              <span className="px-2.5 py-1 rounded-full border border-stone-200 bg-white">
                EGP
              </span>
              <span className="px-2.5 py-1 rounded-full border border-emerald-200 bg-emerald-50 text-emerald-700 flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 anim-pulse-dot" />
                Online
              </span>
            </div>
          </div>
        </div>

        <main className="flex-1 px-8 py-10">
          <div className="max-w-[1280px] mx-auto">{children}</div>
        </main>
      </div>
    </div>
  );
}
