import { Link } from "react-router-dom";
import { AlertTriangle, FileText, Sparkles, TrendingDown, TrendingUp } from "lucide-react";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Badge, type BadgeTone } from "../../components/ui/Badge";
import { Gauge } from "../../components/ui/Gauge";
import { Skeleton } from "../../components/ui/Skeleton";
import { EmptyState } from "../../components/ui/EmptyState";
import { Markdown } from "../../components/ui/Markdown";
import { useLatestSessionTrace } from "../../hooks/useSessionTrace";
import { useT } from "../../lib/i18n";
import { cn } from "../../lib/utils";

// Display order + label key for the 14 core ratios reported by
// tradingagents/agents/analysts/fundamentals/financial_calculator.py.
const RATIO_DISPLAY: { key: string; labelKey: string; unit?: "%" | ""; decimals: number }[] = [
  { key: "roe",                    labelKey: "fund.ratio.roe",            unit: "%", decimals: 1 },
  { key: "roa",                    labelKey: "fund.ratio.roa",            unit: "%", decimals: 1 },
  { key: "gross_margin",           labelKey: "fund.ratio.gross_margin",   unit: "%", decimals: 1 },
  { key: "operating_margin",       labelKey: "fund.ratio.op_margin",      unit: "%", decimals: 1 },
  { key: "net_margin",             labelKey: "fund.ratio.net_margin",     unit: "%", decimals: 1 },
  { key: "debt_to_equity",         labelKey: "fund.ratio.de",             decimals: 2 },
  { key: "current_ratio",          labelKey: "fund.ratio.current",        decimals: 2 },
  { key: "asset_turnover",         labelKey: "fund.ratio.asset_turn",     decimals: 2 },
  { key: "eps",                    labelKey: "fund.ratio.eps",            decimals: 2 },
  { key: "pe_ratio",               labelKey: "fund.ratio.pe",             decimals: 2 },
  { key: "pb_ratio",               labelKey: "fund.ratio.pb",             decimals: 2 },
  { key: "earnings_yield",         labelKey: "fund.ratio.ey",             unit: "%", decimals: 2 },
  { key: "dividend_yield",         labelKey: "fund.ratio.dy",             unit: "%", decimals: 2 },
  { key: "piotroski_score",        labelKey: "fund.ratio.piotroski",      decimals: 0 },
];

const HEALTH_TONE: Record<string, BadgeTone> = {
  healthy: "brand",
  concerning: "warning",
  critical: "down",
  insufficient_data: "neutral",
};

const DIRECTION_TONE: Record<string, BadgeTone> = {
  up: "up",
  down: "down",
  flat: "accent",
};

function fmtRatio(
  value: unknown,
  unit: "%" | "" | undefined,
  decimals: number
): string {
  if (value === null || value === undefined) return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n.toFixed(decimals)}${unit ?? ""}`;
}

interface FundamentalReport {
  ticker?: string;
  fiscal_period?: string;
  sector?: string;
  ratios?: Record<string, number | null>;
  distress_flags?: string[];
  data_confidence?: number;
  signal_coherence?: number;
  financial_health?: string;
  valuation_assessment?: string;
  earnings_direction?: string;
  earnings_direction_confidence?: number;
  fair_value_range?: unknown;
  key_risks?: string[];
}

export function FundamentalsTab({ ticker }: { ticker: string }) {
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
  const report = (fullState.fundamental_analysis ?? {}) as FundamentalReport;
  const markdown =
    typeof fullState.fundamentals_report === "string"
      ? (fullState.fundamentals_report as string)
      : null;

  const hasReport =
    Object.keys(report).length > 0 &&
    (report.ratios || report.financial_health || markdown);

  if (!hasReport) {
    return (
      <Card>
        <CardBody>
          <EmptyState
            icon={<FileText className="h-4 w-4" />}
            title={t("fund.empty.title")}
            description={t("fund.empty.desc")}
            action={
              <Link
                to={`/run?ticker=${encodeURIComponent(ticker)}`}
                className="inline-flex items-center gap-1 text-xs text-brand-400 hover:text-brand-300"
              >
                <Sparkles className="h-3 w-3" aria-hidden />
                {t("fund.empty.cta")}
              </Link>
            }
          />
        </CardBody>
      </Card>
    );
  }

  const health = (report.financial_health ?? "").toLowerCase();
  const direction = (report.earnings_direction ?? "").toLowerCase();

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("fund.summary.title")}</CardTitle>
            <CardDescription>
              {report.fiscal_period
                ? `${report.fiscal_period} · ${ticker}`
                : ticker}
              {report.sector && ` · ${report.sector}`}
            </CardDescription>
          </div>
          {health && (
            <Badge tone={HEALTH_TONE[health] ?? "neutral"}>
              {t(`fund.health.${health}`)}
            </Badge>
          )}
        </CardHeader>
        <CardBody>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Gauge
              value={report.data_confidence ?? null}
              label={t("fund.gauge.dataConfidence")}
              tone={
                (report.data_confidence ?? 0) >= 70
                  ? "brand"
                  : (report.data_confidence ?? 0) >= 40
                    ? "accent"
                    : "warning"
              }
              unit="%"
              hint={t("fund.gauge.dataConfidence.hint")}
            />
            <Gauge
              value={report.signal_coherence ?? null}
              label={t("fund.gauge.signalCoherence")}
              tone={
                (report.signal_coherence ?? 100) >= 85
                  ? "brand"
                  : (report.signal_coherence ?? 100) >= 60
                    ? "accent"
                    : "warning"
              }
              unit="%"
              hint={t("fund.gauge.signalCoherence.hint")}
            />
          </div>

          {report.valuation_assessment && (
            <p className="mt-4 text-sm text-fg-muted leading-relaxed">
              <span className="text-[10px] uppercase tracking-wider text-fg-subtle me-2">
                {t("fund.valuation")}
              </span>
              {report.valuation_assessment}
            </p>
          )}

          {direction && (
            <div className="mt-4 flex items-center gap-2">
              <span className="text-[10px] uppercase tracking-wider text-fg-subtle">
                {t("fund.earnings.direction")}
              </span>
              <Badge tone={DIRECTION_TONE[direction] ?? "neutral"}>
                {direction === "up" && <TrendingUp className="h-2.5 w-2.5" aria-hidden />}
                {direction === "down" && <TrendingDown className="h-2.5 w-2.5" aria-hidden />}
                {direction.toUpperCase()}
              </Badge>
              {typeof report.earnings_direction_confidence === "number" && (
                <span className="num text-xs text-fg-muted">
                  {report.earnings_direction_confidence}%
                </span>
              )}
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("fund.ratios.title")}</CardTitle>
            <CardDescription>{t("fund.ratios.desc")}</CardDescription>
          </div>
        </CardHeader>
        <CardBody>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5">
            {RATIO_DISPLAY.map(({ key, labelKey, unit, decimals }) => {
              const value = report.ratios?.[key];
              return (
                <div
                  key={key}
                  className="surface rounded-lg px-3 py-2.5 flex flex-col gap-0.5"
                >
                  <span className="text-[10px] uppercase tracking-wider text-fg-muted">
                    {t(labelKey)}
                  </span>
                  <span
                    className={cn(
                      "num text-sm font-semibold",
                      value === null || value === undefined
                        ? "text-fg-subtle"
                        : "text-fg"
                    )}
                  >
                    {fmtRatio(value, unit, decimals)}
                  </span>
                </div>
              );
            })}
          </div>
        </CardBody>
      </Card>

      {(report.distress_flags?.length ?? 0) > 0 && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>{t("fund.flags.title")}</CardTitle>
              <CardDescription>{t("fund.flags.desc")}</CardDescription>
            </div>
            <AlertTriangle className="h-4 w-4 text-amber-400" aria-hidden />
          </CardHeader>
          <CardBody>
            <ul className="flex flex-wrap gap-1.5">
              {report.distress_flags!.map((flag) => (
                <li
                  key={flag}
                  className="px-2 py-1 rounded-md text-[11px] num bg-amber-400/10 text-amber-300 border border-amber-400/30"
                >
                  {flag}
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}

      {(report.key_risks?.length ?? 0) > 0 && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>{t("fund.risks.title")}</CardTitle>
              <CardDescription>{t("fund.risks.desc")}</CardDescription>
            </div>
          </CardHeader>
          <CardBody>
            <ul className="list-disc ps-5 text-sm text-fg-muted space-y-1">
              {report.key_risks!.map((risk, i) => (
                <li key={i}>{risk}</li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}

      {markdown && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>{t("fund.narrative.title")}</CardTitle>
              <CardDescription>{t("fund.narrative.desc")}</CardDescription>
            </div>
          </CardHeader>
          <CardBody>
            <Markdown>{markdown}</Markdown>
          </CardBody>
        </Card>
      )}
    </div>
  );
}
