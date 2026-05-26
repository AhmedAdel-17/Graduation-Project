import { Check, Hourglass, Loader2, X, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import type { AgentOutput } from "@/types/api";

interface Props {
  agents: AgentOutput[];
  selectedAgent?: string | null;
  onSelect?: (agent: AgentOutput | null) => void;
}

export function AgentTimeline({ agents, selectedAgent, onSelect }: Props) {
  return (
    <ol className="relative space-y-2 pl-4 before:absolute before:left-1.5 before:top-2 before:h-[calc(100%-1rem)] before:w-px before:bg-border">
      {agents.map((a) => {
        const isSelected = selectedAgent === a.agent;
        const clickable = a.status === "done" && !!onSelect;
        return (
          <li key={a.agent} className="relative">
            <span
              className={cn(
                "absolute -left-3 top-3 h-3 w-3 rounded-full border-2 border-background",
                a.status === "done" && "bg-primary",
                a.status === "running" && "bg-primary/40",
                a.status === "pending" && "bg-muted",
                a.status === "failed" && "bg-destructive",
              )}
            />
            <button
              onClick={() => clickable && onSelect?.(isSelected ? null : a)}
              disabled={!clickable}
              className={cn(
                "block w-full rounded-md border bg-card p-3 text-left transition-colors",
                a.status === "running" && "pulse-border border-primary/50",
                clickable && "cursor-pointer hover:border-primary/40 hover:bg-secondary/30",
                isSelected && "border-primary bg-secondary/40 shadow-sm",
                a.status === "pending" && "border-border opacity-60",
                a.status === "failed" && "border-destructive/50",
              )}
            >
              <div className="flex items-center gap-2">
                <StatusIcon status={a.status} />
                <span className="text-sm font-medium">{a.agent}</span>
                <span className="ml-auto flex items-center gap-1">
                  {clickable && (
                    <ChevronRight
                      className={cn(
                        "h-4 w-4 transition-transform",
                        isSelected ? "rotate-90 text-primary" : "text-muted-foreground",
                      )}
                    />
                  )}
                </span>
              </div>
              {(a.summary || a.error) && (
                <p
                  className={cn(
                    "mt-1.5 line-clamp-2 text-xs",
                    a.error ? "text-destructive" : "text-muted-foreground",
                  )}
                >
                  {a.error || a.summary}
                </p>
              )}
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function StatusIcon({ status }: { status: AgentOutput["status"] }) {
  if (status === "done") return <Check className="h-4 w-4 text-primary" />;
  if (status === "running") return <Loader2 className="h-4 w-4 animate-spin text-primary" />;
  if (status === "failed") return <X className="h-4 w-4 text-destructive" />;
  return <Hourglass className="h-4 w-4 text-muted-foreground" />;
}
