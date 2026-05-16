import { useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  FileText,
  Fingerprint,
  ListChecks,
  Radio,
  ScanLine,
} from "lucide-react";
import { AppShell } from "../../components/layout/AppShell";
import {
  Card,
  CardBody,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { Skeleton } from "../../components/ui/Skeleton";
import { EmptyState } from "../../components/ui/EmptyState";
import { JSONViewer } from "../../components/ui/JSONViewer";
import { useT } from "../../lib/i18n";
import { cn } from "../../lib/utils";
import {
  useFingerprints,
  useHealth,
  usePrompts,
} from "../../hooks/useDiagnostics";
import { useRlStatus } from "../../hooks/useBacktest";
import type {
  FingerprintRow,
  FingerprintsResponse,
  HealthResponse,
  PromptsResponse,
} from "../../services/api/types";

const WINDOW_OPTIONS: { label: string; days: number }[] = [
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
];

function fmtTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function fmtBool(
  v: boolean | null | undefined,
  t: (k: string) => string
): string {
  if (v === null || v === undefined) return t("diagnostics.unknown");
  return v ? t("diagnostics.yes") : t("diagnostics.no");
}

export function DiagnosticsPage() {
  const t = useT();
  const [windowDays, setWindowDays] = useState<number>(30);

  const health = useHealth(true);
  const prompts = usePrompts();
  const fingerprints = useFingerprints(windowDays);
  const rl = useRlStatus();

  return (
    <AppShell
      title={t("diagnostics.title")}
      subtitle={t("diagnostics.subtitle")}
    >
      <div className="flex flex-col gap-5">
        <HealthCard health={health.data} loading={health.isLoading} />

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <MemoryCard health={health.data} loading={health.isLoading} />
          <PersistenceCard health={health.data} loading={health.isLoading} />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <StreamingCard
            health={health.data}
            rlEnabled={rl.data?.enabled ?? false}
            rlLoaded={rl.data?.loaded ?? false}
            loading={health.isLoading}
          />
          <RlCard rl={rl.data} />
        </div>

        <FingerprintCard
          response={fingerprints.data}
          loading={fingerprints.isLoading}
          windowDays={windowDays}
          onWindowChange={setWindowDays}
        />

        <PromptsCard
          response={prompts.data}
          loading={prompts.isLoading}
        />
      </div>
    </AppShell>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Top-level health card with degraded reasons + raw payload toggle.
// ──────────────────────────────────────────────────────────────────────────
function HealthCard({
  health,
  loading,
}: {
  health: HealthResponse | undefined;
  loading: boolean;
}) {
  const t = useT();
  const [showRaw, setShowRaw] = useState(false);
  const ok = health?.status === "ok" && !health?.diagnostics?.degraded;
  const reasons = health?.diagnostics?.degraded_reasons ?? [];
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-brand-400" aria-hidden />
            {t("diagnostics.health.title")}
          </CardTitle>
          <CardDescription>{t("diagnostics.health.desc")}</CardDescription>
        </div>
        {loading ? (
          <Skeleton className="h-6 w-20" />
        ) : ok ? (
          <Badge tone="up">
            <CheckCircle2 className="h-3 w-3" aria-hidden />
            {t("diagnostics.health.statusOk")}
          </Badge>
        ) : (
          <Badge tone="warning">
            <AlertTriangle className="h-3 w-3" aria-hidden />
            {t("diagnostics.health.statusDegraded")}
          </Badge>
        )}
      </CardHeader>
      <CardBody className="flex flex-col gap-3">
        {loading ? (
          <Skeleton className="h-20 w-full rounded-lg" />
        ) : !health ? (
          <EmptyState
            icon={<AlertTriangle className="h-4 w-4" />}
            title={t("diagnostics.health.unavailable.title")}
            description={t("diagnostics.health.unavailable.desc")}
          />
        ) : (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
              <StatTile
                label={t("diagnostics.health.lastCheck")}
                value={fmtTimestamp(health.timestamp)}
                hint={t("diagnostics.health.refreshHint")}
              />
              <StatTile
                label={t("diagnostics.health.egxTools")}
                value={fmtBool(health.egx_tools, t)}
              />
              <StatTile
                label={t("diagnostics.health.degradedFlag")}
                value={fmtBool(health.diagnostics?.degraded, t)}
              />
              <StatTile
                label={t("diagnostics.health.reasonCount")}
                value={String(reasons.length)}
                hint={reasons.length > 0 ? reasons[0] : undefined}
              />
            </div>
            {reasons.length > 0 && (
              <div className="rounded-lg border border-amber-400/30 bg-amber-400/10 p-3">
                <p className="text-[11px] uppercase tracking-wider text-amber-300 mb-1.5">
                  {t("diagnostics.health.reasonsTitle")}
                </p>
                <ul className="text-xs text-amber-200/90 list-disc list-inside space-y-0.5 num">
                  {reasons.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              </div>
            )}
            <button
              type="button"
              onClick={() => setShowRaw((p) => !p)}
              className="self-start text-[11px] text-fg-muted hover:text-fg underline underline-offset-2"
            >
              {showRaw
                ? t("diagnostics.health.hideRaw")
                : t("diagnostics.health.showRaw")}
            </button>
            {showRaw && (
              <JSONViewer
                data={health}
                rootKey="health"
                className="max-h-96 overflow-auto"
              />
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Memory backend card
// ──────────────────────────────────────────────────────────────────────────
function MemoryCard({
  health,
  loading,
}: {
  health: HealthResponse | undefined;
  loading: boolean;
}) {
  const t = useT();
  const mem = health?.diagnostics?.memory;
  const counts = mem?.chroma_collection_counts ?? null;
  const seeded = mem?.seeded ?? null;
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Database className="h-4 w-4 text-brand-400" aria-hidden />
            {t("diagnostics.memory.title")}
          </CardTitle>
          <CardDescription>{t("diagnostics.memory.desc")}</CardDescription>
        </div>
        {mem && (
          <Badge tone={mem.chroma_persistent ? "up" : "warning"}>
            {mem.backend}
          </Badge>
        )}
      </CardHeader>
      <CardBody className="flex flex-col gap-2.5">
        {loading || !mem ? (
          <Skeleton className="h-32 w-full rounded-lg" />
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 text-xs">
              <KvRow
                label={t("diagnostics.memory.vector")}
                value={mem.vector_store}
              />
              <KvRow
                label={t("diagnostics.memory.persistent")}
                value={fmtBool(Boolean(mem.chroma_persistent), t)}
              />
              <KvRow
                label={t("diagnostics.memory.persistDir")}
                value={mem.chroma_persist_dir ?? "—"}
                mono
              />
              <KvRow
                label={t("diagnostics.memory.minSim")}
                value={(mem.min_similarity ?? 0.3).toFixed(2)}
                mono
              />
              <KvRow
                label={t("diagnostics.memory.docs")}
                value={String(mem.chroma_total_documents ?? "—")}
                mono
              />
            </div>

            {counts && (
              <div>
                <p className="text-[10px] uppercase tracking-wider text-fg-muted mb-1.5">
                  {t("diagnostics.memory.collections")}
                </p>
                <div className="grid grid-cols-1 gap-1">
                  {Object.entries(counts).map(([name, count]) => {
                    const isSeeded = seeded?.[name];
                    return (
                      <div
                        key={name}
                        className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-md bg-ink-800/60 border border-line"
                      >
                        <span className="text-xs num text-fg">{name}</span>
                        <div className="flex items-center gap-2">
                          <span className="num text-xs text-fg-muted">
                            {count}
                          </span>
                          <Badge tone={isSeeded ? "up" : "neutral"}>
                            {isSeeded
                              ? t("diagnostics.memory.seeded")
                              : t("diagnostics.memory.unseeded")}
                          </Badge>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Persistence (Postgres) card
// ──────────────────────────────────────────────────────────────────────────
function PersistenceCard({
  health,
  loading,
}: {
  health: HealthResponse | undefined;
  loading: boolean;
}) {
  const t = useT();
  const block = health?.diagnostics?.postgres;
  const reachable = block?.reachable;
  const lag = block?.audit_write_lag_seconds;
  const runs = block?.backtest_runs_count;

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Database className="h-4 w-4 text-brand-400" aria-hidden />
            {t("diagnostics.persistence.title")}
          </CardTitle>
          <CardDescription>
            {t("diagnostics.persistence.desc")}
          </CardDescription>
        </div>
        {block && (
          <Badge
            tone={
              reachable === true
                ? "up"
                : reachable === false
                  ? "warning"
                  : "neutral"
            }
          >
            {reachable === true
              ? t("diagnostics.persistence.online")
              : reachable === false
                ? t("diagnostics.persistence.offline")
                : t("diagnostics.persistence.unknown")}
          </Badge>
        )}
      </CardHeader>
      <CardBody>
        {loading || !block ? (
          <Skeleton className="h-24 w-full rounded-lg" />
        ) : (
          <div className="grid grid-cols-2 gap-3 text-xs">
            <KvRow
              label={t("diagnostics.persistence.configured")}
              value={fmtBool(Boolean(block.configured), t)}
            />
            <KvRow
              label={t("diagnostics.persistence.lag")}
              value={
                typeof lag === "number"
                  ? `${lag.toFixed(2)} s`
                  : t("diagnostics.unknown")
              }
              mono
            />
            <KvRow
              label={t("diagnostics.persistence.runs")}
              value={
                typeof runs === "number" ? String(runs) : t("diagnostics.unknown")
              }
              mono
            />
            <KvRow
              label={t("diagnostics.persistence.purpose")}
              value={String(block.purpose ?? "")}
            />
          </div>
        )}
      </CardBody>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Streaming (Redis + RL flag) card
// ──────────────────────────────────────────────────────────────────────────
function StreamingCard({
  health,
  rlEnabled,
  rlLoaded,
  loading,
}: {
  health: HealthResponse | undefined;
  rlEnabled: boolean;
  rlLoaded: boolean;
  loading: boolean;
}) {
  const t = useT();
  const block = health?.diagnostics?.redis;
  const reachable = block?.reachable;
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Radio className="h-4 w-4 text-brand-400" aria-hidden />
            {t("diagnostics.streaming.title")}
          </CardTitle>
          <CardDescription>
            {t("diagnostics.streaming.desc")}
          </CardDescription>
        </div>
        {block && (
          <Badge
            tone={
              reachable === true
                ? "up"
                : reachable === false
                  ? "warning"
                  : "neutral"
            }
          >
            {reachable === true
              ? t("diagnostics.streaming.online")
              : reachable === false
                ? t("diagnostics.streaming.offline")
                : t("diagnostics.streaming.unknown")}
          </Badge>
        )}
      </CardHeader>
      <CardBody>
        {loading || !block ? (
          <Skeleton className="h-24 w-full rounded-lg" />
        ) : (
          <div className="grid grid-cols-2 gap-3 text-xs">
            <KvRow
              label={t("diagnostics.streaming.configured")}
              value={fmtBool(Boolean(block.configured), t)}
            />
            <KvRow
              label={t("diagnostics.streaming.package")}
              value={fmtBool(Boolean(block.package_available), t)}
            />
            <KvRow
              label={t("diagnostics.streaming.rlEnabled")}
              value={fmtBool(rlEnabled, t)}
            />
            <KvRow
              label={t("diagnostics.streaming.rlLoaded")}
              value={fmtBool(rlLoaded, t)}
            />
          </div>
        )}
      </CardBody>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// RL meta-policy card
// ──────────────────────────────────────────────────────────────────────────
function RlCard({
  rl,
}: {
  rl:
    | {
        enabled: boolean;
        loaded: boolean;
        model_path: string | null;
        feature_version: string | null;
        model_fingerprint: Record<string, unknown> | null;
      }
    | undefined;
}) {
  const t = useT();
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <ScanLine className="h-4 w-4 text-brand-400" aria-hidden />
            {t("diagnostics.rl.title")}
          </CardTitle>
          <CardDescription>{t("diagnostics.rl.desc")}</CardDescription>
        </div>
        <Badge tone={rl?.enabled ? "brand" : "neutral"}>
          {rl?.enabled
            ? t("diagnostics.rl.on")
            : t("diagnostics.rl.off")}
        </Badge>
      </CardHeader>
      <CardBody className="flex flex-col gap-2.5">
        <div className="grid grid-cols-2 gap-3 text-xs">
          <KvRow
            label={t("diagnostics.rl.loaded")}
            value={fmtBool(rl?.loaded ?? null, t)}
          />
          <KvRow
            label={t("diagnostics.rl.feature")}
            value={rl?.feature_version ?? "—"}
            mono
          />
          <KvRow
            label={t("diagnostics.rl.path")}
            value={rl?.model_path ?? "—"}
            mono
          />
        </div>
        {rl?.model_fingerprint ? (
          <JSONViewer
            data={rl.model_fingerprint}
            rootKey="model_fingerprint"
            className="max-h-48 overflow-auto"
          />
        ) : (
          <p className="text-[11px] text-fg-subtle">
            {t("diagnostics.rl.noFingerprint")}
          </p>
        )}
      </CardBody>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Fingerprint drift card
// ──────────────────────────────────────────────────────────────────────────
function FingerprintCard({
  response,
  loading,
  windowDays,
  onWindowChange,
}: {
  response: FingerprintsResponse | undefined;
  loading: boolean;
  windowDays: number;
  onWindowChange: (n: number) => void;
}) {
  const t = useT();
  const rows = response?.fingerprints ?? [];
  const daily = useMemo(
    () => response?.daily_counts ?? [],
    [response]
  );
  const max = useMemo(
    () => daily.reduce((acc, d) => Math.max(acc, d.events), 0),
    [daily]
  );

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Fingerprint className="h-4 w-4 text-brand-400" aria-hidden />
            {t("diagnostics.fp.title")}
          </CardTitle>
          <CardDescription>{t("diagnostics.fp.desc")}</CardDescription>
        </div>
        <div className="flex items-center gap-1.5">
          {WINDOW_OPTIONS.map((opt) => (
            <button
              key={opt.days}
              type="button"
              onClick={() => onWindowChange(opt.days)}
              aria-pressed={windowDays === opt.days}
              className={cn(
                "h-7 px-2.5 rounded-md text-[11px] font-medium border",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50",
                windowDays === opt.days
                  ? "bg-brand-500/15 text-brand-400 border-brand-500/30"
                  : "bg-ink-800 text-fg-muted border-line hover:text-fg"
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardBody className="flex flex-col gap-3">
        {loading ? (
          <Skeleton className="h-32 w-full rounded-lg" />
        ) : !response || response.source === "none" ? (
          <EmptyState
            icon={<Fingerprint className="h-4 w-4" />}
            title={t("diagnostics.fp.empty.title")}
            description={
              response?.reason === "postgres_unavailable"
                ? t("diagnostics.fp.empty.descNoPg")
                : t("diagnostics.fp.empty.desc")
            }
          />
        ) : (
          <>
            <div className="grid grid-cols-3 gap-3 text-xs">
              <StatTile
                label={t("diagnostics.fp.window")}
                value={`${windowDays}d`}
              />
              <StatTile
                label={t("diagnostics.fp.distinct")}
                value={String(response.distinct_fingerprints)}
              />
              <StatTile
                label={t("diagnostics.fp.events")}
                value={String(response.total_events)}
              />
            </div>

            {daily.length > 0 && (
              <div>
                <p className="text-[10px] uppercase tracking-wider text-fg-muted mb-1">
                  {t("diagnostics.fp.sparkline")}
                </p>
                <Sparkline daily={daily} max={max} />
              </div>
            )}

            {rows.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-[10px] uppercase tracking-wider text-fg-muted border-b border-line">
                      <th className="px-3 py-2">
                        {t("diagnostics.fp.col.fp")}
                      </th>
                      <th className="px-3 py-2">
                        {t("diagnostics.fp.col.firstSeen")}
                      </th>
                      <th className="px-3 py-2">
                        {t("diagnostics.fp.col.lastSeen")}
                      </th>
                      <th className="px-3 py-2 num text-end">
                        {t("diagnostics.fp.col.count")}
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {rows.map((r) => (
                      <FpRow key={r.fingerprint_text} row={r} />
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState
                icon={<Fingerprint className="h-4 w-4" />}
                title={t("diagnostics.fp.noData.title")}
                description={t("diagnostics.fp.noData.desc")}
              />
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}

function FpRow({ row }: { row: FingerprintRow }) {
  const t = useT();
  const fp = row.fingerprint ?? {};
  const sha = String(
    (fp as Record<string, unknown>)["weights_sha256_16"] ??
      (fp as Record<string, unknown>)["model"] ??
      (fp as Record<string, unknown>)["model_name"] ??
      ""
  );
  const feat =
    (fp as Record<string, unknown>)["feature_version"] ??
    (fp as Record<string, unknown>)["temperature"] ??
    null;
  return (
    <tr className="align-top">
      <td className="px-3 py-2">
        <div className="flex flex-col gap-0.5">
          <span className="num text-fg break-all">
            {sha || row.fingerprint_text.slice(0, 32)}
          </span>
          {feat !== null && (
            <span className="text-[10px] num text-fg-subtle">
              {t("diagnostics.fp.col.feature")}: {String(feat)}
            </span>
          )}
        </div>
      </td>
      <td className="px-3 py-2 num text-fg-muted">
        {fmtTimestamp(row.first_seen)}
      </td>
      <td className="px-3 py-2 num text-fg-muted">
        {fmtTimestamp(row.last_seen)}
      </td>
      <td className="px-3 py-2 num text-end text-fg">{row.event_count}</td>
    </tr>
  );
}

function Sparkline({
  daily,
  max,
}: {
  daily: { day: string | null; events: number; distinct: number }[];
  max: number;
}) {
  if (max === 0) return null;
  return (
    <div
      className="flex items-end gap-[2px] h-12"
      role="img"
      aria-label="Daily fingerprint events"
    >
      {daily.map((d, i) => {
        const h = Math.max(2, Math.round((d.events / max) * 48));
        const distinctTone = d.distinct > 1 ? "bg-amber-400/60" : "bg-brand-500/60";
        return (
          <div
            key={`${d.day}-${i}`}
            className={cn("flex-1 rounded-sm", distinctTone)}
            style={{ height: `${h}px` }}
            title={`${d.day ?? ""}: ${d.events} events · ${d.distinct} distinct`}
          />
        );
      })}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Prompt registry card
// ──────────────────────────────────────────────────────────────────────────
function PromptsCard({
  response,
  loading,
}: {
  response: PromptsResponse | undefined;
  loading: boolean;
}) {
  const t = useT();
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-brand-400" aria-hidden />
            {t("diagnostics.prompts.title")}
          </CardTitle>
          <CardDescription>
            {t("diagnostics.prompts.desc")}
          </CardDescription>
        </div>
        {response && (
          <Badge tone="neutral">
            <ListChecks className="h-3 w-3" aria-hidden />
            {response.total} {t("diagnostics.prompts.prompts")}
          </Badge>
        )}
      </CardHeader>
      <CardBody>
        {loading ? (
          <Skeleton className="h-32 w-full rounded-lg" />
        ) : !response || response.source === "none" ? (
          <EmptyState
            icon={<FileText className="h-4 w-4" />}
            title={t("diagnostics.prompts.empty.title")}
            description={t("diagnostics.prompts.empty.desc")}
          />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {response.prompts.map((p) => (
              <div
                key={p.id}
                className="rounded-md border border-line bg-ink-800/40 px-3 py-2.5"
              >
                <div className="flex items-center justify-between gap-2 mb-1">
                  <span className="num text-[11px] text-brand-400">
                    {p.id}
                  </span>
                  <span className="num text-[10px] text-fg-subtle">
                    L{p.line}
                  </span>
                </div>
                <p className="text-xs text-fg-muted leading-snug">
                  {p.title}
                </p>
              </div>
            ))}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Atoms
// ──────────────────────────────────────────────────────────────────────────
function StatTile({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-md border border-line bg-ink-800/40 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wider text-fg-muted">
        {label}
      </p>
      <p className="text-sm font-medium text-fg num mt-0.5">{value}</p>
      {hint && (
        <p className="text-[10px] text-fg-subtle num mt-0.5">{hint}</p>
      )}
    </div>
  );
}

function KvRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wider text-fg-subtle">
        {label}
      </span>
      <span
        className={cn(
          "text-fg break-words",
          mono ? "num text-xs" : "text-sm"
        )}
      >
        {value}
      </span>
    </div>
  );
}
