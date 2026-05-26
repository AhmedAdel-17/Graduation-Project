import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";
import { RunPage } from "@/components/run/RunPage";

export const Route = createFileRoute("/_authenticated/run")({
  validateSearch: z.object({ ticker: z.string().optional() }).parse,
  component: RunPage,
});
