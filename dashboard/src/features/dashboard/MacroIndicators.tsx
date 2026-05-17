import {
  ArrowDownRight,
  ArrowUpRight,
  Minus,
  Landmark,
  Globe,
  TrendingUp,
  TrendingDown,
  Fuel,
  CircleDollarSign,
  AlertTriangle,
} from "lucide-react";
import { useMacro, type MacroContext } from "../../hooks/useMacro";
import { cn } from "../../lib/utils";

function pct(v: number | null | undefined, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

function num(v: number | null | undefined, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toFixed(digits);
}

function TrendBadge({ trend, sub }: { trend: string; sub?: string }) {
  const map: Record<string, { cls: string; icon: React.ReactNode; label: string }> = {
    bullish:       { cls: "bg-emerald-50 text-emerald-700 border-emerald-200", icon: <ArrowUpRight className="h-3 w-3" />,   label: "Bullish" },
    appreciating:  { cls: "bg-emerald-50 text-emerald-700 border-emerald-200", icon: <ArrowUpRight className="h-3 w-3" />,   label: "Appreciating" },
    bearish:       { cls: "bg-rose-50 text-rose-700 border-rose-200",          icon: <ArrowDownRight className="h-3 w-3" />, label: "Bearish" },
    depreciating:  { cls: "bg-rose-50 text-rose-700 border-rose-200",          icon: <ArrowDownRight className="h-3 w-3" />, label: "Depreciating" },
    stable:        { cls: "bg-stone-100 text-stone-600 border-stone-200",      icon: <Minus className="h-3 w-3" />,         label: "Stable" },
    neutral:       { cls: "bg-stone-100 text-stone-600 border-stone-200",      icon: <Minus className="h-3 w-3" />,         label: "Neutral" },
    unknown:       { cls: "bg-amber-50 text-amber-700 border-amber-200",       icon: <AlertTriangle className="h-3 w-3" />, label: "Unknown" },
  };
  const m = map[trend] ?? map.unknown;
  return (
    <span className={cn(
      "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10.5px] font-medium border",
      m.cls
    )}>
      {m.icon}
      {sub ?? m.label}
    </span>
  );
}

function Stat({
  label,
  value,
  sub,
  valueClass,
}: {
  label: string;
  value: string;
  sub?: React.ReactNode;
  valueClass?: string;
}) {
  const isEmpty = value === "—" || value === "" || value == null;
  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      <div className="text-[10.5px] uppercase tracking-wider text-stone-500 dark:text-[var(--ink-3)] truncate">
        {label}
      </div>
      <div
        className={cn(
          "display font-semibold leading-tight tabular-nums",
          isEmpty
            ? "text-[18px] text-stone-300 dark:text-[var(--ink-3)]"
            : "text-[22px] text-ink",
          !isEmpty && valueClass
        )}
      >
        {value}
      </div>
      {sub !== undefined && (
        <div className="text-[11px] text-ink-3 leading-snug mt-0.5">{sub}</div>
      )}
    </div>
  );
}

function GroupCard({
  title,
  icon,
  children,
  className,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn(
      "rounded-2xl border border-stone-200 bg-white p-5 flex flex-col gap-4",
      "dark:bg-[var(--paper)] dark:border-[var(--hairline)]",
      className
    )}>
      <div className="flex items-center gap-2 text-[10.5px] uppercase tracking-[0.14em] text-stone-500 dark:text-[var(--ink-3)]">
        <span className="h-6 w-6 rounded-md bg-stone-100 dark:bg-[var(--hairline)] flex items-center justify-center text-stone-600 dark:text-[var(--ink-2)]">
          {icon}
        </span>
        {title}
      </div>
      {children}
    </div>
  );
}

function MacroBody({ m }: { m: MacroContext }) {
  const realRatePositive = (m.real_rate ?? 0) > 0;
  const realRateClass = realRatePositive ? "text-emerald-700" : "text-rose-700";
  const fxClass =
    m.fx_trend === "appreciating" ? "text-emerald-700"
      : m.fx_trend === "depreciating" ? "text-rose-700"
      : "text-ink";
  const egx30Class =
    m.egx30_trend === "bullish" ? "text-emerald-700"
      : m.egx30_trend === "bearish" ? "text-rose-700"
      : "text-ink";

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
      {/* ── Rates group ──────────────────────────────────────────────── */}
      <GroupCard title="Rates & inflation" icon={<Landmark className="h-3.5 w-3.5" />}>
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <Stat
            label="CBE policy"
            value={pct(m.cbe_policy_rate, 2)}
            sub={`91d T-bill ${pct(m.tbill_yield_91d, 2)}`}
          />
          <Stat
            label="Real rate"
            value={pct(m.real_rate, 2)}
            valueClass={realRateClass}
            sub={realRatePositive ? "Tight monetary stance" : "Negative — repression"}
          />
          <Stat
            label="CPI (YoY)"
            value={pct(m.egypt_cpi, 2)}
            sub="Egypt headline inflation"
          />
          <Stat
            label="Spread"
            value={pct(m.spread_vs_tbill, 2)}
            sub="CBE − 91d T-bill"
          />
        </div>
      </GroupCard>

      {/* ── FX & commodities ────────────────────────────────────────── */}
      <GroupCard title="Currency & commodities" icon={<CircleDollarSign className="h-3.5 w-3.5" />}>
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <Stat
            label="USD / EGP"
            value={num(m.usd_egp, 2)}
            valueClass={fxClass}
            sub={
              <div className="flex items-center gap-1.5 mt-0.5">
                <TrendBadge trend={m.fx_trend} />
                <span className="text-[11px] text-stone-500">
                  1m {pct(m.usd_egp_1m_return, 2)}
                </span>
              </div>
            }
          />
          <Stat
            label="Brent crude"
            value={m.brent_usd !== null ? `$${num(m.brent_usd, 2)}` : "—"}
            sub="USD / barrel"
          />
        </div>
        <div className="text-[10.5px] text-stone-400 mt-auto leading-snug">
          {m.fx_trend === "depreciating"
            ? "EGP weakness raises import costs and USD-debt burden."
            : m.fx_trend === "appreciating"
            ? "EGP strength eases imports; watch capital flows."
            : "EGP largely stable vs USD."}
        </div>
      </GroupCard>

      {/* ── Market & programme ──────────────────────────────────────── */}
      <GroupCard title="Market & programme" icon={<TrendingUp className="h-3.5 w-3.5" />}>
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <Stat
            label="EGX30 (1m)"
            value={pct(m.egx30_return_1m, 2)}
            valueClass={egx30Class}
            sub={<TrendBadge trend={m.egx30_trend} />}
          />
          <Stat
            label="IMF programme"
            value={m.imf_program_active ? "Active" : "Inactive"}
            valueClass={m.imf_program_active ? "text-emerald-700" : "text-stone-500"}
            sub={
              m.imf_program_active
                ? `USD ${m.imf_program_size_bn}bn EFF/SBA`
                : "No external anchor"
            }
          />
        </div>
        <div className="text-[10.5px] text-stone-400 mt-auto leading-snug">
          {m.imf_program_active
            ? "IMF anchors fiscal credibility & FX stability."
            : "Sovereign risk premium without an external anchor."}
        </div>
      </GroupCard>
    </div>
  );
}

export function MacroIndicators({ className }: { className?: string }) {
  const { data, isLoading, isError, error, refetch, isFetching } = useMacro();
  const asOf = data?.macro_context?.as_of_date;

  return (
    <section className={cn("space-y-3", className)}>
      <header className="flex items-end justify-between gap-4 flex-wrap">
        <div className="flex items-baseline gap-3">
          <h2 className="text-[20px] md:text-[22px] font-semibold leading-tight text-ink">
            EGX macro snapshot
          </h2>
          {asOf && (
            <span className="text-[11.5px] text-stone-500 dark:text-[var(--ink-3)]">
              · as of <span className="tabular-nums">{asOf}</span>
            </span>
          )}
        </div>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="text-[12px] px-3 py-1.5 rounded-md border border-stone-200 hover:bg-stone-50 disabled:opacity-50 text-ink-2
            dark:border-[var(--hairline)] dark:hover:bg-[var(--hairline)]"
        >
          {isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </header>

      {isLoading && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div
              key={i}
              className="rounded-2xl border border-stone-200 bg-white p-5 h-[152px] animate-pulse
                dark:bg-[var(--paper)] dark:border-[var(--hairline)]"
            />
          ))}
        </div>
      )}

      {isError && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-[13px] text-rose-700">
          Failed to load macro data: {error instanceof Error ? error.message : "unknown error"}
        </div>
      )}

      {data?.macro_context && <MacroBody m={data.macro_context} />}
    </section>
  );
}
