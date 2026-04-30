import { useMutation } from "@tanstack/react-query";
import { endpoints } from "../services/api";
import type { PredictionResult } from "../services/api/types";

export function useRunPrediction() {
  return useMutation<PredictionResult, Error, string | undefined>({
    mutationFn: (ticker) => endpoints.runPrediction(ticker),
  });
}
