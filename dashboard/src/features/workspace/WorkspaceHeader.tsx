import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Sparkles } from "lucide-react";
import { Card, CardBody } from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { StockSelector } from "../../components/ui/StockSelector";
import { Button } from "../../components/ui/Button";
import { useStockData } from "../../hooks/useStockData";
import { useT } from "../../lib/i18n";
import {
  formatCurrency,
  formatPercent,
  formatCompact,
} from "../../lib/utils";
import {
  getTickerMeta,
  sectorLabelKey,
  type LiquidityTier,
} from "../../data/egx-tickers";
import type { StockBar } from "../../services/api/types";

function lastClose(bars: StockBar[] | undefined): number | null {
  if (!bars || bars.length === 0) return null;
  const v = bars[bars.length - 1].close;
  return Number.isFinite(v) ? Number(v) : null;
}

function changePct(
  bars: StockBar[] | undefined,
  lookback: number
): number | null {
  if (!bars || bars.length < lookback + 1) return null;
  const last = bars[bars.length - 1].close;
  const prior = bars[bars.length - 1 - lookback].close;
  if (!Number.isFinite(last) || !Number.isFinite(prior) || prior === 0) {
    return null;
  }
  return ((Number(last) - Number(prior)) / Number(prior)) * 100;
}

function avgVolume(bars: StockBar[] | undefined, n = 30): number | null {
  if (!bars || bars.length === 0) return null;
  const slice = bars.slice(-n);
  const valid = slice.filter((b) => Number.isFinite(b.volume));
  if (valid.length === 0) return null;
  return valid.reduce((s, b) => s + Number(b.volume), 0) / valid.length;
}

const liquidityToneByTier: Record<LiquidityTier, "brand" | "accent" | "warning"> = {
  MEGA: "brand",
  MID: "accent",
  SMALL: "warning",
};

export function WorkspaceHeader({
  ticker,
  onTickerChange,
}: {
  ticker: string;
  onTickerChange: (next: string) => void;
}) {
  const t = useT();
  const meta = useMemo(() => getTickerMeta(ticker), [ticker]);
  const { data: bars } = useStockData(ticker, { days: 90 });

  const price = lastClose(bars);
  const d1 = changePct(bars, 1);
  const d5 = changePct(bars, 5);
  const d20 = changePct(bars, 20);
  const adv = avgVolume(bars, 30);

  // Distance from the EGX daily circuit-breaker (±10% of prior close).
  // Surface only when we have a yesterday close to anchor on.
  const limitProximity =
    bars && bars.length >= 2 && price !== null
      ? (() => {
          const prevClose = Number(bars[bars.length - 2].close);
          if (!Number.isFinite(prevClose) || prevClose === 0) return null;
          const move = ((price - prevClose) / prevClose) * 100;
          return move; // signed percent vs prior close
        })()
      : null;

  return (
    <Card>
      <CardBody className="pt-5">
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex flex-col gap-1.5 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <h1 className="text-xl font-semibold tracking-tight text-fg num">
                  {ticker}
                </h1>
                {meta && (
                  <Badge tone="neutral">{t(sectorLabelKey(meta.sector))}</Badge>
                )}
                {meta && (
                  <Badge tone={liquidityToneByTier[meta.liquidity]}>
                    {meta.liquidity}
                  </Badge>
                )}
              </div>
              <p className="text-xs text-fg-muted">{t("workspace.header.desc")}</p>
            </div>

            <div className="flex items-center gap-2">
              <div className="w-56">
                <StockSelector value={ticker} onChange={onTickerChange} />
              </div>
              <Link to={`/run?ticker=${encodeURIComponent(ticker)}`}>
                <Button
                  size="sm"
                  leftIcon={<Sparkles className="h-3.5 w-3.5" aria-hidden />}
                >
                  {t("workspace.header.runCta")}
                </Button>
              </Link>
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-5 gap-2.5">
            <KpiTile
              label={t("workspace.kpi.price")}
              value={price !== null ? formatCurrency(price, "EGP", 2) : "—"}
            />
            <KpiTile
              label={t("workspace.kpi.1d")}
              value={formatPercent(d1, 2)}
              tone={d1 === null ? "default" : d1 >= 0 ? "up" : "down"}
            />
            <KpiTile
              label={t("workspace.kpi.5d")}
              value={formatPercent(d5, 2)}
              tone={d5 === null ? "default" : d5 >= 0 ? "up" : "down"}
            />
            <KpiTile
              label={t("workspace.kpi.20d")}
              value={formatPercent(d20, 2)}
              tone={d20 === null ? "default" : d20 >= 0 ? "up" : "down"}
            />
            <KpiTile
              label={t("workspace.kpi.adv")}
              value={adv !== null ? formatCompact(adv) : "—"}
              hint={t("workspace.kpi.adv.hint")}
            />
          </div>

          <CircuitBreakerRibbon move={limitProximity} />
        </div>
      </CardBody>
    </Card>
  );
}

function KpiTile({
  label,
  value,
  tone = "default",
  hint,
}: {
  label: string;
  value: string;
  tone?: "default" | "up" | "down";
  hint?: string;
}) {
  const toneClass =
    tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-fg";
  return (
    <div className="surface rounded-lg px-3 py-2 flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wider text-fg-muted">
        {label}
      </span>
      <span className={"num text-sm font-semibold " + toneClass}>{value}</span>
      {hint && <span className="text-[10px] text-fg-subtle">{hint}</span>}
    </div>
  );
}

function CircuitBreakerRibbon({ move }: { move: number | null }) {
  const t = useT();
  if (move === null) return null;
  const abs = Math.abs(move);
  // EGX rulebook: ±10% triggers a halt. Warn from 7% onward.
  const status: "safe" | "warning" | "halt" =
    abs >= 9.5 ? "halt" : abs >= 7 ? "warning" : "safe";
  if (status === "safe") return null;

  const toneCls =
    status === "halt"
      ? "border-down/30 bg-down/10 text-down"
      : "border-amber-400/30 bg-amber-400/10 text-amber-300";

  return (
    <div
      role="status"
      className={
        "flex items-center gap-2 px-3 py-2 rounded-lg border text-xs " + toneCls
      }
    >
      <span className="font-semibold uppercase tracking-wider">
        {t(`workspace.cb.${status}`)}
      </span>
      <span className="num">
        {move >= 0 ? "+" : ""}
        {move.toFixed(2)}% {t("workspace.cb.vsPrev")}
      </span>
      <span className="text-fg-subtle ms-auto">
        {t("workspace.cb.limit")} ±10%
      </span>
    </div>
  );
}
