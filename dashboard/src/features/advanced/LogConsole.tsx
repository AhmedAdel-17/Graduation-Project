import { useEffect, useRef } from "react";
import { cn } from "../../lib/utils";
import { Terminal as TerminalIcon } from "lucide-react";

export interface LogLine {
  ts: string;
  level: "info" | "warn" | "error" | "success" | "debug";
  message: string;
}

export function LogConsole({
  lines,
  className,
  title = "Execution log",
  emptyHint = "Idle — trigger a run to stream logs.",
}: {
  lines: LogLine[];
  className?: string;
  title?: string;
  emptyHint?: string;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [lines]);

  return (
    <div
      className={cn(
        "rounded-xl border border-line bg-ink-950/70 overflow-hidden flex flex-col",
        className
      )}
    >
      <div className="flex items-center justify-between px-4 h-10 border-b border-line bg-ink-900/70">
        <div className="flex items-center gap-2 text-xs text-fg-muted">
          <TerminalIcon className="h-3.5 w-3.5" />
          {title}
        </div>
        <span className="text-[10px] uppercase tracking-wider text-fg-subtle">
          {lines.length} entries
        </span>
      </div>
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto font-mono text-[12px] leading-relaxed p-4 min-h-[220px]"
      >
        {lines.length === 0 ? (
          <p className="text-fg-subtle italic">{emptyHint}</p>
        ) : (
          lines.map((l, i) => (
            <div key={i} className="flex gap-3">
              <span className="text-fg-subtle shrink-0">{l.ts}</span>
              <span
                className={cn("shrink-0 w-12 uppercase text-[10px] pt-0.5", {
                  "text-fg-muted": l.level === "info",
                  "text-amber-400": l.level === "warn",
                  "text-down": l.level === "error",
                  "text-up": l.level === "success",
                  "text-accent": l.level === "debug",
                })}
              >
                {l.level}
              </span>
              <span className="text-fg/90 whitespace-pre-wrap break-words">
                {l.message}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
