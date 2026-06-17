import { useState } from "react";
import { Check, FlaskConical, Trash2, X } from "lucide-react";
import type { RebalanceAction, ScenarioRef } from "../../services/api/portfolioTypes";
import type { Loc } from "./format";
import { fmtEgp, fmtInt } from "./format";
import { cn } from "../../lib/utils";

/**
 * Scenario branch switcher (design §10): tabs across Baseline + live scenarios;
 * the active branch drives the whole canvas. Adopt opens a confirm dialog that
 * **restates the trades** before promoting — confusing a hypothetical with an
 * adopted plan is the worst failure this feature can have, so adoption is
 * explicit and legible.
 */
export function ScenarioTabs({
  scenarios,
  activeRef,
  onSwitch,
  onAdopt,
  onDiscard,
  adoptActions = [],
  loc = "en",
}: {
  scenarios: ScenarioRef[];
  activeRef: string;
  onSwitch: (ref: string) => void;
  onAdopt: (scenarioId: number) => void;
  onDiscard: (scenarioId: number) => void;
  adoptActions?: RebalanceAction[];
  loc?: Loc;
}) {
  const [confirming, setConfirming] = useState(false);
  const live = scenarios.filter((s) => (s.status ?? "active") === "active");
  const activeScenarioId = activeRef !== "baseline" ? Number(activeRef) : null;
  const activeScenario = live.find((s) => s.scenario_id === activeScenarioId) ?? null;

  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      <Tab active={activeRef === "baseline"} onClick={() => onSwitch("baseline")} label="Baseline" />
      {live.map((s) => (
        <Tab
          key={s.scenario_id}
          active={String(s.scenario_id) === activeRef}
          onClick={() => onSwitch(String(s.scenario_id))}
          label={s.label || `Scenario ${s.scenario_id}`}
          hypothetical
        />
      ))}

      {activeScenario && activeScenarioId != null && (
        <div className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setConfirming(true)}
            className="inline-flex items-center gap-1 px-2.5 h-7 rounded-md text-[11.5px] font-medium bg-stone-900 text-white hover:bg-stone-800 dark:bg-white dark:text-stone-900 dark:hover:bg-stone-200"
          >
            <Check className="h-3.5 w-3.5" /> Adopt
          </button>
          <button
            type="button"
            onClick={() => onDiscard(activeScenarioId)}
            aria-label="Discard scenario"
            className="inline-flex items-center justify-center h-7 w-7 rounded-md text-stone-400 hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-900/20"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {confirming && activeScenario && activeScenarioId != null && (
        <AdoptDialog
          label={activeScenario.label || `Scenario ${activeScenarioId}`}
          actions={adoptActions}
          loc={loc}
          onCancel={() => setConfirming(false)}
          onConfirm={() => { setConfirming(false); onAdopt(activeScenarioId); }}
        />
      )}
    </div>
  );
}

function Tab({ active, onClick, label, hypothetical }: {
  active: boolean; onClick: () => void; label: string; hypothetical?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1 px-2.5 h-7 rounded-md text-[12px] font-medium max-w-[160px]",
        active
          ? hypothetical
            ? "bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-300"
            : "bg-stone-100 text-stone-700 dark:bg-white/[0.06] dark:text-white"
          : "text-stone-400 hover:text-stone-600 dark:text-[var(--ink-3)] dark:hover:text-[var(--ink-2)]"
      )}
    >
      {hypothetical && <FlaskConical className="h-3 w-3 shrink-0" aria-hidden />}
      <span className="truncate">{label}</span>
    </button>
  );
}

function AdoptDialog({ label, actions, loc, onConfirm, onCancel }: {
  label: string; actions: RebalanceAction[]; loc: Loc; onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-stone-900/40 backdrop-blur-[2px]" onClick={onCancel} aria-hidden />
      <div className="relative w-full max-w-md rounded-xl border border-stone-200 bg-white shadow-xl dark:border-[var(--hairline)] dark:bg-[var(--paper)]">
        <div className="flex items-center gap-2 px-4 h-12 border-b border-stone-200/70 dark:border-[var(--hairline)]">
          <span className="text-[13px] font-semibold text-ink">Adopt “{label}” as your baseline?</span>
          <button type="button" onClick={onCancel} aria-label="Cancel" className="ml-auto h-7 w-7 inline-flex items-center justify-center rounded-md text-stone-400 hover:bg-stone-100 dark:hover:bg-white/5">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="p-4">
          <p className="text-[12.5px] text-stone-600 dark:text-[var(--ink-2)] mb-3">
            This replaces your baseline with the scenario’s portfolio. It restates these trades — still a
            proposal for your review, not an order:
          </p>
          {actions.length === 0 ? (
            <p className="text-[12.5px] text-stone-500 dark:text-[var(--ink-3)]">No trades to restate.</p>
          ) : (
            <ul className="space-y-1 max-h-48 overflow-y-auto">
              {actions.map((a, i) => (
                <li key={`${a.ticker}-${i}`} className="flex items-center justify-between text-[12.5px]">
                  <span>
                    <b className={a.side === "BUY" ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}>{a.side}</b>{" "}
                    <span className="num">{fmtInt(a.shares, loc)}</span> <span dir="auto">{a.ticker}</span>
                  </span>
                  <span className="num text-stone-400 dark:text-[var(--ink-3)]">≈ {fmtEgp(a.est_value_egp, loc)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-stone-200/70 dark:border-[var(--hairline)]">
          <button type="button" onClick={onCancel} className="px-3 h-8 rounded-lg border border-stone-200 text-[12.5px] text-ink-2 hover:bg-stone-50 dark:border-[var(--hairline)] dark:hover:bg-white/5">Cancel</button>
          <button type="button" onClick={onConfirm} className="inline-flex items-center gap-1.5 px-3 h-8 rounded-lg bg-stone-900 text-white text-[12.5px] font-medium hover:bg-stone-800 dark:bg-white dark:text-stone-900 dark:hover:bg-stone-200">
            <Check className="h-3.5 w-3.5" /> Adopt as baseline
          </button>
        </div>
      </div>
    </div>
  );
}
