import { useEffect, useState } from "react";
import { TrendingUp, TrendingDown, Loader2, ArrowUpRight } from "lucide-react";
import { api } from "../../services/api/client";
import { cn, formatPercent } from "../../lib/utils";
import { useT } from "../../lib/i18n";
import { TickerLogo } from "../../components/ui/TickerLogo";

interface Mover {
  ticker: string;
  name: string;
  sector?: string;
  change: number;
}

type Timeframe = "month" | "quarter" | "year" | "5year";

type MoversData = Record<Timeframe, { gainers: Mover[]; losers: Mover[] }>;

const TF_ORDER: Timeframe[] = ["month", "quarter", "year", "5year"];
const TF_LABELS: Record<Timeframe, string> = {
  month: "1M",
  quarter: "3M",
  year: "1Y",
  "5year": "5Y",
};

export function TopMovers({ onSelectTicker }: { onSelectTicker?: (ticker: string) => void }) {
  const t = useT();
  const [data, setData] = useState<MoversData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [timeframe, setTimeframe] = useState<Timeframe>("month");

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        setLoading(true);
        const res = await api.get<{ status: string; data: MoversData }>("/market/movers");
        if (!alive) return;
        if (res.status === "ok") setData(res.data);
        else setError("Failed to load movers data");
      } catch (err) {
        if (alive) setError("Error fetching market movers");
        console.error(err);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const current = data?.[timeframe];

  return (
    <section className="card overflow-hidden grain anim-fade-up">
      <div className="flex items-center justify-between gap-3 border-b border-stone-200/80 px-5 py-4 dark:border-[var(--hairline)]">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[var(--brand-green-soft)] text-[var(--brand-green)]">
            <TrendingUp className="h-4 w-4" />
          </span>
          <div>
            <h3 className="text-[14px] font-semibold text-ink leading-none">{t("movers.title")}</h3>
            <p className="text-[11px] text-ink-3 mt-1">{t("movers.subtitle")}</p>
          </div>
        </div>
        <TimeframeToggle value={timeframe} onChange={setTimeframe} />
      </div>

      {loading ? (
        <Placeholder>
          <Loader2 className="h-5 w-5 animate-spin text-stone-400" />
          <span>{t("movers.loading")}</span>
        </Placeholder>
      ) : error || !current ? (
        <Placeholder>
          <span className="text-stone-500">{error || t("movers.noData")}</span>
        </Placeholder>
      ) : (
        <div className="grid md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-stone-200/80 dark:divide-[var(--hairline)]">
          <MoverList title={t("movers.topGainers")} rows={current.gainers} dir="up" onSelect={onSelectTicker} />
          <MoverList title={t("movers.topLosers")} rows={current.losers} dir="down" onSelect={onSelectTicker} />
        </div>
      )}
    </section>
  );
}

function TimeframeToggle({
  value,
  onChange,
}: {
  value: Timeframe;
  onChange: (t: Timeframe) => void;
}) {
  return (
    <div className="flex rounded-lg bg-stone-100 p-0.5 dark:bg-white/[0.06]">
      {TF_ORDER.map((tf) => (
        <button
          key={tf}
          onClick={() => onChange(tf)}
          className={cn(
            "px-2.5 py-1 text-[11.5px] font-semibold rounded-md transition-all",
            value === tf
              ? "bg-white text-[var(--brand-navy)] shadow-sm dark:bg-white/10 dark:text-white"
              : "text-stone-500 hover:text-ink"
          )}
        >
          {TF_LABELS[tf]}
        </button>
      ))}
    </div>
  );
}

function MoverList({
  title,
  rows,
  dir,
  onSelect,
}: {
  title: string;
  rows: Mover[];
  dir: "up" | "down";
  onSelect?: (t: string) => void;
}) {
  const t = useT();
  const isUp = dir === "up";
  const Icon = isUp ? TrendingUp : TrendingDown;
  return (
    <div className="p-3">
      <div className="flex items-center gap-1.5 px-2 pb-2">
        <Icon className={cn("h-3.5 w-3.5", isUp ? "text-[var(--brand-green)]" : "text-rose-500")} />
        <span className="eyebrow text-stone-500">{title}</span>
      </div>
      <div className="space-y-0.5">
        {rows.length === 0 && (
          <div className="p-3 text-center text-[13px] text-stone-500">{t("movers.noDataShort")}</div>
        )}
        {rows.map((m, i) => (
          <MoverRow key={m.ticker} rank={i + 1} mover={m} up={isUp} onSelect={onSelect} />
        ))}
      </div>
    </div>
  );
}

function MoverRow({
  rank,
  mover,
  up,
  onSelect,
}: {
  rank: number;
  mover: Mover;
  up: boolean;
  onSelect?: (t: string) => void;
}) {
  return (
    <button
      onClick={() => onSelect?.(mover.ticker)}
      className="group flex w-full items-center gap-3 rounded-lg p-2 text-left transition-colors hover:bg-stone-50 dark:hover:bg-white/[0.04]"
    >
      <span className="w-4 shrink-0 text-center mono text-[11px] font-medium text-stone-400">
        {rank}
      </span>
      <TickerLogo ticker={mover.ticker} size="sm" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1 text-[13px] font-semibold text-ink">
          {mover.ticker.replace(".CA", "")}
          <ArrowUpRight className="h-3 w-3 text-stone-300 opacity-0 transition-opacity group-hover:opacity-100" />
        </div>
        <div className="truncate max-w-[150px] text-[11px] text-stone-500">{mover.name}</div>
      </div>
      <span
        className={cn(
          "mono text-[13px] font-semibold tabular-nums",
          up ? "text-[var(--brand-green)]" : "text-rose-600"
        )}
      >
        {formatPercent(mover.change)}
      </span>
    </button>
  );
}

function Placeholder({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 p-12 text-[13px] text-stone-500">
      {children}
    </div>
  );
}
