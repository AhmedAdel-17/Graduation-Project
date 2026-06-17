import { AlertTriangle, Info, OctagonAlert } from "lucide-react";
import type { FlagSeverity, PolicyFlag, PolicyFlagsBlock } from "../../../services/api/portfolioTypes";
import { BlockCard } from "./BlockCard";

const SEV: Record<FlagSeverity, { icon: typeof Info; cls: string }> = {
  info: { icon: Info, cls: "text-sky-600 dark:text-sky-400 border-sky-200 dark:border-sky-900/40 bg-sky-50/50 dark:bg-sky-900/10" },
  warning: { icon: AlertTriangle, cls: "text-amber-600 dark:text-amber-400 border-amber-200 dark:border-amber-900/40 bg-amber-50/50 dark:bg-amber-900/10" },
  critical: { icon: OctagonAlert, cls: "text-rose-600 dark:text-rose-400 border-rose-200 dark:border-rose-900/40 bg-rose-50/50 dark:bg-rose-900/10" },
};

export function PolicyFlags({ block }: { block: PolicyFlagsBlock }) {
  const flags = block.flags ?? [];
  if (flags.length === 0) return null;
  return (
    <BlockCard title="Policy notes">
      <ul className="space-y-2">
        {flags.map((f, i) => (
          <FlagRow key={`${f.code}-${i}`} flag={f} />
        ))}
      </ul>
    </BlockCard>
  );
}

function FlagRow({ flag }: { flag: PolicyFlag }) {
  const meta = SEV[flag.severity ?? "warning"];
  const Icon = meta.icon;
  return (
    <li className={`flex items-start gap-2 rounded-lg border px-3 py-2 ${meta.cls}`}>
      <Icon className="h-4 w-4 mt-px shrink-0" aria-hidden />
      <div className="min-w-0">
        <div className="text-[12.5px] text-ink leading-snug" dir="auto">{flag.detail}</div>
        <div className="text-[10px] uppercase tracking-wide text-stone-400 dark:text-[var(--ink-3)] mt-0.5">{flag.code}</div>
      </div>
    </li>
  );
}
