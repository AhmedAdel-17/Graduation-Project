# SKILLS.md — Competency Map for the EGX Multi-Agent System

> **Purpose.** A skills/knowledge reference for everyone — and every Claude Code session — working on this project. Organized by subsystem so you can map a task ("fix the backtester") to the exact competencies needed and find learning resources fast.
>
> **Companion files:**
> - `CLAUDE.md` — operational reference (architecture, conventions, commands)
> - `MEMORY.md` — audit findings, open issues, ship plan
> - `SKILLS.md` (this file) — what you need to know to do the work
>
> **How to read it.** Each skill block follows the same shape:
> - **What it is** (1 line)
> - **Why it matters here** (tied to a real subsystem in this repo)
> - **Where it shows up** (file paths)
> - **Level needed** (Awareness / Working / Expert)
> - **Learn / sharpen** (concrete resources)

Levels:
- **Awareness** — read about it, recognize it in code review
- **Working** — can implement under supervision, can debug
- **Expert** — can design from scratch, can teach, can defend in front of a senior quant or compliance officer

---

## Section A. Technical engineering skills

### A.1 Python 3.13 (advanced)
- **What:** Modern Python — type hints, dataclasses, `typing.TypedDict`, async/await, context managers, decorators.
- **Why here:** The entire backend. `AgentState` is a TypedDict. Async is the FastAPI + LangGraph spine.
- **Where:** Whole repo. Especially `tradingagents/agents/utils/agent_states.py`, `server/api_server.py`, `tradingagents/dataflows/gateway.py`.
- **Level:** Working → Expert.
- **Learn:** *Fluent Python* (Ramalho, 2nd ed) chapters 5–8, 17–21. PEP 484/585/604 type-hint specs.

### A.2 LangGraph + LangChain
- **What:** Stateful, multi-actor LLM orchestration as a directed graph.
- **Why here:** This IS the project skeleton. `TradingAgentsGraph` is a `StateGraph` with parallel analyst fan-out, conditional debate routing, and tool nodes.
- **Where:** `tradingagents/graph/trading_graph.py`, `setup.py`, `conditional_logic.py`, `propagation.py`. ToolNode wiring in `_create_tool_nodes()`.
- **Level:** Expert.
- **Learn:** LangGraph official docs → "Multi-Agent" + "Tool calling" guides. Read the source of `langgraph.prebuilt.ToolNode`. Practice: rewrite a small bull/bear debate from scratch, then compare to ours.

### A.3 LLM tool-use & function calling
- **What:** Letting an LLM call typed Python functions to fetch data or take actions, with structured arguments validated against a schema.
- **Why here:** Every analyst calls tools (`get_stock_data`, `get_egx_fundamentals`, etc.) before reasoning. Bad tool-call design = silent agent failure.
- **Where:** `tradingagents/agents/utils/*_tools.py`. Bound to LLMs via `llm.bind_tools(...)` inside each analyst.
- **Level:** Working → Expert.
- **Learn:** Anthropic + OpenAI function-calling docs. Pydantic v2 model + JSON-schema generation. Read `langchain_core.tools.tool` decorator implementation.

### A.4 Prompt engineering for structured output
- **What:** Designing prompts that reliably emit parseable JSON, respect constraints, and degrade gracefully on failure.
- **Why here:** Every analyst output is a JSON block extracted via regex. Malformed → fallback to neutral, low-confidence (currently leniently accepted, see `MEMORY.md` issue J).
- **Where:** All `agents/analysts/*.py` system prompts; `agents/researchers/*.py`; `agents/managers/risk_manager.py`.
- **Level:** Expert.
- **Learn:** Anthropic prompting guide (chain-of-thought, JSON mode, role separation, few-shot constraints). Practice: write the same prompt three ways and benchmark JSON-validity rate on 50 samples.

### A.5 Pydantic v2
- **What:** Runtime data validation with type-driven schemas; the de-facto standard for I/O contracts.
- **Why here:** Provider responses validated via `dataflows/schemas.py`. Fundamentals report at `agents/analysts/fundamentals/schemas.py`. FastAPI request bodies.
- **Where:** Both schemas files plus `server/api_server.py` request models.
- **Level:** Working.
- **Learn:** Pydantic v2 migration guide. `model_validator`, `field_validator`, `computed_field`. Pydantic Settings for env-var typing.

### A.6 FastAPI + WebSockets
- **What:** Async HTTP + bidirectional sockets for streaming agent progress.
- **Why here:** `server/api_server.py` exposes REST + a WebSocket that streams graph chunks. Currently has no auth/heartbeat — see `MEMORY.md` issue E.
- **Where:** `server/api_server.py:796-1095`.
- **Level:** Working → Expert (you'll be hardening it).
- **Learn:** Sebastián Ramírez's FastAPI docs (full read). RFC 6455 (WebSocket protocol). `slowapi` for rate-limiting. JWT/OIDC integration via `fastapi-users` or `authlib`.

### A.7 React 19 + TypeScript + Vite
- **What:** Modern SPA stack.
- **Why here:** `dashboard/` runs on this. `lightweight-charts` for equity curves. TanStack Query for data fetching. Zustand for state.
- **Where:** `dashboard/src/`.
- **Level:** Working.
- **Learn:** React 19 release notes (concurrent rendering, `use()` hook). TanStack Query docs. TradingView lightweight-charts API.

### A.8 Pandas + NumPy + financial time-series handling
- **What:** Vectorized data manipulation on OHLCV bars, ratios, returns, rolling stats.
- **Why here:** Backtester P&L, technical indicators, fundamental ratios.
- **Where:** `scripts/backtester.py`, `dataflows/y_finance.py`, `agents/analysts/fundamentals/financial_calculator.py`.
- **Level:** Expert.
- **Learn:** *Python for Data Analysis* (McKinney). Pandas `groupby`, `resample`, `rolling`. Beware floating-point in monetary math — use `Decimal` or integers-of-cents for any P&L work.

### A.9 PostgreSQL + pgvector
- **What:** Relational DB with vector-similarity extension for embedding-based memory.
- **Why here:** `db_schema.sql` + `persistent_memory.py` provide audit, agent memory, backtest history.
- **Where:** `db_schema.sql`, `persistent_memory.py`.
- **Level:** Working.
- **Learn:** PostgreSQL official docs (indexes, transactions, JSONB). pgvector README. Alembic for migrations (currently missing, see `MEMORY.md` issue F).

### A.10 Redis pub/sub
- **What:** In-memory message broker for fan-out streaming.
- **Why here:** WebSocket clients subscribe to `AgentEventPublisher` events keyed by ticker.
- **Where:** `redis_pubsub.py`, `server/api_server.py` subscriber loop.
- **Level:** Working.
- **Learn:** Redis pub/sub vs Streams (you may want Streams for durable replay). `redis-py` async API.

### A.11 Docker + docker-compose
- **What:** Reproducible deployment of the app + Postgres + Redis as a unit.
- **Why here:** **Currently missing.** `MEMORY.md` issue F.
- **Where:** N/A — you'll be writing it.
- **Level:** Working.
- **Learn:** Docker official tutorial. Multi-stage builds. Python slim images. `uv pip install --system` inside container.

### A.12 CI/CD (GitHub Actions)
- **What:** Automated test + lint + build on every push.
- **Why here:** **Currently missing.** A real fintech project cannot ship without it.
- **Where:** N/A — you'll be writing `.github/workflows/`.
- **Level:** Working.
- **Learn:** GitHub Actions syntax + matrix builds. Secrets management via repo settings.

### A.13 Observability (logging, metrics, tracing)
- **What:** Production-grade visibility into running systems.
- **Why here:** Currently `print()` calls in some modules; no Prometheus, no OpenTelemetry, no Sentry. `MEMORY.md` issues O + Week-4 plan.
- **Where:** Touch every long-lived module.
- **Level:** Working.
- **Learn:** Python `logging` cookbook (rotation, structured JSON, request-id propagation). Prometheus `prometheus_client`. OpenTelemetry Python SDK.

### A.14 Testing (pytest, mocking, regression)
- **What:** Unit + integration + property-based testing.
- **Why here:** Fundamentals subsystem has 79 unit tests as the gold standard. Server + dashboard largely untested.
- **Where:** `tests/`. Especially `test_fundamentals_phase1a.py`, `phase1b_audit.py`, `phase2b_audit.py`.
- **Level:** Working → Expert.
- **Learn:** pytest fixtures, parametrize, marks. `pytest-mock`, `httpx` for API testing. Hypothesis for property-based tests on financial calcs.

### A.15 Security & secret hygiene
- **What:** Never committing keys; rotating + scrubbing on accident; secret scanning in CI.
- **Why here:** Live EODHD key currently committed — `MEMORY.md` issue A.
- **Where:** `dataflows/eodhd.py:17`, `dataflows/gateway.py:17`.
- **Level:** Working.
- **Learn:** `gitleaks` / `detect-secrets` / `trufflehog`. `git filter-repo` for history scrubbing. OWASP cheat sheet on secret management.

### A.16 Async concurrency & rate-limit handling
- **What:** asyncio, aiohttp, semaphores, exponential backoff with jitter, retry budgets.
- **Why here:** Multiple LLM providers, scraping with rate limits, parallel pre-fetcher.
- **Where:** `tradingagents/agents/utils/llm_failover.py`, `graph/prefetch.py`, `dataflows/retry_engine.py`.
- **Level:** Working.
- **Learn:** *Python Concurrency with asyncio* (Fowler). `tenacity` library. Token-bucket vs leaky-bucket rate-limit theory.

---

## Section B. Fintech / quantitative skills

### B.1 EGX market microstructure
- **What:** How the Egyptian Exchange actually works — order types, sessions, settlement, limits, halts, lot sizes, fees.
- **Why here:** Hard-coded across the codebase. Get any detail wrong and the system suggests illegal trades.
- **Where:** `default_config.py` (price limits, hours, currency). `risk_manager.EGX_RISK_LIMITS`. `scripts/backtester.py` cost stack.
- **Level:** Expert (or hire one — non-negotiable).
- **Learn:** EGX rulebook (egx.com.eg). FRA decrees (Decree 11/2014 on advice; Law 95/1992). Talk to an EGX-floor broker. Read the IPO prospectus of any EGX-30 issuer for trading mechanics.

### B.2 Egyptian regulatory regime (FRA + EGX)
- **What:** What is permitted, what requires a licence, what triggers reporting.
- **Why here:** Determines whether this product can be sold to a fund without legal exposure. Affects disclaimer copy, audit trail, retention policy.
- **Where:** N/A in code — this is policy work.
- **Level:** Awareness for engineers; Expert for a compliance officer (which the project needs).
- **Learn:** FRA website (fra.gov.eg). Capital Market Law 95/1992 + executive regulations. Egypt Personal Data Protection Law 151/2020. Get a 1-hour briefing from a Cairo-based capital-markets lawyer before ship.

### B.3 Quantitative finance fundamentals
- **What:** Returns, volatility, Sharpe / Calmar / Sortino / Information Ratio, drawdown, CAPM, Fama-French factors.
- **Why here:** All reported in `scripts/backtester.py`. Several are computed wrong today (risk-free rate, annualization) — `MEMORY.md` issue C3.
- **Where:** `scripts/backtester.py:301-372`.
- **Level:** Working → Expert.
- **Learn:** Hull *Options, Futures, and Other Derivatives*. Bodie/Kane/Marcus *Investments*. CFA Level I "Quantitative Methods" module.

### B.4 Backtesting methodology (the discipline that matters)
- **What:** Avoiding look-ahead bias, survivorship bias, snooping bias, regime-change blindness; walk-forward; train/test splits; statistical significance with small samples.
- **Why here:** **The single highest-leverage skill on this project.** Two look-ahead bugs already identified (`MEMORY.md` issue C). A senior quant will spot them in 5 minutes.
- **Where:** `scripts/backtester.py`, `scripts/run_real_backtests.py`, `tradingagents/ablation/`.
- **Level:** Expert.
- **Learn:** López de Prado *Advances in Financial Machine Learning* (THE book on this). Marcos's "10 Reasons Most Machine-Learning Funds Fail." Bailey & López de Prado on the *Probability of Backtest Overfitting* (PBO).

### B.5 Risk management for institutional trading
- **What:** Position limits, concentration, liquidity-adjusted exposure, VaR / CVaR / expected shortfall, stop-loss design, exit-window analysis.
- **Why here:** Encoded as `EGX_RISK_LIMITS` and the deterministic veto logic.
- **Where:** `agents/managers/risk_manager.py:14-40` (limits) and `:73-326` (checks).
- **Level:** Working → Expert.
- **Learn:** Jorion *Value at Risk*. Coleman *Quantitative Risk Management*. Read EGX-30 issuers' filings for real ADV / spread data.

### B.6 Technical analysis (correctly applied)
- **What:** RSI, MACD, Bollinger Bands, SMA — what they actually mean, when they fail, how to combine them without overfitting.
- **Why here:** Market analyst + Backtrader baseline.
- **Where:** `tradingagents/agents/analysts/market_analyst.py`, `scripts/bt_benchmark.py`.
- **Level:** Working.
- **Learn:** Murphy *Technical Analysis of the Financial Markets* (the canon). Aronson *Evidence-Based Technical Analysis* (the antidote — it shows most TA fails statistical significance tests).

### B.7 Fundamental analysis (sector-aware)
- **What:** Income/balance/cash-flow reading, ratio analysis, sector-specific benchmarks (banks ≠ real estate ≠ holdings ≠ operational).
- **Why here:** This is the strongest subsystem. Phase 1A/1B pipeline distinguishes 4 sectors with calibrated safety floors.
- **Where:** `agents/analysts/fundamentals/sector_config.py`, `financial_calculator.py`, `scoring.py`.
- **Level:** Expert.
- **Learn:** Damodaran *Investment Valuation*. *Financial Statement Analysis* (Subramanyam). For banks specifically: NIM, NPL ratio, CAR — different ratio set entirely.

### B.8 Transaction cost analysis (TCA)
- **What:** Brokerage, exchange fees, stamp duty, spread, slippage, market impact (Almgren-Chriss), opportunity cost.
- **Why here:** EGX cost stack hard-coded at 0.189%/side. Slippage at 0.1%/0.5%. Both are optimistic — `MEMORY.md` issue near C and the backtest audit.
- **Where:** `scripts/backtester.py:49-52`, `bt_benchmark.py:42`.
- **Level:** Working.
- **Learn:** Kissell *The Science of Algorithmic Trading and Portfolio Management*. Almgren-Chriss 2000 paper "Optimal Execution of Portfolio Transactions."

### B.9 Live-data infrastructure (Bloomberg / Refinitiv / EGX direct)
- **What:** Real institutional data feeds vs. consumer (yfinance).
- **Why here:** Currently consumer-grade only. Out of 4-week scope but will be required for any live deployment.
- **Where:** N/A in code — vendor procurement.
- **Level:** Awareness now; Working when you sign a contract.
- **Learn:** Bloomberg API (BLPAPI) docs. Refinitiv Eikon Data API. EGX direct-data product sheet.

---

## Section C. AI/ML skills (LLM-specific)

### C.1 LLM architecture awareness
- **What:** What an autoregressive transformer is, why it hallucinates, what context-window limits mean, how tokenization affects cost.
- **Why here:** Helps you reason about why an agent fails or costs more than expected.
- **Level:** Awareness → Working.
- **Learn:** Karpathy "Let's build GPT" video. Anthropic's Constitutional AI paper. *The Annotated Transformer*.

### C.2 Multi-model routing & failover
- **What:** Picking the right model per task; falling back across providers on rate-limit / error.
- **Why here:** `agents/utils/llm_failover.py` rotates DeepSeek → Google → OpenRouter → Groq.
- **Where:** Same file.
- **Level:** Working.
- **Learn:** OpenRouter docs. Read the failover code, draw its state machine, find the gaps.

### C.3 RAG + embedding-based memory
- **What:** Storing past situations as embeddings, retrieving similar ones to ground new reasoning.
- **Why here:** `FinancialSituationMemory` and bull/bear past-trade context. Currently cold-start broken — `MEMORY.md` issue K.
- **Where:** `tradingagents/agents/utils/memory.py`, `persistent_memory.py`.
- **Level:** Working.
- **Learn:** *Building LLM Apps with LangChain*. BM25 vs dense vs hybrid retrieval. Evaluate retrieval with `recall@k` on a labelled dev set.

### C.4 Evaluation of LLM-driven systems
- **What:** Hit rate, Brier score, calibration plots, IC, agreement metrics, ablation discipline.
- **Why here:** Phase 2B audit (`PROOF_OF_WORK.md`) does this honestly and reports null results — model the rest of the project on it.
- **Where:** `tests/phase2b_audit.py`, `tradingagents/ablation/evaluate.py`.
- **Level:** Expert.
- **Learn:** Brier (1950) original paper. *Forecasting: Principles and Practice* (Hyndman). Anthropic's "Many-shot jailbreaking" + "On the conversational use of language models" for eval discipline.

### C.5 Prompt-output schema validation
- **What:** Enforcing typed JSON output and rejecting/retrying on schema violation, not silently accepting malformed.
- **Why here:** Currently leniently accepted (see `MEMORY.md` issue M, scoring weakness J).
- **Where:** All analyst output parsers; `signal_processing.py`.
- **Level:** Working.
- **Learn:** OpenAI structured outputs / Anthropic tool-use JSON schemas. `instructor` library (Python). Pydantic + retry.

### C.6 Determinism & reproducibility
- **What:** Pinning temperature, seeds, model versions, prompt versions, tool versions; logging fingerprints per call.
- **Why here:** Currently broken — `MEMORY.md` issue B. Without this, no audit is meaningful.
- **Where:** Every `.invoke()` site.
- **Level:** Working.
- **Learn:** OpenAI's `seed` parameter docs. Anthropic does NOT support seeds — design for that asymmetry. Read the *Reproducibility in ML* literature (e.g., Pineau's checklist).

### C.7 Model risk management (MRM)
- **What:** Model card, validation report, monitoring plan, kill-switch, challenger model. The artifacts a regulated firm requires.
- **Why here:** Currently zero — Week-4 plan in `MEMORY.md`.
- **Where:** N/A — you'll be writing them.
- **Level:** Working.
- **Learn:** SR 11-7 (Fed guidance on model risk). Mitchell et al. "Model Cards for Model Reporting." Google's "ML Test Score" paper.

---

## Section D. Arabic NLP / domain-specific skills

### D.1 Arabic linguistics for NLP
- **What:** Diacritics, hamza variants, dialect vs MSA, code-switching, RTL handling.
- **Why here:** Half the news + most social posts are Arabic; Egyptian dialect is its own thing.
- **Where:** `tradingagents/utils/text_preprocessor.py`, `utils/sentiment_engine.py`.
- **Level:** Working.
- **Learn:** Habash *Introduction to Arabic Natural Language Processing*. Egyptian dialect cheat sheets. CAMeL Lab (NYU Abu Dhabi) tooling.

### D.2 Egyptian financial slang
- **What:** "بامب" (pump), "تجميع" (accumulation), "تصريف" (distribution), "هيطلع" (it'll rise), "هينزل" (it'll fall), etc. — the actual vocabulary of Egyptian retail trader chatter.
- **Why here:** v2 social pipeline depends on this. Generic Arabic NLP misses it.
- **Where:** `scripts/twitter_pipeline/v2/intent.py` patterns; `dataflows/social_media_sources/sentiment_engine.py` lexicon.
- **Level:** Expert (native-speaker territory).
- **Learn:** Read 100 EGX retail FB posts. Talk to retail Egyptian traders. Watch Egyptian trading YouTube channels.

### D.3 Bilingual sentiment models
- **What:** When to use FinBERT (English finance) vs CAMeLBERT-DA (Arabic dialect) vs XLM-RoBERTa (mixed/code-switched). Routing heuristics.
- **Why here:** `utils/sentiment_engine.py:165-551`.
- **Where:** Same file.
- **Level:** Working.
- **Learn:** FinBERT paper (Yang et al. 2020). CAMeLBERT paper (Inoue et al. 2021). XLM-R paper (Conneau et al. 2020).

---

## Section E. Working effectively with Claude Code on this project

These aren't just generic prompting tips — they're the specific habits that pay off most for this codebase.

### E.1 Lead with files, not abstractions
- **Why:** Claude is fastest when grounded. "Read `tradingagents/agents/managers/risk_manager.py:73-326` and tell me which checks fire on backtest mode" beats "review the risk manager."
- **Habit:** Open every prompt with at least one file path.

### E.2 Prefer `MEMORY.md` over re-discovery
- **Why:** Most issues are already catalogued. You waste tokens (and time) if Claude re-finds them.
- **Habit:** Start non-trivial sessions with: *"Read CLAUDE.md and MEMORY.md first. Don't re-audit; build on what's there."*

### E.3 Be honest about what's broken
- **Why:** If you ask Claude to "fix the backtester to show better returns," you get garbage. If you ask "remove the look-ahead bias documented in MEMORY.md issue C and re-run on EGX-30," you get correct work.
- **Habit:** State the truth, not the desired outcome.

### E.4 Force structured output for agent edits
- **Why:** Trading-decision code can't be reviewed by reading prose. Request diffs, file:line citations, and a regression-test plan in every change.
- **Habit:** End every code-change prompt with: *"Show me the diff, the file:line citations for what's changed, and which existing tests cover this."*

### E.5 Treat hallucinated APIs as a fire-drill
- **Why:** Claude sometimes invents function signatures. In a financial system, a fake API in production is catastrophic.
- **Habit:** After any non-trivial generation, run `python -c "from <module> import <thing>"` as a smoke test before committing.

### E.6 Use Claude for the parts where it's strongest, and stop when it isn't
- **Strongest here:** Reading large codebases, drafting tests, refactoring, writing docs (this file is one), drafting prompts, drafting PR descriptions, generating SQL migrations.
- **Weakest here:** Inventing trading alpha, judging statistical significance, reading non-Latin PDFs, predicting EGX-specific edge cases (suspensions, regulatory halts), writing legal/compliance copy.
- **Habit:** Ask yourself "would a senior engineer accept this without verification?" If not, verify.

### E.7 Use parallel sub-agents for audits
- **Why:** A "study the codebase" task is faster split across parallel Explore agents (data layer / agent quality / backtest / server) and synthesised by you, than done linearly.
- **Habit:** For any "review the whole X" request, ask Claude to dispatch parallel investigators and synthesize.

### E.8 Maintain `MEMORY.md` discipline
- **Why:** This file is only useful if it's current. Stale audit notes are worse than none.
- **Habit:** Every session that fixes or finds something updates `MEMORY.md` in the same commit. Never split.

### E.9 Be explicit about scope and risk
- **Why:** "Improve the data layer" is a 3-week project. "Centralize `.CA` symbol normalization in one helper, update all 3 call sites, add a round-trip unit test" is one PR.
- **Habit:** Scope every prompt to one PR (≤300 lines of diff, ≤5 files), one acceptance test, one rollback.

### E.10 Pair human-in-the-loop with regression gates
- **Why:** Trading code touches money. The only safe pattern is: Claude proposes → you read the diff → tests pass → you ship.
- **Habit:** Never let Claude run `git push` to main on its own. CI runs are green-light, not merge-light.

---

## Section F. Skills self-assessment matrix (for you)

Score yourself 0–3 (0 = nothing, 1 = aware, 2 = working, 3 = expert). The columns are the four ship gates from `MEMORY.md` Week-1 to Week-4. The rows are skills.

|                                     | W1: stop bleeding | W2: defensible backtest | W3: server hardening | W4: observability + MRM |
|-------------------------------------|:-:|:-:|:-:|:-:|
| Python advanced (A.1)               | 3 | 3 | 3 | 3 |
| LangGraph + tool use (A.2 / A.3)    | 2 |   |   |   |
| Pydantic + schemas (A.5)            | 2 |   | 3 |   |
| FastAPI + WebSockets (A.6)          |   |   | 3 |   |
| React/TS (A.7)                      |   |   | 2 |   |
| Pandas + time-series (A.8)          |   | 3 |   |   |
| Postgres + pgvector (A.9)           | 2 |   |   | 2 |
| Docker + CI/CD (A.11 / A.12)        | 2 |   |   | 3 |
| Observability (A.13)                |   |   |   | 3 |
| Testing (A.14)                      | 2 | 3 | 2 | 2 |
| Secret hygiene (A.15)               | 3 |   |   |   |
| EGX microstructure (B.1)            |   | 3 |   |   |
| Regulatory FRA (B.2)                |   | 2 | 2 | 3 |
| Quant finance (B.3)                 |   | 3 |   |   |
| **Backtesting methodology (B.4)**   |   | **3** |   |   |
| Risk management (B.5)               |   | 2 | 2 |   |
| Fundamental analysis (B.7)          |   | 2 |   |   |
| TCA (B.8)                           |   | 2 |   |   |
| Prompt engineering (A.4)            | 2 |   |   |   |
| Eval of LLM systems (C.4)           |   | 3 |   | 2 |
| LLM determinism (C.6)               | 3 |   |   |   |
| Model risk management (C.7)         |   |   |   | 3 |
| Arabic NLP + dialect (D.1 / D.2)    |   |   | 2 |   |
| Bilingual sentiment models (D.3)    |   | 2 |   |   |

**How to read it:** any cell marked 2 or 3 in a week-column is a skill you must have at that level by that week. Gaps = hire / partner / pair.

---

## Section G. Hire-vs-learn-vs-AI-assist guide

| Skill area | Decision (for a 1-month ship) |
|---|---|
| Python / LangGraph / FastAPI / React | Learn — Claude Code accelerates this |
| **EGX microstructure + FRA regulatory** | **Hire / contract** — non-negotiable, 1-day briefing minimum |
| **Backtesting methodology** | **Learn from López de Prado, then have a senior quant review** before publishing any return numbers |
| Quant finance fundamentals | Learn — CFA L1 quant chapter is enough for v1 |
| Arabic dialect + financial slang | Hire native Egyptian speaker as advisor; supplement with Claude |
| Model risk management | Contract template + lawyer review; engineering writes; compliance signs |
| DevOps / Docker / CI | Learn — Claude Code can scaffold; you maintain |
| Observability + Prometheus | Learn — straightforward once scaffolded |
| Live broker integration (FIX, Bloomberg) | **Hire / partner / defer** — not in 4-week scope |

---

## How to use this file

- **At project start (or onboarding a new engineer):** read it top-to-bottom. Score yourself in §F. Fill the gaps before they bite.
- **Before a task:** find the subsystem in §A/B/C/D, check the "Level needed". If you're below, read the resource or pair.
- **When prompting Claude Code:** reference specific skill IDs (e.g. *"Apply B.4 backtesting discipline to the C.6 determinism fix in scripts/backtester.py"*). Forces precision.
- **When hiring:** §F + §G are the JD. Don't hire someone who can't score 3 on B.4 if their job is the backtester.
- **Quarterly:** revisit §F. Skills decay. Update levels.
