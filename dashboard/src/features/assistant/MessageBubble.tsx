import { useState } from "react";
import { Check } from "lucide-react";
import type { ExtractedPortfolioTableBlock, PortfolioSnapshot } from "../../services/api/portfolioTypes";
import type { Loc } from "./format";
import type { ChatMessage } from "./hooks/usePortfolioChat";
import { BlockList } from "./blocks/BlockRenderer";
import { ExtractionConfirmCard } from "./ExtractionConfirmCard";
import { cn } from "../../lib/utils";

const POLICY_FIELDS = ["objective", "risk_tolerance", "horizon"] as const;

export function MessageBubble({
  message,
  loc = "en",
  onConfirmSnapshot,
  onConfirmPolicy,
}: {
  message: ChatMessage;
  loc?: Loc;
  onConfirmSnapshot?: (s: PortfolioSnapshot) => void;
  onConfirmPolicy?: () => void;
}) {
  // System notes (policy edits, panel actions) — centered + muted.
  if (message.kind === "system") {
    return (
      <div className="flex justify-center">
        <div dir="auto" className="text-[11.5px] text-stone-400 dark:text-[var(--ink-3)] bg-stone-100/60 dark:bg-white/[0.04] rounded-full px-3 py-1">
          {message.text}
        </div>
      </div>
    );
  }

  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div dir="auto" className="max-w-[80%] rounded-2xl rounded-br-sm bg-stone-900 text-white px-3.5 py-2 text-[13px] dark:bg-white dark:text-stone-900">
          {message.text}
        </div>
      </div>
    );
  }

  // Extraction → the editable confirmation card (safety gate).
  if (message.kind === "extraction" && message.blocks?.[0]?.type === "extracted_portfolio_table") {
    return (
      <AssistantRow>
        <ExtractionConfirmCard
          block={message.blocks[0] as ExtractedPortfolioTableBlock}
          loc={loc}
          onConfirm={onConfirmSnapshot}
        />
      </AssistantRow>
    );
  }

  if (message.kind === "policy" && message.policy) {
    return (
      <AssistantRow>
        <PolicyChips message={message} onConfirmPolicy={onConfirmPolicy} />
      </AssistantRow>
    );
  }

  const tone =
    message.kind === "error"
      ? "border-rose-200 bg-rose-50/60 text-rose-700 dark:border-rose-900/40 dark:bg-rose-900/10 dark:text-rose-300"
      : message.kind === "clarification"
        ? "border-amber-200 bg-amber-50/50 text-ink dark:border-amber-900/40 dark:bg-amber-900/10"
        : "border-stone-200 bg-white text-ink dark:border-[var(--hairline)] dark:bg-[var(--paper)]";

  return (
    <AssistantRow>
      <div className="space-y-3">
        {message.text && (
          <div dir="auto" className={cn("max-w-[88%] rounded-2xl rounded-bl-sm border px-3.5 py-2 text-[13px] leading-relaxed", tone)}>
            {message.text}
          </div>
        )}
        {message.blocks && message.blocks.length > 0 && (
          <BlockList blocks={message.blocks} loc={loc} />
        )}
      </div>
    </AssistantRow>
  );
}

function AssistantRow({ children }: { children: React.ReactNode }) {
  return <div className="flex justify-start"><div className="w-full max-w-[640px]">{children}</div></div>;
}

function PolicyChips({ message, onConfirmPolicy }: { message: ChatMessage; onConfirmPolicy?: () => void }) {
  const [confirmed, setConfirmed] = useState(false);
  const policy = message.policy!;
  const inferred = new Set(message.inferredFields ?? []);
  return (
    <div className="rounded-xl border border-stone-200 bg-white p-3.5 dark:border-[var(--hairline)] dark:bg-[var(--paper)]">
      {message.text && <p className="text-[12.5px] text-ink-2 mb-2.5">{message.text}</p>}
      <div className="flex flex-wrap gap-1.5">
        {POLICY_FIELDS.map((f) => {
          const v = policy[f] as string | undefined;
          if (!v) return null;
          return (
            <span
              key={f}
              className={cn(
                "inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11.5px] border",
                inferred.has(f)
                  ? "border-dashed border-stone-300 text-stone-500 dark:border-[var(--hairline)] dark:text-[var(--ink-3)]"
                  : "border-stone-200 text-ink-2 dark:border-[var(--hairline)]"
              )}
              title={inferred.has(f) ? "Assumed — confirm or correct" : "From what you told me"}
            >
              {f.replace(/_/g, " ")}: <b className="font-semibold text-ink">{v.replace(/_/g, " ")}</b>
            </span>
          );
        })}
      </div>
      {!confirmed ? (
        <button
          type="button"
          onClick={() => { setConfirmed(true); onConfirmPolicy?.(); }}
          className="mt-3 inline-flex items-center gap-1.5 px-3 h-8 rounded-lg bg-stone-900 text-white text-[12.5px] font-medium hover:bg-stone-800 dark:bg-white dark:text-stone-900 dark:hover:bg-stone-200"
        >
          <Check className="h-3.5 w-3.5" /> Confirm profile
        </button>
      ) : (
        <div className="mt-3 inline-flex items-center gap-1 text-[11.5px] text-emerald-600 dark:text-emerald-400">
          <Check className="h-3.5 w-3.5" /> Profile confirmed
        </div>
      )}
    </div>
  );
}
