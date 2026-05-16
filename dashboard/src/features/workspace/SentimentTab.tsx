import { Link } from "react-router-dom";
import { AlertOctagon, Brain, Globe2, Layers as LayersIcon, Sparkles, Users } from "lucide-react";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Badge, type BadgeTone } from "../../components/ui/Badge";
import { Gauge } from "../../components/ui/Gauge";
import { Skeleton } from "../../components/ui/Skeleton";
import { EmptyState } from "../../components/ui/EmptyState";
import { Markdown } from "../../components/ui/Markdown";
import { useLatestSessionTrace } from "../../hooks/useSessionTrace";
import { useT } from "../../lib/i18n";

// Layer schema mirrors tradingagents.sentiment.{macro,market,sector,stock,blender}
// surfaces. Anything missing in full_state renders as a neutral placeholder.

interface NoSignalReason {
  gate_failed?: string;
  human_readable?: string;
  metrics?: Record<string, unknown>;
}

interface LayerRecord {
  status?: string;
  reason?: NoSignalReason | null;
  score?: number;
  confidence?: number;
  // Layer-specific fields:
  regime?: string;
  market_regime?: string;
  volatility_mood?: string;
  macro_direction?: string;
  composite_regime?: string;
  contradicts_market?: boolean;
  sector?: string;
}

interface BlendResult {
  confidence_multiplier?: number;
  position_size_multiplier?: number;
  market_regime?: string;
  volatility_mood?: string;
  macro_direction?: string;
  applied_modifiers?: string[];
  narrative?: string;
}

const MARKET_REGIME_TONE: Record<string, BadgeTone> = {
  EUPHORIA: "warning",
  GREED: "up",
  NEUTRAL: "accent",
  FEAR: "warning",
  PANIC: "down",
};
const VOL_MOOD_TONE: Record<string, BadgeTone> = {
  CALM: "brand",
  ELEVATED: "warning",
  STRESSED: "down",
};
const MACRO_TONE: Record<string, BadgeTone> = {
  RISK_ON: "up",
  RISK_OFF: "down",
  NEUTRAL: "accent",
};

function asRecord(value: unknown): LayerRecord {
  if (value && typeof value === "object") return value as LayerRecord;
  return {};
}

function isNoSignal(layer: LayerRecord): boolean {
  return (layer.status ?? "").toUpperCase() === "NO_SIGNAL";
}

export function SentimentTab({ ticker }: { ticker: string }) {
  const t = useT();
  const { loading, session } = useLatestSessionTrace(ticker);

  if (loading) {
    return (
      <Card>
        <CardBody>
          <Skeleton className="h-48 w-full rounded-lg" />
        </CardBody>
      </Card>
    );
  }

  const fullState = (session?.full_state ?? {}) as Record<string, unknown>;
  const audit = asRecord(fullState.sentiment_audit);
  // Either the surfacing module wrote a single `sentiment_audit` blob
  // (preferred) or the individual layer keys live at the top level.
  const macro = asRecord((audit as Record<string, unknown>).macro ?? fullState.macro_sentiment);
  const market = asRecord((audit as Record<string, unknown>).market ?? fullState.market_sentiment);
  const sector = asRecord((audit as Record<string, unknown>).sector ?? fullState.sector_sentiment);
  const stock = asRecord(fullState.social_sentiment_analysis);
  const blend = (fullState.sentiment_blend_result ?? {}) as BlendResult;
  const narrative =
    typeof fullState.sentiment_report === "string"
      ? (fullState.sentiment_report as string)
      : null;

  const anyLayer =
    Object.keys(macro).length > 0 ||
    Object.keys(market).length > 0 ||
    Object.keys(sector).length > 0 ||
    Object.keys(stock).length > 0;

  if (!anyLayer && !narrative) {
    return (
      <Card>
        <CardBody>
          <EmptyState
            icon={<Brain className="h-4 w-4" />}
            title={t("sent.empty.title")}
            description={t("sent.empty.desc")}
            action={
              <Link
                to={`/run?ticker=${encodeURIComponent(ticker)}`}
                className="inline-flex items-center gap-1 text-xs text-brand-400 hover:text-brand-300"
              >
                <Sparkles className="h-3 w-3" aria-hidden />
                {t("sent.empty.cta")}
              </Link>
            }
          />
        </CardBody>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <BlendCard blend={blend} />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <LayerCard
          icon={<Globe2 className="h-3.5 w-3.5" aria-hidden />}
          titleKey="sent.macro.title"
          descKey="sent.macro.desc"
          layerLabel="A0"
          layer={macro}
          renderSignal={(l) => (
            <Badge tone={MACRO_TONE[(l.composite_regime ?? "").toUpperCase()] ?? "neutral"}>
              {l.composite_regime ?? t("sent.unknown")}
            </Badge>
          )}
        />
        <LayerCard
          icon={<Users className="h-3.5 w-3.5" aria-hidden />}
          titleKey="sent.market.title"
          descKey="sent.market.desc"
          layerLabel="A"
          layer={market}
          renderSignal={(l) => (
            <div className="flex flex-wrap gap-1.5">
              <Badge
                tone={
                  MARKET_REGIME_TONE[(l.market_regime ?? l.regime ?? "").toUpperCase()] ??
                  "neutral"
                }
              >
                {l.market_regime ?? l.regime ?? t("sent.unknown")}
              </Badge>
              {l.volatility_mood && (
                <Badge
                  tone={VOL_MOOD_TONE[l.volatility_mood.toUpperCase()] ?? "neutral"}
                >
                  {l.volatility_mood}
                </Badge>
              )}
            </div>
          )}
        />
        <LayerCard
          icon={<LayersIcon className="h-3.5 w-3.5" aria-hidden />}
          titleKey="sent.sector.title"
          descKey="sent.sector.desc"
          layerLabel="B"
          layer={sector}
          renderSignal={(l) => (
            <span className="num text-sm font-semibold text-fg">
              {typeof l.score === "number" ? l.score.toFixed(2) : "—"}
            </span>
          )}
        />
        <LayerCard
          icon={<Brain className="h-3.5 w-3.5" aria-hidden />}
          titleKey="sent.stock.title"
          descKey="sent.stock.desc"
          layerLabel="C"
          layer={stock}
          renderSignal={(l) => (
            <div className="flex flex-wrap gap-1.5 items-center">
              <span className="num text-sm font-semibold text-fg">
                {typeof l.score === "number" ? l.score.toFixed(2) : "—"}
              </span>
              {l.contradicts_market && (
                <Badge tone="warning">{t("sent.stock.contradicts")}</Badge>
              )}
            </div>
          )}
        />
      </div>

      {narrative && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>{t("sent.narrative.title")}</CardTitle>
              <CardDescription>{t("sent.narrative.desc")}</CardDescription>
            </div>
          </CardHeader>
          <CardBody>
            <Markdown>{narrative}</Markdown>
          </CardBody>
        </Card>
      )}
    </div>
  );
}

function BlendCard({ blend }: { blend: BlendResult }) {
  const t = useT();
  const confMult = blend.confidence_multiplier ?? null;
  const sizeMult = blend.position_size_multiplier ?? null;
  const hasBlend = confMult !== null || sizeMult !== null || (blend.applied_modifiers?.length ?? 0) > 0;

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>{t("sent.blend.title")}</CardTitle>
          <CardDescription>{t("sent.blend.desc")}</CardDescription>
        </div>
        <Badge tone="brand" className="font-mono">
          Layer E
        </Badge>
      </CardHeader>
      <CardBody className="flex flex-col gap-3">
        {!hasBlend ? (
          <p className="text-sm text-fg-muted">{t("sent.blend.empty")}</p>
        ) : (
          <>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {confMult !== null && (
                <Gauge
                  value={Math.round(confMult * 100)}
                  label={t("sent.blend.confidenceMult")}
                  tone={confMult < 1 ? "warning" : "brand"}
                  max={150}
                  unit="%"
                />
              )}
              {sizeMult !== null && (
                <Gauge
                  value={Math.round(sizeMult * 100)}
                  label={t("sent.blend.sizeMult")}
                  tone={sizeMult < 1 ? "warning" : "brand"}
                  max={150}
                  unit="%"
                />
              )}
            </div>
            {(blend.applied_modifiers?.length ?? 0) > 0 && (
              <div>
                <p className="text-[10px] uppercase tracking-wider text-fg-subtle mb-1.5">
                  {t("sent.blend.modifiers")}
                </p>
                <ul className="flex flex-wrap gap-1.5">
                  {blend.applied_modifiers!.map((m, i) => (
                    <li
                      key={i}
                      className="px-2 py-0.5 rounded-md text-[11px] num bg-ink-800 border border-line text-fg-muted"
                    >
                      {m}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}

function LayerCard({
  icon,
  titleKey,
  descKey,
  layerLabel,
  layer,
  renderSignal,
}: {
  icon: React.ReactNode;
  titleKey: string;
  descKey: string;
  layerLabel: string;
  layer: LayerRecord;
  renderSignal: (l: LayerRecord) => React.ReactNode;
}) {
  const t = useT();
  const noSignal = isNoSignal(layer);
  const empty = Object.keys(layer).length === 0;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          {icon}
          <div>
            <CardTitle>{t(titleKey)}</CardTitle>
            <CardDescription>{t(descKey)}</CardDescription>
          </div>
        </div>
        <Badge tone="neutral" className="font-mono">
          Layer {layerLabel}
        </Badge>
      </CardHeader>
      <CardBody>
        {empty ? (
          <p className="text-xs text-fg-subtle">{t("sent.layer.empty")}</p>
        ) : noSignal ? (
          <NoSignalDetail reason={layer.reason ?? null} />
        ) : (
          <div className="flex flex-col gap-2.5">
            {renderSignal(layer)}
            {typeof layer.confidence === "number" && (
              <Gauge
                value={Math.round(layer.confidence * 100)}
                label={t("sent.layer.confidence")}
                tone="accent"
                unit="%"
              />
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function NoSignalDetail({ reason }: { reason: NoSignalReason | null }) {
  const t = useT();
  return (
    <div className="flex flex-col gap-2 rounded-md border border-amber-400/30 bg-amber-400/5 px-3 py-2.5">
      <div className="flex items-center gap-2">
        <AlertOctagon className="h-3.5 w-3.5 text-amber-400" aria-hidden />
        <span className="text-xs font-semibold uppercase tracking-wider text-amber-300">
          NO_SIGNAL
        </span>
      </div>
      {reason?.gate_failed && (
        <p className="text-[11px] num text-fg-muted">
          <span className="text-fg-subtle">{t("sent.nosignal.gate")} </span>
          {reason.gate_failed}
        </p>
      )}
      {reason?.human_readable && (
        <p className="text-xs text-fg-muted leading-relaxed">
          {reason.human_readable}
        </p>
      )}
      {reason?.metrics && Object.keys(reason.metrics).length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {Object.entries(reason.metrics).map(([k, v]) => (
            <li
              key={k}
              className="px-1.5 py-0.5 rounded text-[10px] num bg-ink-800 text-fg-subtle border border-line"
            >
              {k}={String(v)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
