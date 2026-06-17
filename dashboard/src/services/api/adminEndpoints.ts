import { api } from "./client";
import type { AdminErrorsResponse, AdminMetricsResponse } from "./adminTypes";

// Read-only admin aggregation endpoints. Reuse the shared fetch client so the
// Vite dev proxy + error normalization apply identically to the rest of the app.
export const adminEndpoints = {
  metrics: (days?: number) =>
    api.get<AdminMetricsResponse>(`/admin/metrics${days ? `?days=${days}` : ""}`),
  errors: (days?: number) =>
    api.get<AdminErrorsResponse>(`/admin/errors${days ? `?days=${days}` : ""}`),
};
