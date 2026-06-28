-- =============================================================================
-- EGX Trading System — PostgreSQL Schema
-- =============================================================================
-- Run this once to set up the database:
--   createdb egx_trading
--   psql -U postgres -d egx_trading -f db_schema.sql
-- =============================================================================
-- NOTE: pgvector extension is optional (requires compiling from source on Windows).
-- The embedding column uses JSONB as a fallback. To enable vector similarity search,
-- install pgvector (https://github.com/pgvector/pgvector) and change the column
-- type back to vector(1536).
-- =============================================================================

-- CREATE EXTENSION IF NOT EXISTS vector;  -- Uncomment after installing pgvector on Windows

-- =============================================================================
-- 1. AGENT MEMORIES  (OPTIONAL — opt-in Postgres/pgvector memory backend only)
-- =============================================================================
-- Default agent memory is ChromaDB (TRADINGAGENTS_MEMORY_BACKEND=chroma).
-- This table is used ONLY when TRADINGAGENTS_MEMORY_BACKEND=postgres AND pgvector
-- is installed. It is NOT part of the core thesis audit schema; exclude from the
-- thesis ERD. See agent_docs/db_infrastructure.md §1.
-- =============================================================================
CREATE TABLE IF NOT EXISTS agent_memories (
    id              SERIAL PRIMARY KEY,
    agent_name      TEXT        NOT NULL,
    ticker          TEXT,
    situation       TEXT        NOT NULL,
    recommendation  TEXT        NOT NULL,
    embedding       JSONB,          -- stores float[] as JSON; change to vector(1536) after installing pgvector
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_memories_agent ON agent_memories (agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_memories_ticker ON agent_memories (ticker);
-- Run after 100+ rows accumulate:
-- CREATE INDEX agent_memories_vec_idx ON agent_memories
--     USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- =============================================================================
-- 2. ANALYSIS SESSIONS  (replaces audit_logs/*.jsonl files)
-- =============================================================================
CREATE TABLE IF NOT EXISTS analysis_sessions (
    id                  SERIAL PRIMARY KEY,
    session_id          TEXT        NOT NULL UNIQUE,
    ticker              TEXT        NOT NULL,
    trade_date          DATE        NOT NULL,
    market              TEXT        DEFAULT 'EGX',
    final_decision      TEXT,
    risk_veto           BOOLEAN     DEFAULT FALSE,
    confidence_overall  NUMERIC(5,3),
    confidence_scores   JSONB,
    execution_plan      JSONB,
    risk_assessment     JSONB,
    data_quality        JSONB,
    full_state          JSONB,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sessions_ticker      ON analysis_sessions (ticker);
CREATE INDEX IF NOT EXISTS idx_sessions_date        ON analysis_sessions (trade_date);
CREATE INDEX IF NOT EXISTS idx_sessions_ticker_date ON analysis_sessions (ticker, trade_date DESC);

-- =============================================================================
-- 3. AGENT EVENTS  (granular per-agent event log)
-- =============================================================================
CREATE TABLE IF NOT EXISTS agent_events (
    id               SERIAL PRIMARY KEY,
    session_id       TEXT        NOT NULL REFERENCES analysis_sessions(session_id),
    event_type       TEXT        NOT NULL,
    agent_name       TEXT,
    opinion_type     TEXT,
    opinion_summary  TEXT,
    confidence_score NUMERIC(5,3),
    structured_output JSONB,
    logged_at        TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_events_session ON agent_events (session_id);

-- =============================================================================
-- 4. BACKTEST RUNS  (replaces backtest_results/*.json files)
-- =============================================================================
CREATE TABLE IF NOT EXISTS backtest_runs (
    id                   SERIAL PRIMARY KEY,
    run_id               TEXT        NOT NULL UNIQUE,
    ticker               TEXT        NOT NULL,
    strategy             TEXT        NOT NULL,
    start_date           DATE        NOT NULL,
    end_date             DATE        NOT NULL,
    total_return_pct     NUMERIC(8,4),
    benchmark_return_pct NUMERIC(8,4),
    alpha_pct            NUMERIC(8,4),
    sharpe_ratio         NUMERIC(8,4),
    calmar_ratio         NUMERIC(8,4),
    max_drawdown_pct     NUMERIC(8,4),
    win_rate_pct         NUMERIC(8,4),
    total_trades         INTEGER,
    total_commissions    NUMERIC(12,2),
    final_portfolio_egp  NUMERIC(14,2),
    metrics              JSONB,
    created_at           TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_backtest_ticker   ON backtest_runs (ticker);
CREATE INDEX IF NOT EXISTS idx_backtest_strategy ON backtest_runs (strategy);

-- =============================================================================
-- 5. BACKTEST TRADES  (replaces trades_*.csv files)
-- =============================================================================
CREATE TABLE IF NOT EXISTS backtest_trades (
    id              SERIAL PRIMARY KEY,
    run_id          TEXT        NOT NULL REFERENCES backtest_runs(run_id),
    trade_date      DATE        NOT NULL,
    action          TEXT        NOT NULL,
    shares          NUMERIC(12,4),
    price_egp       NUMERIC(10,4),
    value_egp       NUMERIC(14,2),
    commission_egp  NUMERIC(10,4),
    portfolio_value NUMERIC(14,2),
    signal          TEXT,
    confidence      NUMERIC(5,3),
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_run_id ON backtest_trades (run_id);
CREATE INDEX IF NOT EXISTS idx_trades_date   ON backtest_trades (trade_date);

-- =============================================================================
-- 6. OHLCV PRICE DATA  (replaces repeated yfinance/EODHD API calls)
-- =============================================================================
CREATE TABLE IF NOT EXISTS ohlcv_prices (
    ticker      TEXT        NOT NULL,
    trade_date  DATE        NOT NULL,
    open        NUMERIC(12,4),
    high        NUMERIC(12,4),
    low         NUMERIC(12,4),
    close       NUMERIC(12,4),
    volume      BIGINT,
    source      TEXT        DEFAULT 'yfinance',
    fetched_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (ticker, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker_date ON ohlcv_prices (ticker, trade_date DESC);

-- =============================================================================
-- 7. CACHE TABLE  (optional: for deployments without Redis)
-- =============================================================================
CREATE TABLE IF NOT EXISTS cache_entries (
    cache_key   TEXT        PRIMARY KEY,
    value       JSONB       NOT NULL,
    data_type   TEXT        DEFAULT 'default',
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache_entries (expires_at);

CREATE OR REPLACE FUNCTION purge_expired_cache()
RETURNS INTEGER AS $$
DECLARE deleted_count INTEGER;
BEGIN
    DELETE FROM cache_entries WHERE expires_at < NOW();
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- 8. USEFUL VIEWS
-- =============================================================================
CREATE OR REPLACE VIEW latest_analysis AS
SELECT DISTINCT ON (ticker)
    ticker, trade_date, final_decision, confidence_overall, risk_veto, session_id, created_at
FROM analysis_sessions
ORDER BY ticker, trade_date DESC;

CREATE OR REPLACE VIEW backtest_comparison AS
SELECT
    llm.ticker,
    llm.start_date,
    llm.end_date,
    llm.total_return_pct    AS llm_return,
    cls.total_return_pct    AS classical_return,
    llm.total_return_pct - cls.total_return_pct AS alpha_vs_classical,
    llm.sharpe_ratio        AS llm_sharpe,
    cls.sharpe_ratio        AS classical_sharpe,
    llm.max_drawdown_pct    AS llm_drawdown,
    cls.max_drawdown_pct    AS classical_drawdown,
    llm.win_rate_pct        AS llm_win_rate
FROM backtest_runs llm
JOIN backtest_runs cls
    ON  llm.ticker     = cls.ticker
    AND llm.start_date = cls.start_date
    AND llm.end_date   = cls.end_date
WHERE llm.strategy = 'llm'
  AND cls.strategy  = 'classical';

-- =============================================================================
-- social_v2_posts — historical social-media post archive
-- =============================================================================
-- Written by tradingagents/dataflows/social_v2/post_store.py on every live
-- pipeline run. Lets future backtests replay real historical social data
-- instead of using news-derived proxies. The signal_adapter falls back to
-- this table when curr_date is more than 1 day in the past.
--
-- Idempotency: post_hash = sha256(platform|url|timestamp|text[:500]).
-- Conflicting rows are ignored, so re-scraping the same posts is safe.
CREATE TABLE IF NOT EXISTS social_v2_posts (
    id              BIGSERIAL PRIMARY KEY,
    post_hash       TEXT UNIQUE NOT NULL,
    platform        TEXT NOT NULL,
    source          TEXT NOT NULL,
    url             TEXT,
    username        TEXT,
    post_timestamp  TIMESTAMPTZ,
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    text            TEXT NOT NULL,
    engagement      INTEGER DEFAULT 0,
    symbols         TEXT[],
    intents         TEXT[],
    content_label   TEXT,
    sentiment_score REAL,
    sentiment_label TEXT,
    sectors         TEXT[],   -- EGX sectors rolled up from symbols (taxonomy)
    indices         TEXT[]    -- EGX30/70/100 membership rolled up from symbols
);

-- Additive migration for deployments created before the tag columns existed.
ALTER TABLE social_v2_posts ADD COLUMN IF NOT EXISTS sectors TEXT[];
ALTER TABLE social_v2_posts ADD COLUMN IF NOT EXISTS indices TEXT[];

CREATE INDEX IF NOT EXISTS social_v2_posts_ts_idx
    ON social_v2_posts (post_timestamp);

-- GIN indexes support the `WHERE %s = ANY(symbols/sectors/indices)` lookups used
-- by the signal_adapter archive-replay path (select a stock's own / sector / index posts).
CREATE INDEX IF NOT EXISTS social_v2_posts_symbols_idx
    ON social_v2_posts USING GIN (symbols);
CREATE INDEX IF NOT EXISTS social_v2_posts_sectors_idx
    ON social_v2_posts USING GIN (sectors);
CREATE INDEX IF NOT EXISTS social_v2_posts_indices_idx
    ON social_v2_posts USING GIN (indices);

-- =============================================================================
-- 9. PORTFOLIO ASSISTANT  (Portfolio Optimization Assistant subsystem, design v3)
-- =============================================================================
-- Persistence for the conversational copilot (docs/PORTFOLIO_ASSISTANT_DESIGN.md
-- §8). Tables are prefixed `pa_` and are self-contained: dropping the subsystem
-- means dropping only these. All JSONB payloads mirror the Pydantic models in
-- tradingagents/portfolio/schemas.py (the store round-trips through them, so the
-- column shapes intentionally stay loose — JSONB, not normalized columns).
--
-- Audit doctrine (CLAUDE.md §11): snapshots / policies / scenarios / proposals
-- are append-only and soft-deleted (status flags), NEVER hard-deleted, except
-- via the ON DELETE CASCADE from a conversation the user explicitly removes.
--
-- REQUIREMENT: PostgreSQL >= 13 (for the core `gen_random_uuid()` used by
-- pa_conversations). On Postgres < 13, first run `CREATE EXTENSION IF NOT EXISTS
-- pgcrypto;` (needs appropriate privileges) to provide that function.
-- =============================================================================

-- 9.1 Conversations — one row per chat thread (1:1 with a workspace for now).
-- `active_ref` is the workspace pointer: 'baseline' | str(scenario_id).
CREATE TABLE IF NOT EXISTS pa_conversations (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT,                   -- nullable until auth lands (MEMORY.md §B); 'local' in single-user demo
    title           TEXT,
    language        TEXT        DEFAULT 'auto',   -- 'en' | 'ar' | 'auto'
    active_ref      TEXT        DEFAULT 'baseline',
    last_proposal_id BIGINT,
    archived        BOOLEAN     DEFAULT FALSE,    -- soft delete
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pa_conversations_user
    ON pa_conversations (user_id, updated_at DESC);

-- 9.2 Messages — user/assistant/system turns with typed content blocks.
-- `blocks` is the ChatBlock[] discriminated union (rendered client-side).
-- `scenario_id` records which branch the message was about (nullable).
CREATE TABLE IF NOT EXISTS pa_messages (
    id              BIGSERIAL   PRIMARY KEY,
    conversation_id UUID        NOT NULL REFERENCES pa_conversations(id) ON DELETE CASCADE,
    role            TEXT        NOT NULL CHECK (role IN ('user','assistant','system')),
    text_content    TEXT,
    blocks          JSONB,                  -- list[ChatBlock]
    scenario_id     BIGINT,                 -- soft ref to pa_scenarios.id (nullable)
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pa_messages_conv
    ON pa_messages (conversation_id, id);

-- 9.3 Portfolio snapshots — BASELINE versions only (scenarios store their own
-- derived state in pa_scenarios). `positions` = list[PortfolioHolding].
CREATE TABLE IF NOT EXISTS pa_portfolio_snapshots (
    id                      BIGSERIAL   PRIMARY KEY,
    conversation_id         UUID        NOT NULL REFERENCES pa_conversations(id) ON DELETE CASCADE,
    version                 INTEGER     NOT NULL,
    cash_egp                NUMERIC,
    positions               JSONB       NOT NULL,   -- list[PortfolioHolding]
    promoted_from_scenario  BIGINT,                 -- provenance when adopted from a what-if
    confirmed_by_user       BOOLEAN     DEFAULT FALSE,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (conversation_id, version)
);
CREATE INDEX IF NOT EXISTS idx_pa_snapshots_conv
    ON pa_portfolio_snapshots (conversation_id, version DESC);

-- 9.4 Investment policy — versioned (Strategy Agent output). `policy` carries
-- the full InvestmentPolicy incl. source_spans + inferred_fields; the compiled
-- OptimizerParams snapshot is kept alongside for audit/explainability.
CREATE TABLE IF NOT EXISTS pa_policies (
    id                BIGSERIAL   PRIMARY KEY,
    conversation_id   UUID        NOT NULL REFERENCES pa_conversations(id) ON DELETE CASCADE,
    version           INTEGER     NOT NULL,
    policy            JSONB       NOT NULL,   -- InvestmentPolicy
    compiled_params   JSONB,                  -- OptimizerParams snapshot
    compiler_version  TEXT,
    source_message_id BIGINT,                 -- which message produced this policy
    confirmed_by_user BOOLEAN     DEFAULT FALSE,
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (conversation_id, version)
);
CREATE INDEX IF NOT EXISTS idx_pa_policies_conv
    ON pa_policies (conversation_id, version DESC);

-- 9.5 Scenario tree — git-like branches of hypotheticals (Enhancement 2).
-- `parent_scenario_id` NULL = forked from baseline. `derived_positions` /
-- `derived_policy` are the PINNED post-patch state; `input_set` pins the
-- prices/signals/covariance the branch was computed against so diffs are honest.
CREATE TABLE IF NOT EXISTS pa_scenarios (
    id                  BIGSERIAL   PRIMARY KEY,
    conversation_id     UUID        NOT NULL REFERENCES pa_conversations(id) ON DELETE CASCADE,
    parent_scenario_id  BIGINT      REFERENCES pa_scenarios(id) ON DELETE CASCADE,
    base_snapshot_id    BIGINT      REFERENCES pa_portfolio_snapshots(id),
    patch               JSONB       NOT NULL,   -- ScenarioPatch
    derived_positions   JSONB       NOT NULL,   -- pinned post-patch list[PortfolioHolding]
    derived_policy      JSONB,                  -- pinned post-OVERRIDE_POLICY InvestmentPolicy
    input_set           JSONB,                  -- PinnedInputSet (price asof, signal session ids, covariance hash)
    proposal_id         BIGINT,
    status              TEXT        DEFAULT 'active'
                                    CHECK (status IN ('active','promoted','discarded')),
    label               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pa_scenarios_conv
    ON pa_scenarios (conversation_id, status);

-- 9.6 Optimization proposals — full-audit, append-only (never deleted).
-- Exactly one of snapshot_id / scenario_id identifies what was optimized
-- (enforced in the application layer via OptimizationProposal's validator).
CREATE TABLE IF NOT EXISTS pa_optimization_proposals (
    id              BIGSERIAL   PRIMARY KEY,
    conversation_id UUID        REFERENCES pa_conversations(id) ON DELETE CASCADE,
    snapshot_id     BIGINT      REFERENCES pa_portfolio_snapshots(id),   -- baseline runs
    scenario_id     BIGINT      REFERENCES pa_scenarios(id),             -- what-if runs
    policy_id       BIGINT      REFERENCES pa_policies(id),
    inputs          JSONB       NOT NULL,   -- prices, signals(+session ids, freshness), params, covariance hash
    proposal        JSONB       NOT NULL,   -- OptimizationProposal (actions, before/after, metric deltas)
    solver_status   TEXT,                   -- 'optimal' | 'infeasible_relaxed' | 'heuristic_fallback'
    engine_version  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pa_proposals_conv
    ON pa_optimization_proposals (conversation_id, created_at DESC);

-- 9.7 Copilot domain events — trace stream for the admin Trace Inspector
-- (pre-flight finding #1: agent_events has a hard FK to analysis_sessions and
-- AgentEventPublisher is ticker-scoped, so copilot turns CANNOT reuse it). This
-- table mirrors the agent_events SHAPE but keys on conversation_id, not session.
CREATE TABLE IF NOT EXISTS pa_events (
    id                BIGSERIAL   PRIMARY KEY,
    conversation_id   UUID        NOT NULL REFERENCES pa_conversations(id) ON DELETE CASCADE,
    message_id        BIGINT,                 -- the turn this event belongs to (nullable)
    event_type        TEXT        NOT NULL,   -- e.g. pa.turn.start, pa.policy_compiled, pa.scenario_forked
    stage             TEXT,                   -- extracting | signals | optimizing | ...
    payload           JSONB,                  -- structured event detail
    logged_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pa_events_conv
    ON pa_events (conversation_id, id);
