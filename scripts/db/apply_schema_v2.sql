-- =============================================================================
-- EGX Trading System — Schema v2 (additive migration)
-- =============================================================================
-- Apply once over a database already initialized with db_schema.sql.
-- All operations are idempotent (IF NOT EXISTS / IF EXISTS guards) so this
-- file is safe to re-run.
--
--   psql -U postgres -d egx_trading -f scripts/db/apply_schema_v2.sql
--
-- Rolling this out without Alembic is deliberate (see MEMORY.md §F). PR 9
-- documents the migration path; Alembic is a Week-3/4 hardening item.
-- =============================================================================

-- ─── analysis_sessions: add user_id (auth) + model_fingerprint (determinism) ─
ALTER TABLE analysis_sessions
    ADD COLUMN IF NOT EXISTS user_id           TEXT  NULL,
    ADD COLUMN IF NOT EXISTS model_fingerprint JSONB NULL;

-- ─── agent_events: same model_fingerprint column for per-row audit ──────────
ALTER TABLE agent_events
    ADD COLUMN IF NOT EXISTS model_fingerprint JSONB NULL;

-- Composite index used by `/api/audit-log` lookups by ticker+date.
CREATE INDEX IF NOT EXISTS idx_sessions_user
    ON analysis_sessions (user_id);

-- =============================================================================
-- Verification queries (run after migrating to confirm the new columns exist):
--   \d analysis_sessions
--   \d agent_events
-- =============================================================================
