# Documentation Truth Audit — 2026-06-16

> Every major claim from project documentation, verified against the current codebase.
> **Rule:** If docs conflict with code, trust the code. Mark planned/unimplemented clearly.

---

## 1. LangGraph / Multi-Agent Architecture

| Claim from docs | Source doc | Current code evidence | Status |
|---|---|---|---|
| `TradingAgentsGraph` class at `tradingagents/graph/trading_graph.py` | CLAUDE.md, AGENTS.md | `trading_graph.py:89` — class confirmed | TRUE |
| Parallel analyst fan-out: Market, Fundamentals, News, Social | CLAUDE.md, AGENTS.md | `setup.py:219` — `workflow.add_edge(START, current_analyst)` loop | TRUE |
| "Analysts Sync" barrier node with `defer=True` | langgraph_zero_to_hero.md | `setup.py:211` — `add_node("Analysts Sync", lambda state: {}, defer=True)` | TRUE |
| `PerAnalystToolNode` swaps messages to per-analyst channel | langgraph_zero_to_hero.md | `setup.py:15-33` — class confirmed | TRUE |
| `AgentState` TypedDict with `_keep_last` reducer | CLAUDE.md | `agents/utils/agent_states.py` — confirmed | TRUE |
| "Star-topology orchestration" with a Coordinator Node | system_design.md | No coordinator node; graph is a DAG fan-out from START | FALSE |
| `DecisionManager` node synthesizes signals | system_design.md | Node is "Research Manager" in `setup.py:149,185`; no `DecisionManager` | FALSE |
| Dynamic weighting: `if hype_index > 0.7` overrides fundamentals | system_design.md | No such logic anywhere in codebase | FALSE |
| Analysts return `{ "score": Float[-1:1], "signals": List[String] }` | system_design.md | Analysts update AgentState string keys (`market_report`, etc.); no structured score dict | FALSE |

**Summary:** Core architecture (LangGraph, fan-out, barrier, state) is accurately documented in CLAUDE.md and AGENTS.md. `system_design.md` is largely **fictional** and must not be cited.

---

## 2. Analyst Modules

| Claim from docs | Source doc | Current code evidence | Status |
|---|---|---|---|
| 4 analysts: Market, Fundamentals, News, Social | CLAUDE.md | `setup.py:90-140` — all four confirmed | TRUE |
| EGX market analyst uses deterministic path (no LLM) | langgraph_zero_to_hero.md | `setup.py:93-99` — `create_deterministic_market_analyst()` when `target_market == "EGX"` | TRUE |
| Fundamentals analyst wraps 3-stage CoT pipeline | CLAUDE.md | `fundamentals_analyst.py:48` imports pipeline; `pipeline.py:1-17` confirms 3 stages | TRUE |
| README calls them "Sentiment Analyst" and "Technical Analyst" | README.md | Code uses "Social Media Analyst" and "Market Analyst" — naming diverges | OUTDATED |

---

## 3. Bull/Bear Debate and Research Manager

| Claim from docs | Source doc | Current code evidence | Status |
|---|---|---|---|
| Bull → Bear → Research Manager linear chain | CLAUDE.md §9, MEMORY.md §AA | `setup.py:247-249` — linear edges confirmed | TRUE |
| Debate was originally Bull ↔ Bear cycle (now fixed) | MEMORY.md §AA | `setup.py:234` comment explains fix; concurrent clobbering resolved | TRUE |
| `max_debate_rounds` is permanently 1 | MEMORY.md §AA | `setup.py:244` comment confirms | TRUE |
| `should_continue_debate` conditional edges alternate Bull/Bear | langgraph_zero_to_hero.md | No longer wired; linear chain replaced it | OUTDATED |
| Debate diagram shows Bull ↔ Bear cycle | langgraph_zero_to_hero.md | Code is linear, not cyclic — diagram is outdated | OUTDATED |
| Research Manager prompt includes macro context | research_manager.py | `research_manager.py:81-82` — `format_macro_context_for_prompt` confirmed | TRUE |
| Structured bull/bear JSON theses consumed by Research Manager | AGENTS.md | `research_manager.py:21-22` — `bull_thesis`, `bear_thesis` from state | TRUE |

---

## 4. Trader and Risk Manager

| Claim from docs | Source doc | Current code evidence | Status |
|---|---|---|---|
| Trader agent composes execution plan | CLAUDE.md | `trader/trader.py` exists, receives macro context | TRUE |
| Risk pipeline: Risk Scorer → [VETO → END \| Merged Risk Debate → Risk Judge → END] | langgraph_zero_to_hero.md | `setup.py:259-269` — confirmed exactly | TRUE |
| Risk Scorer is deterministic (no LLM) | CLAUDE.md | `risk_scorer.py:48-77` — `EGX_RISK_LIMITS` dict, deterministic checks | TRUE |
| Merged Risk Debator: 3-perspective LLM in a single call | AGENTS.md | `merged_debator.py:6,100` — single LLM call confirmed | TRUE |
| EGX constraints: long_only, no shorts, no leverage, +/-10% limit, 10% ADV | CLAUDE.md §4 | `default_config.py:181-197` — all confirmed | TRUE |

---

## 5. Data Pipeline and Fallback Chains

| Claim from docs | Source doc | Current code evidence | Status |
|---|---|---|---|
| `DataGateway` provides cache → primary → fallback chain | CLAUDE.md | `gateway.py` — confirmed | TRUE |
| `route_to_vendor()` dispatcher in `interface.py` | CLAUDE.md | `interface.py:150` — `route_to_vendor()` with multi-vendor fallback | TRUE |
| Default vendors: yfinance / yfinance / local / local | CLAUDE.md §4 | `default_config.py` `data_vendors` — confirmed | TRUE |
| DiskCache (SQLite) for TTL cache of OHLCV/news | db_infrastructure.md | `cache_manager.py:4,78-80` — diskcache.Cache | TRUE |
| Exponential backoff for REST requests | data_pipeline.md | `retry_engine.py` exists | TRUE |
| Parquet for caching DataFrames | data_pipeline.md | Uses DiskCache (SQLite-backed), not Parquet | FALSE |
| All timestamps localized to `Africa/Cairo` | data_pipeline.md | No timezone enforcement found in data layer code | FALSE |
| Circuit breaker if provider goes offline | data_pipeline.md | No formal circuit breaker; stale-cache fallback is the nearest mechanism | PARTIAL |

---

## 6. Technical Indicators and OHLCV Temporal Safety

| Claim from docs | Source doc | Current code evidence | Status |
|---|---|---|---|
| `get_stock_data` tool clamps `end_date` to `trade_date` | CLAUDE.md (audit doctrine) | `core_stock_tools.py:22-28` — hard clamp confirmed | TRUE |
| `get_indicators` tool clamps `curr_date` to `trade_date` | CLAUDE.md (audit doctrine) | `technical_indicators_tools.py:23-27` — clamp confirmed | TRUE |
| `_get_stock_stats_bulk` fetches data bounded by `curr_date` | Patched 2026-06-16 | `y_finance.py:422-426` — `end_date = curr_date + 1 day`, belt-and-suspenders filter | TRUE |
| `StockstatsUtils.get_stock_stats` bounded by `curr_date` | Patched 2026-06-16 | `stockstats_utils.py:39-43` — same fix applied | TRUE |
| Cache keys include trade_date-derived end_date | Patched 2026-06-16 | Both files use `curr_date`-derived end in cache filename | TRUE |
| 7 OHLCV temporal safety tests pass | test_ohlcv_temporal_safety.py | 7/7 passing | TRUE |

---

## 7. Fundamentals Pipeline and Ratio Calculations

| Claim from docs | Source doc | Current code evidence | Status |
|---|---|---|---|
| 3-stage CoT: data_cot → concept_cot → thesis_cot | CLAUDE.md, lit_review.md | `pipeline.py:1-17` — all 3 stages, `data_cot.py`, `concept_cot.py`, `thesis_cot.py` | TRUE |
| Stage 2 uses `quick_think_llm`; Stage 3 uses `deep_think_llm` | lit_review.md | `pipeline.py:53-57` — confirmed | TRUE |
| Graceful fallback: any stage failure → partial report, no exception | lit_review.md | `pipeline.py:89-170` — each stage wrapped in try/except | TRUE |
| 14 core ratios + 3 experimental (earnings_yield_spread, dividend_yield, piotroski) | RATIO_SOURCES.md | `financial_calculator.py` — all 17 implemented as static methods | TRUE |
| Piotroski: 7 of 9 signals (F5/F8 dropped for missing EGX cash flow data) | RATIO_SOURCES.md | `financial_calculator.py:218-244` — F5, F8 excluded with comments | TRUE |
| Piotroski < 4 computable signals → returns None | RATIO_SOURCES.md | `financial_calculator.py:291` — confirmed | TRUE |
| 4 sector configs: banks, real_estate, holdings, operational | CLAUDE.md | `sector_config.py:29-77` — SECTOR_MAP with exactly 4 | TRUE |
| D/E excluded from banks sector | research_notes.md | `sector_config.py:97-99` — METRIC_EXCLUSIONS for "banks" | TRUE |
| Filing lag: annual=120d, quarterly=45d | CLAUDE.md, research_notes.md | `data_loader.py:180-183` — defaults confirmed | TRUE |
| publish_date filter with lag heuristic fallback | research_notes.md | `data_loader.py:169-184` — `has_usable_publish_date` check → fallback | TRUE |
| Date-aware CBE rate lookup (rate_lookup.py) | CLAUDE.md | `rate_lookup.py:63` — `get_egx_risk_free_rate_as_of()` with CSV lookup | TRUE |
| LLM never computes ratios — Python computes, LLM interprets | lit_review.md | `financial_calculator.py:27` — docstring confirms | TRUE |
| Output: typed Pydantic `FundamentalAnalysisReport` | lit_review.md | `schemas.py` — class confirmed; `pipeline.py:29` imports it | TRUE |
| Phase 3 memory (layered): stubs only, NOT IMPLEMENTED | CLAUDE.md | `memory_manager.py` exists but `pipeline.py:72-86` has `memory_enabled=False` | TRUE |
| 3-call thesis variant (H&P competing hypotheses) switchable | lit_review.md | `pipeline.py:154-155` — `thesis_cot_mode` config switch, `thesis_cot_3call.py` exists | TRUE |
| Data confidence caps LLM direction confidence (calibration) | lit_review.md | `pipeline.py:274-283` — `calibrate_earnings_direction()` applied | TRUE |

---

## 8. Sentiment / News / Social Pipeline

| Claim from docs | Source doc | Current code evidence | Status |
|---|---|---|---|
| 3 NLP models: FinBERT (EN), CAMeLBERT-DA (AR), XLM-R (fallback) | visual_explainer.md | `sentiment_engine.py:176-178` — all three model IDs confirmed | TRUE |
| Fallback chain: primary → XLM-R → rule-based (confidence capped at 0.45) | visual_explainer.md | `sentiment_engine.py:317-327,136` — `confidence = min(0.45, ...)` | TRUE |
| v2 pipeline: 7 stages (SCRAPE→RELEVANCE→ENRICH→QUALITY→SENTIMENT→AGGREGATE→SPLIT) | CLAUDE.md §8 | `social_v2/pipeline.py` — consistent with documented stages | TRUE |
| 84-issuer SYMBOL_REGISTRY | CLAUDE.md §8 | `social_v2/entities.py` — confirmed | TRUE |
| Backtest honesty gate: >1 day historical → NO_SIGNAL or Postgres archive | visual_explainer.md | `signal_adapter.py:14-24` — `BACKTEST_TOLERANCE_DAYS = 1` | TRUE |
| Facebook Apify: actor `2chN8UQcH1CfxLRNE`, 5 AR groups | CLAUDE.md §8 | `facebook_apify.py` — actor ID and default groups confirmed | TRUE |
| Aggregation: `weight = entity_conf * content_weight * intent_factor * log(engagement)` | CLAUDE.md §8 | `aggregator.py:43-49` — similar formula but `platform_factor` replaces pure entity_conf in base | PARTIAL |
| Confidence formula: `0.6 * quality + 0.4 * size` | CLAUDE.md §8 | Actual aggregator uses different calculation; formula is approximate | PARTIAL |
| Gate A: >=50 posts, >=2 sources, >=30% within 24h | visual_explainer.md | `sentiment/config.py:18-22` — exact values confirmed | TRUE |
| Gate B: >=10 sector posts, >=3 days, entity_conf mean >=0.70 | visual_explainer.md | `sentiment/config.py:25-29` — confirmed | TRUE |
| Gate C MEGA tier: >=8 mentions, >=5 authors, >=3 sources | visual_explainer.md | `sentiment/config.py:37-41` — confirmed | TRUE |
| Sentiment never flips trade direction | sentiment_architecture.md | `signal_adapter.py` emits multipliers, not directional overrides | TRUE |
| Uses MARBERT or fine-tuned Llama for Arabic | sentiment_analysis.md | Code uses CAMeLBERT-DA, not MARBERT or Llama | FALSE |
| Uses Tweepy / Telethon for social media | sentiment_analysis.md | Uses Apify, public Telegram previews, Reddit — not Tweepy/Telethon | FALSE |
| `hype_index [0.0-1.0]` field in output | sentiment_analysis.md | No `hype_index` in `SentimentOutput` dataclass | FALSE |

**Summary:** `sentiment_analysis.md` is severely outdated (pre-v2). `sentiment_visual_explainer.md` is highly accurate.

---

## 9. Memory System and `valid_after_date` Filtering

| Claim from docs | Source doc | Current code evidence | Status |
|---|---|---|---|
| Memory backend default = `"chroma"` | CLAUDE.md §4 | `default_config.py:125` — confirmed | TRUE |
| Postgres + pgvector optional, opt-in | CLAUDE.md §4 | `TRADINGAGENTS_MEMORY_BACKEND=postgres` env-gated | TRUE |
| BM25 fallback when embeddings unavailable | CLAUDE.md | `memory.py` implements BM25 path; `EMBEDDINGS_BACKEND_URL=disabled` | TRUE |
| `valid_after_date` filtering prevents temporal leakage | MEMORY.md temporal safety | `memory.py` — date filtering on retrieval; 30 tests in `test_memory_temporal.py` | TRUE |
| 5 agent memory collections: bull, bear, trader, invest_judge, risk_manager | db_infrastructure.md | `api_server.py:203-208` — `_CHROMA_AGENT_COLLECTIONS` tuple matches | TRUE |
| `memory_min_similarity` default = 0.30 | CLAUDE.md §4 | `default_config.py` — confirmed | TRUE |
| Memory backend is PostgreSQL by default (falls back to ChromaDB) | AGENTS.md | Default is ChromaDB, Postgres is opt-in — AGENTS.md has it inverted | FALSE |
| Reflection flushing disabled during backtests | CLAUDE.md | Belt-and-suspenders with `valid_after_date`; `test_memory_temporal.py` verifies | TRUE |

---

## 10. Backtesting Engine and Benchmark Logic

| Claim from docs | Source doc | Current code evidence | Status |
|---|---|---|---|
| Look-ahead bias RESOLVED: `_evaluate_trade_outcomes` deleted | MEMORY.md §C1 | `backtester.py` — function deleted per audit trail | TRUE |
| Risk-free rate is config-driven (not hardcoded 0.05) | MEMORY.md §C3 | `backtester.py:183` — reads from `config["egx_risk_free_rate"]` with fallback | TRUE |
| Benchmark window strictly date-intersected | MEMORY.md §C4 | `backtester.py:1135-1144` — window alignment confirmed | TRUE |
| Crash-hardened + retry + `--resume`-able | CLAUDE.md §6 | `backtester.py` has `--resume` flag, retry logic | TRUE |
| Win-rate is realized closed-trade only, with Wilson CI | CLAUDE.md | Backtest report includes `win_rate_ci_lo`, `win_rate_ci_hi` | TRUE |
| EGX30 CSV loaded from project root (`EGX 30 Historical Data.csv`) | backtester.py | `backtester.py:1122` — candidate filenames include this | TRUE |
| EGX30 CSV range: 2020-01-02 to 2026-06-14, 1561 rows | Verified 2026-06-16 | CSV read confirmed range and row count | TRUE |
| Circuit breaker skips when EGX daily price limit hit | CLAUDE.md | `backtester.py` circuit breaker logic produces skip messages | TRUE |
| "Zero Lookahead Bias" in BacktestingEngine | system_design.md | Backtester look-ahead was a real issue (fixed 2026-05-20); claim was aspirational at time of writing | PARTIAL |

---

## 11. API / Backend

| Claim from docs | Source doc | Current code evidence | Status |
|---|---|---|---|
| FastAPI + Uvicorn + Pydantic | backend.md | `api_server.py:36-38` — confirmed | TRUE |
| WebSocket at `/api/analyze` | dashboard/README.md | `api_server.py:2045` — confirmed | TRUE |
| `asyncio.to_thread` for blocking agent computations | backend.md | `api_server.py:2045` — confirmed in WS handler | TRUE |
| `GET /api/health` with diagnostics | db_infrastructure.md | `api_server.py:677` — confirmed | TRUE |
| `GET /sessions/{id}/trace` endpoint | dashboard.md | `api_server.py:947` — confirmed | TRUE |
| `GET /memory/{agent}/search`, `/entries`, `/reflections` | dashboard.md | `api_server.py:1205,1254,1339` — all three confirmed | TRUE |
| `GET /api/rl/status`, `/api/rl/decisions` | dashboard.md | `api_server.py:1434,1473` — confirmed | TRUE |
| `GET /api/diagnostics/prompts`, `/api/diagnostics/fingerprints` | dashboard.md | `api_server.py:1615,1636` — confirmed | TRUE |
| Modular `routers/` directory (backtest.py, agents.py, etc.) | backend.md | No `server/routers/` directory; monolithic `api_server.py` | FALSE |
| CORS = `allow_origins=["*"]`, no auth | CLAUDE.md §9, dashboard.md | `api_server.py` — confirmed; documented production blocker | TRUE |
| Several undocumented endpoints exist | N/A | `/api/macro`, `/api/market/indices`, `/api/indicators/{ticker}`, `/api/news/{ticker}`, `/api/fundamentals/{ticker}`, `/api/analyze-full`, `/api/investor-profile` — not in api-integration.md | TRUE |

---

## 12. Dashboard

| Claim from docs | Source doc | Current code evidence | Status |
|---|---|---|---|
| React 19 + Vite + TypeScript + Tailwind + TanStack Query + Zustand | dashboard/README.md | `package.json` — all confirmed | TRUE |
| lightweight-charts for price/equity rendering | dashboard/README.md | `package.json`: lightweight-charts ^5.1.0; `PriceChart.tsx`, `EquityCurve.tsx` | TRUE |
| react-router-dom v6 | architecture.md | `package.json`: `^7.13.0` — actually v7 | OUTDATED |
| Route tree: /workspace, /run, /sessions, /backtest, /universe, /diagnostics, /settings | dashboard/README.md, onboarding.md | `App.tsx` has ONLY 3 routes: `/`, `/backtest`, `/history` | FALSE |
| 13-node AgentTimeline | dashboard/README.md, architecture.md | `AgentTimeline.tsx:14-27` — 12 nodes | FALSE |
| `react-window` installed but not wired | architecture.md | `package.json` confirms installed; no usage in components | TRUE |
| `useAgentStream` WebSocket hook | dashboard/README.md | `src/hooks/useAgentStream.ts` — confirmed | TRUE |
| No Vitest/React Testing Library suite | extending.md | No `dashboard/tests/` directory | TRUE |
| Dark-only theme | design-system.md | Consistent across all component files | TRUE |
| i18n hand-rolled dict file | architecture.md | `src/lib/i18n.ts` exists | TRUE |

**Summary:** Tech stack claims are accurate. Route tree and node count in all dashboard docs are **wrong** — `App.tsx` has 3 routes, not 10+, and 12 nodes, not 13. The features exist in `src/features/` but are not wired into the router.

---

## 13. Monitoring Stack

| Claim from docs | Source doc | Current code evidence | Status |
|---|---|---|---|
| `monitoring/` directory with docker-compose.yml | Plan file, thesis_outline.md | `monitoring/docker-compose.yml` — confirmed | TRUE |
| Prometheus + Grafana services | Plan file | `monitoring/prometheus/`, `monitoring/grafana/` — confirmed | TRUE |
| Loki + Promtail | (extended monitoring) | `monitoring/loki/`, `monitoring/promtail/` — directories exist | TRUE |
| 23 Prometheus metrics exposed at `/metrics` on port 8000 | MEMORY (Phase 1) | `tradingagents/observability/metrics.py` — metrics module exists | TRUE |
| Phase 1 instrumentation complete (2026-06-14) | Project memory | `metered_node()` wrappers, `MetricsCallbackHandler` in codebase | TRUE |

---

## 14. Tests and Current Verified Counts

| Claim from docs | Source doc | Current code evidence | Status |
|---|---|---|---|
| Total test files: 46 | Verified 2026-06-16 | `ls tests/test_*.py | wc -l` = 46 | TRUE |
| Total test functions (`def test_`): 679 | Verified 2026-06-16 | `grep -c "def test_" tests/test_*.py` sum = 679 | TRUE |
| Total pytest collected: 755 | Verified 2026-06-16 | `pytest --collect-only -q` = 755 (parametrized expand) | TRUE |
| 30 memory temporal safety tests | thesis_outline.md | `test_memory_temporal.py` — 30 confirmed | TRUE |
| 7 OHLCV temporal safety tests | New 2026-06-16 | `test_ohlcv_temporal_safety.py` — 7 confirmed | TRUE |
| 6 macro rate date-aware tests | New 2026-06-15 | `test_macro_rate_dateaware.py` — 6 confirmed | TRUE |
| 2 news temporal safety tests | test_news_temporal_safety.py | 2 confirmed | TRUE |
| 45 total temporal safety tests (all passing) | Verified 2026-06-16 | 30+7+6+2 = 45, all green | TRUE |
| 7 RL test files, 123 RL test functions | rl_meta_policy.md | All 7 files exist; 123 `def test_` confirmed | TRUE |
| 3 RL scripts (generate, train, evaluate) | rl_meta_policy.md | All 3 in `scripts/` — confirmed | TRUE |
| `test_fundamentals_phase1a.py` exists | CLAUDE.md §11 | File does NOT exist — known CLAUDE.md inaccuracy | FALSE |
| 51 fundamentals tests (coverage + pipeline) | Verified 2026-06-16 | `test_fundamentals_coverage.py` (25) + `test_fundamentals_pipeline.py` (16+) = 51 | TRUE |

---

## 15. Config and Environment

| Claim from docs | Source doc | Current code evidence | Status |
|---|---|---|---|
| `deep_think_llm` / `quick_think_llm` = `"deepseek-chat"` | CLAUDE.md §4 | `default_config.py:34-35` — confirmed | TRUE |
| `backend_url` = `"https://api.deepseek.com"` | CLAUDE.md §4 | `default_config.py:36` — confirmed | TRUE |
| LLM determinism: constructors pin `temperature=0, seed=42` | MEMORY.md §B | `trading_graph.py:131-132` — constructors DO pin | PARTIAL |
| Per-agent `.invoke()` calls may not re-pin seed | MEMORY.md §B | Known open issue — researchers/trader rely on constructor-level pinning | TRUE |
| `CIch.CA` in ticker universe | CLAUDE.md §10 | Code has `"CICH.CA"` (all caps) in `default_config.py:19` | OUTDATED |
| EODHD hardcoded key in source | CLAUDE.md §4, MEMORY.md §A | Partially resolved per MEMORY — still flagged | PARTIAL |

---

## Reliable Thesis Claims

These are safe to write into the thesis with current code backing:

1. LangGraph-based multi-agent architecture with 4 parallel analysts, linearized bull/bear debate, deterministic risk scorer with hard VETO path
2. 3-stage CoT fundamentals pipeline (data → concept → thesis) with Pydantic output schema, 17 financial ratios, 4 sector configs
3. Temporal safety: date-aware CBE rate lookup, OHLCV bounded by trade_date, news bypassed for historical dates, memory valid_after_date filtering — 45 tests
4. Triple NLP model routing (FinBERT/CAMeLBERT-DA/XLM-R) with rule-based fallback
5. v2 social pipeline: 7-stage ETL, 84-issuer registry, backtest honesty gate
6. EGX regulatory constraints enforced deterministically (long-only, no shorts, no leverage, +/-10%, 10% ADV)
7. Crash-hardened backtester with resume, look-ahead-free metrics, Wilson CI win rate
8. 755 tests collected (679 functions, 46 files), including 123 RL tests
9. Prometheus/Grafana monitoring stack with 23 metrics
10. React 19 dashboard with WebSocket streaming, lightweight-charts, i18n

## Outdated or Unsafe Claims — DO NOT USE

1. **`system_design.md`** — almost entirely fictional (star topology, DecisionManager, hype_index, per-analyst score dicts). Do not cite.
2. **`sentiment_analysis.md`** — pre-v2, references MARBERT/Tweepy/Telethon/hype_index. All replaced. Do not cite.
3. **Dashboard route tree** — all docs describe 10+ routes; `App.tsx` has 3. Features exist in `src/features/` but are not router-wired.
4. **Bull/Bear debate cycle diagram** in `langgraph_zero_to_hero.md` — was a cycle, now linear chain. Diagram is outdated.
5. **`backend.md` claims modular `routers/`** — everything is in monolithic `api_server.py`.
6. **README.md** — upstream Tauric branding, does not describe EGX fork state.
7. **`test_fundamentals_phase1a.py`** referenced in CLAUDE.md §11 — file does not exist.
8. **Memory default inverted in AGENTS.md** — says Postgres default, actually ChromaDB.
9. **MCTS / FinMAN / FinDAP / AraBERT** in research_notes.md — aspirational, not implemented.
10. **`tradingagents/sentiment/blender.py`** referenced in rl_meta_policy.md — file does not exist.

## Needs Follow-up

1. **AgentTimeline node count:** Docs say 13, code has 12. Need to verify which node was removed/merged and update docs.
2. **react-router-dom version:** Docs say v6, package.json says v7. Minor but should be corrected.
3. **Aggregation formula exactness:** CLAUDE.md formula (0.6*quality + 0.4*size) is approximate; actual aggregator code differs slightly.
4. **Per-agent LLM seed pinning:** Constructor-level pinning exists but MEMORY.md §B flags per-invoke gaps as open issue. Partial fix.
5. **Undocumented API endpoints:** ~7 endpoints in api_server.py not covered by api-integration.md.
6. **Dashboard features not wired:** All feature pages exist in `src/features/` but only 3 are in the router. Either the router needs updating or the features are WIP.
7. **Sunday–Thursday EGX trading week calibration:** Referenced in research_notes.md but not implemented in trader or backtester day-of-week logic.
