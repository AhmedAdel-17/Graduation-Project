import { useState } from "react";
import { StockSelector } from "../../components/ui/StockSelector";
import { Select } from "../../components/ui/Select";
import { Input } from "../../components/ui/Input";
import { Button } from "../../components/ui/Button";
import { Play } from "lucide-react";
import type { RunBacktestRequest } from "../../services/api/types";

type DurationKey = "2w" | "1m" | "3m" | "6m" | "1y";

const DURATIONS: {
  key: DurationKey;
  label: string;
  days: number;
  horizon: "short" | "long";
  interval: number;
}[] = [
  { key: "2w", label: "2 weeks (short-term)", days: 14, horizon: "short", interval: 2 },
  { key: "1m", label: "1 month (short-term)", days: 30, horizon: "short", interval: 3 },
  { key: "3m", label: "3 months (short-term)", days: 90, horizon: "short", interval: 5 },
  { key: "6m", label: "6 months (long-term)", days: 180, horizon: "long", interval: 7 },
  { key: "1y", label: "1 year (long-term)", days: 365, horizon: "long", interval: 10 },
];

function daysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export interface BacktestFormValues extends RunBacktestRequest {
  duration: DurationKey;
}

export interface BacktestFormProps {
  ticker: string;
  onTickerChange: (ticker: string) => void;
  initialCapital: number;
  onInitialCapitalChange: (v: number) => void;
  onSubmit: (values: BacktestFormValues) => void | Promise<void>;
  submitting?: boolean;
  fullAnalysts?: boolean;
}

export function BacktestForm({
  ticker,
  onTickerChange,
  initialCapital,
  onInitialCapitalChange,
  onSubmit,
  submitting,
  fullAnalysts = false,
}: BacktestFormProps) {
  const [duration, setDuration] = useState<DurationKey>("3m");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const cfg = DURATIONS.find((d) => d.key === duration)!;
    const values: BacktestFormValues = {
      ticker,
      duration,
      start_date: daysAgo(cfg.days),
      end_date: today(),
      interval: cfg.interval,
      initial_capital: initialCapital,
      selected_analysts: fullAnalysts
        ? ["market", "fundamentals", "news", "social"]
        : ["market", "fundamentals"],
    };
    onSubmit(values);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <StockSelector label="Stock" value={ticker} onChange={onTickerChange} />

      <Select
        label="Investment duration"
        value={duration}
        onChange={(e) => setDuration(e.target.value as DurationKey)}
      >
        {DURATIONS.map((d) => (
          <option key={d.key} value={d.key}>
            {d.label}
          </option>
        ))}
      </Select>

      <Input
        label="Initial capital"
        type="number"
        min={10_000}
        step={10_000}
        value={initialCapital}
        onChange={(e) => onInitialCapitalChange(Number(e.target.value) || 0)}
        rightAddon="EGP"
        hint="Minimum 10,000 EGP"
      />

      <Button
        type="submit"
        size="lg"
        className="w-full"
        leftIcon={<Play className="h-4 w-4" />}
        loading={submitting}
        disabled={!ticker || initialCapital < 10_000}
      >
        {submitting ? "Running backtest…" : "Run Backtest"}
      </Button>
    </form>
  );
}
