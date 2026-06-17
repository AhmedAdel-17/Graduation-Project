import { Cell, Pie, PieChart, ResponsiveContainer } from "recharts";
import type { AllocationDonutBlock } from "../../../services/api/portfolioTypes";
import type { Loc } from "../format";
import { fmtEgp, fmtPct } from "../format";
import { BlockCard } from "./BlockCard";
import { sliceColor } from "./palette";

export function AllocationDonut({ block, loc = "en" }: { block: AllocationDonutBlock; loc?: Loc }) {
  const slices = (block.data.slices ?? []).filter((s) => s.value_egp > 0);
  const data = slices.map((s, i) => ({
    name: s.label,
    value: s.value_egp,
    weight: s.weight_pct,
    fill: sliceColor(i, s.is_cash),
  }));

  return (
    <BlockCard title={block.title ?? "Allocation"}>
      <div className="flex items-center gap-4 flex-col sm:flex-row">
        <div className="relative h-[160px] w-[160px] shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={data}
                dataKey="value"
                nameKey="name"
                innerRadius={52}
                outerRadius={78}
                paddingAngle={1.5}
                stroke="none"
                isAnimationActive={false}
              >
                {data.map((d) => (
                  <Cell key={d.name} fill={d.fill} />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
          <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
            <span className="text-[10px] uppercase tracking-wider text-stone-400 dark:text-[var(--ink-3)]">
              Total
            </span>
            <span className="num text-[13px] font-semibold text-ink">
              {fmtEgp(block.data.total_egp, loc)}
            </span>
          </div>
        </div>

        <ul className="flex-1 w-full space-y-1.5">
          {data.map((d) => (
            <li key={d.name} className="flex items-center gap-2 text-[12.5px]">
              <span className="h-2.5 w-2.5 rounded-sm shrink-0" style={{ background: d.fill }} />
              <span dir="auto" className="truncate text-ink-2">{d.name}</span>
              <span className="ml-auto num font-medium text-ink">{fmtPct(d.weight, { loc })}</span>
              <span className="num text-stone-400 dark:text-[var(--ink-3)] w-[88px] text-right">
                {fmtEgp(d.value, loc)}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </BlockCard>
  );
}
