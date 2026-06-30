import { Routes, Route, Navigate } from "react-router-dom";
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
