import { cn } from "../../../lib/utils";
import { SIGNAL_CLASS, type SignalKind } from "./signal";

export function SignalBadge({
  signal,
  className,
}: {
  signal: SignalKind;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center px-2 py-0.5 rounded-md border text-[11px] font-semibold tracking-wide",
        SIGNAL_CLASS[signal],
        className
      )}
    >
      {signal === "UNKNOWN" ? "—" : signal}
    </span>
  );
}
