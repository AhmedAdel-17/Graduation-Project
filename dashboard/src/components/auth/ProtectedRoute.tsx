import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./AuthProvider";

/**
 * Route guard — renders children only if authenticated.
 * While Firebase is resolving the initial auth state, shows a branded loading
 * spinner. Once resolved, unauthenticated users are redirected to /login with
 * a returnTo state so they land back after signing in.
 */
export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--bg)]">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 rounded-full border-2 border-[var(--brand-green)] border-t-transparent animate-spin" />
          <span className="text-sm text-stone-500 dark:text-[var(--ink-3)]">
            Loading…
          </span>
        </div>
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" state={{ returnTo: location.pathname }} replace />;
  }

  return <>{children}</>;
}
