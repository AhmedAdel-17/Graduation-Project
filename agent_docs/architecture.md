# EGX Multi-Agent Stock Prediction System — Full Architecture

> **Last updated:** 2025-05-10
>
> This document describes the complete architecture of the EGX Multi-Agent
> Stock Prediction System, a LangGraph-based research workbench that produces
> analyst-grade BUY / HOLD / SELL theses for Egyptian Exchange (EGX) tickers.
> Every subsystem, threshold, formula, and academic citation is recorded here.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [State Management (AgentState)](#2-state-management)
3. [Graph Orchestration](#3-graph-orchestration)
4. [Analyst Agents (7 agents)](#4-analyst-agents)
   - 4.1 Market Analyst (Technical)
   - 4.2 Fundamentals Analyst (3-Stage CoT)
   - 4.3 News Analyst (Journalist)
   - 4.4 Social Media Analyst (Phase 3)
   - 4.5 Macro Analyst (Phase 2.1)
   - 4.6 Liquidity Analyst (Phase 2.3)
   - 4.7 Regime Analyst (Phase 2.3)
5. [Researcher Debate Subsystem](#5-researcher-debate-subsystem)
6. [Research Manager](#6-research-manager)
7. [Trader Agent](#7-trader-agent)
8. [Three-Layer Risk Management](#8-three-layer-risk-management)
   - 8.1 Deterministic Risk Scorer
   - 8.2 Merged LLM Risk Debate
   - 8.3 Constitutional Risk Manager
9. [Unified Scoring Engine](#9-unified-scoring-engine)
10. [Sentiment Blend Cascade](#10-sentiment-blend-cascade)
11. [Sentiment Engine (NLP)](#11-sentiment-engine)
12. [Data Layer](#12-data-layer)
13. [EGX Trading Constitution (21 Clauses)](#13-egx-trading-constitution)
14. [Academic References](#14-academic-references)

---

## 1. System Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        Entry Points                                     │
│   main.py │ cli/main.py │ server/api_server.py │ scripts/backtester.py  │
└────────────────────────────┬─────────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    TradingAgentsGraph                                    │
│                (tradingagents/graph/trading_graph.py)                    │
│                                                                          │
│  ┌────────────────┐                                                      │
│  │ DataPrefetcher │  Pre-fetches news + social data in parallel          │
│  └───────┬────────┘  (saves ~2 LLM calls + 30-60s per run)              │
│          │                                                               │
│  ┌───────▼──────────────────────────────────────────────────────────┐    │
│  │              ANALYST TEAM  (parallel fan-out)                    │    │
│  │                                                                  │    │
│  │  ┌──────────────┐  ┌────────────────┐  ┌───────────────┐        │    │
│  │  │   Market     │  │ Fundamentals   │  │     News      │        │    │
│  │  │  (Technical) │  │  (3-Stage CoT) │  │  (Journalist) │        │    │
│  │  └──────────────┘  └────────────────┘  └───────────────┘        │    │
│  │  ┌──────────────┐  ┌────────────────┐  ┌───────────────┐        │    │
│  │  │   Social     │  │     Macro      │  │   Liquidity   │        │    │
│  │  │  (Phase 3)   │  │   (Phase 2.1)  │  │  (Phase 2.3)  │        │    │
│  │  └──────────────┘  └────────────────┘  └───────────────┘        │    │
│  │  ┌──────────────┐                                                │    │
│  │  │    Regime    │                                                │    │
│  │  │  (Phase 2.3) │                                                │    │
│  │  └──────────────┘                                                │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│          │                                                               │
│  ┌───────▼──────────────────────────────────────────────────────────┐    │
│  │            RESEARCHER DEBATE  (single-round)                     │    │
│  │                                                                  │    │
│  │  ┌──────────────┐                  ┌───────────────┐             │    │
│  │  │    Bull      │◄────debate────►  │     Bear      │             │    │
│  │  │  Researcher  │                  │   Researcher  │             │    │
│  │  └──────────────┘                  └───────────────┘             │    │
│  │          │                                  │                    │    │
│  │          └──────────┬───────────────────────┘                    │    │
│  │                     ▼                                            │    │
│  │           ┌──────────────────┐                                   │    │
│  │           │ Research Manager │  Judges debate → BUY/SELL/HOLD    │    │
│  │           └──────────────────┘                                   │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│          │                                                               │
│  ┌───────▼──────────────────────────────────────────────────────────┐    │
│  │                    TRADER AGENT                                  │    │
│  │        Generates execution_plan with position sizing             │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│          │                                                               │
│  ┌───────▼──────────────────────────────────────────────────────────┐    │
│  │           THREE-LAYER RISK MANAGEMENT                            │    │
│  │                                                                  │    │
│  │  Layer 1: Deterministic Risk Scorer (hard veto / warn / throttle)│    │
│  │  Layer 2: Merged LLM Risk Debate (aggressive/conservative/       │    │
│  │           neutral perspectives)                                  │    │
│  │  Layer 3: Constitutional Risk Manager (21-clause EGX             │    │
│  │           constitution + final deterministic gate)               │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│          │                                                               │
│  ┌───────▼──────────────────────────────────────────────────────────┐    │
│  │            SignalProcessor                                       │    │
│  │   Regex extraction → final BUY / SELL / HOLD signal              │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│          │                                                               │
│  ┌───────▼──────────────────────────────────────────────────────────┐    │
│  │            Reflector (post-trade memory update)                  │    │
│  │   NOT called inside backtest loop — only live / single-shot      │    │
│  └──────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────┘
```

**Target market:** Egyptian Exchange (EGX). Tickers use `.CA` suffix (Yahoo Finance convention).
**Currency:** EGP. **Settlement:** T+2. **Trading hours:** 10:00–14:30 EGT (UTC+2).
**Regulatory regime:** Egyptian Financial Regulatory Authority (FRA). Long-only, no short-selling, no leverage, ±10% daily price-limit circuit breaker.

---

## 2. State Management

**File:** `tradingagents/agents/utils/agent_states.py`

All agents communicate exclusively through a typed `AgentState` TypedDict. No agent imports another agent directly — this is enforced as an architectural invariant.

### AgentState Fields

| Category | Field | Type | Description |
|---|---|---|---|
| **Metadata** | `company_of_interest` | `str` | EGX ticker (e.g., `COMI.CA`) |
| | `trade_date` | `str` | Analysis date (`YYYY-MM-DD`) |
| | `sender` | `str` | Agent that last wrote |
| | `target_market` | `str` | `"EGX"` |
| | `trading_currency` | `str` | `"EGP"` |
| | `trade_horizon_months` | `int` | Default 6 |
| **Message Channels** | `market_messages` | `List[BaseMessage]` | Market analyst tool calls |
| | `social_messages` | `List[BaseMessage]` | Social analyst tool calls |
| | `news_messages` | `List[BaseMessage]` | News analyst tool calls |
| | `fundamentals_messages` | `List[BaseMessage]` | Fundamentals tool calls |
| | `macro_messages` | `List[BaseMessage]` | Macro analyst messages |
| | `liquidity_messages` | `List[BaseMessage]` | Liquidity analyst messages |
| | `regime_messages` | `List[BaseMessage]` | Regime analyst messages |
| **Text Reports** | `market_report` | `str` | Human-readable market report |
| | `sentiment_report` | `str` | Sentiment report |
| | `news_report` | `str` | News report |
| | `fundamentals_report` | `str` | Fundamentals report |
| | `macro_report` | `str` | Macro report |
| | `liquidity_report` | `str` | Liquidity report |
| | `regime_report` | `str` | Regime report |
| **Structured Analysis** | `technical_analysis` | `Dict` | Indicators, trend, confidence |
| | `fundamental_analysis` | `Dict` | Ratios, health, distress flags |
| | `sentiment_analysis` | `Dict` | Sentiment, strength, confidence |
| | `macro_analysis` | `Dict` | Composite direction, signals |
| | `liquidity_analysis` | `Dict` | Classification, ADV, metrics |
| | `regime_analysis` | `Dict` | Regime enum, vol persistence |
| | `social_sentiment_analysis` | `str` | Phase 3 JSON string |
| **Debate** | `investment_debate_state` | `InvestDebateState` | Bull/bear histories, theses, judge |
| | `risk_debate_state` | `RiskDebateState` | 3-perspective risk histories |
| **Execution & Risk** | `execution_plan` | `Dict` | Position sizing, order type, stops |
| | `risk_assessment` | `Dict` | Risk Manager's analysis |
| | `risk_veto` | `bool` | Hard veto flag |
| | `risk_action` | `str` | `ALLOW` / `WARN` / `THROTTLE` / `VETO` |
| | `risk_metrics` | `Dict` | position_pct, adv_participation, etc. |
| **Portfolio** | `portfolio_value` | `float` | Total portfolio value (EGP) |
| | `current_price` | `float` | Current stock price |
| | `avg_daily_volume` | `float` | 21-day ADV |
| | `low_liquidity` | `bool` | Low-liquidity flag |
| | `volume_missing` | `bool` | Volume data unavailable |
| | `current_position` | `Dict` | {shares, avg_cost, market_value} |
| **Prefetch (Phase 2b)** | `prefetched_company_news` | `str` | Pre-fetched company news |
| | `prefetched_market_news` | `str` | Pre-fetched market news |
| | `prefetched_social_sentiment` | `str` | Pre-fetched social sentiment |
| | `prefetched_social_posts` | `str` | Pre-fetched social posts |
| | `prefetched_stock_datapoints` | `List[Dict]` | StockDataPoint dicts for Layer C |
| **Blend (Phase 3)** | `sentiment_blend_result` | `Dict` | {confidence_multiplier, position_size_multiplier, audit} |

### InvestDebateState

```
bull_history, bear_history: List[BaseMessage]  — debate transcript
bull_thesis, bear_thesis: str                  — structured JSON theses
judge_decision: str                            — Research Manager verdict
count: int                                     — debate round counter
```

### RiskDebateState

```
risky_history, safe_history, neutral_history: List[BaseMessage]
risky_response, safe_response, neutral_response: str
judge_decision: str
count: int
```

---

## 3. Graph Orchestration

**Files:** `tradingagents/graph/trading_graph.py`, `setup.py`, `propagation.py`, `prefetch.py`, `signal_processing.py`

### TradingAgentsGraph

The top-level class that builds and runs the LangGraph `StateGraph`.

**LLM Configuration:**
- `deep_thinking_llm`: Complex reasoning tasks (default: `deepseek-chat`)
- `quick_thinking_llm`: Fast synthesis (default: `deepseek-chat`)
- Both pinned to `temperature=0, seed=42` for reproducibility
- Backend URL: `https://api.deepseek.com` (OpenAI-compatible)
- Failover chain: DeepSeek → OpenAI → Anthropic → Google

**Memory System:**
- 5 persistent memory stores: `bull_memory`, `bear_memory`, `trader_memory`, `invest_judge_memory`, `risk_manager_memory`
- Storage backends: PostgreSQL + pgvector (persistent) → ChromaDB (in-memory fallback)
- Memory is used by researchers and managers to learn from past decisions

### DataPrefetcher (Phase 2b)

**File:** `tradingagents/graph/prefetch.py`

Pre-fetches news and social media data in parallel before the graph starts executing. This eliminates tool-calling round-trips for News and Social analysts, saving approximately:
- ~2 LLM calls
- ~6K tokens
- ~30–60 seconds per trade date

Controlled by `config["prefetch_data"]` (default `True`).

### State Flow

```
1. Entry: AgentState initialized with (company_of_interest, trade_date)
2. DataPrefetcher: parallel news + social fetch (optional)
3. Analyst Fan-Out: all 7 analysts run in parallel
4. Researcher Debate: bull + bear single-round debate
5. Research Manager: judges debate → BUY/SELL/HOLD
6. Trader: generates execution_plan
7. Risk Scorer: deterministic checks → ALLOW/WARN/THROTTLE/VETO
8. Merged Risk Debate: 3-perspective LLM discussion
9. Risk Manager: Constitutional AI final gate
10. SignalProcessor: regex extraction → BUY/SELL/HOLD
11. Reflector: post-trade memory update (live mode only)
```

### SignalProcessor

**File:** `tradingagents/graph/signal_processing.py`

Pure regex extraction — no LLM. Parses the Risk Manager's `final_trade_decision` text to extract the final `BUY`, `SELL`, or `HOLD` signal. This ensures the output is always one of three valid values regardless of LLM verbosity.

---

## 4. Analyst Agents

All analysts are **non-directional-quorum or directional** (see §9). They write to their respective state keys and never import each other.

---

### 4.1 Market Analyst (Technical)

**File:** `tradingagents/agents/analysts/market_analyst.py`
**Mode:** Deterministic (no LLM) via `create_deterministic_market_analyst()`
**Role:** Directional analyst (participates in quorum)

#### Indicators Computed

| Indicator | Parameters | Source |
|---|---|---|
| RSI | 14-day | Wilder (1978) |
| MACD | 12/26/9 | Standard |
| Bollinger Bands | 20 SMA ± 2σ | Bollinger (1992) |
| SMA | 50-day trend | Standard |

#### 6-Month Horizon Factors (Phase 1.2)

| Factor | Formula | Citation |
|---|---|---|
| **Momentum** | `(close[t-21] / close[t-126]) - 1` | Jegadeesh & Titman (1993) — skip most recent 21 days to avoid short-term reversal |
| **Realized Volatility** | `std(log_returns[-63:]) × √252` | Annualized 63-day |
| **Amihud Illiquidity** | `mean(\|r_t\| / volume_t)` over 21 days | Amihud (2002) |
| **Zero-Return Frequency** | Fraction of days with `\|r\| < 1e-8` | Sussex (2023) — outperforms Amihud on EGX |

#### Confidence Computation

```
base_confidence = 0.70
agreement_ratio = 0.50 + (agreement_fraction × 0.40)  → [0.50, 0.90]

Penalties:
  - Low liquidity flag:        -0.20  (LIQUIDITY_CONFIDENCE_PENALTY)
  - Missing volume data:       -0.10  (MISSING_DATA_CONFIDENCE_PENALTY)
  - Data completeness < 80%:   -(1 - completeness) × 0.20
  - Zero-return freq > 30%:    -0.15  (ZERO_RETURN_CONFIDENCE_PENALTY)

Final confidence = clamp(base × agreement_ratio - penalties, 0.10, 1.0)
```

#### Signal Classification

| Signal | Condition |
|---|---|
| `strong buy` | RSI < 30 AND price below lower BB AND MACD histogram positive |
| `buy` | RSI < 45 AND (price near lower BB OR MACD bullish crossover) |
| `sell` | RSI > 55 AND (price near upper BB OR MACD bearish crossover) |
| `strong sell` | RSI > 70 AND price above upper BB AND MACD histogram negative |
| `neutral` | Everything else |

#### Thresholds

```python
LIQUIDITY_CONFIDENCE_PENALTY    = 0.20
MISSING_DATA_CONFIDENCE_PENALTY = 0.10
ZERO_RETURN_CONFIDENCE_PENALTY  = 0.15
ZERO_RETURN_WARNING_THRESHOLD   = 0.30  # > 30% zero-return days
ZERO_RETURN_HARD_VETO           = 0.40  # > 40% → hard veto
```

#### Output State Keys

- `market_report` (str): Human-readable text
- `technical_analysis` (dict): `{signals, trend_direction, confidence_score, invalidation_conditions, horizon_factors}`

---

### 4.2 Fundamentals Analyst (3-Stage CoT Pipeline)

**Files:** `tradingagents/agents/analysts/fundamentals_analyst.py`, `tradingagents/agents/analysts/fundamentals/`
**Mode:** Three variants — Deterministic / Hybrid / Full LLM
**Role:** Directional analyst (participates in quorum)

This is the most mature subsystem (Phase 1A/1B).

#### Three Variants

| Variant | Function | LLM Usage |
|---|---|---|
| **Deterministic** | `create_deterministic_fundamentals_analyst()` | None — Phase 1A only |
| **Hybrid** | `create_hybrid_fundamentals_analyst()` | Phase 1A + 3-stage CoT |
| **Full LLM** | `create_fundamentals_analyst()` | Tool-calling (non-EGX only) |

#### 3-Stage Chain-of-Thought Pipeline

```
Stage 1: data_cot.py (Deterministic)
    → Loads multi-period CSVs
    → Computes all 14 ratios + experimental metrics
    → Builds "evidence pack" JSON
    → No LLM, no network

Stage 2: concept_cot.py (Quick-Think LLM)
    → Receives evidence pack
    → Synthesizes key concepts and patterns
    → Identifies sector-specific concerns
    → Uses quick_thinking_llm (temperature=0, seed=42)

Stage 3: thesis_cot.py (Deep-Think LLM)
    → Receives evidence + concepts
    → Produces structured investment thesis
    → Helfert-Penman (H&P) framework
    → Uses deep_thinking_llm (temperature=0, seed=42)
```

#### 14 Core Financial Ratios

| # | Ratio | Formula | Notes |
|---|---|---|---|
| 1 | ROE | Net Income / Total Equity | |
| 2 | ROA | Net Income / Total Assets | |
| 3 | Gross Margin | Gross Profit / Revenue | |
| 4 | Operating Margin | Operating Income / Revenue | |
| 5 | Net Margin | Net Income / Revenue | |
| 6 | Gross Profit / Assets | Gross Profit / Total Assets | Novy-Marx (2013), Hanauer & Lauterbach (2019) |
| 7 | Debt-to-Equity | Total Liabilities / Total Equity | |
| 8 | Current Ratio | Current Assets / Current Liabilities | |
| 9 | Asset Turnover | Revenue / Total Assets | |
| 10 | Equity Multiplier | Total Assets / Total Equity | |
| 11 | DuPont 3-Factor | Net Margin × Asset Turnover × Equity Multiplier | Identity check vs. computed ROE |
| 12 | EPS | Net Income / Shares Outstanding | |
| 13 | P/E Ratio | Price / EPS | Undefined if EPS ≤ 0 |
| 14 | P/B Ratio | Price / Book Value Per Share | |

#### Experimental Metrics

| Metric | Formula | Notes |
|---|---|---|
| Earnings Yield | 1 / P/E | |
| Earnings Yield Spread | Earnings Yield - Risk-Free Rate | Fed Model variant; `egx_risk_free_rate = 0.275` |
| Dividend Yield | Dividends / Price | |
| Piotroski Score | 7-signal variant | Checks ROA direction, accruals, CFO, leverage, liquidity, GMM, ATM |

#### Sector Configuration

**File:** `tradingagents/agents/analysts/fundamentals/sector_config.py`

Four sector profiles with different ratio interpretations:

| Sector | Special Handling |
|---|---|
| **Banks** | Exclude `current_ratio`, `debt_to_equity` from alerts; D/E 5–10× is structurally normal; flagged with banking context |
| **Real Estate** | P/B < 1× does NOT indicate undervaluation (land at historical cost); project-financing debt is structurally elevated |
| **Holdings** | ROE is consolidated/blended; minority interest affects reported figures |
| **Operational** | No special exclusions; standard thresholds apply |

#### Safety Floors (Distress Flags)

```python
NEGATIVE_MARGIN_ALERT:    net_margin < 0         (universal — all sectors)
HIGH_LEVERAGE_ALERT:      D/E > 5.0              (operational sectors only)
LIQUIDITY_EMERGENCY:      current_ratio < 0.5    (operational sectors only)
NEGATIVE_EQUITY_ALERT:    total_equity ≤ 0        (universal)
PE_UNDEFINED:             EPS ≤ 0                 (universal)
```

#### Data Quality Scoring

**File:** `tradingagents/agents/analysts/fundamentals/scoring.py`

**data_confidence** (0–100):

```
45% × field_coverage          (required fields present)
15% × optional_coverage       (optional fields present)
25% × staleness_score         (max(0, 1 - (periods_since - 1) / 5))
15% × period_depth_score      (periods_available / 8)
```

- Staleness decay: reaches 0 at ≥6 missed periods
- Period depth: normalized to 8 years

**signal_coherence** (0–100): Internal consistency of ratios (e.g., DuPont identity check matches computed ROE, CFO alignment with net income direction).

**financial_health**: `"Strong"` / `"Moderate"` / `"Weak"` — heuristic from YoY trend directions.

#### Output State Keys

- `fundamentals_report` (str): Human-readable text
- `fundamental_analysis` (dict): `{ratios, distress_flags, data_confidence, signal_coherence, financial_health, health_assessment: {profitability, leverage, liquidity}}`

---

### 4.3 News Analyst (Journalist)

**File:** `tradingagents/agents/analysts/news_analyst.py`
**Mode:** LLM-assisted with deterministic confidence adjustments
**Role:** Directional analyst (participates in quorum)

#### Data Sources (Fallback Chain)

1. Pre-fetched data (Phase 2b `DataPrefetcher`)
2. NewsAPI
3. RSS feeds (Al-Mal, Mubasher, Enterprise, Daily News Egypt)
4. Google News
5. Local CSV fallback

#### Confidence Adjustments

```python
NO_NEWS_CONFIDENCE_PENALTY  = 0.40  # No articles found at all
SPARSE_NEWS_PENALTY         = 0.20  # Fewer than 3 articles
SINGLE_SOURCE_PENALTY       = 0.15  # Only 1 news source
```

#### Multilingual Processing

All news text is processed bilingually:
- **English** → FinBERT (ProsusAI/finbert)
- **Arabic** (MSA + Egyptian dialect) → CAMeLBERT-DA
- **Mixed** → XLM-R multilingual

(See §11 Sentiment Engine for full details)

#### Sentiment Blend (News + Transformer)

```
LLM confidence weight:          35%
Transformer confidence weight:  65%  (when available)

Mapping:
  bullish  → +0.6 (strong) / +0.5 (moderate)
  bearish  → -0.6 (strong) / -0.5 (moderate)
  neutral  → 0.0

Final score: clamp(weighted_blend, -1.0, 1.0)
```

#### Output State Keys

- `news_report` (str): Human-readable summary
- `sentiment_analysis` (dict): `{sentiment, confidence_score, key_headlines, news_coverage, transformer_sentiment}`

---

### 4.4 Social Media Analyst (Phase 3)

**File:** `tradingagents/agents/analysts/social_media_analyst.py`
**Mode:** Deterministic pipeline + LLM explainer (LLM does NOT score)
**Role:** Non-directional (modulates confidence and position size only)

#### Architecture

The Phase 3 social analyst uses a **layered pipeline** where the LLM is the explainer, not the scorer:

```
Layer A0: Source Aggregation
    → Apify Facebook (PRIMARY, trusted — bypasses Layer-0 relevance)
    → Reddit targeted queries
    → Telegram public channel previews
    → Mubasher news
    → Twitter (authenticated, Playwright + cookies)
    → Facebook Groups (Playwright fallback)

Layer A: Relevance Filtering
    → Two-signal classifier: must be BOTH financial AND EGX-related
    → Trusted sources (Apify FB) bypass this gate

Layer B: Entity Enrichment
    → Entity extraction (ticker symbols, company names)
    → Intent classification (analysis, rumor, news, opinion)
    → Content type tagging

Layer C: Pre-LLM Quality Gate (deterministic)
    → compute_stock_sentiment() on prefetched StockDataPoints
    → If NO_SIGNAL → skip LLM entirely, return template response
    → Saves LLM calls when social data is insufficient

Layer D: LLM Explanation (quick_thinking_llm)
    → Receives pre-computed numbers
    → Generates human-readable narrative
    → Does NOT modify scores — explainer only

Layer E: Sentiment Blend (blend_sentiment())
    → Applies market regime × macro direction × sector tilt
    → Outputs sentiment_blend_result to state
```

#### Social Aggregation Formula

```
weight     = entity_conf × content_weight × intent_factor × log(engagement + 1)
quality    = clamp(weight_total / n, 0, 1)
size       = n / (n + 5)
confidence = 0.6 × quality + 0.4 × size

Thresholds:
  MIN_TOTAL_POSTS     = 50   (else NO_SIGNAL)
  MIN_MENTIONS_PER_STOCK = 5 (per ticker)
```

#### Output State Keys

- `sentiment_report` (str): Human-readable text
- `social_sentiment_analysis` (str): Full JSON with per-stock breakdown
- `sentiment_blend_result` (dict): `{confidence_multiplier, position_size_multiplier, audit}`

---

### 4.5 Macro Analyst (Phase 2.1)

**File:** `tradingagents/agents/analysts/macro_analyst.py`
**Mode:** Hybrid deterministic + optional LLM commentary
**Role:** Non-directional (modulates via sentiment blend cascade)

#### Data Loading Chain

```
1. CSV primary:  data_cache/egx_macro/egypt_macro.csv
2. FRED API:     fetch_egypt_cpi(), fetch_egypt_tbill() via fred_provider.py
3. Static:       hardcoded fallback values as last resort

Data source tracked in data_sources dict ("csv", "fred", "static")
```

#### Signals Computed

| Signal | Formula / Threshold | Source |
|---|---|---|
| `cbe_policy_rate` | From CSV / static | CBE official rate |
| `real_rate` | `cbe_rate - cpi_yoy` | Fisher equation |
| `real_rate_positive` | `real_rate > 0` | |
| `rate_shock` | `\|cbe_change_bps\| ≥ 200` AND within 14-day window | Time-aware (Phase 5) |
| `tbill_spread` | `tbill_91d - cbe_rate` | |
| `tbill_inversion` | `tbill_spread < -0.02` | Inverted yield signal |
| `fx_change_30d` | `(egp_usd_today - egp_usd_30d_ago) / egp_usd_30d_ago` | yfinance lookup |
| `vix_elevated` | `vix_level > 25` | |
| `vix_extreme` | `vix_level > 35` | |
| `inflation_high` | `cpi_yoy > 0.20` | |
| `inflation_extreme` | `cpi_yoy > 0.30` | |
| `fx_premium_pct` | From fx_premium.csv | Parallel market premium |
| `fx_premium_signal` | `NORMAL` / `ELEVATED` / `CRITICAL` | See below |
| `composite_direction` | `RISK_ON` / `RISK_OFF` / `NEUTRAL` | Aggregate of all signals |

#### Rate Shock Recency Window (Phase 5)

```python
RATE_SHOCK_RECENCY_DAYS = 14

magnitude_ok = abs(last_change_bps) >= 200
recency_ok = 0 <= (trade_date - change_date).days <= RATE_SHOCK_RECENCY_DAYS

rate_shock = magnitude_ok AND recency_ok
```

A CBE rate change of ≥200 bps that happened more than 14 days before the trade date does NOT trigger `rate_shock`. This prevents stale historical rate changes from permanently affecting backtest signals.

#### FX Premium (Phase 5)

**File:** `data_cache/egx_macro/fx_premium.csv`
**Citation:** Harvard/Oki (2023) — strongest leading devaluation indicator

```python
def _load_fx_premium(trade_date):
    # Loads from CSV, applies data_quality guard
    # stub/demo/provisional data → ALWAYS maps to NORMAL
    # Only "real" or "verified" data can trigger ELEVATED/CRITICAL

    if data_quality not in ("real", "verified"):
        return {"fx_premium_signal": "NORMAL", ...}  # Guard

    if premium_pct > 25:
        signal = "CRITICAL"
    elif premium_pct > 10:
        signal = "ELEVATED"
    else:
        signal = "NORMAL"
```

#### Composite Direction Logic

```
risk_off_count, risk_on_count = 0, 0

rate_shock           → risk_off += 2
inflation_extreme    → risk_off += 2
vix_extreme          → risk_off += 1
tbill_inversion      → risk_off += 1
inflation_high       → risk_off += 1
vix_elevated         → risk_off += 1
real_rate_positive   → risk_on += 1
not inflation_high   → risk_on += 1

if risk_off >= 3:     composite = "RISK_OFF"
elif risk_on >= 2:    composite = "RISK_ON"
else:                 composite = "NEUTRAL"
```

#### Output State Keys

- `macro_report` (str): Human-readable text
- `macro_analysis` (dict): `{composite_direction, rate_shock, real_rate, fx_change_30d, fx_premium_pct, fx_premium_signal, cbe_last_change_bps, cbe_last_change_date, data_sources, ...}`

---

### 4.6 Liquidity Analyst (Phase 2.3)

**File:** `tradingagents/agents/analysts/liquidity_analyst.py`
**Mode:** Fully deterministic — no LLM calls
**Role:** Non-directional (modifies confidence and position size via risk checks)

#### Metrics Computed

| Metric | Formula | Citation |
|---|---|---|
| **Amihud ILLIQ (21d)** | `mean(\|r_t\| / volume_t)` | Amihud (2002) |
| **Zero-Return Frequency (21d)** | Fraction of days with `\|return\| < 1e-8` | BHL (2007), Sussex/African Markets (2023) |
| **ADV (21d)** | Average daily volume over 21 trading days | Standard |
| **ADV Trend (5d/21d)** | `ADV_5d / ADV_21d` — >1.0 = increasing | Standard |
| **Volume Concentration** | Fraction of volume from top 20% of days | >0.60 = episodic liquidity |
| **HL Spread Proxy (21d)** | `mean((high - low) / midpoint)` | Corwin & Schultz (2012) |
| **Days to Exit** | `ceil(hypothetical_shares / daily_capacity)` for 1M EGP position | |

#### Classification Thresholds

```python
ZERO_RETURN_HIGH_THRESHOLD      = 0.30   # > 30% → SEVERELY_ILLIQUID
ZERO_RETURN_MODERATE_THRESHOLD  = 0.15   # > 15% → concern count +1
ADV_LOW_THRESHOLD               = 50,000  # < 50K shares → concern +1
ADV_VERY_LOW_THRESHOLD          = 10,000  # < 10K shares → SEVERELY_ILLIQUID
VOLUME_CONCENTRATION_HIGH       = 0.60   # Top 20% days > 60% volume → concern +1
ILLIQ_HIGH_THRESHOLD            = 1e-5   # Amihud above this → concern +1
```

#### Classification Rules

```
SEVERELY_ILLIQUID:  zero_return ≥ 0.30 OR ADV < 10K
ILLIQUID:           concern_count ≥ 2
MODERATE_CONCERN:   concern_count ≥ 1
ADEQUATE:           concern_count = 0
```

#### FPI Foreign Flow Integration (Phase 5)

**File:** `data_cache/egx_macro/foreign_flow.csv`

```python
def _load_fpi_flow(ticker, trade_date):
    # Loads from CSV
    # data_quality must be "real" or "verified" for decision impact
    # "stub", "demo", "provisional" → reported but does NOT escalate

    Signals:
      STRONG_INFLOW:   net > +50M EGP
      INFLOW:          net > +10M
      NEUTRAL:         -10M to +10M
      OUTFLOW:         net < -10M
      STRONG_OUTFLOW:  net < -50M
```

**Escalation Guard:** Only verified STRONG_OUTFLOW escalates classification by one tier:

```
ADEQUATE        → MODERATE_CONCERN
MODERATE_CONCERN → ILLIQUID
ILLIQUID         → SEVERELY_ILLIQUID
```

Stub data is reported for visibility but never affects the classification.

#### Output State Keys

- `liquidity_report` (str): Human-readable text with ADV, zero-return, days-to-exit
- `liquidity_analysis` (dict): `{liquidity_classification, amihud_illiq_21d, zero_return_frequency_21d, avg_daily_volume_21d, adv_trend_5d_vs_21d, volume_concentration_top20pct, hl_spread_proxy_21d, max_shares_at_10pct_adv, days_to_exit_1m_egp, fpi_net_flow_egp, fpi_signal, fpi_data_quality, data_bars_available}`

---

### 4.7 Regime Analyst (Phase 2.3)

**File:** `tradingagents/agents/analysts/regime_analyst.py`
**Mode:** Fully deterministic — rule-based (HMM was considered and rejected)
**Role:** Non-directional (modulates via sentiment blend cascade)

#### Regime Classification

| Regime | Condition |
|---|---|
| `PANIC` | Extreme drawdown + volume spike + high volatility |
| `FEAR` | Elevated volatility + negative momentum |
| `NEUTRAL` | Normal conditions |
| `GREED` | Strong positive momentum + low volatility |
| `EUPHORIA` | Excessive gains + potential bubble signals |
| `NO_SIGNAL` | Insufficient data |

#### Key Signals

| Signal | Description | Citation |
|---|---|---|
| **Volatility Persistence** | Measures whether volatility clusters persist | Ezzat (2013) — Joseph Effect |
| **Drawdown** | Maximum drawdown from recent peak | Standard |
| **Volume Anomaly** | Volume spike relative to ADV | Standard |
| **Momentum** | Price momentum over trailing window | Standard |

#### Output State Keys

- `regime_report` (str): Human-readable text
- `regime_analysis` (dict): `{market_regime_enum, vol_persistence_flag, drawdown, volatility_state, ...}`

---

## 5. Researcher Debate Subsystem

**Files:** `tradingagents/agents/researchers/bull_researcher.py`, `bear_researcher.py`

### Bull Researcher

Combines all analyst signals into a **bullish investment thesis**. The thesis is structured JSON containing:

- Investment summary
- Key bullish catalysts
- Time horizon justification (6-month default)
- EGX-specific liquidity analysis
- Invalidation conditions (what would flip the thesis)
- Position sizing recommendations

The Bull Researcher receives all analyst reports and structured analyses. It explicitly discusses EGX liquidity constraints and incorporates macro/regime context.

### Bear Researcher

Builds a **bearish thesis** with a critical distinction: on EGX, "bearish" does NOT mean "short sell." Bear recommendations use:
- **AVOID** — do not enter a new position
- **REDUCE** — trim existing position
- **UNDERWEIGHT** — allocate less than benchmark weight

The Bear Researcher focuses on:
- Capital preservation risks
- Exit execution risk for low-liquidity stocks
- Macro headwinds and regulatory risks
- Downside scenarios

### Sentiment Guard

Both researchers receive sentiment data through `_format_sentiment_section()` which checks for `NO_SIGNAL` and injects an `"EXCLUDED"` guard — ensuring researchers don't weigh zero-confidence sentiment data.

### Debate Format

Single-round debate (configurable via `max_debate_rounds`, default 1). Both researchers produce structured JSON theses that are passed to the Research Manager for adjudication.

---

## 6. Research Manager

**File:** `tradingagents/agents/managers/research_manager.py`

Receives structured `bull_thesis` and `bear_thesis` (preferred) or falls back to debate history.

### Decision Framework

The Research Manager operates under a specific decision philosophy:

```
BUY:  More compelling even on moderate evidence — asymmetric upside
SELL: On moderate bearish evidence — capital preservation priority
HOLD: ONLY valid when:
      1. Data is genuinely insufficient for any thesis, OR
      2. Arguments are perfectly balanced AND risk/reward is unfavorable
```

**Critical rule:** *"HOLD costs opportunity; indecision is worse than a small mistake."*

### Output

- Updates `investment_debate_state.judge_decision` with structured investment plan
- Decision contains: direction (BUY/SELL/HOLD), conviction level, key reasoning, risk factors

---

## 7. Trader Agent

**File:** `tradingagents/agents/trader/trader.py`

Generates a detailed `execution_plan` based on the Research Manager's decision.

### Position Sizing

```python
max_shares_per_day = ADV × participation_rate
    participation_rate = 10% (normal) or 5% (low liquidity)

max_shares_total = portfolio_value × concentration_limit / current_price
    concentration_limit = 100% (backtest_mode) or 10% (live)

days_to_accumulate = max_shares_total / max_shares_per_day
```

### Order Constraints

```python
ALLOWED_ORDER_TYPES    = ["limit", "limit_ioc", "vwap", "twap"]
FORBIDDEN_ORDER_TYPES  = ["market", "market_on_close", "stop_market"]
MAX_POSITION_PCT_ADV   = 0.10   # 10% of ADV
MAX_PORTFOLIO_SINGLE   = 0.10   # 10% of portfolio (UCITS Art. 52)
DAILY_PRICE_LIMIT      = 0.10   # ±10% EGX circuit breaker
```

### Output State Keys

- `execution_plan` (dict): `{position_sizing: {target_shares, max_shares}, entry_logic, exit_logic: {stop_loss, take_profit}, order_type, execution_window, rationale}`

---

## 8. Three-Layer Risk Management

The risk management system uses three sequential layers, each progressively more nuanced. A hard veto at any layer stops the trade.

```
Layer 1: Deterministic Risk Scorer
    → Hard rules, no LLM, instant
    → Output: ALLOW / WARN / THROTTLE / VETO

Layer 2: Merged LLM Risk Debate
    → Three LLM perspectives discuss the trade
    → Qualitative risk assessment

Layer 3: Constitutional Risk Manager
    → 21-clause EGX constitution
    → Constitutional AI pattern (Bai et al. 2022)
    → Final deterministic gate
```

---

### 8.1 Deterministic Risk Scorer (Layer 1)

**File:** `tradingagents/agents/risk_mgmt/risk_scorer.py`
**Citation:** Alshiekh et al. (AAAI 2018) — Safe RL via Shielding (hard-rule pre-check)

#### EGX Risk Limits

```python
EGX_RISK_LIMITS = {
    # Portfolio concentration (UCITS Art. 52)
    "max_single_stock_pct":     0.10,    # 10% max per name
    "max_sector_pct":           0.30,    # 30% max per sector
    "max_correlated_exposure":  0.40,    # 40% max correlated group

    # Liquidity (two-tier: Almgren & Chriss 2000)
    "min_avg_daily_volume":     50_000,  # shares/day minimum
    "max_days_to_exit":         10,      # Almgren-Chriss "characteristic time"
    "low_liquidity_reduction":  0.50,    # halve capacity for illiquid names
    "adv_throttle_pct":         0.05,    # 5% ADV → WARN + THROTTLE
    "adv_hard_veto_pct":        0.10,    # 10% ADV → VETO

    # Drawdown / loss limits (Elder 1993, 2014)
    "max_single_trade_loss_pct": 0.02,   # Elder 2% rule — CRITICAL
    "max_daily_loss_pct":        0.05,   # 5% daily
    "max_weekly_loss_pct":       0.10,   # 10% weekly

    # EGX market structure
    "daily_price_limit":        0.10,    # ±10% circuit breaker
    "magnet_zone_pct":          0.015,   # 1.5% inside band (Farag 2013)
    "no_short_selling":         True,
    "no_leverage":              True,

    # ATR stops (Wilder 1978, Kaminski & Lo 2014)
    "atr_stop_multiplier":      2.0,     # 2×ATR(14) primary stop
    "atr_stop_max_pct":         0.07,    # cap at 7% (inside magnet zone)
    "fallback_stop_pct":        0.05,    # fixed 5% only when ATR unavailable
}
```

#### Checks Performed

| Check | Condition | Action |
|---|---|---|
| **Short Selling** | Pattern match for short-selling language in execution plan | VETO |
| **Leverage** | Pattern match for margin/leverage language | VETO |
| **Position Size** | > `max_single_stock_pct` of portfolio | VETO (live) / WARN (backtest) |
| **ADV Participation** | > 10% of ADV | VETO |
| **ADV Throttle** | > 5% of ADV | THROTTLE (auto-reduce to 5%) |
| **Per-Trade Loss** | > 2% of portfolio at risk | VETO (Elder 2% rule) |
| **Stop Distance** | Stop > 7% from entry | WARN |
| **FX Stress** | 30-day FX move > 20% | VETO on new entries |
| **Zero-Return** | > 40% zero-return days | VETO (price discovery broken) |
| **Magnet Zone** | Price within 1.5% of daily limit | WARN (Farag 2013) |

#### FX Stress Check (Phase 5)

```python
# Uses macro_analysis.fx_change_30d when available
fx_change_30d = macro_analysis.get("fx_change_30d")
if fx_change_30d is not None:
    move_pct = abs(fx_change_30d)
else:
    # Fallback: static reference comparison
    reference = 48.5
    move_pct = abs(egp_usd - reference) / reference

if move_pct >= 0.20:
    return VETO  # 20% FX dislocation
```

---

### 8.2 Merged LLM Risk Debate (Layer 2)

**File:** `tradingagents/agents/risk_mgmt/merged_debator.py`

Three LLM perspectives discuss the trade qualitatively:

| Perspective | Bias | Focus |
|---|---|---|
| **Aggressive** | Risk-seeking | Opportunity cost, alpha potential |
| **Conservative** | Risk-averse | Downside protection, tail risks |
| **Neutral** | Balanced | Risk-reward ratio, sizing appropriateness |

Each perspective receives the full analyst output, execution plan, and risk scorer results. The debate produces a merged risk assessment that informs the final Risk Manager.

---

### 8.3 Constitutional Risk Manager (Layer 3)

**File:** `tradingagents/agents/managers/risk_manager.py`
**Citation:** Bai et al. (2022) — Constitutional AI

The Risk Manager evaluates the trade against the 21-clause EGX Trading Constitution (see §13). It operates as a Constitutional AI critic:

1. **Constitution Check:** LLM evaluates trade against each applicable clause
2. **Qualitative Risk Assessment:** LLM identifies risks not captured by deterministic rules
3. **Final Deterministic Gate:** Catches cases where LLM contradicts hard facts:
   - SELL with no existing position
   - BUY on foreign-restricted tickers
   - Position exceeding regulatory limits

#### Output State Keys

- `final_trade_decision` (str): The final approved/vetoed trade decision
- `risk_veto` (bool): Whether the trade was vetoed
- `risk_action` (str): `ALLOW` / `WARN` / `THROTTLE` / `VETO`

---

## 9. Unified Scoring Engine

**File:** `tradingagents/agents/utils/scoring.py`

### Agent Weights

```python
agent_weights = {
    "technical":    0.40,
    "fundamental":  0.30,
    "news":         0.15,
    "social":       0.15,
}
```

### Decision Thresholds

The confidence-weighted aggregate score ranges from -1.0 to +1.0:

```
STRONG_BUY:   score ≥ +0.60
BUY:          score ≥ +0.20
HOLD:         -0.20 < score < +0.20
SELL:         score ≤ -0.20
STRONG_SELL:  score ≤ -0.60
```

### Quorum Rule

**Minimum 2 non-absent directional analysts** required for a valid decision. If fewer than 2 analysts provide directional signals, the system returns `HOLD` with low confidence and a status flag.

Directional analysts: Market (Technical), Fundamentals, News, Social
Non-directional analysts: Macro, Liquidity, Regime (these modulate but don't vote)

### Key Principle

> **Sentiment NEVER flips direction — it only modulates confidence and position size.**

A strong BUY from all directional analysts + PANIC regime + RISK_OFF macro = still BUY, but with reduced confidence and smaller position size.

---

## 10. Sentiment Blend Cascade

**File:** `tradingagents/agents/utils/scoring.py`

The blend cascade applies multiplicative modifiers from three sources. Each layer reduces (or occasionally increases) confidence and position size without changing the directional decision.

### Layer 1: Social Sentiment Blend (from Phase 3 Social Analyst)

Applied first if `sentiment_blend_result` exists in state:

```
blended_conf × sentiment_blend.confidence_multiplier
pos_size_mult × sentiment_blend.position_size_multiplier
```

### Layer 2: Macro/Regime Blend (Phase 5 fix)

Applied AFTER social blend (not instead of — this was a bug fixed in Phase 5):

#### Market Regime Multipliers

| Regime | Confidence × | Position Size × |
|---|---|---|
| PANIC | 0.70 | 0.50 |
| FEAR | 0.85 | 0.75 |
| NEUTRAL | 1.00 | 1.00 |
| GREED | 0.90 | 0.90 |
| EUPHORIA | 0.70 | 0.60 |
| NO_SIGNAL | 1.00 (pass-through) | 1.00 |

#### Macro Direction Multipliers

| Direction | Confidence × | Position Size × |
|---|---|---|
| RISK_OFF | 0.80 | 1.00 |
| RISK_ON | 0.95 | 1.00 |
| NEUTRAL | 1.00 | 1.00 |
| NO_SIGNAL | 1.00 (pass-through) | 1.00 |

### Combined Example

```
Prior social blend:  conf × 0.90, size × 0.85
RISK_OFF:            conf × 0.80
FEAR:                conf × 0.85, size × 0.75

Combined conf:  0.90 × 0.80 × 0.85 = 0.612
Combined size:  0.85 × 0.75         = 0.6375
```

### Layer 3: Liquidity Confidence Haircut

Applied post-blend, separate from the sentiment cascade:

```
If liquidity_classification in ("ILLIQUID", "SEVERELY_ILLIQUID"):
    confidence -= 0.15
    position_size_multiplier × 0.50  (low_liquidity_reduction)
```

---

## 11. Sentiment Engine

**File:** `tradingagents/utils/sentiment_engine.py`

### Multilingual Transformer Routing

| Language | Model | Reference |
|---|---|---|
| **English** | FinBERT (`ProsusAI/finbert`) | Araci (2019) |
| **Arabic** (MSA + Egyptian dialect) | CAMeLBERT-DA (`CAMeL-Lab/bert-base-arabic-camelbert-da-sentiment`) | Inoue et al. (2021) |
| **Mixed / code-switch** | XLM-R (`cardiffnlp/twitter-xlm-roberta-base-sentiment-multilingual`) | Conneau et al. (2020) |

### Arabic Text Preprocessing

**File:** `tradingagents/utils/text_preprocessor.py`

- Hamza normalization (أ/إ/آ → ا)
- Diacritics removal (tashkeel stripping)
- Egyptian dialect handling
- Elongation reduction (e.g., "جميييل" → "جميل")

### Label Normalization

```
positive → bullish  (score: +1.0)
negative → bearish  (score: -1.0)
neutral  → neutral  (score:  0.0)
```

### Aggregation

Average confidence across all processed texts. Final score clamped to `[-1.0, 1.0]`.

### Fallback

Rule-based lexicon matching when transformer models fail to load. Maximum confidence capped at `0.45` for rule-based results to signal lower reliability.

---

## 12. Data Layer

### DataGateway

**File:** `tradingagents/dataflows/gateway.py`

Central orchestrator for all data access:

```
1. Check cache (diskcache TTL store)
2. Try primary provider
3. Try fallback chain
4. Validate via Pydantic schemas
5. Cache result
```

### Provider Fallback Chains

| Data Type | Primary | Fallback 1 | Fallback 2 |
|---|---|---|---|
| **OHLCV** | yfinance | EODHD | egxpy |
| **News** | NewsAPI | RSS feeds | Google News → Local CSV |
| **Fundamentals** | Local EGX CSVs | — | — |
| **Macro** | CSV (`egypt_macro.csv`) | FRED API | Static defaults |
| **FPI Flow** | CSV (`foreign_flow.csv`) | — | — |
| **FX Premium** | CSV (`fx_premium.csv`) | — | — |

### FRED API Fallback

**File:** `tradingagents/dataflows/fred_provider.py`

Lightweight wrapper using raw `requests` (no `fredapi` dependency):

```python
fetch_egypt_cpi(trade_date)   → FRED series FPCPITOTLZGEGY
fetch_egypt_tbill(trade_date) → FRED series INTGSTEGY91N
fetch_fred_series(series_id, trade_date, scale) → generic helper
```

Requires `FRED_API_KEY` in `.env`. Returns `None` if key missing or API fails — triggers static fallback.

### Cache Manager

**File:** `tradingagents/dataflows/cache_manager.py`

Default TTLs:

```python
"stock_data":     4 * 3600,   # 4 hours
"news":           1 * 3600,   # 1 hour
"social":         1 * 3600,   # 1 hour
"macro":         24 * 3600,   # 24 hours (CBE decisions are infrequent)
"regime":        24 * 3600,   # 24 hours (regime shifts daily at most)
"foreign_flow":  24 * 3600,   # 24 hours (FPI data published daily)
```

---

## 13. EGX Trading Constitution (21 Clauses)

The Constitutional Risk Manager evaluates trades against these 21 clauses, organized by category:

### Market Structure (Clauses 1–5)

1. **Long-only constraint** — No short selling permitted on EGX
2. **No leverage** — No margin trading or leveraged positions
3. **±10% daily price limit** — Circuit breaker halts trading
4. **T+2 settlement** — Funds/shares settle 2 business days after trade
5. **Trading hours** — 10:00–14:30 EGT (UTC+2) continuous session only

### Position Sizing (Clauses 6–10)

6. **10% ADV cap** — Maximum position ≤ 10% of Average Daily Volume
7. **10% portfolio concentration** — Maximum 10% of portfolio in single name (UCITS Art. 52)
8. **30% sector cap** — Maximum 30% of portfolio in single sector
9. **2% per-trade loss limit** — Elder (1993) 2% rule — hardest constraint
10. **ATR-based stops** — 2×ATR(14) primary, capped at 7%

### Liquidity (Clauses 11–14)

11. **50K minimum ADV** — Stocks below this threshold flagged as illiquid
12. **10-day exit horizon** — Must be able to exit position within 10 trading days
13. **Volume concentration** — Episodic liquidity (>60% from top 20% of days) triggers concern
14. **Zero-return veto** — >40% zero-return days = price discovery broken (Sussex 2023)

### FX & Macro (Clauses 15–18)

15. **FX stress veto** — ≥20% 30-day FX move → veto new entries
16. **Rate shock awareness** — ≥200 bps CBE change within 14 days triggers RISK_OFF
17. **Inflation regime** — CPI >30% triggers extreme caution
18. **VIX transmission** — VIX >35 indicates global risk-off spillover to EGX

### Data Quality (Clauses 19–21)

19. **Stub data guard** — Stub/demo/provisional data cannot escalate risk classifications
20. **FX premium monitoring** — Parallel market premium is strongest devaluation indicator (Harvard/Oki 2023)
21. **Data source transparency** — All signals must carry data_source and data_quality audit fields

---

## 14. Academic References

The following academic papers are directly referenced in the codebase (in comments, docstrings, or design documents):

### Market Microstructure & Liquidity

| Paper | Concept Used | Where |
|---|---|---|
| **Amihud, Y. (2002).** "Illiquidity and stock returns: cross-section and time-series effects." *Journal of Financial Markets.* | Amihud ILLIQ ratio: `mean(\|r_t\| / V_t)` | `liquidity_analyst.py`, `market_analyst.py` |
| **Sussex (2023).** African Markets study. | Zero-return frequency outperforms Amihud on EGX specifically | `liquidity_analyst.py` — used as primary illiquidity signal |
| **BHL (2007).** Bekaert, Harvey, Lundblad. | Zero-return frequency as illiquidity measure | `liquidity_analyst.py` |
| **Corwin, S. & Schultz, P. (2012).** "A simple way to estimate bid-ask spreads from daily high and low prices." *Journal of Finance.* | High-low spread proxy | `liquidity_analyst.py` |
| **Almgren, R. & Chriss, N. (2000).** "Optimal execution of portfolio transactions." *Journal of Risk.* | Two-tier liquidity, "characteristic time" (max 10 days to exit) | `risk_scorer.py` |
| **Bangia, A. et al. (1999).** "Modeling liquidity risk with implications for traditional market risk measurement and management." | L-VaR exogenous/endogenous decomposition | `risk_scorer.py` |

### Momentum & Factor Models

| Paper | Concept Used | Where |
|---|---|---|
| **Jegadeesh, N. & Titman, S. (1993).** "Returns to buying winners and selling losers: Implications for stock market efficiency." *Journal of Finance.* | 6-month momentum with 21-day skip (avoiding short-term reversal) | `market_analyst.py` |
| **Novy-Marx, R. (2013).** "The other side of value: The gross profitability premium." *Journal of Financial Economics.* | Gross Profit / Assets as profitability factor | `financial_calculator.py` |
| **Hanauer, M. & Lauterbach, J. (2019).** | Gross Profit / Assets metric validation | `financial_calculator.py` |

### Risk Management

| Paper | Concept Used | Where |
|---|---|---|
| **Elder, A. (1993, 2014).** *Trading for a Living* / *The New Trading for a Living.* | 2% per-trade hard loss cap (Elder 2% rule) | `risk_scorer.py` — CRITICAL constraint |
| **Wilder, J.W. (1978).** *New Concepts in Technical Trading Systems.* | ATR(14) as volatility baseline | `risk_scorer.py`, `market_analyst.py` |
| **Kaminski, K. & Lo, A. (2014).** | ATR stops add value under momentum/regime-switching conditions | `risk_scorer.py` |
| **Tharp, V. (2008).** | R-multiple framework | `risk_scorer.py` |

### EGX-Specific

| Paper | Concept Used | Where |
|---|---|---|
| **Farag, H. (2013).** | EGX magnet zone: elevated adverse selection within 1.5% of daily price limit | `risk_scorer.py` |
| **Farag, H. (2015).** | 1-day price reversal after limit-down events on EGX | `risk_scorer.py` |
| **Ezzat (2013).** | Joseph Effect — volatility persistence in Egyptian market | `regime_analyst.py` |
| **Harvard/Oki (2023).** | Parallel FX premium as strongest leading devaluation indicator | `macro_analyst.py` — FX premium loader |

### AI / ML Architecture

| Paper | Concept Used | Where |
|---|---|---|
| **Bai, Y. et al. (2022).** "Constitutional AI: Harmlessness from AI Feedback." | Constitutional AI pattern — LLM evaluates decisions against written principles | `risk_manager.py` — 21-clause EGX constitution |
| **Alshiekh, M. et al. (AAAI 2018).** "Safe Reinforcement Learning via Shielding." | Hard-rule pre-check before LLM decision (deterministic risk scorer as "shield") | `risk_scorer.py` — Layer 1 |

### NLP / Sentiment

| Paper | Concept Used | Where |
|---|---|---|
| **Araci, D. (2019).** "FinBERT: Financial Sentiment Analysis with Pre-Trained Language Models." | FinBERT for English financial sentiment | `sentiment_engine.py` |
| **Inoue, G. et al. (2021).** "The Interplay of Variant, Size, and Task Type in Arabic Pre-trained Language Models." CAMeL Lab. | CAMeLBERT-DA for Arabic dialect sentiment | `sentiment_engine.py` |
| **Conneau, A. et al. (2020).** "Unsupervised Cross-lingual Representation Learning at Scale." | XLM-RoBERTa for multilingual/code-switch sentiment | `sentiment_engine.py` |

### Fundamental Analysis

| Paper | Concept Used | Where |
|---|---|---|
| **Piotroski, J. (2000).** "Value investing: The use of historical financial statement information to separate winners from losers." | Piotroski F-Score (7-signal variant) | `financial_calculator.py` |
| **Helfert, E. & Penman, S.** Financial statement analysis framework. | H&P framework for thesis construction | `thesis_cot.py` |

---

## Appendix A: Configuration Reference

### `tradingagents/default_config.py`

| Key | Default | Description |
|---|---|---|
| `llm_provider` | `"openai"` | OpenAI-compatible client |
| `deep_think_llm` | `"deepseek-chat"` | Complex reasoning LLM |
| `quick_think_llm` | `"deepseek-chat"` | Fast synthesis LLM |
| `backend_url` | `"https://api.deepseek.com"` | LLM endpoint |
| `target_market` | `"EGX"` | Activates EGX-specific paths |
| `trading_currency` | `"EGP"` | All amounts in Egyptian Pounds |
| `long_only` | `True` | No short positions |
| `allow_short_selling` | `False` | FRA regulation |
| `allow_leverage` | `False` | No margin trading |
| `daily_price_limit_pct` | `0.10` | ±10% circuit breaker |
| `max_position_pct_adv` | `0.10` | 10% of ADV cap |
| `trade_horizon_months` | `6` | Investment horizon |
| `backtest_mode` | `True` | Relaxes concentration veto |
| `prefetch_data` | `True` | Parallel pre-fetch |
| `auto_refresh_fundamentals` | `True` | Refresh stale CSVs |
| `fundamentals_max_age_days` | `90` | Staleness threshold |
| `egx_risk_free_rate` | `0.275` | CBE policy rate proxy |

### Environment Variables (`.env`)

| Variable | Required | Purpose |
|---|---|---|
| `DEEPSEEK_API_KEY` | Yes | Primary LLM |
| `OPENAI_API_KEY` | Fallback | LLM failover |
| `EODHD_API_KEY` | Optional | OHLCV fallback |
| `NEWS_API_KEY` | Optional | News fallback chain |
| `FRED_API_KEY` | Optional | FRED macro data |
| `APIFY_API_TOKEN` | Optional | Facebook scraping |
| `POSTGRES_URL` | Optional | Persistent memory |
| `REDIS_URL` | Optional | WebSocket streaming |

---

## Appendix B: File Index

| File | Purpose |
|---|---|
| `tradingagents/agents/analysts/market_analyst.py` | Technical analysis (RSI, MACD, BB, momentum) |
| `tradingagents/agents/analysts/fundamentals_analyst.py` | Wrapper for fundamentals pipeline |
| `tradingagents/agents/analysts/fundamentals/pipeline.py` | 3-stage CoT orchestrator |
| `tradingagents/agents/analysts/fundamentals/data_cot.py` | Stage 1: deterministic evidence pack |
| `tradingagents/agents/analysts/fundamentals/concept_cot.py` | Stage 2: quick-LLM concept synthesis |
| `tradingagents/agents/analysts/fundamentals/thesis_cot.py` | Stage 3: deep-LLM H&P thesis |
| `tradingagents/agents/analysts/fundamentals/financial_calculator.py` | 14 core ratios + Piotroski |
| `tradingagents/agents/analysts/fundamentals/scoring.py` | data_confidence, signal_coherence |
| `tradingagents/agents/analysts/fundamentals/sector_config.py` | Bank/RE/Holdings/Operational profiles |
| `tradingagents/agents/analysts/news_analyst.py` | Bilingual news processing |
| `tradingagents/agents/analysts/social_media_analyst.py` | Phase 3 layered social pipeline |
| `tradingagents/agents/analysts/macro_analyst.py` | CBE rates, CPI, FX, FRED fallback |
| `tradingagents/agents/analysts/liquidity_analyst.py` | Amihud, zero-return, ADV, FPI flow |
| `tradingagents/agents/analysts/regime_analyst.py` | Market regime classification |
| `tradingagents/agents/researchers/bull_researcher.py` | Bullish thesis construction |
| `tradingagents/agents/researchers/bear_researcher.py` | Bearish thesis (AVOID/REDUCE/UNDERWEIGHT) |
| `tradingagents/agents/managers/research_manager.py` | Debate judge → BUY/SELL/HOLD |
| `tradingagents/agents/managers/risk_manager.py` | Constitutional AI risk gate |
| `tradingagents/agents/risk_mgmt/risk_scorer.py` | Deterministic risk checks |
| `tradingagents/agents/risk_mgmt/merged_debator.py` | 3-perspective LLM risk debate |
| `tradingagents/agents/trader/trader.py` | Execution plan generation |
| `tradingagents/agents/utils/scoring.py` | Unified scoring + blend cascade |
| `tradingagents/agents/utils/agent_states.py` | AgentState TypedDict |
| `tradingagents/utils/sentiment_engine.py` | FinBERT / CAMeLBERT / XLM-R router |
| `tradingagents/utils/text_preprocessor.py` | Arabic text normalization |
| `tradingagents/dataflows/gateway.py` | DataGateway: cache → primary → fallback |
| `tradingagents/dataflows/fred_provider.py` | FRED API wrapper |
| `tradingagents/dataflows/cache_manager.py` | diskcache TTL store |
| `tradingagents/graph/trading_graph.py` | TradingAgentsGraph entry class |
| `tradingagents/graph/setup.py` | Graph nodes + edges |
| `tradingagents/graph/propagation.py` | Initial state factory |
| `tradingagents/graph/prefetch.py` | Parallel news/social pre-fetch |
| `tradingagents/graph/signal_processing.py` | Regex BUY/SELL/HOLD extractor |
| `tradingagents/default_config.py` | DEFAULT_CONFIG + EGX_TICKERS |
