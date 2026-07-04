import { useEffect, useState } from "react";
import { Loader2, DollarSign, Percent, Info, Factory, Activity } from "lucide-react";
import { api } from "../../services/api/client";
import { cn, formatPercent } from "../../lib/utils";
import { useT } from "../../lib/i18n";

interface MacroData {
  cbe_policy_rate: number;
  usd_egp: number;
  usd_egp_1m_return: number | null;
  fx_trend: string;
  egx30_return_1m: number | null;
  egx30_trend: string;
  tbill_yield_91d: number;
  egypt_cpi: number;
  real_rate: number;
  spread_vs_tbill: number;
  brent_usd: number | null;
  imf_program_active: boolean;
  imf_program_size_bn: number;
  as_of_date: string;
}

export function MacroIndicators() {
  const t = useT();
  const [data, setData] = useState<MacroData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchMacro() {
      try {
        setLoading(true);
        const res = await api.get<{ status: string; macro_context: MacroData }>("/macro");
        if (res.status === "ok" && res.macro_context) {
          setData(res.macro_context);
        } else {
          setError("Failed to load macro data");
        }
      } catch (err) {
        setError("Error fetching macro indicators");
        console.error(err);
      } finally {
        setLoading(false);
      }
    }
    fetchMacro();
  }, []);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center p-10 card grain">
        <Loader2 className="h-6 w-6 animate-spin text-stone-400 mb-3" />
        <span className="text-stone-500 text-[13px]">{t("macro.loading")}</span>
      </div>
    );
  }

  if (error || !data) {
    return null;
  }

  const formatVal = (val: number | null, prefix = "", suffix = "") => {
    if (val === null || val === undefined) return "N/A";
    return `${prefix}${val.toFixed(2)}${suffix}`;
  };

  return (
    <div className="card overflow-hidden grain anim-fade-up" style={{ animationDelay: "150ms" }}>
      <div className="border-b border-stone-200 p-4 dark:border-[var(--hairline)] flex justify-between items-center">
        <h3 className="text-[15px] font-semibold text-ink flex items-center gap-2">
          {t("macro.title")}
        </h3>
        <span className="text-[11px] text-stone-500 bg-stone-100 px-2 py-0.5 rounded-full">
          {t("macro.asOf", { date: data.as_of_date })}
        </span>
      </div>
      
      <div className="grid grid-cols-2 md:grid-cols-4 divide-y md:divide-y-0 md:divide-x divide-stone-200 dark:divide-[var(--hairline)]">
        {/* CBE Rate */}
        <div className="p-4 hover:bg-stone-50/50 transition-colors">
          <div className="flex items-center gap-2 mb-2 text-stone-500">
            <Percent className="h-4 w-4" />
            <span className="text-[12px] font-medium">{t("macro.cbe")}</span>
          </div>
          <div className="text-[20px] font-bold text-ink mb-1">
            {formatPercent(data.cbe_policy_rate)}
          </div>
          <div className="text-[11px] text-stone-500 flex items-center gap-1">
            <span className={cn(
              "font-medium",
              data.real_rate > 0 ? "text-emerald-600" : "text-rose-600"
            )}>
              {formatPercent(data.real_rate)}
            </span>
            {t("macro.realRate")}
          </div>
        </div>

        {/* Inflation */}
        <div className="p-4 hover:bg-stone-50/50 transition-colors">
          <div className="flex items-center gap-2 mb-2 text-stone-500">
            <Activity className="h-4 w-4" />
            <span className="text-[12px] font-medium">{t("macro.cpi")}</span>
          </div>
          <div className="text-[20px] font-bold text-ink mb-1">
            {formatPercent(data.egypt_cpi)}
          </div>
          <div className="text-[11px] text-stone-500">
            {t("macro.yoy")}
          </div>
        </div>

        {/* USD / EGP */}
        <div className="p-4 hover:bg-stone-50/50 transition-colors">
          <div className="flex items-center gap-2 mb-2 text-stone-500">
            <DollarSign className="h-4 w-4" />
            <span className="text-[12px] font-medium">{t("macro.usdegp")}</span>
          </div>
          <div className="text-[20px] font-bold text-ink mb-1">
            {formatVal(data.usd_egp)}
          </div>
          <div className="text-[11px] text-stone-500 flex items-center gap-1">
            {t("macro.trend1m")}
            <span className={cn(
              "font-medium uppercase text-[10px]",
              data.fx_trend === "appreciating" ? "text-emerald-600" :
              data.fx_trend === "depreciating" ? "text-rose-600" : "text-stone-500"
            )}>
              {t(`macro.fx.${data.fx_trend}`)}
            </span>
          </div>
        </div>

        {/* Brent */}
        <div className="p-4 hover:bg-stone-50/50 transition-colors">
          <div className="flex items-center gap-2 mb-2 text-stone-500">
            <Factory className="h-4 w-4" />
            <span className="text-[12px] font-medium">{t("macro.brent")}</span>
          </div>
          <div className="text-[20px] font-bold text-ink mb-1">
            {formatVal(data.brent_usd, "$")}
          </div>
          <div className="text-[11px] text-stone-500">
            {t("macro.perBarrel")}
          </div>
        </div>
      </div>
      
      {/* IMF Status */}
      <div className="bg-stone-50/50 border-t border-stone-200 p-3 px-4 dark:border-[var(--hairline)] flex items-center gap-3">
        <Info className="h-4 w-4 text-sky-600" />
        <span className="text-[12px] text-stone-600">
          <strong className="text-ink">{t("macro.imf")}</strong>{" "}
          {data.imf_program_active
            ? t("macro.imf.active", { size: data.imf_program_size_bn })
            : t("macro.imf.inactive")}
        </span>
      </div>
    </div>
  );
}
