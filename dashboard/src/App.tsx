import { lazy, Suspense } from "react";
import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import { Shell } from "./components/layout/Shell";
import { HomeScreen } from "./features/home/HomeScreen";
import { BacktestScreen } from "./features/backtest/BacktestScreen";
import { HistoryScreen } from "./features/history/HistoryScreen";

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

// The original live dashboard, wrapped in Shell. Stock Prediction now lives at
// /predict; "/" redirects there so existing bookmarks keep working.
function MainApp() {
  return (
    <Shell>
      <Suspense fallback={<PageLoading />}>
        <Routes>
          <Route path="/" element={<Navigate to="/predict" replace />} />
          <Route path="/predict" element={<HomeScreen />} />
          <Route path="/portfolio" element={<AssistantPage />} />
          <Route path="/portfolio/__blocks" element={<BlocksDevPage />} />
          <Route path="/backtest" element={<BacktestScreen />} />
          <Route path="/history" element={<HistoryScreen />} />
          <Route path="*" element={<Navigate to="/predict" replace />} />
        </Routes>
      </Suspense>
    </Shell>
  );
}

export default function App() {
  const isAdmin = useLocation().pathname.startsWith("/admin");
  if (isAdmin) {
    return (
      <Suspense fallback={<AdminLoading />}>
        <AdminApp />
      </Suspense>
    );
  }
  return <MainApp />;
}
