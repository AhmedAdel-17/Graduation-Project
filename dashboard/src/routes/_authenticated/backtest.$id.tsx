import { createFileRoute } from "@tanstack/react-router";
import { BacktestDetailPage } from "@/components/backtest/BacktestDetailPage";

export const Route = createFileRoute("/_authenticated/backtest/$id")({
  component: BacktestDetailPage,
});
