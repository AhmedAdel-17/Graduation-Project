# EGX Research Console — Dashboard

Bilingual, evidence-first dashboard for the EGX Multi-Agent Stock Prediction
System. Built with **React 19 + Vite + TypeScript + Tailwind**, with TanStack
Query for server state, Zustand for app state, lightweight-charts for price
and equity rendering, and a native WebSocket bridge to the LangGraph agent
stream.

This is an **AI-augmented research console**, not an order-execution system.
Every recommendation must be reviewed by a human PM before any trade is
placed. See the disclaimer ribbon and `agent_docs/dashboard.md` for the
audit-deferred caveat list.

---

## Features

- **Workspace** (`/workspace`) — Tabbed ticker research: Overview · Reasoning
  · Fundamentals · Sentiment · Memory · History. Last agent run rendered
  evidence-first, with model fingerprints and prompt IDs exposed for audit.
- **Live Run** (`/run`) — WebSocket-streamed multi-agent execution, 13-node
  timeline + token-by-token reasoning canvas.
- **Sessions** (`/sessions` · `/sessions/:id`) — Audit history of every past
  graph run, frozen in time.
- **Backtest Lab** (`/backtest` · `/backtest/new` · `/backtest/:runId` ·
  `/backtest/compare`) — LLM strategy vs classical Backtrader benchmark, with
  RL meta-policy drill-down when enabled.
- **Universe** (`/universe`) — EGX-30 browser with sector + liquidity-tier
  filters and bulk quick-predict.
- **Diagnostics** (`/diagnostics`) — `/api/health` payload in full + prompt
  registry + model-fingerprint drift sparkline.
- **Settings** (`/settings`) — `GET`/`PUT /api/config`, locale, EGX risk
  limits, build identity.

Bilingual (EN / AR with full RTL flip). Disclaimer modal + persistent
ribbon. Model + git-SHA footer on every page.

## Prerequisites

- Node 18+
- The project's Python environment (FastAPI backend)

## Run locally

```bash
# From repo root
uvicorn server.api_server:app --reload --port 8000

# From this directory
npm install
npm run dev
# http://localhost:5173
```

Vite proxies `/api/**` to `http://localhost:8000` and forwards
`/api/analyze` as a WebSocket. CORS is not an issue in development.

## Environment

```bash
# .env.local
VITE_API_BASE=/api              # or https://your-api/api in production
VITE_GIT_SHA=$(git rev-parse --short HEAD)
```

`VITE_GIT_SHA` shows up in the footer of every page so the build that
produced a screenshot can be reproduced.

## Scripts

```bash
npm run dev      # Vite dev server + HMR
npm run build    # tsc -b && vite build → dist/
npm run preview  # Preview production build
npm run lint     # ESLint
```

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — stack, state management,
  WS protocol on the client
- [`docs/design-system.md`](docs/design-system.md) — tokens, primitives,
  theming, RTL rules
- [`docs/api-integration.md`](docs/api-integration.md) — endpoint catalog,
  query keys, mutation invalidations
- [`docs/extending.md`](docs/extending.md) — how to add a page, chart,
  hook, locale
- [`docs/onboarding.md`](docs/onboarding.md) — analyst walkthrough +
  manual smoke gauntlet

Backend perspective on the new endpoints + the auth-deferred caveat lives
in [`agent_docs/dashboard.md`](../agent_docs/dashboard.md).

## Backend endpoints consumed

| Endpoint | Method | Hook | Page |
|---|---|---|---|
| `/health` | GET | `useHealth` | TopBar, Diagnostics |
| `/config` | GET / PUT | `useConfig` / `useUpdateConfig` | Settings, Footer |
| `/test/egx-tickers` | GET | `useTickers` | StockSelector |
| `/test/random-egx` | POST | `useRunPrediction` | Universe bulk-run |
| `/stock/{ticker}` | GET | inline | Workspace Overview |
| `/results` | GET | `useResultsIndex` | Sessions, Universe |
| `/sessions/{id}/trace` | GET | `useSessionTrace` | Workspace, Sessions detail |
| `/memory/{agent}/search` · `/entries` · `/reflections` | GET | `useAgentMemoryFanout`, `useReflections` | Workspace > Memory |
| `/backtests` · `/backtests/{id}` · `/backtests/compare/{ticker}` | GET | `useBacktests`, `useBacktestDetail`, `useBacktestCompare` | Backtest Lab |
| `/backtests/run` · `/backtests/run-bt` | POST | `useRunBacktest`, `useRunBtBenchmark` | New Backtest |
| `/rl/status` · `/rl/decisions` | GET | `useRlStatus`, `useRlDecisions` | Diagnostics, Backtest Detail |
| `/diagnostics/prompts` · `/diagnostics/fingerprints` | GET | `usePrompts`, `useFingerprints` | Diagnostics |
| `/analyze` | WS | `useAgentStream` | Live Run |

## Architecture (one paragraph)

Pages compose `<AppShell>` (Sidebar + TopBar + MobileNav + Footer + disclaimer
ribbon) around feature components. Server state is TanStack Query keyed by
the natural axis (`["sessionTrace", id]`, `["backtest-detail", id]`, etc.) —
see [`docs/api-integration.md`](docs/api-integration.md) §3. App state lives
in a tiny Zustand store (`selectedTicker`, `initialCapital`). Locale lives in
a hand-rolled external store consumed via `useSyncExternalStore`. WebSocket
state lives in the `useAgentStream` hook only.

## Status

Built incrementally across PRs 1–10. See `MEMORY.md` and the project plan
file for the audit log of decisions and known limitations.
