# Database & Memory Infrastructure — Operator Reference

> **Audience.** Engineers running this repo locally or in staging, plus auditors who need to reconstruct any decision. **Source of truth** is `CLAUDE.md` for conventions and `MEMORY.md` for known issues; this file is the practical operator guide.
>
> **Last updated:** 2026-05-14 (PR 9 of the DB-infra pass).

---

## 1. What store is responsible for what

| Store | Role | Required? | Source of truth | Wired via |
|---|---|---|---|---|
| **PostgreSQL** | Audit trail (`analysis_sessions`, `agent_events`), backtest history (`backtest_runs`, `backtest_trades`), opt-in vector memory (`agent_memories`) | Required for audit + backtest persistence; optional for vector memory | `db_schema.sql` + `scripts/db/apply_schema_v2.sql` | `tradingagents/db/connection.py` (pool) → `audit_writer.py`, `backtest_writer.py`, `persistent_memory.py` |
| **ChromaDB (PersistentClient)** | Default vector memory for the 5 agent collections (bull, bear, trader, invest_judge, risk_manager) | Required | `${CHROMA_PERSIST_DIR:-./chroma_db}` (gitignored) | `tradingagents/agents/utils/memory.py` |
| **BM25 corpus (in-memory)** | Keyword fallback when embeddings are disabled (DeepSeek/Groq) or the Chroma collection is empty | Always present | `tradingagents/agents/utils/seed_memories.py` (rebuilt per-process from seeds + reflection writes) | Same module as Chroma |
| **Redis** | WebSocket pub/sub for live agent progress events | Optional (graceful no-op) | N/A | `redis_pubsub.py` |
| **DiskCache (SQLite)** | TTL cache for OHLCV / news / fundamentals data-vendor responses | Required | `tradingagents/dataflows/data_cache/_diskcache/` (gitignored) | `tradingagents/dataflows/cache_manager.py` |
| **Local JSON** | Human-readable per-session debugging snapshots; backtest reports | Optional (Postgres is source of truth post-PR 5/6) | `eval_results/`, `backtest_results/` (gitignored) | `trading_graph._log_state`, `scripts/backtester.save_results` |
| **MongoDB** | — | **Declined** (not added) | N/A | Postgres JSONB covers the dynamic-log case at current scale |

---

## 2. Environment variables

```bash
# Required when you want audit / backtest writes to land in Postgres
POSTGRES_URL=postgresql://user:password@localhost:5432/egx_trading

# Vector memory backend — defaults to "chroma". Set to "postgres" only with
# pgvector installed AND db_schema.sql + apply_schema_v2.sql applied.
TRADINGAGENTS_MEMORY_BACKEND=chroma

# ChromaDB persistent path. Defaults to ./chroma_db (gitignored).
CHROMA_PERSIST_DIR=./chroma_db

# Memory retrieval threshold — drop matches below this cosine similarity.
# Range [0, 1]. Default 0.30. Set 0.0 to disable; higher = stricter.
MEMORY_MIN_SIMILARITY=0.30

# Optional — WebSocket event streaming. Falls back to silent no-op if absent.
REDIS_URL=redis://localhost:6379
```

See [.env.example](../.env.example) for the canonical list (including the LLM keys and the security note on rotation).

---

## 3. First-time setup

### 3a. Without Postgres (default ChromaDB-only path)

Works out of the box. The first time you run anything:

1. `uv sync` (installs `chromadb`, `diskcache`, the LLM clients, etc.).
2. `python run_egx_prediction.py COMI.CA` (or `main.py`).
3. `./chroma_db/` is created on first agent memory write. It survives restarts.
4. `/api/health` reports `memory.chroma_persistent: true`, `memory.seeded: {bull_memory: true, ...}` (after the first ever read, seeds load lazily on collection init).

No Postgres → audit writes are skipped silently, backtest results stay in `backtest_results/*.json` only. Health reports `postgres.configured: false`.

### 3b. With Postgres (audit + backtest mirror)

```bash
# Install the optional extra (psycopg2-binary + pgvector).
uv pip install -e ".[postgres]"

# Create the database.
createdb -U postgres egx_trading

# Apply the base schema (8 tables, 2 views).
psql -U postgres -d egx_trading -f db_schema.sql

# Apply v2 additions (user_id, model_fingerprint on audit tables).
psql -U postgres -d egx_trading -f scripts/db/apply_schema_v2.sql

# Set the URL.
export POSTGRES_URL="postgresql://postgres:CHANGEME@localhost:5432/egx_trading"

# Verify health.
curl -s http://localhost:8000/api/health | jq '.diagnostics.postgres'
# → { "configured": true, "reachable": true, "audit_write_lag_seconds": null,
#     "backtest_runs_count": 0, ... }
```

Any subsequent `propagate()` call writes one `analysis_sessions` row + up to 13 `agent_events` rows. Any `scripts/backtester.py` run writes one `backtest_runs` row + N `backtest_trades` rows.

### 3c. Switching to pgvector memory (opt-in)

Only attempt this after step 3b is working:

```bash
# Install pgvector (Linux). Windows users typically stay on ChromaDB.
sudo apt install postgresql-15-pgvector
psql -d egx_trading -c "CREATE EXTENSION vector;"

export TRADINGAGENTS_MEMORY_BACKEND=postgres
```

The Postgres-backed `PersistentAgentMemory` falls back to ChromaDB at runtime if pgvector is missing or the extension can't be created. Watch `tradingagents.memory` logs for the fallback line.

---

## 4. Schema migrations

There is no Alembic yet (MEMORY.md §F, Week-3/4 ship item). Migrations are hand-applied SQL files:

| File | Purpose |
|---|---|
| `db_schema.sql` | Base schema — 8 tables, 2 views. Apply once. |
| `scripts/db/apply_schema_v2.sql` | Adds `user_id`, `model_fingerprint` on audit tables. Idempotent (`ADD COLUMN IF NOT EXISTS`). |

When you add a new schema change, create `scripts/db/apply_schema_v<N>.sql` and document it here. **Do not edit `db_schema.sql` after initial release** — it's the canonical baseline.

---

## 5. Data flow at a glance

```
propagate(company, trade_date, user_id=None)
        │
        ├── (existing) graph.invoke → final_state (JSON)
        │
        ├── _log_state(...) → eval_results/{ticker}/.../full_states_log_{date}.json
        │
        └── audit_writer:
              ├── write_analysis_session(...)   → INSERT 1 row into analysis_sessions
              └── write_agent_events(...)       → INSERT ≤13 rows into agent_events

scripts/backtester.run_backtest(ticker, start, end, ...)
        │
        ├── for each rebalance date:
        │     ├── graph.propagate(...) [writes audit rows above per-date]
        │     ├── execute_trade(...)
        │     └── self._reflection_state_queue.append((date, final_state))
        │
        ├── _evaluate_trade_outcomes(...)  → annotates forward_return_20d etc.
        │
        ├── _flush_reflection_with_forward_returns(graph, lag_days=10)
        │     └── per queued (date, state): graph._run_reflections(realized_outcome)
        │         → writes reflection rows into agent memory with outcome metadata
        │
        └── save_results(...):
              ├── JSON report → backtest_results/report_{ticker}_{ts}.json
              └── backtest_writer:
                    ├── write_backtest_run(...)    → INSERT 1 row backtest_runs
                    └── write_backtest_trades(...) → INSERT N rows backtest_trades

FinancialSituationMemory.get_memories(query, where={ticker:...}, min_similarity=0.30)
        │
        ├── embeddings_enabled = False  →  BM25 over seed corpus + reflection writes
        ├── chroma.count() == 0         →  BM25 fallback over seeds
        └── otherwise                   →  Chroma vector query (cosine)
```

---

## 6. The 5 agent memory collections

| Collection | Used by | Memory types stored |
|---|---|---|
| `bull_memory` | Bull researcher | `thesis`, `reflection` |
| `bear_memory` | Bear researcher | `thesis`, `reflection` |
| `trader_memory` | Trader | `execution`, `reflection` |
| `invest_judge_memory` | Research manager | `thesis`, `reflection` |
| `risk_manager_memory` | Risk manager | `risk_decision`, `reflection` |

Each row carries the canonical metadata block:
- `ticker` (e.g. `COMI.CA`) — filterable via `where={"ticker": ...}`
- `trade_date` (ISO date string)
- `memory_type` — one of `thesis | execution | risk_decision | reflection`
- `agent_name` — collection identifier
- `outcome` — JSON-encoded `{verdict, forward_return, horizon_days, action}` (populated by reflection batch)
- `confidence` — optional float in `[0, 1]`

Recognized keys are whitelisted in `FinancialSituationMemory.METADATA_KEYS`. Unrecognized keys are dropped silently.

---

## 7. Backups and reset

### Back up Chroma memory

```bash
tar czf chroma_db_backup_$(date +%Y%m%d).tar.gz chroma_db/
```

The directory is self-contained sqlite + parquet under the hood. Restore by extracting back into place.

### Back up Postgres

```bash
pg_dump -U postgres -d egx_trading -F c -f egx_trading_$(date +%Y%m%d).dump
```

### Reset all agent memory (full wipe)

```bash
rm -rf chroma_db/
# Or, surgically, by collection:
python -c "
import chromadb
client = chromadb.PersistentClient(path='./chroma_db')
for name in ('bull_memory','bear_memory','trader_memory','invest_judge_memory','risk_manager_memory'):
    try: client.delete_collection(name)
    except Exception: pass
"
```

After the next process restart, the seed corpus reloads automatically. Reflection rows from prior runs are gone.

### Reset audit / backtest tables (e.g., a clean evaluation run)

```sql
TRUNCATE agent_events, analysis_sessions RESTART IDENTITY CASCADE;
TRUNCATE backtest_trades, backtest_runs RESTART IDENTITY CASCADE;
```

---

## 8. Health endpoint contract

`GET /api/health` returns `diagnostics` with these fields (PR 9 surface):

```jsonc
{
  "memory": {
    "backend": "chroma",                         // or "postgres" / "pgvector"
    "vector_store": "chromadb",
    "postgres_vector_required": false,
    "chroma_persist_dir": "./chroma_db",
    "chroma_persistent": true,
    "chroma_collection_counts": {                // per-collection row counts
      "bull_memory": 6, "bear_memory": 5, ...
    },
    "chroma_total_documents": 29,
    "seeded": {                                   // True iff count ≥ expected seed size
      "bull_memory": true, "bear_memory": true, ...
    },
    "min_similarity": 0.30
  },
  "postgres": {
    "configured": true,
    "reachable": true,
    "audit_write_lag_seconds": 47,                // null when table empty
    "backtest_runs_count": 7,
    "purpose": "audit/backtest persistence only unless memory_backend=postgres"
  },
  "redis": {
    "configured": false, "package_available": true, "reachable": null,
    "purpose": "optional websocket progress streaming"
  },
  "degraded": false,
  "degraded_reasons": []                          // see below
}
```

### Possible `degraded_reasons`

| Reason | Meaning |
|---|---|
| `postgres_vector_memory_unavailable` | `memory_backend=postgres` but Postgres TCP probe failed |
| `redis_streaming_unavailable` | `REDIS_URL` set but Redis can't be reached |
| `chroma_memory_in_memory_only` | `memory_backend=chroma` but `CHROMA_PERSIST_DIR` is unset → restart-fragile |
| `chroma_collections_unseeded` | Chroma is persistent but none of the 5 agent collections have any rows — seed loading likely failed |

---

## 9. Where to read more

- `CLAUDE.md` — coding standards, EGX-specific constraints, common commands.
- `MEMORY.md` — open + resolved technical-debt entries; the 4-week ship plan.
- `db_schema.sql` — canonical baseline schema with inline comments.
- `tradingagents/db/connection.py` — pool + `cursor()` context manager; read this before adding a new writer.
- `tradingagents/agents/utils/memory.py` — full retrieval semantics including the BM25 fallback path.
- `tradingagents/agents/utils/seed_memories.py` — the curated EGX precedent corpus.
- `scripts/db/apply_schema_v2.sql` — additive migration applied after `db_schema.sql`.
