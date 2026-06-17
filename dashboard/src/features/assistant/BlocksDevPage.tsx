import { useState } from "react";
import type {
  AssistantMessageEvent,
  ChatBlock,
  ExtractionEvent,
  HoldingsTableBlock,
} from "../../services/api/portfolioTypes";
import type { Loc } from "./format";
import { BlockRenderer } from "./blocks/BlockRenderer";
import proposalFixture from "./fixtures/sample_proposal_blocks.json";
import scenarioFixture from "./fixtures/sample_scenario_compare.json";
import extractionFixture from "./fixtures/sample_extraction.json";

// A synthesized holdings_table — the only block type not present in the P0
// fixtures — so the visual-regression page exercises all renderers.
const HOLDINGS_TABLE: HoldingsTableBlock = {
  type: "holdings_table",
  rows: [
    { ticker: "ETEL.CA", shares: 1000, avg_cost: 35, price: 38, market_value_egp: 38000, weight_pct: 16, unrealized_pnl_egp: 3000, signal_label: "BUY" },
    { ticker: "FWRY.CA", shares: 5000, avg_cost: 10, price: 12, market_value_egp: 60000, weight_pct: 24, unrealized_pnl_egp: 10000, signal_label: "BUY" },
    { ticker: "TMGH.CA", shares: 1538, avg_cost: 60, price: 65, market_value_egp: 100000, weight_pct: 40, unrealized_pnl_egp: 7690, signal_label: "SELL" },
  ],
};

const proposalBlocks = (proposalFixture as AssistantMessageEvent).blocks ?? [];
const scenarioBlocks = (scenarioFixture as AssistantMessageEvent).blocks ?? [];
const extractionBlocks = (extractionFixture as ExtractionEvent).blocks ?? [];

const ALL_BLOCKS: { label: string; block: ChatBlock }[] = [
  ...proposalBlocks.map((b) => ({ label: b.type, block: b })),
  { label: "holdings_table", block: HOLDINGS_TABLE },
  ...scenarioBlocks.map((b) => ({ label: `${b.type} (hypothetical)`, block: b })),
  ...extractionBlocks.map((b) => ({ label: b.type, block: b })),
];

/**
 * Dev-only visual-regression page (roadmap P7): every ChatBlock renderer, from
 * every fixture, in the current theme and a toggleable locale. Reachable at
 * /portfolio/__blocks.
 */
export function BlocksDevPage() {
  const [loc, setLoc] = useState<Loc>("en");
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="display text-[20px] font-semibold tracking-tight">Block gallery</h1>
          <p className="text-[12.5px] text-stone-500 dark:text-[var(--ink-3)]">
            Every visualization block from the P0 fixtures — toggle locale; theme follows the global switch.
          </p>
        </div>
        <div className="inline-flex rounded-lg border border-stone-200 dark:border-[var(--hairline)] overflow-hidden text-[12px]">
          {(["en", "ar"] as Loc[]).map((l) => (
            <button
              key={l}
              onClick={() => setLoc(l)}
              className={
                "px-3 py-1.5 " +
                (loc === l
                  ? "bg-stone-900 text-white dark:bg-white dark:text-stone-900"
                  : "text-stone-600 dark:text-[var(--ink-2)]")
              }
            >
              {l === "en" ? "English" : "العربية"}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4" dir={loc === "ar" ? "rtl" : "ltr"}>
        {ALL_BLOCKS.map(({ label, block }, i) => (
          <div key={`${label}-${i}`} className="space-y-1.5">
            <div className="eyebrow text-stone-400 dark:text-[var(--ink-3)]">{label}</div>
            <BlockRenderer block={block} loc={loc} />
          </div>
        ))}
      </div>
    </div>
  );
}

export default BlocksDevPage;
