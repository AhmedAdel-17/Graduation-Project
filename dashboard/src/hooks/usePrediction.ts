import { useMutation, useQuery } from "@tanstack/react-query";
import { endpoints } from "../services/api";
import type { PredictionResult } from "../services/api/types";

export function useRunPrediction() {
  return useMutation<PredictionResult, Error, string | undefined>({
    mutationFn: (ticker) => endpoints.runPrediction(ticker),
  });
}

export function useRunFullPipeline() {
  return useMutation<PredictionResult, Error, { ticker: string; profileId?: string }>({
    mutationFn: ({ ticker, profileId }) =>
      endpoints.runFullPipeline(ticker, profileId),
  });
}

export function usePastPrediction(sessionId?: string) {
  return useQuery<PredictionResult, Error>({
    queryKey: ["prediction", sessionId],
    queryFn: () => endpoints.getPastPrediction(sessionId!),
    enabled: !!sessionId,
  });
}
