/**
 * Research Manager verdict card — "Debate Resolution" section.
 *
 *   ┌──────────────────────────────────────────────────┐
 *   │ [⚖️]  RESEARCH MANAGER VERDICT     [VERDICT: SELL]│
 *   │       Debate judge                                │
 *   │                                                   │
 *   │ - bullet 1                                        │
 *   │ - bullet 2                                        │
 *   │ - bullet 3                                        │
 *   │                                                   │
 *   │ SIGNAL    CONFIDENCE    RISK PROFILE              │
 *   │ SELL      MEDIUM        MEDIUM                    │
 *   └──────────────────────────────────────────────────┘
 */
import { Gavel } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Decision } from "@/types/api";

interface Props {
  decision?: Decision;
  confidence?: number;          // 0-1
  rationale?: string;           // The plain text from Research Manager
  riskProfile?: "LOW" | "MEDIUM" | "HIGH";
}

export function DebateResolution({
  decision,
  confidence,
  rationale,
  riskProfile = "MEDIUM",
}: Props) {
  const confLabel =
    confidence == null ? "—"
    : confidence >= 0.75 ? "HIGH"
    : confidence >= 0.50 ? "MEDIUM"
    : "LOW";

  const gradient =
    decision === "BUY"
      ? "from-emerald-200 via-emerald-50 to-amber-100"
      : decision === "SELL"
      ? "from-emerald-200 via-violet-100 to-amber-200"
      : "from-amber-100 via-amber-50 to-lime-100";

  const verdictBadge =
    decision === "BUY"
      ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
      : decision === "SELL"
      ? "bg-violet-100 text-violet-700 dark:bg-violet-950/40 dark:text-violet-300"
      : "bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300";

  // Split the rationale into bullet points if it contains line breaks or "- " markers
  const bullets = parseBullets(rationale);

  return (
    <div className="relative overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
      <div className={cn("absolute inset-x-0 top-0 h-1 bg-gradient-to-r", gradient)} />
      <div className="p-6 md:p-7">
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-100 text-violet-600 dark:bg-violet-950/40 dark:text-violet-400">
              <Gavel className="h-4 w-4" />
            </div>
            <div>
              <div className="text-[11px] font-medium uppercase tracking-[0.15em] text-muted-foreground">
                Research manager verdict
              </div>
              <h3 className="font-serif text-xl font-semibold leading-tight tracking-tight">
                Debate judge
              </h3>
            </div>
          </div>
          {decision && (
            <span className={cn(
              "shrink-0 rounded-full px-2.5 py-1 text-[10px] font-semibold tracking-wider",
              verdictBadge,
            )}>
              VERDICT: {decision}
            </span>
          )}
        </div>

        {/* Body */}
        {bullets.length > 0 ? (
          <ul className="mt-5 space-y-2 text-sm leading-relaxed text-foreground/85">
            {bullets.map((b, i) => (
              <li key={i} className="flex gap-2">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-muted-foreground/50" />
                <span>{b}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-5 text-sm italic text-muted-foreground">
            No verdict reasoning recorded yet.
          </p>
        )}

        {/* Bottom metric strip */}
        <div className="mt-6 grid grid-cols-3 border-t border-border pt-4">
          <BottomMetric label="Signal" value={decision ?? "—"} />
          <BottomMetric label="Confidence" value={confLabel} />
          <BottomMetric label="Risk profile" value={riskProfile} />
        </div>
      </div>
    </div>
  );
}

function BottomMetric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] font-medium uppercase tracking-[0.15em] text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 font-mono text-sm tabular">{value}</div>
    </div>
  );
}

/**
 * Try to split a rationale block into individual bullet points.
 *
 *  - Strips JSON fences and bold markdown
 *  - Splits on numbered headings (1., 2., 3., 4.), then on "- " markers,
 *    then on blank lines as a fallback
 *  - Returns at most 8 bullets, each capped at 500 chars (split at word boundary)
 */
function parseBullets(text?: string): string[] {
  if (!text) return [];
  let cleaned = text
    .replace(/```[\s\S]*?```/g, "")               // strip code fences
    .replace(/\*\*([^*]+)\*\*/g, "$1")            // strip bold markdown
    .trim();

  const truncate = (s: string, n = 500) => {
    if (s.length <= n) return s;
    // Truncate at the last word boundary before n
    const sliced = s.slice(0, n);
    const lastSpace = sliced.lastIndexOf(" ");
    return (lastSpace > n * 0.7 ? sliced.slice(0, lastSpace) : sliced) + "…";
  };

  // Try numbered headings first ("1. ", "2. ", ...) — most common LLM format
  const numbered = cleaned
    .split(/\n\s*(?=\d+\.\s)/)
    .map((s) => s.trim())
    .filter((s) => /^\d+\./.test(s) && s.length > 15);

  if (numbered.length >= 2) {
    return numbered.slice(0, 8).map((s) => truncate(s));
  }

  // Try "- bullet" markers
  const dashSplit = cleaned
    .split(/\n+\s*[-•]\s+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 10);

  if (dashSplit.length > 1) {
    return dashSplit.slice(0, 8).map((s) => truncate(s));
  }

  // Fallback: split on blank lines
  const blockSplit = cleaned
    .split(/\n{2,}/)
    .map((s) => s.trim())
    .filter((s) => s.length > 10);

  if (blockSplit.length > 1) {
    return blockSplit.slice(0, 6).map((s) => truncate(s));
  }

  // Single paragraph fallback
  return [truncate(cleaned, 1500)];
}
