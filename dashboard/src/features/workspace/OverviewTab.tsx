import React, { useState } from "react";
import { Link } from "react-router-dom";
import {
  TrendingUp,
  TrendingDown,
  Scale,
  ShieldCheck,
  ShieldAlert,
  ShieldX,
  BarChart3,
  Newspaper,
  PieChart,
  MessageSquare,
  Sparkles,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { Card, CardBody, CardHeader, CardTitle, CardDescription } from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { Gauge } from "../../components/ui/Gauge";
import { Skeleton } from "../../components/ui/Skeleton";
import { EmptyState } from "../../components/ui/EmptyState";
import { useLatestSessionTrace } from "../../hooks/useSessionTrace";
import { signalBg } from "../../lib/utils";
import { useT } from "../../lib/i18n";
import { cn } from "../../lib/utils";
import type { SessionTraceSummary } from "../../services/api/types";

// ── Data extraction ────────────────────────────────────────────────

interface AgentOutputs {
  finalDecision: string | null;
  confidence: number | null;
  tradeDate: string | null;
  riskAction: string | null;
  riskMetrics: Record<string, unknown>;
  riskAssessment: string | null;
  marketRegime: string | null;
  volatilityMood: string | null;
  macroDirection: string | null;
  positionMultiplier: number | null;
  contradictsMarket: boolean;
  bullText: string | null;
  bearText: string | null;
  judgeText: string | null;
  traderText: string | null;
  marketReport: string | null;
  newsReport: string | null;
  fundamentalsReport: string | null;
  socialReport: string | null;
  scores: Record<string, number>;
}

function extractOutputs(session: SessionTraceSummary | null | undefined): AgentOutputs {
  const full = (session?.full_state ?? {}) as Record<string, unknown>;
  const debate = (full.investment_debate_state ?? {}) as Record<string, unknown>;
  const riskDebate = (full.risk_debate_state ?? {}) as Record<string, unknown>;
  const blend = (full.sentiment_blend_result ?? {}) as Record<string, unknown>;
  const social = (full.social_sentiment_analysis ?? {}) as Record<string, unknown>;

  const bullRaw = debate.bull_thesis ?? debate.bull_history;
  const bearRaw = debate.bear_thesis ?? debate.bear_history;
  const riskRaw = full.risk_assessment ?? riskDebate.judge_decision;

  return {
    finalDecision: (session?.final_decision as string) ?? null,
    confidence: session?.confidence_overall ?? null,
    tradeDate: session?.trade_date ?? null,
    riskAction: (full.risk_action as string) ?? null,
    riskMetrics: (full.risk_metrics ?? {}) as Record<string, unknown>,
    riskAssessment: stringify(riskRaw),
    marketRegime: (blend.market_regime as string) ?? null,
    volatilityMood: (blend.volatility_mood as string) ?? null,
    macroDirection: (blend.macro_direction as string) ?? null,
    positionMultiplier:
      typeof blend.position_size_multiplier === "number"
        ? (blend.position_size_multiplier as number)
        : null,
    contradictsMarket: (social.contradicts_market as boolean) ?? false,
    bullText: stringify(bullRaw),
    bearText: stringify(bearRaw),
    judgeText: stringify(debate.judge_decision ?? full.investment_plan),
    traderText: stringify(full.trader_investment_plan ?? full.execution_plan),
    marketReport: stringify(full.market_report),
    newsReport: stringify(full.news_report),
    fundamentalsReport: stringify(full.fundamentals_report),
    socialReport: stringify(full.sentiment_report),
    scores: (full.confidence_scores ?? {}) as Record<string, number>,
  };
}

function stringify(v: unknown): string | null {
  if (!v) return null;
  if (typeof v === "string") return v.trim() || null;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

function parseSignal(decision: string | null): "BUY" | "SELL" | "HOLD" | null {
  if (!decision) return null;
  const u = decision.toUpperCase();
  if (u.includes("BUY")) return "BUY";
  if (u.includes("SELL")) return "SELL";
  if (u.includes("HOLD")) return "HOLD";
  return null;
}

// ── Shared helpers ─────────────────────────────────────────────────

function truncate(text: string, len: number): string {
  return text.length <= len ? text : text.slice(0, len).trimEnd() + "…";
}

function ExpandableText({
  text,
  previewLen = 220,
  className,
}: {
  text: string;
  previewLen?: number;
  className?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const needsToggle = text.length > previewLen;
  return (
    <div className={className}>
      <p className="text-xs text-fg-muted leading-relaxed whitespace-pre-wrap">
        {expanded ? text : truncate(text, previewLen)}
      </p>
      {needsToggle && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-2 inline-flex items-center gap-1 text-[11px] text-brand-400 hover:text-brand-500 font-medium"
        >
          {expanded ? (
            <>Collapse <ChevronUp className="h-3 w-3" /></>
          ) : (
            <>Read more <ChevronDown className="h-3 w-3" /></>
          )}
        </button>
      )}
    </div>
  );
}

function ConfidenceBar({ value, label }: { value: number | null; label: string }) {
  const pct = value !== null ? Math.round(Math.max(0, Math.min(1, value)) * 100) : null;
  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] text-fg-subtle uppercase tracking-wider w-16 shrink-0">{label}</span>
      <div className="flex-1 h-1.5 rounded-full bg-ink-700 overflow-hidden">
        {pct !== null && (
          <div
            className="h-full rounded-full bg-brand-400 transition-all"
            style={{ width: `${pct}%` }}
          />
        )}
      </div>
      <span className="text-[10px] font-mono text-fg-muted w-8 text-right shrink-0">
        {pct !== null ? `${pct}%` : "—"}
      </span>
    </div>
  );
}

const RISK_ACTION_CONFIG: Record<string, { label: string; tone: "up" | "warning" | "down" | "neutral"; icon: React.ReactNode }> = {
  ALLOW: { label: "Allow", tone: "up", icon: <ShieldCheck className="h-4 w-4 text-up" /> },
  WARN: { label: "Warning", tone: "warning", icon: <ShieldAlert className="h-4 w-4 text-nosignal" /> },
  THROTTLE: { label: "Throttle", tone: "warning", icon: <ShieldAlert className="h-4 w-4 text-nosignal" /> },
  VETO: { label: "Veto", tone: "down", icon: <ShieldX className="h-4 w-4 text-down" /> },
};

const MARKET_REGIME_TONES: Record<string, "up" | "down" | "warning" | "accent" | "neutral"> = {
  EUPHORIA: "warning",
  GREED: "up",
  NEUTRAL: "accent",
  FEAR: "warning",
  PANIC: "down",
};

const VOL_MOOD_TONES: Record<string, "up" | "warning" | "down"> = {
  CALM: "up",
  ELEVATED: "warning",
  STRESSED: "down",
};

// ── Main component ─────────────────────────────────────────────────

export function OverviewTab({ ticker }: { ticker: string }) {
  const t = useT();
  const { loading, source, session } = useLatestSessionTrace(ticker);
  const out = extractOutputs(session);
  const sig = parseSignal(out.finalDecision);

  if (loading) {
    return (
      <div className="flex flex-col gap-5">
        <Skeleton className="h-32 w-full rounded-xl" />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          <Skeleton className="h-48 w-full rounded-xl" />
          <Skeleton className="h-48 w-full rounded-xl" />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
          <Skeleton className="h-40 rounded-xl" />
          <Skeleton className="h-40 rounded-xl" />
          <Skeleton className="h-40 rounded-xl" />
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-5">
          <Skeleton className="h-36 rounded-xl" />
          <Skeleton className="h-36 rounded-xl" />
          <Skeleton className="h-36 rounded-xl" />
          <Skeleton className="h-36 rounded-xl" />
        </div>
      </div>
    );
  }

  if (!session) {
    return (
      <Card>
        <CardBody>
          <EmptyState
            icon={<Sparkles className="h-4 w-4" />}
            title={t("workspace.overview.noSignal.title")}
            description={t("workspace.overview.noSignal.desc")}
            action={
              <Link
                to={`/run?ticker=${encodeURIComponent(ticker)}`}
                className="inline-flex items-center gap-1 text-xs text-brand-400 hover:text-brand-500"
              >
                <Sparkles className="h-3 w-3" aria-hidden />
                {t("workspace.overview.noSignal.cta")}
              </Link>
            }
          />
        </CardBody>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      {/* ── Row 1: Hero Signal Card ─────────────────────────────── */}
      <HeroSignalCard ticker={ticker} sig={sig} out={out} source={source} />

      {/* ── Row 2: Bull vs Bear ─────────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <DebateCard
          side="bull"
          text={out.bullText}
          confidence={out.scores["bull"] ?? null}
        />
        <DebateCard
          side="bear"
          text={out.bearText}
          confidence={out.scores["bear"] ?? null}
        />
      </div>

      {/* ── Row 3: Decisions ────────────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        <div className="md:col-span-2">
          <ResearchManagerCard text={out.judgeText} confidence={out.scores["overall"] ?? null} />
        </div>
        <RiskManagerCard
          action={out.riskAction}
          metrics={out.riskMetrics}
          assessment={out.riskAssessment}
          confidence={out.scores["risk_manager"] ?? null}
        />
      </div>

      {/* ── Row 4: Analyst Team ─────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-5">
        <AnalystCard
          icon={<BarChart3 className="h-4 w-4 text-brand-400" />}
          title="Market Analyst"
          report={out.marketReport}
          confidence={out.scores["market"] ?? null}
        />
        <AnalystCard
          icon={<Newspaper className="h-4 w-4 text-brand-400" />}
          title="News Analyst"
          report={out.newsReport}
          confidence={out.scores["news"] ?? null}
        />
        <AnalystCard
          icon={<PieChart className="h-4 w-4 text-brand-400" />}
          title="Fundamentals"
          report={out.fundamentalsReport}
          confidence={out.scores["fundamentals"] ?? null}
        />
        <AnalystCard
          icon={<MessageSquare className="h-4 w-4 text-brand-400" />}
          title="Social Media"
          report={out.socialReport}
          confidence={out.scores["social"] ?? null}
        />
      </div>
    </div>
  );
}

// ── Row 1: Hero signal card ────────────────────────────────────────

function HeroSignalCard({
  ticker,
  sig,
  out,
  source,
}: {
  ticker: string;
  sig: "BUY" | "SELL" | "HOLD" | null;
  out: AgentOutputs;
  source: "postgres" | "jsonl" | "none" | null;
}) {
  const riskCfg = out.riskAction ? RISK_ACTION_CONFIG[out.riskAction.toUpperCase()] : null;
  const regimeTone = out.marketRegime
    ? MARKET_REGIME_TONES[out.marketRegime.toUpperCase()] ?? "neutral"
    : "neutral";
  const moodTone = out.volatilityMood
    ? VOL_MOOD_TONES[out.volatilityMood.toUpperCase()] ?? "up"
    : "up";

  return (
    <Card>
      <CardBody className="pt-5">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          {/* Signal + confidence */}
          <div className="flex items-center gap-4 flex-wrap">
            {sig ? (
              <span
                className={cn(
                  "inline-flex items-center justify-center px-4 h-10 rounded-lg border text-sm font-bold tracking-widest",
                  signalBg(sig)
                )}
              >
                {sig}
              </span>
            ) : (
              <span className="inline-flex items-center justify-center px-4 h-10 rounded-lg border border-line bg-ink-700 text-fg-muted text-sm font-semibold tracking-wider">
                NO SIGNAL
              </span>
            )}

            {out.confidence !== null && (
              <Gauge
                value={out.confidence * 100}
                label="Confidence"
                tone={sig === "SELL" ? "down" : sig === "BUY" ? "up" : "accent"}
                unit="%"
              />
            )}

            {out.tradeDate && (
              <span className="text-xs text-fg-muted num">{out.tradeDate}</span>
            )}

            {source === "postgres" && (
              <Badge tone="brand">verified</Badge>
            )}
          </div>

          {/* Regime chips + CTA */}
          <div className="flex items-center gap-2 flex-wrap">
            {riskCfg && (
              <div className="flex items-center gap-1.5">
                {riskCfg.icon}
                <Badge tone={riskCfg.tone}>{riskCfg.label}</Badge>
              </div>
            )}
            {out.marketRegime && (
              <Badge tone={regimeTone}>{out.marketRegime}</Badge>
            )}
            {out.volatilityMood && (
              <Badge tone={moodTone}>{out.volatilityMood}</Badge>
            )}
            {out.macroDirection && (
              <Badge tone={out.macroDirection === "RISK_ON" ? "up" : "down"}>
                {out.macroDirection}
              </Badge>
            )}
            {out.contradictsMarket && (
              <Badge tone="warning">Contradicts Market</Badge>
            )}

            <Link to={`/run?ticker=${encodeURIComponent(ticker)}`}>
              <button
                type="button"
                className="inline-flex items-center gap-1.5 px-3 h-8 rounded-lg border border-brand-400 text-brand-400 hover:bg-brand-50 text-xs font-medium transition-colors"
              >
                <Sparkles className="h-3 w-3" aria-hidden />
                Run New Analysis
              </button>
            </Link>
          </div>
        </div>

        {out.positionMultiplier !== null && (
          <div className="mt-3 pt-3 border-t border-line">
            <Gauge
              label="Position Size Multiplier"
              value={Math.round((out.positionMultiplier ?? 0) * 100)}
              max={150}
              unit="%"
              tone={out.positionMultiplier < 1 ? "warning" : "up"}
            />
          </div>
        )}
      </CardBody>
    </Card>
  );
}

// ── Row 2: Debate cards ────────────────────────────────────────────

function DebateCard({
  side,
  text,
  confidence,
}: {
  side: "bull" | "bear";
  text: string | null;
  confidence: number | null;
}) {
  const isBull = side === "bull";
  const Icon = isBull ? TrendingUp : TrendingDown;
  const accentCls = isBull
    ? "text-emerald-700 bg-emerald-50 border-emerald-200"
    : "text-rose-700 bg-rose-50 border-rose-200";
  const iconCls = isBull ? "text-emerald-600" : "text-rose-600";
  const barCls = isBull ? "bg-emerald-500" : "bg-rose-500";
  const pct =
    confidence !== null ? Math.round(Math.max(0, Math.min(1, confidence)) * 100) : null;

  return (
    <div className={cn("rounded-xl border p-5 flex flex-col gap-3", accentCls)}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Icon className={cn("h-4 w-4 shrink-0", iconCls)} aria-hidden />
          <span className="text-sm font-semibold">
            {isBull ? "Bull Researcher" : "Bear Researcher"}
          </span>
        </div>
        {pct !== null && (
          <span className="text-[11px] font-mono font-medium">{pct}%</span>
        )}
      </div>

      {pct !== null && (
        <div className="h-1 rounded-full bg-white/60 overflow-hidden">
          <div className={cn("h-full rounded-full", barCls)} style={{ width: `${pct}%` }} />
        </div>
      )}

      {text ? (
        <ExpandableText text={text} previewLen={220} />
      ) : (
        <p className="text-xs text-fg-subtle italic">No thesis available for this session.</p>
      )}
    </div>
  );
}

// ── Row 3: Research Manager ────────────────────────────────────────

function ResearchManagerCard({
  text,
  confidence,
}: {
  text: string | null;
  confidence: number | null;
}) {
  return (
    <Card className="h-full">
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Scale className="h-4 w-4 text-brand-400" aria-hidden />
            Research Manager
          </CardTitle>
          <CardDescription>Debate judge — investment decision</CardDescription>
        </div>
        {confidence !== null && (
          <span className="text-xs font-mono text-fg-muted num">
            {Math.round(confidence * 100)}%
          </span>
        )}
      </CardHeader>
      <CardBody>
        {text ? (
          <ExpandableText text={text} previewLen={320} />
        ) : (
          <p className="text-xs text-fg-subtle italic">No decision recorded for this session.</p>
        )}
      </CardBody>
    </Card>
  );
}

// ── Row 3: Risk Manager ────────────────────────────────────────────

function RiskManagerCard({
  action,
  metrics,
  assessment,
  confidence,
}: {
  action: string | null;
  metrics: Record<string, unknown>;
  assessment: string | null;
  confidence: number | null;
}) {
  const cfg = action ? RISK_ACTION_CONFIG[action.toUpperCase()] : null;
  const cardBg =
    action?.toUpperCase() === "VETO"
      ? "border-rose-200 bg-rose-50"
      : action?.toUpperCase() === "ALLOW"
      ? "border-emerald-200 bg-emerald-50"
      : "border-amber-200 bg-amber-50";

  const positionPct = metrics.position_pct as number | undefined;
  const advPct = metrics.adv_participation_pct as number | undefined;

  return (
    <div className={cn("rounded-xl border p-5 flex flex-col gap-3 h-full", cardBg)}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold text-fg">Risk Manager</span>
        {cfg && (
          <div className="flex items-center gap-1.5">
            {cfg.icon}
            <Badge tone={cfg.tone}>{cfg.label}</Badge>
          </div>
        )}
      </div>

      {(positionPct !== undefined || advPct !== undefined) && (
        <div className="flex flex-col gap-1.5">
          {positionPct !== undefined && (
            <div className="flex justify-between text-xs">
              <span className="text-fg-muted">Position</span>
              <span className="font-mono font-medium num">{(positionPct * 100).toFixed(1)}%</span>
            </div>
          )}
          {advPct !== undefined && (
            <div className="flex justify-between text-xs">
              <span className="text-fg-muted">ADV</span>
              <span className="font-mono font-medium num">{(advPct * 100).toFixed(1)}%</span>
            </div>
          )}
        </div>
      )}

      {assessment ? (
        <ExpandableText text={assessment} previewLen={180} />
      ) : (
        <p className="text-xs text-fg-subtle italic">No risk assessment available.</p>
      )}

      {confidence !== null && (
        <ConfidenceBar value={confidence} label="Conf." />
      )}
    </div>
  );
}

// ── Row 4: Analyst cards ───────────────────────────────────────────

function AnalystCard({
  icon,
  title,
  report,
  confidence,
}: {
  icon: React.ReactNode;
  title: string;
  report: string | null;
  confidence: number | null;
}) {
  return (
    <Card className="flex flex-col">
      <CardHeader className="pb-2">
        <div className="flex items-center gap-1.5">
          {icon}
          <CardTitle className="text-xs">{title}</CardTitle>
        </div>
      </CardHeader>
      <CardBody className="flex-1 flex flex-col gap-2 pt-0">
        <ConfidenceBar value={confidence} label="Conf." />
        {report ? (
          <ExpandableText text={report} previewLen={150} className="flex-1" />
        ) : (
          <p className="text-xs text-fg-subtle italic flex-1">No report for this session.</p>
        )}
      </CardBody>
    </Card>
  );
}