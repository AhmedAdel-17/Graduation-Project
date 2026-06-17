import { useEffect, useRef } from "react";
import { Sparkles } from "lucide-react";
import type { PortfolioSnapshot } from "../../services/api/portfolioTypes";
import type { Loc } from "./format";
import type { ChatMessage } from "./hooks/usePortfolioChat";
import { MessageBubble } from "./MessageBubble";

export function ChatThread({
  messages,
  busy,
  status,
  loc = "en",
  onConfirmSnapshot,
  onConfirmPolicy,
}: {
  messages: ChatMessage[];
  busy: boolean;
  status: string | null;
  loc?: Loc;
  onConfirmSnapshot?: (s: PortfolioSnapshot) => void;
  onConfirmPolicy?: () => void;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy, status]);

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-center px-6 gap-2">
        <Sparkles className="h-6 w-6 text-stone-300 dark:text-[var(--ink-3)]" aria-hidden />
        <p className="text-[13px] font-medium text-stone-600 dark:text-[var(--ink-2)]">Start a conversation</p>
        <p className="text-[12px] text-stone-400 dark:text-[var(--ink-3)] max-w-[300px]">
          Describe your holdings in English or Arabic, set an objective, then ask for a rebalancing proposal.
        </p>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
      {messages.map((m) => (
        <MessageBubble
          key={m.id}
          message={m}
          loc={loc}
          onConfirmSnapshot={onConfirmSnapshot}
          onConfirmPolicy={onConfirmPolicy}
        />
      ))}
      {busy && (
        <div className="flex items-center gap-2 text-[12px] text-stone-400 dark:text-[var(--ink-3)] pl-1">
          <span className="flex gap-1">
            <span className="h-1.5 w-1.5 rounded-full bg-stone-400 anim-pulse-dot" />
            <span className="h-1.5 w-1.5 rounded-full bg-stone-400 anim-pulse-dot" style={{ animationDelay: "150ms" }} />
            <span className="h-1.5 w-1.5 rounded-full bg-stone-400 anim-pulse-dot" style={{ animationDelay: "300ms" }} />
          </span>
          {status ?? "Thinking…"}
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}
