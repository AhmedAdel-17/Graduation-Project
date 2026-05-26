import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import type { AgentOutput } from "@/types/api";

export function AgentDetailDrawer({
  agent,
  onClose,
}: {
  agent: AgentOutput | null;
  onClose: () => void;
}) {
  const open = !!agent;
  return (
    <Sheet open={open} onOpenChange={(v) => !v && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">{agent?.agent}</SheetTitle>
        </SheetHeader>
        {agent && (
          <div className="mt-4 space-y-4">
            {agent.summary && (
              <p className="text-sm leading-relaxed text-muted-foreground">
                {agent.summary}
              </p>
            )}
            <AgentRenderer agent={agent} />
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

/** Pick the right human-readable layout per agent type. */
function AgentRenderer({ agent }: { agent: AgentOutput }) {
  const name = agent.agent;
  const d = (agent.details ?? {}) as any;

  if (name === "Market Analyst") return <MarketView d={d} />;
  if (name === "Fundamentals Analyst") return <FundamentalsView d={d} />;
  if (name === "News Analyst" || name === "Social Media Analyst")
    return <SentimentView d={d} agent={name} />;
  if (name === "Bull Researcher" || name === "Bear Researcher")
    return <ThesisView d={d} side={name === "Bull Researcher" ? "bull" : "bear"} />;
  if (name === "Research Manager") return <ResearchManagerView d={d} />;
  if (name === "Trader") return <TraderView d={d} />;
  if (name === "Risk Scorer") return <RiskScorerView d={d} />;
  if (name === "Risk Debators") return <RiskDebatorsView d={d} />;
  if (name === "Risk Manager") return <RiskManagerView d={d} />;
  return <GenericView d={d} />;
}

// ──────────────── Market ────────────────
function MarketView({ d }: { d: any }) {
  const trend = d?.trend_direction || {};
  const sig = d?.indicator_signals || {};
  const dq = d?.data_quality || {};
  return (
    <div className="space-y-3">
      <KV label="Trend" value={`${trend.direction ?? "—"} (${trend.strength ?? "—"})`} />
      <KV label="Bullish signals" value={String(trend.bullish_signals ?? 0)} />
      <KV label="Bearish signals" value={String(trend.bearish_signals ?? 0)} />
      <KV label="Confidence" value={`${Math.round((d?.confidence_score ?? 0) * 100)}%`} />
      <Divider />
      <SectionTitle>Indicators</SectionTitle>
      <IndicatorRow name="RSI(14)" value={sig?.rsi?.value} signal={sig?.rsi?.signal} desc={sig?.rsi?.description} />
      <IndicatorRow name="MACD" value={sig?.macd?.value} signal={sig?.macd?.signal} desc={sig?.macd?.description} />
      <IndicatorRow name="Bollinger" value={sig?.bollinger?.position} signal={sig?.bollinger?.signal} desc={sig?.bollinger?.description} />
      <IndicatorRow name="SMA trend" value={sig?.trend_sma?.value} signal={sig?.trend_sma?.signal} desc={sig?.trend_sma?.description} />
      <Divider />
      <KV label="Bars analysed" value={String(dq?.total_bars ?? "—")} />
      <KV label="Sufficient history" value={dq?.sufficient_history ? "yes" : "no"} />
    </div>
  );
}

function IndicatorRow({ name, value, signal, desc }: any) {
  if (value === null || value === undefined) {
    return (
      <div className="flex items-center justify-between text-sm">
        <span className="text-muted-foreground">{name}</span>
        <span className="text-xs italic text-muted-foreground">no data</span>
      </div>
    );
  }
  const sigColor =
    signal === "bullish" ? "text-primary"
    : signal === "bearish" ? "text-destructive"
    : "text-muted-foreground";
  return (
    <div className="space-y-0.5">
      <div className="flex items-center justify-between text-sm">
        <span className="text-muted-foreground">{name}</span>
        <span className="flex items-center gap-2">
          <span className="font-mono tabular">
            {typeof value === "number" ? value.toFixed(2) : String(value)}
          </span>
          <Badge variant="outline" className={`text-[10px] ${sigColor}`}>{signal}</Badge>
        </span>
      </div>
      {desc && <div className="text-xs text-muted-foreground">{desc}</div>}
    </div>
  );
}

// ──────────────── Fundamentals ────────────────
function FundamentalsView({ d }: { d: any }) {
  const ratios = d?.ratios || {};
  const flags = d?.distress_flags || [];
  return (
    <div className="space-y-3">
      <KV label="Financial health" value={d?.financial_health ?? "—"} />
      <KV label="Sector" value={d?.sector ?? "—"} />
      <KV label="Data confidence" value={`${d?.data_confidence ?? 0}/100`} />
      <KV label="Signal coherence" value={`${d?.signal_coherence ?? 0}/100`} />
      {flags.length > 0 && (
        <>
          <Divider />
          <SectionTitle>Distress flags</SectionTitle>
          <div className="flex flex-wrap gap-1">
            {flags.map((f: string) => (
              <Badge key={f} variant="outline" className="text-[10px] text-destructive">
                {f.replace(/_/g, " ")}
              </Badge>
            ))}
          </div>
        </>
      )}
      <Divider />
      <SectionTitle>Key ratios</SectionTitle>
      <RatioRow label="ROE" value={ratios.roe} pct />
      <RatioRow label="Net Margin" value={ratios.net_margin} pct />
      <RatioRow label="Operating Margin" value={ratios.operating_margin} pct />
      <RatioRow label="Debt/Equity" value={ratios.debt_to_equity} />
      <RatioRow label="Current Ratio" value={ratios.current_ratio} />
      <RatioRow label="P/E" value={ratios.pe_ratio} />
      <RatioRow label="P/B" value={ratios.pb_ratio} />
      <RatioRow label="EPS" value={ratios.eps} />
      {d?.key_risks?.length > 0 && (
        <>
          <Divider />
          <SectionTitle>Key risks</SectionTitle>
          <ul className="list-inside list-disc space-y-1 text-sm text-muted-foreground">
            {d.key_risks.slice(0, 5).map((r: string, i: number) => <li key={i}>{r}</li>)}
          </ul>
        </>
      )}
    </div>
  );
}

function RatioRow({ label, value, pct }: { label: string; value: any; pct?: boolean }) {
  const v =
    typeof value === "number"
      ? pct ? `${(value * 100).toFixed(1)}%` : value.toFixed(2)
      : "—";
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono tabular">{v}</span>
    </div>
  );
}

// ──────────────── News / Social ────────────────
function SentimentView({ d, agent }: { d: any; agent: string }) {
  const sent = d?.sentiment || "neutral";
  const coverage = d?.news_coverage || {};
  const headlines = d?.key_headlines || [];
  const transformer = d?.transformer_sentiment;
  const combined = d?.combined_sentiment;
  const sigColor =
    sent === "bullish" ? "text-primary"
    : sent === "bearish" ? "text-destructive"
    : "text-muted-foreground";

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm text-muted-foreground">Sentiment</span>
        <Badge variant="outline" className={`text-xs ${sigColor}`}>
          {sent} {d?.sentiment_strength ? `(${d.sentiment_strength})` : ""}
        </Badge>
      </div>
      <KV label="Confidence" value={`${d?.confidence_score ?? 0}/100`} />

      {agent === "News Analyst" && (
        <>
          <KV label="Articles" value={String(coverage.total_articles ?? 0)} />
          <KV label="Sources" value={String(coverage.sources_count ?? 0)} />
          <KV label="Languages" value={(coverage.languages || []).join(", ") || "—"} />
        </>
      )}

      {d?.explanation && (
        <>
          <Divider />
          <SectionTitle>Analysis</SectionTitle>
          <p className="text-sm leading-relaxed">{d.explanation}</p>
        </>
      )}

      {transformer?.model_status === "ok" && (
        <>
          <Divider />
          <SectionTitle>AI sentiment models</SectionTitle>
          <KV label="Score" value={typeof transformer.score === "number" ? transformer.score.toFixed(3) : "—"} />
          <KV label="Label" value={transformer.label ?? "—"} />
          <KV label="Model confidence" value={typeof transformer.confidence === "number" ? `${(transformer.confidence * 100).toFixed(0)}%` : "—"} />
        </>
      )}

      {combined?.label && (
        <>
          <Divider />
          <SectionTitle>Combined (LLM + AI blend)</SectionTitle>
          <KV label="Final score" value={typeof combined.score === "number" ? combined.score.toFixed(3) : "—"} />
          <KV label="Final label" value={combined.label} />
        </>
      )}

      {d?.catalysts_from_news?.length > 0 && (
        <>
          <Divider />
          <SectionTitle>Catalysts</SectionTitle>
          <ul className="list-inside list-disc space-y-1 text-sm text-primary/80">
            {d.catalysts_from_news.slice(0, 4).map((c: string, i: number) => <li key={i}>{c}</li>)}
          </ul>
        </>
      )}

      {d?.risks_from_news?.length > 0 && (
        <>
          <SectionTitle>Risks identified</SectionTitle>
          <ul className="list-inside list-disc space-y-1 text-sm text-destructive/80">
            {d.risks_from_news.slice(0, 4).map((r: string, i: number) => <li key={i}>{r}</li>)}
          </ul>
        </>
      )}

      {headlines.length > 0 && (
        <>
          <Divider />
          <SectionTitle>Top headlines</SectionTitle>
          <ul className="space-y-2 text-sm">
            {headlines.slice(0, 5).map((h: any, i: number) => (
              <li key={i} className="rounded-md border border-border bg-secondary/30 p-2.5">
                <div className="flex items-start justify-between gap-2">
                  <span className="leading-snug" dir={h.language === "ar" ? "rtl" : "ltr"}>
                    {h.headline}
                  </span>
                  <Badge variant="outline" className="shrink-0 text-[9px]">
                    {h.impact ?? "—"}
                  </Badge>
                </div>
                <div className="mt-1 text-[10px] text-muted-foreground">
                  {h.source} · {h.language === "ar" ? "Arabic" : "English"}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

// ──────────────── Bull / Bear thesis ────────────────
function ThesisView({ d, side }: { d: any; side: "bull" | "bear" }) {
  const isBull = side === "bull";
  const horizon = d?.time_horizon || {};
  const summary = d?.signal_summary || {};
  const liquidity = d?.liquidity_assessment || {};
  const upside = d?.upside_scenario || {};
  const risks = d?.key_risks || [];
  const catalysts = d?.key_catalysts || [];
  const invalidation = d?.invalidation_conditions || [];
  const accent = isBull ? "text-primary" : "text-destructive";

  return (
    <div className="space-y-3">
      <KV label="Conviction" value={d?.conviction_level ?? "—"} valueClass={accent} />
      {d?.recommendation && <KV label="Recommendation" value={d.recommendation} valueClass={accent} />}
      <KV label="Time horizon" value={horizon.primary ?? "—"} />
      {horizon.entry_window && <KV label="Entry window" value={horizon.entry_window} />}
      {horizon.expected_duration && <KV label="Expected duration" value={horizon.expected_duration} />}

      {(summary.technical || summary.fundamental || summary.sentiment) && (
        <>
          <Divider />
          <SectionTitle>Signal alignment</SectionTitle>
          <KV label="Technical" value={summary.technical ?? "—"} />
          <KV label="Fundamental" value={summary.fundamental ?? "—"} />
          <KV label="Sentiment" value={summary.sentiment ?? "—"} />
          {summary.alignment_score && <KV label="Alignment score" value={summary.alignment_score} />}
        </>
      )}

      {catalysts.length > 0 && (
        <>
          <Divider />
          <SectionTitle>Key catalysts</SectionTitle>
          <ul className="list-inside list-disc space-y-1 text-sm text-primary/80">
            {catalysts.map((c: string, i: number) => <li key={i}>{c}</li>)}
          </ul>
        </>
      )}

      {risks.length > 0 && (
        <>
          <Divider />
          <SectionTitle>{isBull ? "Risks to thesis" : "Key risks"}</SectionTitle>
          <ul className="list-inside list-disc space-y-1 text-sm text-destructive/80">
            {risks.map((r: string, i: number) => <li key={i}>{r}</li>)}
          </ul>
        </>
      )}

      {isBull && upside.base_case_upside_pct !== undefined && (
        <>
          <Divider />
          <SectionTitle>Upside scenario</SectionTitle>
          <KV label="Base case" value={`+${upside.base_case_upside_pct}%`} valueClass="text-primary" />
          <KV label="Bull case" value={`+${upside.bull_case_upside_pct}%`} valueClass="text-primary" />
          <KV label="Downside risk" value={`${upside.downside_risk_pct}%`} valueClass="text-destructive" />
          {upside.basis && <p className="mt-1 text-xs text-muted-foreground">{upside.basis}</p>}
        </>
      )}

      {d?.risk_reward_ratio && <KV label="Risk/Reward" value={d.risk_reward_ratio} />}

      {(liquidity.recommended_position_size || liquidity.execution_strategy) && (
        <>
          <Divider />
          <SectionTitle>Execution</SectionTitle>
          {liquidity.recommended_position_size && (
            <KV label="Position size" value={liquidity.recommended_position_size} />
          )}
          {liquidity.execution_strategy && (
            <p className="text-sm text-muted-foreground">{liquidity.execution_strategy}</p>
          )}
        </>
      )}

      {invalidation.length > 0 && (
        <>
          <Divider />
          <SectionTitle>Invalidation conditions</SectionTitle>
          <ul className="list-inside list-disc space-y-1 text-sm text-muted-foreground">
            {invalidation.map((c: string, i: number) => <li key={i}>{c}</li>)}
          </ul>
        </>
      )}
    </div>
  );
}

// ──────────────── Research Manager ────────────────
function ResearchManagerView({ d }: { d: any }) {
  const text = String(d?.decision || d?.judge_decision || "");
  const sections = parseSections(text, [
    "Strongest Bull Case",
    "Strongest Bear Case",
    "Why One Side Wins",
    "Decision",
  ]);

  if (Object.keys(sections).length > 0) {
    return (
      <div className="space-y-3">
        {Object.entries(sections).map(([heading, body]) => (
          <div key={heading}>
            <SectionTitle>{heading}</SectionTitle>
            <p className="whitespace-pre-line text-sm leading-relaxed text-muted-foreground">
              {body}
            </p>
          </div>
        ))}
      </div>
    );
  }

  return (
    <p className="whitespace-pre-line text-sm leading-relaxed text-muted-foreground">
      {text || "No reasoning recorded."}
    </p>
  );
}

// ──────────────── Trader ────────────────
function TraderView({ d }: { d: any }) {
  const ep = d?.execution_plan || d;
  const sizing = ep?.position_sizing || {};
  const entry = ep?.entry_logic || {};
  const exit = ep?.exit_logic || {};
  const risk = ep?.risk_management || ep?.risk_controls || {};
  const decision = ep?.decision ?? d?.decision;
  const accent =
    decision === "BUY" ? "text-primary"
    : decision === "SELL" ? "text-destructive"
    : "text-warning";

  return (
    <div className="space-y-3">
      <KV label="Decision" value={decision ?? "—"} valueClass={`font-semibold ${accent}`} />
      <KV label="Conviction" value={ep?.conviction ?? d?.conviction ?? "—"} />

      <Divider />
      <SectionTitle>Position sizing</SectionTitle>
      <KV label="Target shares" value={fmtInt(sizing.target_shares)} />
      <KV label="Allocation" value={sizing.portfolio_allocation ?? "—"} />
      <KV label="Max shares/day" value={fmtInt(sizing.max_shares_per_day)} />
      <KV label="Execution days" value={String(sizing.execution_days ?? "—")} />

      {entry.order_type && (
        <>
          <Divider />
          <SectionTitle>Entry plan</SectionTitle>
          <KV label="Order type" value={entry.order_type} />
          {entry.entry_zone && (
            <KV
              label="Entry zone"
              value={`${entry.entry_zone.price_range_low ?? "—"} – ${entry.entry_zone.price_range_high ?? "—"} EGP`}
            />
          )}
          {entry.timing && <p className="text-sm text-muted-foreground">{entry.timing}</p>}
        </>
      )}

      {exit?.take_profit && (
        <>
          <Divider />
          <SectionTitle>Exit plan</SectionTitle>
          {exit.take_profit.target_1?.price && (
            <KV label="Target 1" value={`${exit.take_profit.target_1.price} EGP`} />
          )}
          {exit.take_profit.target_2?.price && (
            <KV label="Target 2" value={`${exit.take_profit.target_2.price} EGP`} />
          )}
          {exit.stop_loss?.price && (
            <KV label="Stop loss" value={`${exit.stop_loss.price} EGP`} valueClass="text-destructive" />
          )}
        </>
      )}

      {(risk.max_loss_per_trade || risk.liquidity_exit_plan) && (
        <>
          <Divider />
          <SectionTitle>Risk controls</SectionTitle>
          {risk.max_loss_per_trade && <KV label="Max loss" value={String(risk.max_loss_per_trade)} />}
          {risk.liquidity_exit_plan && (
            <p className="text-sm text-muted-foreground">{risk.liquidity_exit_plan}</p>
          )}
        </>
      )}
    </div>
  );
}

// ──────────────── Risk Scorer ────────────────
function RiskScorerView({ d }: { d: any }) {
  const action = d?.risk_action ?? "—";
  const warnings = d?.warnings || [];
  const violations = d?.violations || [];
  const accent =
    action === "VETO" ? "text-destructive"
    : action === "ALLOW" ? "text-primary"
    : "text-warning";

  return (
    <div className="space-y-3">
      <KV label="Risk action" value={action} valueClass={`font-semibold ${accent}`} />
      <KV label="Violations" value={String(violations.length)} />
      <KV label="Warnings" value={String(warnings.length)} />

      {warnings.length > 0 && (
        <>
          <Divider />
          <SectionTitle>Warnings</SectionTitle>
          <ul className="space-y-2 text-sm">
            {warnings.map((w: any, i: number) => (
              <li key={i} className="rounded-md border border-border bg-secondary/30 p-2.5">
                <div className="flex items-start justify-between gap-2">
                  <span className="font-medium">{w.rule?.replace(/_/g, " ")}</span>
                  <Badge variant="outline" className="text-[10px] text-warning">{w.severity}</Badge>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">{w.explanation}</p>
              </li>
            ))}
          </ul>
        </>
      )}

      {violations.length > 0 && (
        <>
          <Divider />
          <SectionTitle>Critical violations</SectionTitle>
          <ul className="space-y-2 text-sm">
            {violations.map((v: any, i: number) => (
              <li key={i} className="rounded-md border border-destructive/40 bg-destructive/5 p-2.5">
                <div className="font-medium text-destructive">{v.rule?.replace(/_/g, " ")}</div>
                <p className="mt-1 text-xs text-muted-foreground">{v.explanation}</p>
                {v.remediation && (
                  <p className="mt-1 text-xs italic text-muted-foreground">→ {v.remediation}</p>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

// ──────────────── Risk Debators ────────────────
function RiskDebatorsView({ d }: { d: any }) {
  const history = String(d?.history || d?.judge_decision || "");
  const sections = parseSections(history, [
    "RISKY ANALYST",
    "SAFE ANALYST",
    "NEUTRAL ANALYST",
    "SYNTHESIS",
  ]);

  if (Object.keys(sections).length === 0) {
    return (
      <p className="whitespace-pre-line text-sm leading-relaxed text-muted-foreground">
        {history || "No debate output recorded."}
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {sections["RISKY ANALYST"] && (
        <PerspectiveCard label="🔴 Risky (aggressive)" body={sections["RISKY ANALYST"]} accent="destructive" />
      )}
      {sections["SAFE ANALYST"] && (
        <PerspectiveCard label="🟢 Safe (conservative)" body={sections["SAFE ANALYST"]} accent="primary" />
      )}
      {sections["NEUTRAL ANALYST"] && (
        <PerspectiveCard label="🟡 Neutral (balanced)" body={sections["NEUTRAL ANALYST"]} accent="warning" />
      )}
      {sections["SYNTHESIS"] && (
        <PerspectiveCard label="📋 Synthesis" body={sections["SYNTHESIS"]} />
      )}
    </div>
  );
}

function PerspectiveCard({
  label, body, accent,
}: { label: string; body: string; accent?: "primary" | "destructive" | "warning" }) {
  const borderCls =
    accent === "primary" ? "border-l-primary"
    : accent === "destructive" ? "border-l-destructive"
    : accent === "warning" ? "border-l-warning"
    : "border-l-border";
  return (
    <div className={`rounded-r-md border-l-2 bg-secondary/30 p-3 ${borderCls}`}>
      <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{label}</div>
      <p className="mt-1 whitespace-pre-line text-sm leading-relaxed">{body}</p>
    </div>
  );
}

// ──────────────── Risk Manager (Constitutional) ────────────────
function RiskManagerView({ d }: { d: any }) {
  const action = d?.llm_action ?? "—";
  const conf = d?.llm_confidence;
  const constitution = d?.llm_constitution_check;
  const debateQuality = d?.llm_debate_quality;
  const qualitative = d?.llm_qualitative_risks;
  const clauses = d?.clauses_referenced || [];
  const gateNotes = d?.final_gate_notes || [];
  const accent =
    action === "BUY" ? "text-primary"
    : action === "SELL" ? "text-destructive"
    : "text-warning";

  return (
    <div className="space-y-3">
      <KV label="Final action" value={action} valueClass={`font-semibold ${accent}`} />
      {typeof conf === "number" && <KV label="Confidence" value={`${Math.round(conf * 100)}%`} />}

      {constitution && constitution !== "(not parseable)" && (
        <>
          <Divider />
          <SectionTitle>Constitutional check</SectionTitle>
          <p className="whitespace-pre-line text-sm leading-relaxed text-muted-foreground">
            {constitution}
          </p>
        </>
      )}

      {qualitative && qualitative !== "(not parseable)" && (
        <>
          <Divider />
          <SectionTitle>Qualitative risks</SectionTitle>
          <p className="whitespace-pre-line text-sm leading-relaxed text-muted-foreground">
            {qualitative}
          </p>
        </>
      )}

      {debateQuality && debateQuality !== "(not parseable)" && (
        <>
          <Divider />
          <SectionTitle>Debate quality</SectionTitle>
          <p className="whitespace-pre-line text-sm leading-relaxed text-muted-foreground">
            {debateQuality}
          </p>
        </>
      )}

      {clauses.length > 0 && (
        <>
          <Divider />
          <SectionTitle>Constitution clauses referenced</SectionTitle>
          <div className="flex flex-wrap gap-1">
            {clauses.map((c: number) => (
              <Badge key={c} variant="outline" className="font-mono text-[10px]">Clause {c}</Badge>
            ))}
          </div>
        </>
      )}

      {gateNotes.length > 0 && (
        <>
          <Divider />
          <SectionTitle>Final gate notes</SectionTitle>
          <ul className="list-inside list-disc space-y-1 text-sm text-warning">
            {gateNotes.map((n: string, i: number) => <li key={i}>{n}</li>)}
          </ul>
        </>
      )}
    </div>
  );
}

function GenericView({ d }: { d: any }) {
  if (!d || (typeof d === "object" && Object.keys(d).length === 0)) {
    return <p className="text-sm italic text-muted-foreground">No additional details.</p>;
  }
  return (
    <div className="space-y-1.5">
      {Object.entries(d).slice(0, 20).map(([k, v]) => (
        <KV
          key={k}
          label={k.replace(/_/g, " ")}
          value={typeof v === "object" ? JSON.stringify(v).slice(0, 80) : String(v)}
        />
      ))}
    </div>
  );
}

// ──────────────── Tiny shared ────────────────
function KV({ label, value, valueClass }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="capitalize text-muted-foreground">{label}</span>
      <span className={`text-right font-mono tabular ${valueClass ?? ""}`}>{value}</span>
    </div>
  );
}
function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
      {children}
    </div>
  );
}
function Divider() {
  return <div className="my-1 h-px bg-border" />;
}
function fmtInt(v: any): string {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return new Intl.NumberFormat("en-US").format(Math.round(n));
}

function parseSections(text: string, headings: string[]): Record<string, string> {
  if (!text) return {};
  const out: Record<string, string> = {};
  const escaped = headings.map((h) => h.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = escaped.join("|");
  const re = new RegExp(
    `(?:\\*\\*?\\d?\\.?\\s*|#{1,3}\\s*|🔴|🟢|🟡|📋)?\\s*(${pattern})[:*\\s]*([\\s\\S]*?)(?=(?:\\*\\*?\\d?\\.?\\s*|#{1,3}\\s*|🔴|🟢|🟡|📋)?\\s*(?:${pattern})\\b|$)`,
    "gi",
  );
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const heading = m[1].trim();
    const body = m[2].trim().replace(/^[*:\s]+/, "").replace(/\n{3,}/g, "\n\n");
    if (body && body.length > 5) out[heading.toUpperCase()] = body.slice(0, 1500);
  }
  // Normalize keys to Title Case versions
  const titled: Record<string, string> = {};
  headings.forEach((h) => {
    const key = h.toUpperCase();
    if (out[key]) titled[h.includes(" ") ? h : h.toUpperCase()] = out[key];
  });
  return Object.keys(titled).length > 0 ? titled : out;
}
