import type { ChatBlock } from "../../../services/api/portfolioTypes";
import type { Loc } from "../format";
import { AllocationDonut } from "./AllocationDonut";
import { BeforeAfterChart } from "./BeforeAfterChart";
import { ExtractedPortfolioTable } from "./ExtractedPortfolioTable";
import { HoldingsTable } from "./HoldingsTable";
import { HypotheticalFrame } from "./HypotheticalFrame";
import { PolicyFlags } from "./PolicyFlags";
import { RebalanceActions } from "./RebalanceActions";
import { RiskPanel } from "./RiskPanel";
import { ScenarioCompare } from "./ScenarioCompare";
import { SectorTreemap } from "./SectorTreemap";
import { SignalFreshnessChip } from "./SignalFreshnessChip";

function renderOne(block: ChatBlock, loc: Loc) {
  switch (block.type) {
    case "allocation_donut": return <AllocationDonut block={block} loc={loc} />;
    case "sector_treemap": return <SectorTreemap block={block} loc={loc} />;
    case "before_after": return <BeforeAfterChart block={block} loc={loc} />;
    case "rebalance_actions": return <RebalanceActions block={block} loc={loc} />;
    case "risk_panel": return <RiskPanel block={block} loc={loc} />;
    case "signal_freshness": return <SignalFreshnessChip block={block} loc={loc} />;
    case "scenario_compare": return <ScenarioCompare block={block} loc={loc} />;
    case "policy_flags": return <PolicyFlags block={block} />;
    case "holdings_table": return <HoldingsTable block={block} loc={loc} />;
    case "extracted_portfolio_table": return <ExtractedPortfolioTable block={block} loc={loc} />;
    default: {
      // Exhaustiveness guard — a new block type is a compile error here.
      const _never: never = block;
      return _never;
    }
  }
}

/** Render a single ChatBlock; hypothetical blocks get the scenario frame. */
export function BlockRenderer({ block, loc = "en" }: { block: ChatBlock; loc?: Loc }) {
  const rendered = renderOne(block, loc);
  if (block.is_hypothetical) {
    return <HypotheticalFrame>{rendered}</HypotheticalFrame>;
  }
  return <>{rendered}</>;
}

/** Render an ordered list of blocks (a proposal / extraction payload). */
export function BlockList({ blocks, loc = "en" }: { blocks: ChatBlock[]; loc?: Loc }) {
  return (
    <div className="space-y-3">
      {blocks.map((b, i) => (
        <BlockRenderer key={`${b.type}-${i}`} block={b} loc={loc} />
      ))}
    </div>
  );
}
