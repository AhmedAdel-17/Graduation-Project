import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { ArrowLeft, Brain, Cpu, Play, Zap } from "lucide-react";
import { AppShell } from "../../components/layout/AppShell";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { Badge } from "../../components/ui/Badge";
import { StockSelector } from "../../components/ui/StockSelector";
import { useAppStore } from "../../store/appStore";
import {
  useBacktests,
  useRlStatus,
  useRunBacktest,
  useRunBtBenchmark,
} from "../../hooks/useBacktest";
import { useT } from "../../lib/i18n";
import { cn } from "../../lib/utils";

const ALL_ANALYSTS = ["market", "fundamentals", "news", "social"] as const;
type AnalystKey = (typeof ALL_ANALYSTS)[number];

type PresetKey = "2w" | "1m" | "3m" | "6m" | "1y";

const PRESETS: { key: PresetKey; days: number; interval: number }[] = [
  { key: "2w", days: 14, interval: 2 },
  { key: "1m", days: 30, interval: 3 },
  { key: "3m", days: 90, interval: 5 },
  { key: "6m", days: 180, interval: 7 },
  { key: "1y", days: 365, interval: 10 },
];

function daysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

export function NewBacktestPage() {
  const t = useT();
  const ticker = useAppStore((s) => s.selectedTicker);
  const setTicker = useAppStore((s) => s.setSelectedTicker);
  const initialCapital = useAppStore((s) => s.initialCapital);
  const setInitialCapital = useAppStore((s) => s.setInitialCapital);

  const [preset, setPreset] = useState<PresetKey>("3m");
  const [startDate, setStartDate] = useState(daysAgo(90));
  const [endDate, setEndDate] = useState(todayISO());
  const [intervalDays, setIntervalDays] = useState(5);
  const [analysts, setAnalysts] = useState<AnalystKey[]>([
    "market",
    "fundamentals",
  ]);

  const runLLM = useRunBacktest();
  const runBT = useRunBtBenchmark();
  const rlStatus = useRlStatus();
  const { data: list, refetch: refetchList } = useBacktests(
    runLLM.isPending || runBT.isPending
  );

  // Snapshot the latest session id at submit time so we can navigate to the
  // new detail page as soon as the file appears. State (not refs) so the
  // hooks-plugin lets us read these during render.
  const [expectingEngine, setExpectingEngine] = useState<"llm" | "bt" | null>(
    null
  );
  const [snapshotAtSubmit, setSnapshotAtSubmit] = useState<string | null>(null);

  const latestId = useMemo(() => {
    if (!list?.sessions || !expectingEngine) return null;
    const match = list.sessions.find(
      (s) =>
        s.ticker === ticker &&
        (expectingEngine === "bt"
          ? s.engine === "classical_technical"
          : s.engine === "llm_multi_agent")
    );
    return match?.session_id ?? null;
  }, [list, ticker, expectingEngine]);

  useEffect(() => {
    if (!expectingEngine) return;
    if (latestId && latestId !== snapshotAtSubmit) {
      toast.success(t("backtest.new.toast.ready"));
      // Hard nav so the detail page mounts with a fresh hook tree. Calling
      // assign() is idempotent if the effect happens to fire twice under
      // StrictMode — same URL, same outcome.
      window.location.assign(`/backtest/${encodeURIComponent(latestId)}`);
    }
  }, [latestId, expectingEngine, snapshotAtSubmit, t]);

  function applyPreset(key: PresetKey) {
    setPreset(key);
    const cfg = PRESETS.find((p) => p.key === key)!;
    setStartDate(daysAgo(cfg.days));
    setEndDate(todayISO());
    setIntervalDays(cfg.interval);
  }

  function toggleAnalyst(key: AnalystKey) {
    setAnalysts((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
  }

  async function submitLLM() {
    if (!ticker) return;
    setSnapshotAtSubmit(
      list?.sessions.find(
        (s) => s.ticker === ticker && s.engine === "llm_multi_agent"
      )?.session_id ?? null
    );
    setExpectingEngine("llm");
    try {
      await runLLM.mutateAsync({
        ticker,
        start_date: startDate,
        end_date: endDate,
        interval: intervalDays,
        initial_capital: initialCapital,
        selected_analysts: analysts,
      });
      toast.success(t("backtest.new.toast.submitted"));
      refetchList();
    } catch (e) {
      setExpectingEngine(null);
      toast.error(
        e instanceof Error ? e.message : t("backtest.new.toast.failed")
      );
    }
  }

  async function submitBT() {
    if (!ticker) return;
    setSnapshotAtSubmit(
      list?.sessions.find(
        (s) => s.ticker === ticker && s.engine === "classical_technical"
      )?.session_id ?? null
    );
    setExpectingEngine("bt");
    try {
      await runBT.mutateAsync({
        ticker,
        start_date: startDate,
        end_date: endDate,
        initial_capital: initialCapital,
      });
      toast.success(t("backtest.new.toast.submitted"));
      refetchList();
    } catch (e) {
      setExpectingEngine(null);
      toast.error(
        e instanceof Error ? e.message : t("backtest.new.toast.failed")
      );
    }
  }

  const submitting = runLLM.isPending || runBT.isPending;
  const formValid =
    !!ticker && analysts.length > 0 && initialCapital >= 10_000 && !!startDate && !!endDate;
  const rlOn = Boolean(rlStatus.data?.enabled);

  return (
    <AppShell
      title={t("backtest.new.title")}
      subtitle={t("backtest.new.subtitle")}
    >
      <div className="flex flex-col gap-5">
        <div className="flex items-center gap-2">
          <Link
            to="/backtest"
            className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md text-xs text-fg-muted hover:text-fg border border-line hover:border-line-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
          >
            <ArrowLeft className="h-3 w-3" aria-hidden />
            {t("backtest.new.back")}
          </Link>
          {rlStatus.data && (
            <Badge tone={rlOn ? "brand" : "neutral"}>
              <Brain className="h-2.5 w-2.5" aria-hidden />
              RL{" "}
              {rlOn
                ? t("backtest.new.rl.on")
                : t("backtest.new.rl.off")}
            </Badge>
          )}
        </div>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>{t("backtest.new.config.title")}</CardTitle>
              <CardDescription>
                {t("backtest.new.config.desc")}
              </CardDescription>
            </div>
            {submitting && (
              <Badge tone="accent">{t("backtest.new.config.running")}</Badge>
            )}
          </CardHeader>
          <CardBody className="flex flex-col gap-5">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <StockSelector
                label={t("backtest.new.field.ticker")}
                value={ticker}
                onChange={setTicker}
                disabled={submitting}
              />
              <Input
                label={t("backtest.new.field.capital")}
                type="number"
                min={10_000}
                step={10_000}
                value={initialCapital}
                onChange={(e) =>
                  setInitialCapital(Number(e.target.value) || 0)
                }
                rightAddon="EGP"
                hint={t("backtest.new.field.capitalHint")}
                disabled={submitting}
              />
            </div>

            <div>
              <p className="text-[11px] uppercase tracking-wider text-fg-muted mb-1.5">
                {t("backtest.new.field.preset")}
              </p>
              <div className="flex flex-wrap gap-1.5">
                {PRESETS.map(({ key }) => {
                  const active = preset === key;
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={() => applyPreset(key)}
                      disabled={submitting}
                      aria-pressed={active}
                      className={cn(
                        "h-7 px-2.5 rounded-md text-[11px] font-medium border",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50",
                        "disabled:opacity-50 disabled:cursor-not-allowed",
                        active
                          ? "bg-brand-500/15 text-brand-400 border-brand-500/30"
                          : "bg-ink-800 text-fg-muted border-line hover:text-fg"
                      )}
                    >
                      {key}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <Input
                label={t("backtest.new.field.start")}
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                disabled={submitting}
              />
              <Input
                label={t("backtest.new.field.end")}
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                disabled={submitting}
              />
              <Select
                label={t("backtest.new.field.interval")}
                value={intervalDays}
                onChange={(e) => setIntervalDays(Number(e.target.value))}
                disabled={submitting}
              >
                {[2, 3, 5, 7, 10, 14].map((d) => (
                  <option key={d} value={d}>
                    {t("backtest.new.field.intervalUnit").replace("{d}", String(d))}
                  </option>
                ))}
              </Select>
            </div>

            <div>
              <p className="text-[11px] uppercase tracking-wider text-fg-muted mb-1.5">
                {t("backtest.new.field.analysts")}
              </p>
              <div className="flex flex-wrap gap-1.5">
                {ALL_ANALYSTS.map((key) => {
                  const on = analysts.includes(key);
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={() => toggleAnalyst(key)}
                      disabled={submitting}
                      aria-pressed={on}
                      className={cn(
                        "h-8 px-3 rounded-md text-xs border transition-colors",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50",
                        "disabled:opacity-50 disabled:cursor-not-allowed",
                        on
                          ? "bg-brand-500/15 text-brand-400 border-brand-500/30"
                          : "bg-ink-800 text-fg-muted border-line hover:text-fg"
                      )}
                    >
                      {t(`run.analyst.${key}`)}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="flex flex-wrap gap-2 pt-1">
              <Button
                size="lg"
                leftIcon={<Play className="h-4 w-4" aria-hidden />}
                loading={runLLM.isPending}
                disabled={!formValid || submitting}
                onClick={submitLLM}
              >
                {t("backtest.new.cta.llm")}
              </Button>
              <Button
                size="lg"
                variant="outline"
                leftIcon={<Zap className="h-4 w-4" aria-hidden />}
                loading={runBT.isPending}
                disabled={!ticker || submitting}
                onClick={submitBT}
              >
                {t("backtest.new.cta.bt")}
              </Button>
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                <Cpu className="h-4 w-4 text-fg-muted" aria-hidden />
                {t("backtest.new.notes.title")}
              </CardTitle>
            </div>
          </CardHeader>
          <CardBody className="text-xs text-fg-muted leading-relaxed space-y-1.5">
            <p>{t("backtest.new.notes.body")}</p>
            <p className="text-fg-subtle">
              {t("backtest.new.notes.endpoint")}
            </p>
          </CardBody>
        </Card>
      </div>
    </AppShell>
  );
}
