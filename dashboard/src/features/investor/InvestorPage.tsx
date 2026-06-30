import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Brain,
  ChevronDown,
  Clock,
  Eye,
  FileText,
  HardDrive,
  Shield,
  Target,
  TrendingDown,
  TrendingUp,
  User,
  X,
} from "lucide-react";
import { endpoints } from "../../services/api/endpoints";
import type {
  DataFreshnessResponse,
  InvestorProfile,
  ShadowRun,
} from "../../services/api/types";
import { cn } from "../../lib/utils";
import { useAppStore } from "../../store/appStore";
import { DecisionCard, fromShadowRun } from "../shared/DecisionCard";

/* ═══════════════════════════════════════════════════════════════════════
   Investor Page — Shadow Portfolio & Run History
   ═══════════════════════════════════════════════════════════════════════ */

export function InvestorPage() {
  const activeProfileId = useAppStore((s) => s.activeProfileId);
  const profileQ = useQuery({
    queryKey: ["profile", activeProfileId],
    queryFn: () => endpoints.getProfile(activeProfileId!),
    enabled: !!activeProfileId,
  });
  const runsQ = useQuery({
    queryKey: ["shadow-runs"],
    queryFn: () => endpoints.listShadowRuns({ limit: 50 }),
    refetchInterval: 30_000,
  });

  const freshnessQ = useQuery({
    queryKey: ["data-freshness"],
    queryFn: () => endpoints.dataFreshness(),
    refetchInterval: 60_000,
  });

  const [selectedRun, setSelectedRun] = useState<ShadowRun | null>(null);
  const profile = profileQ.data;
  const runs = runsQ.data ?? [];
  const freshness = freshnessQ.data ?? null;

  return (
    <div className="space-y-8">
      {/* Page header */}
      <header>
        <div className="eyebrow mb-3">Recommendations</div>
        <h1 className="display text-[36px] md:text-[42px] font-semibold leading-[1.05] text-ink">
          Your past recommendations
        </h1>
        <p className="text-[14px] text-ink-3 mt-3 max-w-xl leading-relaxed">
          StockHive recommendations for{" "}
          <strong className="text-ink-2">{profile?.name ?? "…"}</strong>.
          These are <strong className="text-ink-2">suggestions for review</strong>
          {" "}&mdash; the investor decides before any action.
        </p>
      </header>

      {/* Persona strip — compact, storytelling context */}
      {profile && <PersonaStrip profile={profile} />}

      {/* Latest decision hero */}
      {runs.length > 0 && (
        <LatestDecision run={runs[0]} freshness={freshness} />
      )}

      {/* Decision history table */}
      <section>
        <div className="flex items-center gap-2 mb-4">
          <Clock className="h-4 w-4 text-stone-500" />
          <span className="text-[14px] font-semibold text-ink">
            Recommendation history
          </span>
          <span className="text-[12px] text-ink-3">
            ({runs.length} recommendation{runs.length !== 1 ? "s" : ""})
          </span>
        </div>

        {runs.length === 0 ? (
          <div className="card p-8 text-center text-ink-3 text-[14px]">
            No recommendations yet. Get a recommendation from the Home page to
            create the first entry.
          </div>
        ) : (
          <div className="card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="border-b border-stone-200 dark:border-[var(--hairline)] bg-stone-50 dark:bg-white/[0.02]">
                    <th className="text-left px-4 py-3 font-medium text-stone-500">
                      Date
                    </th>
                    <th className="text-left px-4 py-3 font-medium text-stone-500">
                      Ticker
                    </th>
                    <th className="text-left px-4 py-3 font-medium text-stone-500">
                      Recommendation
                    </th>
                    <th className="text-left px-4 py-3 font-medium text-stone-500">
                      Confidence
                    </th>
                    <th className="text-left px-4 py-3 font-medium text-stone-500 hidden md:table-cell">
                      Summary
                    </th>
                    <th className="px-4 py-3" />
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => (
                    <RunRow
                      key={run.id}
                      run={run}
                      onSelect={() => setSelectedRun(run)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>

      {/* Run detail modal */}
      {selectedRun && (
        <RunDetailModal
          run={selectedRun}
          onClose={() => setSelectedRun(null)}
        />
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   Persona Strip — compact storytelling context
   ═══════════════════════════════════════════════════════════════════════ */

function PersonaStrip({ profile }: { profile: InvestorProfile }) {
  const traits = [
    { label: "Style", value: profile.trading_style },
    { label: "Risk", value: profile.risk_tolerance },
    { label: "Capital", value: `${(profile.capital_size / 1e6).toFixed(1)}M EGP` },
    { label: "Benchmark", value: profile.benchmark_target },
    { label: "Horizon", value: (profile.investment_horizon || "").replace(/_/g, " ") },
  ];

  return (
    <section className="card overflow-hidden">
      <div className="flex items-center gap-4 px-5 py-3.5">
        {/* Avatar circle */}
        <div className="h-10 w-10 shrink-0 rounded-full bg-stone-200 dark:bg-white/10 flex items-center justify-center">
          <User className="h-5 w-5 text-stone-500 dark:text-[var(--ink-3)]" />
        </div>
        {/* Name + role */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[15px] font-semibold text-ink truncate">
              {profile.name}
            </span>
            <span className="text-[10.5px] px-2 py-0.5 rounded-full bg-stone-100 text-stone-500 dark:bg-white/[0.06] dark:text-[var(--ink-3)] font-medium uppercase tracking-wider whitespace-nowrap">
              Demo shadow investor
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1">
            {traits.map((t) => (
              <span key={t.label} className="text-[11.5px] text-stone-500 dark:text-[var(--ink-3)]">
                {t.label}:{" "}
                <span className="text-ink-2 font-medium capitalize">{t.value}</span>
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className="px-5 py-2 bg-stone-50/50 dark:bg-white/[0.01] border-t border-stone-200/80 dark:border-[var(--hairline)] text-[11px] text-stone-500 dark:text-[var(--ink-3)]">
        Demo investor profile. Recommendations are for review only &mdash; not
        executed trades or investment advice.
      </div>
    </section>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   Latest Decision Hero
   ═══════════════════════════════════════════════════════════════════════ */

function LatestDecision({
  run,
  freshness,
}: {
  run: ShadowRun;
  freshness: DataFreshnessResponse | null;
}) {
  const dir =
    run.signal === "BUY"
      ? "up"
      : run.signal === "SELL"
      ? "down"
      : ("flat" as const);
  const Arrow =
    dir === "up" ? ArrowUpRight : dir === "down" ? ArrowDownRight : ArrowRight;
  const head =
    dir === "up"
      ? "bg-gradient-to-br from-emerald-600 to-teal-600"
      : dir === "down"
      ? "bg-gradient-to-br from-rose-600 to-orange-600"
      : "bg-gradient-to-br from-stone-700 to-stone-800";

  const decisionProps = fromShadowRun(run.pipeline_audit);

  return (
    <section className="card-elevated overflow-hidden">
      <div className={cn("p-5 text-white relative overflow-hidden", head)}>
        <div className="flex items-center gap-1.5 text-white/70 text-[10.5px] font-medium uppercase tracking-wider">
          <Eye className="h-3.5 w-3.5" />
          Latest recommendation &mdash; {run.ticker}
        </div>
        <div className="mt-2 flex items-center gap-3">
          <Arrow className="h-8 w-8" strokeWidth={2.4} />
          <span className="display text-[38px] font-semibold leading-none">
            {run.signal || "—"}
          </span>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3 text-[12px]">
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-white/15 font-medium">
            Confidence: {run.confidence_overall != null ? (run.confidence_overall * 100).toFixed(0) : "—"}%
          </span>
          {run.latest_price && (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-white/15">
              Price: {run.latest_price.toFixed(2)} EGP
            </span>
          )}
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-white/15">
            {new Date(run.created_at).toLocaleString()}
          </span>
        </div>
        {/* Recommendation disclaimer */}
        <div className="mt-3 flex items-center gap-1.5 text-white/60 text-[11px]">
          <Shield className="h-3 w-3" />
          Recommendation only &mdash; the investor reviews before any action
        </div>
        {/* Decision explanation — replaces hardcoded HOLD text */}
        {decisionProps.headline && (
          <div className="mt-3">
            <DecisionCard
              {...decisionProps}
              variant="hero"
            />
          </div>
        )}
        {/* Fallback for old runs without pipeline_audit */}
        {!decisionProps.headline && run.signal === "HOLD" && (
          <div className="mt-2.5 flex items-start gap-1.5 text-white/50 text-[11px] leading-relaxed max-w-lg">
            <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
            <span>
              StockHive recommended HOLD. Detailed decision trail is not
              available for this recommendation.
            </span>
          </div>
        )}
      </div>

      {/* Confidence breakdown */}
      <div className="grid grid-cols-3 gap-px bg-stone-200/80 dark:bg-[var(--hairline)]">
        <ConfTile
          label="Technical"
          value={run.confidence_technical}
          icon={Target}
        />
        <ConfTile
          label="Fundamental"
          value={run.confidence_fundamental}
          icon={FileText}
        />
        <ConfTile
          label="Sentiment"
          value={run.confidence_sentiment}
          icon={Brain}
        />
      </div>

      {/* Macro context strip */}
      {run.macro_context && <MacroStrip macro={run.macro_context} />}

      {/* Data freshness strip */}
      {freshness && <FreshnessStrip run={run} freshness={freshness} />}
    </section>
  );
}

function FreshnessStrip({
  run,
  freshness,
}: {
  run: ShadowRun;
  freshness: DataFreshnessResponse;
}) {
  const bySource = (name: string) =>
    freshness.sources.find((s) => s.source === name);

  const price = bySource("price/yfinance");
  const macro = bySource("macro");
  const news = bySource("news");
  const social = bySource("social");
  const memory = bySource("memory");

  const dot = (status: string | undefined) => {
    if (!status) return "bg-stone-300";
    if (status.startsWith("Fresh")) return "bg-emerald-500";
    if (status === "Available") return "bg-sky-500";
    if (status === "Stale") return "bg-amber-500";
    return "bg-rose-500";
  };

  const items = [
    {
      label: "Price as-of",
      value: run.price_date || price?.age_human || "—",
      status: price?.status,
    },
    {
      label: "Macro",
      value:
        run.macro_context?.as_of_date
          ? `as-of ${run.macro_context.as_of_date}`
          : macro?.age_human || "—",
      status: macro?.status,
    },
    {
      label: "News checked",
      value: news?.age_human ?? "—",
      status: news?.status,
    },
    {
      label: "Social checked",
      value: social?.age_human ?? "—",
      status: social?.status,
    },
    {
      label: "Memory",
      value:
        memory?.status === "Available"
          ? `${memory.total_documents ?? 0} docs`
          : memory?.status ?? "—",
      status: memory?.status,
    },
  ];

  return (
    <div className="flex flex-wrap gap-4 px-5 py-2.5 bg-stone-50/50 dark:bg-white/[0.01] border-t border-stone-200/80 dark:border-[var(--hairline)]">
      <div className="flex items-center gap-1.5 mr-1">
        <HardDrive className="h-3 w-3 text-stone-400" />
        <span className="text-[10.5px] font-medium text-stone-500 uppercase tracking-wider">
          Data
        </span>
      </div>
      {items.map((it) => (
        <div key={it.label} className="flex items-center gap-1.5 text-[11px]">
          <span className={cn("h-1.5 w-1.5 rounded-full", dot(it.status))} />
          <span className="text-stone-500">{it.label}:</span>
          <span className="font-medium text-ink">{it.value}</span>
        </div>
      ))}
    </div>
  );
}

function ConfTile({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: number | null;
  icon: typeof Target;
}) {
  const pct = value != null ? (value * 100).toFixed(0) : "—";
  return (
    <div className="bg-white dark:bg-[var(--paper)] p-4 flex items-center gap-3">
      <Icon className="h-4 w-4 text-stone-400 shrink-0" />
      <div>
        <div className="eyebrow text-stone-500">{label}</div>
        <div className="text-[18px] font-semibold text-ink mt-0.5 tabular-nums">
          {pct}%
        </div>
      </div>
    </div>
  );
}

function MacroStrip({ macro }: { macro: Record<string, unknown> }) {
  const items = [
    {
      label: "CBE rate",
      value: macro.cbe_policy_rate
        ? `${((macro.cbe_policy_rate as number) * 100).toFixed(1)}%`
        : "—",
    },
    {
      label: "USD/EGP",
      value: macro.usd_egp
        ? `${(macro.usd_egp as number).toFixed(2)}`
        : "—",
    },
    {
      label: "FX trend",
      value: (macro.fx_trend as string) || "—",
    },
    {
      label: "EGX30 1m",
      value: macro.egx30_return_1m
        ? `${((macro.egx30_return_1m as number) * 100).toFixed(1)}%`
        : "—",
    },
    {
      label: "EGX30 trend",
      value: (macro.egx30_trend as string) || "—",
    },
  ];
  return (
    <div className="flex flex-wrap gap-4 px-5 py-3 bg-stone-50/50 dark:bg-white/[0.01] border-t border-stone-200/80 dark:border-[var(--hairline)]">
      {items.map((it) => (
        <div key={it.label} className="text-[11.5px]">
          <span className="text-stone-500">{it.label}: </span>
          <span className="font-medium text-ink capitalize">{it.value}</span>
        </div>
      ))}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   Run Row
   ═══════════════════════════════════════════════════════════════════════ */

function RunRow({
  run,
  onSelect,
}: {
  run: ShadowRun;
  onSelect: () => void;
}) {
  const sigColor =
    run.signal === "BUY"
      ? "text-emerald-700 bg-emerald-50 border-emerald-200"
      : run.signal === "SELL"
      ? "text-rose-700 bg-rose-50 border-rose-200"
      : "text-stone-700 bg-stone-50 border-stone-200";

  // Build headline snippet from pipeline_audit or fall back to signal
  const audit = run.pipeline_audit;
  const chain = audit?.signal_chain as Record<string, string | null> | undefined;
  const riskBlocked = !!audit?.risk_veto;
  const headline = riskBlocked && chain?.trader && chain.trader !== "HOLD"
    ? `${chain.trader} blocked by risk rules`
    : chain?.final
    ? chain.final === "HOLD"
      ? "Insufficient evidence for action"
      : `${chain.final} — passed risk review`
    : null;

  return (
    <tr
      className="border-b border-stone-100 dark:border-[var(--hairline)] hover:bg-stone-50/50 dark:hover:bg-white/[0.02] cursor-pointer transition-colors"
      onClick={onSelect}
    >
      <td className="px-4 py-3 text-ink-2 tabular-nums">
        {new Date(run.created_at).toLocaleDateString()}{" "}
        <span className="text-ink-3">
          {new Date(run.created_at).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </span>
      </td>
      <td className="px-4 py-3 font-medium text-ink">{run.ticker}</td>
      <td className="px-4 py-3">
        <span
          className={cn(
            "px-2 py-0.5 rounded-md border text-[11px] font-semibold uppercase",
            sigColor
          )}
        >
          {run.signal || "—"}
        </span>
      </td>
      <td className="px-4 py-3 tabular-nums text-ink-2">
        {run.confidence_overall != null
          ? `${(run.confidence_overall * 100).toFixed(0)}%`
          : "—"}
      </td>
      <td className="px-4 py-3 text-ink-3 text-[12px] max-w-[200px] truncate hidden md:table-cell">
        {headline || "—"}
      </td>
      <td className="px-4 py-3 text-ink-3">
        <ChevronDown className="h-4 w-4" />
      </td>
    </tr>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   Run Detail Modal
   ═══════════════════════════════════════════════════════════════════════ */

function RunDetailModal({
  run,
  onClose,
}: {
  run: ShadowRun;
  onClose: () => void;
}) {
  // Close on Escape
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const decisionProps = fromShadowRun(run.pipeline_audit);

  // Build developer payload from pipeline_audit + investor_context_snapshot
  const devData: Record<string, unknown> | null =
    run.pipeline_audit || run.investor_context_snapshot
      ? {
          ...(run.pipeline_audit ? { pipeline_audit: run.pipeline_audit } : {}),
          ...(run.investor_context_snapshot
            ? { investor_context_snapshot: run.investor_context_snapshot }
            : {}),
          ...(run.model_provider ? { model: `${run.model_provider} / ${run.model_name}` } : {}),
          ...(run.duration_seconds ? { duration_seconds: run.duration_seconds } : {}),
        }
      : null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-16 px-4">
      <div
        className="absolute inset-0 bg-stone-900/50 backdrop-blur-[2px]"
        onClick={onClose}
      />
      <div className="relative w-full max-w-3xl max-h-[80vh] overflow-y-auto bg-white dark:bg-[var(--paper)] rounded-2xl shadow-2xl border border-stone-200 dark:border-[var(--hairline)]">
        {/* Header */}
        <div className="sticky top-0 flex items-center justify-between px-6 py-4 border-b border-stone-200 dark:border-[var(--hairline)] bg-white/90 dark:bg-[var(--paper)]/90 backdrop-blur-md z-10">
          <div>
            <div className="text-[16px] font-semibold text-ink">
              {run.ticker} &mdash; {run.signal || "—"}
            </div>
            <div className="text-[12px] text-ink-3">
              {new Date(run.created_at).toLocaleString()}
            </div>
          </div>
          <button
            onClick={onClose}
            className="h-8 w-8 inline-flex items-center justify-center rounded-lg hover:bg-stone-100 dark:hover:bg-white/5 text-stone-500"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-6 space-y-6">
          {/* Recommendation disclaimer */}
          <div className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-amber-50 border border-amber-200 text-[12px] text-amber-800 dark:bg-amber-900/20 dark:border-amber-900/40 dark:text-amber-300">
            <Shield className="h-4 w-4 shrink-0" />
            This is a recommendation for review &mdash; not an executed trade
            or investment advice. The investor decides before any action.
          </div>

          {/* Decision explanation — the headline + decision trail */}
          <DecisionCard
            {...decisionProps}
            variant="card"
          />

          {/* Confidence scores */}
          <DetailSection title="Confidence scores">
            <div className="grid grid-cols-4 gap-3">
              <ScoreTile
                label="Overall"
                value={run.confidence_overall}
                highlight
              />
              <ScoreTile
                label="Technical"
                value={run.confidence_technical}
              />
              <ScoreTile
                label="Fundamental"
                value={run.confidence_fundamental}
              />
              <ScoreTile
                label="Sentiment"
                value={run.confidence_sentiment}
              />
            </div>
          </DetailSection>

          {/* Research summary */}
          {run.judge_decision_summary && (
            <DetailSection title="Recommendation reasoning">
              <p className="text-[13px] text-ink-2 leading-relaxed whitespace-pre-wrap">
                {run.judge_decision_summary}
              </p>
            </DetailSection>
          )}

          {/* Opportunity & risk */}
          <div className="grid gap-4 md:grid-cols-2">
            {run.bull_thesis_summary && (
              <DetailSection title="Opportunity case" icon={TrendingUp}>
                <p className="text-[13px] text-ink-2 leading-relaxed whitespace-pre-wrap">
                  {run.bull_thesis_summary}
                </p>
              </DetailSection>
            )}
            {run.bear_thesis_summary && (
              <DetailSection title="Risk case" icon={TrendingDown}>
                <p className="text-[13px] text-ink-2 leading-relaxed whitespace-pre-wrap">
                  {run.bear_thesis_summary}
                </p>
              </DetailSection>
            )}
          </div>

          {/* Macro context */}
          {run.macro_context && (
            <DetailSection title="Macro context">
              <MacroStrip macro={run.macro_context} />
            </DetailSection>
          )}

          {/* Warnings */}
          {run.warnings.length > 0 && (
            <DetailSection title="Warnings" icon={AlertTriangle}>
              <ul className="space-y-1">
                {run.warnings.map((w, i) => (
                  <li
                    key={i}
                    className="text-[12px] text-amber-700 dark:text-amber-400 flex items-start gap-2"
                  >
                    <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                    {w}
                  </li>
                ))}
              </ul>
            </DetailSection>
          )}

          {/* Developer details — collapsible at bottom */}
          {devData && (
            <DecisionCard
              variant="card"
              developerData={devData}
              developerLabel="Developer details"
            />
          )}
        </div>
      </div>
    </div>
  );
}

function DetailSection({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon?: typeof Target;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-2.5">
        {Icon && <Icon className="h-4 w-4 text-stone-400" />}
        <span className="text-[13px] font-semibold text-ink">{title}</span>
      </div>
      {children}
    </div>
  );
}

function ScoreTile({
  label,
  value,
  highlight,
}: {
  label: string;
  value: number | null;
  highlight?: boolean;
}) {
  const pct = value != null ? (value * 100).toFixed(0) : "—";
  return (
    <div
      className={cn(
        "rounded-xl border p-3 text-center",
        highlight
          ? "border-stone-300 bg-stone-50 dark:border-[var(--hairline)] dark:bg-white/[0.03]"
          : "border-stone-200 dark:border-[var(--hairline)]"
      )}
    >
      <div className="eyebrow text-stone-500">{label}</div>
      <div
        className={cn(
          "text-[20px] font-semibold mt-1 tabular-nums",
          highlight ? "text-ink" : "text-ink-2"
        )}
      >
        {pct}%
      </div>
    </div>
  );
}
