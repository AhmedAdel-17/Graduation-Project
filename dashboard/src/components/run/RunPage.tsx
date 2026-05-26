import { useEffect, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { toast } from "sonner";
import { Loader2, PlayCircle, RotateCcw, ArrowRight } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { Route as RunRoute } from "@/routes/_authenticated/run";
import { api } from "@/lib/api";
import { AGENT_PIPELINE } from "@/lib/constants";
import type { AgentOutput, Decision, WSMessage } from "@/types/api";
import { TickerCombobox } from "./TickerCombobox";
import { AgentTimeline } from "./AgentTimeline";
import { AgentDetailDrawer } from "./AgentDetailDrawer";
import { MacroPanel } from "./MacroPanel";
import { HeroCard } from "./editorial/HeroCard";
import { SectionHeader } from "./editorial/SectionHeader";
import { AdversarialResearch } from "./editorial/AdversarialResearch";
import { DebateResolution } from "./editorial/DebateResolution";
import { ExecutionPlanCard } from "./editorial/ExecutionPlanCard";
import { Link } from "@tanstack/react-router";

interface RunState {
  jobId: string;
  agents: AgentOutput[];
  decision?: { action: Decision; confidence: number; rationale: string };
  runId?: string;
  status: "running" | "done" | "failed";
  reconnecting?: boolean;
}

export function RunPage() {
  const search = RunRoute.useSearch();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [ticker, setTicker] = useState<string>(search.ticker || "COMI.CA");
  const [tradeDate, setTradeDate] = useState<string>(format(new Date(), "yyyy-MM-dd"));
  const [portfolio, setPortfolio] = useState<number>(1_000_000);
  const [riskAppetite, setRiskAppetite] = useState<"conservative" | "balanced" | "aggressive">("balanced");

  const [run, setRun] = useState<RunState | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<AgentOutput | null>(null);

  const macroQ = useQuery({ queryKey: ["macro"], queryFn: api.market.macroCurrent });

  // Fetch the live ticker quote so the HeroCard has a current price even
  // before the Market Analyst finishes (or if the run fails entirely).
  const quoteQ = useQuery({
    queryKey: ["quote", ticker],
    queryFn: () => api.market.quote(ticker),
    enabled: !!ticker && !!run,
    staleTime: 60_000,
  });

  // Keep selectedAgent in sync with the latest agent details (so when a new
  // message updates the agent the drawer re-renders with the fresh data).
  // Note: we deliberately do NOT auto-open the drawer — the user opens it
  // by clicking an agent card. Auto-opening would fight with the close button.
  useEffect(() => {
    if (!selectedAgent || !run) return;
    const fresh = run.agents.find((a) => a.agent === selectedAgent.agent);
    if (fresh && fresh !== selectedAgent) setSelectedAgent(fresh);
  }, [run?.agents, selectedAgent]);

  const startMutation = useMutation({
    mutationFn: () => api.runs.predict({ ticker, trade_date: tradeDate, portfolio_value: portfolio, risk_appetite: riskAppetite }),
    onSuccess: ({ job_id }) => {
      setRun({
        jobId: job_id,
        agents: AGENT_PIPELINE.map((a) => ({ agent: a, status: "pending" })),
        status: "running",
      });
    },
    onError: (e: any) => toast.error(e?.message || "Failed to start run"),
  });

  // WebSocket connection (mock)
  useEffect(() => {
    if (!run || run.status !== "running") return;
    const ws = api.jobSocket(run.jobId);
    const off = ws.on((msg: WSMessage) => {
      setRun((cur) => {
        if (!cur) return cur;
        const next = { ...cur, agents: [...cur.agents] };
        if (msg.type === "agent_start") {
          const i = next.agents.findIndex((a) => a.agent === msg.agent);
          if (i >= 0) next.agents[i] = { ...next.agents[i], status: "running", started_at: new Date().toISOString() };
        } else if (msg.type === "agent_done") {
          const i = next.agents.findIndex((a) => a.agent === msg.agent);
          if (i >= 0)
            next.agents[i] = {
              ...next.agents[i],
              status: "done",
              finished_at: new Date().toISOString(),
              summary: msg.summary,
              details: msg.details,
            };
        } else if (msg.type === "decision") {
          next.decision = { action: msg.action, confidence: msg.confidence, rationale: msg.rationale };
          toast.success(`${msg.action} for ${cur.agents.length ? ticker : ""} — ${(msg.confidence * 100).toFixed(0)}% confidence`);
        } else if (msg.type === "complete") {
          next.runId = msg.run_id;
          next.status = "done";
          queryClient.invalidateQueries({ queryKey: ["runs"] });
        } else if (msg.type === "error") {
          next.status = "failed";
          toast.error(msg.message);
        }
        return next;
      });
    });
    return () => {
      off();
      ws.close();
    };
  }, [run?.jobId, run?.status, queryClient, ticker]);

  const reset = () => {
    setRun(null);
    setSelectedAgent(null);
  };

  if (!run) {
    return (
      <div className="mx-auto max-w-3xl space-y-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Run analysis</h1>
          <p className="text-sm text-muted-foreground">
            Multi-agent research run on a single ticker. Takes ~3–4 minutes.
          </p>
        </div>
        <Card>
          <CardContent className="space-y-5 p-6">
            <div className="space-y-2">
              <Label>Ticker</Label>
              <TickerCombobox value={ticker} onChange={setTicker} />
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="date">Trade date</Label>
                <Input
                  id="date"
                  type="date"
                  value={tradeDate}
                  min="2022-01-01"
                  max={format(new Date(), "yyyy-MM-dd")}
                  onChange={(e) => setTradeDate(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="pv">Portfolio size (EGP)</Label>
                <Input
                  id="pv"
                  type="number"
                  value={portfolio}
                  min={10_000}
                  step={10_000}
                  onChange={(e) => setPortfolio(Number(e.target.value))}
                />
              </div>
            </div>

            <div className="space-y-2">
              <Label>Risk appetite</Label>
              <div className="grid grid-cols-3 gap-2">
                {(["conservative", "balanced", "aggressive"] as const).map((mode) => {
                  const active = riskAppetite === mode;
                  const label = mode[0].toUpperCase() + mode.slice(1);
                  const desc =
                    mode === "conservative" ? "Macro-strict · biased to HOLD"
                    : mode === "balanced"   ? "Macro + technicals weighed equally"
                    : "Technicals/momentum first · macro is a tiebreaker";
                  return (
                    <button
                      key={mode}
                      type="button"
                      onClick={() => setRiskAppetite(mode)}
                      className={`rounded-md border p-3 text-left transition-colors ${
                        active
                          ? "border-primary bg-primary/10"
                          : "border-border bg-card hover:border-primary/40 hover:bg-secondary/40"
                      }`}
                    >
                      <div className="text-sm font-medium">{label}</div>
                      <div className="mt-1 text-[11px] text-muted-foreground">{desc}</div>
                    </button>
                  );
                })}
              </div>
            </div>
            <Button
              size="lg"
              className="w-full"
              onClick={() => startMutation.mutate()}
              disabled={startMutation.isPending || !ticker}
            >
              {startMutation.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <PlayCircle className="mr-2 h-4 w-4" />
              )}
              Run analysis
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  // Extract structured data from the streaming agent outputs
  const bullData = run.agents.find((a) => a.agent === "Bull Researcher")?.details;
  const bearData = run.agents.find((a) => a.agent === "Bear Researcher")?.details;
  const researchManagerData = run.agents.find((a) => a.agent === "Research Manager")?.details;
  const executionPlan = run.agents.find((a) => a.agent === "Trader")?.details;
  const marketData = run.agents.find((a) => a.agent === "Market Analyst")?.details;

  // Pull rationale out of the research manager output
  const rmRationale =
    researchManagerData?.decision || researchManagerData?.judge_decision || run.decision?.rationale;

  // Trader fallback decision — shown until the Risk Manager issues the final one
  const ep: any = executionPlan?.execution_plan ?? executionPlan ?? null;
  const traderDecision: { action: any; confidence: number } | null = ep?.decision
    ? {
        action: ep.decision,
        confidence:
          ep.conviction === "HIGH" ? 0.85
          : ep.conviction === "LOW"  ? 0.40
          : 0.65,
      }
    : null;

  // Effective decision: Risk Manager wins; Trader is the fallback while waiting
  const effectiveDecision = run.decision ?? traderDecision ?? undefined;

  // Best-effort price extraction:
  //   1. Market Analyst's last close (most accurate, but only after node runs)
  //   2. Live quote from /api/ticker/:symbol/quote (fallback used while waiting)
  //   3. null
  const currentPrice =
    marketData?.last_close ??
    marketData?.current_price ??
    quoteQ.data?.last ??
    null;

  // Bull's base-case target
  const bullTargetPrice =
    bullData?.upside_scenario?.base_case_upside_pct != null && currentPrice
      ? currentPrice * (1 + bullData.upside_scenario.base_case_upside_pct / 100)
      : null;

  const bearStopPrice =
    bearData?.downside_range?.support_level_1 != null
      ? Number(bearData.downside_range.support_level_1)
      : null;

  // Decision-aware predicted price:
  //   BUY  → bull's base-case target
  //   SELL → bear's first support / stop level
  //   HOLD → current price (no expected change)
  //   null → null (still waiting on initial agents)
  const action = effectiveDecision?.action;
  const predictedPrice =
    action === "BUY"  && bullTargetPrice ? bullTargetPrice
    : action === "SELL" && bearStopPrice ? bearStopPrice
    : action === "HOLD" && currentPrice  ? currentPrice
    : null;

  return (
    <div className="space-y-8">
      {/* Run header — quick actions */}
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">
          <span className="font-mono text-foreground/60">{tradeDate}</span>
        </div>
        <div className="flex items-center gap-2">
          {run.status === "done" && run.runId && (
            <Link to="/history/$id" params={{ id: run.runId }}>
              <Button variant="secondary" size="sm">
                Open in history <ArrowRight className="ml-1 h-3 w-3" />
              </Button>
            </Link>
          )}
          <Button variant="outline" size="sm" onClick={reset}>
            <RotateCcw className="mr-1 h-3 w-3" /> Run again
          </Button>
        </div>
      </div>

      {/* HERO CARD */}
      <HeroCard
        ticker={ticker}
        decision={effectiveDecision}
        currentPrice={currentPrice}
        targetPrice={predictedPrice}
        createdAt={new Date().toISOString()}
        status={run.status}
      />

      {/* 01 — ADVERSARIAL RESEARCH */}
      <div className="space-y-4">
        <SectionHeader number="01" title="Adversarial research" />
        <AdversarialResearch bull={bullData} bear={bearData} currentPrice={currentPrice} />
      </div>

      {/* 02 — DEBATE RESOLUTION */}
      <div className="space-y-4">
        <SectionHeader number="02" title="Debate resolution" />
        <DebateResolution
          decision={effectiveDecision?.action}
          confidence={effectiveDecision?.confidence}
          rationale={typeof rmRationale === "string" ? rmRationale : run.decision?.rationale}
        />
      </div>

      {/* 03 — EXECUTION PLAN */}
      <div className="space-y-4">
        <SectionHeader number="03" title="Execution plan" />
        <ExecutionPlanCard
          executionPlan={executionPlan}
          currentPrice={currentPrice}
          finalDecision={effectiveDecision?.action}
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
                  agents={run.agents}
                  selectedAgent={selectedAgent?.agent ?? null}
                  onSelect={setSelectedAgent}
                />
              </CardContent>
            </Card>
          </div>
          <div className="lg:col-span-2">
            {macroQ.data && <MacroPanel macro={macroQ.data} />}
          </div>
        </div>
      </div>

      {/* Slide-out drawer for the clicked agent */}
      <AgentDetailDrawer
        agent={selectedAgent}
        onClose={() => setSelectedAgent(null)}
      />
    </div>
  );
}
