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

### A. Hardcoded API keys — **CRIT** (security + cost exposure) — PARTIALLY RESOLVED
- **Original:** `tradingagents/dataflows/eodhd.py:17` hardcoded EODHD key as `os.getenv()` fallback.
- **Escalated (2026-05-09):** `tradingagents/default_config.py:4-11` was additionally setting **four** live keys via module-level `os.environ[…]` calls (GROQ, OPENAI, EODHD, Google), overwriting any `.env`-loaded values on every import.
- **Resolved (2026-05-09):** Removed all 4 `os.environ[…]` assignments from `default_config.py`. Replaced EODHD hardcoded fallback in `eodhd.py:17` with empty string. Fixed `main.py`, `run_egx_prediction.py`, `api_server.py` import order so `load_dotenv()` runs before any tradingagents import. Added `EGX_TICKERS` module-level list to `default_config.py` (was missing, imported by `taxonomy.py`).
- **Remaining:** Keys are still in git history — rotate all 4 keys and scrub history with `git filter-repo --replace-text`. Add `gitleaks` pre-commit hook.
- **Owner / status:** open (history scrub + key rotation pending).

### B. LLM non-determinism — **CRIT** (audit, reproducibility, backtest validity)
- **Where:** `tradingagents/graph/trading_graph.py:104-105` pins `temperature=0` only on the deep/quick LLM constructors. Per-agent `.invoke()` calls don't re-pin and never set `seed`. Examples: `bull_researcher.py:155`, `bear_researcher.py:172`, `trader.py:286`, `risk_manager.py:647`.
- **Risk:** Same `(ticker, date)` produces different theses per run. Backtest results unrepeatable. Audit trail can't be reconstructed.
- **Fix:** Define a single `LLM_INVOKE_KWARGS = {"temperature": 0, "seed": 42}` constant. Route every `.invoke(...)` through it. Persist model name + provider + fingerprint into `agent_events.structured_output` per call.
- **Owner / status:** open.

### C. Look-ahead bias in `scripts/backtester.py` — **CRIT** (overstates returns; due-diligence killer) — **PARTIALLY RESOLVED (C2 closed in DB-infra PR 7, 2026-05-14)**
- **C1.** `_evaluate_trade_outcomes()` at `scripts/backtester.py:942-1050` — labels trades WIN/LOSS using forward prices fetched after the backtest, then surfaces this as "Hit Rate (fwd)" in reports. Pure look-ahead. **OPEN.**
- **C2.** ~~Reflection inside the loop — `scripts/backtester.py:855-881` calls `graph.reflect_and_remember(returns_losses)` per date with realized PnL, updating agent memory before the next decision date. Causal leakage even if no labels are reported.~~ **RESOLVED (DB-infra PR 7).** In-loop call removed; per-date `(date, state)` captured into a queue; `_flush_reflection_with_forward_returns(graph, lag_days=10)` runs once after `_evaluate_trade_outcomes` using realized 20-day forward returns. Reflection memory rows now carry JSON-encoded `outcome` metadata. Regression gate test `test_in_loop_reflect_and_remember_call_removed` blocks reintroduction.
- **C3.** Risk-free rate hardcoded to 0.05 in `scripts/backtester.py:303` and `:358`. EGP policy rate is ~22–27% in 2024–2026. Sharpe is overstated by 0.5–1.0 across the board. **OPEN.**
- **C4.** Benchmark window misalignment at `scripts/backtester.py:587-621` — agents see ~252 days of pre-`start_date` data; benchmark return is measured only from `start_date` onward. **OPEN.**
- **Fix for remaining open items:** Delete `_evaluate_trade_outcomes` (C1). Make risk-free rate configurable, default to a CBE-published EGP rate (C3). Align benchmark window with agent training window (C4).
- **Owner / status:** C1/C3/C4 open — separate quant/backtest PR.

### D. Universe survivorship + sample-size — **HIGH**
- **Where:** `scripts/run_real_backtests.py:31` runs only `["COMI.CA", "EAST.CA", "HRHO.CA"]`. Phase 2B (`PROOF_OF_WORK.md`) explicitly notes N=28 vs required 141 for 80% power.
- **Risk:** All published returns/Sharpe carry [0%, 100%] confidence intervals on hit rate; cherry-pick risk obvious to any quant.
- **Fix:** Expand to full EGX-30. Add ADV liquidity gate (`min_avg_daily_volume = 50_000`). Walk-forward windows. Report Wilson CIs. Disclose ticker selection rule.
- **Owner / status:** open.

### E. FastAPI server has no auth, CORS=*, no rate-limit, no heartbeat — **CRIT**
- **Where:** `server/api_server.py:277` — `allow_origins=["*"]` with TODO. No auth middleware anywhere. `uvicorn.run(..., reload=True)` in `__main__` at line 1249.
- **WebSocket:** `api_server.py:796-1095` — no ping/pong, no message ordering guarantee, no client-side reconnect protocol, `active_analyses[ticker]` lock is global (User A blocks User B on the same ticker).
- **Stabilization note (2026-05-12):** Auth was explicitly deferred for the current runtime-stabilization pass. `/api/health` now surfaces Redis/Postgres/memory degraded-state diagnostics, but auth/CORS/rate-limit remain open production blockers.
- **Fix:** JWT or OIDC + roles `{analyst, pm, risk, admin}`. CORS allow-list from env. `slowapi` rate limit. Pydantic validation on every endpoint. WebSocket `seq` field, ping/pong every 20 s, reconnect spec in client.
- **Owner / status:** open.

### F. No CI, no Dockerfile, no migration tool, no `requirements.txt` — **CRIT** (deployability)
- **Where:** Repo has `pyproject.toml` + `uv.lock` but no `Dockerfile`, `.github/workflows/`, `Makefile`, `requirements.txt`, or migration tooling. `db_schema.sql` is hand-applied.
- **Fix:** `Dockerfile` (Python 3.13 slim, install via uv). `docker-compose.yml` (app + Postgres + Redis + nginx). GitHub Actions: `pytest -m "not integration"`, `ruff check`, `mypy --ignore-missing-imports`, build image. Adopt `alembic` for schema migrations.
- **Owner / status:** open.

### G. Audit trail is partial — **CRIT** (regulatory compliance) — **RESOLVED (DB-infra PR 5 + 6, 2026-05-14)**
- **Where:** `db_schema.sql` defines `analysis_sessions`, `agent_events`, `backtest_runs`, `backtest_trades` tables, but `server/api_server.py` writes audit lines from only 3 endpoints (lines 558, 617, 715). The graph itself writes to `eval_results/*.json` files only.
- **Risk:** Egyptian FRA / SOC 2 / model-risk-management cannot reconstruct who triggered what decision and why. No user identity on any record.
- **Resolved:** PR 5 wires `propagate()` to mint a UUID session_id, write one `analysis_sessions` row + up to 13 `agent_events` rows per call via new `tradingagents/db/audit_writer.py`. PR 6 wires `scripts/backtester.py` + `scripts/bt_benchmark.py` to write `backtest_runs` + `backtest_trades`. `scripts/db/apply_schema_v2.sql` adds `user_id` (NULL until JWT lands per §E) + `model_fingerprint JSONB` columns idempotently. Writers never raise into the graph. See §4 resolved entry for full details.

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

### K. Memory cold-start — **HIGH** — **RESOLVED (DB-infra PR 3 + 4 + 8, 2026-05-14)**
- **Where:** `tradingagents/agents/utils/memory.py:73` returns `[]` when the vector store is empty (steady state for first 10–20 trades, and **forever** when embeddings are disabled — lines 21-31). Bull/Bear/Trader/Risk-Manager receive empty `past_memory_str` and silently degrade.
- **Stabilization note (2026-05-12):** ChromaDB is now the default vector-memory backend via `memory_backend="chroma"`. Postgres/pgvector memory is opt-in only (`TRADINGAGENTS_MEMORY_BACKEND=postgres`) so pgvector setup cannot block backend/dashboard startup.
- **Resolved:**
  - PR 3 — `chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)` so memory survives restarts.
  - PR 4 — `get_memories(where, min_similarity)` + canonical row metadata so filtered retrieval works.
  - PR 8 — 29 hand-curated EGX seed memories in `tradingagents/agents/utils/seed_memories.py` auto-load on first init; BM25 keyword fallback (`rank-bm25`) handles embedding-disabled backends (DeepSeek/Groq); `memory.empty_return` telemetry logs sub-threshold returns. See §4 resolved entry for full details.

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

### O. `print()` calls in production code paths — **MED** — PARTIALLY RESOLVED
- **Where:** `dataflows/y_finance.py:150, 377`, `dataflows/local.py:147`, others. Escalated (2026-05-09): also found in `agents/risk_mgmt/risk_scorer.py` (production path: `_generate_atr_stop_loss()` and ADV throttle).
- **Resolved (2026-05-09):** `risk_scorer.py` — 2 `print()` calls replaced with `logger.info()`.
- **Remaining:** `dataflows/y_finance.py`, `dataflows/local.py`, and others.
- **Fix:** Replace with `logging.getLogger("tradingagents.<module>")` per the project's stated standard.
- **Owner / status:** open (non-risk-scorer paths).

### P. Mubasher scraper is fragile — **MED**
- **Where:** `dataflows/mubasher_scraper.py:95-100`. CSS selectors on `live.mubasher.info` change frequently. `max_retries=1` hardcoded.
- **Fix:** Either move to a paid EGX data feed (Refinitiv / EODHD premium / EGX direct) or accept that mubasher is best-effort and surface explicit `data_source: "mubasher_unavailable"` to agents instead of silent fallback.
- **Owner / status:** open.

### Q. Twitter/X anonymous scraping is dead — **RESOLVED (PR 10)**
- `dataflows/social_media_sources/twitter_source.py` deleted. `"twitter"` removed from `PLATFORM_SOURCES`. `scripts/social_pipeline/v2/sources/twitter_authed.py` deleted.
- Apify Facebook + Telegram + Reddit are the active sources. Revisit only if Apify Twitter/X actor becomes available.

### R. v2 entity registry too narrow — **MED**
- **Where:** `scripts/social_pipeline/v2/entities.py::SYMBOL_REGISTRY`. Live FB groups regularly mention small-cap names (`توطين التكنولوجيا`, `عبور لاند`, `ابن سينا`) not in the registry → `symbols_covered = 0` on most runs.
- **Fix:** Expand registry to cover EGX-30 + EGX-70 + main-market top-200, with Arabic name aliases. High-leverage win.
- **Owner / status:** open.

### S. Mubasher source in v2 returns 0 — **RESOLVED (PR 10)**
- `scripts/social_pipeline/v2/sources/mubasher_news.py` deleted. Selectors were permanently broken upstream with no maintainer fix planned.
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

### X. `agents/__init__.py` missing `create_hybrid_fundamentals_analyst` export — **CRIT** — RESOLVED (2026-05-09)
- `setup.py:126` called `create_hybrid_fundamentals_analyst()` via `from tradingagents.agents import *` but the function was not exported → `NameError` at graph init when `use_hybrid_fundamental_analyst=True`.
- **Resolved:** Added export to `agents/__init__.py`. All 409 core subsystem tests green.

### Y. `check_short_selling_violation` over-corrected regex — **HIGH** — RESOLVED (2026-05-09)
- `risk_scorer.py` regex fix (PR ~earlier) correctly avoided "short_term" false positives but missed bare `short TICKER.CA` language (e.g. `"short COMI.CA 1000 shares"`).
- **Resolved:** Added `r"\bshort\s+(?:\w+\.ca|\d+)"` pattern to match "short [ticker].ca" or "short [quantity]" constructs on the lowercased plan text. `test_egx_constraints.py` 9/9 green.

### Z. `run_all_risk_checks` missing from codebase — **HIGH** — RESOLVED (2026-05-09)
- Tests and scripts imported `run_all_risk_checks` from `risk_manager.py` but function never existed anywhere.
- `check_short_selling_violation`, `check_leverage_violation` also imported from `risk_manager` after being moved to `risk_scorer.py`.
- **Resolved:** Added `run_all_risk_checks()` convenience function to `risk_scorer.py`. Added backward-compat re-exports block in `risk_manager.py`. All `test_egx_constraints.py` pass.

### Z2. Integration tests require API keys but lacked skip markers — **MED**
- `test_fundamentals_phase2b.py::TestPipelineIntegration::test_stage2_output_schema_valid` — calls real LLM, fails without API key in env. Previously "passing" only because hardcoded key in `default_config.py` was supplying it.
- `test_reasoning_quality.py` — 15 tests error due to missing `--artifacts-dir` CLI flag (unrelated to API keys).
- **Fix:** Mark LLM-calling tests with `@pytest.mark.integration` and add `pyproject.toml` filter.
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

- **§A (partial) + §X + §Y + §Z — Secret exposure, missing export, short-sell regex, missing risk helper (2026-05-09, Batch 1 refactor)** — Removed 4 hardcoded API keys from `default_config.py` (GROQ/OPENAI/EODHD/Google), removed EODHD fallback from `eodhd.py`. Added `EGX_TICKERS` list to `default_config.py` (was missing, used by `taxonomy.py`, caused startup failure). Fixed import order in `main.py`, `run_egx_prediction.py`, `api_server.py` so `load_dotenv()` precedes tradingagents imports. Added `create_hybrid_fundamentals_analyst` export to `agents/__init__.py`. Added `run_all_risk_checks()` to `risk_scorer.py` + backward-compat re-exports in `risk_manager.py`. Fixed short-sell regex to match bare `short TICKER.CA` patterns. Replaced 2 `print()` calls in `risk_scorer.py` with `logger.info()`. Smoke test passes; 1396/1396 non-integration tests pass (was 1391, +5 net).

- **Codebase cleanup — dead code, stale docs, broken ablation harness (2026-05-11)** — Pulled latest from `origin/main` (commit `d6f34e0`). Deleted `dashboard-simple/` (Vite build artifact — not a real dashboard; `dashboard/` with 48 TS files is the sole dashboard). Deleted `scripts/capture_reasoning_artifacts.py` and `scripts/score_reasoning_quality.py` (zero imports, zero references). Deleted `scripts/social_pipeline/models.py` and `scripts/social_pipeline/relevance.py` (v1 pipeline remnants not imported by v2). Deleted `PROJECT_CONTEXT.md` (25 KB stale doc, unreferenced). Fixed `tradingagents/ablation/deterministic_agents.py`: removed 4 dead imports (`assess_financial_health`, `determine_valuation_gap`, `identify_key_risks`, `calculate_data_completeness_score` — never existed in `fundamentals_analyst.py`); replaced `deterministic_fundamentals_analyst` body with delegation to `create_deterministic_fundamentals_analyst()` (the actual Phase 1A/1B pipeline); removed unused `get_egx_fundamentals/income/balance/ratios` tool imports. Ablation harness now imports cleanly (was `ImportError` on every load). Removed 3 unused dependencies from `pyproject.toml`: `praw` (Reddit uses `requests`), `parsel` (0 imports), `setuptools` (0 imports). Cleaned CLAUDE.md: removed reference to deleted `test_fb_sentiment.py` and 3 deleted test-file commands. Replaced `.claude/settings.local.json`: removed 44 stale pytest permission entries for deleted test files, kept 5 valid entries. All smoke tests pass.

- **Runtime stabilization pass — Chroma default, diagnostics, API contract, frontend lint (2026-05-12)** — Implemented the agreed auth-free stabilization slice. `TradingAgentsGraph` now defaults to ChromaDB memory and only imports Postgres/pgvector memory when `memory_backend` explicitly requests it. `/api/health` reports memory/Postgres/Redis diagnostics and degraded reasons. Backtrader report responses are normalized to the same numeric snake_case dashboard contract as LLM reports. Quick-prediction LLM failures return `status="degraded"` and are not persisted as successful HOLD history entries. `main.py` no longer runs analysis at import time. Dashboard React hook lint errors were fixed and prediction/health API types were updated. Added `tests/test_stabilization.py`; `pytest tests/`, backend import smokes, `npm run lint`, `npm run build`, and live uvicorn API smoke pass. Remaining production blockers: auth/CORS/rate-limit, CI/Docker/migrations, complete audit DB wiring.

- **DB & memory infrastructure pass (PRs 1–9, 2026-05-13 → 2026-05-14)** — End-to-end MVP+memory-quality slice of the persistence audit ([agent_docs/db_infrastructure.md](agent_docs/db_infrastructure.md) is the operator reference). All PRs ship together with **128/128 tests green** and zero existing-feature regressions.

  - **PR 1 — Deps + secrets** `[MED]`. Added `diskcache>=5.6.0` (was imported but undeclared — broken on fresh `uv sync`), `psycopg2-binary>=2.9.9`+`pgvector>=0.3.0` as the `[postgres]` extra, `rank-bm25>=0.2.2`. `.env.example` documents `CHROMA_PERSIST_DIR` + secret-rotation note. `.gitignore` adds `chroma_db/`.

  - **PR 2 — DB connection layer** `[MED, ADDED]`. New `tradingagents/db/connection.py` — `ThreadedConnectionPool` singleton with `cursor(register_pgvector, dict_cursor)` context manager (auto-commit / rollback / return-to-pool). `persistent_memory.py` refactored to route through it (was opening one raw `psycopg2.connect()` per memory instance — 5 connections for 5 collections). Sticky `_POOL_FAILED` flag; never raises into caller.

  - **PR 3 — ChromaDB → PersistentClient** `[HIGH §K partial, RESOLVED]`. `FinancialSituationMemory.__init__` uses `chromadb.PersistentClient(path=config["chroma_persist_dir"])` when set; falls back to in-memory `Client(...)` only when path is unset (test paths). Agent memory survives process restarts. `/api/health` exposes `memory.chroma_persist_dir`, `memory.chroma_persistent`, `memory.chroma_collection_counts`, `memory.chroma_total_documents`. New `degraded_reasons` entry `chroma_memory_in_memory_only`.

  - **PR 4 — Memory metadata + similarity threshold** `[HIGH, RESOLVED]`. `add_situations(metadatas, default_metadata)` and `get_memories(where, min_similarity)` extended. Canonical metadata keys: `ticker, trade_date, memory_type, agent_name, outcome, confidence`. Default threshold 0.30 via `MEMORY_MIN_SIMILARITY`. 5 read-side call-sites (bull, bear, trader, research_manager, risk_manager) pass `where={"ticker": ticker}`. `reflection.py` writes `memory_type="reflection"` + ticker + trade_date on every reflection row. Postgres backend mirrors the API; full column support pending future schema bump.

  - **PR 5 — `propagate()` audit write-through** `[CRIT §G, RESOLVED]`. Every `propagate()` mints a UUID `session_id`, writes one `analysis_sessions` row + up to 13 `agent_events` rows (one per non-empty agent output) with `model_fingerprint` (provider, model, temperature, seed). New `tradingagents/db/audit_writer.py` is canonical. `scripts/db/apply_schema_v2.sql` adds `user_id TEXT NULL` + `model_fingerprint JSONB NULL` to both audit tables idempotently. Writers never raise into the graph.

  - **PR 6 — Backtester persistence** `[HIGH, RESOLVED]`. `tradingagents/db/backtest_writer.py` parses string metrics (`"1.72%"`, `"1,017,197.11 EGP"`) into NUMERIC columns and bulk-inserts trades via `execute_values`. `scripts/backtester.py` + `scripts/bt_benchmark.py` now write to `backtest_runs` + `backtest_trades` after the JSON report is saved. `backtest_comparison` view ([db_schema.sql:167](db_schema.sql#L167)) finally has data. `ON CONFLICT (run_id) DO NOTHING` makes retries idempotent. JSON reports remain on-disk source of truth.

  - **PR 7 — Reflection moved out of backtest loop** `[CRIT §C2, RESOLVED]`. In-loop `graph.reflect_and_remember()` call removed from `scripts/backtester.py`. Per-date `(date, state)` captured into `self._reflection_state_queue`. After `_evaluate_trade_outcomes`, `_flush_reflection_with_forward_returns(graph, lag_days=10)` walks the queue, looks up matching trades' realized `forward_return_20d`, swaps `graph.curr_state` to the historical snapshot, and calls `_run_reflections()`. Skips HOLDs + pending forward returns. `Reflector._default_metadata` propagates JSON-encoded `outcome` (verdict + forward_return) into the reflection memory rows. Regression gate: `test_in_loop_reflect_and_remember_call_removed` ensures the bug can't return.

  - **PR 8 — Seed corpus + BM25 fallback** `[HIGH §K, RESOLVED]`. New `tradingagents/agents/utils/seed_memories.py` with 29 hand-curated EGX precedents (5–6 per agent collection × 6 tickers × balanced WIN/LOSS outcomes). `FinancialSituationMemory.__init__` auto-loads seeds into the BM25 corpus always; into Chroma when embeddings enabled + collection empty. New `_bm25_search()` provides keyword retrieval for embedding-disabled configs (DeepSeek/Groq — the default) with where-filter + tanh-normalized similarity. `get_memories()` emits `memory.empty_return` log line on `[]` return. Opt-out via `config["disable_seed_memories"]=True`.

  - **PR 9 — Health endpoint hardening + docs** `[HIGH, ADDED]`. `/api/health` extended with `postgres.audit_write_lag_seconds` (NOW() − MAX(created_at) from `analysis_sessions`), `postgres.backtest_runs_count`, `memory.seeded` (per-collection bool), `memory.min_similarity`. New `degraded_reasons` entry `chroma_collections_unseeded`. New [agent_docs/db_infrastructure.md](agent_docs/db_infrastructure.md) is the operator reference (responsibilities table, setup commands, data-flow diagram, backup/reset, health-endpoint contract). CLAUDE.md config table + env keys updated.

  **Cumulative test growth:** baseline 4 stabilization tests → **128 tests across 10 files** (+124 net, zero existing tests broken). Files touched: `tradingagents/agents/utils/memory.py`, `tradingagents/agents/utils/seed_memories.py` (new), `tradingagents/db/` (new: `__init__.py`, `connection.py`, `audit_writer.py`, `backtest_writer.py`), `tradingagents/graph/{trading_graph.py,reflection.py}`, `tradingagents/default_config.py`, `tradingagents/agents/{researchers,trader,managers}/*.py` (5 call-sites), `scripts/backtester.py`, `scripts/bt_benchmark.py`, `persistent_memory.py`, `server/api_server.py`, `pyproject.toml`, `.gitignore`, `.env.example`, `db_schema.sql` (additive via v2 migration), `scripts/db/apply_schema_v2.sql` (new), CLAUDE.md, [agent_docs/db_infrastructure.md](agent_docs/db_infrastructure.md) (new).

  **Declined:** MongoDB. Postgres JSONB (`confidence_scores`, `full_state`, `metrics`, `structured_output`) covers the dynamic-log case at current scale (~30 tickers, end-of-day cadence). Adding a third datastore would triple the ops surface for no measurable benefit.

  **Deferred to MEMORY.md §F (Week-3/4 ship):** Alembic migrations (this pass uses a single additive `apply_schema_v2.sql` script), Dockerfile/docker-compose, GitHub Actions CI, JWT auth (schema accepts NULL `user_id` so audit rows still write without auth).

---

## 4a. Sentiment redesign progress (Phase 3)

> Tracks the multi-PR sentiment subsystem rebuild approved 2026-05-01. Each PR is foundation-only until wired in. See Phase 1 audit + Phase 2 redesign in session transcript.

- **PR 1 (2026-05-01) — Foundation: typed contracts + NO_SIGNAL sentinel + central taxonomy + liquidity tiers + config.** Adds `tradingagents/sentiment/{contracts,taxonomy,liquidity_tiers,config}.py` with no behavioral wiring. Introduces `NoSignalReason` carrying `gate_failed` + `human_readable` + `metrics`, rendered to canonical audit string `NO_SIGNAL: <reason> (gate=..., k=v, ...)` for propagation into logs/agent context/reports/audit. Centralizes the 6-sector EGX taxonomy (banks/real_estate/industry/telecom_tech/financial_services/food_bev) with Arabic aliases per sector; legacy 4-sector fundamentals shim via `to_fundamentals_sector()` keeps `tests/test_fundamentals_phase1a.py` (79 tests) green. Liquidity tiers MEGA(8)/MID(5)/SMALL(3) seeded as PROVISIONAL; calibration script deferred to PR 10. Tests: `tests/test_sentiment_{contracts,taxonomy,liquidity_tiers}.py` — 37 passing.
- **PR 2 (2026-05-01) — Entity-layer fix: phrase-boundary regex, EGX_* key rename, SCTS alias cleanup.** Four targeted changes to `scripts/social_pipeline/v2/entities.py` and `aggregator.py`: (1) replaced `_alias_in_text` substring match for multi-word Arabic aliases with pre-compiled phrase-boundary regex (`_compile_phrase_pattern`) — eliminates Arabic morphological suffix-extension false positives (e.g. "التجاري الدولية" no longer matches COMI alias "التجاري الدولي"); (2) renamed `MARKET_INDEX_TERMS` keys EGX30/70/100 → EGX_30/70/100 so aggregator's `startswith("EGX_")` correctly routes all index mentions to `EGX_MARKET` rather than per-stock pools; (3) removed ambiguous standalone alias "توطين التكنولوجيا" from SCTS (kept full company-name aliases); (4) documented the intentional secondary market contribution in `aggregator.py:122-126`. Resolves MEMORY §R (partial — phrase-boundary fix) and the EGX_MARKET routing bug identified in Phase 1 Sub-agent 2 audit. Tests: `tests/test_sentiment_entity_extraction.py` — 36 passing (including 5 adversarial market→stock contamination tests). All prior suites green (fundamentals 79/79, pipeline 8/8, PR 1 37/37).

- **PR 3 (2026-05-01) — Layer A: MarketSentiment aggregator + dead source cleanup.** Adds `tradingagents/sentiment/market.py` implementing `compute_market_sentiment(posts, *, reference_time)` with three binary hard gates: (1) `market.n_total_posts` ≥ 50, (2) `market.n_distinct_sources` ≥ 2, (3) `market.recent_24h_share` ≥ 0.30. First failing gate emits `MarketSentiment(status=NO_SIGNAL, reason=NoSignalReason(...))` with the canonical audit string propagated to logs. On success, computes weighted-mean score → `MarketRegime` (EUPHORIA/GREED/NEUTRAL/FEAR/PANIC via ±0.15/±0.35 thresholds), per-post score std → `VolatilityMood` (CALM/ELEVATED/STRESSED via 0.35/0.55 thresholds), and `confidence` (40% size + 35% recency + 25% clarity). Removes `facebook_groups`, `twitter_authed`, and `mubasher_news` from `pipeline.py` source loop (dead sources, always 0 posts; files retained until PR 10 cleanup). Exports `MarketDataPoint` and `compute_market_sentiment` from `tradingagents/sentiment/__init__.py`. Tests: `tests/test_sentiment_market.py` — 86 passing across 10 test classes (gate paths, regime/volatility bands, confidence properties, weighted stats, timestamp parsing, log-format invariants). All prior regression gates green: fundamentals 79/79, pipeline 8/8, PR 1 37/37, PR 2 36/36.

- **PR 4 (2026-05-02) — Layer B: SectorSentiment aggregator + bilingual sector classifier.** Adds `tradingagents/sentiment/sector.py` implementing `compute_sector_sentiment(sector, posts)` with three binary hard gates: (1) `sector.n_sector_posts` ≥ 10, (2) `sector.n_distinct_days` ≥ 3, (3) `mean_entity_conf` ≥ 0.70. First failing gate emits `SectorSentiment(status=NO_SIGNAL, reason=NoSignalReason(...))` with canonical audit string. On SIGNAL: computes weighted-mean score (weight-normalised; falls back to simple mean on zero weights), confidence (40% size + 35% spread/days + 25% clarity). Adds `classify_post_to_sector(text) → SectorEnum | None` for bilingual (Arabic phrase + English word-boundary) sector keyword routing using taxonomy aliases. IEEE-754 fix: `_mean_entity_conf` rounds to 6 d.p. to avoid accumulation artifacts at the 0.70 boundary. Exports `SectorDataPoint`, `compute_sector_sentiment`, `classify_post_to_sector` from `tradingagents/sentiment/__init__.py`. Tests: `tests/test_sentiment_sector.py` — 75 passing across 11 test classes (all gate paths, gate ordering, all 6 sectors, bilingual matching, score/confidence computation, boundary values, contract invariants, internal helpers). Full regression: 320/320 passing (fundamentals 79, pipeline 8, PR 1 37, PR 2 36, PR 3 86, PR 4 75).

- **PR 5 (2026-05-02) — Layer C: StockSentiment aggregator + pre-LLM NO_SIGNAL gate.** Adds `tradingagents/sentiment/stock.py` implementing `compute_stock_sentiment(ticker, posts, reference_time, market_score=None)` with four binary hard gates applied to non-spam posts only: (1) `n_strong_mentions` ≥ tier threshold (entity_conf ≥ 0.85), (2) `n_distinct_authors` ≥ tier threshold (empty-author posts collapsed to single `"_anonymous"` bucket), (3) `n_distinct_sources` ≥ tier threshold, (4) `recent_72h_share` ≥ 0.60. Tier thresholds: MEGA(8/5/3), MID(5/3/2), SMALL(3/2/2). On SIGNAL: weighted-mean score, confidence (40% size + 35% diversity/authors + 25% clarity), and `contradicts_market` flag (True when market_score and stock score are both ≥ 0.15 abs and opposite sign). Adds `prefetched_stock_datapoints: Optional[List[Dict]]` field to `AgentState`. Adds `_try_layer_c_gate` helper and pre-LLM gate to `social_media_analyst.py`: if `prefetched_stock_datapoints` is non-empty and Layer C returns NO_SIGNAL → LLM skipped, `sentiment_report = "Social sentiment: insufficient data — excluded."`. Exports `StockDataPoint`, `compute_stock_sentiment` from `tradingagents/sentiment/__init__.py`. Tests: `tests/test_sentiment_stock.py` — 89 passing across 15 test classes (all tier/gate paths, gate ordering, spam exclusion, recency edge cases, contradicts_market, all 5 MEGA tickers, boundary/entity-conf threshold, score/confidence computation, anonymous-author bucketing, contract invariants, pre-LLM gate wiring). Full regression: 409/409 passing (fundamentals 79, pipeline 8, PR 1 37, PR 2 36, PR 3 86, PR 4 75, PR 5 89).

- **PR 8 (2026-05-02) — LLM-as-explainer demotion + bull/bear NO_SIGNAL guard.** Rewrites `social_media_analyst.py`: LLM prompt schema changed to `narrative` + `cited_post_ids` only (no `sentiment_score`, `direction`, `confidence`). `_LLM_ROLE_INSTRUCTION` constant injected into both prefetch-mode and tool-mode prompts. Adds `_compute_blend_result()` which tries to parse `prefetched_social_sentiment` as JSON (v2 pipeline format) to reconstruct MarketSentiment/MacroSentiment/SectorSentiment and call `blend_sentiment()`, falling back to pass-through when no structured data. `sentiment_blend_result` dict written to state in all code paths (SIGNAL, NO_SIGNAL, explainer). `_build_sentiment_report()` produces a researcher-safe string surfacing the LLM narrative + blend modifiers with an explicit "execution only — not directional" note. `bull_researcher.py` and `bear_researcher.py` each gain `_format_sentiment_section()` helper + `sentiment_section` variable injected into their prompts: detects `_NO_SIGNAL_TEMPLATE` / "insufficient data" phrases and substitutes a clear EXCLUDED instruction; otherwise surfaces blend multipliers as non-directional execution context. Tests: `tests/test_sentiment_workflow.py` — 47 passing. Full regression: 645/645.

- **PR 7 (2026-05-02) — Layer E: Blender/propagation rewrite.** Replaces naive linear blend in `scoring.py` with confidence-weighted mean (weight = structural analyst weight × per-analyst confidence) and quorum rule (≥2 directional analysts required → else `INSUFFICIENT_DATA`). Adds `SentimentBlend` NamedTuple + `blend_sentiment(macro, market, sector)` function applying market-regime, macro-direction, and sector-tilt multipliers (sentiment never flips direction, only modulates confidence × and position size ×). Adds `blend_from_dict()` for AgentState round-trip. `propagate_confidence()` rewritten to enforce quorum, read `sentiment_blend_result` from state, return `overall_status` ("OK" | "INSUFFICIENT_DATA") and `position_size_multiplier`. `AgentState` gains `sentiment_blend_result: Optional[Dict]` field. `calculate_unified_score()` now returns 5-tuple adding `overall_status`; 3 callers in `system_validation.py` updated. Resolves MEMORY §J (misleading scoring aggregation). Tests: `tests/test_sentiment_blender.py` — 74 passing. Full regression: 598/598 passing.

- **PR 6 (2026-05-02) — Layer A0: MacroSentiment aggregator with source-credibility and corroboration gates.** Adds `tradingagents/sentiment/macro.py` implementing `compute_macro_sentiment(posts, reference_time)` with three sequential binary hard gates: (1) `macro.source_credibility` — at least one post from OFFICIAL/TIER1/TIER2 source (RUMOR excluded); (2) `macro.corroborating_sources` — at least one `(category, direction)` group has ≥2 distinct source_domains within 48 h; (3) `macro.half_life_expired` — at least one corroborated event is within its category half-life window. Source credibility ladder: OFFICIAL (cbe.org.eg, mof.gov.eg, fra.gov.eg, egx.com.eg), TIER1_NEWS (reuters.com, bloomberg.com, mubasher.info), TIER2_NEWS (enterprise.press, almalnews.com, dailynewsegypt.com, alborsaanews.com). Half-lives by category: RATE_DECISION=120 h, EGP_DEVALUATION=240 h, IMF_PROGRAM=168 h, INFLATION_PRINT=72 h, TAX_REGULATION=168 h, GEOPOLITICAL=48 h, COMMODITY_SHOCK=72 h. Composite regime: RISK_OFF dominates RISK_ON dominates NEUTRAL. Event confidence = 0.60×credibility_weight + 0.40×corroboration_ratio. Adds `MacroDataPoint` NamedTuple (timestamp, source_domain, headline, category, direction, sentiment_score, weight, post_id, url). Updates `tradingagents/sentiment/__init__.py` to export `MacroDataPoint`, `compute_macro_sentiment`. Tests: `tests/test_sentiment_macro.py` — 115 passing across 14 test classes (all 3 gate paths, composite regime rules, source credibility ladder, domain normalisation, half-life by category, corroboration window boundary, MacroEvent field correctness, magnitude bands, confidence formula, contract invariants, multiple concurrent events, audit string format). Full regression: 524/524 passing (fundamentals 79, pipeline 8, PR 1 37, PR 2 36, PR 3 86, PR 4 75, PR 5 89, PR 6 115).

- **PR 9 (2026-05-02) — Surfacing + audit trail.** Adds `tradingagents/sentiment/surfacing.py` with four pure functions: `extract_sentiment_audit_record(final_state)` — flat dict with all sentiment audit fields; `format_sentiment_for_api(final_state)` — frontend-friendly dict; `format_sentiment_for_cli(final_state)` — multi-line Rich TUI string; `build_sentiment_context_event(session_id, ticker, trade_date, final_state, logged_at)` — JSONL event dict. `trading_graph._log_state()` now always appends `"sentiment_audit"` block to the eval-results JSON. `server/api_server.py` appends a `SENTIMENT_CONTEXT` JSONL event alongside SESSION_START/QUICK_ANALYSIS_RESULT when `_final_state` is present. `cli/main.py` renders a "VI. Sentiment Context (Phase 3)" Rich Panel after display_complete_report(), colour-coded cyan (signal) / yellow (no-signal), with blend multipliers and narrative. All functions have try/except guards — never crash the audit write or the CLI display. Exports added to `tradingagents/sentiment/__init__.py`. Tests: `tests/test_sentiment_surfacing.py` — 50 passing across 9 test classes. Full regression: 633/633 passing.

- **PR 10 (2026-05-02) — Cleanup + calibration script + final.** Deleted four zombie source files: `scripts/social_pipeline/v2/sources/twitter_authed.py`, `facebook_groups.py`, `mubasher_news.py`, and `tradingagents/dataflows/social_media_sources/twitter_source.py` (all returned 0 posts in production). Removed `"twitter"` from `PLATFORM_SOURCES` in `dataflows/social_media_sources/aggregator.py` and the dead `from .twitter_source import fetch_twitter_data` import. Updated `pipeline.py` docstring and inline comments to reflect removal. Added `scripts/calibrate_tier_thresholds.py`: offline-safe script that reads `results_*.json` files from `scripts/social_pipeline/v2/logs/`, computes per-tier p5/p25/p50/p75/p95 statistics for `n_strong_mentions`/`n_distinct_authors`/`n_distinct_sources`, and recommends updated MEGA/MID/SMALL thresholds at the 25th-percentile level (≈75% of trading days would pass). Keeps current thresholds when <10 observations. Supports `--format json` for CI patch output. Tests: `tests/test_pr10_cleanup.py` — 47 passing across 7 test classes. Full regression: 680/680 passing.

- **Phase 3 addendum (2026-05-04) — Manual test harness + deep documentation.** Added two deliverables completing the Phase 3 documentation layer:
  - `scripts/test_sentiment_pipeline.py` — standalone CLI test harness (no LLM, no full graph) exercising Layers A0/A/B/C/E with 5 built-in scenarios (positive, no_signal, conflict, low_liquidity, multilingual). Fixed scenario data builders to correctly provide 3 distinct sources for MEGA-tier tickers (COMI/TMGH/ETEL); updated low_liquidity scenario to MID-tier thresholds (DOMT is MID, not SMALL). CLI flags: `--ticker`, `--date`, `--scenario`, `--raw-posts-file`, `--verbose`, `--save-report`, `--show-intermediate-json`, `--list-scenarios`.
  - `agent_docs/sentiment_architecture.md` — comprehensive Mermaid-diagram-based technical and trading documentation (14 sections, ~600 lines): architecture overview, layer-by-layer specs, sequence diagram, model interaction map (CAMeLBERT-DA/FinBERT/XLM-R/VADER), signal propagation flowchart, confidence formulas with worked COMI.CA example, NO_SIGNAL guide, gate specifications, quorum rule, end-to-end worked example, Arabic processing details, known limitations table, files quick reference.
  - `tests/test_sentiment_harness.py` — 106 regression tests for the harness covering all 5 data builders, all 5 layer integrations per scenario, `build_report()` JSON structure, `load_from_pipeline_file()` robustness, `_parse_ts()`, liquidity tier assignments. All 106 passing.
  - **Total regression: 786/786 passing** (680 PR 1-10 + 106 new harness + unchanged fundamentals 79/79 + pipeline 8/8).

### Open follow-ups (will be opened formally as issues when their PR lands)
- Fundamentals migration to consume `tradingagents.sentiment.taxonomy.SectorEnum` directly instead of via shim (separate PR; legacy 4-sector module stays canonical for fundamentals until then).
- Run `calibrate_tier_thresholds.py` after accumulating ≥30 days of real pipeline_v2 results and apply the recommended threshold update to `liquidity_tiers.py`.

---

## 4b. RL meta-policy progress (PRs A-D)

> Tracks the offline-RL meta-policy work approved 2026-05-14 (plan: `C:\Users\ahmed\.claude\plans\1-role-you-crispy-mist.md`; operator guide: [agent_docs/rl_meta_policy.md](agent_docs/rl_meta_policy.md)). The policy is a single-step CQL Q-network sitting **above** the LLM graph; it modulates position size in ``[0, 1]`` after the directional decision and deterministic risk veto. **Flag is default OFF** until a walk-forward evaluation with adequate sample size clears the pre-registered PASS criterion. All PRs ship together with **241/241 tests green** and zero existing-feature regressions.

- **PR A (2026-05-14) — Data infrastructure.** Added `tradingagents/rl/{feature_extractor,dataset}.py` + `scripts/generate_rl_training_data.py`. Feature extractor (`FEATURE_VERSION="rl_state_v1"`, 49 columns) is the single canonical state→vector mapping shared by training and inference. Dataset builder consumes `analysis_sessions.full_state` (Postgres preferred) or `backtest_results/report_*.json` (fallback) and joins to forward returns recomputed from raw OHLCV (avoids MEMORY §C1 inheritance). Post-hoc rewards: `r = log(1 + size·sign(action)·forward_return_20d) − λ_dd·max(0, dd-0.05) − tx_cost`, HOLD=0, clipped to ±0.5. Look-ahead guard `_no_lookahead_window` marks horizons not yet elapsed as PENDING. Tests: `tests/test_rl_{feature_extractor,dataset_integrity}.py` — **48 passing**. Smoke parquet at `data/rl/training_v1.parquet` (8 samples from 2 existing reports, 6 usable, 2 correctly PENDING).

- **PR B (2026-05-14) — Training + offline eval.** Added `tradingagents/rl/{config,policy,train,eval}.py` + `scripts/train_rl_policy.py`. d3rlpy not installable on Python 3.13 / Windows / uv — used the plan's documented fallback: ~200-LoC hand-written single-step CQL (γ=0). `QNetwork = MLP(49→64→64→5)` with ReLU + dropout 0.1, ~5k params. CQL loss = MSE(Q(s,a_b), r) + α·(logsumexp(Q/τ) − Q(s,a_b)); α=1.0 default. Per-ticker chronological train/val split, seed-pinned (numpy+torch+cudnn). `RLSizingPolicy` wraps the network with identity fallback, weight-hash fingerprint (`weights_sha256_16`), and a `feature_version` mismatch guard on load. Three OPE estimators: FQE direct method, SNIPS with ε-greedy π̃ and empirical β̂, behavior-policy mean reward baseline. Tests: `tests/test_rl_{policy,train,eval}.py` — **37 passing**. Smoke checkpoint at `models/rl_meta_v1.pt`; training on the 6-sample parquet converged TD loss 0.0065 → 6×10⁻⁶ in 37 epochs (0.2s wall), with honestly-FAIL OPE summary (`preliminary_ok: false`, `snips_ess: 2.0`) — sample size far below the threshold for a useful model, as expected at this stage.

- **PR C (2026-05-14) — Backtester integration (additive, default-off).** Three surgical edits to existing files, 268 net insertions, zero deletions:
  - `tradingagents/default_config.py` — added `rl_meta_policy_enabled` (env `RL_META_POLICY_ENABLED`, default False) and `rl_model_path` (env `RL_MODEL_PATH`, default empty).
  - `tradingagents/db/audit_writer.py` — added `write_rl_meta_event(session_id, prediction, ticker, trade_date)` that appends one `agent_events` row per decision with `event_type='rl_meta_size_adjustment'`. No schema change.
  - `scripts/backtester.py` — `BacktestingEngine.__init__` loads the policy with try/except fail-closed fallback to `identity_policy()`; `execute_trade(..., final_state)` calls `RLSizingPolicy.predict` once (any error → fallback to 1.0), clamps `size_mult ∈ [0, 1]` defense-in-depth, multiplies `target_shares` AFTER the existing confidence-scaling line 444-452, records `rl_size_multiplier` + `rl_action_index` + `rl_model_fingerprint` + `rl_feature_version` on the trade record. Outer loop pipes `(graph.session_id, prediction)` to `write_rl_meta_event` and stamps the same fields onto `audit_log[i]`. Tests: `tests/test_rl_policy_safety.py` — **16 passing** covering RL-never-amplifies, risk-veto-wins, SELL-unaffected, fail-closed-on-load-failure, fail-closed-on-predict-error, default-off-yields-identity, audit-fields-present.

- **PR D (2026-05-14) — Walk-forward comparison + Phase-4 docs.** Added `tradingagents/rl/walkforward.py` + `scripts/run_rl_evaluation.py` + `agent_docs/rl_meta_policy.md`. Comparison module recomputes Sharpe / Calmar from raw `daily_portfolio` series using `default_risk_free_rate() = 0.24` (CBE policy rate; closes MEMORY §C3 on the RL eval path even though `scripts/backtester.py` still uses 0.05). Closed-trade win rate uses realized PnL only — the legacy `"Hit Rate (fwd)"` field is intentionally NOT consumed (MEMORY §C1 still open). Wilson 95% CI on win rate. Bootstrap 95% CI on per-day mean-return difference (intersection of dates, seed-pinned). **Pre-registered PASS criterion:** Sharpe(rl_meta) − Sharpe(baseline) ≥ +0.20 AND bootstrap CI excludes zero; FAIL → flag stays default OFF. Tests: `tests/test_rl_walkforward.py` — **22 passing** including PASS/FAIL/CI-crosses-zero/no-overlapping-dates branches. Smoke at `eval_results/rl_vs_baseline_walkforward_smoke.json` correctly reports FAIL (n_paired_days=0; existing reports cover different date ranges).

  **Cumulative test count for the RL meta-policy stream: 123 tests across 7 files, all passing. Full repo regression suite: 241/241 passing (excluding pre-existing `test_bm25_fallback.py` env failures unrelated to RL).**

  **Files modified (existing, all additive):** `tradingagents/default_config.py`, `tradingagents/db/audit_writer.py`, `scripts/backtester.py`, `MEMORY.md` (this entry).
  **Files added:** `tradingagents/rl/__init__.py` + 7 modules; `scripts/{generate_rl_training_data,train_rl_policy,run_rl_evaluation}.py`; 7 test files; `agent_docs/rl_meta_policy.md`; smoke artifacts under `data/rl/`, `models/`, `eval_results/`.

  **Deferred to a follow-up PR (out of scope for this stream):** EGX-30 × walk-forward windows full evaluation run (requires multi-hundred-dollar LLM spend that's not feasible on a developer machine); the pipeline is in place, the verdict will be applied honestly when run.

### Open follow-ups for the RL stream
- Run `scripts/generate_rl_training_data.py` against an EGX-30 walk-forward Postgres history (≥30 tickers × ≥3 windows × ~50 dates) once such backtests have been generated. Retrain. Re-evaluate. Apply the pre-registered PASS/FAIL.
- If PASS: enable `rl_meta_policy_enabled` by default in a separate PR with a "gradual rollout" plan (one ticker family at a time, model card published).
- If FAIL: keep the infrastructure, document the negative result in `PROOF_OF_WORK.md`, do not flip the flag.
- Once `analysis_sessions.full_state` is populated by live `propagate()` runs (PR 5 of the DB-infra pass), the dataset builder's Postgres path will produce dense feature vectors automatically; no code change needed.

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
