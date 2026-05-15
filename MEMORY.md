# MEMORY.md — Audit Findings, Known Issues, and Technical-Debt Log

> **Purpose.** Persistent record of what is broken, what is suspect, what is solid, and what the 4-week ship plan is. This file is updated whenever a Claude session discovers, validates, or resolves an issue. It is the institutional memory of the project.
>
> **Companion file:** `CLAUDE.md` is the operational reference (architecture, commands, conventions). Read both together.
>
> **Last full audit:** 2026-04-29 — senior-engineer + senior-PO joint review.

---

## 0. Executive verdict

This is a **strong research prototype** with one production-grade subsystem (Fundamentals Phase 1A/1B) and several critical production blockers. **Do not ship as a live trading system in the current state.** A 4-week effort is sufficient to reposition it as a defensible **EGX research console** with audit, auth, fixed reproducibility, and a corrected backtest. A live order-execution system requires 10–14 weeks plus a fintech engineer.

What is genuinely defensible:
- LangGraph orchestration, deterministic risk veto, fundamentals CoT pipeline, bilingual Arabic sentiment routing (CAMeLBERT-DA), v2 social-signal pipeline with explicit `NO_SIGNAL` honesty.

What is not yet defensible:
- LLM determinism, backtest methodology, secret hygiene, server hardening, audit trail, scoring math, memory cold-start, universe size.

---

## 1. Open issues (severity-ordered)

Each issue is tagged: **CRIT** (blocks ship), **HIGH** (must fix in 4 weeks), **MED** (should fix), **LOW** (cleanup).

### A. ~~Hardcoded EODHD API key in source~~ — **RESOLVED**
- **Where (was):** `tradingagents/dataflows/eodhd.py:17`, `tradingagents/dataflows/gateway.py:17`.
- **Current state:** Both files now use `os.getenv("EODHD_API_KEY", "")`. No literal key in source. Verified 2026-05-15.
- **Remaining:** Git history may still contain the old literal. `git filter-repo --replace-text` scrub and `gitleaks` pre-commit hook are still recommended.
- **Owner / status:** resolved (code); git-history scrub still open.

### B. LLM non-determinism — **MED** (was CRIT; mostly resolved)
- **Where:** 17 of 18 direct LLM `.invoke()` calls now pass both `temperature=0` and `seed=42`. Verified 2026-05-15 via `grep temperature=0` across `tradingagents/agents/`.
- **One minor gap:** `investor_profiling_agent.py:190` passes `temperature=0` but not `seed` in `.invoke()`. However, the LLM constructor at line 146-151 sets `seed=42` for the OpenAI provider, so the effective behaviour is deterministic for the default provider. Anthropic and Google providers don't support `seed` natively.
- **Risk (remaining):** Provider-level non-determinism is inherent — even `seed=42` doesn't guarantee bit-identical output across provider versions. True reproducibility requires output caching per `(ticker, date, model_version)`.
- **Owner / status:** mostly resolved; output caching is a future enhancement.

### C. Look-ahead bias in `scripts/backtester.py` — **CRIT** (overstates returns; due-diligence killer)
- **C1.** `_evaluate_trade_outcomes()` at `scripts/backtester.py:942-1050` — labels trades WIN/LOSS using forward prices fetched after the backtest, then surfaces this as "Hit Rate (fwd)" in reports. Pure look-ahead.
- **C2.** Reflection inside the loop — `scripts/backtester.py:878` calls `graph.reflect_and_remember(returns_losses)` per date. However, `trading_graph.py:380-385` checks `backtest_mode` and **queues** the pair into `_reflection_queue` instead of executing — no LLM reflection calls and no memory writes occur during the loop. **Remaining concerns:** (a) `flush_reflection_queue()` is never called by the backtester, so queued reflections are silently dropped; (b) memory retrieval via `get_memories()` has no `as_of_date` filter, so if memory were populated from a non-backtest run, a later backtest could retrieve temporally inappropriate memories.
- **C3.** Risk-free rate hardcoded to 0.05 in `scripts/backtester.py:303` and `:358`. EGP policy rate is ~22–27% in 2024–2026. Sharpe is overstated by 0.5–1.0 across the board.
- **C4.** Benchmark window misalignment at `scripts/backtester.py:587-621` — agents see ~252 days of pre-`start_date` data; benchmark return is measured only from `start_date` onward.
- **Fix:** Delete `_evaluate_trade_outcomes`. Move reflection to a post-backtest batch with ≥10-trading-day return lag (`flush_reflection_queue` already exists). Make risk-free rate configurable, default to a CBE-published EGP rate. Align benchmark window with agent training window.
- **Owner / status:** open.

### D. Universe survivorship + sample-size — **HIGH**
- **Where:** `scripts/run_real_backtests.py:31` runs only `["COMI.CA", "EAST.CA", "HRHO.CA"]`. Phase 2B (`PROOF_OF_WORK.md`) explicitly notes N=28 vs required 141 for 80% power.
- **Risk:** All published returns/Sharpe carry [0%, 100%] confidence intervals on hit rate; cherry-pick risk obvious to any quant.
- **Fix:** Expand to full EGX-30. Add ADV liquidity gate (`min_avg_daily_volume = 50_000`). Walk-forward windows. Report Wilson CIs. Disclose ticker selection rule.
- **Owner / status:** open.

### E. FastAPI server has no auth, CORS=*, no rate-limit, no heartbeat — **CRIT**
- **Where:** `server/api_server.py:277` — `allow_origins=["*"]` with TODO. No auth middleware anywhere. `uvicorn.run(..., reload=True)` in `__main__` at line 1249.
- **WebSocket:** `api_server.py:796-1095` — no ping/pong, no message ordering guarantee, no client-side reconnect protocol, `active_analyses[ticker]` lock is global (User A blocks User B on the same ticker).
- **Fix:** JWT or OIDC + roles `{analyst, pm, risk, admin}`. CORS allow-list from env. `slowapi` rate limit. Pydantic validation on every endpoint. WebSocket `seq` field, ping/pong every 20 s, reconnect spec in client.
- **Owner / status:** open.

### F. No CI, no Dockerfile, no migration tool, no `requirements.txt` — **CRIT** (deployability)
- **Where:** Repo has `pyproject.toml` + `uv.lock` but no `Dockerfile`, `.github/workflows/`, `Makefile`, `requirements.txt`, or migration tooling. `db_schema.sql` is hand-applied.
- **Fix:** `Dockerfile` (Python 3.13 slim, install via uv). `docker-compose.yml` (app + Postgres + Redis + nginx). GitHub Actions: `pytest -m "not integration"`, `ruff check`, `mypy --ignore-missing-imports`, build image. Adopt `alembic` for schema migrations.
- **Owner / status:** open.

### G. Audit trail is partial — **CRIT** (regulatory compliance)
- **Where:** `db_schema.sql` defines `analysis_sessions`, `agent_events`, `backtest_runs`, `backtest_trades` tables, but `server/api_server.py` writes audit lines from only 3 endpoints (lines 558, 617, 715). The graph itself writes to `eval_results/*.json` files only.
- **Risk:** Egyptian FRA / SOC 2 / model-risk-management cannot reconstruct who triggered what decision and why. No user identity on any record.
- **Fix:** Wire every `propagate()` and every agent invocation to insert into `analysis_sessions` + `agent_events` with: session_id, user_id (from auth), prompt fingerprint, model+version, tools called, data snapshot hash, output, timestamp, immutable.
- **Owner / status:** open.

### H. Symbol normalization inconsistency — **HIGH**
- **Where:** `.CA`-suffix logic duplicated in `dataflows/gateway.py:530-534`, `dataflows/y_finance.py:62-68`, `dataflows/eodhd.py:72-86`. Each has subtly different rules.
- **Risk:** Cache key collisions; double-suffixing; provider mismatch.
- **Fix:** Centralize in `dataflows/symbol_utils.py::normalize_egx_ticker(t) -> str`. Every provider imports from one place. Add unit test for round-trip on edge cases (`"comi"`, `"COMI"`, `"COMI.CA"`, `"COMI.ca"`, `" comi.ca "`).
- **Owner / status:** open.

### I. CSV staleness is mtime-based, not data-based — **HIGH**
- **Where:** `dataflows/local.py` and `dataflows/egx_data_refresh.py:23-57`. Filename embeds `2015-01-01-2025-03-25`; staleness checker only reads filesystem mtime. A `touch` makes 2024 data look fresh.
- **Risk:** Once `trade_date > 2025-03-25`, system silently uses outdated fundamentals.
- **Fix:** Embed `as_of_date` and `data_version` columns inside the CSV. Validate `as_of_date >= trade_date - max_age_days` on every load. Fail loud (not silent fallback) when stale.
- **Owner / status:** open.

### J. Scoring aggregation math is misleading — **HIGH** → **RESOLVED (PR 7)**
- Replaced by confidence-weighted mean with quorum rule. See §4 (Resolved) for commit details.

### K. Memory cold-start — **HIGH**
- **Where:** `tradingagents/agents/utils/memory.py:73` returns `[]` when the vector store is empty (steady state for first 10–20 trades, and **forever** when embeddings are disabled — lines 21-31). Bull/Bear/Trader/Risk-Manager receive empty `past_memory_str` and silently degrade.
- **Fix:** (a) Seed corpus of 50–100 hand-curated EGX precedents (good and bad trades) with embeddings. (b) When embeddings disabled, fall back to BM25 keyword search over the seed corpus. (c) Telemetry: log when memory returns empty so we know how often it happens.
- **Owner / status:** open.

### L. Bare `except` clauses + silent failures — **HIGH**
- **Where:** Multiple instances:
  - `dataflows/eodhd.py` — 3 bare excepts swallow network + parse errors identically
  - `dataflows/mubasher_scraper.py` — 3 bare excepts
  - `dataflows/interface.py:240-243`
  - `run_egx_prediction.py`, `scripts/benchmark_egx.py`
- **Risk:** Failed network call → `None` → analyst sees empty data → emits "neutral" recommendation it considers valid.
- **Fix:** Replace each with typed exception handling + structured `logger.error(..., extra={...})`. Let the `DataGateway` decide whether to fall back or raise.
- **Owner / status:** open.

### M. Schema validation logged-but-ignored — **MED**
- **Where:** `dataflows/gateway.py:119-124`. Pydantic validation failures log a warning, then return raw data anyway.
- **Fix:** Either enforce (raise / fall back) or remove the schemas entirely. Half-validation is worse than none — it gives false comfort.
- **Owner / status:** open.

### N. Signal extraction regex too permissive — **MED**
- **Where:** `tradingagents/graph/signal_processing.py:18` — bare `\b(BUY|SELL|HOLD)\b` is the final fallback; matches the first occurrence in any rationale paragraph, including "do not BUY because…".
- **Fix:** Require a JSON `action` field in the trader's structured output. Refuse to fall back to bare regex; emit `HOLD` + `parse_failed=True` and surface as a data-quality flag.
- **Owner / status:** open.

### O. `print()` calls in production code paths — **MED**
- **Where:** `dataflows/y_finance.py:150, 377`, `dataflows/local.py:147`, others.
- **Fix:** Replace with `logging.getLogger("tradingagents.<module>")` per the project's stated standard.
- **Owner / status:** open.

### P. Mubasher scraper is fragile — **MED**
- **Where:** `dataflows/mubasher_scraper.py:95-100`. CSS selectors on `live.mubasher.info` change frequently. `max_retries=1` hardcoded.
- **Fix:** Either move to a paid EGX data feed (Refinitiv / EODHD premium / EGX direct) or accept that mubasher is best-effort and surface explicit `data_source: "mubasher_unavailable"` to agents instead of silent fallback.
- **Owner / status:** open.

### Q. Twitter/X anonymous scraping is dead — **RESOLVED (PR 10)**
- `dataflows/social_media_sources/twitter_source.py` deleted. `"twitter"` removed from `PLATFORM_SOURCES`. `scripts/twitter_pipeline/v2/sources/twitter_authed.py` deleted.
- Apify Facebook + Telegram + Reddit are the active sources. Revisit only if Apify Twitter/X actor becomes available.

### R. v2 entity registry too narrow — **MED**
- **Where:** `scripts/twitter_pipeline/v2/entities.py::SYMBOL_REGISTRY`. Live FB groups regularly mention small-cap names (`توطين التكنولوجيا`, `عبور لاند`, `ابن سينا`) not in the registry → `symbols_covered = 0` on most runs.
- **Fix:** Expand registry to cover EGX-30 + EGX-70 + main-market top-200, with Arabic name aliases. High-leverage win.
- **Owner / status:** open.

### S. Mubasher source in v2 returns 0 — **RESOLVED (PR 10)**
- `scripts/twitter_pipeline/v2/sources/mubasher_news.py` deleted. Selectors were permanently broken upstream with no maintainer fix planned.
- Replacement path: EGX direct news API or a re-implemented scraper when selectors can be verified — out of scope for current sprint.

### T. Dashboard is a research demo, not a trading platform — **HIGH** (product framing)
- **Where:** `dashboard/src/`. Has BacktestPage + PredictionPage + AdvancedBacktestPage. Missing: live position blotter, order management, Greeks/exposure, audit-trail export, multi-user roles, Arabic/RTL UI, dark mode, mobile responsive.
- **Fix (4-week scope):** Stop calling it a trading platform. Add disclaimer modal. Add audit-trail CSV/PDF export. Add Arabic/RTL toggle. Add model-version + build-hash footer. Defer order-management to post-launch.
- **Owner / status:** open.

### U. CLI not scriptable — **MED**
- **Where:** `cli/main.py` — all input via `questionary.text/select`, no CLI flags, no `--output json`, no `--resume SESSION_ID`.
- **Fix:** Add `argparse` with `--ticker`, `--date`, `--analysts`, `--output {tui,json}`, `--no-interactive`. Backwards-compatible with current TUI as default.
- **Owner / status:** open.

### V. Duplicate files — **LOW**
- `AGENTS.md` and `CLAUDE.md` are byte-identical (22,059 bytes each).
- Root-level `sentiment_engine.py`, `persistent_memory.py`, `redis_pubsub.py` overlap with their package equivalents.
- **Fix:** Pick one source of truth per file, delete the other (or symlink).
- **Owner / status:** open.

### W. Magic numbers everywhere — **LOW**
- `MAX_RECORDS=20`, `2000-char truncation`, `50_000` ADV floor, `0.10` price-limit, `0.189%` cost-side stack, `0.05` risk-free, `120-day` filing lag.
- **Fix:** Pull into `tradingagents/egx_constants.py` with sourced citations (FRA decree, EGX rulebook, broker tariff).
- **Owner / status:** open.

---

## 2. Solid subsystems (don't break these)

| Subsystem | Quality evidence | Regression gate |
|---|---|---|
| Fundamentals Phase 1A | 79 unit tests passing (`tests/test_fundamentals_phase1a.py`); sector classification verified across 31 tickers | Run before any change to `agents/analysts/fundamentals/` |
| Fundamentals Phase 1B | 7/7 analytical gates green (`tests/phase1b_audit.py`); 9/9 ratio cross-checks within ±5% | Run before changing financial calculator or sector config |
| Fundamentals CoT pipeline (Phase 2A) | Inter-stage validation, deterministic fallback, schema enforcement | `tests/test_fundamentals_phase2b.py -m "not integration"` |
| Sentiment routing | FinBERT (English) / CAMeLBERT-DA (Arabic dialect) / XLM-R (mixed) with rule-based lexicon fallback | Manual smoke test on Arabic + English samples |
| Risk-manager deterministic checks | Hard-coded `EGX_RISK_LIMITS`; pre-LLM veto logic | `tests/test_risk_manager_veto.py`, `tests/test_egx_constraints.py` |
| Signal processor regex extractor | No-LLM, priority-ordered patterns | `tests/test_signal_processor.py` |
| v2 social pipeline architecture | 7-stage layered flow with `NO_SIGNAL` honesty + Apify integration | Manual run + log inspection |
| Backtrader classical baseline (`scripts/bt_benchmark.py`) | Pure rule-based RSI/MACD/BB; no LLM; deterministic | Re-run for any ticker; outputs are repeatable |
| LangGraph wiring (`graph/setup.py`) | Clean fan-out, typed state, no agent-to-agent imports | `tests/test_graph_wiring.py` |

---

## 3. The 4-week ship plan ("Research Console" framing)

### Week 1 — Stop the bleeding
1. Rotate EODHD key, scrub git history, install `gitleaks` pre-commit. **(A)**
2. Add `LLM_INVOKE_KWARGS` constant; route every `.invoke()` through it; log model fingerprint into `agent_events`. **(B)**
3. Wire `analysis_sessions` + `agent_events` Postgres writes into `propagate()`. **(G)**
4. Write `Dockerfile` + `docker-compose.yml`. **(F)**
5. Replace bare `except` clauses with typed handlers + structured logging. **(L, O)**
6. Add "Not investment advice — for research use, human-reviewed" banner to dashboard + CLI. **(T)**

### Week 2 — Defensible backtest
1. Delete `_evaluate_trade_outcomes`. **(C1)**
2. Move reflection out of the loop into post-backtest batch with ≥10-day lag. **(C2)**
3. Make risk-free rate configurable, default to a sourced CBE EGP rate. **(C3)**
4. Align benchmark window with agent training window. **(C4)**
5. Expand backtest universe to full EGX-30, add ADV gate, walk-forward, Wilson CIs. **(D)**
6. Re-publish `PROOF_OF_WORK.md` Phase 2B with the new sample. Honest results.

### Week 3 — Server / API / dashboard hardening
1. JWT/OIDC auth + roles `{analyst, pm, risk, admin}`. **(E)**
2. CORS allow-list from env, `slowapi` rate-limit, Pydantic input validation everywhere. **(E)**
3. WebSocket: ping/pong every 20 s, `seq` ordering, client reconnect spec. **(E)**
4. Rewrite `scoring.py` aggregator: confidence-weighted median-of-means, single-scale confidences. **(J)**
5. Dashboard: Arabic + RTL toggle, disclaimer modal, audit-trail CSV/PDF export, model-version footer. **(T)**
6. CLI flags: `--ticker --date --analysts --output json --no-interactive`. **(U)**

### Week 4 — Observability + MRM + ship review
1. Prometheus metrics: LLM latency p50/p95, vendor failure rates, decision counts by action.
2. Sentry / OpenTelemetry tracing across the graph.
3. GitHub Actions: pytest + ruff + mypy + Docker build. **(F)**
4. Alembic schema migration tool. **(F)**
5. Model card, validation report, monitoring plan, kill-switch — written, reviewed, signed off.
6. Internal red-team walkthrough: compliance officer + senior PM + senior quant. Gate-by-gate ship.

### Deliberately out of scope for the 4-week ship
- Live broker integration / order execution
- Live EGX-direct or Bloomberg/Refinitiv data feeds (replace yfinance)
- Multi-portfolio / live position management
- Online learning / live reflection
- pgvector at scale (current ChromaDB / JSONB is fine for 4 weeks)

---

## 4. Resolved issues

> When an open issue is fixed, move it here with commit hash + date + 1-sentence description. Keep the historical record.

- **§J — Scoring aggregation misleading (2026-05-02, PR 7)** — Replaced naive linear blend in `scoring.py` with confidence-weighted mean + quorum rule (≥2 directional analysts). `propagate_confidence()` now returns `overall_status` and `position_size_multiplier`. `calculate_unified_score()` returns 5-tuple. Sentiment never changes directional score; only multiplies confidence and position size.

---

## 4a. Sentiment redesign progress (Phase 3)

> Tracks the multi-PR sentiment subsystem rebuild approved 2026-05-01. Each PR is foundation-only until wired in. See Phase 1 audit + Phase 2 redesign in session transcript.

- **PR 1 (2026-05-01) — Foundation: typed contracts + NO_SIGNAL sentinel + central taxonomy + liquidity tiers + config.** Adds `tradingagents/sentiment/{contracts,taxonomy,liquidity_tiers,config}.py` with no behavioral wiring. Introduces `NoSignalReason` carrying `gate_failed` + `human_readable` + `metrics`, rendered to canonical audit string `NO_SIGNAL: <reason> (gate=..., k=v, ...)` for propagation into logs/agent context/reports/audit. Centralizes the 6-sector EGX taxonomy (banks/real_estate/industry/telecom_tech/financial_services/food_bev) with Arabic aliases per sector; legacy 4-sector fundamentals shim via `to_fundamentals_sector()` keeps `tests/test_fundamentals_phase1a.py` (79 tests) green. Liquidity tiers MEGA(8)/MID(5)/SMALL(3) seeded as PROVISIONAL; calibration script deferred to PR 10. Tests: `tests/test_sentiment_{contracts,taxonomy,liquidity_tiers}.py` — 37 passing.
- **PR 2 (2026-05-01) — Entity-layer fix: phrase-boundary regex, EGX_* key rename, SCTS alias cleanup.** Four targeted changes to `scripts/twitter_pipeline/v2/entities.py` and `aggregator.py`: (1) replaced `_alias_in_text` substring match for multi-word Arabic aliases with pre-compiled phrase-boundary regex (`_compile_phrase_pattern`) — eliminates Arabic morphological suffix-extension false positives (e.g. "التجاري الدولية" no longer matches COMI alias "التجاري الدولي"); (2) renamed `MARKET_INDEX_TERMS` keys EGX30/70/100 → EGX_30/70/100 so aggregator's `startswith("EGX_")` correctly routes all index mentions to `EGX_MARKET` rather than per-stock pools; (3) removed ambiguous standalone alias "توطين التكنولوجيا" from SCTS (kept full company-name aliases); (4) documented the intentional secondary market contribution in `aggregator.py:122-126`. Resolves MEMORY §R (partial — phrase-boundary fix) and the EGX_MARKET routing bug identified in Phase 1 Sub-agent 2 audit. Tests: `tests/test_sentiment_entity_extraction.py` — 36 passing (including 5 adversarial market→stock contamination tests). All prior suites green (fundamentals 79/79, pipeline 8/8, PR 1 37/37).

- **PR 3 (2026-05-01) — Layer A: MarketSentiment aggregator + dead source cleanup.** Adds `tradingagents/sentiment/market.py` implementing `compute_market_sentiment(posts, *, reference_time)` with three binary hard gates: (1) `market.n_total_posts` ≥ 50, (2) `market.n_distinct_sources` ≥ 2, (3) `market.recent_24h_share` ≥ 0.30. First failing gate emits `MarketSentiment(status=NO_SIGNAL, reason=NoSignalReason(...))` with the canonical audit string propagated to logs. On success, computes weighted-mean score → `MarketRegime` (EUPHORIA/GREED/NEUTRAL/FEAR/PANIC via ±0.15/±0.35 thresholds), per-post score std → `VolatilityMood` (CALM/ELEVATED/STRESSED via 0.35/0.55 thresholds), and `confidence` (40% size + 35% recency + 25% clarity). Removes `facebook_groups`, `twitter_authed`, and `mubasher_news` from `pipeline_v2.py` source loop (dead sources, always 0 posts; files retained until PR 10 cleanup). Exports `MarketDataPoint` and `compute_market_sentiment` from `tradingagents/sentiment/__init__.py`. Tests: `tests/test_sentiment_market.py` — 86 passing across 10 test classes (gate paths, regime/volatility bands, confidence properties, weighted stats, timestamp parsing, log-format invariants). All prior regression gates green: fundamentals 79/79, pipeline 8/8, PR 1 37/37, PR 2 36/36.

- **PR 4 (2026-05-02) — Layer B: SectorSentiment aggregator + bilingual sector classifier.** Adds `tradingagents/sentiment/sector.py` implementing `compute_sector_sentiment(sector, posts)` with three binary hard gates: (1) `sector.n_sector_posts` ≥ 10, (2) `sector.n_distinct_days` ≥ 3, (3) `mean_entity_conf` ≥ 0.70. First failing gate emits `SectorSentiment(status=NO_SIGNAL, reason=NoSignalReason(...))` with canonical audit string. On SIGNAL: computes weighted-mean score (weight-normalised; falls back to simple mean on zero weights), confidence (40% size + 35% spread/days + 25% clarity). Adds `classify_post_to_sector(text) → SectorEnum | None` for bilingual (Arabic phrase + English word-boundary) sector keyword routing using taxonomy aliases. IEEE-754 fix: `_mean_entity_conf` rounds to 6 d.p. to avoid accumulation artifacts at the 0.70 boundary. Exports `SectorDataPoint`, `compute_sector_sentiment`, `classify_post_to_sector` from `tradingagents/sentiment/__init__.py`. Tests: `tests/test_sentiment_sector.py` — 75 passing across 11 test classes (all gate paths, gate ordering, all 6 sectors, bilingual matching, score/confidence computation, boundary values, contract invariants, internal helpers). Full regression: 320/320 passing (fundamentals 79, pipeline 8, PR 1 37, PR 2 36, PR 3 86, PR 4 75).

- **PR 5 (2026-05-02) — Layer C: StockSentiment aggregator + pre-LLM NO_SIGNAL gate.** Adds `tradingagents/sentiment/stock.py` implementing `compute_stock_sentiment(ticker, posts, reference_time, market_score=None)` with four binary hard gates applied to non-spam posts only: (1) `n_strong_mentions` ≥ tier threshold (entity_conf ≥ 0.85), (2) `n_distinct_authors` ≥ tier threshold (empty-author posts collapsed to single `"_anonymous"` bucket), (3) `n_distinct_sources` ≥ tier threshold, (4) `recent_72h_share` ≥ 0.60. Tier thresholds: MEGA(8/5/3), MID(5/3/2), SMALL(3/2/2). On SIGNAL: weighted-mean score, confidence (40% size + 35% diversity/authors + 25% clarity), and `contradicts_market` flag (True when market_score and stock score are both ≥ 0.15 abs and opposite sign). Adds `prefetched_stock_datapoints: Optional[List[Dict]]` field to `AgentState`. Adds `_try_layer_c_gate` helper and pre-LLM gate to `social_media_analyst.py`: if `prefetched_stock_datapoints` is non-empty and Layer C returns NO_SIGNAL → LLM skipped, `sentiment_report = "Social sentiment: insufficient data — excluded."`. Exports `StockDataPoint`, `compute_stock_sentiment` from `tradingagents/sentiment/__init__.py`. Tests: `tests/test_sentiment_stock.py` — 89 passing across 15 test classes (all tier/gate paths, gate ordering, spam exclusion, recency edge cases, contradicts_market, all 5 MEGA tickers, boundary/entity-conf threshold, score/confidence computation, anonymous-author bucketing, contract invariants, pre-LLM gate wiring). Full regression: 409/409 passing (fundamentals 79, pipeline 8, PR 1 37, PR 2 36, PR 3 86, PR 4 75, PR 5 89).

- **PR 8 (2026-05-02) — LLM-as-explainer demotion + bull/bear NO_SIGNAL guard.** Rewrites `social_media_analyst.py`: LLM prompt schema changed to `narrative` + `cited_post_ids` only (no `sentiment_score`, `direction`, `confidence`). `_LLM_ROLE_INSTRUCTION` constant injected into both prefetch-mode and tool-mode prompts. Adds `_compute_blend_result()` which tries to parse `prefetched_social_sentiment` as JSON (v2 pipeline format) to reconstruct MarketSentiment/MacroSentiment/SectorSentiment and call `blend_sentiment()`, falling back to pass-through when no structured data. `sentiment_blend_result` dict written to state in all code paths (SIGNAL, NO_SIGNAL, explainer). `_build_sentiment_report()` produces a researcher-safe string surfacing the LLM narrative + blend modifiers with an explicit "execution only — not directional" note. `bull_researcher.py` and `bear_researcher.py` each gain `_format_sentiment_section()` helper + `sentiment_section` variable injected into their prompts: detects `_NO_SIGNAL_TEMPLATE` / "insufficient data" phrases and substitutes a clear EXCLUDED instruction; otherwise surfaces blend multipliers as non-directional execution context. Tests: `tests/test_sentiment_workflow.py` — 47 passing. Full regression: 645/645.

- **PR 7 (2026-05-02) — Layer E: Blender/propagation rewrite.** Replaces naive linear blend in `scoring.py` with confidence-weighted mean (weight = structural analyst weight × per-analyst confidence) and quorum rule (≥2 directional analysts required → else `INSUFFICIENT_DATA`). Adds `SentimentBlend` NamedTuple + `blend_sentiment(macro, market, sector)` function applying market-regime, macro-direction, and sector-tilt multipliers (sentiment never flips direction, only modulates confidence × and position size ×). Adds `blend_from_dict()` for AgentState round-trip. `propagate_confidence()` rewritten to enforce quorum, read `sentiment_blend_result` from state, return `overall_status` ("OK" | "INSUFFICIENT_DATA") and `position_size_multiplier`. `AgentState` gains `sentiment_blend_result: Optional[Dict]` field. `calculate_unified_score()` now returns 5-tuple adding `overall_status`; 3 callers in `system_validation.py` updated. Resolves MEMORY §J (misleading scoring aggregation). Tests: `tests/test_sentiment_blender.py` — 74 passing. Full regression: 598/598 passing.

- **PR 6 (2026-05-02) — Layer A0: MacroSentiment aggregator with source-credibility and corroboration gates.** Adds `tradingagents/sentiment/macro.py` implementing `compute_macro_sentiment(posts, reference_time)` with three sequential binary hard gates: (1) `macro.source_credibility` — at least one post from OFFICIAL/TIER1/TIER2 source (RUMOR excluded); (2) `macro.corroborating_sources` — at least one `(category, direction)` group has ≥2 distinct source_domains within 48 h; (3) `macro.half_life_expired` — at least one corroborated event is within its category half-life window. Source credibility ladder: OFFICIAL (cbe.org.eg, mof.gov.eg, fra.gov.eg, egx.com.eg), TIER1_NEWS (reuters.com, bloomberg.com, mubasher.info), TIER2_NEWS (enterprise.press, almalnews.com, dailynewsegypt.com, alborsaanews.com). Half-lives by category: RATE_DECISION=120 h, EGP_DEVALUATION=240 h, IMF_PROGRAM=168 h, INFLATION_PRINT=72 h, TAX_REGULATION=168 h, GEOPOLITICAL=48 h, COMMODITY_SHOCK=72 h. Composite regime: RISK_OFF dominates RISK_ON dominates NEUTRAL. Event confidence = 0.60×credibility_weight + 0.40×corroboration_ratio. Adds `MacroDataPoint` NamedTuple (timestamp, source_domain, headline, category, direction, sentiment_score, weight, post_id, url). Updates `tradingagents/sentiment/__init__.py` to export `MacroDataPoint`, `compute_macro_sentiment`. Tests: `tests/test_sentiment_macro.py` — 115 passing across 14 test classes (all 3 gate paths, composite regime rules, source credibility ladder, domain normalisation, half-life by category, corroboration window boundary, MacroEvent field correctness, magnitude bands, confidence formula, contract invariants, multiple concurrent events, audit string format). Full regression: 524/524 passing (fundamentals 79, pipeline 8, PR 1 37, PR 2 36, PR 3 86, PR 4 75, PR 5 89, PR 6 115).

- **PR 9 (2026-05-02) — Surfacing + audit trail.** Adds `tradingagents/sentiment/surfacing.py` with four pure functions: `extract_sentiment_audit_record(final_state)` — flat dict with all sentiment audit fields; `format_sentiment_for_api(final_state)` — frontend-friendly dict; `format_sentiment_for_cli(final_state)` — multi-line Rich TUI string; `build_sentiment_context_event(session_id, ticker, trade_date, final_state, logged_at)` — JSONL event dict. `trading_graph._log_state()` now always appends `"sentiment_audit"` block to the eval-results JSON. `server/api_server.py` appends a `SENTIMENT_CONTEXT` JSONL event alongside SESSION_START/QUICK_ANALYSIS_RESULT when `_final_state` is present. `cli/main.py` renders a "VI. Sentiment Context (Phase 3)" Rich Panel after display_complete_report(), colour-coded cyan (signal) / yellow (no-signal), with blend multipliers and narrative. All functions have try/except guards — never crash the audit write or the CLI display. Exports added to `tradingagents/sentiment/__init__.py`. Tests: `tests/test_sentiment_surfacing.py` — 50 passing across 9 test classes. Full regression: 633/633 passing.

- **PR 10 (2026-05-02) — Cleanup + calibration script + final.** Deleted four zombie source files: `scripts/twitter_pipeline/v2/sources/twitter_authed.py`, `facebook_groups.py`, `mubasher_news.py`, and `tradingagents/dataflows/social_media_sources/twitter_source.py` (all returned 0 posts in production). Removed `"twitter"` from `PLATFORM_SOURCES` in `dataflows/social_media_sources/aggregator.py` and the dead `from .twitter_source import fetch_twitter_data` import. Updated `pipeline_v2.py` docstring and inline comments to reflect removal. Added `scripts/calibrate_tier_thresholds.py`: offline-safe script that reads `results_*.json` files from `scripts/twitter_pipeline/v2/logs/`, computes per-tier p5/p25/p50/p75/p95 statistics for `n_strong_mentions`/`n_distinct_authors`/`n_distinct_sources`, and recommends updated MEGA/MID/SMALL thresholds at the 25th-percentile level (≈75% of trading days would pass). Keeps current thresholds when <10 observations. Supports `--format json` for CI patch output. Tests: `tests/test_pr10_cleanup.py` — 47 passing across 7 test classes. Full regression: 680/680 passing.

- **Phase 3 addendum (2026-05-04) — Manual test harness + deep documentation.** Added two deliverables completing the Phase 3 documentation layer:
  - `scripts/test_sentiment_pipeline.py` — standalone CLI test harness (no LLM, no full graph) exercising Layers A0/A/B/C/E with 5 built-in scenarios (positive, no_signal, conflict, low_liquidity, multilingual). Fixed scenario data builders to correctly provide 3 distinct sources for MEGA-tier tickers (COMI/TMGH/ETEL); updated low_liquidity scenario to MID-tier thresholds (DOMT is MID, not SMALL). CLI flags: `--ticker`, `--date`, `--scenario`, `--raw-posts-file`, `--verbose`, `--save-report`, `--show-intermediate-json`, `--list-scenarios`.
  - `agent_docs/sentiment_architecture.md` — comprehensive Mermaid-diagram-based technical and trading documentation (14 sections, ~600 lines): architecture overview, layer-by-layer specs, sequence diagram, model interaction map (CAMeLBERT-DA/FinBERT/XLM-R/VADER), signal propagation flowchart, confidence formulas with worked COMI.CA example, NO_SIGNAL guide, gate specifications, quorum rule, end-to-end worked example, Arabic processing details, known limitations table, files quick reference.
  - `tests/test_sentiment_harness.py` — 106 regression tests for the harness covering all 5 data builders, all 5 layer integrations per scenario, `build_report()` JSON structure, `load_from_pipeline_file()` robustness, `_parse_ts()`, liquidity tier assignments. All 106 passing.
  - **Total regression: 786/786 passing** (680 PR 1-10 + 106 new harness + unchanged fundamentals 79/79 + pipeline 8/8).

### Open follow-ups (will be opened formally as issues when their PR lands)
- Fundamentals migration to consume `tradingagents.sentiment.taxonomy.SectorEnum` directly instead of via shim (separate PR; legacy 4-sector module stays canonical for fundamentals until then).
- Run `calibrate_tier_thresholds.py` after accumulating ≥30 days of real pipeline_v2 results and apply the recommended threshold update to `liquidity_tiers.py`.

---

## 5. Decisions log

> Architecture / product decisions made during the audit. Append-only.

- **2026-04-29** — Positioning fixed as "AI-augmented research / decision-support tool with human-in-the-loop". Any UI/copy implying autonomous trading is incorrect and to be removed.
- **2026-04-29** — Universe expansion (EGX-30 → EGX-70 → main-market top-200) is in-scope for Week-2.
- **2026-04-29** — Live broker integration is OUT of 4-week scope. Defer to Phase 2 (months 2–3).
- **2026-04-29** — Backtester reflection inside the loop is a methodological flaw, not a feature. Will be removed.
- **2026-04-29** — Mubasher scraping is best-effort. Replace with paid feed during Phase 2.
- **2026-04-29** — Apify is the primary social source going forward. Twitter/Nitter strategies kept dormant for opportunistic recovery only.

---

## 6. Compliance / regulatory notes

These are gating items for any deployment to a regulated firm. Track separately from engineering work.

| Item | Status | Owner |
|---|---|---|
| Egyptian FRA review of "investment advice" framing | Not started | Legal / compliance |
| Egypt Personal Data Protection Law 151/2020 — social-scraping lawful basis, retention policy, deletion rights | Not started | Legal |
| GDPR exposure (any EU users) | Not started | Legal |
| Model risk management framework (model card, validation report, monitoring plan, kill-switch) | Not started | Engineering + risk |
| SOC 2 Type 1 readiness | Not started | Engineering |
| Pen-test (external) | Not started | Engineering |
| Audit trail acceptance test (regulator can reconstruct any decision) | Not started | Engineering |
| Disclaimer copy approved by legal | Not started | Legal + product |

---

## 7. Cost / capacity notes

- LLM cost per analysis: ~8–10 calls × ~3–5 k input + ~1 k output tokens at DeepSeek pricing ≈ **6–10 ¢ / ticker / date**.
- Full EGX-30 daily run ≈ **$2–3 / day** in LLM cost.
- 252-day walk-forward backtest on EGX-30 ≈ **$500–800** in LLM cost.
- Latency: ~60–120 s end-to-end per ticker (sequential analysts → debate → trader → risk).
- Implication: end-of-day signals are economically and latency-feasible. Intraday is **not** at this architecture.

---

## 8. Differentiation thesis (for the senior PO)

**Defensible value vs. Bloomberg / Refinitiv / generic AI trading frameworks:**
1. Egyptian Arabic dialect sentiment (CAMeLBERT-DA + custom slang dictionary).
2. EGX-specific risk envelope baked into deterministic veto logic, not just prompt instructions.
3. Layered social-signal pipeline with explicit `NO_SIGNAL` when sample size is insufficient.
4. Sector-aware fundamentals analyst with safety floors calibrated on real EGX data.

**Eroded by (until fixed):**
- Backtest results that fail first-week quant due-diligence (look-ahead bias).
- Yahoo Finance as the live-data backbone (consumer-grade for a regulated firm).
- No clear evidence the LLM agents add alpha beyond the deterministic fundamentals pipeline (Phase 2B Gates 3–5 inconclusive at N=28).

**Sales framing:** "EGX research console with Egyptian-Arabic + EGX-specific edge." Defensible.
**Sales framing to avoid:** "Alpha generator that beats EGX-30." Not yet supported by evidence.

---

## 9. How to update this file

When you discover, validate, or resolve an issue:

1. **New issue:** append to §1 with file:line, severity tag (CRIT/HIGH/MED/LOW), 1-sentence remediation, owner, status.
2. **Issue resolved:** move from §1 to §4 (Resolved) with commit hash + date.
3. **Subsystem proven solid:** add row to §2 with the regression gate command.
4. **Plan changes:** edit §3, note the change in §5 (Decisions log) with the date.
5. **Compliance progress:** update §6 status.

Keep entries short and operational. This file is a working tool, not a narrative document.
