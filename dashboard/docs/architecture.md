# Dashboard Architecture

> Companion to the project plan in `C:\Users\ahmed\.claude\plans\1-role-you-cuddly-pearl.md`.
> Audience: future engineers extending or maintaining the EGX research console.

---

## 1. Stack at a glance

| Layer | Choice | Why |
|---|---|---|
| Framework | React 19 + Vite 7 + TypeScript | Existing scaffold; SPA only, no SSR needed |
| Routing | `react-router-dom` v6 | File-flat routes in `App.tsx` |
| Server state | TanStack Query v5 | Caching, polling, optimistic invalidation |
| App state | Zustand v5 + `persist` | `selectedTicker`, `initialCapital`, RTL preference |
| Streaming | Native `WebSocket` wrapped in `useAgentStream` | Backed by `/api/analyze` |
| Styling | Tailwind 3.4 + design tokens in `tailwind.config.js` | See `design-system.md` |
| Charts | `lightweight-charts` v5 | Candlesticks, equity curves |
| i18n | Hand-rolled `lib/i18n.ts` + `useSyncExternalStore` | No `i18next`, ~960-line dict file |
| Forms | Native + a few `ui/` primitives | Settings page is the only form that POSTs |

No Redux. No Recoil. No Next.js. No Remix. `immer` is installed but only the WS reducer uses it.

---

## 2. Directory map

```
dashboard/src/
├── App.tsx                       # Route table
├── main.tsx                      # Provider tree (Query, Router)
├── index.css                     # Tailwind layers + global focus-visible
├── components/
│   ├── layout/
│   │   ├── AppShell.tsx          # Sidebar + TopBar + Footer + Disclaimer
│   │   ├── Sidebar.tsx           # Desktop nav (Research / Strategy / System)
│   │   ├── MobileNav.tsx         # Compact horizontal nav under TopBar
│   │   ├── TopBar.tsx            # Health pill + locale toggle + page title
│   │   ├── SiteFooter.tsx        # Disclaimer + git SHA + model identifiers
│   │   └── Disclaimer.tsx        # First-visit modal + persistent ribbon
│   ├── charts/                   # PriceChart, EquityCurve (lightweight-charts)
│   ├── ui/                       # Button, Card, Badge, Tabs, Tooltip, Dialog,
│   │                             #   Drawer, JSONViewer, Markdown, Gauge,
│   │                             #   StatusPill, EmptyState, LocaleToggle, ...
│   └── ErrorBoundary.tsx
├── features/
│   ├── workspace/                # Tabbed ticker workspace (PR3–6)
│   ├── run/                      # WebSocket-driven live run (PR2)
│   ├── sessions/                 # Audit-log index + detail (PR6)
│   ├── backtest/                 # Lab, wizard, detail, compare (PR7)
│   ├── universe/                 # EGX-30 browser + bulk-run (PR8)
│   ├── diagnostics/              # Health + memory + fingerprints (PR9)
│   ├── settings/                 # Config GET/PUT + locale + risk (PR9)
│   └── prediction/               # Legacy quick-prediction page (kept on /predict)
├── hooks/
│   ├── useAgentStream.ts         # WS lifecycle, idle → connecting → streaming
│   ├── useSessionTrace.ts        # /api/sessions/:id/trace + helpers
│   ├── useAgentMemory.ts         # 5-collection memory fan-out + reflections
│   ├── useBacktest.ts            # Backtests + RL meta-policy
│   ├── useDiagnostics.ts         # Health, prompts, fingerprints, config
│   ├── useTickers.ts             # EGX universe fetch (with fallback list)
│   └── usePrediction.ts          # Legacy quick-prediction mutation
├── services/api/
│   ├── client.ts                 # fetch wrapper, ApiError, VITE_API_BASE
│   ├── endpoints.ts              # All REST bindings, typed
│   ├── wsClient.ts               # WebSocket URL + connect helper
│   └── types.ts                  # All response/request shapes
├── data/
│   ├── egx-tickers.ts            # Sector + liquidity-tier metadata
│   └── agent-prompts.ts          # Phase / prompt-id mapping for ReasoningView
├── store/appStore.ts             # Zustand: selectedTicker, initialCapital
└── lib/
    ├── i18n.ts                   # EN + AR dicts, useT/useLocale, applyDocumentAttrs
    └── utils.ts                  # cn(), format helpers
```

---

## 3. Route tree

```
/                      → redirect → /workspace
/workspace             Tabbed research workspace (default landing)
/predict               Legacy quick-prediction (will retire once /workspace
                       can trigger fresh predictions)
/run                   Live multi-agent stream
/sessions              Audit-log index
/sessions/:sessionId   Frozen reasoning trace
/universe              EGX-30 browser + bulk quick-predict
/backtest              Backtest Lab (list of recent runs)
/backtest/new          Wizard (LLM or Backtrader)
/backtest/compare      Side-by-side LLM vs classical
/backtest/advanced     → redirect → /backtest/new
/backtest/:runId       Detail (KPIs, equity, trades, RL panel)
/diagnostics           Health, memory, persistence, streaming, RL, prompts, drift
/settings              Config form + locale + risk limits + build identity
*                      → redirect → /workspace
```

Sidebar groups (visual only — no nested routes):

- **Research**: Workspace, Run, Sessions, Universe
- **Strategy**: Backtest Lab
- **System**: Diagnostics, Settings

---

## 4. State management

### 4.1 Server state — TanStack Query

Query keys are tuples scoped on the natural cache axis:

| Key | Source | Cadence |
|---|---|---|
| `["health"]` | TopBar | 30 s pulse |
| `["diagnostics", "health"]` | DiagnosticsPage | 15 s pulse |
| `["diagnostics", "prompts"]` | DiagnosticsPage | 60 s stale |
| `["diagnostics", "fingerprints", days]` | DiagnosticsPage | 60 s stale |
| `["tickers"]` | StockSelector / Universe | 1 h stale |
| `["sessionTrace", sessionId]` | Workspace tabs, Sessions detail | 60 s stale |
| `["resultsIndex"]` | Sessions index, Universe rows, History tab | 30 s stale |
| `["backtests"]` | Backtest Lab list + NewBacktest poller | 5 s while pending |
| `["backtest-detail", id]` | BacktestDetailPage | 60 s |
| `["backtest-compare", ticker]` | BacktestComparePage | default |
| `["rl-status"]` | Diagnostics, NewBacktestPage badge, Footer | 60 s |
| `["rl-decisions", ticker, sessionId, limit]` | BacktestDetail RL panel | 60 s |
| `["memory", agent, ticker, q, k, minSim]` | Workspace > Memory | default |
| `["reflections", ticker, limit]` | Workspace > Memory | default |
| `["config"]` | SettingsPage, footer model labels | 60 s |

**Mutations** invalidate the corresponding read keys:

- `runPrediction` → `["resultsIndex"]`
- `runBacktest` / `runBtBenchmark` → `["backtests"]`
- `updateConfig` → `["config"]`, `["diagnostics", "health"]`

### 4.2 App state — Zustand

`store/appStore.ts` persists to `localStorage` under `egx-dashboard-state`:

```ts
{
  selectedTicker: string,   // workspace + run + universe + backtest all share this
  initialCapital: number,   // backtest wizard default
}
```

Locale and disclaimer-ack state live elsewhere (Zustand-less external stores) so they
don't trigger React subscribers across the whole app:

- `lib/i18n.ts` — single locale value subscribed via `useSyncExternalStore`
- `components/layout/Disclaimer.tsx` — local-storage flag, read once at mount

### 4.3 WebSocket state — `useAgentStream`

`hooks/useAgentStream.ts` owns the live-run lifecycle. Status machine:

```
idle → connecting → streaming → complete
                          ↘  → error
                          ↘  → closed (cancelled)
```

State held:
- `events: AgentUpdate[]` — every node update, in order
- `lastEvent: AgentUpdate | null` — pointer to the most recent
- `lastMessageAt: number | null` — `Date.now()` of the last frame, drives the staleness ribbon
- `nodeStatuses: Map<string, status>` — per-node status, fed to `AgentTimeline`
- `error: string | null`

`AgentTimeline` is a derived view: 13 hard-coded nodes lit up by the map. If a node is
referenced in `events` but isn't in the timeline list (defensive), it's silently dropped.

---

## 5. WebSocket protocol — client side

Backend protocol lives in `server/api_server.py:1085+`. The client treats the stream as
best-effort:

1. `useAgentStream.start({ticker, trade_date, selected_analysts, max_debate_rounds, max_risk_rounds})`
   opens a new `WebSocket` to `analyzeStreamUrl()` (derives from `VITE_API_BASE` or
   `window.location` + `/api/analyze`).
2. Each frame is JSON-parsed to an `AgentUpdate`:
   ```ts
   { type: "agent_update" | "complete" | "error",
     node: string,
     status: "idle" | "in_progress" | "completed" | "error",
     state_keys: string[],
     data: Record<string, unknown>,
     timestamp: string }
   ```
3. On `type === "complete"` we transition to `complete` and stop accepting frames.
4. On any send/recv error or non-1000 close we transition to `error` and surface the
   close-code/reason to the UI ribbon.
5. The user can `cancel()` at any time — we send a close frame and transition to
   `closed`. The backend tears down the LangGraph run via the same close.

There is **no client-side reconnect**. A dropped run is irrecoverable for now; the run
is re-launched manually. (Server-side seq + replay is on the roadmap — see plan §2.8.)

---

## 6. Internationalization

`lib/i18n.ts` is the entire i18n layer:

- Two flat dicts (`en`, `ar`), namespaced by dots (e.g. `workspace.tab.reasoning`).
- One external store, subscribed via `useSyncExternalStore`. `useLocale()` re-renders
  on switch; `useT()` returns a memo-stable lookup closure.
- `setLocale("ar")` flips `<html dir="rtl" lang="ar">` and persists to `localStorage`.
- Missing keys: AR fallback → EN → the raw key. So a broken key surfaces but doesn't
  blank the UI.
- Numbers stay LTR via the `.num` utility (institutional convention — chart axes, money
  values, fingerprints).
- Charts wrap themselves in `<div className="ltr-island">` so `lightweight-charts`
  layouts don't flip under RTL.

There's no message-format / pluralization layer. Counts are interpolated by hand
(`label.replace("{n}", String(count))`) — fine at the dashboard's scale.

---

## 7. Performance

- Route-level code splitting is **not** yet enabled. All pages compile into a single
  ~900 kB / ~260 kB-gzipped bundle. This is acceptable for the analyst audience
  (single-user desktop tool), but the Vite chunk warning will keep firing until we add
  `React.lazy` per route. Suggested next-touch: see `extending.md`.
- Trade tables and session lists are paginated client-side (50 rows / page, plain
  `slice`); virtualization via `react-window` is installed but not currently wired
  (the row counts are too small to justify it).
- `lightweight-charts` instances are torn down on unmount via the cleanup callback in
  `useEffect`.

---

## 8. Error surfacing

Three layers, in order:

1. **Per-query empty states** — every `useQuery` consumer renders an
   `<EmptyState>` when the response is empty or the source is `"none"`. This
   is the dominant case (degraded backend, missing audit data).
2. **Toasts** — mutations show success/failure toasts via `sonner`.
3. **Global ErrorBoundary** — `components/ErrorBoundary.tsx` wraps the route
   tree in `main.tsx`. Catches render-time crashes only; query errors don't
   escape Query's internal handling.

There is no client-side telemetry / Sentry yet. A 401/403 from the API would surface as
a toast or empty state — auth doesn't exist server-side (MEMORY.md §E), so the dashboard
makes no attempt to refresh tokens.
