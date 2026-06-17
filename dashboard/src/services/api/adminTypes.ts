// Payload shapes for the read-only admin aggregation endpoints
// (server/api_server.py: /api/admin/metrics, /api/admin/errors).

export interface AdminDailyRun {
  day: string | null;
  runs: number;
}

export interface AdminAgentMetric {
  agent_name: string | null;
  executions: number;
  avg_confidence: number | null;
  last_seen?: string | null;
}

export interface AdminMetricsResponse {
  source: "postgres" | "none";
  reason?: string;
  days: number;
  total_runs: number;
  total_events: number;
  avg_confidence: number | null;
  daily_runs: AdminDailyRun[];
  decision_counts: Record<string, number>;
  per_agent: AdminAgentMetric[];
}

export type ErrorSeverity = "critical" | "error" | "warning" | "info" | string;
export type ErrorCategory = "agent" | "llm" | "api" | "database" | "timeout" | "risk" | "system" | string;

export interface AdminErrorRecord {
  timestamp: string | null;
  session_id?: string | null;
  ticker?: string | null;
  agent?: string | null;
  error_type: string;
  severity: ErrorSeverity;
  category: ErrorCategory;
  message: string;
  stack_trace?: string | null;
  suggested_resolution?: string | null;
}

export interface AdminErrorsResponse {
  source: "postgres" | "none";
  reason?: string;
  days: number;
  errors: AdminErrorRecord[];
  total: number;
}
