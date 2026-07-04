import { useEffect, useState } from "react";
import { Loader2, Layers } from "lucide-react";
import { api } from "../../services/api/client";
import { cn, formatPercent } from "../../lib/utils";
import { useT, tEnum } from "../../lib/i18n";

interface SectorRow {
  sector: string;
  change: number;
  count: number;
}

type Timeframe = "month" | "quarter" | "year" | "5year";

type SectorData = Record<Timeframe, SectorRow[]>;

const TF_ORDER: Timeframe[] = ["month", "quarter", "year", "5year"];
const TF_LABELS: Record<Timeframe, string> = {
  month: "1M",
  quarter: "3M",
  year: "1Y",
  "5year": "5Y",
};

export function SectorPerformance() {
  const t = useT();
  const [data, setData] = useState<SectorData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [timeframe, setTimeframe] = useState<Timeframe>("month");

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        setLoading(true);
        const res = await api.get<{ status: string; data: SectorData }>("/market/sectors");
        if (!alive) return;
        if (res.status === "ok") setData(res.data);
        else setError("Failed to load sector data");
      } catch (err) {
        if (alive) setError("Error fetching sectors");
        console.error(err);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const rows = data?.[timeframe] ?? [];
  const maxAbs = Math.max(...rows.map((r) => Math.abs(r.change)), 1);

  return (
    <section className="card overflow-hidden grain anim-fade-up" style={{ animationDelay: "80ms" }}>
      <div className="flex items-center justify-between gap-3 border-b border-stone-200/80 px-5 py-4 dark:border-[var(--hairline)]">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[var(--brand-navy)]/10 text-[var(--brand-navy)]">
            <Layers className="h-4 w-4" />
          </span>
          <div>
            <h3 className="text-[14px] font-semibold text-ink leading-none">{t("sectors.title")}</h3>
            <p className="text-[11px] text-ink-3 mt-1">{t("sectors.subtitle")}</p>
          </div>
        </div>
        <div className="flex rounded-lg bg-stone-100 p-0.5 dark:bg-white/[0.06]">
          {TF_ORDER.map((tf) => (
            <button
              key={tf}
              onClick={() => setTimeframe(tf)}
              className={cn(
                "px-2.5 py-1 text-[11.5px] font-semibold rounded-md transition-all",
                timeframe === tf
                  ? "bg-white text-[var(--brand-navy)] shadow-sm dark:bg-white/10 dark:text-white"
                  : "text-stone-500 hover:text-ink"
              )}
            >
              {TF_LABELS[tf]}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="flex flex-col items-center justify-center gap-3 p-12 text-[13px] text-stone-500">
          <Loader2 className="h-5 w-5 animate-spin text-stone-400" />
          <span>{t("sectors.loading")}</span>
        </div>
      ) : error || rows.length === 0 ? (
        <div className="p-12 text-center text-[13px] text-stone-500">{error || t("sectors.noData")}</div>
      ) : (
        <div className="space-y-3 p-5">
          {rows.map((r, i) => {
            const pos = r.change >= 0;
            const width = Math.max((Math.abs(r.change) / maxAbs) * 100, 3);
            return (
              <div key={r.sector} className="grid grid-cols-[1fr_auto] items-center gap-3">
                <div className="min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-2 truncate text-[13px] font-medium text-ink">
                      <span className="mono text-[10.5px] text-stone-400">{i + 1}</span>
                      {tEnum(t, "sector", r.sector)}
                    </span>
                  </div>
                  <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-stone-100 dark:bg-white/[0.06]">
                    <div
                      className={cn(
                        "h-full rounded-full transition-all",
                        pos ? "bg-[var(--brand-green)]" : "bg-rose-400"
                      )}
                      style={{ width: `${width}%` }}
                    />
                  </div>
                </div>
                <div className="text-right">
                  <div
                    className={cn(
                      "mono text-[13px] font-semibold tabular-nums",
                      pos ? "text-[var(--brand-green)]" : "text-rose-600"
                    )}
                  >
                    {formatPercent(r.change)}
                  </div>
                  <div className="text-[10px] text-stone-400">{t("sectors.names", { count: r.count })}</div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
