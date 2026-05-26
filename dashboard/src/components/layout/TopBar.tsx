import { useRouterState } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp } from "lucide-react";
import { api } from "@/lib/api";
import { formatPct } from "@/lib/formatters";
import { cn } from "@/lib/utils";

const TITLES: Record<string, string> = {
  "/": "Home",
  "/run": "Run analysis",
  "/backtest": "Backtest",
  "/history": "History",
  "/settings": "Settings",
};

export function Topbar() {
  const path = useRouterState({ select: (s) => s.location.pathname });
  const title =
    TITLES[path] ||
    (path.startsWith("/history/")
      ? "Run detail"
      : path.startsWith("/backtest/")
        ? "Backtest detail"
        : "TradingAgents");

  const { data: hist } = useQuery({
    queryKey: ["egx30-mini"],
    queryFn: () => api.market.egx30History(2),
    refetchInterval: 60_000,
    staleTime: 50_000,
  });
  const last = hist?.[hist.length - 1];
  const prev = hist?.[hist.length - 2];
  const change = last && prev ? ((last.close - prev.close) / prev.close) * 100 : 0;

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center justify-between border-b border-border bg-background/80 px-6 backdrop-blur">
      <div className="text-sm text-muted-foreground">
        <span className="font-medium text-foreground">{title}</span>
      </div>
      {last && (
        <div className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">EGX30</span>
          <span className="font-mono font-medium tabular">
            {last.close.toLocaleString("en-US", { maximumFractionDigits: 0 })}
          </span>
          <span
            className={cn(
              "flex items-center gap-0.5 font-mono tabular",
              change >= 0 ? "text-primary" : "text-destructive",
            )}
          >
            {change >= 0 ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />}
            {formatPct(change)}
          </span>
        </div>
      )}
    </header>
  );
}
