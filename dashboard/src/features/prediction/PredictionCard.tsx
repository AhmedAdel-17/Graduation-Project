import type { PredictionResult } from "../../services/api/types";
import { Card, CardBody, CardHeader, CardTitle, CardDescription } from "../../components/ui/Card";
import { SignalPill, ConfidencePill } from "./SignalBadge";
import { MetricsCard } from "../../components/ui/MetricsCard";
import { formatCurrency, formatPercent, formatNumber } from "../../lib/utils";

function num(v: unknown): number | undefined {
  if (v === null || v === undefined) return undefined;
  const n = typeof v === "number" ? v : parseFloat(String(v));
  return Number.isFinite(n) ? n : undefined;
}

export function PredictionCard({ result }: { result: PredictionResult }) {
  const rec = result.recommendation ?? {};
  const price = result.price ?? {};
  const ind = result.indicators ?? {};

  const current = num(price.current);
  const target = num(rec.target_price);
  const stop = num(rec.stop_loss);
  const upside =
    current !== undefined && target !== undefined && current > 0
      ? ((target - current) / current) * 100
      : undefined;

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <span className="num text-base">{result.ticker}</span>
            <SignalPill signal={rec.signal} />
            <ConfidencePill level={rec.confidence} />
          </CardTitle>
          <CardDescription>
            {rec.risk ? `Risk profile: ${rec.risk}` : "AI-generated signal"}
          </CardDescription>
        </div>
      </CardHeader>
      <CardBody>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <MetricsCard
            label="Current Price"
            value={formatCurrency(current)}
            hint={price.source ? `Source: ${price.source}` : undefined}
          />
          <MetricsCard
            label="Daily Change"
            value={formatPercent(num(price.daily_change))}
            trend={
              num(price.daily_change) !== undefined
                ? (num(price.daily_change) as number) >= 0
                  ? "up"
                  : "down"
                : undefined
            }
            tone={
              num(price.daily_change) !== undefined
                ? (num(price.daily_change) as number) >= 0
                  ? "up"
                  : "down"
                : "default"
            }
          />
          <MetricsCard
            label="Target Price"
            value={formatCurrency(target)}
            hint={
              upside !== undefined ? `${formatPercent(upside)} upside` : undefined
            }
            tone="brand"
          />
          <MetricsCard
            label="Stop Loss"
            value={formatCurrency(stop)}
            hint="Risk cap"
            tone="down"
          />
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
          <MetricsCard
            label="Weekly Change"
            value={formatPercent(num(price.weekly_change))}
            tone={
              num(price.weekly_change) !== undefined
                ? (num(price.weekly_change) as number) >= 0
                  ? "up"
                  : "down"
                : "default"
            }
          />
          <MetricsCard
            label="RSI (14)"
            value={formatNumber(num(ind.rsi))}
            hint={
              num(ind.rsi) !== undefined
                ? (num(ind.rsi) as number) > 70
                  ? "Overbought"
                  : (num(ind.rsi) as number) < 30
                  ? "Oversold"
                  : "Neutral zone"
                : undefined
            }
          />
          <MetricsCard
            label="SMA 5 / 10"
            value={`${formatNumber(num(ind.sma_5))} / ${formatNumber(num(ind.sma_10))}`}
            hint={ind.trend ? `Trend: ${ind.trend}` : undefined}
          />
          <MetricsCard
            label="Trend"
            value={ind.trend || "—"}
            hint="Short-term"
          />
        </div>
      </CardBody>
    </Card>
  );
}
