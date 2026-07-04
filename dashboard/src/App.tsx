import { lazy, Suspense } from "react";
import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import { Shell } from "./components/layout/Shell";
import { ProtectedRoute } from "./components/auth/ProtectedRoute";
import { HomeScreen } from "./features/home/HomeScreen";
import { BacktestScreen } from "./features/backtest/BacktestScreen";
import { HistoryScreen } from "./features/history/HistoryScreen";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";

// Admin Monitoring Suite — isolated, lazy-loaded, owns its own shell.
// Lives entirely under /admin/* so the live dashboard below is untouched.
const AdminApp = lazy(() => import("./features/admin/AdminApp"));

// Portfolio Assistant — lazy so its (eventually heavy: recharts, chat) bundle
// loads only when /portfolio is visited.
const AssistantPage = lazy(() =>
  import("./features/assistant/AssistantPage").then((m) => ({ default: m.AssistantPage }))
);

// Dev-only block gallery / visual-regression page for the chat blocks (P7).
const BlocksDevPage = lazy(() =>
  import("./features/assistant/BlocksDevPage").then((m) => ({ default: m.BlocksDevPage }))
);

function AdminLoading() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--bg)] text-stone-500 dark:text-[var(--ink-3)]">
      <div className="flex items-center gap-2 text-sm">
        <span className="h-2 w-2 rounded-full bg-emerald-500 anim-pulse-dot" />
        Loading admin suite…
      </div>
    </div>
  );
}

function PageLoading() {
  return (
    <div className="flex items-center justify-center py-24 text-stone-500 dark:text-[var(--ink-3)]">
      <div className="flex items-center gap-2 text-sm">
        <span className="h-2 w-2 rounded-full bg-emerald-500 anim-pulse-dot" />
        Loading…
      </div>
    </div>
  );
}

// The original live dashboard, wrapped in Shell + auth guard.
function MainApp() {
  return (
    <ProtectedRoute>
      <Shell>
        <Suspense fallback={<PageLoading />}>
          <Routes>
            <Route path="/" element={<Navigate to="/predict" replace />} />
            <Route path="/predict" element={<HomeScreen />} />
            <Route path="/prediction/:sessionId" element={<HomeScreen />} />
            <Route path="/portfolio" element={<AssistantPage />} />
            <Route path="/portfolio/__blocks" element={<BlocksDevPage />} />
            <Route path="/backtest" element={<BacktestScreen />} />
            <Route path="/history" element={<HistoryScreen />} />
            <Route path="*" element={<Navigate to="/predict" replace />} />
          </Routes>
        </Suspense>
      </Shell>
    </ProtectedRoute>
  );
}

export default function App() {
  const location = useLocation();
  const isAdmin = location.pathname.startsWith("/admin");
  const isAuth =
    location.pathname === "/login" || location.pathname === "/register";

  // Auth pages — no shell, no guard
  if (isAuth) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
      </Routes>
    );
  }

  // Admin suite — its own shell
  if (isAdmin) {
    return (
      <Suspense fallback={<AdminLoading />}>
        <AdminApp />
      </Suspense>
    );
  }

  // Main dashboard — wrapped in ProtectedRoute + Shell
  return <MainApp />;
}
