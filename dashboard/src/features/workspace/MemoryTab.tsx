import { useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  Brain,
  Lightbulb,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Badge, type BadgeTone } from "../../components/ui/Badge";
import { Skeleton } from "../../components/ui/Skeleton";
import { EmptyState } from "../../components/ui/EmptyState";
import {
  DEFAULT_MIN_SIMILARITY,
  MEMORY_AGENTS,
  useAgentMemoryFanout,
  useReflections,
} from "../../hooks/useAgentMemory";
import { useT } from "../../lib/i18n";
import { cn } from "../../lib/utils";
import type {
  MemoryAgent,
  MemoryMatch,
  Reflection,
} from "../../services/api/types";

const AGENT_LABEL_KEY: Record<MemoryAgent, string> = {
  bull_memory: "agent.bull",
  bear_memory: "agent.bear",
  trader_memory: "agent.trader",
  invest_judge_memory: "agent.research_manager",
  risk_manager_memory: "agent.risk_manager",
};

const MEMORY_TYPE_TONE: Record<string, BadgeTone> = {
  reflection: "accent",
  thesis: "brand",
  execution: "brand",
  risk_decision: "warning",
  seed: "neutral",
};

export function MemoryTab({ ticker }: { ticker: string }) {
  const t = useT();
  // Let the operator slacken the threshold (e.g. 0.1) when memories look sparse
  // to surface near-miss precedents. Below 0.0 disables filtering entirely.
  const [threshold, setThreshold] = useState<number>(DEFAULT_MIN_SIMILARITY);

  const memory = useAgentMemoryFanout({
    ticker,
    k: 3,
    minSimilarity: threshold,
  });
  const reflections = useReflections(ticker, { limit: 10 });

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("memory.controls.title")}</CardTitle>
            <CardDescription>{t("memory.controls.desc")}</CardDescription>
          </div>
          <ThresholdSlider value={threshold} onChange={setThreshold} />
        </CardHeader>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {MEMORY_AGENTS.map((agent) => (
          <AgentMemoryCard
            key={agent}
            agent={agent}
            ticker={ticker}
            data={memory.byAgent[agent]}
            loading={memory.isLoading}
            threshold={threshold}
          />
        ))}
      </div>

      <ReflectionsCard
        loading={reflections.isLoading}
        reflections={reflections.data?.reflections ?? []}
      />
    </div>
  );
}

function ThresholdSlider({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  const t = useT();
  return (
    <label className="flex items-center gap-2 text-xs text-fg-muted shrink-0">
      <span className="uppercase tracking-wider">
        {t("memory.controls.threshold")}
      </span>
      <input
        type="range"
        min={0}
        max={0.9}
        step={0.05}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        aria-label={t("memory.controls.threshold")}
        className="h-1 w-24 accent-brand-500"
      />
      <span className="num text-fg w-10 text-end">{value.toFixed(2)}</span>
    </label>
  );
}

function AgentMemoryCard({
  agent,
  ticker,
  data,
  loading,
  threshold,
}: {
  agent: MemoryAgent;
  ticker: string;
  data: ReturnType<typeof useAgentMemoryFanout>["byAgent"][MemoryAgent];
  loading: boolean;
  threshold: number;
}) {
  const t = useT();
  const matches = data?.results ?? [];
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>{t(AGENT_LABEL_KEY[agent])}</CardTitle>
          <CardDescription>{agent}</CardDescription>
        </div>
        <Badge tone="neutral">
          {matches.length} {t("memory.card.matches")}
        </Badge>
      </CardHeader>
      <CardBody className="flex flex-col gap-2">
        {loading && !data ? (
          <Skeleton className="h-24 w-full rounded-md" />
        ) : matches.length === 0 ? (
          <p className="text-xs text-fg-subtle">
            {t("memory.card.empty.prefix")} {ticker}{" "}
            {threshold > 0 && (
              <>
                · {t("memory.card.empty.threshold")}{" "}
                <span className="num text-fg-muted">{threshold.toFixed(2)}</span>
              </>
            )}
          </p>
        ) : (
          matches.map((match, i) => (
            <MemoryMatchRow key={i} match={match} threshold={threshold} />
          ))
        )}
      </CardBody>
    </Card>
  );
}

function MemoryMatchRow({
  match,
  threshold,
}: {
  match: MemoryMatch;
  threshold: number;
}) {
  const t = useT();
  const score = match.similarity_score;
  const aboveThreshold = score >= threshold;
  const memType = match.metadata.memory_type ?? "";
  const tone = MEMORY_TYPE_TONE[memType] ?? "neutral";

  return (
    <div className="rounded-md border border-line bg-ink-900/40 px-3 py-2 flex flex-col gap-1.5">
      <div className="flex items-center gap-2 flex-wrap text-[11px]">
        <Badge
          tone={aboveThreshold ? "brand" : "warning"}
          className="font-mono"
        >
          {(score * 100).toFixed(0)}%
        </Badge>
        {memType && (
          <Badge tone={tone} className="normal-case">
            {memType}
          </Badge>
        )}
        {match.metadata.trade_date && (
          <span className="num text-fg-subtle">{match.metadata.trade_date}</span>
        )}
        {match.metadata.ticker && match.metadata.ticker !== "—" && (
          <span className="num text-fg-muted">{match.metadata.ticker}</span>
        )}
        {!aboveThreshold && (
          <span className="text-[10px] text-amber-300 uppercase tracking-wider">
            {t("memory.match.belowThreshold")}
          </span>
        )}
      </div>
      <p className="text-xs text-fg leading-relaxed">
        {match.matched_situation}
      </p>
      {match.recommendation && (
        <p className="text-[11px] text-fg-muted flex items-start gap-1.5">
          <Lightbulb className="h-3 w-3 mt-0.5 shrink-0" aria-hidden />
          <span>{match.recommendation}</span>
        </p>
      )}
    </div>
  );
}

function ReflectionsCard({
  loading,
  reflections,
}: {
  loading: boolean;
  reflections: Reflection[];
}) {
  const t = useT();
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>{t("memory.reflections.title")}</CardTitle>
          <CardDescription>{t("memory.reflections.desc")}</CardDescription>
        </div>
        <RefreshCw className="h-3.5 w-3.5 text-fg-subtle" aria-hidden />
      </CardHeader>
      <CardBody>
        {loading ? (
          <Skeleton className="h-24 w-full rounded-md" />
        ) : reflections.length === 0 ? (
          <EmptyState
            icon={<Brain className="h-4 w-4" />}
            title={t("memory.reflections.empty.title")}
            description={t("memory.reflections.empty.desc")}
          />
        ) : (
          <ul className="flex flex-col gap-2">
            {reflections.map((r, i) => (
              <ReflectionRow key={i} reflection={r} />
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

function ReflectionRow({ reflection }: { reflection: Reflection }) {
  const t = useT();
  const outcomeSummary = summarizeOutcome(reflection.outcome);
  return (
    <li className="rounded-md border border-line bg-ink-900/40 px-3 py-2.5 flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <Badge tone="accent">{t(AGENT_LABEL_KEY[reflection.agent_name])}</Badge>
        {reflection.trade_date && (
          <span className="num text-fg-subtle">{reflection.trade_date}</span>
        )}
        {reflection.ticker && (
          <span className="num text-fg-muted">{reflection.ticker}</span>
        )}
        {outcomeSummary && (
          <span
            className={cn(
              "inline-flex items-center gap-1 text-[10px] uppercase tracking-wider",
              outcomeSummary.tone === "up"
                ? "text-up"
                : outcomeSummary.tone === "down"
                  ? "text-down"
                  : "text-fg-muted"
            )}
          >
            {outcomeSummary.tone === "up" && (
              <ArrowUp className="h-2.5 w-2.5" aria-hidden />
            )}
            {outcomeSummary.tone === "down" && (
              <ArrowDown className="h-2.5 w-2.5" aria-hidden />
            )}
            {outcomeSummary.label}
          </span>
        )}
      </div>
      <p className="text-xs text-fg leading-relaxed">{reflection.situation}</p>
      {reflection.recommendation && (
        <p className="text-[11px] text-fg-muted flex items-start gap-1.5">
          <Sparkles className="h-3 w-3 mt-0.5 shrink-0" aria-hidden />
          <span>{reflection.recommendation}</span>
        </p>
      )}
    </li>
  );
}

interface OutcomeSummary {
  label: string;
  tone: "up" | "down" | "neutral";
}

// The reflection writer stores outcome as a JSON blob with shape like
// `{verdict: "WIN" | "LOSS", forward_return: 0.034}`. We surface a tiny
// badge so the operator can scan past lessons at a glance.
function summarizeOutcome(outcome: unknown): OutcomeSummary | null {
  if (outcome === null || outcome === undefined) return null;
  let parsed: Record<string, unknown> | null = null;
  if (typeof outcome === "string") {
    try {
      parsed = JSON.parse(outcome) as Record<string, unknown>;
    } catch {
      return { label: outcome.slice(0, 24), tone: "neutral" };
    }
  } else if (typeof outcome === "object") {
    parsed = outcome as Record<string, unknown>;
  }
  if (!parsed) return null;

  const verdict = String(parsed.verdict ?? "").toUpperCase();
  const fwd =
    typeof parsed.forward_return === "number"
      ? (parsed.forward_return as number)
      : null;

  if (verdict === "WIN") {
    return {
      tone: "up",
      label: fwd !== null ? `WIN ${(fwd * 100).toFixed(1)}%` : "WIN",
    };
  }
  if (verdict === "LOSS") {
    return {
      tone: "down",
      label: fwd !== null ? `LOSS ${(fwd * 100).toFixed(1)}%` : "LOSS",
    };
  }
  if (fwd !== null) {
    return {
      tone: fwd >= 0 ? "up" : "down",
      label: `${(fwd * 100).toFixed(1)}%`,
    };
  }
  return null;
}
