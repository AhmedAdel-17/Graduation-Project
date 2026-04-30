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

### A. Hardcoded EODHD API key in source — **CRIT** (security + cost exposure)
- **Where:** `tradingagents/dataflows/eodhd.py:17`, `tradingagents/dataflows/gateway.py:17` — literal `EODHD_API_KEY = "696cff318de733.38444726"`.
- **Risk:** Live key, committed to git history, drains paid quota, leakage on public push.
- **Fix:** Rotate the key with EODHD. Remove the literal. Load from `.env` only. Scrub git history with `git filter-repo --replace-text`. Add `gitleaks` or `detect-secrets` to a pre-commit hook.
- **Owner / status:** open.

### B. LLM non-determinism — **CRIT** (audit, reproducibility, backtest validity)
- **Where:** `tradingagents/graph/trading_graph.py:104-105` pins `temperature=0` only on the deep/quick LLM constructors. Per-agent `.invoke()` calls don't re-pin and never set `seed`. Examples: `bull_researcher.py:155`, `bear_researcher.py:172`, `trader.py:286`, `risk_manager.py:647`.
- **Risk:** Same `(ticker, date)` produces different theses per run. Backtest results unrepeatable. Audit trail can't be reconstructed.
- **Fix:** Define a single `LLM_INVOKE_KWARGS = {"temperature": 0, "seed": 42}` constant. Route every `.invoke(...)` through it. Persist model name + provider + fingerprint into `agent_events.structured_output` per call.
- **Owner / status:** open.

### C. Look-ahead bias in `scripts/backtester.py` — **CRIT** (overstates returns; due-diligence killer)
- **C1.** `_evaluate_trade_outcomes()` at `scripts/backtester.py:942-1050` — labels trades WIN/LOSS using forward prices fetched after the backtest, then surfaces this as "Hit Rate (fwd)" in reports. Pure look-ahead.
- **C2.** Reflection inside the loop — `scripts/backtester.py:855-881` calls `graph.reflect_and_remember(returns_losses)` per date with realized PnL, updating agent memory before the next decision date. Causal leakage even if no labels are reported.
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

### J. Scoring aggregation math is misleading — **HIGH**
- **Where:** `tradingagents/agents/utils/scoring.py:165-171`.
- **Issues:** confidence scales are inconsistent (line 161 has a `>1.0` heuristic divisor that fails silently if `confidence_score == 0`); naive linear combination treats four agents as independent when on EGX they are strongly correlated; `overall_confidence` is reported as if calibrated when it isn't.
- **Fix:** Replace with confidence-weighted median-of-means. Either normalize all confidences to `[0,1]` deterministically at the source (each analyst), or refuse to aggregate. Stop emitting a single `overall_confidence` until calibrated.
- **Owner / status:** open.

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

### Q. Twitter/X anonymous scraping is dead — **MED** (already documented)
- **Where:** `scripts/twitter_pipeline/scraper.py` docstring; `dataflows/social_media_sources/twitter_source.py` (uses Google News as a proxy, returns 0 hits).
- **Status:** Documented in CLAUDE.md and PROJECT_CONTEXT.md. Apify Facebook is the working substitute. Twitter strategies kept in the ladder for opportunistic recovery.
- **Action:** none required short-term; revisit if Apify Twitter/X actor becomes available.

### R. v2 entity registry too narrow — **MED**
- **Where:** `scripts/twitter_pipeline/v2/entities.py::SYMBOL_REGISTRY`. Live FB groups regularly mention small-cap names (`توطين التكنولوجيا`, `عبور لاند`, `ابن سينا`) not in the registry → `symbols_covered = 0` on most runs.
- **Fix:** Expand registry to cover EGX-30 + EGX-70 + main-market top-200, with Arabic name aliases. High-leverage win.
- **Owner / status:** open.

### S. Mubasher source in v2 returns 0 — **MED**
- **Where:** `scripts/twitter_pipeline/v2/sources/mubasher_news.py`. Public listing HTML doesn't match the candidate selectors.
- **Fix:** Re-map selectors from a saved sample, or replace with EGX direct news API.
- **Owner / status:** open.

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

*(none yet — start populating as fixes land)*

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
