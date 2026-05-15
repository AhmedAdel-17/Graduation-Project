# EGX Trading Intelligence — Dashboard

Production-grade dashboard for the EGX Multi-Agent Stock Prediction System.
Built with **Vite + React 19 + TypeScript**, **TailwindCSS**, **Zustand**,
**TanStack Query**, **react-router-dom**, and **lightweight-charts**.

## Features

1. **Prediction** (`/`) — Pick any EGX ticker, run the multi-agent pipeline,
   and view the signal, confidence, target/stop prices, indicators, and the
   bull / bear / judge thesis alongside a candlestick history + projection chart.
2. **Backtesting** (`/backtest`) — User-driven: choose stock, horizon, and
   capital. Shows profit/loss, ROI, Sharpe, drawdown, win-rate, and equity curve.
3. **Advanced Backtesting** (`/backtest/advanced`) — Script execution with
   live runtime log, configurable date range/interval/analysts, and a dual
   trigger for the LLM pipeline and the classical Backtrader benchmark.

## Prerequisites

- Node.js 18+
- Python 3.10+ with the project's existing environment (FastAPI backend)

## Run locally

```bash
# 1. Start the backend (from project root — see project README)
uvicorn server.api_server:app --reload
# Backend listens on http://localhost:8000

# 2. Start the dashboard (from this directory)
npm install    # first time only
npm run dev
# Dashboard listens on http://localhost:5173
```

The Vite dev server proxies `/api/**` to `http://localhost:8000`, so CORS
is a non-issue in development.

## Environment

To point at a different backend:

```bash
# .env.local
VITE_API_BASE=https://my-api.example.com/api
```

## Architecture

```
src/
├── components/
│   ├── ui/         Button, Card, Input, Select, Badge, Skeleton, Spinner,
│   │               StockSelector, MetricsCard, EmptyState
│   ├── layout/     AppShell, Sidebar, TopBar, MobileNav
│   └── charts/     PriceChart (candlestick+volume), EquityCurve
├── features/
│   ├── prediction/ PredictionPage, PredictionCard, SignalBadge, ThesisPanel
│   ├── backtest/   BacktestPage, BacktestForm, MetricsGrid, BacktestResult
│   └── advanced/   AdvancedBacktestPage, LogConsole
├── services/api/   client (fetch wrapper), endpoints, types
├── hooks/          useTickers, usePrediction, useStockData, useBacktest
├── store/          appStore (Zustand + persist)
└── lib/            utils (cn, currency / percent / number formatters)
```

## Backend endpoints consumed

| Endpoint | Method | Used in |
|---|---|---|
| `/api/health` | GET | TopBar (status pill) |
| `/api/test/egx-tickers` | GET | Stock selector |
| `/api/test/random-egx` | POST | Prediction page |
| `/api/stock/{ticker}` | GET | Prediction chart |
| `/api/backtests` | GET | Backtest list, polling |
| `/api/backtests/compare/{ticker}` | GET | Backtest results |
| `/api/backtests/run` | POST | Run multi-agent backtest |
| `/api/backtests/run-bt` | POST | Run classical benchmark |

## Scripts

```bash
npm run dev      # Vite dev server + HMR
npm run build    # Type-check + production build → dist/
npm run preview  # Preview production build
npm run lint     # ESLint
```

## Notes on the stack

The project brief mentioned Next.js. This repo was already scaffolded with
Vite + React 19 + TypeScript (with Zustand, TanStack Query, lightweight-charts,
and react-router-dom already installed) — we built on that rather than
rewriting the scaffolding. For a pure-SPA dashboard with no server-rendered
pages, Vite is a leaner choice and delivers the same developer experience.
Migrating to Next.js is straightforward if SSR/ISR is needed later: the
feature folders, services/api layer, and hooks are framework-agnostic.
