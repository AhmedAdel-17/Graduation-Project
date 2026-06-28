-- =============================================================================
-- EGX Trading System — Schema migration v3
-- =============================================================================
-- Idempotent. Safe to run repeatedly (all statements use IF NOT EXISTS).
--   psql "$POSTGRES_URL" -f scripts/db/apply_schema_v3.sql
--
-- Purpose:
--   1. Mark every analysis session as a 'live' run or a 'backtest'-interior run
--      so the dashboard "My Analyses" history shows ONLY genuine live runs and
--      never the per-interval analyses the backtester emits.
--   2. Give backtest_runs an owner column (per-user history once Firebase auth
--      lands) — analysis_sessions already has user_id.
--   3. Seed a minimal users table keyed by Firebase UID for the upcoming auth
--      step (FastAPI verifies the Firebase ID token, upserts here, and stamps
--      user_id on every analysis_sessions / backtest_runs row).
-- =============================================================================

-- 1. Live vs backtest discriminator on analysis sessions ----------------------
ALTER TABLE analysis_sessions
    ADD COLUMN IF NOT EXISTS run_type TEXT NOT NULL DEFAULT 'live';

-- Backfill: any session that shares a (ticker, trade_date intersection) with a
-- backtest is impossible to know retroactively, so we leave historic rows at the
-- 'live' default. Going forward the backtester stamps 'backtest' explicitly.
CREATE INDEX IF NOT EXISTS idx_sessions_run_type
    ON analysis_sessions (run_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_user
    ON analysis_sessions (user_id, created_at DESC);

-- 2. Ownership + run timing on backtest_runs ----------------------------------
ALTER TABLE backtest_runs
    ADD COLUMN IF NOT EXISTS user_id TEXT;
CREATE INDEX IF NOT EXISTS idx_backtest_user
    ON backtest_runs (user_id, created_at DESC);

-- 3. Firebase users (auth lands next step) ------------------------------------
-- firebase_uid is the Firebase Authentication UID (TEXT, NOT a UUID). user_id
-- columns elsewhere store this same value. 'local' remains the single-user
-- demo owner until the token guard is switched on.
CREATE TABLE IF NOT EXISTS users (
    firebase_uid  TEXT        PRIMARY KEY,
    email         TEXT,
    display_name  TEXT,
    photo_url     TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Demo owner so existing rows have a valid referent once FKs are added later.
INSERT INTO users (firebase_uid, display_name)
VALUES ('local', 'Local Demo User')
ON CONFLICT (firebase_uid) DO NOTHING;
