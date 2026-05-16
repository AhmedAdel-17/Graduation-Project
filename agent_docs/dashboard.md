# Dashboard — Backend perspective

> Companion to `dashboard/docs/*.md` and `dashboard/README.md`.
> Audience: backend / FastAPI maintainers reviewing dashboard-driven endpoint
> changes.

The dashboard at `dashboard/` (React 19 + Vite + TS) consumes the FastAPI
server at `server/api_server.py`. This file documents the contract surface
the UI depends on, the endpoints that were added during the PR1→PR10
redesign, and the security caveats that ship with them.

---

## 1. Endpoints added for the redesign

All read-only (the only writer is the pre-existing `PUT /api/config`), all
Pydantic-validated where applicable, all degrade to `200 + {source: "none"}`
rather than `500` on infrastructure failures.

| Endpoint | PR | Purpose | Source |
|---|---|---|---|
| `GET /api/sessions/{session_id}/trace` | PR3 | Reasoning trace for one session | Postgres `analysis_sessions.full_state` + `agent_events`, falls back to `audit_logs/<ticker>/audit_log.jsonl` |
| `GET /api/memory/{agent_name}/search` | PR5 | Top-K memory search | Chroma collection via `FinancialSituationMemory.get_memories()` |
| `GET /api/memory/{agent_name}/entries` | PR5 | Dump seeded vs learned entries | Chroma collection scan, BM25 fallback |
| `GET /api/reflections` | PR5 | Walks all 5 agent collections for `memory_type='reflection'` | Chroma |
| `GET /api/rl/status` | PR7 | RL meta-policy flag + loaded fingerprint | Env (`RL_META_POLICY_ENABLED`, `RL_MODEL_PATH`) + best-effort `RLSizingPolicy.load()` |
| `GET /api/rl/decisions` | PR7 | List `rl_meta_size_adjustment` events | Postgres `agent_events` LEFT JOIN `analysis_sessions` (for ticker filter) |
| `GET /api/diagnostics/prompts` | PR9 | Parsed PROMPTS.md catalog (id, title, line) | Regex parse of `PROMPTS.md`, cached 60 s in-process |
| `GET /api/diagnostics/fingerprints` | PR9 | Distinct model_fingerprint values over N days | Postgres `agent_events.model_fingerprint` JSONB |

The dashboard sticks to a tight contract for new endpoints: shape is
`{source: <provenance>, [reason]: <string when source==="none">, …}` so the
frontend can render an EmptyState with a usable reason instead of swallowing
a 500.

### 1.1 No new mutations

The only mutation the dashboard performs is the pre-existing
`PUT /api/config`, exposed in the Settings page. Backend changes that affect
this endpoint (new keys, redaction rules) need to be mirrored in:
- `dashboard/src/services/api/types.ts` — `ConfigUpdateRequest` /
  `ConfigUpdateResponse`
- `dashboard/src/features/settings/SettingsPage.tsx` — form schema +
  baseline calculation

---

## 2. WebSocket protocol the client expects

`/api/analyze` is consumed by `useAgentStream` in `dashboard/src/hooks/`.
Client expectations:

1. **Opening frame from client**: a single JSON object with
   `{ticker, trade_date, selected_analysts[], max_debate_rounds,
   max_risk_rounds}`. The backend already accepts this shape.
2. **Server frames**: JSON with `{type, node, status, state_keys[], data{},
   timestamp}`. `type` ∈ `{"agent_update", "complete", "error"}`. `status`
   ∈ `{"idle", "in_progress", "completed", "error"}`.
3. **No seq, no replay**. The client treats stream loss as
   irrecoverable. Adding `seq` server-side enables replay (planned, see
   plan §2.8).
4. **Close codes**: `1000` (normal) ends the run cleanly. Any non-1000
   close puts the client into `error` status. The TopBar ribbon shows the
   close reason verbatim.

Keep the contract stable. If you must change frame shape, do so
additively (new optional fields) and version the frame with `protocol_version`.

---

## 3. Auth-deferred caveat

The dashboard sends every request without an `Authorization` header and
talks to a `CORSMiddleware(allow_origins=["*"])` backend. **This is the
documented stabilization state (MEMORY.md §E)**. Two implications:

1. **The new endpoints have the same access posture as the existing ones.**
   `/api/diagnostics/fingerprints` returns Postgres data that could be
   sensitive in a real deployment. Don't surface this on a public origin
   until JWT auth lands and the CORS allow-list is tightened to the
   dashboard's actual URL.
2. **The client wrapper has a slot for `Authorization`**
   (`services/api/client.ts`). When the JWT story lands, the dashboard
   just needs a refresh-token hook; no endpoint changes are required.

If you ship the dashboard publicly before auth lands, at minimum:

- Restrict CORS to the dashboard origin (not `*`).
- Add Cloudflare / nginx-level rate limiting in front of `POST /api/test/random-egx`
  and `POST /api/backtests/run` — both of these can run multi-minute LLM jobs.
- Consider a read-only mode for diagnostics endpoints (drop everything
  except `/api/health`).

---

## 4. Backend invariants the UI assumes

Things that, if changed, will silently break the UI:

| Invariant | Where the UI depends on it |
|---|---|
| `analysis_sessions.session_id` is unique and quotable in a URL | `/sessions/:id`, `/backtest/:runId` deep links |
| `agent_events.event_type='rl_meta_size_adjustment'` for RL rows | RL panel filter in Backtest Detail |
| `model_fingerprint` is a JSONB column on `agent_events` (added by `scripts/db/apply_schema_v2.sql`) | Fingerprint drift query |
| `agent_events.structured_output` carries the parsed agent output | Reasoning panel JSONViewer |
| `PROMPTS.md` lives at the repo root and uses `### P-<ID> — <title>` headings | Diagnostics > Prompts |
| Health degrades gracefully (always returns `200` with `status: "ok"` + a `degraded` flag, never `500`) | TopBar pill, Diagnostics health card |
| `/api/results` sorts sessions by `timestamp DESC` | History tab and Universe "Last analyzed" rely on `[0]` being newest |
| Backtest reports under `backtest_results/` use the legacy keys (`Total Return`, `Sharpe Ratio`, `Max Drawdown`, `Win Rate`) | `_normalize_llm_report` translates them to the dashboard shape |

If you change any of these, search `dashboard/src/` for the consumer and
update both sides in the same PR.

---

## 5. Data shapes worth knowing

- **`SessionTraceResponse`** — server side returns one of three sources:
  `postgres` (full row), `jsonl` (decoded from audit log), or `none` (404
  for missing trace). Client renders all three but shows a "JSONL fallback"
  hint when source is `jsonl`.
- **`BacktestTrade`** — RL fields (`rl_size_multiplier`,
  `rl_action_index`, `rl_model_fingerprint`, `rl_feature_version`,
  `rl_meta_policy_enabled`) are present only when the RL flag was on at
  run time. The trade table conditionally shows the extra columns.
- **`HealthResponse.diagnostics`** — three top-level blocks (`memory`,
  `postgres`, `redis`) plus `degraded` + `degraded_reasons`. The
  dashboard exposes all of it on `/diagnostics`.

---

## 6. Test coverage

Every new endpoint has at least one test in `tests/test_api_<endpoint>.py`:

- `tests/test_api_session_trace.py` — JSONL fallback, 404, Postgres path
- `tests/test_api_memory.py` — allowlist, search routing, Chroma dump,
  BM25 fallback, reflections fan-out
- `tests/test_api_rl.py` — status disabled / load-failure, decisions
  empty / serialize / query-failure
- `tests/test_api_diagnostics.py` — missing PROMPTS.md, parse + slash
  split, 60 s cache, no-Postgres, query-failure, JSONB→dict

Run them as part of any PR that touches the FastAPI side:

```bash
python -m pytest tests/test_api_session_trace.py \
                 tests/test_api_memory.py \
                 tests/test_api_rl.py \
                 tests/test_api_diagnostics.py -v
```

19 tests; ~12 s wall clock.

---

## 7. What's NOT in scope

- Live order execution / broker integration.
- PDF export of audit trails (CSV may follow if requested).
- Bloomberg / Refinitiv / EGX-direct vendor swaps.
- Mobile-native app shell.
- Light theme.
- RL flag flip — the UI shows status only; gating is server-side and
  remains default OFF until MEMORY.md §4b clears.
