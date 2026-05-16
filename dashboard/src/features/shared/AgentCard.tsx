import { cn } from "../../lib/utils";
import type { LucideIcon } from "lucide-react";

export type AgentTone = "bull" | "bear" | "judge" | "manager" | "risk";

const TONES: Record<
  AgentTone,
  {
    accent: string;
    accentStrong: string;
    avatarBg: string;
    avatarFg: string;
    chip: string;
    chipText: string;
    rule: string;
    glow: string;
  }
> = {
  bull: {
    accent: "from-emerald-500/0 via-emerald-500 to-teal-500",
    accentStrong: "bg-emerald-600",
    avatarBg: "bg-emerald-50 border-emerald-200",
    avatarFg: "text-emerald-700",
    chip: "bg-emerald-50 border-emerald-200",
    chipText: "text-emerald-800",
    rule: "border-emerald-100",
    glow: "shadow-[0_8px_30px_-12px_rgba(16,185,129,0.18)]",
  },
  bear: {
    accent: "from-rose-500/0 via-rose-500 to-orange-500",
    accentStrong: "bg-rose-600",
    avatarBg: "bg-rose-50 border-rose-200",
    avatarFg: "text-rose-700",
    chip: "bg-rose-50 border-rose-200",
    chipText: "text-rose-800",
    rule: "border-rose-100",
    glow: "shadow-[0_8px_30px_-12px_rgba(244,63,94,0.18)]",
  },
  judge: {
    accent: "from-indigo-500/0 via-indigo-500 to-violet-500",
    accentStrong: "bg-indigo-700",
    avatarBg: "bg-indigo-50 border-indigo-200",
    avatarFg: "text-indigo-700",
    chip: "bg-indigo-50 border-indigo-200",
    chipText: "text-indigo-800",
    rule: "border-indigo-100",
    glow: "shadow-[0_8px_30px_-12px_rgba(99,102,241,0.18)]",
  },
  manager: {
    accent: "from-amber-500/0 via-amber-500 to-orange-500",
    accentStrong: "bg-amber-600",
    avatarBg: "bg-amber-50 border-amber-200",
    avatarFg: "text-amber-700",
    chip: "bg-amber-50 border-amber-200",
    chipText: "text-amber-800",
    rule: "border-amber-100",
    glow: "shadow-[0_8px_30px_-12px_rgba(245,158,11,0.16)]",
  },
  risk: {
    accent: "from-violet-500/0 via-violet-500 to-fuchsia-500",
    accentStrong: "bg-violet-700",
    avatarBg: "bg-violet-50 border-violet-200",
    avatarFg: "text-violet-700",
    chip: "bg-violet-50 border-violet-200",
    chipText: "text-violet-800",
    rule: "border-violet-100",
    glow: "shadow-[0_8px_30px_-12px_rgba(139,92,246,0.16)]",
  },
};

export interface AgentCardProps {
  tone: AgentTone;
  icon: LucideIcon;
  agent: string;          // e.g. "Bull researcher"
  role: string;           // e.g. "Why this could work"
  chip?: string;          // e.g. "Bullish thesis"
  meta?: { label: string; value: string }[];
  children?: React.ReactNode;
  className?: string;
}

export function AgentCard({
  tone,
  icon: Icon,
  agent,
  role,
  chip,
  meta,
  children,
  className,
}: AgentCardProps) {
  const t = TONES[tone];
  return (
    <article
      className={cn(
        "group relative card overflow-hidden anim-fade-up",
        t.glow,
        className
      )}
    >
      {/* Top gradient accent strip */}
      <div className={cn("h-[3px] w-full bg-gradient-to-r", t.accent)} />

      <div className="px-6 pt-5 pb-5">
        {/* Header */}
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3.5">
            <div
              className={cn(
                "h-11 w-11 rounded-xl border flex items-center justify-center shrink-0",
                t.avatarBg
              )}
            >
              <Icon className={cn("h-5 w-5", t.avatarFg)} strokeWidth={2.2} />
            </div>
            <div className="leading-tight pt-0.5">
              <div className="eyebrow mb-1">{role}</div>
              <div className="display text-[17px] font-semibold text-ink">
                {agent}
              </div>
            </div>
          </div>
          {chip && (
            <span
              className={cn(
                "px-2.5 py-1 rounded-full text-[10.5px] font-medium tracking-wide uppercase border",
                t.chip,
                t.chipText
              )}
            >
              {chip}
            </span>
          )}
        </div>

        {/* Body */}
        <div className="mt-4 text-[14px] leading-[1.65] text-ink-2 whitespace-pre-wrap">
          {children}
        </div>

        {/* Meta footer */}
        {meta && meta.length > 0 && (
          <div className={cn("mt-5 pt-4 border-t flex flex-wrap gap-x-8 gap-y-2", t.rule)}>
            {meta.map((m) => (
              <div key={m.label}>
                <div className="text-[10px] uppercase tracking-[0.15em] text-stone-500 font-medium">
                  {m.label}
                </div>
                <div className="mono text-[13px] font-medium text-ink mt-0.5">
                  {m.value}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}

export function AgentCardSkeleton({ tone }: { tone: AgentTone }) {
  const t = TONES[tone];
  return (
    <div className="card overflow-hidden">
      <div className={cn("h-[3px] w-full bg-gradient-to-r", t.accent)} />
      <div className="px-6 py-5">
        <div className="flex items-center gap-3.5">
          <div className={cn("h-11 w-11 rounded-xl border", t.avatarBg)} />
          <div className="flex-1 space-y-2">
            <div className="h-2.5 w-24 skeleton rounded" />
            <div className="h-3.5 w-40 skeleton rounded" />
          </div>
        </div>
        <div className="mt-5 space-y-2.5">
          <div className="h-2.5 w-full skeleton rounded" />
          <div className="h-2.5 w-11/12 skeleton rounded" />
          <div className="h-2.5 w-9/12 skeleton rounded" />
          <div className="h-2.5 w-7/12 skeleton rounded" />
        </div>
      </div>
    </div>
  );
}
