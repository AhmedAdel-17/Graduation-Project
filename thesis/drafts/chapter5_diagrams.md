# Chapter 5 Diagrams - StockHive System Design

This draft contains thesis-ready Mermaid diagrams for Chapter 5. The diagrams are based on the current implementation rather than the older template placeholders:

- `tradingagents/graph/setup.py` for the LangGraph workflow.
- `tradingagents/agents/utils/agent_states.py` for state objects.
- `Graduation-Project/db_schema.sql` for the database model.
- `server/api_server.py` and `dashboard/src` for UI/API boundaries.
- `CLAUDE.md` and `agent_docs/db_infrastructure.md` for architecture framing.

Important correction: older notes show a Bull/Bear cycle. The current graph is a strict linear chain: Bull Researcher -> Bear Researcher -> Research Manager.

## 5.1 System Architecture Diagram

```mermaid
flowchart TB
    User["Analyst / Investor<br/>System Evaluator<br/>Administrator"]

    subgraph Client["Client Layer"]
        Dashboard["React Dashboard<br/>Vite + lightweight-charts"]
        CLI["Rich CLI"]
    end

    subgraph API["Application Interface Layer"]
        FastAPI["FastAPI Server<br/>REST endpoints + WebSocket stream"]
        BacktestCLI["Backtest and Evaluation Scripts"]
    end

    subgraph Core["Core Decision-Support Layer"]
        TAG["TradingAgentsGraph<br/>LangGraph orchestrator"]
        Prefetch["DataPrefetcher<br/>parallel news + social prefetch"]
        Graph["Compiled StateGraph<br/>agent workflow"]
        Signal["SignalProcessor<br/>BUY / HOLD / SELL extraction"]
        Audit["Audit Writer<br/>sessions + agent events"]
    end

    subgraph Agents["Agent Reasoning Layer"]
        Market["Market Analyst<br/>technical indicators"]
        Fundamental["Fundamentals Analyst<br/>EGX ratios + CoT pipeline"]
        News["News Analyst<br/>company + market news"]
        Social["Social Media Analyst<br/>Arabic/English sentiment"]
        Bull["Bull Researcher"]
        Bear["Bear Researcher"]
        ResearchMgr["Research Manager"]
        Trader["Trader<br/>execution plan"]
        RiskScorer["Deterministic EGX Risk Scorer"]
        RiskDebate["Merged Risk Debate"]
        RiskJudge["Risk Judge"]
    end

    subgraph Data["Data and Memory Layer"]
        Gateway["DataGateway<br/>cache -> primary -> fallback"]
        DiskCache["DiskCache TTL store"]
        LocalCSV["Local EGX CSV datasets"]
        Chroma["ChromaDB memory<br/>default vector store"]
        Postgres["PostgreSQL<br/>audit, backtests, optional memory"]
        Redis["Redis pub/sub<br/>optional live progress"]
    end

    subgraph External["External Providers"]
        YFinance["Yahoo Finance / yfinance"]
        EODHD["EODHD fallback"]
        NewsAPI["News/RSS providers"]
        SocialSources["Facebook, Telegram, Reddit"]
        LLM["LLM Provider<br/>DeepSeek/OpenAI-compatible API"]
    end

    User --> Dashboard
    User --> CLI
    Dashboard --> FastAPI
    CLI --> TAG
    FastAPI --> TAG
    FastAPI --> BacktestCLI
    BacktestCLI --> TAG

    TAG --> Prefetch
    TAG --> Graph
    Graph --> Market
    Graph --> Fundamental
    Graph --> News
    Graph --> Social
    Market --> Bull
    Fundamental --> Bull
    News --> Bull
    Social --> Bull
    Bull --> Bear
    Bear --> ResearchMgr
    ResearchMgr --> Trader
    Trader --> RiskScorer
    RiskScorer -->|"VETO"| Signal
    RiskScorer -->|"ALLOW / WARN / THROTTLE"| RiskDebate
    RiskDebate --> RiskJudge
    RiskJudge --> Signal

    Prefetch --> Gateway
    Market --> Gateway
    Fundamental --> LocalCSV
    News --> Gateway
    Social --> Gateway
    Gateway --> DiskCache
    Gateway --> YFinance
    Gateway --> EODHD
    Gateway --> NewsAPI
    Gateway --> SocialSources

    Bull --> Chroma
    Bear --> Chroma
    ResearchMgr --> Chroma
    Trader --> Chroma
    RiskJudge --> Chroma
    Audit --> Postgres
    TAG --> Audit
    FastAPI --> Redis
    TAG --> Redis
    Agents --> LLM
```

Figure 5.1: Layered system architecture for StockHive, showing client interfaces, the FastAPI boundary, the LangGraph orchestration core, specialized analysis agents, data providers, audit storage, and optional real-time streaming infrastructure.

## 5.2 Database Design

```mermaid
erDiagram
    analysis_sessions {
        int id PK
        text session_id UK
        text ticker
        date trade_date
        text market
        text final_decision
        boolean risk_veto
        numeric confidence_overall
        jsonb confidence_scores
        jsonb execution_plan
        jsonb risk_assessment
        jsonb data_quality
        jsonb full_state
        timestamptz created_at
    }

    agent_events {
        int id PK
        text session_id FK
        text event_type
        text agent_name
        text opinion_type
        text opinion_summary
        numeric confidence_score
        jsonb structured_output
        timestamptz logged_at
    }

    backtest_runs {
        int id PK
        text run_id UK
        text ticker
        text strategy
        date start_date
        date end_date
        numeric total_return_pct
        numeric benchmark_return_pct
        numeric alpha_pct
        numeric sharpe_ratio
        numeric max_drawdown_pct
        numeric win_rate_pct
        int total_trades
        numeric final_portfolio_egp
        jsonb metrics
        timestamptz created_at
    }

    backtest_trades {
        int id PK
        text run_id FK
        date trade_date
        text action
        numeric shares
        numeric price_egp
        numeric value_egp
        numeric commission_egp
        numeric portfolio_value
        text signal
        numeric confidence
        text notes
    }

    agent_memories {
        int id PK
        text agent_name
        text ticker
        text situation
        text recommendation
        jsonb embedding
        timestamptz created_at
    }

    ohlcv_prices {
        text ticker PK
        date trade_date PK
        numeric open
        numeric high
        numeric low
        numeric close
        bigint volume
        text source
        timestamptz fetched_at
    }

    cache_entries {
        text cache_key PK
        jsonb value
        text data_type
        timestamptz expires_at
        timestamptz created_at
    }

    social_v2_posts {
        bigint id PK
        text post_hash UK
        text platform
        text source
        text url
        text username
        timestamptz post_timestamp
        timestamptz scraped_at
        text text
        int engagement
        text_array symbols
        text_array intents
        text content_label
        real sentiment_score
        text sentiment_label
    }

    analysis_sessions ||--o{ agent_events : "records"
    backtest_runs ||--o{ backtest_trades : "contains"
```

Figure 5.2: PostgreSQL database design for audit logging, backtest persistence, optional memory storage, OHLCV caching, and social-media archive replay.

## 5.3 Data Design: Runtime Data Flow

```mermaid
flowchart LR
    Request["Analysis request<br/>ticker + trade date"] --> Prefetch["Prefetch phase"]

    subgraph Sources["Multi-source data inputs"]
        OHLCV["OHLCV prices"]
        Indicators["Technical indicators"]
        Fundamentals["Fundamentals CSVs<br/>statements + ratios"]
        News["Company and market news"]
        Social["Social posts<br/>Facebook / Telegram / Reddit"]
        Macro["Macro and market breadth context"]
    end

    subgraph Acquisition["Data acquisition and quality controls"]
        Cache["DiskCache lookup"]
        Primary["Primary provider"]
        Fallback["Fallback provider or stale cache"]
        Quality["Data-quality flags<br/>freshness, coverage, confidence penalty"]
    end

    subgraph State["AgentState payload"]
        Reports["Analyst reports"]
        Structured["Structured outputs<br/>technical, fundamental, sentiment"]
        Context["Portfolio, liquidity, macro, memory context"]
    end

    Prefetch --> Cache
    Cache --> Primary
    Primary --> Fallback
    Fallback --> Quality

    OHLCV --> Cache
    Indicators --> Cache
    Fundamentals --> Cache
    News --> Cache
    Social --> Cache
    Macro --> Cache

    Quality --> Reports
    Quality --> Structured
    Quality --> Context
    Reports --> Decision["Recommendation + thesis + risk explanation"]
    Structured --> Decision
    Context --> Decision
```

Figure 5.3: Runtime data design showing how StockHive combines price data, fundamentals, news, social sentiment, macro context, cache/fallback controls, and quality flags into the shared `AgentState`.

## 5.4.1 Class Diagram

```mermaid
classDiagram
    class TradingAgentsGraph {
        +config: Dict
        +graph: CompiledStateGraph
        +curr_state: AgentState
        +propagate(company_name, trade_date)
        +_create_tool_nodes()
        +_log_state()
    }

    class GraphSetup {
        +quick_thinking_llm
        +deep_thinking_llm
        +tool_nodes: Dict
        +setup_graph(selected_analysts)
    }

    class ConditionalLogic {
        +should_continue_market()
        +should_continue_social()
        +should_continue_news()
        +should_continue_fundamentals()
        +route_risk_action()
    }

    class Propagator {
        +create_initial_state(company, trade_date)
        +propagate_confidence()
    }

    class SignalProcessor {
        +process_signal(final_state)
        +extract_signal(text)
    }

    class AgentState {
        +company_of_interest: str
        +trade_date: str
        +market_report: str
        +fundamentals_report: str
        +news_report: str
        +sentiment_report: str
        +investment_debate_state: InvestDebateState
        +execution_plan: Dict
        +risk_action: str
        +risk_metrics: Dict
        +final_trade_decision: str
    }

    class InvestDebateState {
        +bull_history: str
        +bear_history: str
        +bull_thesis: Dict
        +bear_thesis: Dict
        +judge_decision: str
    }

    class RiskDebateState {
        +risky_history: str
        +safe_history: str
        +neutral_history: str
        +judge_decision: str
    }

    class FinancialSituationMemory {
        +collection_name: str
        +add_situations()
        +get_memories()
    }

    class DataGateway {
        +get_price_data()
        +get_news()
        +cache_primary_fallback_chain()
    }

    class BacktestingEngine {
        +initial_capital: float
        +positions: Dict
        +trade_history: List
        +run_backtest()
        +execute_trade()
        +save_results()
    }

    TradingAgentsGraph *-- GraphSetup
    TradingAgentsGraph *-- Propagator
    TradingAgentsGraph *-- SignalProcessor
    TradingAgentsGraph *-- FinancialSituationMemory
    TradingAgentsGraph --> AgentState
    GraphSetup --> ConditionalLogic
    GraphSetup --> AgentState
    AgentState *-- InvestDebateState
    AgentState *-- RiskDebateState
    BacktestingEngine --> TradingAgentsGraph
    BacktestingEngine --> DataGateway
```

Figure 5.4: Main class relationships for the StockHive orchestration, state, memory, data gateway, signal processing, and backtesting components.

## 5.4.2 Sequence Diagram: Single Analysis Run

```mermaid
sequenceDiagram
    autonumber
    actor Analyst as Analyst / Investor
    participant UI as Dashboard or CLI
    participant API as FastAPI Server
    participant Graph as TradingAgentsGraph
    participant Prefetch as DataPrefetcher
    participant Gateway as DataGateway
    participant Analysts as Parallel Analyst Team
    participant Research as Bull/Bear/Research Manager
    participant Trader as Trader Agent
    participant Risk as Risk Scorer and Risk Judge
    participant Signal as SignalProcessor
    participant Store as Audit Store

    Analyst->>UI: Select ticker and analysis date
    UI->>API: Submit analysis request
    API->>Graph: propagate(ticker, trade_date)
    Graph->>Prefetch: Fetch news, social, macro, breadth
    Prefetch->>Gateway: cache -> primary -> fallback
    Gateway-->>Prefetch: Prefetched data + quality flags
    Graph->>Analysts: Run market, fundamentals, news, social in parallel
    Analysts-->>Graph: Reports, structured outputs, confidence scores
    Graph->>Research: Bull thesis
    Research->>Research: Bear response
    Research-->>Graph: Research Manager investment decision
    Graph->>Trader: Generate execution plan
    Trader-->>Graph: Position size, stop-loss, take-profit
    Graph->>Risk: Deterministic EGX risk checks

    alt Critical risk violation
        Risk-->>Graph: VETO + structured violations
        Graph->>Signal: Force HOLD / reject unsafe plan
    else Passes deterministic shield
        Risk->>Risk: Merged risk debate
        Risk-->>Graph: Final risk decision
        Graph->>Signal: Extract BUY / HOLD / SELL
    end

    Signal-->>Graph: Discrete signal + confidence
    Graph->>Store: Persist session, events, full state
    Graph-->>API: Recommendation, thesis, risk explanation
    API-->>UI: REST response / WebSocket progress
    UI-->>Analyst: Display recommendation for human review
```

Figure 5.5: Sequence diagram for a single StockHive analysis run from user request through parallel analysis, research debate, execution planning, EGX risk enforcement, signal extraction, and audit persistence.

## 5.4.3 Activity Diagram: Historical Evaluation / Backtest

```mermaid
flowchart TD
    Start([Start backtest]) --> Configure["Configure ticker set, date window,<br/>interval, initial capital, analysts"]
    Configure --> LoadData["Load historical OHLCV<br/>and benchmark data"]
    LoadData --> InitLedger["Initialize cash, positions,<br/>settlement queue, audit log"]
    InitLedger --> DateLoop{"More evaluation dates?"}

    DateLoop -->|"Yes"| Settle["Release T+2 settled cash"]
    Settle --> Bounds["Apply temporal bounds<br/>trade_date is the data horizon"]
    Bounds --> RunGraph["Run UC-02 analysis pipeline"]
    RunGraph --> Signal{"Signal?"}

    Signal -->|"BUY"| BuyChecks["Check cash, liquidity,<br/>ADV cap, price band"]
    Signal -->|"SELL"| SellChecks["Check existing long position<br/>and T+2 settlement"]
    Signal -->|"HOLD"| RecordHold["Record no-trade decision"]

    BuyChecks --> ExecuteBuy["Execute simulated BUY<br/>with commission + slippage"]
    SellChecks --> ExecuteSell["Execute simulated SELL<br/>proceeds settle T+2"]
    ExecuteBuy --> UpdateLedger["Update portfolio ledger"]
    ExecuteSell --> UpdateLedger
    RecordHold --> UpdateLedger

    UpdateLedger --> Checkpoint["Save checkpoint / audit entry"]
    Checkpoint --> DateLoop

    DateLoop -->|"No"| Metrics["Compute return, alpha vs EGX30,<br/>Sharpe, drawdown, win rate,<br/>Wilson confidence interval"]
    Metrics --> Persist["Persist JSON report and optional<br/>PostgreSQL backtest rows"]
    Persist --> End([End backtest])
```

Figure 5.6: Activity diagram for historical evaluation, including strict temporal bounds, EGX trading assumptions, T+2 settlement, checkpointing, and final performance metric computation.

## Optional Appendix Diagram: Agent Workflow Detail

Use this if Chapter 5 needs one deeper diagram after the high-level architecture.

```mermaid
flowchart TB
    START([START])

    subgraph Parallel["Parallel analyst fan-out"]
        M["Market Analyst"]
        S["Social Media Analyst"]
        N["News Analyst"]
        F["Fundamentals Analyst"]
    end

    subgraph Tools["Per-analyst isolated tool loops"]
        MT["tools_market"]
        ST["tools_social"]
        NT["tools_news"]
        FT["tools_fundamentals"]
    end

    MC["Msg Clear Market"]
    SC["Msg Clear Social"]
    NC["Msg Clear News"]
    FC["Msg Clear Fundamentals"]
    Sync["Analysts Sync<br/>deferred barrier"]

    Bull["Bull Researcher"]
    Bear["Bear Researcher"]
    RM["Research Manager"]
    Trader["Trader"]
    RS["Risk Scorer"]
    RV["Risk Veto"]
    MRD["Merged Risk Debate"]
    RJ["Risk Judge"]
    ENDN([END])

    START --> M
    START --> S
    START --> N
    START --> F

    M -->|"tool call"| MT
    MT --> M
    M -->|"done"| MC

    S -->|"tool call"| ST
    ST --> S
    S -->|"done"| SC

    N -->|"tool call"| NT
    NT --> N
    N -->|"done"| NC

    F -->|"tool call"| FT
    FT --> F
    F -->|"done"| FC

    MC --> Sync
    SC --> Sync
    NC --> Sync
    FC --> Sync

    Sync --> Bull
    Bull --> Bear
    Bear --> RM
    RM --> Trader
    Trader --> RS
    RS -->|"risk_action == VETO"| RV
    RS -->|"otherwise"| MRD
    MRD --> RJ
    RV --> ENDN
    RJ --> ENDN
```

Figure 5.7: Detailed LangGraph workflow showing parallel analyst branches, isolated per-analyst tool loops, the synchronization barrier, the linear research debate, and deterministic risk routing.
