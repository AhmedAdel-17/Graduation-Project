import { useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import {
  BarChart3,
  Clock4,
  LineChart,
  LogOut,
  Menu,
  Moon,
  PanelLeft,
  Sun,
  Wallet,
  X,
  type LucideIcon,
} from "lucide-react";
import { useAuth } from "../auth/AuthProvider";
import { ADMIN_NAV } from "../../features/admin/layout/adminNav";

import { BrandLogo } from "../ui/BrandLogo";
import { LocaleToggle } from "../ui/LocaleToggle";
import { cn } from "../../lib/utils";
import { useTheme } from "../../hooks/useTheme";
import { useLocale, useT } from "../../lib/i18n";

interface NavItem {
  to: string;
  labelKey: string;
  icon: LucideIcon;
  end?: boolean;
}

// Maps each admin route to its i18n key so the sidebar chrome localizes even
// though the admin *pages* themselves stay English (by design).
const ADMIN_LABEL_KEYS: Record<string, string> = {
  "/admin": "admin.nav.overview",
  "/admin/system-health": "admin.nav.systemHealth",
  "/admin/pipeline-graph": "admin.nav.pipeline",
  "/admin/live": "admin.nav.live",
  "/admin/traces": "admin.nav.traces",
  "/admin/lineage": "admin.nav.lineage",
  "/admin/run-explorer": "admin.nav.runExplorer",
  "/admin/errors": "admin.nav.errors",
  "/admin/performance": "admin.nav.performance",
};

// Grouped navigation (design §10): a single app with a USER and an ADMIN
// section. The ADMIN group reuses ADMIN_NAV (the admin suite's own source of
// truth) so the two never drift, plus Backtesting which lives in the main app.
const USER_NAV: NavItem[] = [
  { to: "/predict", labelKey: "shell.nav.predict", icon: LineChart },
  // { to: "/portfolio", labelKey: "shell.nav.portfolio", icon: Wallet }, // Hidden per user request
  { to: "/history", labelKey: "shell.nav.history", icon: Clock4 },
];

const ADMIN_GROUP: NavItem[] = [
  ...ADMIN_NAV.map(({ to, icon, end }) => ({
    to,
    labelKey: ADMIN_LABEL_KEYS[to] ?? to,
    icon,
    end,
  })),
  { to: "/backtest", labelKey: "shell.nav.backtest", icon: BarChart3 },
];

const NAV_GROUPS: { eyebrowKey: string; items: NavItem[] }[] = [
  { eyebrowKey: "shell.group.user", items: USER_NAV },
  { eyebrowKey: "shell.group.admin", items: ADMIN_GROUP },
];

const STORAGE_KEY = "egx:sidebar:collapsed";

function todayLong(locale: string) {
  return new Date().toLocaleDateString(locale === "ar" ? "ar-EG" : "en-US", {
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

function NavRow({ item, onItemClick }: { item: NavItem; onItemClick?: () => void }) {
  const { to, labelKey, icon: Icon, end } = item;
  const t = useT();
  return (
    <li>
      <NavLink
        to={to}
        end={end ?? to === "/predict"}
        onClick={onItemClick}
        className={({ isActive }) =>
          cn(
            "relative flex items-center gap-3 px-2.5 rounded-md text-[13px] font-medium transition-colors duration-150 h-9",
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
                className="absolute start-0 top-1.5 bottom-1.5 w-[2px] rounded-e-full bg-stone-900 dark:bg-white"
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
            />
            <span className="truncate">{t(labelKey)}</span>
          </>
        )}
      </NavLink>
    </li>
  );
}

function SidebarContent({ onItemClick }: { onItemClick?: () => void }) {
  const t = useT();
  return (
    <div className="flex flex-col h-full">
      {/* Brand */}
      <div className="flex items-center h-14 px-4 border-b border-stone-200/80 dark:border-[var(--hairline)]">
        <div className="flex items-center gap-2.5 min-w-0">
          <BrandLogo className="h-8 w-8 shrink-0" />
          <div className="leading-tight min-w-0">
            <div className="display text-[15px] font-semibold tracking-tight truncate text-[var(--brand-navy)]">
              StockHive
            </div>
            <div className="text-[10.5px] text-stone-500 dark:text-[var(--ink-3)] mt-0.5 truncate">
              {t("shell.brand.desk")}
            </div>
          </div>
        </div>
      </div>

      {/* Nav — grouped USER / ADMIN sections */}
      <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-5">
        {NAV_GROUPS.map((group) => (
          <div key={group.eyebrowKey}>
            <div className="eyebrow px-2 mb-2 text-stone-400 dark:text-[var(--ink-3)]">
              {t(group.eyebrowKey)}
            </div>
            <ul className="space-y-0.5">
              {group.items.map((item) => (
                <NavRow key={item.to} item={item} onItemClick={onItemClick} />
              ))}
            </ul>
          </div>
        ))}
      </nav>

      {/* Footer — market status */}
      <div className="border-t border-stone-200/80 dark:border-[var(--hairline)] px-4 py-3.5">
        <div className="flex items-center gap-1.5 text-[10.5px] font-medium tracking-wider uppercase text-stone-500 dark:text-[var(--ink-3)]">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 anim-pulse-dot" />
          {t("shell.market")}
        </div>
        <div className="text-[12px] font-medium text-ink mt-1.5 num">
          {t("shell.hours")}
        </div>
        <div className="text-[11px] text-stone-500 dark:text-[var(--ink-3)] mt-0.5">
          {t("shell.constraints")}
        </div>
      </div>
    </div>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const { theme, toggle } = useTheme();
  const t = useT();
  const locale = useLocale();
  const { user, logout } = useAuth();
  const navigate = useNavigate();
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
          "hidden md:flex shrink-0 sticky top-0 h-screen overflow-hidden bg-[#FBFAF7] dark:bg-[var(--paper)] transition-[width,border-color] duration-200 ease-out",
          collapsed
            ? "w-0 border-e-0"
            : "w-[244px] border-e border-stone-200 dark:border-[var(--hairline)]"
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
            className="absolute start-0 top-0 h-full w-[260px] bg-[#FBFAF7] dark:bg-[var(--paper)] border-e border-stone-200 dark:border-[var(--hairline)] shadow-xl anim-fade-up"
            aria-label="Primary"
          >
            <button
              type="button"
              onClick={() => setMobileOpen(false)}
              aria-label={t("shell.closeMenu")}
              className="absolute top-3 end-3 h-7 w-7 inline-flex items-center justify-center rounded-md text-stone-500 hover:bg-stone-100 dark:text-[var(--ink-2)] dark:hover:bg-white/5"
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
        <div className="h-14 border-b border-stone-200 dark:border-[var(--hairline)] bg-[var(--bg)]/80 backdrop-blur-md sticky top-0 z-30">
          <div className="h-full px-5 lg:px-8 flex items-center justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              <button
                type="button"
                onClick={() => setMobileOpen(true)}
                aria-label={t("shell.openMenu")}
                className="md:hidden inline-flex items-center justify-center h-8 w-8 rounded-md text-stone-600 hover:bg-stone-100
                  dark:text-[var(--ink-2)] dark:hover:bg-white/5"
              >
                <Menu className="h-4 w-4" aria-hidden />
              </button>
              <button
                type="button"
                onClick={() => setCollapsed((v) => !v)}
                aria-label={collapsed ? t("shell.expandSidebar") : t("shell.collapseSidebar")}
                title={collapsed ? t("shell.expandSidebar") : t("shell.collapseSidebar")}
                aria-pressed={collapsed}
                className="hidden md:inline-flex items-center justify-center h-8 w-8 rounded-md text-stone-500 hover:text-stone-900 hover:bg-stone-100 transition-colors
                  dark:text-[var(--ink-3)] dark:hover:text-white dark:hover:bg-white/5"
              >
                <PanelLeft className="h-[16px] w-[16px]" aria-hidden />
              </button>
              <div className="h-4 w-px bg-stone-200 dark:bg-[var(--hairline)] hidden md:block" />
              <div className="text-[12px] text-stone-500 dark:text-[var(--ink-3)] truncate">
                <span className="text-ink-2">{todayLong(locale)}</span>
              </div>
            </div>
            <div className="flex items-center gap-2.5 text-[11px] text-stone-500 dark:text-[var(--ink-3)]">
              <LocaleToggle />
              <button
                type="button"
                onClick={toggle}
                aria-label={theme === "dark" ? t("shell.toLight") : t("shell.toDark")}
                title={theme === "dark" ? t("shell.toLight") : t("shell.toDark")}
                className="inline-flex items-center justify-center h-7 w-7 rounded-full border border-stone-200 bg-white hover:bg-stone-50 text-stone-600 transition-colors
                  dark:bg-[var(--paper)] dark:border-[var(--hairline)] dark:hover:bg-[var(--hairline)] dark:text-[var(--ink-2)]"
              >
                {theme === "dark" ? (
                  <Sun className="h-3.5 w-3.5" aria-hidden />
                ) : (
                  <Moon className="h-3.5 w-3.5" aria-hidden />
                )}
              </button>


              {/* ─── User display + logout ──────────────────────── */}
              {user && (
                <>
                  <div className="h-4 w-px bg-stone-200 dark:bg-[var(--hairline)] hidden sm:block" />
                  <div className="hidden sm:flex items-center gap-2 px-2.5 py-1 rounded-full border border-stone-200 bg-white dark:bg-[var(--paper)] dark:border-[var(--hairline)]">
                    {user.photoURL ? (
                      <img
                        src={user.photoURL}
                        alt=""
                        className="h-5 w-5 rounded-full object-cover"
                        referrerPolicy="no-referrer"
                      />
                    ) : (
                      <span
                        className="h-5 w-5 rounded-full flex items-center justify-center text-[10px] font-bold text-white"
                        style={{ background: "var(--brand-green)" }}
                      >
                        {(user.displayName || user.email || "U").charAt(0).toUpperCase()}
                      </span>
                    )}
                    <span className="text-[11px] font-medium text-stone-600 dark:text-[var(--ink-2)] max-w-[120px] truncate">
                      {user.displayName || user.email}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={async () => {
                      await logout();
                      navigate("/login", { replace: true });
                    }}
                    aria-label="Sign out"
                    title="Sign out"
                    className="inline-flex items-center justify-center h-7 w-7 rounded-full border border-stone-200 bg-white hover:bg-red-50 text-stone-500 hover:text-red-600 transition-colors
                      dark:bg-[var(--paper)] dark:border-[var(--hairline)] dark:hover:bg-red-900/20 dark:text-[var(--ink-3)] dark:hover:text-red-400"
                  >
                    <LogOut className="h-3.5 w-3.5" aria-hidden />
                  </button>
                </>
              )}
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
