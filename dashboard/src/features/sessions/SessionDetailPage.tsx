import { Link, useParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  Database,
  ExternalLink,
  LayoutDashboard,
  Search,
} from "lucide-react";
import { AppShell } from "../../components/layout/AppShell";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { EmptyState } from "../../components/ui/EmptyState";
import { Badge } from "../../components/ui/Badge";
import { Gauge } from "../../components/ui/Gauge";
import { useSessionTrace } from "../../hooks/useSessionTrace";
import { useT } from "../../lib/i18n";
import { signalBg } from "../../lib/utils";
import { ReasoningView } from "../workspace/ReasoningView";

function decisionToSignal(decision: string | null | undefined): string | null {
  const upper = (decision ?? "").toUpperCase();
  if (upper.includes("BUY")) return "BUY";
  if (upper.includes("SELL")) return "SELL";
  if (upper.includes("HOLD")) return "HOLD";
  return null;
}

export function SessionDetailPage() {
  const t = useT();
  const { sessionId = "" } = useParams<{ sessionId: string }>();
  const traceQuery = useSessionTrace(sessionId || null);
  const session = traceQuery.data?.session ?? null;
  const events = traceQuery.data?.events ?? [];
  const source = traceQuery.data?.source ?? null;

  return (
    <AppShell
      title={t("sessionDetail.title")}
      subtitle={session?.ticker ?? sessionId.slice(0, 18)}
    >
      <div className="flex flex-col gap-5">
        <BackBar />

        {traceQuery.isLoading ? (
          <Card>
            <CardBody>
              <Skeleton className="h-32 w-full rounded-lg" />
            </CardBody>
          </Card>
        ) : traceQuery.isError || !traceQuery.data ? (
          <Card>
            <CardBody>
              <EmptyState
                icon={<AlertTriangle className="h-4 w-4" />}
                title={t("sessionDetail.notFound.title")}
                description={t("sessionDetail.notFound.desc")}
                action={
                  <Link
                    to="/sessions"
                    className="inline-flex items-center gap-1 text-xs text-brand-400 hover:text-brand-300"
                  >
                    <ArrowLeft className="h-3 w-3" aria-hidden />
                    {t("sessionDetail.notFound.cta")}
                  </Link>
                }
              />
            </CardBody>
          </Card>
        ) : !session ? (
          <Card>
            <CardBody>
              <EmptyState
                icon={<Search className="h-4 w-4" />}
                title={t("sessionDetail.empty.title")}
                description={t("sessionDetail.empty.desc")}
              />
            </CardBody>
          </Card>
        ) : (
          <>
            <SessionHeader session={session} source={source} />
            <ReasoningView
              session={session}
              events={events}
              source={source}
              hideMetaBar
            />
          </>
        )}
      </div>
    </AppShell>
  );
}

function BackBar() {
  const t = useT();
  return (
    <div className="flex items-center gap-2">
      <Link
        to="/sessions"
        className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md text-xs text-fg-muted hover:text-fg border border-line hover:border-line-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
      >
        <ArrowLeft className="h-3 w-3" aria-hidden />
        {t("sessionDetail.back")}
      </Link>
    </div>
  );
}

function SessionHeader({
  session,
  source,
}: {
  session: NonNullable<ReturnType<typeof useSessionTrace>["data"]>["session"];
  source: "postgres" | "jsonl" | "none" | null;
}) {
  const t = useT();
  if (!session) return null;
  const sig = decisionToSignal(session.final_decision);
  const confidence =
    typeof session.confidence_overall === "number"
      ? session.confidence_overall * 100
      : null;
  const fingerprint =
    (session.model_fingerprint as Record<string, unknown> | null) ?? null;
  const deepModel =
    (fingerprint?.deep_think_llm as string) ||
    (fingerprint?.quick_think_llm as string) ||
    null;

  return (
    <Card>
      <CardHeader>
        <div className="min-w-0">
          <CardTitle className="num truncate">
            {session.ticker ?? "—"}
          </CardTitle>
          <CardDescription>
            <span className="num">{session.trade_date ?? "—"}</span>
            {session.session_id && (
              <span className="ms-2 num text-fg-subtle">
                {session.session_id.slice(0, 18)}…
              </span>
            )}
          </CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {session.ticker && (
            <Link
              to={`/workspace?ticker=${encodeURIComponent(session.ticker)}`}
              className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md text-xs text-fg-muted hover:text-fg border border-line hover:border-line-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
            >
              <LayoutDashboard className="h-3 w-3" aria-hidden />
              {t("sessionDetail.openInWorkspace")}
            </Link>
          )}
          {source === "postgres" && (
            <Badge tone="brand">
              <Database className="h-2.5 w-2.5" aria-hidden /> postgres
            </Badge>
          )}
          {source === "jsonl" && <Badge tone="neutral">jsonl</Badge>}
        </div>
      </CardHeader>
      <CardBody className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="flex flex-col gap-2">
          <span className="text-[10px] uppercase tracking-wider text-fg-muted">
            {t("sessionDetail.finalDecision")}
          </span>
          {sig ? (
            <span
              className={
                "inline-flex items-center justify-center self-start " +
                "px-3 h-9 rounded-md border text-sm font-semibold tracking-wider " +
                signalBg(sig)
              }
            >
              {sig}
            </span>
          ) : (
            <span className="text-sm text-fg-subtle">
              {session.final_decision || "—"}
            </span>
          )}
          {session.risk_veto && (
            <Badge tone="down" className="self-start">
              <AlertTriangle className="h-2.5 w-2.5" aria-hidden />{" "}
              {t("reasoning.meta.veto")}
            </Badge>
          )}
        </div>
        <div className="flex flex-col gap-2">
          {confidence !== null && (
            <Gauge
              value={confidence}
              label={t("workspace.overview.signal.confidence")}
              tone={
                sig === "SELL" ? "down" : sig === "BUY" ? "up" : "accent"
              }
              unit="%"
            />
          )}
          {deepModel && (
            <p className="text-[11px] text-fg-subtle">
              <span className="uppercase tracking-wider">
                {t("reasoning.row.model")}
              </span>{" "}
              <span className="num text-fg-muted">{deepModel}</span>
              {(fingerprint?.temperature !== undefined ||
                fingerprint?.seed !== undefined) && (
                <span className="num text-fg-subtle ms-2">
                  T=
                  {String(fingerprint?.temperature ?? "?")}
                  {" · "}
                  seed={String(fingerprint?.seed ?? "?")}
                </span>
              )}
            </p>
          )}
          {session.session_id && source === "jsonl" && (
            <a
              href={`/api/results/${encodeURIComponent(session.ticker ?? "")}/${encodeURIComponent(session.session_id)}`}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 self-start text-[11px] text-fg-muted hover:text-fg"
            >
              <ExternalLink className="h-3 w-3" aria-hidden />
              {t("sessionDetail.viewRaw")}
            </a>
          )}
        </div>
      </CardBody>
    </Card>
  );
}
