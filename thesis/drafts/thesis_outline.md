# StockHive: AI Multi-Agent Trading System for the Egyptian Exchange
## Thesis Outline — Project-Specific Table of Contents

> **Status key:** DRAFT = writable now | BLOCKED = needs benchmark/teammate input | TODO = not started
>
> **Official template:** `thesis/template/gp26_thesis_template_with_cover_page.md` (unchanged)
>
> **Last updated:** 2026-06-15

---

## Front Matter

### Cover Page
- Faculty of Computing and Information Sciences
- **Title:** StockHive: AI Multi-Agent Trading System for the Egyptian Exchange
- **Authors:** TODO — fill team member names
- **Supervisors:** TODO — fill supervisor names
- **Date:** July 2026
- **Status:** DRAFT

### Declaration & Acknowledgements
- Standard declaration of originality
- Acknowledgements: supervisors, teammates, open-source projects (LangGraph, TradingAgents, DeepSeek)
- **Status:** DRAFT

### Abstract (150-250 words)
- **Status:** BLOCKED — requires accepted 5-ticker benchmark results
- **TODO:** Write after benchmark rerun is accepted. Must include: motivation, method (multi-agent LangGraph), key result (alpha vs EGX30), conclusion.
- **Safety note:** Do not claim EGX30 outperformance unless benchmark reports confirm it.

---

## Chapter 1: Introduction

### 1.1 Problem Statement
- **Core problem:** Retail investors on the Egyptian Exchange face severe information asymmetry. Professional-grade fundamental analysis, sentiment aggregation, and risk-adjusted decision support are inaccessible to most Egyptian market participants.
- **Technical challenge:** No existing system combines multi-agent LLM reasoning with EGX-specific data sources (Arabic bilingual sentiment, local CSV fundamentals, EGX regulatory constraints) into a structured research tool.
- **Status:** DRAFT — can write now

### 1.2 Project Objectives
- O1: Design and implement a multi-agent LLM trading framework adapted for EGX stocks
- O2: Build a 3-stage Chain-of-Thought fundamental analysis pipeline (Data-CoT, Concept-CoT, Thesis-CoT) validated against the literature [P6, P7]
- O3: Implement bilingual (Arabic + English) sentiment analysis covering social media, news, and financial forums
- O4: Develop a structured Bull/Bear debate mechanism for investment thesis generation
- O5: Evaluate the system against EGX30 buy-and-hold benchmark using walk-forward backtesting with honest, look-ahead-free metrics
- **Status:** DRAFT — can write now

### 1.3 Motivation and Significance
- **Market need:** EGX has ~200+ listed companies but limited AI-powered research tools. Retail investors rely on informal social media signals and broker tips.
- **Academic need:** Multi-agent financial AI research (TradingAgents [P1], FinCon [P3], FinAgent [P4]) has focused exclusively on US/developed markets. No published system targets the Egyptian Exchange with its unique constraints (long-only, +/-10% circuit breaker, T+2 settlement, Sunday-Thursday trading).
- **Technical significance:** Demonstrates that commodity LLM APIs (DeepSeek) with structured multi-agent orchestration (LangGraph) can produce analyst-grade research theses for an emerging market.
- **Status:** DRAFT — can write now

### 1.4 Scope and Limitations
- **In scope:**
  - 29 EGX tickers across 6 sectors (banks, real estate, industry, telecom, financial services, food & beverage)
  - 4 analyst modules: market (technical), fundamentals, news, social media
  - Research/decision-support output only — not a live order-execution system
  - Walk-forward backtesting with EGX30 benchmark
  - BM25-based memory retrieval with temporal safety gating
- **Out of scope:**
  - Live order execution or broker API integration
  - Short selling, leverage, or derivatives (prohibited by EGX/FRA regulations)
  - Real-time intraday trading
  - Full EGX universe (~200+ tickers)
  - Online learning during backtests (reflection flushing disabled)
- **Known limitations:**
  - LLM non-determinism (temperature=0, seed=42 mitigates but does not eliminate)
  - EGX data availability gaps (some tickers lack sufficient historical data)
  - Arabic dialect NLP remains imperfect for Egyptian colloquial financial discussions
  - Backtest uses BM25-only memory (semantic Chroma embeddings not validated for mixed-dimension safety)
- **Status:** DRAFT — can write now

### 1.5 Team Members' Contributions
- **TODO:** Fill with actual team member names and roles
- | Member | Role/Responsibility |
  |--------|-------------------|
  | TODO | TODO |
  | TODO | TODO |
- **Status:** TODO — needs team input

---

## Chapter 2: Literature Review

### 2.1 Similar Systems

#### 2.1.1 Academic Scientific Research
- **Source material:** `thesis/literature_reviews/fundamental_analyst_lit_review.md` (15 papers, P1-P15)
- **Source material:** `thesis/research_notes/egx30_fundamental_analyst_ai_research.md` (32 sources, broader scope)
- **Structure for this section:**
  - **Multi-agent trading frameworks:** TradingAgents [P1], FinMem [P2], FinCon [P3], FinAgent [P4], MarketSenseAI 2.0 [P5], FinRobot [P6]
  - **LLM financial reasoning:** Kim, Muhn & Nikolaev [P7] — GPT-4 outperforms median analyst
  - **Benchmarks:** FinanceBench [P8] — 81% failure rate on naive RAG
  - **RAG for financial documents:** Setty et al. [P9] — hybrid BM25 + dense retrieval
  - **Surveys:** Nie et al. [P10], Lee et al. [P11], Ding et al. [P12], Li et al. [P13]
  - **Domain LLMs:** BloombergGPT [P14] — validates "don't train from scratch" for this project
  - **TODO:** Add social media sentiment analysis papers (teammate input needed)
  - **TODO:** Add news analysis / NLP papers (teammate input needed)
  - **TODO:** Add technical analysis papers (teammate input needed)
  - **TODO:** Add risk management / portfolio theory papers (teammate input needed)
  - **TODO:** Add EGX-specific papers from research note (Elnokoudy 2025, Gao 2026, etc.)
- **Status:** DRAFT (fundamentals portion) + TODO (other modules)

#### 2.1.2 Market/Industrial Research
- Bloomberg Terminal — comprehensive but expensive, no EGX multi-agent reasoning
- TradingView — charting and community, no fundamental analysis automation
- CapitalCube / Wright Reports — automated equity reports, US-only, no LLM reasoning [P6]
- Mubasher / Investing.com EGX — market data providers, no AI-driven analysis
- **Gap:** No existing commercial product combines multi-agent LLM reasoning with EGX-specific data
- **Status:** DRAFT — can write now

### 2.2 Technologies and Tools Overview
- | Technology | Purpose | Version/Details |
  |-----------|---------|-----------------|
  | Python 3.11+ | Core language | All modules |
  | LangGraph | Multi-agent orchestration | State-based graph with typed AgentState |
  | DeepSeek API | LLM backbone (deep_think + quick_think) | deepseek-chat via OpenAI-compatible client |
  | ChromaDB | Vector memory store | Persistent on-disk, BM25 fallback |
  | rank_bm25 | Keyword-based memory retrieval | BM25Okapi with temporal filtering |
  | yfinance | OHLCV price data | Primary data vendor for EGX .CA tickers |
  | FinBERT / CAMeLBERT-DA / XLM-R | Bilingual sentiment analysis | Arabic + English routing |
  | FastAPI | REST API server | Async, WebSocket support |
  | React 19 + Vite | Dashboard frontend | lightweight-charts for price visualization |
  | PostgreSQL | Audit persistence + backtest storage | Optional, schema in db_schema.sql |
  | Redis | WebSocket event streaming | Optional, pub/sub for live progress |
  | pytest | Test framework | Unit + integration tests |
  | Prometheus + Grafana | Monitoring stack | Phase 1 instrumentation complete |
- **Status:** DRAFT — can write now

### 2.3 Gap Analysis
- **Gap 1:** No multi-agent AI trading system for the Egyptian Exchange in published literature
- **Gap 2:** Existing multi-agent frameworks (TradingAgents [P1], FinCon [P3]) evaluated only on US large-cap stocks
- **Gap 3:** Arabic financial NLP (especially Egyptian dialect) underrepresented in financial agent research
- **Gap 4:** EGX regulatory constraints (long-only, +/-10% circuit breaker) not modeled in any published framework
- **Gap 5:** Walk-forward backtesting with honest metrics (no look-ahead) rare in multi-agent trading literature
- **This project addresses:** All five gaps by building a production-grade research prototype adapted for EGX
- **Status:** DRAFT — can write now

---

## Chapter 3: System Analysis & Requirements

### 3.1 Functional Requirements

#### 3.1.1 System Functions
- FR-01: The system must analyze EGX stocks using 4 parallel analyst agents (market, fundamentals, news, social) [Must-have]
- FR-02: The system must produce structured BUY/HOLD/SELL recommendations with confidence scores [Must-have]
- FR-03: The system must enforce EGX regulatory constraints (long-only, no leverage, +/-10% daily limit) [Must-have]
- FR-04: The system must support bilingual sentiment analysis (Arabic + English) [Must-have]
- FR-05: The system must run walk-forward backtests against EGX30 benchmark [Must-have]
- FR-06: Users should be able to interact via a web dashboard showing analysis results [Should-have]
- FR-07: Users should be able to run analyses via CLI or REST API [Should-have]
- FR-08: The system must persist analysis audit trails for reproducibility [Must-have]
- FR-09: The system should support memory retrieval with temporal safety gating [Should-have]
- FR-10: The system should provide real-time progress updates during analysis [Could-have]
- **Status:** DRAFT — can write now

#### 3.1.2 Detailed Functional Specification
- **TODO:** Create FR table for each requirement (Description, Input, Output, Priority, Pre/Post-condition)
- **Status:** DRAFT — can write now

### 3.2 Non-functional Requirements
- **Security:** API keys in .env only, no hardcoded secrets (known violation: EODHD key — see MEMORY.md)
- **Reliability:** Crash-hardened backtester with --resume, LLM failover with multi-provider retry
- **Portability:** Python-based, cross-platform, Docker-ready monitoring stack
- **Maintainability:** Modular agent architecture, typed state dicts, structured communication
- **Availability:** Local deployment, optional cloud (Postgres/Redis)
- **Usability:** Rich CLI TUI, React dashboard, REST API
- **Performance:** ~180s per evaluation date, 8 LLM calls per date
- **Status:** DRAFT — can write now

### 3.3 Use Case Diagrams / Scenarios
- UC-01: Analyst runs single-stock analysis
- UC-02: Analyst runs walk-forward backtest
- UC-03: Analyst views dashboard with historical results
- UC-04: System performs automated risk veto
- **TODO:** Create actual UML use case diagram
- **Status:** TODO — needs diagram tool

### 3.4 Stakeholders and User Roles
- **Primary:** Retail investors and financial researchers in Egypt
- **Secondary:** Academic supervisors evaluating the system
- **Tertiary:** Potential institutional users (EGX brokerages)
- **Status:** DRAFT — can write now

---

## Chapter 4: Methodology

### 4.1 Project Development Methodology
- Agile/iterative approach with AI-augmented development
- Sprint-based delivery: data layer -> agents -> integration -> testing -> benchmarking
- Continuous integration with automated test suite (pytest)
- Audit-driven development: MEMORY.md tracks all known issues and resolutions
- **Status:** DRAFT — can write now

### 4.2 Project Timeline
- **TODO:** Gantt chart or sprint timeline from team
- **TODO:** Reconstruct key milestones from git log if needed
- **Status:** TODO — needs team input

### 4.3 Tools and Technologies Used
- See Ch2.2 table (cross-reference)
- Additional development tools: Git/GitHub, VS Code, Claude Code (AI-augmented development)
- **Status:** DRAFT — can write now

---

## Chapter 5: System Design

### 5.1 System Architecture Diagram
- Multi-agent graph pipeline (see CLAUDE.md Section 2 for ASCII diagram)
- **TODO:** Create clean visual diagram for thesis
- Components: Entry points -> TradingAgentsGraph -> Analyst Team (parallel) -> Research Team (debate) -> Trader -> Risk Management -> Signal Processor
- **Status:** DRAFT (textual) + TODO (diagram)

### 5.2 Database Design
- PostgreSQL schema: audit_log, backtest_results, social_v2_posts tables
- ChromaDB: 5 vector collections (bull_memory, bear_memory, trader_memory, invest_judge_memory, risk_manager_memory)
- diskcache: TTL-based data caching layer
- **Source:** db_schema.sql
- **Status:** DRAFT — can write now

### 5.3 Data Design

#### 5.3.1 Data Description
- **EGX Fundamentals CSV Dataset:** Income statements, balance sheets, key ratios for 31 tickers. Annual + quarterly. Source: Mubasher/EGX Annex 5 PDFs parsed via `parse_egx_annex5.py`.
- **EGX30 Historical Data CSV:** 1,561 rows, 2020-01-02 to 2026-06-14. Source: Investing.com export. Used as benchmark.
- **yfinance OHLCV:** Daily price data for all .CA tickers. Primary market data source.
- **Social media corpus:** Facebook (Apify), Reddit, Telegram, Mubasher RSS. Arabic + English.
- **Status:** DRAFT — can write now

#### 5.3.2 Dataset Description
- **TODO:** Create dataset description tables per template format
- **Status:** DRAFT — can write now

### 5.4 UML Diagrams
- **TODO:** Class diagram (AgentState, TradingAgentsGraph, FinancialSituationMemory, BacktestingEngine)
- **TODO:** Sequence diagram (single analysis flow: entry -> analysts -> debate -> trader -> risk -> signal)
- **TODO:** Activity diagram (backtest walk-forward loop)
- **Status:** TODO — needs diagram creation

### 5.5 UI/UX Mockups or Wireframes
- **TODO:** Dashboard screenshots (React 19 + Vite + lightweight-charts)
- **TODO:** CLI TUI screenshots
- **Status:** TODO — needs screenshots

---

## Chapter 6: Implementation

### 6.1 Description of Major Modules/Components
1. **Analyst Team** (4 parallel agents): Market, Fundamentals, News, Social
2. **Fundamentals Pipeline** (3-stage CoT): data_cot.py -> concept_cot.py -> thesis_cot.py
3. **Research Team** (debate): Bull Researcher -> Bear Researcher -> Research Manager
4. **Trader Agent**: Execution plan with liquidity-aware position sizing
5. **Risk Manager**: Deterministic EGX constraint checks + LLM risk judge
6. **Signal Processor**: Regex BUY/SELL/HOLD extraction (no LLM)
7. **Memory System**: ChromaDB + BM25 with temporal safety gating (valid_after_date)
8. **Data Gateway**: Cache -> primary -> fallback chain for all data sources
9. **API Server**: FastAPI REST + WebSocket with Prometheus metrics
10. **Dashboard**: React 19 + lightweight-charts
- **Status:** DRAFT — can write now from CLAUDE.md and codebase

### 6.2 Code Snippets
- **Snippet 1:** 3-stage fundamentals pipeline orchestrator (pipeline.py)
- **Snippet 2:** Risk manager deterministic veto logic (risk_manager.py)
- **Snippet 3:** Signal processor regex extraction (signal_processing.py)
- **Snippet 4:** Temporal memory safety gate (memory.py — valid_after_date filtering)
- **Snippet 5:** Walk-forward backtester date scheduling (backtester.py)
- **Status:** DRAFT — can extract from codebase now

### 6.3 Integration Process
- LangGraph state-based graph compilation
- Agent communication via typed AgentState dict (no direct imports)
- FastAPI server integrates graph execution with WebSocket progress events
- Dashboard consumes REST API + WebSocket for real-time updates
- **Status:** DRAFT — can write now

### 6.4 Version Control Practices
- Git with main branch, feature branches for major components
- CLAUDE.md as operational reference, MEMORY.md as audit log
- Automated test suite as regression gate
- **Status:** DRAFT — can write now

---

## Chapter 7: Testing & Validation

### 7.1 Test Plan and Strategy
- **Strategy:** White-box unit testing + integration testing + walk-forward benchmarking
- **Tools:** pytest, automated CI, manual benchmark protocol
- **Goals:** Correctness of financial calculations, temporal safety, EGX constraint enforcement, benchmark integrity
- **Status:** DRAFT — can write now

### 7.2 Unit Testing, Integration Testing
- **Unit tests:** Fundamentals phase 1A/1B (ratio calculations, data loading, standardization)
- **Unit tests:** Memory temporal safety (30 tests — valid_after_date filtering)
- **Unit tests:** Risk manager veto logic (EGX constraints)
- **Integration tests:** Full pipeline smoke tests (single-ticker, 3-ticker)
- **TODO:** Collect actual pytest output and pass/fail counts
- **Status:** DRAFT (descriptions) + TODO (actual results)

### 7.3 Test Cases and Results
- **TODO:** Table of key test cases with input/expected/actual/status
- **TODO:** Include benchmark protocol test cases (stop rules, acceptance criteria)
- **Status:** DRAFT (structure) + BLOCKED (benchmark performance rows need accepted results)

### 7.4 Usability and Performance Testing
- **Performance:** ~180s per evaluation date, 8 LLM calls per date, ~$0.002 per analysis
- **TODO:** Fill with accepted 5-ticker benchmark metrics
- **Status:** BLOCKED — needs accepted benchmark results

### 7.5 Bug Tracking
- **System:** MEMORY.md audit trail (maintained across all development sessions)
- **Key resolved bugs:**
  - Bear researcher empty output (linearized debate flow)
  - Backtester look-ahead bias (deleted _evaluate_trade_outcomes, config-driven risk-free rate)
  - Chroma $lte string incompatibility (integer YYYYMMDD storage)
  - BM25 negative IDF with small corpus (padding documents)
- **Status:** DRAFT — can write now from MEMORY.md

---

## Chapter 8: Results & Evaluation

### 8.1 Comparison with Initial Requirements
- **TODO:** FR/NFR status table mapping each requirement to implementation status
- **Status:** DRAFT (structure) + partially writable

### 8.2 Performance Metrics
- **Benchmark protocol:** Walk-forward, 20-day intervals, EGX30 buy-and-hold baseline, BM25-only memory
- **Accepted smoke results (infrastructure validation only, not thesis-grade):**
  - Single-ticker COMI.CA: passed all gates
  - 3-ticker smoke (COMI, TMGH, ETEL): passed infrastructure gates
- **5-ticker thesis run:** INVALID/DISCARDED (2026-06-15 attempt failed: stale report reuse + missing API key). Pending rerun.
- **TODO:** Fill per-ticker tables after accepted 5-ticker benchmark:
  - | Ticker | Total Return | Sharpe | Max DD | Win Rate | EGX30 Return | Alpha |
  - | --- | --- | --- | --- | --- | --- | --- |
  - | COMI.CA | TODO | TODO | TODO | TODO | TODO | TODO |
  - | TMGH.CA | TODO | TODO | TODO | TODO | TODO | TODO |
  - | ETEL.CA | TODO | TODO | TODO | TODO | TODO | TODO |
  - | SWDY.CA | TODO | TODO | TODO | TODO | TODO | TODO |
  - | FWRY.CA | TODO | TODO | TODO | TODO | TODO | TODO |
- **Status:** BLOCKED — needs accepted 5-ticker benchmark results

### 8.3 User Feedback
- **TODO:** Collect user feedback (surveys, supervisor review, peer testing)
- **Status:** BLOCKED — not yet conducted

---

## Chapter 9: Conclusion & Future Work

### 9.1 Summary of Achievements
- **TODO:** Write after accepted benchmark results
- **Status:** BLOCKED — needs final metrics

### 9.2 Challenges Faced and Overcome
- **Challenge 1:** EGX data scarcity — solved with multi-source fallback chain (yfinance -> EODHD -> local CSV)
- **Challenge 2:** Arabic dialect NLP — solved with FinBERT/CAMeLBERT-DA/XLM-R ensemble routing
- **Challenge 3:** Look-ahead bias in backtesting — solved by deleting _evaluate_trade_outcomes, config-driven risk-free rate, date-intersected benchmark
- **Challenge 4:** Bear researcher empty output — solved by linearizing Bull->Bear->Manager debate flow
- **Challenge 5:** Chroma vector DB type incompatibility — solved with integer YYYYMMDD storage for temporal filtering
- **Challenge 6:** Memory temporal safety — solved with valid_after_date gating across all agent retrieval paths
- **Status:** DRAFT — can write now

### 9.3 Suggested Enhancements
- Online reinforcement learning meta-policy for position sizing
- Expanded ticker universe (EGX-70, full EGX main market)
- Semantic vector memory with validated embedding dimensions
- Live broker API integration for paper trading
- Authentication and authorization for API server
- Alerting rules in Prometheus/Grafana monitoring stack
- **Status:** DRAFT — can write now

### 9.4 Possibility of Future Research or Scaling
- Multi-agent systems for other MENA exchanges (Saudi Tadawul, Dubai DFM)
- Cross-market correlation analysis using shared agent architecture
- Arabic financial NLP benchmark dataset for EGX
- Comparative study: multi-agent vs. single-agent vs. classical quant on emerging markets
- **Status:** DRAFT — can write now

---

## References
- **Format:** IEEE
- **Verified (from fundamentals lit review):** [P1]-[P14] fully verified, [P15] partially verified
- **Verified (from research note):** Sources [1]-[32] — need cross-check against IEEE format
- **TODO:** Consolidate all references into single IEEE-formatted list
- **TODO:** Verify [P15] author names before final submission
- **TODO:** Add references from teammate lit reviews when available
- **Status:** DRAFT (partial) + TODO (consolidation)

---

## Appendices

### a. Code
- GitHub repository link: TODO
- **Status:** DRAFT

### b. Dataset Folder
- EGX fundamentals CSVs (income statements, balance sheets, key ratios)
- EGX30 Historical Data CSV (benchmark)
- Social media corpus samples
- **Status:** DRAFT

### c. Deployment Manual
- Prerequisites: Python 3.11+, .env configuration, optional Docker for monitoring
- Installation: pip install, database setup, ChromaDB initialization
- Running: uvicorn, CLI, backtester commands
- **Source:** CLAUDE.md Section 6
- **Status:** DRAFT — can write now

### d. User Guide / Training Material
- Dashboard usage guide
- CLI interaction guide
- Backtest interpretation guide
- **Status:** TODO

### e. Survey/Interview Questions
- **TODO:** Design if user feedback collection is planned
- **Status:** TODO

### f. Additional Diagrams
- Multi-agent graph flow diagram
- Fundamentals 3-stage CoT pipeline diagram
- Data gateway fallback chain diagram
- Benchmark protocol flowchart
- **Status:** TODO — needs diagram creation

---

## Benchmark Status Log

| Run | Date | Tickers | Window | Status | Notes |
|-----|------|---------|--------|--------|-------|
| Single-ticker smoke | 2026-06-15 | COMI.CA | 2024-06-01 to 2024-07-15 | Accepted | Clean BM25-only, no resume |
| 3-ticker smoke | 2026-06-15 | COMI, TMGH, ETEL | 2024-06-01 to 2024-07-15 | Accepted (infra gates) | TMGH 2-date boundary noted |
| 5-ticker thesis (attempt 1) | 2026-06-15 | COMI, TMGH, ETEL, SWDY, FWRY | 2024-01-02 to 2024-07-14 | INVALID/DISCARDED | Stale report reuse + DEEPSEEK_API_KEY not loaded |
| 5-ticker thesis (attempt 2) | PENDING | COMI, TMGH, ETEL, SWDY, FWRY | 2024-01-02 to 2024-07-14 | Pending | Needs --force + source .env |
