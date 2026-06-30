# StockHive — AI-Powered Investment Assistant for the Egyptian Stock Exchange
## Graduation Project Presentation Discussion Guide

> This document is designed to be converted into a PowerPoint presentation.
> Each slide section includes bullets, speaker notes, visuals, and technical details.
> All content is grounded in the actual repository at commit time (2026-06-23).

---

# 1. Introduction & Storytelling (Slides 1–5)

---

## Slide 1: Project Title

**Main message:**
Introduce the project and team.

**Slide bullets:**
- **StockHive** — AI-Powered Investment Assistant for the Egyptian Stock Exchange (EGX)
- A multi-agent system that combines technical, fundamental, and sentiment analysis
- Built with LangGraph, DeepSeek LLM, FastAPI, React, and Prometheus
- Graduation Project — Faculty of Computer Science

**Speaker notes:**
Good morning. Today we present StockHive — an AI-powered investment assistant designed specifically for the Egyptian Stock Exchange. This is not a simple chatbot or a price prediction tool. It is a full decision-support system that mimics how a professional investment team operates: multiple specialized analysts examine a stock from different angles, debate their findings, and produce an explainable recommendation. We built it as a research prototype that demonstrates how multi-agent AI can be applied to a real, underserved market.

**Suggested visual:**
Project logo or title slide with EGX + AI visual. Show team member names.

**Technical details / prompts to mention:**
- Repository: `tradingagents/` Python package, `dashboard/` React app, `server/` FastAPI backend
- ~29 actively covered EGX tickers across Banks, Real Estate, Industry, Telecom, Financial Services, Food & Beverage

---

## Slide 2: Why Investing Is Difficult

**Main message:**
Investing in stock markets is inherently complex and uncertain — even for professionals.

**Slide bullets:**
- Markets are driven by thousands of interconnected factors
- Professional analysts spend hours per stock per day
- Retail investors lack access to institutional-quality research
- Egyptian market has unique challenges: limited English-language coverage, Arabic-dominant news, sparse data for mid-cap stocks
- No tool can guarantee profit — but better tools lead to better-informed decisions

**Speaker notes:**
Before we dive into the technical details, let us ground the conversation in why this problem matters. Investing is hard. Even professional fund managers with teams of analysts and Bloomberg terminals struggle to consistently outperform the market. For a retail investor in Egypt, the challenge is multiplied: there is less coverage, less data, and the news is often in Arabic with no structured sentiment analysis available. Our goal is not to promise profits — no honest system can do that. Our goal is to provide better information, better structure, and better reasoning to help investors make more informed decisions.

**Suggested visual:**
Split visual: (left) overwhelmed retail investor with scattered news, (right) calm professional with organized research dashboard.

**Technical details / prompts to mention:**
- EGX has ~200+ listed stocks but only ~30 are well-covered
- Arabic financial news requires specialized NLP (CAMeLBERT, FinBERT)

---

## Slide 3: Challenges Investors Face

**Main message:**
Four core challenges define the investor's pain point.

**Slide bullets:**
- **Information Overload**: News, reports, social media, technical charts — too much data, too little time
- **Conflicting Signals**: Technical indicators say BUY, news says SELL — how to reconcile?
- **Time-Consuming Research**: A thorough analysis requires examining financials, news, technicals, and sentiment separately
- **Risk Management**: Knowing when NOT to trade is as important as knowing when to trade

**Speaker notes:**
We identified four core pain points from talking to Egyptian retail investors and studying institutional workflows. First, information overload — there is too much data to process manually. Second, conflicting signals — different analysis methods often disagree, and investors do not know how to weigh them. Third, time — a proper analysis of one stock can take hours. Fourth, risk management — most retail investors focus on entry but not on exit or position sizing. Our system addresses each of these directly.

**Suggested visual:**
Four-quadrant infographic, each quadrant representing one challenge with an icon.

**Technical details / prompts to mention:**
- The system uses 4 specialized analysts that run in parallel to address information overload
- Conflicting signals are resolved by a structured Bull vs. Bear debate

---

## Slide 4: Why the Egyptian Stock Market Needs Better Tools

**Main message:**
The EGX has unique characteristics that make generic tools insufficient.

**Slide bullets:**
- **Regulatory Constraints**: Long-only market, no short selling, no leverage, ±10% daily price limits
- **Limited Data Coverage**: Many mid-cap stocks have sparse or no English-language news
- **Arabic-Dominant Sentiment**: Most retail discussion happens in Arabic (Facebook groups, Telegram channels)
- **Liquidity Challenges**: Some stocks have very low average daily volume (ADV)
- **T+2 Settlement**: Cash management and timing matter more than in T+0/T+1 markets
- **Currency Exposure**: All positions denominated in EGP with potential FX implications

**Speaker notes:**
Why did we choose the Egyptian Exchange? Because generic AI trading tools built for the US market simply do not work here. The EGX is long-only — you cannot short sell. There are ±10% daily price limits that trigger circuit breakers. Most retail discussion happens in Arabic Facebook groups and Telegram channels that no existing tool monitors. And many EGX stocks have such low volume that entering or exiting a position can move the price. We needed a system built from the ground up for this market.

**Suggested visual:**
Map of Egypt with EGX building, surrounded by constraint icons (no shorts, price limits, Arabic text, etc.)

**Technical details / prompts to mention:**
- Constraints encoded in `tradingagents/default_config.py`: `long_only=True`, `allow_short_selling=False`, `daily_price_limit_pct=0.10`, `max_position_pct_adv=0.10`
- Settlement: T+2 via MCDR (Misr for Central Depository and Registration)
- EGX Trading Constitution (15 clauses) anchors the risk manager — see `risk_manager.py:50-89`

---

## Slide 5: Our Vision

**Main message:**
An AI-powered multi-agent investment assistant that provides explainable, data-driven recommendations.

**Slide bullets:**
- **Not a black-box predictor** — every recommendation comes with full reasoning
- **Not a robo-advisor** — the system recommends, a human decides
- **Multi-agent AI** — mimics an institutional investment team
- **EGX-native** — built for Egyptian market constraints from day one
- **Research prototype** — honest about limitations, designed for continuous improvement

**Speaker notes:**
Our vision is an AI system that works the way a good investment team works. You have a technical analyst reading charts, a fundamental analyst reading balance sheets, a news analyst scanning Arabic and English sources, a social media analyst tracking market sentiment, and a research manager who synthesizes everything into a recommendation. Then a risk manager reviews the recommendation against strict rules before it reaches the user. The key word is "explainable" — the user sees every step of the reasoning, not just a final BUY or SELL signal. This is a decision-support tool, not an automated trader. The human always makes the final call.

**Suggested visual:**
Vision diagram: Data Sources → Multi-Agent Team → Explainable Recommendation → Human Decision

**Technical details / prompts to mention:**
- The system is described in `CLAUDE.md` as "AI-augmented research / decision-support tool"
- The `recommendation_only` field in `InvestorContext` is always `True` — StockHive never executes trades

---

# 2. Problem–Solution Mapping (Slides 6–9)

---

## Slide 6: Problem #1 — Information Overload → Multi-Agent Research Team

**Main message:**
We solve information overload by deploying specialized AI agents that process different data types in parallel.

**Slide bullets:**
- **Problem**: An investor would need to read financial statements, technical charts, news articles, and social media — all before the market opens
- **Solution**: Four specialized analyst agents run simultaneously:
  - Market Analyst (Technical / "Chartist") — RSI, MACD, Bollinger Bands, SMA, momentum, relative strength
  - Fundamentals Analyst (Accountant) — 14 financial ratios, 3-stage Chain-of-Thought pipeline
  - News Analyst (Journalist) — Arabic + English news with transformer-based sentiment
  - Social Media Analyst — Facebook Groups (via Apify), Reddit, Telegram, Mubasher RSS

**Speaker notes:**
The first problem is information overload. A human analyst cannot simultaneously read financial statements, compute technical indicators, scan Arabic Facebook groups, and analyze English-language news. Our system does all of this in parallel using four specialized agents. The Market Analyst computes technical indicators deterministically — no LLM needed for math. The Fundamentals Analyst runs a three-stage Chain-of-Thought pipeline on local financial data. The News Analyst processes both Arabic and English news using FinBERT and CAMeLBERT-DA sentiment models. The Social Media Analyst scrapes live Egyptian financial communities. All four run simultaneously and feed their findings into the next phase.

**Suggested visual:**
Parallel fan-out diagram: START → 4 analysts running simultaneously → Sync barrier

**Technical details / prompts to mention:**
- Parallel fan-out implemented in `graph/setup.py:219` — `workflow.add_edge(START, current_analyst)` for each analyst
- Pre-fetch optimization (`graph/prefetch.py`) runs news + social data fetching before graph execution, saving ~30-60s per analysis
- File references: `market_analyst.py`, `fundamentals_analyst.py`, `news_analyst.py`, `social_media_analyst.py`

---

## Slide 7: Problem #2 — Black-Box AI → Explainable Reasoning

**Main message:**
Every recommendation includes full reasoning, structured debate, and auditability.

**Slide bullets:**
- **Problem**: Most AI systems output a signal with no explanation — users cannot trust what they cannot understand
- **Solution**: Transparent reasoning at every stage:
  - Analyst reports with structured JSON outputs (sentiment scores, confidence levels, key metrics)
  - Bull vs. Bear structured debate with explicit thesis, evidence, and invalidation conditions
  - Research Manager acts as CIO — weighs both sides, commits to a decision with written reasoning
  - Risk Manager evaluates against a 15-clause Trading Constitution, citing clause numbers
  - All LLM prompts request explicit reasoning before conclusions

**Speaker notes:**
The second problem is trust. If an AI tells you to buy a stock but does not explain why, would you risk your money on it? We would not either. Our system produces a full audit trail. The Bull Researcher writes an institutional-grade bullish thesis with specific catalysts, timeframes, and invalidation conditions. The Bear Researcher writes an equally detailed bearish thesis. The Research Manager reads both, weighs the evidence, and commits to a decision — citing which side's arguments were stronger. The Risk Manager then evaluates the trade against a 15-clause EGX Trading Constitution, referencing clause numbers in its response. Every step is visible and reviewable.

**Suggested visual:**
Waterfall diagram: Analysts → Bull Thesis → Bear Thesis → Research Manager Decision → Risk Review → Final Recommendation with reasoning shown at each step

**Technical details / prompts to mention:**
- Bull Researcher prompt requires JSON output with `conviction_level`, `time_horizon`, `signal_summary`, `invalidation_conditions`
- Risk Manager uses Constitutional AI pattern (Bai et al. 2022) with explicit clause references
- Research Manager prompt includes "Decision Consistency Self-Check" — must verify action matches reasoning before output

---

## Slide 8: Problem #3 — Price Prediction Alone Is Not Enough → Position Sizing & Execution Planning

**Main message:**
A BUY signal without execution details is incomplete — the system generates full execution plans.

**Slide bullets:**
- **Problem**: Knowing a stock might go up is not enough — how much to buy? At what price? When to sell?
- **Solution**: The Trader Agent generates detailed execution plans including:
  - Liquidity-aware position sizing (max 10% of ADV per day)
  - Entry logic with limit prices and conditions
  - Multi-target take-profit levels
  - Stop-loss levels (ATR-based preferred, 5% fixed backstop)
  - Time stops and invalidation triggers
  - Risk controls including daily execution limits

**Speaker notes:**
The third pain point is execution. Many prediction tools tell you "BUY" but not how much, at what price, or when to exit. Our Trader Agent generates a full institutional-quality execution plan. It considers the stock's liquidity — how many shares trade daily — and sizes the position so that entering or exiting will not move the market. It specifies entry zones, take-profit targets at multiple levels, stop-loss prices, and time-based exits. And all of this respects EGX constraints: no short selling, no leverage, and awareness of the ±10% daily price limits.

**Suggested visual:**
Sample execution plan JSON (simplified) showing position sizing, entry/exit levels, and risk controls

**Technical details / prompts to mention:**
- Trader prompt at `trader.py:239` requests structured JSON execution plan
- Position sizing respects `max_position_pct_adv=0.10` from config
- Note: This is per-stock analysis, not multi-stock portfolio optimization. Portfolio-level allocation is identified as future work.

---

## Slide 9: Problem #4 — Fragmented Analysis → Unified Agentic Workflow

**Main message:**
Instead of using separate tools for each analysis type, StockHive integrates everything into one coherent workflow.

**Slide bullets:**
- **Problem**: An investor currently needs separate tools for charts, financials, news, and sentiment — results are never integrated
- **Solution**: A single LangGraph workflow that:
  - Runs all four analysts in parallel
  - Feeds results into a structured Bull vs. Bear debate
  - Synthesizes findings through a Research Manager
  - Generates an execution plan through a Trader Agent
  - Validates everything through a three-layer risk pipeline
  - Produces one final recommendation with full audit trail
- All agents communicate via a shared typed state — no direct agent-to-agent coupling

**Speaker notes:**
The fourth problem is fragmentation. Right now, an Egyptian investor might use a charting website for technicals, a financial news site for news, read Facebook groups for sentiment, and check financial statements separately. None of these are connected. Our system runs all of these analyses, feeds them into a structured debate, and produces a single integrated recommendation. The agents do not talk to each other directly — they all write to a shared state that flows through the graph. This means we can add or remove analysts without breaking the system.

**Suggested visual:**
Flow diagram: Fragmented tools (separate boxes, disconnected) vs. StockHive unified pipeline (single connected graph)

**Technical details / prompts to mention:**
- State management via `AgentState` TypedDict in `agents/utils/agent_states.py`
- LangGraph StateGraph with deferred barrier node for parallel analyst synchronization
- Each analyst has isolated message channels to prevent tool-call interference during parallel execution

---

# 3. Solution Overview (Slides 10–12)

---

## Slide 10: Project Overview

**Main message:**
StockHive is a complete investment research system with six major subsystems.

**Slide bullets:**
- **Data Layer**: Yahoo Finance (OHLCV), local CSVs (EGX fundamentals), RSS/NewsAPI (news), Apify (Facebook), Reddit, Telegram
- **Analysis Engine**: 4 specialized analyst agents + deterministic + hybrid LLM pipelines
- **Decision Engine**: Bull/Bear debate → Research Manager → Trader → Risk Pipeline
- **Backend**: FastAPI REST API + WebSocket for real-time streaming
- **Frontend**: React 19 dashboard with TradingView-style charts (lightweight-charts)
- **Monitoring**: Prometheus metrics, structured JSON logging, health probes, alerting rules

**Speaker notes:**
Let me give you a high-level overview of the system. StockHive has six major subsystems. The Data Layer connects to multiple sources — Yahoo Finance for prices, local CSV files for Egyptian fundamentals, multiple news and social media sources. The Analysis Engine deploys four specialized agents. The Decision Engine runs a structured debate and risk review. The Backend serves a FastAPI REST API with WebSocket streaming. The Frontend is a React dashboard with professional charting. And the Monitoring stack tracks everything from LLM call latency to data freshness. Let me walk through each of these.

**Suggested visual:**
Six-block system overview diagram showing the subsystems and their connections

**Technical details / prompts to mention:**
- Entry points: `main.py`, `cli/main.py`, `server/api_server.py`, `scripts/backtester.py`
- ~29 EGX tickers actively covered: banks (COMI, ADIB, etc.), real estate (TMGH, HELI), industry, telecom, financial services, food & beverage

---

## Slide 11: Key Features

**Main message:**
A summary of what StockHive delivers to users.

**Slide bullets:**
- Multi-agent AI analysis with 4 parallel analysts
- Bilingual Arabic + English news and sentiment analysis
- Three-stage Chain-of-Thought fundamental analysis (deterministic → concept → thesis)
- Structured Bull vs. Bear investment debate
- Institutional-grade execution plans with position sizing
- Three-layer risk pipeline (deterministic scoring → LLM debate → constitutional judge)
- Investor profiling (Intraday / Swing / Position) with personalized analysis priority
- Real-time dashboard with WebSocket event streaming
- Backtesting engine with crash recovery and resume capability
- Full observability: Prometheus metrics, structured logs, health probes

**Speaker notes:**
Here are the key features. Each one addresses a specific challenge we identified. The multi-agent architecture tackles information overload. Bilingual analysis addresses the Arabic coverage gap. The three-stage Chain-of-Thought pipeline for fundamentals ensures reasoning depth. The debate structure forces balanced analysis. The risk pipeline prevents bad trades. Investor profiling personalizes the experience. And the monitoring stack ensures we know when something breaks.

**Suggested visual:**
Feature grid with icons — 2 columns × 5 rows, each feature with a small icon and one-line description

**Technical details / prompts to mention:**
- Investor profiling: `InvestorProfilingAgent` classifies users as INTRADAY/SWING/POSITION_6MO based on interview text
- Analysis priority changes per profile: INTRADAY prioritizes Technical, POSITION_6MO prioritizes Fundamental

---

## Slide 12: User Journey and Workflow

**Main message:**
How a user interacts with StockHive from start to finish.

**Slide bullets:**
1. **Onboarding**: User completes investor profile (risk tolerance, investment horizon, capital size, sector preferences)
2. **Stock Selection**: User selects an EGX ticker (e.g., COMI.CA — Commercial International Bank)
3. **Analysis**: System runs the full multi-agent pipeline (2-5 minutes depending on LLM latency)
4. **Real-Time Progress**: Dashboard shows WebSocket events as each agent completes
5. **Results**: User receives:
   - Final recommendation (BUY / HOLD / SELL) with confidence score
   - Full analyst reports (technical, fundamental, news, sentiment)
   - Bull and Bear theses with structured debate
   - Execution plan with entry/exit levels
   - Risk assessment with constitution clause references
6. **Review**: User reviews all reasoning and makes their own decision

**Speaker notes:**
Let me walk through the user journey. First, the user creates an investor profile — conservative, moderate, or aggressive risk tolerance, investment horizon, capital size. Then they select a stock. The system runs the full pipeline — this takes 2 to 5 minutes depending on LLM response times. During execution, the dashboard streams real-time progress events showing which agent is currently working. When complete, the user sees the full picture: recommendation, analyst reports, debate, execution plan, and risk review. The key point is: the user always makes the final decision. We inform, we do not execute.

**Suggested visual:**
Horizontal timeline/journey map: Profile → Select Stock → Pipeline Running (with live events) → Results → Human Decision

**Technical details / prompts to mention:**
- WebSocket streaming via Redis pub/sub (`redis_pubsub.py`) — in-memory ring buffer fallback when Redis is unavailable
- Events include: agent start/complete, analysis progress, final decision
- Dashboard features: `dashboard/src/features/` — home, prediction, investor, monitoring, sessions, backtest, diagnostics, universe

---

# 4. Prediction & Execution Intelligence (Slides 13–17)

---

## Slide 13: Stock Analysis Engine

**Main message:**
The system generates trading recommendations through a structured multi-agent pipeline, not a single prediction model.

**Slide bullets:**
- StockHive does NOT use a single ML model for prediction
- Instead, it orchestrates multiple analysis perspectives and synthesizes them through debate
- The "prediction" is the output of the entire pipeline: a BUY/HOLD/SELL recommendation with confidence and execution plan
- A standalone direct prediction mode also exists (`run_egx_prediction.py`) for quick single-stock analysis
- All predictions carry uncertainty — this is a research tool, not a guaranteed signal

**Speaker notes:**
I want to be clear about what we mean by "prediction." StockHive does not train a neural network on historical prices and output a number. Instead, it orchestrates multiple analysis perspectives — technical, fundamental, news, and sentiment — and synthesizes them through a structured debate. The final recommendation is the product of this entire process. We also have a standalone direct prediction mode that generates a quick LLM-based recommendation with technical indicators. But even this mode is explicit: it asks the LLM to reason about the data, not just predict a price. No prediction system can guarantee accuracy, and we are honest about that.

**Suggested visual:**
Comparison diagram: Traditional ML predictor (data → model → price) vs. StockHive (data → multiple analysts → debate → recommendation with reasoning)

**Technical details / prompts to mention:**
- Direct prediction in `run_egx_prediction.py` — uses `ChatOpenAI` with DeepSeek, temperature=0, seed=42 for reproducibility
- Multi-perspective analysis: the direct prediction prompt asks for BULL_CASE, BEAR_CASE, NEUTRAL_CASE before final SIGNAL
- File: `run_egx_prediction.py:356-403`

---

## Slide 14: Analysis Inputs

**Main message:**
The system consumes multiple data types to build a comprehensive picture.

**Slide bullets:**
- **Historical Market Data**: OHLCV from Yahoo Finance (yfinance) with EGX `.CA` suffix convention
- **Technical Indicators**: RSI(14), MACD, Bollinger Bands, SMA(20/50/200), momentum, relative strength vs EGX30, volume confirmation
- **Fundamental Data**: Local EGX CSV files — income statements, balance sheets, cash flow. 14 core financial ratios computed deterministically.
- **News Sentiment**: RSS feeds (Mubasher), NewsAPI, Google News — Arabic + English. Transformer-based sentiment (FinBERT + CAMeLBERT-DA + VADER baseline)
- **Social Sentiment**: Facebook Groups (Apify actor), Reddit, Telegram public channels. 7-stage pipeline with entity extraction, intent detection, quality gating
- **Macro Context**: CBE policy rate, T-bill yields, EGP/USD rate — used for earnings yield spread analysis

**Speaker notes:**
The system consumes five categories of data. Market data comes from Yahoo Finance. Technical indicators are computed deterministically — RSI, MACD, Bollinger Bands, moving averages, and our own momentum and relative strength measures. Fundamental data comes from local CSV files that we maintain for EGX companies — income statements, balance sheets, cash flows. We compute 14 core financial ratios. News is sourced from Mubasher RSS, NewsAPI, and Google News in both Arabic and English, with sentiment analysis using FinBERT and CAMeLBERT-DA. Social media is scraped from Egyptian Facebook groups, Reddit, and Telegram using a 7-stage pipeline. And we incorporate macro context like the CBE policy rate for earnings yield spread analysis.

**Suggested visual:**
Five input streams flowing into a central "Analysis Engine" — each stream labeled with source names and data types

**Technical details / prompts to mention:**
- Data vendor config in `default_config.py:48-53`: yfinance for core/indicators, local for fundamentals/news
- Sentiment engine router in `utils/sentiment_engine.py` — handles Arabic (MSA + Egyptian dialect) and English
- Social v2 pipeline: `dataflows/social_v2/pipeline.py` — 7 stages: SCRAPE → RELEVANCE → ENRICH → QUALITY GATE → SENTIMENT → AGGREGATE → ARCHIVE
- Entity registry: 84 EGX issuers with Arabic + English aliases in `social_v2/entities.py`

---

## Slide 15: Why Execution Planning Matters

**Main message:**
A recommendation without execution details is incomplete and potentially dangerous.

**Slide bullets:**
- "BUY" without knowing how much to buy = uncontrolled risk
- EGX liquidity constraints mean large orders can move prices
- Without a stop-loss, a small dip can become a catastrophic loss
- Without take-profit targets, investors often hold winners too long or sell too early
- Position sizing relative to ADV prevents market impact
- The system generates complete execution plans, not just signals

**Speaker notes:**
Why do we emphasize execution planning? Because a BUY signal alone can be dangerous. If you buy too much of a low-liquidity EGX stock, your order itself can push the price up — and then you cannot exit without pushing it down. Without a stop-loss, a 10% dip in a stock with daily price limits means you might be locked in for multiple days. Our Trader Agent generates complete execution plans with position sizing, entry zones, multi-level take-profit targets, stop-losses, and time-based exits. This is what institutional traders do, and our system brings that discipline to retail investors.

**Suggested visual:**
Before/After comparison: "BUY" signal alone (risky) vs. Full execution plan with sizing, entries, exits (disciplined)

**Technical details / prompts to mention:**
- Trader prompt at `trader.py:239-327` requests detailed JSON execution plan
- Position sizing constraint: `max_position_pct_adv=0.10` (10% of average daily volume)

---

## Slide 16: Execution Plan Structure

**Main message:**
The Trader Agent outputs a structured, machine-readable execution plan.

**Slide bullets:**
- **Decision**: BUY / HOLD / SELL with conviction level (high / moderate / low)
- **Position Sizing**: Target shares, max shares per day, execution days, % of ADV per day
- **Entry Logic**: Order type (limit/VWAP/TWAP), entry zone with price range, timing conditions
- **Exit Logic**: Multi-target take-profit (3 levels with % of position), stop-loss, time stop
- **Risk Controls**: Max loss per trade, daily execution limit, price-limit risk plan, liquidity exit plan
- **Invalidation Triggers**: Conditions that would cancel the trade thesis

**Speaker notes:**
Here is the structure of an execution plan. The Trader Agent specifies the decision with a conviction level, then breaks down position sizing into daily execution amounts that respect the ADV constraint. Entry logic includes specific order types — no market orders on EGX, only limit, VWAP, or TWAP. Exit logic has three take-profit targets with percentage allocations, plus a stop-loss and a time-based stop. Risk controls include a maximum loss per trade and a plan for what to do if the stock hits the daily ±10% limit. And invalidation triggers tell the investor when to abandon the thesis entirely.

**Suggested visual:**
Annotated JSON execution plan (simplified, key fields highlighted with callout boxes)

**Technical details / prompts to mention:**
- Full execution plan schema at `trader.py:274-325`
- Risk manager validates the plan against 15 constitution clauses
- No market orders on EGX — tick sizes: 0.001 EGP below 2 EGP, 0.01 EGP above (since Sept 2018)

---

## Slide 17: Sample Analysis Output

**Main message:**
Show what the system actually produces.

**Slide bullets:**
- TODO: Insert a real output from a COMI.CA analysis run
- Output includes: recommendation, confidence, analyst reports summary, debate highlights, execution plan, risk assessment
- All outputs are viewable in the dashboard or via the API

**Speaker notes:**
This slide shows a real output from the system analyzing COMI.CA — Commercial International Bank, one of the largest and most liquid stocks on the EGX. You can see the final recommendation with confidence score, summaries from each analyst, the key arguments from the Bull and Bear researchers, the Research Manager's decision rationale, the Trader's execution plan, and the Risk Manager's assessment with constitution clause references. Everything is transparent and reviewable.

**Suggested visual:**
Screenshot of the dashboard showing a completed analysis, or a formatted output from the API

**Technical details / prompts to mention:**
- Run command: `python main.py` for COMI.CA analysis
- API endpoint: `POST /api/analyze` with ticker parameter
- TODO: Capture actual screenshot before presentation

---

# 5. Agentic AI Design (Slides 18–23)

---

## Slide 18: Why Agentic AI?

**Main message:**
Multi-agent AI is the right architecture for complex investment analysis.

**Slide bullets:**
- **Specialization**: Each agent is an expert in one domain — better than one generalist
- **Parallel Processing**: Four analysts run simultaneously — faster than sequential analysis
- **Structured Debate**: Bull vs. Bear framework forces consideration of both sides
- **Separation of Concerns**: Analysis, decision-making, and risk management are independent
- **Extensibility**: New analysts can be added without modifying existing agents
- Inspired by institutional investment teams: Chartist, Accountant, Journalist, CIO, Risk Officer

**Speaker notes:**
Why did we choose a multi-agent architecture? Because investment analysis is inherently multi-disciplinary. No single model can be equally good at reading charts, interpreting financial ratios, processing Arabic news, and assessing social sentiment. By using specialized agents, each one can focus on what it does best. The agents run in parallel for speed, and they communicate only through the shared state — there are no direct imports between agents. This means we can add a new analyst type, like a macroeconomic analyst, without touching any existing code. The debate structure is inspired by how real investment committees work: someone argues the bull case, someone argues the bear case, and a senior decision-maker weighs both.

**Suggested visual:**
Investment team metaphor: Chartist, Accountant, Journalist, Social Analyst → Portfolio Manager → Risk Officer

**Technical details / prompts to mention:**
- Architecture pattern: LangGraph StateGraph with parallel fan-out and deferred barrier synchronization
- Agent communication: via `AgentState` TypedDict — no direct agent-to-agent imports
- Literature: references Constitutional AI (Bai et al. 2022), Reflexion (Shinn et al. 2023), FinCon (Yu et al. 2024)

---

## Slide 19: Multi-Agent Ecosystem Overview

**Main message:**
The complete agent ecosystem and how agents interact.

**Slide bullets:**
- **Layer 1 — Analysts** (parallel):
  - Market Analyst ("Chartist") — technical indicators, deterministic for EGX
  - Fundamentals Analyst ("Accountant") — 3-stage CoT pipeline on financial data
  - News Analyst ("Journalist") — bilingual news sentiment with FinBERT/CAMeLBERT-DA
  - Social Media Analyst — Apify-backed Facebook Groups + Reddit + Telegram
- **Layer 2 — Research**:
  - Bull Researcher — builds bullish thesis from analyst outputs
  - Bear Researcher — builds bearish counter-thesis
  - Research Manager (CIO) — judges debate, makes final investment decision
- **Layer 3 — Execution**:
  - Trader — generates execution plan with position sizing
- **Layer 4 — Risk**:
  - Risk Scorer — deterministic checks (12+ rules), can hard VETO
  - Merged Risk Debate — 3-perspective LLM risk discussion
  - Risk Judge — Constitutional AI critic with 15-clause EGX Constitution

**Speaker notes:**
Here is the complete agent ecosystem. Layer 1 has four analysts running in parallel — they do not know about each other and they do not wait for each other. When all four finish, Layer 2 activates: the Bull Researcher reads all analyst outputs and builds the strongest bullish case. Then the Bear Researcher reads the same outputs plus the Bull's thesis and builds a counter-argument. The Research Manager reads both theses and makes a decision — not a compromise, but a commitment to one side. Layer 3 has the Trader who converts the decision into an actionable execution plan. And Layer 4 is the three-layer risk pipeline: first a deterministic scorer that can hard-veto trades violating hard rules, then an LLM debate among aggressive/conservative/neutral risk perspectives, and finally a Constitutional Risk Judge that evaluates the trade against 15 explicit clauses.

**Suggested visual:**
Four-layer architecture diagram with agents as nodes and arrows showing data flow. Color-code by layer.

**Technical details / prompts to mention:**
- Graph defined in `graph/setup.py:170-269`
- All nodes wrapped with `metered_node()` for Prometheus observability
- Deferred barrier node ("Analysts Sync") at `setup.py:211` ensures all analysts complete before debate starts

---

## Slide 20: News & Sentiment Agent

**Main message:**
Bilingual news analysis with transformer-based sentiment as the primary signal.

**Slide bullets:**
- **Sources**: Mubasher RSS (Arabic + English), NewsAPI, Google News, local CSV archives
- **Language Handling**: Arabic text analyzed natively — not translated. Arabic news often contains critical local market intelligence.
- **Sentiment Models**: FinBERT (English financial), CAMeLBERT-DA (Arabic dialect), XLM-R (cross-lingual), VADER (baseline)
- **Key Design Decision**: Silence (no news) is treated as neutral, not negative — important for sparse EGX mid-cap coverage
- **Output**: Structured JSON with sentiment direction, strength, confidence, key headlines, and language breakdown
- **Pre-fetch Optimization**: News data fetched before graph execution, saving ~30-60 seconds per analysis

**Speaker notes:**
The News Analyst is one of our most important agents because Arabic-language news is a critical signal for EGX stocks that no existing tool captures well. We route text through a sentiment engine that selects the right model: FinBERT for English financial text, CAMeLBERT-DA for Arabic dialect, and XLM-R as a cross-lingual fallback. A key design decision was how to handle silence — when no news is found. For EGX mid-caps, no news is common and does not mean bad news. So we treat it as neutral rather than penalizing it. The output is structured JSON with sentiment direction, confidence score, key headlines with language tags, and factors that affected confidence.

**Suggested visual:**
Pipeline diagram: News Sources (Arabic + English) → Sentiment Engine Router → Structured Analysis → JSON Output

**Technical details / prompts to mention:**
- Representative simplified prompt (from `news_analyst.py:205`):
  > "You are a News & Sentiment Analyst specializing in [market]. Analyze Arabic and English news. SILENCE IS A SIGNAL. End with structured JSON."
- No-news confidence: 35 (cautious default) — see `news_analyst.py:30-45`
- Pre-fetch: `graph/prefetch.py` — data fetched in parallel thread before graph starts

---

## Slide 21: Market Analysis Agent

**Main message:**
Deterministic technical analysis — no LLM needed for math.

**Slide bullets:**
- **For EGX**: Fully deterministic path — technical indicators computed directly, no LLM call
- **Indicators**: RSI(14), MACD(12,26,9), Bollinger Bands(20,2), SMA(20/50/200), ADX, OBV
- **EGX-specific additions**: Momentum labels, relative strength vs EGX30, volume confirmation
- **Data Source**: Yahoo Finance via yfinance with `.CA` ticker suffix
- **Output**: Structured JSON with all indicator values, momentum labels, and technical signals
- **Why deterministic?**: Technical indicators are mathematical formulas — LLM adds no value and introduces potential errors

**Speaker notes:**
The Market Analyst is unique because for EGX stocks, it runs a fully deterministic path — no LLM is involved. Technical indicators like RSI, MACD, and Bollinger Bands are mathematical formulas, and using an LLM to compute them would be both slower and less accurate. We compute all indicators directly using the stockstats library and yfinance data, add our own EGX-specific measures like momentum labeling and relative strength versus the EGX30 index, and output a structured JSON. This approach is faster, more reliable, and fully reproducible.

**Suggested visual:**
Technical chart mockup with indicators overlaid, plus the structured output JSON beside it

**Technical details / prompts to mention:**
- Deterministic market analyst created at `setup.py:97` when `target_market == "EGX"`
- Indicator computation via `stockstats_utils.py`
- For non-EGX markets, the agent uses LLM with tool calls — but EGX path is pure computation

---

## Slide 22: Fundamentals Analysis Agent

**Main message:**
A three-stage Chain-of-Thought pipeline that combines deterministic computation with LLM reasoning.

**Slide bullets:**
- **Stage 1 — Data CoT** (deterministic): Evidence pack assembly — 14 financial ratios, sector-specific scoring, distress flags, signal coherence
- **Stage 2 — Concept CoT** (quick LLM): Scoped interpretation — financial health assessment, key metrics discussion, risk factors, growth signal
- **Stage 3 — Thesis CoT** (deep LLM): H&P (Hypothesis & Prediction) investment thesis — falsifiable hypothesis, evidence for/against, earnings direction prediction
- **Graceful degradation**: If any stage fails, the pipeline falls back gracefully:
  - All stages succeed → `cot_full` (full enrichment)
  - Partial failure → `cot_partial` (reduced confidence)
  - Complete failure → `deterministic_only` (ratios only, no LLM)
- **Quality tracking**: `fundamentals_quality` field records: `full`, `partial`, `deterministic_only`, `unavailable`

**Speaker notes:**
The Fundamentals Analyst is our most sophisticated subsystem. It runs a three-stage Chain-of-Thought pipeline. Stage 1 is fully deterministic — it computes 14 financial ratios, applies sector-specific scoring rules for banks, real estate, and holding companies, and assembles an evidence pack. Stage 2 sends this evidence to a quick LLM for scoped interpretation — what does the data mean? Stage 3 sends everything to a deep LLM for a full investment thesis using the Hypothesis-and-Prediction method, where the LLM must state a falsifiable hypothesis and argue both for and against before reaching a conclusion. If any stage fails, the pipeline degrades gracefully — it always returns results, just with lower confidence. The quality level is tracked as `full`, `partial`, `deterministic_only`, or `unavailable`.

**Suggested visual:**
Three-stage pipeline diagram with graceful degradation branches: Stage 1 → Stage 2 → Stage 3, with fallback arrows

**Technical details / prompts to mention:**
- Pipeline orchestrator: `fundamentals/pipeline.py`
- Quality tracking: `FundamentalsQualityStatus` in `fundamentals/schemas.py`
- Sector-specific configs: banks, real_estate, holdings, operational in `fundamentals/sector_config.py`
- Signal calibration: `fundamentals/calibration.py` — deterministic correction after LLM output
- Hybrid mode enabled by `use_hybrid_fundamental_analyst=True` in config
- Known fix: `_rebuild_graph()` was forcing deterministic mode in backtests even when hybrid was configured. This was fixed so backtests now correctly use hybrid fundamentals.

---

## Slide 23: Social Media Analysis Agent

**Main message:**
Monitoring Egyptian financial communities in Arabic for sentiment signals.

**Slide bullets:**
- **V2 Pipeline** (production-track): 7-stage processing
  1. SCRAPE — multi-source data collection
  2. RELEVANCE — EGX gate (is this about Egyptian stocks?)
  3. ENRICH — entity extraction (84 issuers, Arabic + English aliases), intent detection, content type classification
  4. QUALITY GATE — permissive gate (downgrade vs. hard-drop)
  5. SENTIMENT — project engine + VADER baseline
  6. AGGREGATE — weighted per-stock + market signal
  7. ARCHIVE — Postgres storage
- **Sources**: Facebook Groups via Apify (primary, 5 Arabic groups), Reddit, Telegram, Mubasher RSS
- **Aggregation**: `confidence = 0.6 × quality + 0.4 × size` with minimum post thresholds
- **Honest limitation**: If data is insufficient, returns `NO_SIGNAL` — does not fabricate sentiment

**Speaker notes:**
The Social Media Analyst is particularly important for the EGX because Egyptian retail investors discuss stocks primarily in Arabic Facebook groups and Telegram channels. Our V2 pipeline has seven stages. It scrapes from multiple sources — Facebook Groups via the Apify cloud scraping service, Reddit for intent-rich queries, Telegram public channels, and Mubasher financial news. Each post goes through entity extraction with 84 known EGX issuers and their Arabic aliases, intent detection for BUY/SELL/BULLISH/BEARISH signals, and quality gating. Sentiment is computed using both our project engine and a VADER baseline. The aggregator produces a per-stock and market-level signal. Critically, if we do not have enough data — fewer than 50 total posts or fewer than 5 mentions for a stock — we return NO_SIGNAL rather than fabricating a number.

**Suggested visual:**
7-stage pipeline flow diagram with source icons (Facebook, Reddit, Telegram) → processing stages → output

**Technical details / prompts to mention:**
- Entity registry: `social_v2/entities.py` — 84 EGX issuers with Arabic + English aliases
- Aggregation formula: `weight = entity_conf × content_weight × intent_factor × log(engagement)`
- Thresholds: `MIN_TOTAL_POSTS=50`, `MIN_MENTIONS_PER_STOCK=5`
- Apify actor: `2chN8UQcH1CfxLRNE` for Facebook Groups scraping
- Known gap: entity registry is too narrow for small-cap names — extending it is high-leverage future work

---

# 6. System Architecture (Slides 24–28)

---

## Slide 24: High-Level Architecture Diagram

**Main message:**
The complete system architecture from data sources to user interface.

**Slide bullets:**
- **Data Sources**: Yahoo Finance, Local CSVs, Mubasher RSS, NewsAPI, Facebook (Apify), Reddit, Telegram
- **Core Engine**: LangGraph-orchestrated multi-agent pipeline with parallel fan-out
- **Backend**: FastAPI REST API + WebSocket, Redis pub/sub for event streaming
- **Storage**: ChromaDB (vector memory), PostgreSQL (optional — audit, backtest, agent memories), diskcache (TTL cache)
- **Frontend**: React 19 + Vite + lightweight-charts + Zustand state management
- **Monitoring**: Prometheus + Grafana + Loki + Promtail (Docker Compose stack)

**Speaker notes:**
Here is the complete architecture. Data flows from left to right. On the left, we have our data sources — Yahoo Finance for prices, local CSVs for fundamentals, multiple news and social sources. In the center, the LangGraph engine orchestrates the multi-agent pipeline. The backend is a FastAPI server that exposes REST endpoints for analysis, health, and metrics, plus WebSocket for real-time event streaming. Storage uses ChromaDB for vector-based agent memory with BM25 keyword fallback, PostgreSQL for persistent audit trails and backtest results, and diskcache for TTL-based data caching. The frontend is a React 19 application with professional charting. And on the right, the monitoring stack — Prometheus for metrics, Grafana for dashboards, Loki for log aggregation, all deployable with a single Docker Compose command.

**Suggested visual:**
Full system architecture diagram: Data Sources → Core Engine → Backend/Storage → Frontend + Monitoring (four columns)

**Technical details / prompts to mention:**
- LangGraph StateGraph with `metered_node()` wrappers on every node
- Redis pub/sub with in-memory ring buffer fallback (`redis_pubsub.py`)
- ChromaDB persists to `./chroma_db` (configurable via `CHROMA_PERSIST_DIR`)
- Monitoring stack: `monitoring/docker-compose.yml`

---

## Slide 25: Data Flow Architecture

**Main message:**
How data moves through the system from source to recommendation.

**Slide bullets:**
1. **Pre-fetch Phase**: News + social data fetched in parallel before graph starts
2. **Parallel Analysis**: 4 analysts execute simultaneously with isolated message channels
3. **Synchronization**: Deferred barrier ("Analysts Sync") waits for all analysts to complete
4. **Sequential Debate**: Bull → Bear → Research Manager (linear chain, not cycle)
5. **Execution Planning**: Trader generates structured execution plan
6. **Risk Pipeline**: Risk Scorer → conditional routing → (VETO → END) or (LLM Debate → Risk Judge → END)
7. **Signal Extraction**: Regex-based BUY/SELL/HOLD extraction from final decision (no LLM)

**Speaker notes:**
Let me trace the data flow. Before the graph even starts, a pre-fetch thread collects news and social data in the background — this saves 30 to 60 seconds. Then four analysts run in parallel, each with its own isolated message channel so tool calls do not interfere. A deferred barrier node waits for all analysts to finish before the debate begins. The debate is a strict linear chain: Bull Researcher first, then Bear Researcher, then Research Manager. We specifically chose a linear chain over a cycle because an earlier design with a cycle had a race condition that caused one-sided debates. After the Research Manager decides, the Trader generates an execution plan. Then the three-layer risk pipeline evaluates the trade. If the deterministic Risk Scorer finds a critical violation, it issues a hard VETO and skips the LLM debate entirely. Otherwise, the LLM debate and Risk Judge evaluate qualitative risks.

**Suggested visual:**
Detailed flow diagram matching the architecture from CLAUDE.md: START → Parallel Analysts → Sync → Bull → Bear → Research Manager → Trader → Risk Scorer → (VETO|Debate) → END

**Technical details / prompts to mention:**
- Pre-fetch: `graph/prefetch.py` — parallel thread, saves ~2 LLM calls + 30-60s
- Deferred barrier: `setup.py:211` — `workflow.add_node("Analysts Sync", lambda state: {}, defer=True)`
- Linear debate chain fix: resolved the "one-sided debate" bug (MEMORY §AA) where a race condition in a Bull↔Bear cycle caused Bear's output to be clobbered
- Signal extraction: `graph/signal_processing.py` — regex-based, no LLM

---

## Slide 26: Backend Architecture

**Main message:**
FastAPI server with REST API, WebSocket streaming, and comprehensive endpoints.

**Slide bullets:**
- **Framework**: FastAPI (Python) with Uvicorn ASGI server
- **REST Endpoints**: `/api/analyze`, `/api/health`, `/api/metrics-summary`, `/api/data-freshness`, `/api/events`
- **WebSocket**: `/ws/{session_id}` for real-time analysis progress streaming
- **Health Probes**:
  - `/live` — lightweight liveness (PID, uptime)
  - `/ready` — readiness (LLM key, ChromaDB, EGX tools import check)
- **Middleware**: Prometheus HTTP metrics (request count, latency, status codes)
- **Event Buffer**: In-memory ring buffer (100 events/channel) for replay on page load

**Speaker notes:**
The backend is a FastAPI application. It exposes REST endpoints for triggering analysis, checking system health, and retrieving metrics. The WebSocket endpoint streams real-time events during analysis — when an agent starts, completes, or encounters an error, the dashboard receives it instantly via Redis pub/sub. We have two health probes: `/live` for basic liveness and `/ready` for readiness that checks LLM API key configuration, ChromaDB availability, and EGX tools importability. The middleware automatically tracks HTTP metrics for Prometheus. And an in-memory event buffer stores the last 100 events per channel so that a new client connecting mid-analysis can replay missed events.

**Suggested visual:**
API endpoint diagram showing routes, their purposes, and response types

**Technical details / prompts to mention:**
- Server: `server/api_server.py`
- Prometheus middleware: `observability/middleware.py`
- Event buffer: `redis_pubsub.py` — `MAX_EVENTS_PER_CHANNEL = 100`
- Note: No authentication currently — identified as a production blocker in MEMORY.md

---

## Slide 27: Database Architecture

**Main message:**
Multi-store persistence with graceful fallbacks.

**Slide bullets:**
- **ChromaDB** (default): Vector store for agent memories — cosine similarity search for past similar situations
  - Persistent on-disk storage (`./chroma_db`)
  - BM25 keyword fallback when embeddings are unavailable
  - Minimum similarity threshold: 0.30 (configurable)
- **PostgreSQL** (optional): Structured persistence for:
  - Analysis sessions with full state snapshots
  - Agent events (per-agent, per-analysis granularity)
  - Backtest runs with performance metrics
  - Social media post archive
- **diskcache**: TTL-based caching for data fetches (API responses, scraped data)
- **Local Files**: CSV fundamentals data, backtest JSON results, structured logs

**Speaker notes:**
We use multiple storage backends, each for a different purpose. ChromaDB stores agent memories as vectors — when the system analyzes a stock, it retrieves past experiences with similar stocks or market conditions. If the embedding service is unavailable, we fall back to BM25 keyword search. PostgreSQL is optional but recommended — it stores analysis sessions, agent events, backtest results, and social media posts for audit and analysis. diskcache provides TTL-based caching so we do not re-fetch data that was just fetched. And local CSV files store fundamentals data that we maintain for EGX companies.

**Suggested visual:**
Storage architecture diagram: ChromaDB (memories), PostgreSQL (audit/backtest), diskcache (TTL cache), Local CSVs (fundamentals)

**Technical details / prompts to mention:**
- DB schema: `db_schema.sql` — 4 main tables: `agent_memories`, `analysis_sessions`, `agent_events`, `backtest_runs`
- ChromaDB config: `chroma_persist_dir`, `memory_min_similarity=0.30`
- Memory class: `agents/utils/memory.py` — `FinancialSituationMemory` with temporal filtering (valid_after_date)
- Embeddings: Ollama `nomic-embed-text` (local, free) or OpenAI (configurable via `EMBEDDINGS_BACKEND_URL`)

---

## Slide 28: Monitoring & Observability Architecture

**Main message:**
Production-grade observability to detect failures, track performance, and ensure data quality.

**Slide bullets:**
- **Health Probes**: `/live` (liveness) and `/ready` (readiness with LLM/ChromaDB/tools checks)
- **Structured Logging**: JSON-structured logs with `python-json-logger`, contextvar-based trace context (session_id, ticker, trade_date)
- **Prometheus Metrics** (50+ metrics):
  - LLM: call count, latency histogram, input/output tokens, estimated cost, failover events
  - Pipeline: duration, signal counts (BUY/SELL/HOLD), risk vetoes
  - HTTP: request count by method/endpoint/status, request duration
  - Data: fetch count/latency by source, staleness, missing data
  - Fundamentals: stale tickers, stub files, PE coverage, dividend yield coverage
  - Process: start time (Grafana uptime convention), active sessions, WebSocket connections
- **Alerting Rules** (8 rules, 4 groups): `DataSourceStale`, `LLMHighErrorRate`, `PipelineDurationHigh`, `HighHoldRate`, `RiskVetoSpike`, `APIHighErrorRate`
- **Log Aggregation**: Promtail → Loki pipeline for centralized log search
- **Backtest Audit**: Per-node JSON records with prompt hashes, timing, state snapshots

**Speaker notes:**
Monitoring is essential for a system that depends on external APIs and LLMs. Our observability stack has five layers. First, health probes that Kubernetes or any orchestrator can use. Second, structured JSON logging with trace context so every log line is associated with a session, ticker, and trade date. Third, over 50 Prometheus metrics covering LLM performance, pipeline health, HTTP traffic, data freshness, and fundamentals quality. Fourth, alerting rules that fire when data sources go stale, LLM error rates spike, or the pipeline takes too long. Fifth, Promtail ships logs to Loki for centralized search and analysis. And for backtesting specifically, we record per-node JSON audit trails with prompt hashes and timing data for reproducibility analysis.

**Suggested visual:**
Monitoring architecture: Application → Prometheus (metrics) + Loki (logs) → Grafana (dashboards) + Alerting Rules

**Technical details / prompts to mention:**
- Metrics defined in `observability/metrics.py` — single source of truth, `tradingagents_` prefix
- LLM metrics via `MetricsCallbackHandler` (LangChain callback) — `observability/llm_metrics.py`
- Per-node instrumentation via `metered_node()` wrapper — `observability/node_metrics.py`
- Alerting rules: `monitoring/prometheus/alerts.yml` — 4 groups, 8 rules
- Docker Compose stack: `monitoring/docker-compose.yml` (Prometheus + Grafana + Loki + Promtail)
- Note: Alertmanager notifications (Slack/email) not yet configured — Phase 3 future work

---

# 7. LangGraph Orchestration (Slides 29–31)

---

## Slide 29: Why LangGraph?

**Main message:**
LangGraph provides the graph-based orchestration needed for multi-agent workflows.

**Slide bullets:**
- **StateGraph**: Agents communicate through a typed shared state, not direct calls
- **Parallel Execution**: Multiple agents can run in the same super-step
- **Conditional Routing**: Risk pipeline dynamically routes to VETO or DEBATE paths
- **Tool Integration**: Built-in ToolNode support for agents that call external APIs
- **Deferred Nodes**: Barrier synchronization for parallel fan-out → fan-in patterns
- **Reproducibility**: Deterministic graph structure aids audit and debugging
- Alternative considered: custom orchestrator — rejected due to maintenance burden

**Speaker notes:**
We chose LangGraph because it gives us the graph-based orchestration that a multi-agent system needs. The StateGraph lets agents communicate through a typed shared state without knowing about each other. Parallel execution means our four analysts run simultaneously in the same LangGraph super-step. Conditional routing lets the risk pipeline dynamically skip the LLM debate when a hard veto is needed. Deferred nodes give us the barrier synchronization pattern — all analysts must finish before the debate starts. We considered building a custom orchestrator but decided that LangGraph's battle-tested implementation and integration with LangChain tools was worth the dependency.

**Suggested visual:**
LangGraph StateGraph visual with nodes and edges, highlighting parallel paths, conditional edges, and the deferred barrier

**Technical details / prompts to mention:**
- LangGraph version: `>=0.4.8` (from `pyproject.toml`)
- StateGraph created in `graph/setup.py:171`
- Conditional edges for risk routing: `setup.py:259-266`
- Deferred barrier: `setup.py:211` — `defer=True` parameter

---

## Slide 30: Workflow Graph

**Main message:**
The exact graph structure of the StockHive pipeline.

**Slide bullets:**
```
START ──┬── Market Analyst ──── tools ──── Msg Clear ──┐
        ├── Fundamentals Analyst ── tools ── Msg Clear ─┤
        ├── News Analyst ────── tools ──── Msg Clear ──┤
        └── Social Analyst ──── tools ──── Msg Clear ──┘
                                                        │
                                           Analysts Sync (barrier)
                                                        │
                                              Bull Researcher
                                                        │
                                              Bear Researcher
                                                        │
                                             Research Manager
                                                        │
                                                  Trader
                                                        │
                                               Risk Scorer
                                                  │    │
                                            VETO ─┘    └─ CONTINUE
                                              │              │
                                          Risk Veto    Merged Risk Debate
                                              │              │
                                             END        Risk Judge
                                                             │
                                                            END
```

**Speaker notes:**
Here is the exact graph. From START, all four analysts launch in parallel. Each analyst has its own tool-call loop — the Market Analyst for EGX skips tools entirely and runs deterministically, while others may call external APIs. When all analysts finish, the deferred barrier fires. Then the debate runs linearly: Bull Researcher, then Bear Researcher, then Research Manager. The Trader generates an execution plan. The Risk Scorer runs deterministic checks — if there is a critical violation, it short-circuits to Risk Veto and the trade is rejected without wasting an LLM call. Otherwise, the Merged Risk Debate runs all three risk perspectives in one LLM call, followed by the Risk Judge for final approval.

**Suggested visual:**
Formatted graph diagram matching the ASCII art above, with color coding: green for analysts, blue for debate, yellow for execution, red for risk

**Technical details / prompts to mention:**
- Graph construction: `graph/setup.py:170-269`
- Per-analyst isolated message channels: `PerAnalystToolNode` at `setup.py:15-33`
- Merged Risk Debate eliminates ~85-90% token redundancy vs. three separate debate rounds

---

## Slide 31: Execution Flow

**Main message:**
Step-by-step execution with timing and LLM call breakdown.

**Slide bullets:**
- **Step 1** — Pre-fetch (parallel thread): ~5-10s for news + social data
- **Step 2** — Parallel analysts: ~30-90s (dominated by LLM calls for fundamentals CoT)
- **Step 3** — Bull Researcher: ~10-20s (one LLM call)
- **Step 4** — Bear Researcher: ~10-20s (one LLM call)
- **Step 5** — Research Manager: ~10-20s (one deep LLM call)
- **Step 6** — Trader: ~15-30s (one deep LLM call with complex JSON output)
- **Step 7** — Risk Scorer: <1s (deterministic, no LLM)
- **Step 8** — Merged Risk Debate: ~10-20s (one LLM call)
- **Step 9** — Risk Judge: ~10-20s (one deep LLM call)
- **Total**: ~2-5 minutes per analysis (varies with LLM latency and rate limits)

**Speaker notes:**
Here is the execution flow with approximate timings. The pre-fetch happens in the background while the graph initializes. Analysts run in parallel — the fundamentals CoT pipeline can be the bottleneck if it runs all three stages. The debate and risk pipeline run sequentially, each step taking 10 to 30 seconds depending on LLM response time. Total pipeline time is typically 2 to 5 minutes. The deterministic Risk Scorer takes less than a second — it evaluates all hard rules without an LLM call, and can short-circuit the entire risk debate if a hard veto is warranted.

**Suggested visual:**
Gantt chart or timeline showing parallel and sequential execution with approximate timings

**Technical details / prompts to mention:**
- Pre-fetch saves ~2 LLM calls + 30-60s per trade date
- Hybrid fundamentals adds ~2-3 additional LLM calls (concept_cot + thesis_cot) compared to deterministic-only mode
- LLM: DeepSeek Chat via OpenAI-compatible API — all calls use `temperature=0, seed=42`

---

# 8. Technologies & Implementation (Slides 32–33)

---

## Slide 32: Technology Stack

**Main message:**
The complete technology stack powering StockHive.

**Slide bullets:**
- **Backend**: Python 3.10+, FastAPI, Uvicorn, LangGraph, LangChain
- **LLM**: DeepSeek Chat (OpenAI-compatible API), temperature=0, seed=42
- **NLP/Sentiment**: FinBERT, CAMeLBERT-DA, XLM-R, VADER
- **Embeddings**: Ollama + nomic-embed-text (local), OpenAI (optional)
- **Frontend**: React 19, Vite, TypeScript, Tailwind CSS, Zustand, React Query, lightweight-charts
- **Data Sources**: yfinance, Apify (Facebook), feedparser (RSS), Mubasher scraper
- **Storage**: ChromaDB (vectors), PostgreSQL + pgvector (optional), diskcache, Redis (pub/sub)
- **Monitoring**: Prometheus, Grafana, Loki, Promtail, python-json-logger
- **Testing**: pytest
- **Deployment**: Docker Compose (monitoring stack)
- **LLM Cost**: DeepSeek pricing tracked via `observability/model_pricing.py`

**Speaker notes:**
Here is our technology stack. The backend is Python with FastAPI and LangGraph for orchestration. We use DeepSeek Chat as our LLM — it is an OpenAI-compatible API that offers good performance at lower cost than GPT-4. For NLP, we use specialized models: FinBERT for English financial sentiment, CAMeLBERT-DA for Arabic dialect. Embeddings are generated locally using Ollama with the nomic-embed-text model, so vector memory does not require any paid API. The frontend is React 19 with Vite for fast development, Tailwind for styling, and lightweight-charts for TradingView-style financial charts. Monitoring uses the industry-standard Prometheus/Grafana/Loki stack deployed via Docker Compose.

**Suggested visual:**
Technology stack diagram organized by layer: Frontend / Backend / Data / Storage / Monitoring / LLM

**Technical details / prompts to mention:**
- Full dependency list in `pyproject.toml`
- Frontend packages in `dashboard/package.json`
- DeepSeek endpoint: `https://api.deepseek.com` (configured in `default_config.py:36`)

---

## Slide 33: Implementation Highlights

**Main message:**
Key implementation decisions and engineering practices.

**Slide bullets:**
- **Deterministic where possible**: Technical analysis and risk scoring are pure computation — no LLM waste
- **Graceful degradation**: Every subsystem has fallback chains (e.g., fundamentals: full CoT → partial → deterministic)
- **Data vendor abstraction**: `DataGateway` with cache → primary → fallback chain
- **Reproducibility**: `temperature=0, seed=42` on all LLM calls; deterministic graph structure
- **Crash resilience**: Backtester has retry wrappers, per-ticker checkpoints, and `--resume` capability
- **Modular agents**: Communicate only via shared state — agents can be added/removed without code changes
- **Temporal memory safety**: `valid_after_date` filtering prevents memory look-ahead in backtests
- **Prompt engineering**: Structured output formats (JSON), self-consistency checks, H&P prompting for fundamentals

**Speaker notes:**
Let me highlight some key engineering decisions. We use deterministic computation wherever an LLM would not add value — technical indicators and risk scoring are pure math. Every subsystem has fallback chains — if the fundamentals CoT fails, we still return deterministic ratios. The data layer uses a gateway pattern with caching. All LLM calls use temperature zero and a fixed seed for reproducibility. The backtester can survive crashes and resume from checkpoints. Agents are modular — they do not import each other. And we implemented temporal memory safety so that backtests cannot accidentally use future information from the memory store.

**Suggested visual:**
Highlight cards or icons for each implementation decision

**Technical details / prompts to mention:**
- Data gateway: `dataflows/gateway.py`
- Retry engine: `dataflows/retry_engine.py`
- Temporal memory: `valid_after_date` parameter in `memory.py` — see MEMORY.md temporal safety entry
- Backtester resume: `scripts/backtester.py` with `--resume` flag

---

# 9. Results & Evaluation (Slides 34–37)

---

## Slide 34: System Demonstration

**Main message:**
Demonstrate the system in action.

**Slide bullets:**
- **Live demo** (if connectivity allows): Run analysis on COMI.CA (Commercial International Bank)
- **Dashboard walkthrough**: Home screen → Select stock → Analysis progress → Results view
- **Key screens to show**:
  - Stock selection from EGX ticker universe
  - Real-time WebSocket progress events during analysis
  - Final recommendation with confidence score
  - Analyst reports (Technical, Fundamental, News, Social)
  - Execution plan with entry/exit levels
  - Risk assessment with constitution references
  - Monitoring page with system health and metrics
  - Investor profile configuration

**Speaker notes:**
Let me demonstrate the system. We will analyze COMI.CA — Commercial International Bank, one of the most liquid stocks on the EGX. On the dashboard, we select the ticker and start the analysis. You can see real-time events as each agent completes its work. The final result shows the recommendation with confidence, all analyst reports, the debate highlights, the execution plan, and the risk assessment. Let me also show you the monitoring page — it shows system health, LLM call metrics, and data freshness.

**Suggested visual:**
Dashboard screenshots or live demo

**Technical details / prompts to mention:**
- Command: `python main.py` or use the dashboard at `http://localhost:5173`
- API: `uvicorn server.api_server:app --reload --port 8000`

---

## Slide 35: Case Study — COMI.CA Analysis

**Main message:**
Walk through a real analysis to show the system's reasoning.

**Slide bullets:**
- TODO: Fill with actual COMI.CA analysis output before presentation
- Expected sections:
  - Technical Analysis: RSI, MACD, momentum labels, relative strength vs EGX30
  - Fundamental Analysis: 14 ratios, sector (Banks), financial health assessment, H&P thesis
  - News Sentiment: Arabic + English coverage, sentiment direction and confidence
  - Bull Thesis: catalysts, time horizon, signal alignment
  - Bear Thesis: risks, invalidation conditions
  - Research Manager Decision: which side won and why
  - Execution Plan: position sizing, entry/exit levels
  - Risk Assessment: constitution clause evaluation

**Speaker notes:**
Let me walk through a case study. [Show actual output.] The Technical Analyst computed RSI at [X], with [momentum_label]. The Fundamentals Analyst ran the full three-stage CoT pipeline and assessed the bank as [financial_health] with [earnings_direction]. The News Analyst found [N] articles with [sentiment] sentiment at [confidence]% confidence. The Bull Researcher argued [key bull point]. The Bear Researcher countered with [key bear point]. The Research Manager decided [BUY/HOLD/SELL] because [reasoning]. The Trader generated an execution plan with [target_shares] shares over [N] days. The Risk Manager approved/modified the trade citing constitution clauses [N, M].

**Suggested visual:**
Formatted analysis output with key sections highlighted

**Technical details / prompts to mention:**
- TODO: Run `python main.py` and capture actual output before presentation

---

## Slide 36: Performance Evaluation — Backtesting

**Main message:**
Backtesting validates system performance on historical data, but with honest caveats.

**Slide bullets:**
- **Backtesting engine**: `scripts/backtester.py` — crash-hardened, retry-wrapped, `--resume`-able
- **Look-ahead prevention**: All analyst tools respect `trade_date` — future data is forbidden
  - Resolved issues: `_evaluate_trade_outcomes` deleted, risk-free rate is config-driven, benchmark strictly date-intersected
- **Metrics computed**: Total return, alpha vs. EGX30, Sharpe ratio, Calmar ratio, max drawdown, win rate (realized closed trades only, Wilson CI)
- **Classical baseline**: Backtrader RSI/MACD/BB baseline (`scripts/bt_benchmark.py`) for comparison
- **Multi-ticker evaluation**: `scripts/evaluate_egx_backtests.py` — pooled CSV + Markdown summary vs. EGX30
- **Quality tracking in backtests**: `fundamentals_quality`, `effective_confidence`, pipeline mode recorded per trade date
- **Known fix**: `_rebuild_graph()` was forcing deterministic fundamentals in backtests even when hybrid was configured — this was corrected so backtests now respect the hybrid fundamentals configuration

**Speaker notes:**
We validate the system through backtesting — running historical analyses without any future information. Our backtester is crash-hardened with retry wrappers and can resume from checkpoints if interrupted. All look-ahead issues have been identified and fixed: the system cannot access data that would not have been available on the trade date. We compute standard metrics: total return, alpha versus the EGX30 index, Sharpe ratio, maximum drawdown, and win rate. Win rate is computed only on realized closed trades with a Wilson confidence interval — we do not count open positions as wins. We also run a classical technical-analysis baseline using Backtrader for comparison. An important caveat: backtesting, no matter how carefully done, is not a guarantee of future performance. Market conditions change, and a strategy that worked historically may not work in the future.

**Suggested visual:**
Backtest results table or chart — equity curve, key metrics, comparison vs. EGX30. Include a prominent disclaimer.

**Technical details / prompts to mention:**
- Backtest command: `python scripts/backtester.py --ticker COMI.CA --start 2023-10-01 --end 2024-01-01 --interval 20`
- TODO: Include actual backtest metrics from completed runs (check `backtest_records_*/` directories)
- Disclaimer: "Past performance does not guarantee future results"

---

## Slide 37: Business Impact & Value Proposition

**Main message:**
The system's value is in better-informed decisions, not guaranteed profits.

**Slide bullets:**
- **Time Saved**: Multi-agent parallel analysis completes in 2-5 minutes vs. hours of manual research
- **Coverage Breadth**: Simultaneous analysis of technicals, fundamentals, Arabic news, and social media
- **Risk Discipline**: Three-layer risk pipeline enforces institutional-grade risk management
- **Audit Trail**: Every decision is fully explainable and reviewable
- **Scalability**: Can analyze ~29 tickers (expandable) with consistent methodology
- **Educational Value**: Helps retail investors understand institutional analysis practices
- **Honest Limitations**: Cannot guarantee profits; dependent on LLM quality and data availability

**Speaker notes:**
The value of StockHive is not that it predicts the market — no system can reliably do that. The value is that it saves time, broadens coverage, enforces risk discipline, and makes every decision explainable. A retail investor would need hours to do what our system does in minutes. They would also miss Arabic social media sentiment entirely. And they certainly would not apply a 15-clause risk constitution to every trade. The educational value is also significant — by seeing how a structured analysis works, investors learn to think more systematically about their own decisions.

**Suggested visual:**
Value proposition infographic with icons: Time (clock), Coverage (globe), Risk (shield), Audit (document), Scale (graph)

**Technical details / prompts to mention:**
- ~29 tickers × 4 analysts × parallel execution = consistent, scalable methodology
- Risk constitution: 15 clauses with literature references (Bai 2022, Elder 1993, Tharp 2008, Farag 2013/2015)

---

# 10. Challenges & Lessons Learned (Slides 38–40)

---

## Slide 38: Data Challenges

**Main message:**
Working with EGX data presented unique and significant challenges.

**Slide bullets:**
- **Sparse fundamentals coverage**: Not all EGX companies have machine-readable financial data; ESRS.CA (Ezz Steel) excluded entirely due to empty data across all sources
- **Arabic NLP complexity**: Egyptian dialect differs significantly from MSA (Modern Standard Arabic); requires specialized models (CAMeLBERT-DA)
- **Social media noise**: Facebook groups contain high volumes of non-financial content; our 7-stage pipeline filters extensively but coverage remains limited
- **News gaps**: Many EGX mid-caps have zero English-language news coverage; Arabic RSS feeds are the primary source
- **Data freshness**: Fundamentals data can be stale (>90 days since last filing); we track staleness but cannot force disclosures
- **Look-ahead risk in backtesting**: Historical backtests must avoid using data that would not have been available at the trade date — we identified and fixed multiple surfaces

**Speaker notes:**
Data was our biggest challenge. Not all EGX companies have machine-readable financial data — Ezz Steel, one of the largest industrial companies, had to be excluded entirely because no data source returned usable numbers. Arabic NLP is harder than English because Egyptian dialect differs significantly from Modern Standard Arabic. Social media scraping from Facebook groups is noisy — most posts are not about stocks, and those that are often mention companies by informal names not in our entity registry. Many mid-cap stocks have zero English news coverage. And for backtesting, we had to identify and fix multiple look-ahead surfaces where the system could accidentally use future data.

**Suggested visual:**
Challenge matrix: Data type × Challenge × Status (Fixed / Mitigated / Open)

**Technical details / prompts to mention:**
- ESRS.CA exclusion documented in `default_config.py:12-15`
- Look-ahead fixes documented in MEMORY.md §C — C1/C3/C4 resolved 2026-05-20
- Entity registry gap: `social_v2/entities.py` has 84 issuers but small-caps are underrepresented

---

## Slide 39: AI & Multi-Agent Challenges

**Main message:**
Multi-agent LLM systems introduce unique engineering challenges.

**Slide bullets:**
- **One-sided debates**: A race condition in the Bull↔Bear cycle caused Bear's output to be dropped — fixed by linearizing to Bull → Bear → Research Manager
- **LLM latency and rate limits**: Each analysis uses ~8-12 LLM calls; rate limits can stall the pipeline
- **Reproducibility**: LLM outputs are not perfectly deterministic even with temperature=0 — exact same input can produce slightly different text
- **Cost management**: Hybrid fundamentals adds extra LLM calls; we track estimated costs via Prometheus
- **Prompt engineering iteration**: Getting agents to produce parseable structured JSON required extensive prompt iteration
- **HOLD bias**: Early versions defaulted to HOLD too often; addressed through regime-specific guidance and decision consistency self-checks
- **`_rebuild_graph()` bug**: This function was forcing deterministic fundamentals in backtests even when hybrid mode was configured — caused earlier backtests to not actually use hybrid fundamentals. Fixed.

**Speaker notes:**
Multi-agent LLM systems are hard to engineer correctly. Our most painful bug was a race condition in the debate cycle. The Bull and Bear researchers were supposed to take turns, but the graph's barrier triggered multiple times, launching a second Bull task that ran concurrently with Bear. Both wrote to the same state field, and the reducer dropped one — always Bear's. We fixed it by replacing the cycle with a strict linear chain. LLM latency and rate limits are constant concerns — a single analysis uses 8 to 12 LLM calls, and one slow response can stall everything. Reproducibility is imperfect even with temperature zero. And prompt engineering is an iterative art — getting agents to consistently produce parseable JSON took many rounds of refinement.

**Suggested visual:**
Issue → Root Cause → Fix diagram for the top 3 challenges

**Technical details / prompts to mention:**
- One-sided debate bug: MEMORY.md §AA — resolved by linearizing in `graph/setup.py` and `ablation/runner.py`
- HOLD bias addressed in Research Manager prompt with "Decision Consistency Self-Check" and "Multi-Signal Alignment Rule (P7)"
- `_rebuild_graph()` fix: ensures `use_hybrid_fundamental_analyst` config is respected in backtests

---

## Slide 40: Solutions and Key Lessons Learned

**Main message:**
What we learned and how we addressed challenges.

**Slide bullets:**
- **Lesson 1 — Deterministic > LLM when possible**: Technical analysis and risk scoring should never use LLMs. Math is math.
- **Lesson 2 — Graceful degradation is essential**: Every stage should handle failure without crashing the pipeline
- **Lesson 3 — Structured output formats save debugging time**: Requiring JSON with validation catches errors early
- **Lesson 4 — Audit everything**: Prompt hashes, timing data, and state snapshots make debugging multi-agent systems possible
- **Lesson 5 — Honest about limitations**: Claiming "AI-powered" without acknowledging uncertainty would be misleading
- **Lesson 6 — EGX-specific rules matter**: Generic US market tools would fail on long-only, no-short, ±10% limit constraints
- **Lesson 7 — Monitoring is not optional**: Without observability, you cannot tell if the system is working correctly
- **Lesson 8 — Test with real data early**: Synthetic tests miss real-world data quality issues

**Speaker notes:**
Here are our key lessons. First, use LLMs only where they add value — for computation, deterministic code is faster and more reliable. Second, build fallback chains everywhere — if one stage fails, the system should degrade gracefully, not crash. Third, structured output with validation is essential — parsing free-form LLM text is fragile. Fourth, audit everything — when you have 8 agents producing text, you need prompt hashes and state snapshots to debug issues. Fifth, be honest about what AI can and cannot do. Sixth, market-specific rules are not optional — our EGX Trading Constitution catches many issues that a generic system would miss. Seventh, monitoring is not a nice-to-have — without it, you are flying blind. And eighth, test with real data as early as possible — the gap between synthetic tests and real-world Egyptian financial data is large.

**Suggested visual:**
Numbered lesson list with icons, or a "lessons learned" matrix

**Technical details / prompts to mention:**
- Backtest recording: `backtest_record_outputs` config flag → JSON audit trail per node
- Observability package: `tradingagents/observability/` — 7 modules covering logging, metrics, LLM tracking, node instrumentation

---

# 11. Future Work & Conclusion (Slides 41–43)

---

## Slide 41: Future Enhancements

**Main message:**
Identified improvements for production readiness and expanded capabilities.

**Slide bullets:**
- **Authentication & Authorization**: API currently has no auth — required before any deployment
- **Portfolio-Level Optimization**: Current system is per-stock; multi-stock portfolio allocation (mean-variance, efficient frontier) is not yet implemented
- **Expanded Ticker Universe**: 29 → 100+ tickers with automated data collection
- **Entity Registry Expansion**: Broader Arabic alias coverage for social media entity extraction
- **Alertmanager Notifications**: Prometheus alerting rules exist but notification delivery (Slack/email) is not configured
- **Full Dependency Health Probes**: `/ready` currently checks config only; add actual network probes
- **Persistent Event Log**: Replace in-memory ring buffer with Redis Streams for durable event history
- **Grafana Dashboards**: Pre-built dashboards for LLM metrics, pipeline health, data freshness
- **Distributed Tracing**: OpenTelemetry spans across the multi-agent pipeline
- **Live Order Execution**: Currently recommendation-only; order execution would require FRA regulatory compliance

**Speaker notes:**
We have a clear roadmap for future work. The most critical item is authentication — the API currently has no auth, which is a hard blocker for any real deployment. Portfolio-level optimization is a natural next step — right now the system analyzes one stock at a time, but a real portfolio needs multi-stock allocation optimization. Expanding the ticker universe from 29 to 100+ stocks would cover most of the EGX main market. The monitoring stack needs notification delivery and pre-built Grafana dashboards. And eventually, live order execution could be explored — but that requires regulatory compliance with the Egyptian Financial Regulatory Authority, which is a significant undertaking.

**Suggested visual:**
Roadmap timeline with short-term (1-2 months), medium-term (3-6 months), and long-term (6-12 months) items

**Technical details / prompts to mention:**
- Auth blocker: documented in MEMORY.md
- Portfolio optimization: no scipy/cvxpy code exists — this is genuinely future work, not a partially implemented feature
- Observability Phase 3 items: `agent_docs/observability_roadmap.md:126-141`

---

## Slide 42: Project Summary

**Main message:**
Recap the complete system and its contributions.

**Slide bullets:**
- **Built a complete AI-powered investment assistant** for the Egyptian Stock Exchange
- **Multi-agent architecture** with 4 parallel analysts, structured debate, and 3-layer risk pipeline
- **EGX-native design**: Long-only, ±10% limits, Arabic sentiment, EGP currency, T+2 settlement
- **Explainable AI**: Every recommendation comes with full reasoning, debate, and audit trail
- **Three-stage Chain-of-Thought** fundamental analysis with graceful degradation
- **Production-grade observability**: 50+ Prometheus metrics, structured logging, health probes, alerting rules
- **Honest evaluation**: Backtesting with look-ahead prevention, classical baseline comparison, and clear disclaimers
- **Research prototype**: Honest about limitations — not a deployed trading system, but a strong foundation

**Speaker notes:**
Let me summarize what we have built. StockHive is a complete AI-powered investment assistant for the Egyptian Stock Exchange. It uses a multi-agent architecture with four parallel analysts, a structured Bull vs. Bear debate, and a three-layer risk pipeline anchored to a 15-clause EGX Trading Constitution. It is designed from the ground up for the Egyptian market — long-only constraints, daily price limits, Arabic-dominant sentiment, and EGP-denominated analysis. Every recommendation is fully explainable. The fundamentals pipeline uses a three-stage Chain-of-Thought approach with graceful degradation. The monitoring stack tracks over 50 metrics. And we are honest about what this is — a research prototype that demonstrates the potential of multi-agent AI for investment analysis, not a deployed trading system.

**Suggested visual:**
Summary diagram combining all major components into one cohesive visual

**Technical details / prompts to mention:**
- Total codebase: `tradingagents/` package + `dashboard/` + `server/` + `scripts/` + `monitoring/`
- Key innovation: combining deterministic computation with LLM reasoning in a structured pipeline with constitutional risk management

---

## Slide 43: Q&A

**Main message:**
Open the floor for questions.

**Slide bullets:**
- Thank you for your attention
- Questions are welcome
- Demo available on request

**Speaker notes:**
Thank you for listening to our presentation. We are happy to answer any questions about the system architecture, implementation decisions, evaluation methodology, or future work. We can also run a live demo if you would like to see the system in action.

**Suggested visual:**
Clean Q&A slide with team contact information

**Technical details / prompts to mention:**
- Be prepared for questions about:
  - LLM cost and latency
  - How the system handles Arabic vs. English
  - Why not a traditional ML model?
  - Regulatory compliance (FRA)
  - How to prevent LLM hallucination
  - Backtesting methodology and look-ahead prevention

---

# Appendix: Important Prompt Templates

> The following are representative simplified prompts extracted from the actual codebase.
> Full prompts in the code include additional context injection, error handling, and formatting.
> Secrets, API keys, and credentials have been removed.

---

## A1. Fundamental Analysis — Stage 2: Concept CoT

**Source:** `tradingagents/agents/analysts/fundamentals/concept_cot.py:41`

**Representative simplified prompt:**
```
You are a senior Fundamental Analyst specializing in Egyptian Exchange (EGX) equities.
You have been given a structured evidence pack for one EGX-listed company.

CRITICAL RULES:
1. Only reference numbers and signals that appear in the evidence pack. Do not invent data.
2. Apply sector-specific interpretation: banks have structurally high D/E; real estate PB is understated.
3. EGX-specific context: EGP currency exposure, ±10% daily price limits, no short selling.
4. HIGH-RATE REGIME: When CBE > 15%, negative earnings yield spread is structurally common,
   not company-specific overvaluation.
5. Output ONLY valid JSON.

Output format:
{
  "financial_health": "healthy|concerning|critical|insufficient_data",
  "key_metrics_discussion": "...",
  "risk_factors": ["..."],
  "growth_signal": "positive|neutral|negative",
  "valuation_read": "..."
}
```

---

## A2. Fundamental Analysis — Stage 3: Thesis CoT (H&P)

**Source:** `tradingagents/agents/analysts/fundamentals/thesis_cot.py:36`

**Representative simplified prompt:**
```
You are a senior Portfolio Manager specializing in EGX equities.
Write an institutional-quality investment thesis using the H&P method.

H&P METHOD:
1. HYPOTHESIS: State one falsifiable hypothesis about earnings trajectory
2. EVIDENCE FOR: 2-3 specific data points supporting the hypothesis
3. EVIDENCE AGAINST: 2-3 data points challenging the hypothesis
4. SYNTHESIS: Weigh evidence and arrive at final thesis
5. ASSESSMENT: Fundamental outlook and downside risk
6. PREDICTION: Earnings direction (up/down/flat) with confidence 0-100

CRITICAL: Only cite numbers from the evidence pack. The hypothesis must be
falsifiable. Evidence AGAINST must genuinely challenge, not strawman.

Output: {
  "hypothesis": "...",
  "evidence_for": [...],
  "evidence_against": [...],
  "thesis_text": "...",
  "financial_health": "healthy|concerning|critical",
  "earnings_direction": "up|down|flat",
  "earnings_direction_confidence": 0-100,
  "fundamental_outlook": "bullish|neutral|bearish",
  "downside_risk_level": "low|moderate|high"
}
```

---

## A3. News & Sentiment Agent

**Source:** `tradingagents/agents/analysts/news_analyst.py:205`

**Representative simplified prompt:**
```
You are a News & Sentiment Analyst ("Journalist") specializing in the
Egyptian Exchange (EGX).

Pre-Fetched News Data:
[Company news and market news injected here]

Your Analysis Task:
1. Interpret BOTH Arabic and English text natively
2. Note that SILENCE (no news) REDUCES confidence
3. End with structured JSON:

{
  "sentiment": "bullish|bearish|neutral",
  "sentiment_strength": "strong|moderate|weak",
  "confidence_score": 0-100,
  "explanation": "...",
  "key_headlines": [{"headline": "...", "source": "...", "language": "ar|en", "impact": "..."}],
  "news_coverage": {"total_articles": N, "sources_count": N, "languages": [...]},
  "risks_from_news": [...],
  "catalysts_from_news": [...]
}
```

---

## A4. Market Analysis Agent (Technical / Chartist)

**Source:** `tradingagents/agents/analysts/market_analyst.py:255`

**Representative simplified prompt (non-EGX path):**
```
You are a Technical Analyst ("Chartist") specializing in the Egyptian Exchange (EGX).

[For EGX: Deterministic path — no LLM prompt. Indicators computed directly.]

When LLM is used (non-EGX fallback):
Analyze the stock using technical indicators (RSI, MACD, Bollinger Bands, SMA).
Provide structured analysis with entry/exit signals and confidence levels.
```

> **Note:** For EGX, the Market Analyst runs a fully deterministic path (`create_deterministic_market_analyst()` in `setup.py:97`). Technical indicators are computed mathematically, not by LLM.

---

## A5. Bull Researcher

**Source:** `tradingagents/agents/researchers/bull_researcher.py:178`

**Representative simplified prompt:**
```
You are a Bull Researcher building an institutional-grade investment thesis
advocating for investing in the stock.

EGX Market Context:
- Currency: EGP, Daily price limit: ±10%, No short selling
- Liquidity flag for [ticker]

Your Task — Build a BULLISH thesis by:
1. COMBINING signals from Technical, Fundamental, News analysis
2. Discussing liquidity explicitly
3. Defining clear time horizons
4. Specifying conditions that would INVALIDATE your thesis

Key Points:
1. Signal Agreement: Where do signals ALIGN for the bull case?
2. Catalysts: What specific events could drive the stock higher?
3. Valuation Support: Why is current valuation attractive?
4. Time Horizon: SHORT/MEDIUM/LONG expectations
5. Liquidity Plan: How to build/exit position?
6. Invalidation Conditions: What would make you WRONG?

Output JSON: {thesis_type, conviction_level, time_horizon, signal_summary, ...}
```

---

## A6. Bear Researcher

**Source:** `tradingagents/agents/researchers/bear_researcher.py`

**Representative simplified prompt:**
```
You are a Bear Researcher building an institutional-grade thesis
AGAINST investing in the stock.

[Same structure as Bull Researcher but arguing the bearish case]

Key Points:
1. Signal Disagreement: Where do signals CONFLICT with the bull case?
2. Risks: What specific risks could drive the stock lower?
3. Valuation Concerns: Why might current valuation be stretched?
4. Downside Scenarios: What are the worst-case outcomes?
5. Counter-arguments to bull thesis
6. Conditions that would prove the bear case wrong

Output JSON: {thesis_type: "bearish", conviction_level, risk_assessment, ...}
```

---

## A7. Research Manager (CIO / Investment Judge)

**Source:** `tradingagents/agents/managers/research_manager.py:205`

**Representative simplified prompt:**
```
You are the Chief Investment Officer making the FINAL investment decision.

Decision Framework — MUST commit to one:
- BUY: Bull case is more compelling
- SELL: Bear case is more compelling
- HOLD: ONLY if data is genuinely insufficient OR cases are exactly balanced

CRITICAL: HOLD is a COST. If either side has even a slight edge, choose that side.

Multi-Signal Alignment Rule (P7):
When 2+ independent signals are positive (momentum, RS, volume, valuation,
catalysts, fundamentals), lean BUY unless a concrete stock-specific blocker exists.

Legitimate HOLD Blockers:
- Stale/low-confidence fundamentals (data_confidence < 40%)
- Solvency deterioration
- Extreme valuation without earnings support
- Volume/liquidity trap
- Deteriorating momentum with no catalyst

Decision Consistency Self-Check (MANDATORY):
1. Re-read your reasoning
2. Verify action matches reasoning
3. If reasoning is bullish, action must be BUY, not HOLD

[Bull Thesis] [Bear Thesis] [Analyst Reports]
```

---

## A8. Trader Agent

**Source:** `tradingagents/agents/trader/trader.py:239`

**Representative simplified prompt:**
```
You are an Institutional Trader generating a detailed EXECUTION PLAN.

EGX Constraints:
- Long-only (SELL = exit existing position only)
- No leverage, No market orders
- ±10% daily price limits, T+2 settlement

Your Task:
1. Decision: BUY, HOLD, or SELL
2. Position Sizing: Liquidity-adjusted, max 10% ADV per day
3. Entry Logic: Limit prices, order types, timing conditions
4. Exit Logic: Multi-target take-profit, stop-loss, time stop
5. Risk Controls: Max loss per trade, daily execution limit

Output JSON: {
  execution_plan: {
    symbol, market, currency, decision, conviction,
    position_sizing: {target_shares, max_shares_per_day, ...},
    entry_logic: {order_type, entry_zone, timing, conditions},
    exit_logic: {take_profit: {target_1, target_2, target_3}, stop_loss, time_stop},
    risk_controls: {max_loss, max_daily_execution, price_limit_risk, liquidity_exit_plan},
    invalidation_triggers: [...]
  }
}
```

---

## A9. Risk Manager (Constitutional Critic)

**Source:** `tradingagents/agents/managers/risk_manager.py:50-89`

**Representative simplified prompt (EGX Trading Constitution):**
```
EGX TRADING CONSTITUTION — Evaluate against each clause:

Market Structure Constraints (Regulatory):
1. LONG-ONLY: SELL = exit only. Short selling forbidden (FRA regs).
2. NO LEVERAGE: 100% cash only.
3. PRICE BANDS: ±10% daily limit (List B), ±20% (List A). Halt at ±5%/±10%.
4. SETTLEMENT: T+2 via MCDR.
5. NO MARKET ORDERS: Only limit, VWAP, TWAP.

EGX Microstructure Rules:
6. MAGNET ZONE: Avoid entries within 1.5% of daily band limit.
7. REVERSAL PATTERN: Do NOT close longs during limit-down circuit breaker.
8. CIRCUIT BREAKERS: Do not chase prices during market-wide halt.
9. TICK SIZE: 0.001 EGP (<2 EGP), 0.01 EGP (≥2 EGP).

Position Sizing and Risk Limits:
10. MAX 10% of portfolio in any single stock.
11. STOP-LOSS required (ATR-based preferred, 5% fixed backstop).
12. MAX 10% ADV daily entry (hard veto); 5-10% ADV = throttle zone.
13. MAX 2% of portfolio at risk per trade.

Eligibility:
14. FOREIGN-RESTRICTED: SCEM, SDTI have ownership limits.
15. SELL ELIGIBILITY: Only if portfolio currently holds shares.

Evaluate the proposed trade against EACH clause. Reference clause numbers.
```

---

## A10. Investor Profiling Agent

**Source:** `tradingagents/agents/profiling/investor_profiling_agent.py:69`

**Representative simplified prompt:**
```
You are the Strategic Investment Profiling Agent for the TradingAgents EGX framework.
Classify a retail or institutional investor based on their onboarding interview.

CLASSIFICATION RULES:
INTRADAY — Checks markets multiple times/day, quick gains, high risk
SWING — Checks daily, holds days/weeks, moderate risk
POSITION_6MO — Long-term (6+ months), fundamentals-focused, low risk

Output JSON:
{
  "investor_category": "INTRADAY|SWING|POSITION_6MO",
  "confidence_score": 0.0-1.0,
  "trigger_frequency": "<cron expression>",
  "analysis_priority": ["Technical|Fundamental|Sentiment", ...],
  "reasoning": "..."
}

Analysis Priority by Category:
  INTRADAY → [Technical, Sentiment, Fundamental]
  SWING → [Technical, Fundamental, Sentiment]
  POSITION_6MO → [Fundamental, Sentiment, Technical]
```

---

## A11. Direct Prediction Engine

**Source:** `run_egx_prediction.py:356`

**Representative simplified prompt:**
```
You are a portfolio management team analyzing an EGX stock.
Provide THREE separate analyst perspectives and a final recommendation.

STOCK: [ticker] | MARKET: EGX | CURRENCY: EGP

PRICE DATA: [last 7 days OHLCV]
INDICATORS: Current Price, Daily/Weekly Change, SMA(5/10), RSI(14), Trend, Volume

EGX CONSTRAINTS: Long-only, No leverage, ±10% daily limits

Format:
BULL_CASE: [3-4 bullets]
BEAR_CASE: [3-4 bullets]
NEUTRAL_CASE: [3-4 bullets]
RATIONALE: [synthesis]
SIGNAL: BUY|SELL|HOLD
CONFIDENCE: HIGH|MEDIUM|LOW
TARGET_PRICE: [number] EGP
STOP_LOSS: [number] EGP
RECOMMENDATION: [3-5 sentence investment plan]
```

---

# Evidence Checklist

| Repository File | Slides Supported | What Was Verified |
|---|---|---|
| `CLAUDE.md` | 1-5, 10, 24 | System description, architecture, configuration, ticker universe |
| `tradingagents/default_config.py` | 4, 10, 11, 32, 33 | EGX constraints, LLM config, data vendors, feature flags |
| `tradingagents/graph/setup.py` | 19, 25, 29, 30 | Graph construction, node wiring, parallel fan-out, deferred barrier |
| `tradingagents/graph/trading_graph.py` | 10, 29 | TradingAgentsGraph entry class |
| `tradingagents/agents/analysts/market_analyst.py` | 21 | Deterministic technical analysis, system prompt |
| `tradingagents/agents/analysts/fundamentals_analyst.py` | 22 | Thin wrapper over fundamentals pipeline |
| `tradingagents/agents/analysts/fundamentals/pipeline.py` | 22, 36 | 3-stage CoT pipeline, graceful degradation, quality tracking |
| `tradingagents/agents/analysts/fundamentals/concept_cot.py` | A1 | Stage 2 system prompt |
| `tradingagents/agents/analysts/fundamentals/thesis_cot.py` | A2 | Stage 3 H&P system prompt |
| `tradingagents/agents/analysts/fundamentals/schemas.py` | 22, 36 | FundamentalAnalysisReport, FundamentalsQualityStatus |
| `tradingagents/agents/analysts/fundamentals/sector_config.py` | 22 | Banks, real estate, holdings, operational sector configs |
| `tradingagents/agents/analysts/fundamentals/calibration.py` | 22 | Signal calibration after LLM output |
| `tradingagents/agents/analysts/news_analyst.py` | 6, 14, 20 | Bilingual news analysis, prefetch path, system prompt |
| `tradingagents/agents/analysts/social_media_analyst.py` | 23 | Social media agent prompt |
| `tradingagents/agents/researchers/bull_researcher.py` | 7, 19, A5 | Bull thesis prompt with structured output |
| `tradingagents/agents/researchers/bear_researcher.py` | 7, 19, A6 | Bear thesis prompt |
| `tradingagents/agents/managers/research_manager.py` | 7, 19, A7 | CIO prompt with decision framework, P7 rules, self-check |
| `tradingagents/agents/trader/trader.py` | 8, 15, 16, A8 | Execution plan prompt and JSON schema |
| `tradingagents/agents/managers/risk_manager.py` | 7, 19, A9 | EGX Trading Constitution (15 clauses), constitutional AI pattern |
| `tradingagents/agents/risk_mgmt/risk_scorer.py` | 19, 25 | Deterministic risk scoring, VETO logic, 12+ rules |
| `tradingagents/agents/risk_mgmt/merged_debator.py` | 19 | Merged 3-perspective risk debate |
| `tradingagents/agents/profiling/investor_profiling_agent.py` | 11, 12, A10 | Investor profiling system prompt, classification rules |
| `tradingagents/agents/utils/investor_context.py` | 11, 12 | InvestorContext TypedDict, validation |
| `tradingagents/agents/utils/agent_states.py` | 18, 19, 25 | AgentState TypedDict |
| `tradingagents/agents/utils/memory.py` | 27 | FinancialSituationMemory with temporal filtering |
| `tradingagents/dataflows/social_v2/pipeline.py` | 23 | 7-stage social pipeline |
| `tradingagents/dataflows/social_v2/entities.py` | 23 | 84-issuer SYMBOL_REGISTRY |
| `tradingagents/dataflows/social_v2/aggregator.py` | 23 | Weighted aggregation formula |
| `tradingagents/dataflows/gateway.py` | 33 | DataGateway cache → primary → fallback |
| `tradingagents/utils/sentiment_engine.py` | 14, 20 | FinBERT/CAMeLBERT-DA/XLM-R router |
| `tradingagents/observability/__init__.py` | 28 | Observability package exports |
| `tradingagents/observability/metrics.py` | 28, 32 | 50+ Prometheus metric definitions |
| `tradingagents/observability/llm_metrics.py` | 28 | MetricsCallbackHandler for LLM tracking |
| `tradingagents/observability/node_metrics.py` | 28, 30 | metered_node() wrapper |
| `tradingagents/observability/logging_config.py` | 28 | Structured JSON logging setup |
| `tradingagents/observability/middleware.py` | 26, 28 | Prometheus HTTP middleware |
| `agent_docs/observability_roadmap.md` | 28, 41 | Phase 1 + Phase 2 complete, Phase 3 future work |
| `monitoring/prometheus/alerts.yml` | 28 | 8 alerting rules in 4 groups |
| `monitoring/docker-compose.yml` | 28, 32 | Prometheus + Grafana + Loki + Promtail |
| `server/api_server.py` | 26 | FastAPI endpoints, health probes, WebSocket |
| `redis_pubsub.py` | 12, 26 | Event streaming, in-memory ring buffer |
| `run_egx_prediction.py` | 13, A11 | Direct prediction prompt, multi-perspective analysis |
| `main.py` | 10, 34 | Entry point, COMI.CA analysis |
| `scripts/backtester.py` | 36 | Crash-hardened backtester with resume |
| `scripts/evaluate_egx_backtests.py` | 36 | Multi-ticker evaluation harness |
| `scripts/bt_benchmark.py` | 36 | Classical Backtrader baseline |
| `db_schema.sql` | 27 | PostgreSQL schema: 4 tables |
| `dashboard/package.json` | 32 | React 19, Vite, lightweight-charts, Zustand, Tailwind |
| `dashboard/src/features/` | 12, 34 | 14 feature directories covering full dashboard |
| `pyproject.toml` | 32 | Full Python dependency list |

---

> **Document generated:** 2026-06-23
> **Source of truth:** Repository at `/Users/mennaazazy/stockHive/Graduation-Project/`
> **Disclaimer:** All performance claims require actual backtest data to be filled in. Prompts are representative simplifications of the actual code. This system is a research prototype and does not guarantee investment returns.
