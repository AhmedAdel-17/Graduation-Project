import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  Coins,
  DollarSign,
  Minus,
  type LucideIcon,
} from "lucide-react";
import { useMarketIndices, type MarketIndex } from "../../hooks/useMarketIndices";
import { cn, formatNumber, formatPercent } from "../../lib/utils";
import { useT } from "../../lib/i18n";

const ICONS: Record<string, LucideIcon> = {
  egx30: Activity,
  gold: Coins,
  usd_egp: DollarSign,
};

function IndexTile({ idx }: { idx: MarketIndex }) {
  const t = useT();
  const Icon = ICONS[idx.key] ?? Activity;
  const ch = idx.change_pct;
  const dir = ch == null ? "flat" : ch > 0 ? "up" : ch < 0 ? "down" : "flat";
  const Arrow =
    dir === "up" ? ArrowUpRight : dir === "down" ? ArrowDownRight : Minus;
  const changeClass =
    dir === "up"
      ? "text-emerald-700"
      : dir === "down"
      ? "text-rose-700"
      : "text-stone-500";
  const decimals = idx.kind === "index" ? 0 : 2;

  return (
    <div className="bg-white dark:bg-[var(--paper)] px-4 py-3 flex items-center gap-3 min-w-0">
      <span
        className="h-8 w-8 shrink-0 rounded-lg bg-stone-100 dark:bg-[var(--hairline)] flex items-center justify-center text-stone-600 dark:text-[var(--ink-2)]"
        aria-hidden
      >
        <Icon className="h-4 w-4" />
      </span>
      <div className="min-w-0">
        <div className="eyebrow text-stone-500 truncate">{idx.label}</div>
        <div className="flex items-baseline gap-2 mt-1">
          <span className="display-num text-[17px] font-semibold text-ink leading-none">
            {idx.value != null ? formatNumber(idx.value, decimals) : "—"}
          </span>
          <span
            className={cn(
              "inline-flex items-center gap-0.5 text-[12px] font-semibold mono",
              changeClass
            )}
          >
            <Arrow className="h-3 w-3" aria-hidden />
            {ch != null ? formatPercent(ch) : "—"}
          </span>
          {idx.change_source === "constituent_basket" && (
            <span
              className="text-[10px] font-normal text-stone-400 dark:text-[var(--ink-3)]"
              title={t("indices.estTitle")}
            >
              {t("indices.est")}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

export function MarketIndicesBar({ className }: { className?: string }) {
  const t = useT();
  const { data, isLoading, isError } = useMarketIndices();

  // Non-critical strip — fail quietly rather than showing an error block.
  if (isError) return null;

  if (isLoading) {
    return (
      <div
        className={cn(
          "card overflow-hidden grid grid-cols-3 gap-px bg-stone-200/80 dark:bg-[var(--hairline)]",
          className
        )}
      >
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="bg-white dark:bg-[var(--paper)] px-4 py-3">
            <div className="h-2.5 w-16 skeleton rounded" />
            <div className="h-4 w-24 skeleton rounded mt-2" />
          </div>
        ))}
      </div>
    );
  }

  const indices = data?.indices ?? [];
  if (indices.length === 0) return null;

  return (
    <section
      aria-label={t("indices.aria")}
      className={cn(
        "card overflow-hidden grid grid-cols-3 gap-px bg-stone-200/80 dark:bg-[var(--hairline)]",
        className
      )}
    >
      {indices.map((idx) => (
        <IndexTile key={idx.key} idx={idx} />
      ))}
    </section>
  );
}
