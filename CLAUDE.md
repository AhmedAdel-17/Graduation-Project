# CLAUDE.md — Operational Reference

> **Read this first.** This file is the authoritative working reference for any Claude session in this repo. It supersedes README.md (which is the upstream Tauric TradingAgents readme and does NOT describe this fork's EGX state).
>
> **Companion file:** `MEMORY.md` holds the audit findings, known issues, and technical-debt log. Read it before making any non-trivial change.

---

## 1. What this project actually is (honest framing)

**EGX Multi-Agent Stock Prediction System** — a LangGraph-based research workbench that produces analyst-grade BUY/HOLD/SELL theses for Egyptian Exchange (EGX) tickers, with bilingual (Arabic + English) sentiment and EGX-specific risk constraints.

**Positioning:** This is an **AI-augmented research / decision-support tool**, intended to be reviewed by a human PM/trader before any order is placed. It is **not** a live order-execution system, not a robo-advisor, and not a regulator-cleared automated trader. Any UI copy or doc that implies otherwise is wrong and must be corrected.

**Target market:** Egyptian Exchange (EGX). Tickers use the `.CA` suffix (Yahoo Finance convention).
**Currency:** EGP. **Trading hours:** 10:00–14:30 EGT (UTC+2). **Settlement:** T+2.
**Regulatory regime:** Egyptian FRA. Long-only, no short-selling, no leverage, ±10% daily price-limit circuit breaker.

**Maturity:** Research prototype with one production-grade subsystem (Fundamentals Phase 1A/1B). The rest of the stack is functional but has known production blockers — see `MEMORY.md`.

---

## 2. Architecture

```
Entry points (main.py / cli/main.py / server/api_server.py / scripts/backtester.py)
        |
        v
TradingAgentsGraph  (tradingagents/graph/trading_graph.py)
        |
        +--- DataPrefetcher (graph/prefetch.py) - pre-fetches news + social in parallel
        |
        +--- Analyst Team (parallel fan-out)
        |    +-- Market Analyst       -> yfinance OHLCV + RSI/MACD/BB/SMA
        |    +-- Fundamentals Analyst -> 3-stage CoT pipeline on local EGX CSVs
        |    +-- News Analyst         -> RSS / NewsAPI / Google / local CSV (fallback chain)
        |    +-- Social Media Analyst -> v2 pipeline: Apify FB + Reddit + Telegram + sentiment
        |
        +--- Research Team (single-round debate)
        |    +-- Bull Researcher  -> bullish thesis (uses bull_memory)
        |    +-- Bear Researcher  -> bearish thesis (uses bear_memory)
        |    +-- Research Manager -> judges debate, emits investment decision
        |
        +--- Trader Agent -> execution plan with liquidity-aware position sizing
        |
        +--- Risk Management
        |    +-- Deterministic checks  -> position/liquidity/loss/short/leverage (HARD VETO)
        |    +-- Merged Risk Debator   -> 3-perspective LLM risk discussion
        |
        +--- Risk Manager -> final approval/veto, emits `final_trade_decision`
                  |
                  v
        SignalProcessor (regex extraction, no LLM) -> BUY / SELL / HOLD
```

**State flow:** All agents communicate via the typed `AgentState` TypedDict (`tradingagents/agents/utils/agent_states.py`). No agent imports another agent directly.

---

## 3. Directory map

```
tradingagents/
  agents/
    analysts/
      market_analyst.py
      fundamentals_analyst.py        <- thin wrapper over fundamentals/pipeline.py
      news_analyst.py
      social_media_analyst.py
      fundamentals/                  <- Phase 1A/1B: highest-quality subsystem
        data_loader.py               <- multi-period CSV loader
        financial_calculator.py      <- 14 core ratios + signal_coherence
        statement_standardizer.py    <- common-size, YoY/QoQ
        sector_config.py             <- banks/real_estate/holdings/operational + safety floors
        scoring.py                   <- data_confidence, health heuristic
        schemas.py                   <- Pydantic FundamentalAnalysisReport
        data_cot.py                  <- Stage 1: deterministic evidence pack
        concept_cot.py               <- Stage 2: quick-LLM concept synthesis
        thesis_cot.py                <- Stage 3: deep-LLM H&P thesis
        pipeline.py                  <- 3-stage orchestrator with fallback
        memory_manager.py            <- Phase 3 stubs (NOT IMPLEMENTED)
    researchers/                     <- bull / bear
    managers/
      research_manager.py
      risk_manager.py                <- deterministic VETO + LLM judge
    risk_mgmt/                       <- aggressive / conservative / neutral / merged
    trader/trader.py
    utils/
      agent_states.py                <- AgentState, InvestDebateState, RiskDebateState
      memory.py                      <- FinancialSituationMemory (ChromaDB / pgvector)
      scoring.py                     <- unified [-1, 1] aggregator (SEE MEMORY.md - known issues)
      llm_failover.py                <- multi-provider retry/rotation
      *_tools.py                     <- @tool wrappers used by ToolNodes

  dataflows/
    social_v2/                       <- PRODUCTION social sentiment pipeline (2026-05-18)
      models.py                      <- Post dataclass (shared by all sources)
      relevance.py                   <- Layer-0 EGX gate
      entities.py                    <- 84-issuer SYMBOL_REGISTRY (EN + AR aliases)
      intent.py                      <- BUY/SELL/BULLISH/BEARISH/HOLD/REACTION detector
      content_type.py                <- OPINION/NEWS/ANALYSIS/QUESTION
      quality_gate.py                <- permissive gate (downgrade vs hard-drop)
      aggregator.py                  <- weighted per-stock + market signal
      sentiment_runner.py            <- bridges to sentiment_engine + VADER baseline
      cache.py                       <- diskcache (Apify TTL 1 h, signal TTL 30 min)
      post_store.py                  <- Postgres archive (social_v2_posts table)
      pipeline.py                    <- 7-stage orchestrator (SCRAPE..ARCHIVE)
      signal_adapter.py              <- AGENT-FACING entry; backtest honesty gate
      sources/
        facebook_apify.py            <- Apify actor 2chN8UQcH1CfxLRNE, 5 AR groups,
                                        stale-cache fallback on 402/403/429/5xx
        mubasher_news.py             <- Mubasher EGX RSS (AR + EN), no auth/quota
        telegram_public.py           <- t.me/s/<channel> previews, no auth
        reddit_targeted.py           <- intent-rich queries, requires real
                                        ticker mention (strict relevance gate)
    gateway.py                       <- DataGateway: cache -> primary -> fallback chain
    interface.py                     <- route_to_vendor() dispatcher
    y_finance.py                     <- primary OHLCV provider
    eodhd.py                         <- OHLCV fallback (HAS HARDCODED KEY - see MEMORY.md)
    egxpy_wrapper.py                 <- native EGX library wrapper
    local.py                         <- EGX CSV provider (fundamentals + news)
    mubasher_scraper.py              <- live price scrape (FRAGILE)
    tradingview_provider.py
    stockstats_utils.py
    cache_manager.py                 <- diskcache TTL store
    retry_engine.py
    schemas.py                       <- Pydantic provider responses
    egx_data_refresh.py              <- staleness check + auto-refresh trigger
    news_providers/                  <- aggregator + NewsAPI + RSS sources
    social_media_sources/            <- aggregator + sentiment_engine + Twitter/Reddit/Telegram/StockTwits

  graph/
    trading_graph.py                 <- TradingAgentsGraph (entry class)
    setup.py                         <- GraphSetup: nodes + edges
    propagation.py                   <- initial-state factory + confidence aggregation
    prefetch.py                      <- parallel news/social pre-fetch
    conditional_logic.py             <- debate-loop routing
    reflection.py                    <- post-trade memory updates (DO NOT call inside backtest loop)
    signal_processing.py             <- regex BUY/SELL/HOLD extractor

  utils/
    sentiment_engine.py              <- FinBERT / CAMeLBERT-DA / XLM-R router
    text_preprocessor.py             <- Arabic normalization (hamza, diacritics, dialect)

  ablation/                          <- ablation harness
  default_config.py                  <- DEFAULT_CONFIG + EGX_TICKERS

server/api_server.py                 <- FastAPI REST + WebSocket (NO AUTH - see MEMORY.md)
cli/main.py                          <- Rich-TUI CLI (interactive only - no flags)
dashboard/                           <- React 19 + Vite + lightweight-charts
scripts/
  backtester.py                      <- LLM backtest engine (look-ahead §C1/C3/C4 RESOLVED 2026-05-20;
                                        crash-hardened + retry + --resume; honest closed-trade win-rate)
  bt_benchmark.py                    <- Backtrader classical baseline
  benchmark_comparison.py            <- side-by-side comparator
  run_real_backtests.py              <- multi-ticker driver (legacy 3-ticker)
  evaluate_egx_backtests.py          <- multi-ticker eval harness: runs N tickers, aggregates
                                        per-ticker JSON via walkforward.compute_arm_metrics,
                                        emits CSV + Markdown pooled summary vs EGX30
  parse_egx_annex5.py                <- PDF parser for fundamentals
  social_pipeline/                  <- v1: scrape-only validation
  social_pipeline/v2/               <- v2: layered trading-signal engine (Apify-backed)

tests/                               <- pytest suite (Fundamentals strong; rest sparse)
agent_docs/                          <- per-component design notes (read on demand)
db_schema.sql                        <- Postgres schema (apply manually - no migration tool)
persistent_memory.py                 <- Optional Postgres + pgvector memory class (ChromaDB is default)
redis_pubsub.py                      <- AgentEventPublisher / Subscriber for WebSocket streaming
```

---

## 4. Critical configuration

### `tradingagents/default_config.py`

| Key | Default | Notes |
|---|---|---|
| `llm_provider` | `"openai"` | OpenAI-compatible client |
| `deep_think_llm` / `quick_think_llm` | `"deepseek-chat"` | Pointed at DeepSeek (not OpenAI) |
| `backend_url` | `"https://api.deepseek.com"` | DeepSeek endpoint |
| `target_market` | `"EGX"` | Activates EGX-specific paths |
| `trading_currency` | `"EGP"` | |
| `long_only` / `allow_short_selling` / `allow_leverage` | `True / False / False` | Hard regulatory constraints |
| `daily_price_limit_pct` | `0.10` | EGX +/- 10% circuit breaker |
| `max_position_pct_adv` | `0.10` | 10% of ADV cap |
| `data_vendors` | `yfinance / yfinance / local / local` | core / indicators / fundamentals / news |
| `auto_refresh_fundamentals` | `True` | Refresh CSVs older than 90 days |
| `backtest_mode` | `True` | RELAXES single-stock concentration veto. Set `False` for live multi-stock portfolios. |
| `prefetch_data` | `True` | Parallel news+social pre-fetch (saves ~2 LLM calls + 30-60 s/run) |
| `memory_backend` | `"chroma"` | Vector memory backend. Set `TRADINGAGENTS_MEMORY_BACKEND=postgres` only when pgvector is installed and schema-aligned. |
| `chroma_persist_dir` | `"./chroma_db"` | On-disk ChromaDB path (gitignored). Empty/unset → in-memory client (data lost on restart). |
| `memory_min_similarity` | `0.30` | Cosine threshold for `get_memories()` — drop matches below this. Configurable via `MEMORY_MIN_SIMILARITY`. |
| `postgres_url` / `redis_url` | env-driven | Postgres is optional for audit/backtest persistence; Redis is optional for WebSocket progress events. |

### `.env` keys (loaded via `python-dotenv`)

- `DEEPSEEK_API_KEY` (or `OPENAI_API_KEY`) - required for LLM
- `EODHD_API_KEY` - fallback OHLCV provider
- `NEWS_API_KEY` - news fallback chain
- `APIFY_API_TOKEN` - Facebook Groups scraping (v2 social pipeline). Used by `tradingagents/dataflows/social_v2/sources/facebook_apify.py` (actor `2chN8UQcH1CfxLRNE`). Rotate after any chat/log exposure.
- `EGX_FB_GROUP_URLS` - optional, comma-separated list of Facebook group URLs. If unset, social_v2 uses the 5 user-confirmed default Arabic groups baked into `facebook_apify.DEFAULT_GROUP_URLS`.
- `EGX_FB_MAX_POST_AGE_DAYS` / `MUBASHER_MAX_POST_AGE_DAYS` / `TELEGRAM_MAX_POST_AGE_DAYS` - optional per-source recency filters. Defaults: FB=3, Mubasher=3, Telegram=7. Posts older than the limit are dropped at source.
- `EGX_TELEGRAM_CHANNELS` - optional, comma-separated list of public Telegram channel handles (no `@`). Defaults to a small set of known-public Egyptian financial channels; replace with channels you actually follow that have public previews enabled at `t.me/s/<handle>`.
- `POSTGRES_URL` - optional, enables persistent memory + audit
- `REDIS_URL` - optional, enables WebSocket streaming
- `TRADINGAGENTS_MEMORY_BACKEND` - optional; defaults to `chroma`, set to `postgres` only with pgvector ready
- `CHROMA_PERSIST_DIR` - optional; defaults to `./chroma_db`. Path to the persistent ChromaDB vector store (gitignored).
- `MEMORY_MIN_SIMILARITY` - optional; defaults to `0.30`. Floor for `get_memories()` cosine similarity (or tanh-normalized BM25 score).
- `EGX_FB_STORAGE_STATE` / `EGX_X_STORAGE_STATE` - optional Playwright cookies

**DB infrastructure reference:** see [agent_docs/db_infrastructure.md](agent_docs/db_infrastructure.md) for the full operator guide (responsibilities of each store, setup commands, health-endpoint contract, backup/reset procedures).

**NEVER hardcode keys in source.** A live EODHD key is currently committed in `tradingagents/dataflows/eodhd.py` and `gateway.py`. Rotating + scrubbing this is a Week-1 task - see `MEMORY.md` issue A.

---

## 5. Coding standards (enforce on every change)

1. **Verify before changing.** Use `grep`/`Read` to confirm actual function signatures and imports. Do NOT assume APIs from memory or README.
2. **No hardcoded secrets.** Anything API-key-shaped goes in `.env`. Pre-commit secret scan recommended.
3. **Use `logging`, not `print()`.** Module loggers: `logging.getLogger("tradingagents.<module>")`. Several modules still use `print()` - flag them when you touch them.
4. **Modular agents.** Communicate only via LangGraph state dicts. No direct agent-to-agent imports.
5. **Bilingual sentiment.** All NLP code MUST handle Arabic (MSA + Egyptian dialect) and English. Route via `tradingagents/utils/sentiment_engine.py`.
6. **EGX constraints are hard.** Long-only, no shorts, no leverage, +/-10% daily limit, max 10% ADV, T+2 settlement. Encoded in `risk_manager.EGX_RISK_LIMITS` - never bypass.
7. **Type hints required** on new public functions.
8. **LLM determinism.** Every `.invoke()` MUST set `temperature=0` and a fixed `seed`. Without this, audit trail is meaningless. (Several agents currently violate this - fix on touch.)
9. **No look-ahead in backtest paths.** Any new code in `scripts/backtester.py` or analyst tools must respect `trade_date` as the time horizon. Future data is forbidden.
10. **Test after changes.** `python -m pytest tests/ -v --tb=short`. Fundamentals tests (`tests/test_fundamentals_phase1a.py`, `phase1b_audit.py`) must stay green.
11. **Document on-demand only.** Read `agent_docs/<component>.md` when modifying that specific component, not before.
12. **Don't ship audit-affecting changes without updating `MEMORY.md`.**

---

## 6. Common commands

```bash
# Single-shot analysis
python main.py
python run_egx_prediction.py COMI.CA

# Interactive CLI
python -m cli.main

# API server (dev)
uvicorn server.api_server:app --reload --port 8000

# Dashboard
cd dashboard && npm install && npm run dev   # http://localhost:5173

# LLM backtest (--resume picks up from the per-ticker partial checkpoint)
python scripts/backtester.py --ticker COMI.CA --start 2023-10-01 --end 2024-01-01 \
    --interval 20 --analysts market,fundamentals,news,social [--resume]

# Classical baseline (no LLM, no API key)
python scripts/bt_benchmark.py --ticker COMI.CA --start 2023-10-01 --end 2024-01-01

# Multi-engine driver
python scripts/run_real_backtests.py

# Multi-ticker evaluation harness vs EGX30 (CSV + Markdown pooled summary)
python scripts/evaluate_egx_backtests.py --tickers ETEL.CA,TMGH.CA \
    --start 2024-01-01 --end 2024-03-31 --interval 20

# Twitter/social v2 pipeline
PYTHONIOENCODING=utf-8 python scripts/social_pipeline/v2/pipeline.py

# Tests
python -m pytest tests/ -v --tb=short

# Smoke test
python -c "from tradingagents.graph.trading_graph import TradingAgentsGraph; print('OK')"
```

---

## 7. Entry-point cheat sheet

| File | Use case |
|---|---|
| `main.py` | Minimal example - one analysis on COMI.CA |
| `run_egx_prediction.py` | Direct LLM prediction with live/historical price; powers `/api/test/random-egx` |
| `cli/main.py` | Interactive Rich TUI - research workflow |
| `server/api_server.py` | FastAPI REST + WebSocket - dashboard backend |
| `scripts/backtester.py` | LLM multi-agent backtest engine (crash-hardened, `--resume`-able, look-ahead-free metrics) |
| `scripts/bt_benchmark.py` | Backtrader classical RSI/MACD/BB baseline |
| `scripts/evaluate_egx_backtests.py` | Multi-ticker evaluation harness — pooled CSV + Markdown summary vs EGX30 |
| `scripts/social_pipeline/v2/pipeline.py` | Production-style trading-signal aggregator from social sources |

---

## 8. Twitter / Social Pipeline (v1 + v2)

### v1 - `scripts/social_pipeline/`
Validation-grade scraper-only flow (Reddit JSON + DDG + dead Nitter mesh). Strict two-signal relevance classifier (finance AND EGX). Writes `logs/results_*.json` + `.csv`. Twitter is anonymously unscrapable in 2026 - documented in `scraper.py`.

### v2 - `scripts/social_pipeline/v2/`
Layered trading-signal engine. **This is the production-track pipeline.**

```
v2 stages: SCRAPE -> RELEVANCE -> ENRICH (entity + intent + content_type)
        -> QUALITY GATE -> SENTIMENT -> AGGREGATE -> SPLIT OUTPUTS
```

Sources (in priority order):
- `facebook_apify.py` - PRIMARY. Apify actor `2chN8UQcH1CfxLRNE`. Trusted (bypasses Layer-0 relevance). `APIFY_API_TOKEN` env required.
- `reddit_targeted.py` - intent-only queries
- `telegram_public.py` - public `t.me/s/` previews
- `mubasher_news.py` - public Egyptian financial news
- `twitter_authed.py` - Playwright + persisted cookies (env-gated)
- `facebook_groups.py` - Playwright fallback (env-gated)

Aggregator (`aggregator.py`):
```
weight     = entity_conf * content_weight * intent_factor * log(engagement)
quality    = clamp(weight_total / n, 0, 1)
size       = n / (n + 5)
confidence = 0.6 * quality + 0.4 * size
```

Thresholds: `MIN_TOTAL_POSTS=50` (else `NO_SIGNAL`), `MIN_MENTIONS_PER_STOCK=5`.

Output: `results_<stamp>.json` with `market_sentiment`, `per_stock_sentiment`, full per-symbol breakdown, items with both project-engine + VADER sentiment.

**Known gap:** `entities.SYMBOL_REGISTRY` is too narrow - many retail FB posts mention small-cap EGX names not in the registry, so `symbols_covered` is often 0. Extending the registry is a high-leverage win.

---

## 9. Production readiness - short version

This codebase has **critical production blockers**. Do not deploy as a live trading system. Acceptable Week-4 ship target is a **research console** with audit, auth, fixed reproducibility, and a defensible backtest. Full list of blockers and 4-week plan is in `MEMORY.md` section 3.

**The things every Claude session must internalize:**

1. **Reproducibility is broken.** LLM `.invoke()` calls don't pin temperature/seed everywhere (MEMORY.md §B). Until fixed, every backtest result is unrepeatable and every audit trail is partial. Top of fix list.
2. **Backtester look-ahead is RESOLVED (2026-05-20).** `_evaluate_trade_outcomes` deleted, risk-free rate is config-driven, benchmark window is strictly date-intersected (MEMORY.md §C C1/C3/C4 resolved). The backtester is also crash-hardened, retry-wrapped, and `--resume`-able. Reported win-rate is now realized closed-trade only, with a Wilson CI.
3. **Hardcoded EODHD API key in source.** Rotate + scrub before anything else (MEMORY.md §A).
4. **Bear researcher empty output — RESOLVED 2026-05-20 (MEMORY.md §AA).** The debate was a Bull↔Bear cycle with a leaky "Analysts Sync" fan-in barrier; a re-triggered concurrent Bull task clobbered Bear's `investment_debate_state` write via the `_keep_last` reducer. Fixed by linearizing the debate (`Bull → Bear → Research Manager`) in both `graph/setup.py` and `ablation/runner.py` and deferring the barrier. Debates are now genuinely two-sided.

When in doubt, read `MEMORY.md` first.

---

## 10. EGX ticker universe (current)

From `default_config.EGX_TICKERS`:

```
Banks:          COMI.CA, ADIB.CA, CIEB.CA, EXPA.CA, HDBK.CA, QNBA.CA, SAUD.CA
Real Estate:    TMGH.CA, HELI.CA, PHDC.CA, OCDI.CA, ORAS.CA, EMFD.CA
Industry:       EAST.CA, ESRS.CA, SWDY.CA, ABUK.CA, MFPC.CA, EGAL.CA, EGCH.CA, EFIC.CA
Telecom/Tech:   ETEL.CA, FWRY.CA, EFIH.CA, RAYA.CA
Financial Svc:  HRHO.CA, BTFH.CA, CIch.CA
Food & Bev:     JUFO.CA, EFID.CA, DOMT.CA
```

~30 names. EGX-30 + EGX-70 = ~100 listed; full main market is ~200+. Universe expansion is on the roadmap.

---

## 11. Audit doctrine for Claude sessions

When working on this repo:

- Treat `MEMORY.md` as ground truth for known issues. Don't re-discover them; build on them.
- When you fix a known issue from `MEMORY.md`, move it from "open" to "resolved" with the commit hash and date.
- When you find a new issue, append it to `MEMORY.md` "Open issues" with file:line, severity, and a 1-sentence remediation.
- For trading-logic changes, run the full Phase 1A + Phase 1B fundamentals tests as a regression gate.
- For risk-manager changes, run `tests/test_egx_constraints.py`, `tests/test_trader_limits.py`, `tests/test_risk_manager_veto.py`.
- For data-layer changes, run `tests/test_prefetch.py` and `tests/test_news_analyst_prefetch.py`.
- Never delete an audit log, eval result, or backtest report without explicit user instruction.
