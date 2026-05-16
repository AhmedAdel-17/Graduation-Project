import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  ChevronRight,
  Compass,
  Play,
  Search,
  Sparkles,
  X,
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
import { EmptyState } from "../../components/ui/EmptyState";
import {
  getAllTickerMeta,
  LIQUIDITY_ORDER,
  SECTOR_ORDER,
  sectorLabelKey,
  type LiquidityTier,
  type Sector,
  type TickerMeta,
} from "../../data/egx-tickers";
import { useResultsIndex } from "../../hooks/useSessionTrace";
import { useRunPrediction } from "../../hooks/usePrediction";
import { useAppStore } from "../../store/appStore";
import { useT } from "../../lib/i18n";
import { cn } from "../../lib/utils";
import type { ResultSessionSummary } from "../../services/api/types";

const BULK_MAX = 10;

interface UniverseRow extends TickerMeta {
  lastSession: ResultSessionSummary | null;
  sessionCount: number;
}

function fmtTimestamp(iso: string | undefined): string {
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

function liquidityTone(tier: LiquidityTier): "brand" | "accent" | "neutral" {
  if (tier === "MEGA") return "brand";
  if (tier === "MID") return "accent";
  return "neutral";
}

export function UniversePage() {
  const t = useT();
  const navigate = useNavigate();
  const setSelectedTicker = useAppStore((s) => s.setSelectedTicker);
  const { data: resultsIndex, refetch: refetchResults } = useResultsIndex();
  const runPrediction = useRunPrediction();

  const [params, setParams] = useSearchParams();
  const initialQuery = params.get("q") ?? "";
  const initialSectors = (params.get("sectors") ?? "")
    .split(",")
    .filter((s): s is Sector => SECTOR_ORDER.includes(s as Sector));
  const initialTiers = (params.get("tiers") ?? "")
    .split(",")
    .filter((s): s is LiquidityTier =>
      LIQUIDITY_ORDER.includes(s as LiquidityTier)
    );

  const [query, setQuery] = useState(initialQuery);
  const [sectors, setSectors] = useState<Set<Sector>>(new Set(initialSectors));
  const [tiers, setTiers] = useState<Set<LiquidityTier>>(new Set(initialTiers));
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkProgress, setBulkProgress] = useState<{
    done: number;
    total: number;
    current: string | null;
  } | null>(null);

  const allMeta = useMemo(() => getAllTickerMeta(), []);

  // Build per-ticker map of latest session + count from the results index.
  const lastByTicker = useMemo(() => {
    const out: Record<
      string,
      { latest: ResultSessionSummary; count: number }
    > = {};
    for (const s of resultsIndex?.sessions ?? []) {
      const cur = out[s.ticker];
      if (!cur) {
        out[s.ticker] = { latest: s, count: 1 };
      } else {
        cur.count += 1;
        if (s.timestamp.localeCompare(cur.latest.timestamp) > 0) {
          cur.latest = s;
        }
      }
    }
    return out;
  }, [resultsIndex]);

  const rows = useMemo<UniverseRow[]>(() => {
    return allMeta.map((m) => {
      const entry = lastByTicker[m.ticker];
      return {
        ...m,
        lastSession: entry?.latest ?? null,
        sessionCount: entry?.count ?? 0,
      };
    });
  }, [allMeta, lastByTicker]);

  const filtered = useMemo<UniverseRow[]>(() => {
    const needle = query.trim().toLowerCase();
    return rows.filter((r) => {
      if (sectors.size > 0 && !sectors.has(r.sector)) return false;
      if (tiers.size > 0 && !tiers.has(r.liquidity)) return false;
      if (needle) {
        const hay = `${r.ticker} ${r.ticker.replace(".CA", "")}`.toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
  }, [rows, query, sectors, tiers]);

  // Group by sector for the table view.
  const grouped = useMemo(() => {
    const map = new Map<Sector, UniverseRow[]>();
    for (const r of filtered) {
      const arr = map.get(r.sector) ?? [];
      arr.push(r);
      map.set(r.sector, arr);
    }
    return SECTOR_ORDER.filter((s) => map.has(s)).map((s) => ({
      sector: s,
      rows: map.get(s)!,
    }));
  }, [filtered]);

  // ── URL persistence ───────────────────────────────────────────────────
  const updateUrl = (next: {
    q?: string;
    sectors?: Set<Sector>;
    tiers?: Set<LiquidityTier>;
  }) => {
    setParams((prev) => {
      const out = new URLSearchParams(prev);
      const q = next.q ?? query;
      const ss = next.sectors ?? sectors;
      const ts = next.tiers ?? tiers;
      if (q.trim()) out.set("q", q.trim());
      else out.delete("q");
      if (ss.size > 0) out.set("sectors", [...ss].join(","));
      else out.delete("sectors");
      if (ts.size > 0) out.set("tiers", [...ts].join(","));
      else out.delete("tiers");
      return out;
    });
  };

  const toggleSector = (s: Sector) => {
    const next = new Set(sectors);
    if (next.has(s)) next.delete(s);
    else next.add(s);
    setSectors(next);
    updateUrl({ sectors: next });
  };
  const toggleTier = (tier: LiquidityTier) => {
    const next = new Set(tiers);
    if (next.has(tier)) next.delete(tier);
    else next.add(tier);
    setTiers(next);
    updateUrl({ tiers: next });
  };
  const setQueryAndUrl = (q: string) => {
    setQuery(q);
    updateUrl({ q });
  };
  const clearFilters = () => {
    setQuery("");
    setSectors(new Set());
    setTiers(new Set());
    updateUrl({ q: "", sectors: new Set(), tiers: new Set() });
  };

  // ── Selection ────────────────────────────────────────────────────────
  const toggleRow = (ticker: string) => {
    const next = new Set(selected);
    if (next.has(ticker)) next.delete(ticker);
    else next.add(ticker);
    setSelected(next);
  };
  const allVisibleSelected =
    filtered.length > 0 && filtered.every((r) => selected.has(r.ticker));
  const toggleSelectAll = () => {
    if (allVisibleSelected) {
      const next = new Set(selected);
      filtered.forEach((r) => next.delete(r.ticker));
      setSelected(next);
    } else {
      const next = new Set(selected);
      filtered.forEach((r) => next.add(r.ticker));
      setSelected(next);
    }
  };
  const clearSelection = () => setSelected(new Set());

  // ── Single-row navigation ─────────────────────────────────────────────
  const openWorkspace = (ticker: string) => {
    setSelectedTicker(ticker);
    navigate(`/workspace?ticker=${encodeURIComponent(ticker)}`);
  };
  const launchRun = (ticker: string) => {
    setSelectedTicker(ticker);
    navigate("/run");
  };

  // ── Bulk quick-predict (sequential POST /api/test/random-egx) ─────────
  const runBulk = async () => {
    const tickers = [...selected].slice(0, BULK_MAX);
    if (tickers.length === 0) return;
    setBulkProgress({ done: 0, total: tickers.length, current: null });
    let succeeded = 0;
    for (let i = 0; i < tickers.length; i++) {
      const tk = tickers[i];
      setBulkProgress({ done: i, total: tickers.length, current: tk });
      try {
        await runPrediction.mutateAsync(tk);
        succeeded += 1;
      } catch (e) {
        toast.error(
          `${tk}: ${e instanceof Error ? e.message : t("universe.bulk.toast.failItem")}`
        );
      }
    }
    setBulkProgress(null);
    refetchResults();
    toast.success(
      t("universe.bulk.toast.done").replace("{n}", String(succeeded))
    );
    clearSelection();
  };

  const anyFilter = sectors.size > 0 || tiers.size > 0 || query.trim() !== "";
  const bulkRunning = bulkProgress !== null;

  return (
    <AppShell title={t("universe.title")} subtitle={t("universe.subtitle")}>
      <div className="flex flex-col gap-5">
        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                <Compass className="h-4 w-4 text-brand-400" aria-hidden />
                {t("universe.filter.title")}
              </CardTitle>
              <CardDescription>
                {filtered.length} / {rows.length}{" "}
                {t("universe.filter.shownSuffix")}
              </CardDescription>
            </div>
            {anyFilter && (
              <button
                type="button"
                onClick={clearFilters}
                className="inline-flex items-center gap-1 h-7 px-2.5 rounded-md text-xs text-fg-muted hover:text-fg border border-line hover:border-line-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
              >
                <X className="h-3 w-3" aria-hidden />
                {t("universe.filter.clear")}
              </button>
            )}
          </CardHeader>
          <CardBody className="flex flex-col gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wider text-fg-muted">
                {t("universe.filter.search")}
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
                  placeholder={t("universe.filter.searchPlaceholder")}
                  className="flex-1 bg-transparent text-sm placeholder:text-fg-subtle focus:outline-none"
                />
              </div>
            </label>

            <div>
              <p className="text-[10px] uppercase tracking-wider text-fg-muted mb-1.5">
                {t("universe.filter.sectors")}
              </p>
              <div className="flex flex-wrap gap-1.5">
                {SECTOR_ORDER.map((s) => {
                  const on = sectors.has(s);
                  const count = rows.filter((r) => r.sector === s).length;
                  return (
                    <button
                      key={s}
                      type="button"
                      onClick={() => toggleSector(s)}
                      aria-pressed={on}
                      className={cn(
                        "inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md text-[11px] font-medium border",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50",
                        on
                          ? "bg-brand-500/15 text-brand-400 border-brand-500/30"
                          : "bg-ink-800 text-fg-muted border-line hover:text-fg"
                      )}
                    >
                      <span>{t(sectorLabelKey(s))}</span>
                      <span className="num text-fg-subtle">{count}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            <div>
              <p className="text-[10px] uppercase tracking-wider text-fg-muted mb-1.5">
                {t("universe.filter.liquidity")}
              </p>
              <div className="flex flex-wrap gap-1.5">
                {LIQUIDITY_ORDER.map((tier) => {
                  const on = tiers.has(tier);
                  const count = rows.filter((r) => r.liquidity === tier).length;
                  return (
                    <button
                      key={tier}
                      type="button"
                      onClick={() => toggleTier(tier)}
                      aria-pressed={on}
                      className={cn(
                        "inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md text-[11px] font-medium border",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50",
                        on
                          ? "bg-brand-500/15 text-brand-400 border-brand-500/30"
                          : "bg-ink-800 text-fg-muted border-line hover:text-fg"
                      )}
                    >
                      <span>{t(`universe.tier.${tier.toLowerCase()}`)}</span>
                      <span className="num text-fg-subtle">{count}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          </CardBody>
        </Card>

        {selected.size > 0 && (
          <div
            role="region"
            aria-label={t("universe.bulk.barAria")}
            className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 rounded-lg border border-brand-500/30 bg-brand-500/10"
          >
            <div className="flex items-center gap-2 text-sm">
              <span className="text-brand-300 font-medium">
                {selected.size} {t("universe.bulk.selected")}
              </span>
              {selected.size > BULK_MAX && (
                <span className="text-[11px] text-amber-300">
                  {t("universe.bulk.cap")
                    .replace("{max}", String(BULK_MAX))
                    .replace("{n}", String(selected.size - BULK_MAX))}
                </span>
              )}
              {bulkRunning && bulkProgress && (
                <span className="text-xs text-fg-muted num">
                  · {bulkProgress.done}/{bulkProgress.total}
                  {bulkProgress.current ? ` · ${bulkProgress.current}` : ""}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={clearSelection}
                disabled={bulkRunning}
              >
                {t("universe.bulk.clear")}
              </Button>
              <Button
                size="sm"
                leftIcon={<Sparkles className="h-3.5 w-3.5" aria-hidden />}
                onClick={runBulk}
                loading={bulkRunning}
                disabled={bulkRunning}
              >
                {t("universe.bulk.runCta").replace(
                  "{n}",
                  String(Math.min(selected.size, BULK_MAX))
                )}
              </Button>
            </div>
          </div>
        )}

        {filtered.length === 0 ? (
          <Card>
            <CardBody>
              <EmptyState
                icon={<Compass className="h-4 w-4" />}
                title={t("universe.empty.title")}
                description={t("universe.empty.desc")}
                action={
                  anyFilter ? (
                    <button
                      type="button"
                      onClick={clearFilters}
                      className="inline-flex items-center gap-1 text-xs text-brand-400 hover:text-brand-300"
                    >
                      {t("universe.empty.cta")}
                    </button>
                  ) : null
                }
              />
            </CardBody>
          </Card>
        ) : (
          <Card>
            <CardHeader>
              <div>
                <CardTitle>{t("universe.table.title")}</CardTitle>
                <CardDescription>{t("universe.table.desc")}</CardDescription>
              </div>
              <Badge tone="neutral">
                {t("universe.table.sectorCount").replace(
                  "{n}",
                  String(grouped.length)
                )}
              </Badge>
            </CardHeader>
            <CardBody className="p-0">
              <div className="overflow-x-auto">
                <table
                  className="w-full text-sm"
                  aria-label={t("universe.table.aria")}
                >
                  <thead>
                    <tr className="text-left text-[10px] uppercase tracking-wider text-fg-muted border-b border-line">
                      <th className="px-4 py-2.5 w-10">
                        <input
                          type="checkbox"
                          aria-label={t("universe.table.selectAll")}
                          checked={allVisibleSelected}
                          onChange={toggleSelectAll}
                          disabled={bulkRunning}
                          className="accent-brand-500 cursor-pointer"
                        />
                      </th>
                      <th className="px-3 py-2.5">
                        {t("universe.col.ticker")}
                      </th>
                      <th className="px-3 py-2.5">
                        {t("universe.col.liquidity")}
                      </th>
                      <th className="px-3 py-2.5">
                        {t("universe.col.lastAnalyzed")}
                      </th>
                      <th className="px-3 py-2.5 num text-end">
                        {t("universe.col.sessions")}
                      </th>
                      <th className="px-3 py-2.5 text-end">
                        {t("universe.col.actions")}
                      </th>
                    </tr>
                  </thead>
                  {grouped.map((g) => (
                    <tbody
                      key={g.sector}
                      className="divide-y divide-line"
                    >
                      <tr className="bg-ink-800/40">
                        <td
                          colSpan={6}
                          className="px-4 py-2 text-[11px] uppercase tracking-wider text-fg-muted"
                        >
                          {t(sectorLabelKey(g.sector))}{" "}
                          <span className="text-fg-subtle num ms-1">
                            ({g.rows.length})
                          </span>
                        </td>
                      </tr>
                      {g.rows.map((r) => {
                        const isSelected = selected.has(r.ticker);
                        return (
                          <tr
                            key={r.ticker}
                            className={cn(
                              "transition-colors",
                              isSelected
                                ? "bg-brand-500/5"
                                : "hover:bg-ink-800/40"
                            )}
                          >
                            <td className="px-4 py-2.5">
                              <input
                                type="checkbox"
                                aria-label={t("universe.row.selectAria").replace(
                                  "{t}",
                                  r.ticker
                                )}
                                checked={isSelected}
                                onChange={() => toggleRow(r.ticker)}
                                disabled={bulkRunning}
                                className="accent-brand-500 cursor-pointer"
                              />
                            </td>
                            <td className="px-3 py-2.5">
                              <button
                                type="button"
                                onClick={() => openWorkspace(r.ticker)}
                                className="num text-fg hover:text-brand-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50 rounded"
                              >
                                {r.ticker}
                              </button>
                            </td>
                            <td className="px-3 py-2.5">
                              <Badge tone={liquidityTone(r.liquidity)}>
                                {r.liquidity}
                              </Badge>
                            </td>
                            <td className="px-3 py-2.5 num text-xs text-fg-muted">
                              {r.lastSession
                                ? fmtTimestamp(r.lastSession.timestamp)
                                : t("universe.col.never")}
                            </td>
                            <td className="px-3 py-2.5 num text-end text-fg-muted">
                              {r.sessionCount > 0 ? (
                                <Link
                                  to={`/sessions?ticker=${encodeURIComponent(r.ticker)}`}
                                  className="hover:text-brand-400"
                                >
                                  {r.sessionCount}
                                </Link>
                              ) : (
                                <span className="text-fg-subtle">—</span>
                              )}
                            </td>
                            <td className="px-3 py-2.5">
                              <div className="flex items-center justify-end gap-1">
                                <button
                                  type="button"
                                  onClick={() => launchRun(r.ticker)}
                                  disabled={bulkRunning}
                                  className="inline-flex items-center gap-1 h-7 px-2 rounded-md text-[11px] text-fg-muted hover:text-fg border border-line hover:border-line-strong disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
                                  aria-label={t("universe.row.runAria").replace(
                                    "{t}",
                                    r.ticker
                                  )}
                                >
                                  <Play
                                    className="h-3 w-3"
                                    aria-hidden
                                  />
                                  {t("universe.row.run")}
                                </button>
                                <button
                                  type="button"
                                  onClick={() => openWorkspace(r.ticker)}
                                  className="inline-flex items-center justify-center h-7 w-7 rounded-md text-fg-muted hover:text-fg hover:bg-ink-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
                                  aria-label={t(
                                    "universe.row.openAria"
                                  ).replace("{t}", r.ticker)}
                                >
                                  <ChevronRight
                                    className="h-3.5 w-3.5"
                                    aria-hidden
                                  />
                                </button>
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  ))}
                </table>
              </div>
            </CardBody>
          </Card>
        )}

        <p className="text-[11px] text-fg-subtle">
          {t("universe.footnote")}
        </p>
      </div>
    </AppShell>
  );
}
