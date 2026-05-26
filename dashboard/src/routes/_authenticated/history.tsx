import { createFileRoute, Outlet } from "@tanstack/react-router";

/**
 * Parent route for /history — just an Outlet so child routes can render.
 * The list view lives in `history.index.tsx`, the detail view in
 * `history.$id.tsx`. Without this Outlet the detail page can never paint
 * because TanStack's file-based router treats `history.$id` as a child of
 * `history`.
 */
export const Route = createFileRoute("/_authenticated/history")({
  component: () => <Outlet />,
});
