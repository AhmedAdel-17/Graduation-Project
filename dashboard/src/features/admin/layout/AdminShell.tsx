import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Menu, Moon, Sun, X } from "lucide-react";
import { cn } from "../../../lib/utils";
import { useTheme } from "../../../hooks/useTheme";
import { AdminSidebarContent } from "./AdminSidebar";

export function AdminShell({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  const { theme, toggle } = useTheme();
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    document.body.style.overflow = mobileOpen ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [mobileOpen]);

  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMobileOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileOpen]);

  return (
    <div className="min-h-screen flex bg-[var(--bg)] text-ink">
      {/* Desktop sidebar */}
      <aside
        className="hidden md:flex shrink-0 sticky top-0 h-screen w-[244px] overflow-hidden bg-[#FBFAF7] dark:bg-[var(--paper)] border-r border-stone-200 dark:border-[var(--hairline)]"
        aria-label="Admin navigation"
      >
        <div className="w-[244px] shrink-0">
          <AdminSidebarContent />
        </div>
      </aside>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="md:hidden fixed inset-0 z-50">
          <div
            className="absolute inset-0 bg-stone-900/40 backdrop-blur-[2px]"
            onClick={() => setMobileOpen(false)}
            aria-hidden
          />
          <aside
            className="absolute left-0 top-0 h-full w-[260px] bg-[#FBFAF7] dark:bg-[var(--paper)] border-r border-stone-200 dark:border-[var(--hairline)] shadow-xl anim-fade-up"
            aria-label="Admin navigation"
          >
            <button
              type="button"
              onClick={() => setMobileOpen(false)}
              aria-label="Close menu"
              className="absolute top-3 right-3 h-7 w-7 inline-flex items-center justify-center rounded-md text-stone-500 hover:bg-stone-100 dark:text-[var(--ink-2)] dark:hover:bg-white/5"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
            <AdminSidebarContent onItemClick={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}

      {/* Main column */}
      <div className="flex-1 min-w-0 flex flex-col">
        <div className="h-14 border-b border-stone-200 dark:border-[var(--hairline)] bg-[var(--bg)]/80 backdrop-blur-md sticky top-0 z-30">
          <div className="h-full px-4 lg:px-8 flex items-center justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              <button
                type="button"
                onClick={() => setMobileOpen(true)}
                aria-label="Open menu"
                className="md:hidden inline-flex items-center justify-center h-8 w-8 rounded-md text-stone-600 hover:bg-stone-100 dark:text-[var(--ink-2)] dark:hover:bg-white/5"
              >
                <Menu className="h-4 w-4" aria-hidden />
              </button>
              <div className="min-w-0">
                <h1 className="text-[14px] font-semibold tracking-tight text-ink truncate">
                  {title}
                </h1>
                {subtitle && (
                  <p className="text-[11.5px] text-stone-500 dark:text-[var(--ink-3)] truncate">
                    {subtitle}
                  </p>
                )}
              </div>
            </div>
            <div className="flex items-center gap-2.5 shrink-0">
              {actions}
              <button
                type="button"
                onClick={toggle}
                aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
                title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
                className="inline-flex items-center justify-center h-7 w-7 rounded-full border border-stone-200 bg-white hover:bg-stone-50 text-stone-600 transition-colors dark:bg-[var(--paper)] dark:border-[var(--hairline)] dark:hover:bg-[var(--hairline)] dark:text-[var(--ink-2)]"
              >
                {theme === "dark" ? (
                  <Sun className="h-3.5 w-3.5" aria-hidden />
                ) : (
                  <Moon className="h-3.5 w-3.5" aria-hidden />
                )}
              </button>
              <Link
                to="/"
                className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-stone-200 bg-white text-[11px] text-stone-600 hover:text-stone-900 hover:bg-stone-50 transition-colors dark:bg-[var(--paper)] dark:border-[var(--hairline)] dark:text-[var(--ink-2)] dark:hover:text-white"
              >
                <ArrowLeft className="h-3 w-3" aria-hidden />
                Dashboard
              </Link>
            </div>
          </div>
        </div>

        <main className={cn("flex-1 px-4 lg:px-8 py-6 lg:py-8")}>
          <div className="max-w-[1400px] mx-auto anim-fade-up">{children}</div>
        </main>
      </div>
    </div>
  );
}
