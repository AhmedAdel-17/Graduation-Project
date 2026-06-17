import type { SectorTreemapBlock, TreemapNode } from "../../../services/api/portfolioTypes";
import type { Loc } from "../format";
import { fmtPct } from "../format";
import { BlockCard } from "./BlockCard";
import { toneColor } from "./palette";

/**
 * Sector exposure as a proportional tiling: rows per sector (width ∝ summed
 * weight), tiles per holding (width ∝ weight, color ∝ blended agent signal).
 * A flex-based tiling rather than recharts Treemap — robust at small sizes and
 * theme-aware (the design explicitly allows this fallback).
 */
export function SectorTreemap({ block, loc = "en" }: { block: SectorTreemapBlock; loc?: Loc }) {
  const nodes = block.data.nodes ?? [];
  const bySector = new Map<string, TreemapNode[]>();
  for (const n of nodes) {
    const arr = bySector.get(n.sector) ?? [];
    arr.push(n);
    bySector.set(n.sector, arr);
  }
  const sectors = [...bySector.entries()]
    .map(([sector, items]) => ({
      sector,
      items,
      weight: items.reduce((s, n) => s + n.weight_pct, 0),
    }))
    .sort((a, b) => b.weight - a.weight);
  const totalWeight = Math.max(1, sectors.reduce((s, x) => s + x.weight, 0));

  return (
    <BlockCard title="Sector exposure">
      <div className="space-y-2">
        {sectors.map((sec) => (
          <div key={sec.sector}>
            <div className="flex items-center justify-between mb-1">
              <span className="text-[11px] font-medium text-ink-2">
                {sec.sector.replace(/_/g, " ")}
              </span>
              <span className="num text-[11px] text-stone-400 dark:text-[var(--ink-3)]">
                {fmtPct(sec.weight, { loc })}
              </span>
            </div>
            <div
              className="flex gap-1 h-12"
              style={{ width: `${Math.max(18, (sec.weight / totalWeight) * 100)}%` }}
            >
              {sec.items.map((n) => (
                <div
                  key={n.ticker ?? n.label}
                  className="rounded-md min-w-0 flex flex-col justify-center px-1.5 overflow-hidden border border-black/5 dark:border-white/5"
                  style={{ flex: `${Math.max(1, n.weight_pct)} 1 0%`, background: toneColor(n.signal_tone) }}
                  title={`${n.label} · ${fmtPct(n.weight_pct, { loc })}`}
                >
                  <span dir="auto" className="num text-[10.5px] font-semibold text-ink truncate">{n.label}</span>
                  <span className="num text-[9.5px] text-ink-2 truncate">{fmtPct(n.weight_pct, { loc })}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-[10.5px] text-stone-400 dark:text-[var(--ink-3)]">
        Tile color = blended agent signal (red bearish → green bullish).
      </p>
    </BlockCard>
  );
}
