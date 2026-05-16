import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  AlertTriangle,
  Brain,
  Cpu,
  Globe,
  Languages,
  Lock,
  RotateCcw,
  Save,
  Server,
  Shield,
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
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { Skeleton } from "../../components/ui/Skeleton";
import { JSONViewer } from "../../components/ui/JSONViewer";
import { useConfig, useUpdateConfig } from "../../hooks/useDiagnostics";
import { useRlStatus } from "../../hooks/useBacktest";
import { useT, useLocale, setLocale, type Locale } from "../../lib/i18n";
import { cn } from "../../lib/utils";

interface FormState {
  backend_url: string;
  deep_think_model: string;
  quick_think_model: string;
  target_market: string;
}

function buildInitial(cfg: Record<string, unknown> | undefined): FormState {
  return {
    backend_url: String(cfg?.["backend_url"] ?? ""),
    deep_think_model: String(cfg?.["deep_think_llm"] ?? cfg?.["deep_think_model"] ?? ""),
    quick_think_model: String(cfg?.["quick_think_llm"] ?? cfg?.["quick_think_model"] ?? ""),
    target_market: String(cfg?.["target_market"] ?? ""),
  };
}

export function SettingsPage() {
  const t = useT();
  const locale = useLocale();
  const { data, isLoading, refetch } = useConfig();
  const update = useUpdateConfig();
  const rl = useRlStatus();

  const cfg = data?.config;
  const baseline = useMemo(() => buildInitial(cfg), [cfg]);
  const [form, setForm] = useState<FormState>(baseline);
  // Sync from server whenever the underlying config changes.
  useEffect(() => {
    setForm(baseline);
  }, [baseline]);

  const dirty = useMemo(
    () => (Object.keys(form) as (keyof FormState)[]).some((k) => form[k] !== baseline[k]),
    [form, baseline]
  );

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const reset = () => setForm(baseline);

  const submit = async () => {
    const changes: Record<string, unknown> = {};
    if (form.backend_url !== baseline.backend_url) {
      changes.backend_url = form.backend_url || undefined;
    }
    if (form.deep_think_model !== baseline.deep_think_model) {
      changes.deep_think_model = form.deep_think_model;
    }
    if (form.quick_think_model !== baseline.quick_think_model) {
      changes.quick_think_model = form.quick_think_model;
    }
    if (form.target_market !== baseline.target_market) {
      changes.target_market = form.target_market;
    }
    if (Object.keys(changes).length === 0) return;
    try {
      await update.mutateAsync(changes);
      toast.success(t("settings.toast.saved"));
      refetch();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("settings.toast.failed"));
    }
  };

  const apiKey = cfg ? String(cfg["backend_api_key"] ?? "") : "";
  const dataVendors = cfg?.["data_vendors"] as
    | Record<string, string>
    | undefined;
  const riskLimits = data?.risk_limits ?? null;
  const buildSha = (cfg?.["build_sha"] as string | undefined) ?? "—";

  return (
    <AppShell title={t("settings.title")} subtitle={t("settings.subtitle")}>
      <div className="flex flex-col gap-5">
        {/* Mutable form */}
        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                <Server className="h-4 w-4 text-brand-400" aria-hidden />
                {t("settings.llm.title")}
              </CardTitle>
              <CardDescription>{t("settings.llm.desc")}</CardDescription>
            </div>
            <div className="flex items-center gap-1.5">
              <Button
                size="sm"
                variant="outline"
                leftIcon={<RotateCcw className="h-3.5 w-3.5" aria-hidden />}
                onClick={reset}
                disabled={!dirty || update.isPending}
              >
                {t("settings.btn.reset")}
              </Button>
              <Button
                size="sm"
                leftIcon={<Save className="h-3.5 w-3.5" aria-hidden />}
                onClick={submit}
                loading={update.isPending}
                disabled={!dirty || update.isPending}
              >
                {t("settings.btn.save")}
              </Button>
            </div>
          </CardHeader>
          <CardBody>
            {isLoading ? (
              <Skeleton className="h-48 w-full rounded-lg" />
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
                <Input
                  label={t("settings.field.backendUrl")}
                  value={form.backend_url}
                  onChange={(e) => set("backend_url", e.target.value)}
                  placeholder="https://api.deepseek.com"
                  hint={t("settings.field.backendUrlHint")}
                  disabled={update.isPending}
                />
                <Input
                  label={t("settings.field.market")}
                  value={form.target_market}
                  onChange={(e) => set("target_market", e.target.value)}
                  hint={t("settings.field.marketHint")}
                  disabled={update.isPending}
                />
                <Input
                  label={t("settings.field.deep")}
                  value={form.deep_think_model}
                  onChange={(e) => set("deep_think_model", e.target.value)}
                  placeholder="deepseek-chat"
                  hint={t("settings.field.deepHint")}
                  disabled={update.isPending}
                />
                <Input
                  label={t("settings.field.quick")}
                  value={form.quick_think_model}
                  onChange={(e) => set("quick_think_model", e.target.value)}
                  placeholder="deepseek-chat"
                  hint={t("settings.field.quickHint")}
                  disabled={update.isPending}
                />
              </div>
            )}
          </CardBody>
        </Card>

        {/* Read-only diagnostics */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <Card>
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Lock className="h-4 w-4 text-brand-400" aria-hidden />
                  {t("settings.creds.title")}
                </CardTitle>
                <CardDescription>{t("settings.creds.desc")}</CardDescription>
              </div>
            </CardHeader>
            <CardBody className="flex flex-col gap-2.5">
              <KvRow
                label={t("settings.creds.apiKey")}
                value={apiKey || "—"}
                mono
              />
              {dataVendors && (
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-fg-muted mb-1.5">
                    {t("settings.creds.vendors")}
                  </p>
                  <div className="grid grid-cols-2 gap-1.5">
                    {Object.entries(dataVendors).map(([k, v]) => (
                      <div
                        key={k}
                        className="flex items-center justify-between px-2.5 py-1.5 rounded-md bg-ink-800/60 border border-line"
                      >
                        <span className="text-xs num text-fg-muted">{k}</span>
                        <span className="text-xs num text-fg">{v}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Brain className="h-4 w-4 text-brand-400" aria-hidden />
                  {t("settings.rl.title")}
                </CardTitle>
                <CardDescription>{t("settings.rl.desc")}</CardDescription>
              </div>
              <Badge tone={rl.data?.enabled ? "brand" : "neutral"}>
                {rl.data?.enabled
                  ? t("settings.rl.on")
                  : t("settings.rl.off")}
              </Badge>
            </CardHeader>
            <CardBody className="flex flex-col gap-2 text-xs">
              <KvRow
                label={t("settings.rl.loaded")}
                value={rl.data?.loaded ? t("settings.yes") : t("settings.no")}
              />
              <KvRow
                label={t("settings.rl.feature")}
                value={rl.data?.feature_version ?? "—"}
                mono
              />
              <KvRow
                label={t("settings.rl.path")}
                value={rl.data?.model_path ?? "—"}
                mono
              />
              <p className="text-[11px] text-fg-subtle mt-1 leading-relaxed">
                {t("settings.rl.note")}
              </p>
            </CardBody>
          </Card>
        </div>

        {/* Locale + theme */}
        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                <Languages className="h-4 w-4 text-brand-400" aria-hidden />
                {t("settings.locale.title")}
              </CardTitle>
              <CardDescription>{t("settings.locale.desc")}</CardDescription>
            </div>
          </CardHeader>
          <CardBody>
            <div className="flex flex-wrap gap-1.5">
              {(["en", "ar"] as Locale[]).map((opt) => {
                const active = locale === opt;
                return (
                  <button
                    key={opt}
                    type="button"
                    onClick={() => setLocale(opt)}
                    aria-pressed={active}
                    className={cn(
                      "h-8 px-3 rounded-md text-xs font-medium border",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50",
                      active
                        ? "bg-brand-500/15 text-brand-400 border-brand-500/30"
                        : "bg-ink-800 text-fg-muted border-line hover:text-fg"
                    )}
                  >
                    {opt === "en"
                      ? t("settings.locale.en")
                      : t("settings.locale.ar")}
                  </button>
                );
              })}
            </div>
            <p className="text-[11px] text-fg-subtle mt-2">
              {t("settings.locale.note")}
            </p>
          </CardBody>
        </Card>

        {/* Risk limits + build identity */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <Card>
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Shield className="h-4 w-4 text-brand-400" aria-hidden />
                  {t("settings.risk.title")}
                </CardTitle>
                <CardDescription>{t("settings.risk.desc")}</CardDescription>
              </div>
            </CardHeader>
            <CardBody>
              {isLoading ? (
                <Skeleton className="h-32 w-full rounded-lg" />
              ) : riskLimits ? (
                <JSONViewer
                  data={riskLimits}
                  rootKey="risk_limits"
                  className="max-h-72 overflow-auto"
                />
              ) : (
                <p className="text-xs text-fg-subtle">
                  {t("settings.risk.empty")}
                </p>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Cpu className="h-4 w-4 text-brand-400" aria-hidden />
                  {t("settings.build.title")}
                </CardTitle>
                <CardDescription>{t("settings.build.desc")}</CardDescription>
              </div>
            </CardHeader>
            <CardBody className="flex flex-col gap-2 text-xs">
              <KvRow label={t("settings.build.sha")} value={buildSha} mono />
              <KvRow
                label={t("settings.build.provider")}
                value={
                  cfg ? String(cfg["llm_provider"] ?? "openai") : "—"
                }
                mono
              />
              <KvRow
                label={t("settings.build.onlineTools")}
                value={
                  cfg
                    ? cfg["online_tools"]
                      ? t("settings.yes")
                      : t("settings.no")
                    : "—"
                }
              />
              <KvRow
                label={t("settings.build.market")}
                value={String(cfg?.["target_market"] ?? "EGX")}
                mono
              />
            </CardBody>
          </Card>
        </div>

        {/* Disclaimer reminder */}
        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-amber-300" aria-hidden />
                {t("settings.disclaimer.title")}
              </CardTitle>
              <CardDescription>{t("settings.disclaimer.desc")}</CardDescription>
            </div>
            <Globe className="h-4 w-4 text-fg-subtle" aria-hidden />
          </CardHeader>
          <CardBody>
            <p className="text-xs text-fg-muted leading-relaxed">
              {t("disclaimer.body")}
            </p>
          </CardBody>
        </Card>
      </div>
    </AppShell>
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
    <div className="flex items-baseline justify-between gap-3 border-b border-line/60 pb-1.5">
      <span className="text-[10px] uppercase tracking-wider text-fg-subtle shrink-0">
        {label}
      </span>
      <span
        className={cn(
          "text-fg break-all text-end",
          mono ? "num text-xs" : "text-sm"
        )}
      >
        {value}
      </span>
    </div>
  );
}
