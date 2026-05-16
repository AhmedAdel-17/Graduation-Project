import { useMemo } from "react";
import { Link } from "react-router-dom";
import { ChevronRight, History as HistoryIcon, Sparkles } from "lucide-react";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { EmptyState } from "../../components/ui/EmptyState";
import { useResultsIndex } from "../../hooks/useSessionTrace";
import { useT } from "../../lib/i18n";
import type { ResultSessionSummary } from "../../services/api/types";

function fmtTimestamp(iso: string): string {
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

export function HistoryTab({ ticker }: { ticker: string }) {
  const t = useT();
  const { data, isLoading } = useResultsIndex();

  const rows: ResultSessionSummary[] = useMemo(() => {
    const all = data?.sessions ?? [];
    return all
      .filter((s) => s.ticker === ticker)
      .sort((a, b) => b.timestamp.localeCompare(a.timestamp));
  }, [data, ticker]);

  if (isLoading) {
    return (
      <Card>
        <CardBody>
          <Skeleton className="h-48 w-full rounded-lg" />
        </CardBody>
      </Card>
    );
  }

  if (rows.length === 0) {
    return (
      <Card>
        <CardBody>
          <EmptyState
            icon={<HistoryIcon className="h-4 w-4" />}
            title={t("history.empty.title")}
            description={t("history.empty.desc")}
            action={
              <Link
                to={`/run?ticker=${encodeURIComponent(ticker)}`}
                className="inline-flex items-center gap-1 text-xs text-brand-400 hover:text-brand-300"
              >
                <Sparkles className="h-3 w-3" aria-hidden />
                {t("history.empty.cta")}
              </Link>
            }
          />
        </CardBody>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>{t("history.title")}</CardTitle>
          <CardDescription>
            {rows.length} {t("history.sessions")} · {ticker}
          </CardDescription>
        </div>
      </CardHeader>
      <CardBody className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm" aria-label={t("history.aria")}>
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wider text-fg-muted border-b border-line">
                <th className="px-5 py-2.5">{t("history.col.tradeDate")}</th>
                <th className="px-5 py-2.5">{t("history.col.timestamp")}</th>
                <th className="px-5 py-2.5">{t("history.col.session")}</th>
                <th className="px-5 py-2.5">{t("history.col.market")}</th>
                <th className="px-5 py-2.5 w-10" aria-label="open" />
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rows.map((row) => (
                <tr
                  key={row.session_id}
                  className="hover:bg-ink-800/40 transition-colors"
                >
                  <td className="px-5 py-2.5 num text-fg">
                    {row.trade_date}
                  </td>
                  <td className="px-5 py-2.5 num text-fg-muted text-xs">
                    {fmtTimestamp(row.timestamp)}
                  </td>
                  <td className="px-5 py-2.5 num text-fg-subtle truncate max-w-[180px]">
                    {row.session_id.slice(0, 18)}…
                  </td>
                  <td className="px-5 py-2.5 text-fg-muted text-xs">
                    {row.market || "EGX"}
                  </td>
                  <td className="px-5 py-2.5 text-end">
                    <Link
                      to={`/sessions/${encodeURIComponent(row.session_id)}`}
                      className="inline-flex items-center justify-end h-7 w-7 rounded-md text-fg-muted hover:text-fg hover:bg-ink-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
                      aria-label={t("history.col.open")}
                    >
                      <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardBody>
    </Card>
  );
}
