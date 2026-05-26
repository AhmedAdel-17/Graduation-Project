import { createFileRoute } from "@tanstack/react-router";
import { HistoryPage } from "@/components/history/HistoryPage";

/**
 * Index route — matches `/history` exactly and renders the list view.
 */
export const Route = createFileRoute("/_authenticated/history/")({
  component: HistoryPage,
});
