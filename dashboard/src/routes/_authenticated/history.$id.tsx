import { createFileRoute } from "@tanstack/react-router";
import { HistoryDetailPage } from "@/components/history/HistoryDetailPage";

export const Route = createFileRoute("/_authenticated/history/$id")({
  component: HistoryDetailPage,
});
