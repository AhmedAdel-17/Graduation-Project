import { createFileRoute, Outlet } from "@tanstack/react-router";

/**
 * Parent route for /backtest — just an Outlet so child routes can render.
 * The list/config view lives in `backtest.index.tsx`, detail in `backtest.$id.tsx`.
 */
export const Route = createFileRoute("/_authenticated/backtest")({
  component: () => <Outlet />,
});
