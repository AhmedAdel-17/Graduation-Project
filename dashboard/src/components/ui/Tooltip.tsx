import { useId, useState, type ReactNode } from "react";
import { cn } from "../../lib/utils";

// Lightweight hover/focus tooltip. Accessible: the trigger references the
// tooltip via aria-describedby, and the tooltip is rendered into the DOM only
// while open so screen readers don't see stale content.
export function Tooltip({
  content,
  children,
  side = "top",
  className,
}: {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "bottom" | "start" | "end";
  className?: string;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);

  const sideCls = {
    top: "bottom-full mb-1 left-1/2 -translate-x-1/2",
    bottom: "top-full mt-1 left-1/2 -translate-x-1/2",
    start: "right-full me-1 top-1/2 -translate-y-1/2",
    end: "left-full ms-1 top-1/2 -translate-y-1/2",
  }[side];

  return (
    <span
      className={cn("relative inline-flex", className)}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      <span aria-describedby={open ? id : undefined}>{children}</span>
      {open && (
        <span
          role="tooltip"
          id={id}
          className={cn(
            "absolute z-50 whitespace-nowrap pointer-events-none",
            "px-2 py-1 rounded-md text-[11px] font-medium",
            "bg-ink-700 text-fg border border-line-strong shadow-card",
            sideCls
          )}
        >
          {content}
        </span>
      )}
    </span>
  );
}
