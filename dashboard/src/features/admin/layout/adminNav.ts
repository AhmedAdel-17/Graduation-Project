import {
  Activity,
  AlertTriangle,
  BarChart3,
  GitBranch,
  HeartPulse,
  LayoutDashboard,
  ScanSearch,
  Share2,
  Table2,
  type LucideIcon,
} from "lucide-react";

export interface AdminNavItem {
  to: string;
  label: string;
  desc: string;
  icon: LucideIcon;
  end?: boolean;
}

// Single source of truth for the admin nav. Kept in a data-only module so the
// sidebar component file satisfies react-refresh/only-export-components.
export const ADMIN_NAV: AdminNavItem[] = [
  { to: "/admin", label: "Overview", desc: "System health", icon: LayoutDashboard, end: true },
  { to: "/admin/system-health", label: "System Health", desc: "Infra & data freshness", icon: HeartPulse },
  { to: "/admin/pipeline-graph", label: "Pipeline Graph", desc: "Agent architecture", icon: GitBranch },
  { to: "/admin/live", label: "Live Execution", desc: "Real-time run", icon: Activity },
  { to: "/admin/traces", label: "Trace Inspector", desc: "LLM traces", icon: ScanSearch },
  { to: "/admin/lineage", label: "Data Lineage", desc: "Data flow", icon: Share2 },
  { to: "/admin/run-explorer", label: "Run Explorer", desc: "History", icon: Table2 },
  { to: "/admin/errors", label: "Error Center", desc: "Diagnostics", icon: AlertTriangle },
  { to: "/admin/performance", label: "Performance", desc: "Analytics", icon: BarChart3 },
];
