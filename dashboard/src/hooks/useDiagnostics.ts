import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { endpoints } from "../services/api";
import type {
  ConfigResponse,
  ConfigUpdateRequest,
  ConfigUpdateResponse,
  FingerprintsResponse,
  HealthResponse,
  PromptsResponse,
} from "../services/api/types";

/**
 * Live health-payload poll. Slower cadence than the TopBar pulse so the
 * Diagnostics page can decode the whole diagnostics blob without thrash.
 */
export function useHealth(autoRefetch = true) {
  return useQuery<HealthResponse>({
    queryKey: ["diagnostics", "health"],
    queryFn: () => endpoints.health(),
    refetchInterval: autoRefetch ? 15_000 : false,
    staleTime: 10_000,
    retry: 1,
  });
}

export function usePrompts() {
  return useQuery<PromptsResponse>({
    queryKey: ["diagnostics", "prompts"],
    queryFn: () => endpoints.diagnosticsPrompts(),
    staleTime: 60_000,
    retry: 0,
  });
}

export function useFingerprints(days: number) {
  return useQuery<FingerprintsResponse>({
    queryKey: ["diagnostics", "fingerprints", days],
    queryFn: () => endpoints.diagnosticsFingerprints(days),
    staleTime: 60_000,
    retry: 0,
  });
}

export function useConfig() {
  return useQuery<ConfigResponse>({
    queryKey: ["config"],
    queryFn: () => endpoints.config(),
    staleTime: 60_000,
    retry: 1,
  });
}

export function useUpdateConfig() {
  const qc = useQueryClient();
  return useMutation<ConfigUpdateResponse, Error, ConfigUpdateRequest>({
    mutationFn: (req) => endpoints.updateConfig(req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config"] });
      qc.invalidateQueries({ queryKey: ["diagnostics", "health"] });
    },
  });
}
