import { createFileRoute } from "@tanstack/react-router";
import { BacktestPage } from "@/components/backtest/BacktestPage";

/** Index route — matches `/backtest` exactly. */
export const Route = createFileRoute("/_authenticated/backtest/")({
  component: BacktestPage,
});
