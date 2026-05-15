import { cn, signalBg, confidenceColor } from "../../lib/utils";
import { ArrowUpRight, ArrowDownRight, Minus } from "lucide-react";

export function SignalPill({
  signal,
  className,
}: {
  signal?: string;
  className?: string;
}) {
  const s = (signal || "HOLD").toUpperCase();
  const isBuy = s === "BUY" || s === "STRONG_BUY";
  const isSell = s === "SELL" || s === "STRONG_SELL";

  const Icon = isBuy ? ArrowUpRight : isSell ? ArrowDownRight : Minus;

  return (
    <div
      className={cn(
        "inline-flex items-center gap-1.5 px-2.5 h-7 rounded-md border text-xs font-semibold uppercase tracking-wider",
        signalBg(s),
        className
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      {s}
    </div>
  );
}

export function ConfidencePill({
  level,
  className,
}: {
  level?: string;
  className?: string;
}) {
  const l = (level || "MEDIUM").toUpperCase();
  return (
    <div
      className={cn(
        "inline-flex items-center px-2 h-6 rounded-md border text-[11px] font-medium uppercase tracking-wider",
        confidenceColor(l),
        className
      )}
    >
      {l} confidence
    </div>
  );
}
