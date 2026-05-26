import { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useNavigate, Link } from "@tanstack/react-router";
import { toast } from "sonner";
import { Download, Trash2, ArrowLeft } from "lucide-react";

import { Route as DetailRoute } from "@/routes/_authenticated/history.$id";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

import { api } from "@/lib/api";
import { AGENT_PIPELINE } from "@/lib/constants";
import { formatDateTime } from "@/lib/formatters";
import type { AgentOutput } from "@/types/api";

import { AgentTimeline } from "@/components/run/AgentTimeline";
import { AgentDetailDrawer } from "@/components/run/AgentDetailDrawer";
import { MacroPanel } from "@/components/run/MacroPanel";
import { HeroCard } from "@/components/run/editorial/HeroCard";
import { SectionHeader } from "@/components/run/editorial/SectionHeader";
import { AdversarialResearch } from "@/components/run/editorial/AdversarialResearch";
import { DebateResolution } from "@/components/run/editorial/DebateResolution";
import { ExecutionPlanCard } from "@/components/run/editorial/ExecutionPlanCard";

export function HistoryDetailPage() {
  const { id } = DetailRoute.useParams();
  const navigate = useNavigate();
  const [selectedAgent, setSelectedAgent] = useState<AgentOutput | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["run", id],
    queryFn: () => api.runs.get(id),
  });

  // Live quote so the Hero "Current price" panel isn't blank for saved runs
  const quoteQ = useQuery({
    queryKey: ["quote", data?.ticker],
    queryFn: () => api.market.quote(data!.ticker),
    enabled: !!data?.ticker,
    staleTime: 60_000,
  });

  const del = useMutation({
    mutationFn: () => api.runs.delete(id),
    onSuccess: () => {
      toast.success("Run deleted");
      navigate({ to: "/history" });
    },
  });

  // Reconstruct the agents array from the saved payload (so the timeline /
  // summary panel work even when the streaming history wasn't persisted).
  const agents: AgentOutput[] = useMemo(() => {
    if (!data) return [];
    if (data.agents && data.agents.length > 0) return data.agents;

    const out: AgentOutput[] = [];
    if (data.bull_thesis) {
      out.push({
        agent: "Bull Researcher",
        status: "done",
        summary: `${(data.bull_thesis as any).conviction_level ?? "—"} conviction bull thesis`,
        details: data.bull_thesis,
      });
    }
    if (data.bear_thesis) {
      out.push({
        agent: "Bear Researcher",
        status: "done",
        summary: `${(data.bear_thesis as any).conviction_level ?? "—"} conviction bear thesis`,
        details: data.bear_thesis,
      });
    }
    if (data.execution_plan) {
      const ep = ((data.execution_plan as any)?.execution_plan ?? data.execution_plan) as any;
      out.push({
        agent: "Trader",
        status: "done",
        summary: `${ep?.decision ?? "—"} (${ep?.conviction ?? "—"} conviction)`,
        details: data.execution_plan,
      });
    }
    if (data.risk_assessment && Object.keys(data.risk_assessment as any).length > 0) {
      const ra = data.risk_assessment as any;
      out.push({
        agent: "Risk Manager",
        status: "done",
        summary: ra.llm_action ?? data.decision ?? "—",
        details: data.risk_assessment,
      });
    }

    const seen = new Set(out.map((a) => a.agent));
    for (const name of AGENT_PIPELINE) {
      if (!seen.has(name)) {
        out.push({ agent: name, status: "done", summary: "(details not saved)" });
      }
    }
    out.sort((a, b) => AGENT_PIPELINE.indexOf(a.agent) - AGENT_PIPELINE.indexOf(b.agent));
    return out;
  }, [data]);

  // Note: we deliberately do NOT auto-open the drawer on page load.
  // Auto-opening fights with the close button (closing → effect re-runs →
  // immediately re-opens). User opens the drawer by clicking an agent card.

  if (isLoading) return <Skeleton className="h-96 w-full" />;
  if (!data) return <div className="text-sm text-muted-foreground">Run not found.</div>;

  // Derive prices for the hero card
  const bullData = data.bull_thesis as any;
  const bearData = data.bear_thesis as any;
  const ra = data.risk_assessment as any;

  // Try to find a current price from the execution plan or fall back to a live quote
  const ep = (data.execution_plan as any)?.execution_plan ?? (data.execution_plan as any) ?? {};
  const currentPrice =
    ep?.entry_logic?.entry_zone?.limit_price ??
    ep?.entry_logic?.entry_zone?.price_range_low ??
    quoteQ.data?.last ??
    null;

  const predictedPrice =
    data.decision === "BUY" && currentPrice && bullData?.upside_scenario?.base_case_upside_pct != null
      ? currentPrice * (1 + bullData.upside_scenario.base_case_upside_pct / 100)
    : data.decision === "SELL" && bearData?.downside_range?.support_level_1 != null
      ? Number(bearData.downside_range.support_level_1)
    : data.decision === "HOLD" && currentPrice
      ? currentPrice
    : null;

  // Pull rationale: prefer Risk Manager output, fall back to top-level rationale
  const rmRationale = ra?.llm_qualitative_risks || ra?.llm_constitution_check || data.rationale;

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <Link to="/history" className="text-xs text-muted-foreground hover:text-foreground">
            <ArrowLeft className="mr-1 inline h-3 w-3" /> Back to history
          </Link>
          <div className="mt-1 text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">
            <span className="font-mono text-foreground/60">{data.trade_date} · {formatDateTime(data.created_at)}</span>
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => toast.info("PDF export coming soon")}>
            <Download className="mr-1 h-3 w-3" /> Export
          </Button>
          <Button variant="outline" size="sm" onClick={() => del.mutate()}>
            <Trash2 className="mr-1 h-3 w-3" /> Delete
          </Button>
        </div>
      </div>

      {/* HERO CARD */}
      <HeroCard
        ticker={data.ticker}
        decision={
          data.decision
            ? { action: data.decision, confidence: data.confidence ?? 0 }
            : undefined
        }
        currentPrice={currentPrice ?? undefined}
        targetPrice={predictedPrice ?? undefined}
        createdAt={data.created_at}
        status="done"
      />

      {/* 01 — ADVERSARIAL RESEARCH */}
      <div className="space-y-4">
        <SectionHeader number="01" title="Adversarial research" />
        <AdversarialResearch bull={bullData} bear={bearData} currentPrice={currentPrice ?? undefined} />
      </div>

      {/* 02 — DEBATE RESOLUTION */}
      <div className="space-y-4">
        <SectionHeader number="02" title="Debate resolution" />
        <DebateResolution
          decision={data.decision}
          confidence={data.confidence ?? undefined}
          rationale={rmRationale ?? data.rationale ?? undefined}
        />
      </div>

      {/* 03 — EXECUTION PLAN */}
      <div className="space-y-4">
        <SectionHeader number="03" title="Execution plan" />
        <ExecutionPlanCard
          executionPlan={data.execution_plan}
          currentPrice={currentPrice ?? undefined}
          finalDecision={data.decision}
        />
      </div>

      {/* 04 — PIPELINE + AGENT DETAILS (drawer opens on click) */}
      <div className="space-y-4">
        <SectionHeader number="04" title="Agent pipeline" />
        <div className="grid gap-4 lg:grid-cols-5">
          <div className="lg:col-span-3">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Pipeline</CardTitle>
              </CardHeader>
              <CardContent>
                <AgentTimeline
                  agents={agents}
                  selectedAgent={selectedAgent?.agent ?? null}
                  onSelect={setSelectedAgent}
                />
              </CardContent>
            </Card>
          </div>
          <div className="lg:col-span-2">
            <MacroPanel macro={data.macro_context as any} />
          </div>
        </div>
      </div>

      <AgentDetailDrawer
        agent={selectedAgent}
        onClose={() => setSelectedAgent(null)}
      />
    </div>
  );
}
