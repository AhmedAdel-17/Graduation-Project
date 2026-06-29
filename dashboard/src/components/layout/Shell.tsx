import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import {
  Activity,
  BarChart3,
  Briefcase,
  Clock4,
  Menu,
  Monitor,
  Moon,
  PanelLeft,
  Sun,
  X,
} from "lucide-react";
import { cn } from "../../lib/utils";
import { useTheme } from "../../hooks/useTheme";

interface NavSection {
  heading?: string;
  items: { to: string; label: string; icon: typeof Activity; end?: boolean }[];
}

const NAV_SECTIONS: NavSection[] = [
  {
    heading: "Analyze",
    items: [
      { to: "/", label: "Home", icon: Activity, end: true },
      { to: "/decisions", label: "Recommendations", icon: Briefcase },
    ],
  },
  {
    heading: "Developer",
    items: [
      { to: "/monitoring", label: "Monitoring", icon: Monitor },
      { to: "/backtest", label: "Backtesting", icon: BarChart3 },
      { to: "/history", label: "Audit log", icon: Clock4 },
    ],
  },
];

const STORAGE_KEY = "egx:sidebar:collapsed";

function todayLong() {
  return new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "short",
    day: "numeric",
  });
}

function useCollapsed() {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  });
  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [collapsed]);
  return [collapsed, setCollapsed] as const;
}

function SidebarContent({ onItemClick }: { onItemClick?: () => void }) {
  return (
    <div className="flex flex-col h-full">
      {/* Brand */}
      <div className="flex items-center min-h-[56px] px-4 border-b border-[var(--hairline)] bg-white/45 dark:bg-white/[0.03]">
        <div className="flex items-center gap-2 min-w-0">
          <img
            src="/brand/stockhive-icon.png"
            alt="StockHive"
            className="h-6 w-6 shrink-0"
          />
          <div className="leading-tight min-w-0">
            <div className="display text-[15px] font-semibold truncate text-[var(--ink)]">
              StockHive
            </div>
            <div className="mt-0.5 text-[8px] font-semibold uppercase tracking-[0.12em] text-[var(--accent)]">
              Intelligence · Insight · Invest
            </div>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-4 px-3">
        {NAV_SECTIONS.map((section, si) => (
          <div key={si} className={cn(si > 0 && "mt-5")}>
            {section.heading && (
              <div className="eyebrow px-2 mb-2 text-stone-400 dark:text-[var(--ink-3)]">
                {section.heading}
              </div>
            )}
            <ul className="space-y-0.5">
              {section.items.map(({ to, label, icon: Icon, end }) => (
                <li key={to}>
                  <NavLink
                    to={to}
                    end={end}
                    onClick={onItemClick}
                    className={({ isActive }) =>
                      cn(
                        "relative flex items-center gap-3 px-2.5 rounded-md text-[13px] font-medium transition-colors duration-150 h-9",
                        isActive
                          ? "text-[var(--ink)] bg-white/75 shadow-sm border border-[var(--hairline)] dark:bg-white/[0.08]"
                          : "text-[var(--ink-2)] hover:text-[var(--ink)] hover:bg-white/55 dark:hover:bg-white/[0.04]"
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        {isActive && (
                          <span
                            className="absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-r-full bg-[var(--accent)]"
                            aria-hidden
                          />
                        )}
                        <Icon
                          className={cn(
                            "h-[16px] w-[16px] shrink-0",
                            isActive
                              ? "text-[var(--accent)]"
                              : "text-[var(--ink-3)]"
                          )}
                        />
                        <span className="truncate">{label}</span>
                      </>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      {/* Footer — market status */}
      <div className="border-t border-[var(--hairline)] px-4 py-3.5 bg-white/35 dark:bg-white/[0.02]">
        <div className="flex items-center gap-1.5 text-[10.5px] font-medium tracking-wider uppercase text-[var(--ink-3)]">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 anim-pulse-dot" />
          EGX market
        </div>
        <div className="text-[12px] font-medium text-ink mt-1.5 num">
          10:00 – 14:30 EGT
        </div>
        <div className="text-[11px] text-[var(--ink-3)] mt-0.5">
          Long-only · EGP · T+2
        </div>
      </div>
    </div>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const { theme, toggle } = useTheme();
  const [collapsed, setCollapsed] = useCollapsed();
  const [mobileOpen, setMobileOpen] = useState(false);

  // Lock body scroll when mobile drawer open
  useEffect(() => {
    document.body.style.overflow = mobileOpen ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [mobileOpen]);

  // Close drawer on Escape
  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileOpen]);

  return (
    <div className="min-h-screen flex bg-[var(--bg)] text-ink">
      {/* ─── Desktop sidebar — fully hides when collapsed ──────────── */}
      <aside
        className={cn(
          "hidden md:flex shrink-0 sticky top-0 h-screen overflow-hidden bg-[#F2FAF7]/85 backdrop-blur-xl dark:bg-[var(--paper)] transition-[width,border-color] duration-200 ease-out",
          collapsed
            ? "w-0 border-r-0"
            : "w-[244px] border-r border-[var(--hairline)]"
        )}
        aria-label="Primary"
        aria-hidden={collapsed}
      >
        <div className="w-[244px] shrink-0">
          <SidebarContent />
        </div>
      </aside>

      {/* ─── Mobile drawer ──────────────────────────────────────── */}
      {mobileOpen && (
        <div className="md:hidden fixed inset-0 z-50">
          <div
            className="absolute inset-0 bg-stone-900/40 backdrop-blur-[2px]"
            onClick={() => setMobileOpen(false)}
            aria-hidden
          />
          <aside
            className="absolute left-0 top-0 h-full w-[260px] bg-[#F2FAF7] dark:bg-[var(--paper)] border-r border-[var(--hairline)] shadow-xl anim-fade-up"
            aria-label="Primary"
          >
            <button
              type="button"
              onClick={() => setMobileOpen(false)}
              aria-label="Close menu"
                className="absolute top-3 right-3 h-7 w-7 inline-flex items-center justify-center rounded-md text-[var(--ink-3)] hover:bg-white/70 dark:text-[var(--ink-2)] dark:hover:bg-white/5"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
            <SidebarContent onItemClick={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}

      {/* ─── Main column ─────────────────────────────────────────── */}
      <div className="flex-1 min-w-0 flex flex-col">
        {/* Top strip */}
        <div className="h-14 border-b border-[var(--hairline)] bg-[var(--bg)]/82 backdrop-blur-md sticky top-0 z-30">
          <div className="h-full px-5 lg:px-8 flex items-center justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              <button
                type="button"
                onClick={() => setMobileOpen(true)}
                aria-label="Open menu"
                className="md:hidden inline-flex items-center justify-center h-8 w-8 rounded-md text-stone-600 hover:bg-stone-100
                  dark:text-[var(--ink-2)] dark:hover:bg-white/5"
              >
                <Menu className="h-4 w-4" aria-hidden />
              </button>
              <button
                type="button"
                onClick={() => setCollapsed((v) => !v)}
                aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                aria-pressed={collapsed}
                className="hidden md:inline-flex items-center justify-center h-8 w-8 rounded-md text-[var(--ink-3)] hover:text-[var(--ink)] hover:bg-white/70 transition-colors
                  dark:text-[var(--ink-3)] dark:hover:text-white dark:hover:bg-white/5"
              >
                <PanelLeft className="h-[16px] w-[16px]" aria-hidden />
              </button>
              <div className="h-4 w-px bg-[var(--hairline)] hidden md:block" />
              <div className="text-[12px] text-[var(--ink-3)] truncate">
                <span className="text-ink-2">{todayLong()}</span>
              </div>
            </div>
            <div className="flex items-center gap-2.5 text-[11px] text-[var(--ink-3)]">
              <button
                type="button"
                onClick={toggle}
                aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
                title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
                className="inline-flex items-center justify-center h-7 w-7 rounded-full border border-stone-200 bg-white hover:bg-stone-50 text-stone-600 transition-colors
                  dark:bg-[var(--paper)] dark:border-[var(--hairline)] dark:hover:bg-[var(--hairline)] dark:text-[var(--ink-2)]"
              >
                {theme === "dark" ? (
                  <Sun className="h-3.5 w-3.5" aria-hidden />
                ) : (
                  <Moon className="h-3.5 w-3.5" aria-hidden />
                )}
              </button>
              <span className="hidden sm:inline-flex px-2.5 py-1 rounded-full border border-[var(--hairline)] bg-white/70 dark:bg-[var(--paper)]">
                EGX-30
              </span>
              <span className="hidden sm:inline-flex px-2.5 py-1 rounded-full border border-[var(--hairline)] bg-white/70 dark:bg-[var(--paper)]">
                EGP
              </span>
              <span className="inline-flex px-2.5 py-1 rounded-full border border-emerald-200 bg-emerald-50 text-emerald-700 items-center gap-1.5
                dark:bg-emerald-900/20 dark:border-emerald-900/40 dark:text-emerald-300">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 anim-pulse-dot" />
                Online
              </span>
            </div>
          </div>
        </div>

        <main className="flex-1 px-5 lg:px-8 py-8 lg:py-10">
          <div className="max-w-[1280px] mx-auto">{children}</div>
        </main>
      </div>
    </div>
  );
}
