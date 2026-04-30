# EGX Multi-Agent Stock Prediction System — Full Project Context

> **Purpose of this file:** Give any Claude chat complete knowledge of the project so it can help without needing the zip file. Read this before anything else.

---

## 1. Project Overview

This is a **production-level, multi-agent AI system built specifically for the Egyptian Stock Exchange (EGX)**. It generates trading recommendations (BUY / SELL / HOLD) by running a LangGraph-based pipeline of specialized LLM agents. It is also capable of backtesting against historical data and comparing an LLM strategy vs a classical technical baseline, with results visualized in a React dashboard.

**Market:** Egyptian Exchange (EGX) — tickers use `.CA` suffix (e.g., `COMI.CA`, `EAST.CA`, `HRHO.CA`)  
**Currency:** Egyptian Pound (EGP)  
**LLM Backend:** Groq API (OpenAI-compatible) — models: `llama-3.3-70b-versatile` (deep) and `llama-3.1-8b-instant` (quick)  
**Data Sources:** Yahoo Finance (yfinance), EODHD, EGXpy, local CSV files, Google News RSS  

---

## 2. High-Level Architecture

```
User / Dashboard
      │
      ▼
FastAPI Backend (server/api_server.py)
      │
      ├── TradingAgentsGraph (tradingagents/graph/trading_graph.py)
      │         │
      │         ├── Market Analyst        (RSI, MACD, Bollinger Bands, SMA)
      │         ├── Fundamentals Analyst  (EGX CSV data: income, balance, ratios)
      │         ├── News Analyst          (Arabic + English news, RSS, NewsAPI)
      │         ├── Social Media Analyst  (Twitter, Telegram, Reddit — Egyptian dialect)
      │         │
      │         ├── Bull Researcher  ──┐
      │         ├── Bear Researcher  ──┤── Debate → Research Manager judge
      │         │                      └──────────────────────────────────────┐
      │         ├── Trader Agent (forms investment plan)                      │
      │         │                                                              ▼
      │         ├── Risky Risk Analyst ──┐                          Final Trade Decision
      │         ├── Safe Risk Analyst  ──┤── Risk debate → Risk Manager judge
      │         └── Neutral Risk Analyst ─┘
      │
      └── Backtesting Engine (scripts/backtester.py)
                 │
                 └── Backtrader Benchmark (scripts/bt_benchmark.py)

React Dashboard (dashboard/src/)
      └── BacktestPage → MetricsGrid + EquityCurveChart + TradeLogTable + ComparisonTable
```

**LangGraph Flow:** Analysts run in parallel → Researchers debate → Trader proposes → Risk team debates → Final decision

---

## 3. Configuration

### `tradingagents/default_config.py`
Central configuration dictionary `DEFAULT_CONFIG`. Key fields:

| Key | Value / Purpose |
|---|---|
| `llm_provider` | `"openai"` — uses OpenAI-compatible library pointing to Groq |
| `deep_think_llm` | `"llama-3.3-70b-versatile"` — complex reasoning tasks |
| `quick_think_llm` | `"llama-3.1-8b-instant"` — fast responses |
| `backend_url` | `"https://api.groq.com/openai/v1"` — Groq endpoint |
| `target_market` | `"EGX"` — activates EGX-specific logic throughout |
| `trading_currency` | `"EGP"` |
| `long_only` | `True` — no short selling (EGX regulation) |
| `allow_short_selling` | `False` |
| `allow_leverage` | `False` |
| `daily_price_limit_pct` | `0.10` — EGX ±10% circuit breaker |
| `data_vendors` | `{core_stock_apis: yfinance, technical_indicators: yfinance, fundamental_data: yfinance, news_data: local}` |
| `auto_refresh_fundamentals` | `True` — auto-refreshes CSV data if older than 90 days |
| `trading_hours` | `10:00–14:30 (EGT, UTC+2)` |

**API Keys loaded via `load_dotenv()` from `.env` file:**
- `GROQ_API_KEY` — for Groq LLM
- `OPENAI_API_KEY` — required by langchain-openai (point to Groq endpoint)
- `EODHD_API_KEY` — for EODHD stock data
- `GOOGLE_API_KEY` — Google Gemini (backup LLM)

### `.env.example`
Copy to `.env` and fill in values. Required keys: `GROQ_API_KEY`, `OPENAI_API_KEY`, `EODHD_API_KEY`, `GOOGLE_API_KEY`, `ALPHA_VANTAGE_API_KEY`.

---

## 4. Core Python Package: `tradingagents/`

### 4.1 Graph Layer (`tradingagents/graph/`)

| File | Purpose |
|---|---|
| `trading_graph.py` | **Main orchestrator class `TradingAgentsGraph`.** Initializes all LLMs, memory stores, agent nodes, and tool nodes. The `propagate(ticker, date)` method runs the full pipeline and returns `(final_state, signal)`. Logs state to `eval_results/`. Also triggers EGX data freshness checks before running. |
| `setup.py` | `GraphSetup` class — wires all agents into the LangGraph StateGraph. Defines edges and which analysts are active. |
| `propagation.py` | `Propagator` class — creates the initial agent state dict and graph run args. |
| `conditional_logic.py` | Router functions — determines which node runs next based on state (e.g., whether debate continues or terminates). |
| `reflection.py` | `Reflector` class — after a trade, reflects on outcomes and updates agent memories (bull, bear, trader, judge, risk). |
| `signal_processing.py` | `SignalProcessor` — extracts `BUY/SELL/HOLD` from the full multi-paragraph final decision string. |

### 4.2 Agents (`tradingagents/agents/`)

#### Analysts (`agents/analysts/`)

| File | Purpose |
|---|---|
| `market_analyst.py` | **Technical Analysis Agent.** Fetches OHLCV + indicators (RSI, MACD, Bollinger Bands, SMA50). Computes `EGX_DAILY_INDICATORS`. Uses `parse_technical_signals()` and `determine_trend_direction()`. Returns `market_report` (markdown string). |
| `fundamentals_analyst.py` | **Fundamentals Analyst Agent.** Reads local EGX CSV files via `get_egx_fundamentals`, `get_egx_income`, `get_egx_balance`, `get_egx_ratios`. Constructs DCF context, P/E, ROE, debt ratios. Returns `fundamentals_report`. |
| `news_analyst.py` | **News Analyst Agent.** Fetches Arabic + English EGX news via `get_egx_company_news` and `get_egx_market_news`. Returns `news_report`. Supports bilingual analysis. |
| `social_media_analyst.py` | **Social Media Sentiment Agent.** Pulls posts from Twitter, Telegram, Reddit tuned for Egyptian Arabic dialect (العامية المصرية). Detects hype signals like "بامب" (pump), "تجميع" (accumulation). Returns `sentiment_report`. |

#### Researchers (`agents/researchers/`)

| File | Purpose |
|---|---|
| `bull_researcher.py` | Argues the bullish investment case using analyst reports. Uses `bull_memory` for past decisions. |
| `bear_researcher.py` | Argues the bearish case. Uses `bear_memory`. |

#### Managers (`agents/managers/`)

| File | Purpose |
|---|---|
| `research_manager.py` | Research judge — listens to bull/bear debate and issues a decision via `investment_debate_state`. |
| `risk_manager.py` | **Risk Management + Portfolio Manager.** Contains `EGX_RISK_LIMITS` dict with EGX-specific risk constraints. Runs a 3-way debate (aggressive, conservative, neutral debators) then issues final veto or approval. Can override any BUY into HOLD if risk thresholds breached. |

#### Risk Debators (`agents/risk_mgmt/`)

| File | Purpose |
|---|---|
| `aggresive_debator.py` | Risk personality that argues for taking the trade. |
| `conservative_debator.py` | Risk personality that argues for caution. |
| `neutral_debator.py` | Risk personality that provides balanced view. |

#### Trader (`agents/trader/`)

| File | Purpose |
|---|---|
| `trader.py` | Trader agent — takes research manager's decision and forms a detailed investment plan with position sizing. Uses `trader_memory`. |

#### Utilities (`agents/utils/`)

| File | Purpose |
|---|---|
| `agent_states.py` | Defines TypedDict state schemas: `AgentState`, `InvestDebateState`, `RiskDebateState`. These are the shared state objects passed through LangGraph. |
| `agent_utils.py` | Abstract tool wrappers: `get_stock_data`, `get_indicators`, `get_fundamentals`, `get_news`, etc. These call the `interface.py` router. |
| `memory.py` | `FinancialSituationMemory` class — stores past agent decisions and reflections in a vector store. Agents use this to learn from previous trades. |
| `llm_failover.py` | LLM failover decorator — if primary Groq model fails, tries backup models automatically. |
| `scoring.py` | Confidence score aggregation logic — combines per-agent scores `[-1.0, 1.0]` into a unified score. |
| `fundamental_data_tools.py` | `@tool`-decorated functions for the fundamentals agent: `get_egx_fundamentals`, `get_egx_income`, `get_egx_balance`, `get_egx_ratios`. These call `local.py`. |
| `news_data_tools.py` | `@tool`-decorated functions for the news agent: `get_egx_company_news`, `get_egx_market_news`. |
| `social_media_tools.py` | `@tool`-decorated functions for social media data. |
| `core_stock_tools.py` | `@tool`-decorated wrappers for stock price/OHLCV data. |
| `technical_indicators_tools.py` | `@tool`-decorated wrapper for technical indicator data. |

### 4.3 Data Layer (`tradingagents/dataflows/`)

#### Core Routing

| File | Purpose |
|---|---|
| `interface.py` | **Tool routing layer.** `route_to_vendor(tool_name, *args)` looks up `DEFAULT_CONFIG["tool_vendors"]` or `["data_vendors"]` and calls the right provider. `TOOLS_CATEGORIES` and `VENDOR_LIST` define what's supported. |
| `gateway.py` | **DataGateway class — central data orchestrator.** Single entry point for all data. Implements: cache lookup → primary fetch → fallback chain → Pydantic validation → quality logging. Never raises to callers. Fallback chains: OHLCV: yfinance → EODHD → egxpy; Technical: TradingView → stockstats; News: NewsAPI → RSS → Google → local CSV. |
| `config.py` | Simple in-memory config store (`get_config()`, `set_config()`). Used by `interface.py`. |

#### Data Providers

| File | Purpose |
|---|---|
| `y_finance.py` | **Yahoo Finance provider (primary).** `get_YFin_data_online(ticker, start, end)` — downloads OHLCV. Also contains `get_stock_stats_indicators_window()` for computing technical indicators from yfinance data. Very large file (27KB). |
| `eodhd.py` | EODHD stock data provider. Requires `EODHD_API_KEY`. Used as fallback for OHLCV. |
| `egxpy_wrapper.py` | Wrapper around the `egxpy` library — EGX-native data source. Checks if installed and available. |
| `local.py` | **EGX local data provider (54KB — largest file in project).** Reads fundamental financial data from CSV files stored in `tradingagents/dataflows/data/`. Functions: `get_egx_fundamentals_summary`, `get_egx_news_combined`, income/balance/ratio readers. Handles `period_end_date` parsing and missing fields gracefully. This is the primary data source for the Fundamentals Analyst. |
| `google.py` | Google Search wrapper for news fallback. |
| `tradingview_provider.py` | TradingView-TA library wrapper. Gets real-time technical signals and prices without API key. |
| `stockstats_utils.py` | Stockstats local indicator calculation fallback. |
| `googlenews_utils.py` | Google News RSS feed parser — fallback for news data. |
| `reddit_utils.py` | Reddit API wrapper (`praw`) for social media posts. |
| `egx_data_refresh.py` | Auto-refresh system for EGX fundamentals. `is_fundamentals_stale(date, max_age_days)` checks CSV age; `fetch_and_refresh_egx_data(date)` triggers PDF parsing pipeline. |
| `cache_manager.py` | `CacheManager` — disk-based cache using `diskcache`. TTL-aware. Used by `DataGateway`. |
| `retry_engine.py` | `retry_api_call()` decorator and `fetch_with_fallback()` — tries a list of provider callables in order, returns first success. |
| `schemas.py` | Pydantic schemas: `StockDataResponse`, `TechnicalSignals`, `NewsResponse`, `DataQualityReport`. Used for validating provider responses. |
| `utils.py` | Miscellaneous data utility functions (date parsing helpers, etc.). |

#### News Providers (`dataflows/news_providers/`)

| File | Purpose |
|---|---|
| `aggregator.py` | `fetch_aggregated_news(ticker, days, max_articles)` — orchestrates NewsAPI → RSS → Google News fallback chain and merges results. |
| `newsapi_source.py` | NewsAPI.org integration. Requires `NEWS_API_KEY`. |
| `rss_source.py` | EGX-specific RSS feed parser. Reads from multiple Arabic and English financial news RSS feeds. Falls back gracefully. |

#### Social Media Sources (`dataflows/social_media_sources/`)

| File | Purpose |
|---|---|
| `aggregator.py` | `get_social_media_data(ticker, date, days)` — collects posts from all platforms, returns `SocialMediaData` object. |
| `sentiment_engine.py` | `analyze_social_sentiment(social_data)` — main Arabic + English sentiment analysis engine. Handles Egyptian financial slang dictionary. Returns `SentimentResult` with scores, buzz, momentum, hype flags. |
| `cached_data.py` | Fallback cached social media data — used when live platforms fail. |
| `schema.py` | Pydantic schemas for social media: `SocialPost`, `SocialMediaData`, `SentimentResult`. |
| `twitter_source.py` | Twitter/X scraper for EGX ticker mentions. |
| `telegram_source.py` | Egyptian financial Telegram channel reader. |
| `reddit_source.py` | Reddit posts reader (Arabic investing communities). |
| `stocktwits_source.py` | StockTwits integration (limited EGX coverage). |

#### Utility Layer (`tradingagents/utils/`)

| File | Purpose |
|---|---|
| `sentiment_engine.py` | Standalone sentiment engine (VADER + custom Arabic dictionary). |
| `text_preprocessor.py` | Arabic text normalization: removes diacritics, normalizes hamza, handles Egyptian dialect transliteration. |

---

## 5. Backtesting System (`scripts/`)

### `scripts/backtester.py`
**LLM Multi-Agent Backtesting Engine.** The main backtesting loop.

- Runs `TradingAgentsGraph` on historical dates for a given ticker
- **EGX cost model** applied to every trade:
  - Brokerage: 0.175%, Stamp Duty: 0.005%, FRA Fee: 0.009% → **~0.189% total per side**
  - Slippage: 0.1% (normal) or 0.5% (low-liquidity)
- **T+2 Settlement:** SELL proceeds held for 2 business days before reuse as cash
- **Circuit Breaker:** If price moves ±10% vs previous close, skip agent run for that date
- **Benchmark tracking:** fetches `^EGX30` index for Alpha calculation
- **Metrics:** Total Return, Benchmark Return, Alpha, Win Rate, Max Drawdown, Sharpe, Calmar, Total Commissions
- **Output:** Saves `backtest_results/report_{ticker}_{timestamp}.json` and CSV trade log

```bash
# CLI usage
python scripts/backtester.py --ticker COMI.CA --start 2023-10-01 --end 2024-01-01 --interval 20
```

### `scripts/bt_benchmark.py`
**Backtrader Classical Technical Strategy.** Zero LLM calls — pure rule-based baseline.

- `EGXTechnicalStrategy`: mirrors the exact indicator logic from `market_analyst.py`
- **Indicators:** RSI(14), MACD(12/26/9), Bollinger Bands(20), SMA(50)
- **Entry (BUY):** ≥2 of 3 bullish: RSI < 35, MACD > 0, Close < lower BB
- **Exit (SELL):** ≥2 of 3 bearish: RSI > 65, MACD < 0, Close > upper BB
- Same EGX cost model as `backtester.py` (0.189% commission + 0.1% slippage)
- Outputs `backtest_results/bt_report_{ticker}_{timestamp}.json` with daily equity curve

```bash
python scripts/bt_benchmark.py --ticker COMI.CA --start 2023-10-01 --end 2024-01-01
```

### `scripts/benchmark_comparison.py`
Side-by-side comparison utility. Reads two JSON reports (LLM + BT), prints formatted table with per-metric winner and overall verdict: `LLM OUTPERFORMS` / `Classical OUTPERFORMS` / `Mixed`.

### `scripts/run_real_backtests.py`
Runs both engines (LLM + Backtrader) for all three EGX tickers (COMI.CA, EAST.CA, HRHO.CA) and prints a final summary table.

### `scripts/parse_egx_annex5.py`
PDF parser for EGX Annex 5 quarterly reports. Extracts financial data and updates the local CSV files used by the Fundamentals Analyst.

### `scripts/system_validation.py`
End-to-end system validation script — tests all data providers and the full agent pipeline.

---

## 6. FastAPI Backend (`server/`)

### `server/api_server.py` (34KB)
**The full REST + WebSocket backend.** Runs with: `python3 -m uvicorn server.api_server:app --port 8000 --reload`

#### REST Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Health check + EGX tools availability |
| GET | `/api/config` | Current system config (API keys redacted) |
| PUT | `/api/config` | Update config (model, vendor, etc.) |
| GET | `/api/stock/{ticker}` | Fetch OHLCV data for a ticker |
| GET | `/api/indicators/{ticker}` | Fetch technical indicators |
| GET | `/api/fundamentals/{ticker}` | Fundamental data (EGX CSV or vendor) |
| GET | `/api/news/{ticker}` | News articles |
| GET | `/api/results` | List all past analysis sessions (from `audit_logs/`) |
| GET | `/api/results/{ticker}/{session_id}` | Detailed session log (JSONL + Markdown) |
| GET | `/api/backtests` | List all backtest reports (LLM + BT) from `backtest_results/` |
| GET | `/api/backtests/{session_id}` | Full backtest report JSON |
| GET | `/api/backtests/compare/{ticker}` | Side-by-side LLM vs BT data for a ticker |
| POST | `/api/backtests/run` | Trigger LLM backtest (background task) |
| POST | `/api/backtests/run-bt` | Trigger Backtrader benchmark (background task) |
| POST | `/api/test/random-egx` | Run quick EGX stock analysis, save to audit_logs |
| GET | `/api/test/egx-tickers` | List all available EGX tickers |

#### WebSocket
| Endpoint | Purpose |
|---|---|
| `WS /api/analyze` | Real-time streaming analysis — client sends `{ticker, trade_date, selected_analysts}`, server streams agent update events as they complete |

---

## 7. React Dashboard (`dashboard/`)

**Stack:** React 19, TypeScript, Vite, TanStack Query, React Router, `lightweight-charts` v5, Lucide Icons

**Run:** `cd dashboard && npm install && npm run dev` → http://localhost:5173/  
**Proxy:** `/api` requests proxied to `http://localhost:8000` (configured in `vite.config.ts`)

### Source Files (`dashboard/src/`)

| File | Purpose |
|---|---|
| `main.tsx` | React root — wraps app with `QueryClientProvider` (TanStack Query) |
| `App.tsx` | `BrowserRouter` + top navbar with route to BacktestPage |
| `index.css` | Dark trading-terminal theme — CSS variables, card styles, badge colors, tables |
| `types.ts` | TypeScript interfaces for all API responses: `BacktestSession`, `BacktestDetail`, `CompareResult`, `MetricsMap` |
| `api.ts` | Typed fetch functions for all 5+ endpoints — used by TanStack Query hooks |

### Pages (`dashboard/src/pages/`)

| File | Purpose |
|---|---|
| `BacktestPage.tsx` | **Main and only page.** Session picker dropdown, "Run Backtest" expandable form (LLM or Classical run), tabbed content area (Equity Curve / Trade Log / LLM vs Classical) |

### Components (`dashboard/src/components/`)

| File | Purpose |
|---|---|
| `MetricsGrid.tsx` | 9 metric cards showing Total Return, Sharpe, Win Rate, Max Drawdown, etc. Green/red color coding based on performance |
| `EquityCurveChart.tsx` | Portfolio equity curve + drawdown chart using `lightweight-charts`. Three series: LLM (teal), EGX30 benchmark (grey dashed), BT classical (blue dotted) |
| `TradeLogTable.tsx` | Sortable trade log table — normalizes field names between LLM and BT report formats |
| `ComparisonTable.tsx` | Side-by-side LLM vs Classical metrics table. Per-row winner annotation + verdict banner (LLM wins / BT wins / Mixed) |

---

## 8. CLI (`cli/`)

| File | Purpose |
|---|---|
| `main.py` | Interactive terminal CLI (43KB). Lets user pick ticker, date, LLMs, debate rounds, and shows live agent progress. Uses Rich for terminal UI. |
| `utils.py` | CLI display helpers (tables, spinners, coloring). |
| `models.py` | CLI-specific data models. |

---

## 9. Root-Level Files

| File | Purpose |
|---|---|
| `run_egx_prediction.py` | **Quick EGX prediction script.** Fetches 30 days of yfinance data, computes SMA/RSI, calls Groq LLM with multi-perspective prompt (Bull/Bear/Neutral), and returns structured JSON. `analyze_ticker_for_api(ticker)` is called by the `/api/test/random-egx` endpoint. |
| `main.py` | Quick demo entry point — runs full EGX analysis for a ticker/date. Usage: `python main.py [TICKER] [DATE]`. Defaults to `COMI.CA` on `2024-01-15`. |
| `pyproject.toml` | Python project metadata and dependencies (uses `uv` for package management). |
| `uv.lock` | Full locked dependency tree for `uv` package manager (903KB). |
| `.python-version` | Pin Python version (e.g., 3.13). |
| `.env` | API keys (not committed to git). Copy from `.env.example` and fill in. |
| `.env.example` | Template for environment variables — `GROQ_API_KEY`, `OPENAI_API_KEY`, `EODHD_API_KEY`, `GOOGLE_API_KEY`, `ALPHA_VANTAGE_API_KEY`. |
| `CLAUDE.md` | Instructions for Claude — architecture overview, coding standards, data flow. |
| `PROJECT_CONTEXT.md` | This file — full project reference for AI assistants. |

---

## 10. Agent Documentation (`agent_docs/`)

Internal design docs — only read when modifying the specific component:

| File | Purpose |
|---|---|
| `backend.md` | Backend architecture decisions |
| `data_pipeline.md` | Data pipeline design and vendor priority |
| `sentiment_analysis.md` | Sentiment agent design — Arabic dialect handling, hype detection |
| `system_design.md` | Overall system design rationale |

---

## 11. Tests (`tests/`)

| File | Purpose |
|---|---|
| `test_social_media_analyst.py` | 23KB comprehensive unit tests for the Social Media Analyst agent |

---

## 12. Key Data Files

### `tradingagents/dataflows/data/` (CSV files — not shown in tree)
Contains local fundamental financial data for EGX companies. Used by `local.py`. Updated by `scripts/parse_egx_annex5.py`. Structure per company:
- `{TICKER}_income.csv` — income statement (revenue, net income, EBITDA, EPS)
- `{TICKER}_balance.csv` — balance sheet (assets, liabilities, equity)
- `{TICKER}_ratios.csv` — financial ratios (P/E, ROE, debt-to-equity, current ratio)

### `backtest_results/` (generated at runtime)
- `report_{ticker}_{timestamp}.json` — LLM backtest report
- `bt_report_{ticker}_{timestamp}.json` — Backtrader classical report
- `trades_{ticker}_{timestamp}.csv` — trade log CSV

### `audit_logs/` (generated at runtime)
- `{ticker}/audit_log.jsonl` — JSONL log of analysis sessions
- `{ticker}/audit_summary.md` — markdown summary of last analysis

### `eval_results/` (generated at runtime)
- `{ticker}/TradingAgentsStrategy_logs/full_states_log_{date}.json` — full agent state for each run

---

## 13. How to Run

### Backend
```bash
cd Graduation-Project-main-2
python3 -m uvicorn server.api_server:app --port 8000 --reload
```

### Frontend Dashboard
```bash
cd dashboard
npm install
npm run dev
# Open http://localhost:5173
```

### Single LLM Backtest
```bash
python scripts/backtester.py --ticker COMI.CA --start 2023-10-01 --end 2024-01-01 --interval 20
```

### Single Classical Backtest (no API key needed)
```bash
python scripts/bt_benchmark.py --ticker COMI.CA --start 2023-10-01 --end 2024-01-01
```

### Full Evaluation (all tickers, both engines)
```bash
python scripts/run_real_backtests.py
```

### CLI Interface
```bash
python -m cli.main
```

### Quick Stock Analysis
```bash
python run_egx_prediction.py COMI.CA
```

---

## 14. Known Issues & Important Notes

1. **LLM Run in dashboard gives Error 500** — requires valid Groq API key. Key is hardcoded in `default_config.py` but may have expired. Update `GROQ_API_KEY` there or in `.env`.

2. **Classical Run (Backtrader) works without any API key** — only needs internet access for yfinance.

3. **Mubasher scraper is fragile** — CSS selectors on mubasher.info change frequently. If real-time price fails, system falls back to yfinance delayed price.

4. **No `requirements.txt`** — project uses `uv` package manager. Install with `uv sync` if `uv` is installed. Otherwise use `python3 -m pip install fastapi uvicorn langchain-openai langchain-anthropic langchain-google-genai langgraph yfinance backtrader pandas numpy diskcache pydantic`.

5. **Egyptian Arabic sentiment** — custom dictionary handles financial slang: "بامب" (pump), "تجميع" (accumulation), "هبوط" (drop), etc. Standard NLP libraries misclassify these.

6. **EGX T+0 not T+2** concern — while the backtester simulates T+2, actual EGX settlement has varied. The 2-day lag is a conservative implementation choice.

7. **Agent output format** — all agents must return a standardized confidence score `[-1.0 to 1.0]` and a reasoning string. Risk manager can veto any BUY decision regardless of scores.

---

## 15. EGX Tickers Supported

From `tradingagents/default_config.py` (`EGX_TICKERS` constant):
- `COMI.CA` — Commercial International Bank (CIB)
- `EAST.CA` — Eastern Company
- `HRHO.CA` — Hermes Holding
- `SWDY.CA` — Elsewedy Electric
- `EFIH.CA` — EFG Hermes
- `OCDI.CA` — Orascom Construction
- *(and more — see `EGX_TICKERS` in `tradingagents/default_config.py` for full list)*

All tickers use the `.CA` suffix (Cairo exchange) for Yahoo Finance compatibility.
