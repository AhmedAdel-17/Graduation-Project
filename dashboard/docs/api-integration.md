# API Integration

> Every REST endpoint the dashboard talks to, the typed binding, and the cache
> rules around it. Backend source lives in `server/api_server.py`.

---

## 1. Client wrapper

`services/api/client.ts` exposes `api.get / post / put / delete`, all of which:

1. Resolve URLs against `VITE_API_BASE` (defaults to `/api` via the Vite proxy).
2. Add `Content-Type: application/json` for bodies.
3. Throw a typed `ApiError` with `status` + `body` on non-2xx responses, which
   TanStack Query bubbles up as `error.message`.

Auth is **not wired**. The wrapper has a place for `Authorization` headers when
JWT lands (MEMORY.md §E), but right now every endpoint is anonymous and the
dashboard runs against a permissive CORS origin (`allow_origins=["*"]`).

WebSocket URLs are derived in `services/api/wsClient.ts` from the same `VITE_API_BASE`
(stripping the trailing `/api`, swapping http→ws, appending `/api/analyze`).

---

## 2. Endpoint catalog

| Endpoint | Method | Binding | Consumer hook |
|---|---|---|---|
| `/health` | GET | `endpoints.health()` | `useHealth()`, TopBar |
| `/config` | GET | `endpoints.config()` | `useConfig()` |
| `/config` | PUT | `endpoints.updateConfig(req)` | `useUpdateConfig()` |
| `/test/egx-tickers` | GET | `endpoints.tickers()` | `useTickers()` |
| `/test/random-egx` | POST | `endpoints.runPrediction(ticker?)` | `useRunPrediction()` |
| `/stock/{ticker}` | GET | `endpoints.stockData(ticker, params?)` | OverviewTab via inline `useQuery` |
| `/results` | GET | `endpoints.listResults()` | `useResultsIndex()` |
| `/sessions/{id}/trace` | GET | `endpoints.sessionTrace(id)` | `useSessionTrace()`, `useLatestSessionTrace()` |
| `/memory/{agent}/search` | GET | `endpoints.memorySearch(agent, params?)` | `useAgentMemoryFanout()` |
| `/memory/{agent}/entries` | GET | `endpoints.memoryEntries(agent, params?)` | (not currently surfaced — reserved for "seeded vs learned" view) |
| `/reflections` | GET | `endpoints.reflections(params?)` | `useReflections()` |
| `/backtests` | GET | `endpoints.listBacktests()` | `useBacktests(autoRefetch?)` |
| `/backtests/{id}` | GET | `endpoints.getBacktest(id)` | `useBacktestDetail(id)` |
| `/backtests/compare/{ticker}` | GET | `endpoints.compareBacktests(ticker)` | `useBacktestCompare(ticker)` |
| `/backtests/run` | POST | `endpoints.runBacktest(req)` | `useRunBacktest()` |
| `/backtests/run-bt` | POST | `endpoints.runBtBenchmark(req)` | `useRunBtBenchmark()` |
| `/rl/status` | GET | `endpoints.rlStatus()` | `useRlStatus()` |
| `/rl/decisions` | GET | `endpoints.rlDecisions(params?)` | `useRlDecisions(params)` |
| `/diagnostics/prompts` | GET | `endpoints.diagnosticsPrompts()` | `usePrompts()` |
| `/diagnostics/fingerprints` | GET | `endpoints.diagnosticsFingerprints(days?)` | `useFingerprints(days)` |
| `/analyze` | WS | `analyzeStreamUrl()` + `openAnalyzeStream()` | `useAgentStream()` |

All POST/PUT requests serialize to JSON. All GETs encode query parameters via
`URLSearchParams`.

---

## 3. Query key conventions

Server-state queries follow a tuple convention:

```
[<feature>, <subkey>, ...<varying axes>]
```

Examples:
- `["sessionTrace", sessionId]`
- `["backtest-detail", sessionId]`
- `["backtest-compare", ticker]`
- `["memory", agent, ticker, q, k, minSim]`
- `["diagnostics", "fingerprints", days]`

This convention keeps invalidation simple — `qc.invalidateQueries({ queryKey: ["diagnostics"] })`
flushes the whole diagnostics namespace; finer-grained invalidations target a single
tuple. Stick to it when adding endpoints.

### 3.1 Stale times

| Feature | Stale time | Reason |
|---|---|---|
| Health | 10–30 s | Cheap and informative |
| Tickers | 1 h | EGX-30 doesn't change |
| Stock data | 60 s (default) | Daily bars; refresh on tab focus |
| Session trace | 60 s | Immutable once written |
| Results index | 30 s | Polls during a run, otherwise idle |
| Backtest list | 5 s while pending, otherwise default | Polled by NewBacktestPage |
| Backtest detail | 60 s | Immutable once a run finishes |
| Memory / reflections | default | Polls when the Memory tab is focused |
| RL status / decisions | 60 s | Flag flips are server-side restarts |
| Diagnostics prompts | 60 s | Server caches with the same TTL |
| Diagnostics fingerprints | 60 s | Aggregated query |
| Config | 60 s | Read-only most of the time |

If you change a stale time, mention it in the hook's doc comment so reviewers know
the trade-off.

---

## 4. Mutations + invalidation

Five mutations exist:

| Mutation | Invalidates |
|---|---|
| `useRunPrediction` | nothing automatically (caller chooses); UniversePage refetches `["resultsIndex"]` after the bulk loop |
| `useRunBacktest` | `["backtests"]` on success |
| `useRunBtBenchmark` | `["backtests"]` on success |
| `useUpdateConfig` | `["config"]`, `["diagnostics", "health"]` |
| (WebSocket `start`) | not a TanStack mutation; manages its own state |

When adding a new mutation, follow the rule: **invalidate every read whose value
might change as a result.** Otherwise you ship stale-by-default UIs.

---

## 5. Error handling

- `ApiError.body` carries the server's JSON error blob when available.
- Hooks pass errors through unchanged — components render them via `EmptyState`,
  `toast.error`, or an inline error banner depending on context.
- Mutations should `try/catch` the `await mutateAsync` call and `toast.error` on
  failure. See `NewBacktestPage.submitLLM` for the canonical pattern.

---

## 6. WebSocket protocol

Frames are JSON. Client and server agree on three message types:

```ts
type WireFrame =
  | { type: "agent_update"; node: string; status: "idle" | "in_progress" | "completed" | "error";
      state_keys: string[]; data: Record<string, unknown>; timestamp: string }
  | { type: "complete"; node: string; status: "completed"; state_keys: string[];
      data: Record<string, unknown>; timestamp: string }
  | { type: "error"; node: string; status: "error";
      state_keys: string[]; data: { error: string }; timestamp: string };
```

The opening client frame is:

```json
{ "ticker": "COMI.CA", "trade_date": "2026-05-15",
  "selected_analysts": ["market", "fundamentals"],
  "max_debate_rounds": 1, "max_risk_rounds": 1 }
```

There is **no seq, no replay, no resume**. A dropped run is lost — see
`architecture.md` §5.

---

## 7. Environment variables

| Var | Default | Effect |
|---|---|---|
| `VITE_API_BASE` | `/api` | REST + WS base. Strip trailing slash. |
| `VITE_GIT_SHA` | `dev` | Surfaced in `SiteFooter` for audit trail |

Set both in `.env.local` for development; CI bakes `VITE_GIT_SHA` from
`$GITHUB_SHA` at build time.
