import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ChevronRight, History, Search, Sparkles, X } from "lucide-react";
import { AppShell } from "../../components/layout/AppShell";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { EmptyState } from "../../components/ui/EmptyState";
import { Badge } from "../../components/ui/Badge";
import { useResultsIndex } from "../../hooks/useSessionTrace";
import { useT } from "../../lib/i18n";
import { cn } from "../../lib/utils";
import type { ResultSessionSummary } from "../../services/api/types";

const PAGE_SIZE = 50;

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

export function SessionsIndexPage() {
  const t = useT();
  const [params, setParams] = useSearchParams();
  const initialTicker = params.get("ticker") ?? "";
  const initialQuery = params.get("q") ?? "";
  const [tickerFilter, setTickerFilter] = useState(initialTicker);
  const [query, setQuery] = useState(initialQuery);
  const [page, setPage] = useState(0);

  const { data, isLoading } = useResultsIndex();

  const filtered = useMemo<ResultSessionSummary[]>(() => {
    const all = data?.sessions ?? [];
    const tickerNeedle = tickerFilter.trim().toUpperCase();
    const queryNeedle = query.trim().toLowerCase();
    return all
      .filter((s) => {
        if (tickerNeedle && !s.ticker.toUpperCase().includes(tickerNeedle)) {
          return false;
        }
        if (queryNeedle) {
          const hay =
            `${s.ticker} ${s.session_id} ${s.trade_date}`.toLowerCase();
          if (!hay.includes(queryNeedle)) return false;
        }
        return true;
      })
      .sort((a, b) => b.timestamp.localeCompare(a.timestamp));
  }, [data, tickerFilter, query]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const visible = filtered.slice(
    safePage * PAGE_SIZE,
    safePage * PAGE_SIZE + PAGE_SIZE
  );

  const tickerCount = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const s of data?.sessions ?? []) {
      counts[s.ticker] = (counts[s.ticker] ?? 0) + 1;
    }
    return counts;
  }, [data]);
  const tickerChips = Object.entries(tickerCount)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8);

  // Persist filters into the URL so the page is shareable.
  const setTickerAndUrl = (next: string) => {
    setTickerFilter(next);
    setPage(0);
    setParams((prev) => {
      const out = new URLSearchParams(prev);
      if (next.trim()) out.set("ticker", next.trim());
      else out.delete("ticker");
      return out;
    });
  };

  const setQueryAndUrl = (next: string) => {
    setQuery(next);
    setPage(0);
    setParams((prev) => {
      const out = new URLSearchParams(prev);
      if (next.trim()) out.set("q", next.trim());
      else out.delete("q");
      return out;
    });
  };

  return (
    <AppShell
      title={t("sessions.title")}
      subtitle={t("sessions.subtitle")}
    >
      <div className="flex flex-col gap-5">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>{t("sessions.filter.title")}</CardTitle>
              <CardDescription>
                {filtered.length} / {data?.sessions?.length ?? 0}{" "}
                {t("sessions.filter.matchSuffix")}
              </CardDescription>
            </div>
            {(tickerFilter || query) && (
              <button
                type="button"
                onClick={() => {
                  setTickerAndUrl("");
                  setQueryAndUrl("");
                }}
                className="inline-flex items-center gap-1 h-7 px-2.5 rounded-md text-xs text-fg-muted hover:text-fg border border-line hover:border-line-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
              >
                <X className="h-3 w-3" aria-hidden />
                {t("sessions.filter.clear")}
              </button>
            )}
          </CardHeader>
          <CardBody className="flex flex-col gap-3">
            <div className="grid grid-cols-1 md:grid-cols-[200px_1fr] gap-2.5">
              <label className="flex flex-col gap-1">
                <span className="text-[10px] uppercase tracking-wider text-fg-muted">
                  {t("sessions.filter.ticker")}
                </span>
                <input
                  type="text"
                  value={tickerFilter}
                  onChange={(e) => setTickerAndUrl(e.target.value)}
                  placeholder="COMI.CA"
                  className="h-9 px-3 rounded-md bg-ink-800/80 border border-line text-sm num placeholder:text-fg-subtle focus:outline-none focus:border-brand-500/50"
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-[10px] uppercase tracking-wider text-fg-muted">
                  {t("sessions.filter.search")}
                </span>
                <div className="flex items-center gap-1.5 h-9 px-3 rounded-md bg-ink-800/80 border border-line focus-within:border-brand-500/50">
                  <Search
                    className="h-3.5 w-3.5 text-fg-subtle shrink-0"
                    aria-hidden
                  />
                  <input
                    type="text"
                    value={query}
                    onChange={(e) => setQueryAndUrl(e.target.value)}
                    placeholder={t("sessions.filter.searchPlaceholder")}
                    className="flex-1 bg-transparent text-sm placeholder:text-fg-subtle focus:outline-none"
                  />
                </div>
              </label>
            </div>

            {tickerChips.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                <span className="text-[10px] uppercase tracking-wider text-fg-subtle me-1 self-center">
                  {t("sessions.filter.chipsLabel")}
                </span>
                {tickerChips.map(([ticker, count]) => {
                  const active =
                    tickerFilter.trim().toUpperCase() === ticker.toUpperCase();
                  return (
                    <button
                      key={ticker}
                      type="button"
                      onClick={() => setTickerAndUrl(active ? "" : ticker)}
                      aria-pressed={active}
                      className={cn(
                        "inline-flex items-center gap-1 h-6 px-2 rounded-md text-[11px] num border",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50",
                        active
                          ? "bg-brand-500/15 text-brand-400 border-brand-500/30"
                          : "bg-ink-800 text-fg-muted border-line hover:text-fg"
                      )}
                    >
                      <span>{ticker}</span>
                      <span className="text-fg-subtle">{count}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </CardBody>
        </Card>

        {isLoading ? (
          <Card>
            <CardBody>
              <Skeleton className="h-64 w-full rounded-lg" />
            </CardBody>
          </Card>
        ) : filtered.length === 0 ? (
          <Card>
            <CardBody>
              <EmptyState
                icon={<History className="h-4 w-4" />}
                title={t("sessions.empty.title")}
                description={t("sessions.empty.desc")}
                action={
                  <Link
                    to="/run"
                    className="inline-flex items-center gap-1 text-xs text-brand-400 hover:text-brand-300"
                  >
                    <Sparkles className="h-3 w-3" aria-hidden />
                    {t("sessions.empty.cta")}
                  </Link>
                }
              />
            </CardBody>
          </Card>
        ) : (
          <Card>
            <CardHeader>
              <div>
                <CardTitle>{t("sessions.table.title")}</CardTitle>
                <CardDescription>
                  {visible.length} {t("sessions.table.shown")}
                  {totalPages > 1 && (
                    <>
                      {" · "}
                      {t("sessions.table.page")} {safePage + 1} / {totalPages}
                    </>
                  )}
                </CardDescription>
              </div>
            </CardHeader>
            <CardBody className="p-0">
              <div className="overflow-x-auto">
                <table
                  className="w-full text-sm"
                  aria-label={t("sessions.table.aria")}
                >
                  <thead>
                    <tr className="text-left text-[10px] uppercase tracking-wider text-fg-muted border-b border-line">
                      <th className="px-5 py-2.5">
                        {t("history.col.tradeDate")}
                      </th>
                      <th className="px-5 py-2.5">{t("sessions.col.ticker")}</th>
                      <th className="px-5 py-2.5">
                        {t("history.col.timestamp")}
                      </th>
                      <th className="px-5 py-2.5">
                        {t("history.col.session")}
                      </th>
                      <th className="px-5 py-2.5">{t("history.col.market")}</th>
                      <th className="px-5 py-2.5 w-10" aria-label="open" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {visible.map((row) => (
                      <tr
                        key={row.session_id}
                        className="hover:bg-ink-800/40 transition-colors"
                      >
                        <td className="px-5 py-2.5 num text-fg">
                          {row.trade_date}
                        </td>
                        <td className="px-5 py-2.5">
                          <Link
                            to={`/workspace?ticker=${encodeURIComponent(row.ticker)}`}
                            className="num text-fg hover:text-brand-400"
                          >
                            {row.ticker}
                          </Link>
                        </td>
                        <td className="px-5 py-2.5 num text-fg-muted text-xs">
                          {fmtTimestamp(row.timestamp)}
                        </td>
                        <td className="px-5 py-2.5 num text-fg-subtle truncate max-w-[180px]">
                          {row.session_id.slice(0, 18)}…
                        </td>
                        <td className="px-5 py-2.5">
                          <Badge tone="neutral">{row.market || "EGX"}</Badge>
                        </td>
                        <td className="px-5 py-2.5 text-end">
                          <Link
                            to={`/sessions/${encodeURIComponent(row.session_id)}`}
                            className="inline-flex items-center justify-end h-7 w-7 rounded-md text-fg-muted hover:text-fg hover:bg-ink-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
                            aria-label={t("history.col.open")}
                          >
                            <ChevronRight
                              className="h-3.5 w-3.5"
                              aria-hidden
                            />
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {totalPages > 1 && (
                <div className="flex items-center justify-between gap-2 px-5 py-3 border-t border-line">
                  <button
                    type="button"
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                    disabled={safePage === 0}
                    className="h-7 px-3 rounded-md text-xs text-fg-muted hover:text-fg border border-line disabled:opacity-40 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
                  >
                    ← {t("sessions.table.prev")}
                  </button>
                  <span className="num text-xs text-fg-subtle">
                    {safePage + 1} / {totalPages}
                  </span>
                  <button
                    type="button"
                    onClick={() =>
                      setPage((p) => Math.min(totalPages - 1, p + 1))
                    }
                    disabled={safePage >= totalPages - 1}
                    className="h-7 px-3 rounded-md text-xs text-fg-muted hover:text-fg border border-line disabled:opacity-40 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
                  >
                    {t("sessions.table.next")} →
                  </button>
                </div>
              )}
            </CardBody>
          </Card>
        )}
      </div>
    </AppShell>
  );
}
