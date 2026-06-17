import { useEffect, useMemo, useState } from "react";
import { Info, Sparkles } from "lucide-react";
import { portfolioApi } from "../../services/api/portfolioEndpoints";
import { cn } from "../../lib/utils";
import type {
  AllocationDonutBlock,
  ChatBlock,
  RebalanceAction,
  RebalanceActionsBlock,
} from "../../services/api/portfolioTypes";
import { BlockList } from "./blocks/BlockRenderer";
import { ChatThread } from "./ChatThread";
import { Composer } from "./Composer";
import type { Loc } from "./format";
import { usePortfolioChat, type Transport } from "./hooks/usePortfolioChat";
import { PolicyPanel } from "./PolicyPanel";
import { ScenarioTabs } from "./ScenarioTabs";
import { WhatIfChips } from "./WhatIfChips";

function blockTickers(blocks: ChatBlock[]): string[] {
  const donut = blocks.find((b): b is AllocationDonutBlock => b.type === "allocation_donut");
  if (donut) return (donut.data.slices ?? []).filter((s) => !s.is_cash && s.ticker).map((s) => s.ticker!);
  return [];
}

function rebalanceActions(blocks: ChatBlock[]): RebalanceAction[] {
  const ra = blocks.find((b): b is RebalanceActionsBlock => b.type === "rebalance_actions");
  return ra?.data.actions ?? [];
}

const STARTERS: { label: string; text: string }[] = [
  { label: "Describe holdings", text: "I own 1000 Telecom Egypt and 5000 Fawry, plus 50,000 EGP cash." },
  { label: "Set objective", text: "Make it safer — I'm saving for a wedding in 6 months." },
  { label: "Optimize", text: "Optimize my portfolio." },
  { label: "طب لو بعت فوري؟", text: "طب لو بعت فوري كلها؟" },
];

export function AssistantPage() {
  const [transport, setTransport] = useState<Transport>("mock");
  const [loc, setLoc] = useState<Loc>("en");
  const [conversationId, setConversationId] = useState<string | undefined>();

  // Create a live conversation on demand when switching to Live mode.
  useEffect(() => {
    if (transport !== "live" || conversationId) return;
    let cancelled = false;
    portfolioApi
      .createConversation("auto")
      .then((r) => { if (!cancelled) setConversationId(r.id); })
      .catch(() => { if (!cancelled) setTransport("mock"); });
    return () => { cancelled = true; };
  }, [transport, conversationId]);

  const chat = usePortfolioChat({ transport, conversationId });

  // The canvas follows the active branch: on baseline it shows the latest plain
  // proposal; on a scenario it shows that scenario's hypothetical blocks.
  const { activeRef } = chat;
  const canvasBlocks: ChatBlock[] = useMemo(() => {
    for (let i = chat.messages.length - 1; i >= 0; i--) {
      const m = chat.messages[i];
      if (!m.blocks || m.blocks.length === 0 || m.kind === "extraction") continue;
      const isHyp = m.blocks.some((b) => b.is_hypothetical);
      if (activeRef === "baseline" && !isHyp) return m.blocks;
      if (activeRef !== "baseline" && String(m.scenarioId) === activeRef) return m.blocks;
    }
    return [];
  }, [chat.messages, activeRef]);
  const onBaseline = activeRef === "baseline";
  const tickers = useMemo(() => blockTickers(canvasBlocks), [canvasBlocks]);

  return (
    <div className="flex flex-col gap-4">
      <Header transport={transport} setTransport={setTransport} loc={loc} setLoc={setLoc} connected={chat.connected} />

      {transport === "live" && !chat.connected && (
        <div className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50/70 px-3.5 py-2 text-[12px] text-amber-800 dark:border-amber-900/40 dark:bg-amber-900/15 dark:text-amber-300">
          <span className="h-1.5 w-1.5 rounded-full bg-amber-500 anim-pulse-dot" />
          Disconnected from the assistant — reconnecting… Your conversation is saved and will resume.
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[55fr_45fr] gap-4 items-start">
        {/* Chat pane */}
        <section className="rounded-xl border border-stone-200 bg-white dark:border-[var(--hairline)] dark:bg-[var(--paper)] overflow-hidden flex flex-col h-[calc(100vh-220px)] min-h-[460px]">
          <div className="px-4 h-11 flex items-center border-b border-stone-200/80 dark:border-[var(--hairline)]">
            <span className="eyebrow text-stone-400 dark:text-[var(--ink-3)]">Conversation</span>
          </div>
          <ChatThread
            messages={chat.messages}
            busy={chat.busy}
            status={chat.status}
            loc={loc}
            onConfirmSnapshot={chat.confirmSnapshot}
          />
          {chat.messages.length === 0 && (
            <div className="px-4 pb-2 flex flex-wrap gap-1.5">
              {STARTERS.map((s) => (
                <button
                  key={s.label}
                  type="button"
                  onClick={() => chat.send(s.text)}
                  dir="auto"
                  className="px-2.5 py-1 rounded-full border border-stone-200 text-[11.5px] text-ink-2 hover:bg-stone-50 dark:border-[var(--hairline)] dark:hover:bg-white/5"
                >
                  {s.label}
                </button>
              ))}
            </div>
          )}
          <div className="p-3 border-t border-stone-200/80 dark:border-[var(--hairline)]">
            <Composer onSend={chat.send} disabled={chat.busy} />
          </div>
        </section>

        {/* Canvas pane */}
        <section className="rounded-xl border border-stone-200 bg-white dark:border-[var(--hairline)] dark:bg-[var(--paper)] overflow-hidden flex flex-col h-[calc(100vh-220px)] min-h-[460px]">
          <div className="px-3 min-h-11 py-1.5 flex items-center gap-1.5 border-b border-stone-200/80 dark:border-[var(--hairline)]">
            <ScenarioTabs
              scenarios={chat.scenarios}
              activeRef={activeRef}
              onSwitch={chat.setActiveRef}
              onAdopt={chat.adopt}
              onDiscard={chat.discardScenario}
              adoptActions={rebalanceActions(canvasBlocks)}
              loc={loc}
            />
          </div>
          <div className="flex-1 overflow-y-auto p-3.5 space-y-3">
            <PolicyPanel policy={chat.policy} onPatch={chat.patchPolicy} />
            {canvasBlocks.length > 0 ? (
              <>
                <BlockList blocks={canvasBlocks} loc={loc} />
                {onBaseline && tickers.length > 0 && (
                  <WhatIfChips tickers={tickers} onWhatIf={chat.sendWhatIf} />
                )}
              </>
            ) : (
              <div className="flex flex-col items-center justify-center text-center gap-2 py-16">
                <Sparkles className="h-6 w-6 text-stone-300 dark:text-[var(--ink-3)]" aria-hidden />
                <p className="text-[12px] text-stone-400 dark:text-[var(--ink-3)] max-w-[260px]">
                  Your allocation, sector exposure, risk and rebalancing actions will appear here as the conversation progresses.
                </p>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

function Header({
  transport, setTransport, loc, setLoc, connected,
}: {
  transport: Transport; setTransport: (t: Transport) => void;
  loc: Loc; setLoc: (l: Loc) => void; connected: boolean;
}) {
  return (
    <div>
      <div className="flex items-center gap-2.5">
        <div className="h-9 w-9 shrink-0 rounded-lg border border-stone-200 bg-white text-stone-900 flex items-center justify-center dark:border-[var(--hairline)] dark:bg-[var(--paper)] dark:text-white">
          <Sparkles className="h-[18px] w-[18px]" aria-hidden />
        </div>
        <div className="min-w-0">
          <h1 className="display text-[20px] font-semibold tracking-tight leading-tight">Portfolio Assistant</h1>
          <p className="text-[12.5px] text-stone-500 dark:text-[var(--ink-3)] mt-0.5">
            Bilingual EGX copilot — describe holdings, set objectives, explore rebalancing.
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Segmented value={loc} onChange={setLoc} options={[["en", "EN"], ["ar", "ع"]]} />
          <Segmented
            value={transport}
            onChange={setTransport}
            options={[["mock", "Demo"], ["live", "Live"]]}
            dotOn={transport === "live" && connected ? "live" : undefined}
          />
        </div>
      </div>

      <div className="mt-3 flex items-start gap-2 rounded-lg border border-stone-200 bg-[#FBFAF7] px-3.5 py-2.5 text-[12px] text-stone-600 dark:border-[var(--hairline)] dark:bg-[var(--paper)] dark:text-[var(--ink-2)]">
        <Info className="h-[15px] w-[15px] mt-px shrink-0 text-stone-400 dark:text-[var(--ink-3)]" aria-hidden />
        <p>
          Decision-support only. The assistant produces <strong>proposals for human review</strong> — never orders.
          EGX rules apply: long-only, no leverage, ±10% daily limit, T+2 settlement.
        </p>
      </div>
    </div>
  );
}

function Segmented<T extends string>({
  value, onChange, options, dotOn,
}: {
  value: T; onChange: (v: T) => void; options: [T, string][]; dotOn?: T;
}) {
  return (
    <div className="inline-flex rounded-lg border border-stone-200 dark:border-[var(--hairline)] overflow-hidden text-[12px]">
      {options.map(([val, label]) => (
        <button
          key={val}
          type="button"
          onClick={() => onChange(val)}
          className={cn(
            "px-2.5 py-1.5 inline-flex items-center gap-1.5",
            value === val
              ? "bg-stone-900 text-white dark:bg-white dark:text-stone-900"
              : "text-stone-600 dark:text-[var(--ink-2)] hover:bg-stone-50 dark:hover:bg-white/5"
          )}
        >
          {dotOn === val && <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />}
          {label}
        </button>
      ))}
    </div>
  );
}

export default AssistantPage;
