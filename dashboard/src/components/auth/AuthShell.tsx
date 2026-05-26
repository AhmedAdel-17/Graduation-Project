import { Link } from "@tanstack/react-router";
import { Activity } from "lucide-react";
import type { ReactNode } from "react";

export function AuthShell({
  mode,
  children,
}: {
  mode: "login" | "signup";
  children: ReactNode;
}) {
  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden bg-background px-4 py-12">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_-20%,oklch(0.74_0.17_162/0.18),transparent_60%)]" />
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_bottom,transparent_0%,oklch(0.14_0.005_270)_100%)]" />

      <div className="relative z-10 mb-8 flex items-center gap-2">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <Activity className="h-5 w-5" strokeWidth={2.5} />
        </div>
        <span className="text-lg font-semibold tracking-tight">TradingAgents</span>
      </div>

      <div className="relative z-10 w-full max-w-md">
        <div className="mb-6 flex rounded-lg border border-border bg-card p-1">
          <Link
            to="/login"
            className="flex-1 rounded-md px-4 py-2 text-center text-sm font-medium transition-colors data-[active=true]:bg-secondary data-[active=true]:text-foreground"
            data-active={mode === "login"}
          >
            Sign in
          </Link>
          <Link
            to="/signup"
            className="flex-1 rounded-md px-4 py-2 text-center text-sm font-medium transition-colors data-[active=true]:bg-secondary data-[active=true]:text-foreground"
            data-active={mode === "signup"}
          >
            Sign up
          </Link>
        </div>
        <div className="rounded-xl border border-border bg-card p-6 shadow-2xl shadow-black/40">
          {children}
        </div>
        <p className="mt-6 text-center text-xs text-muted-foreground">
          EGX research dashboard · Mocked backend for preview
        </p>
      </div>
    </div>
  );
}
