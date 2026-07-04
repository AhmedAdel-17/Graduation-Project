import { useEffect } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { getLocale } from "../../lib/i18n";
import { AdminOverview } from "./overview/AdminOverview";
import { AgentMonitorPage } from "./agent-monitor/AgentMonitorPage";
import { LiveExecutionPage } from "./live/LiveExecutionPage";
import { TraceInspectorPage } from "./traces/TraceInspectorPage";
import { RunExplorerPage } from "./run-explorer/RunExplorerPage";
import { DataLineagePage } from "./lineage/DataLineagePage";
import { ErrorCenterPage } from "./errors/ErrorCenterPage";
import { PerformancePage } from "./performance/PerformancePage";
import { MonitoringPage } from "../monitoring/MonitoringPage";
import { AdminShell } from "./layout/AdminShell";

function SystemHealthPage() {
  return (
    <AdminShell title="System Health" subtitle="Infrastructure, data freshness & pipeline metrics">
      <MonitoringPage />
    </AdminShell>
  );
}

// Mounted by App.tsx at /admin/*. Owns its own shell + sub-router so the live
// dashboard routes (/, /backtest, /history) are completely untouched.
export default function AdminApp() {
  // The admin suite is English-only by design. Force LTR while it is mounted
  // (even if the user selected Arabic in the main app), and restore the
  // locale's direction on exit. Applied at the document level so portalled
  // overlays (drawers, dialogs) are covered too.
  useEffect(() => {
    const html = document.documentElement;
    html.dir = "ltr";
    html.lang = "en";
    return () => {
      html.dir = getLocale() === "ar" ? "rtl" : "ltr";
      html.lang = getLocale();
    };
  }, []);

  return (
    <Routes>
      <Route path="/admin" element={<AdminOverview />} />
      <Route path="/admin/system-health" element={<SystemHealthPage />} />
      <Route path="/admin/pipeline-graph" element={<AgentMonitorPage />} />
      <Route path="/admin/live" element={<LiveExecutionPage />} />
      <Route path="/admin/traces" element={<TraceInspectorPage />} />
      <Route path="/admin/lineage" element={<DataLineagePage />} />
      <Route path="/admin/run-explorer" element={<RunExplorerPage />} />
      <Route path="/admin/errors" element={<ErrorCenterPage />} />
      <Route path="/admin/performance" element={<PerformancePage />} />
      {/* Legacy routes — redirect to new names */}
      <Route path="/admin/agent-monitor" element={<Navigate to="/admin/pipeline-graph" replace />} />
      <Route path="/admin/monitoring" element={<Navigate to="/admin/system-health" replace />} />
      <Route path="/admin/*" element={<Navigate to="/admin" replace />} />
    </Routes>
  );
}
