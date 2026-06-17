import { SlidersHorizontal } from "lucide-react";
import type { InvestmentPolicy } from "../../services/api/portfolioTypes";

const OBJECTIVE = [
  ["capital_preservation", "Capital preservation"],
  ["income", "Income"],
  ["balanced", "Balanced"],
  ["growth", "Growth"],
  ["aggressive_growth", "Aggressive growth"],
] as const;
const RISK = [
  ["very_low", "Very low"], ["low", "Low"], ["medium", "Medium"], ["high", "High"], ["very_high", "Very high"],
] as const;
const HORIZON = [
  ["lt_6m", "< 6 months"], ["6_12m", "6–12 months"], ["1_3y", "1–3 years"], ["gt_3y", "> 3 years"],
] as const;

const DEFAULTS: InvestmentPolicy = {
  objective: "balanced", risk_tolerance: "medium", horizon: "1_3y", income_preference: false,
  inferred_fields: ["objective", "risk_tolerance", "horizon"], version: 1, confirmed_by_user: false,
};

/**
 * Editable investment-policy chips (design §10). Each edit calls `onPatch`,
 * which PATCHes the policy and drops a system note in the chat — so the panel
 * and the conversation stay in sync, and the next optimization picks it up.
 */
export function PolicyPanel({
  policy,
  onPatch,
}: {
  policy: InvestmentPolicy | null;
  onPatch: (updates: Record<string, unknown>) => void;
}) {
  const p = policy ?? DEFAULTS;
  const inferred = new Set(policy ? policy.inferred_fields ?? [] : DEFAULTS.inferred_fields);
  const excluded = [...(p.excluded_sectors ?? []), ...(p.excluded_tickers ?? [])];

  return (
    <div className="rounded-lg border border-stone-200 dark:border-[var(--hairline)] p-2.5">
      <div className="flex items-center gap-1.5 mb-2">
        <SlidersHorizontal className="h-3.5 w-3.5 text-stone-400 dark:text-[var(--ink-3)]" aria-hidden />
        <span className="eyebrow text-stone-500 dark:text-[var(--ink-3)]">Investment policy</span>
        {!policy && (
          <span className="ml-auto text-[10.5px] text-amber-600 dark:text-amber-400">assumed defaults</span>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Field label="Objective" value={p.objective ?? "balanced"} options={OBJECTIVE} inferred={inferred.has("objective")} onChange={(v) => onPatch({ objective: v })} />
        <Field label="Risk" value={p.risk_tolerance ?? "medium"} options={RISK} inferred={inferred.has("risk_tolerance")} onChange={(v) => onPatch({ risk_tolerance: v })} />
        <Field label="Horizon" value={p.horizon ?? "1_3y"} options={HORIZON} inferred={inferred.has("horizon")} onChange={(v) => onPatch({ horizon: v })} />
        <button
          type="button"
          onClick={() => onPatch({ income_preference: !p.income_preference })}
          className={
            "px-2.5 py-1 rounded-full text-[11.5px] border " +
            (p.income_preference
              ? "border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-900/20 dark:text-emerald-300"
              : "border-stone-200 text-stone-500 dark:border-[var(--hairline)] dark:text-[var(--ink-3)]")
          }
        >
          income tilt {p.income_preference ? "on" : "off"}
        </button>
      </div>
      {excluded.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1 text-[11px] text-stone-500 dark:text-[var(--ink-3)]">
          <span>Excluded:</span>
          {excluded.map((x) => (
            <span key={x} className="px-1.5 py-0.5 rounded bg-stone-100 dark:bg-white/[0.06]">{x.replace(/_/g, " ")}</span>
          ))}
        </div>
      )}
    </div>
  );
}

function Field({
  label, value, options, inferred, onChange,
}: {
  label: string;
  value: string;
  options: readonly (readonly [string, string])[];
  inferred?: boolean;
  onChange: (v: string) => void;
}) {
  return (
    <label
      className={
        "group inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11.5px] border cursor-pointer " +
        (inferred
          ? "border-dashed border-stone-300 dark:border-[var(--hairline)]"
          : "border-stone-200 dark:border-[var(--hairline)]")
      }
      title={inferred ? "Assumed — change to confirm" : "From your stated preferences"}
    >
      <span className="text-stone-400 dark:text-[var(--ink-3)]">{label}:</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-transparent text-ink font-medium outline-none cursor-pointer -mr-1"
      >
        {options.map(([val, lbl]) => (
          <option key={val} value={val} className="text-stone-900">{lbl}</option>
        ))}
      </select>
    </label>
  );
}
