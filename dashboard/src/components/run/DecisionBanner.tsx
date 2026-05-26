import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import type { Decision } from "@/types/api";
import { DECISION_COLORS } from "@/lib/constants";

interface Props {
  decision?: { action: Decision; confidence: number; rationale: string };
  ticker: string;
  runDetails?: { bull?: any; bear?: any; risk?: any };
}

export function DecisionBanner({ decision, ticker, runDetails }: Props) {
  if (!decision) {
    return (
      <div className="sticky top-20 rounded-lg border border-border bg-card p-5">
        <div className="text-xs uppercase tracking-wider text-muted-foreground">Decision</div>
        <div className="mt-2 flex items-center gap-2 text-sm text-muted-foreground">
          Awaiting decision…
        </div>
        <div className="mt-4 space-y-2">
          <Skeleton className="h-10 w-32" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4" />
        </div>
      </div>
    );
  }

  const c = DECISION_COLORS[decision.action];
  const confPct = Math.round(decision.confidence * 100);

  return (
    <div
      className={cn(
        "banner-reveal sticky top-20 rounded-lg border bg-card p-5",
        decision.action === "BUY" && "border-primary/40",
        decision.action === "SELL" && "border-destructive/40",
        decision.action === "HOLD" && "border-warning/40",
      )}
    >
      <div className="flex items-center justify-between">
        <div className="text-xs uppercase tracking-wider text-muted-foreground">
          Decision · {ticker}
        </div>
        <div className="font-mono text-xs text-muted-foreground">{confPct}% confidence</div>
      </div>

      <div className="mt-3 flex items-baseline gap-3">
        <div
          className={cn(
            "rounded-md px-3 py-1.5 text-2xl font-bold tracking-wide",
            c.bg,
            c.text,
          )}
        >
          {decision.action}
        </div>
        <div className="font-mono text-3xl font-bold tabular">{confPct}</div>
        <div className="text-xs text-muted-foreground">/100</div>
      </div>

      <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
        {decision.rationale && decision.rationale.toUpperCase() !== decision.action
          ? decision.rationale
          : "Click below to see the full reasoning."}
      </p>

      <div className="mt-4 flex gap-2">
        <FullReasoningDialog decision={decision} ticker={ticker} runDetails={runDetails} />
        <Button variant="ghost" size="sm" className="text-xs text-muted-foreground">
          ✓ Saved to history
        </Button>
      </div>
    </div>
  );
}

function FullReasoningDialog({
  decision,
  ticker,
  runDetails,
}: {
  decision: { action: Decision; confidence: number; rationale: string };
  ticker: string;
  runDetails?: { bull?: any; bear?: any; risk?: any };
}) {
  const [open, setOpen] = useState(false);
  const c = DECISION_COLORS[decision.action];
  const conf = Math.round(decision.confidence * 100);
  const bull = runDetails?.bull;
  const bear = runDetails?.bear;
  const risk = runDetails?.risk;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="secondary" size="sm">
          View full reasoning
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-3 flex-wrap">
            <span>Why did the system say</span>
            <Badge className={cn(c.bg, c.text, "px-2 py-0.5 font-mono text-base")}>
              {decision.action}
            </Badge>
            <span className="text-base font-normal text-muted-foreground">
              for {ticker}?
            </span>
          </DialogTitle>
          <DialogDescription>
            {conf}% confidence · decision made by the Risk Manager after a 3-perspective debate.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5 text-sm">
          {/* Headline rationale */}
          {decision.rationale && decision.rationale.toUpperCase() !== decision.action && (
            <Section title="In one line">
              <p className="leading-relaxed">{decision.rationale}</p>
            </Section>
          )}

          {/* Bull case */}
          {bull && (
            <Section title="🟢 The bull case">
              <BullSummary bull={bull} />
            </Section>
          )}

          {/* Bear case */}
          {bear && (
            <Section title="🔴 The bear case">
              <BearSummary bear={bear} />
            </Section>
          )}

          {/* Constitutional check from Risk Manager */}
          {risk?.llm_constitution_check && risk.llm_constitution_check !== "(not parseable)" && (
            <Section title="📜 Constitutional check">
              <p className="whitespace-pre-line leading-relaxed">{risk.llm_constitution_check}</p>
            </Section>
          )}

          {/* Qualitative risks */}
          {risk?.llm_qualitative_risks && risk.llm_qualitative_risks !== "(not parseable)" && (
            <Section title="⚠️ Qualitative risks not in the data">
              <p className="whitespace-pre-line leading-relaxed">{risk.llm_qualitative_risks}</p>
            </Section>
          )}

          {/* Debate quality */}
          {risk?.llm_debate_quality && risk.llm_debate_quality !== "(not parseable)" && (
            <Section title="🎭 Risk debate quality">
              <p className="whitespace-pre-line leading-relaxed">{risk.llm_debate_quality}</p>
            </Section>
          )}

          {/* Clauses */}
          {risk?.clauses_referenced?.length > 0 && (
            <Section title="📖 Constitution clauses cited">
              <div className="flex flex-wrap gap-1.5">
                {risk.clauses_referenced.map((n: number) => (
                  <Badge key={n} variant="outline" className="font-mono">
                    Clause {n}
                  </Badge>
                ))}
              </div>
            </Section>
          )}

          {/* Warnings from the Risk Scorer */}
          {risk?.warnings?.length > 0 && (
            <Section title="⚠️ Deterministic risk warnings">
              <ul className="space-y-2">
                {risk.warnings.map((w: any, i: number) => (
                  <li key={i} className="rounded border border-border bg-secondary/30 p-2.5">
                    <div className="flex items-center justify-between">
                      <span className="font-medium">{w.rule?.replace(/_/g, " ")}</span>
                      <Badge variant="outline" className="text-[10px] text-warning">
                        {w.severity}
                      </Badge>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{w.explanation}</p>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {/* Fallback when the LLM hasn't populated the audit yet */}
          {!bull && !bear && !risk?.llm_qualitative_risks && !risk?.warnings?.length && (
            <Section title="Reasoning">
              <p className="italic text-muted-foreground">
                The full reasoning audit trail isn't available for this run.
                Try a fresh analysis to see it populated.
              </p>
            </Section>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </div>
      {children}
    </div>
  );
}

function BullSummary({ bull }: { bull: any }) {
  const conviction = bull?.conviction_level;
  const horizon = bull?.time_horizon?.primary;
  const catalysts = bull?.key_catalysts || [];
  const upside = bull?.upside_scenario || {};

  return (
    <div className="space-y-2">
      {(conviction || horizon || upside.base_case_upside_pct !== undefined) && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {conviction && (
            <Badge variant="outline" className="text-primary">
              {conviction} conviction
            </Badge>
          )}
          {horizon && (
            <Badge variant="outline">{String(horizon).replace(/_/g, " ")}</Badge>
          )}
          {upside.base_case_upside_pct !== undefined && (
            <span>
              Base case: <span className="text-primary">+{upside.base_case_upside_pct}%</span>
            </span>
          )}
          {bull?.risk_reward_ratio && (
            <span>R/R: <span className="font-mono">{bull.risk_reward_ratio}</span></span>
          )}
        </div>
      )}
      {catalysts.length > 0 ? (
        <ul className="list-inside list-disc space-y-1 text-sm leading-relaxed">
          {catalysts.slice(0, 5).map((c: string, i: number) => <li key={i}>{c}</li>)}
        </ul>
      ) : (
        <p className="text-sm italic text-muted-foreground">No bullish catalysts identified.</p>
      )}
    </div>
  );
}

function BearSummary({ bear }: { bear: any }) {
  const conviction = bear?.conviction_level;
  const horizon = bear?.time_horizon?.primary;
  const risks = bear?.key_risks || [];
  const recommendation = bear?.recommendation;

  return (
    <div className="space-y-2">
      {(conviction || horizon || recommendation) && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {conviction && (
            <Badge variant="outline" className="text-destructive">
              {conviction} conviction
            </Badge>
          )}
          {horizon && (
            <Badge variant="outline">{String(horizon).replace(/_/g, " ")}</Badge>
          )}
          {recommendation && (
            <Badge variant="outline" className="uppercase text-destructive">
              {recommendation}
            </Badge>
          )}
        </div>
      )}
      {risks.length > 0 ? (
        <ul className="list-inside list-disc space-y-1 text-sm leading-relaxed">
          {risks.slice(0, 5).map((r: string, i: number) => <li key={i}>{r}</li>)}
        </ul>
      ) : (
        <p className="text-sm italic text-muted-foreground">No bearish risks identified.</p>
      )}
    </div>
  );
}
