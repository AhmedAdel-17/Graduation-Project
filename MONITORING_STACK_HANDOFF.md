# Monitoring Stack — Agent Handoff Document

> **Audience:** Claude agent sessions working on this repo.
> **Date:** 2026-06-29
> **Scope:** Everything related to observability, metrics, logging, health probes, data freshness, event streaming, and the monitoring infrastructure.

---

## 1. Overview

The monitoring stack provides **end-to-end observability** for the EGX Multi-Agent Trading Pipeline. It covers:

- **Prometheus metrics** — 25+ custom metrics across LLM, graph nodes, pipeline, HTTP, and data freshness
- **Structured JSON logging** — with correlation context (session_id, ticker, trade_date)
- **Per-node instrumentation** — wall-clock timing + status for every LangGraph node
- **LLM cost tracking** — per-call token counts and USD cost estimation
- **Write-only audit trail** — `NodeRecorder` writes one JSON per (ticker, trade_date, node) for reproducibility
- **Data freshness tracking** — per-source age reporting with EGX market-closed awareness
- **Real-time event streaming** — Redis pub/sub with in-memory ring-buffer fallback
- **Health/readiness probes** — `/live`, `/ready`, `/api/health` for orchestrators
- **Dashboard monitoring page** — React page polling system status, metrics, and freshness
- **Docker Compose infrastructure** — Prometheus, Grafana, Loki, Promtail

---

## 2. File Map

### Core observability package: `tradingagents/observability/`

| File | Purpose |
|---|---|
| `__init__.py` | Re-exports: `setup_logging`, `set_trace_context`, `metered_node`, `MetricsCallbackHandler` |
| `metrics.py` | **Single source of truth** for all Prometheus metric definitions (Counters, Gauges, Histograms). Import from here — never define metrics inline elsewhere. |
| `llm_metrics.py` | `MetricsCallbackHandler` — LangChain `BaseCallbackHandler` that emits per-call LLM metrics (latency, tokens, cost, success/error). Uses `_current_node` ContextVar for per-node attribution. |
| `node_metrics.py` | `metered_node(node_fn, node_name)` — wraps LangGraph node functions to measure duration + status. Sets `_current_node` ContextVar read by `MetricsCallbackHandler`. |
| `model_pricing.py` | `estimate_cost(model, input_tokens, output_tokens)` — USD cost lookup for DeepSeek, OpenAI, Anthropic models. Returns 0.0 for unknown models. |
| `middleware.py` | `PrometheusMiddleware` — Starlette middleware for HTTP request metrics. Normalizes paths (UUIDs, tickers) to prevent label cardinality explosion. |
| `logging_config.py` | `setup_logging()` — structured JSON logging via `python-json-logger`. `set_trace_context()` / `get_trace_context()` — ContextVar-based correlation ID injection into every log record. File output via `RotatingFileHandler` (10 MB, 5 backups). |

### Audit trail: `tradingagents/graph/node_record.py`

| Class/Function | Purpose |
|---|---|
| `NodeRecord` (dataclass) | Single node invocation record: identity, status (`success`/`fallback`/`error`/`skipped`), input/output hashes, raw LLM output, model provenance (model_id, temperature, seed), wall clock timing, context items. |
| `NodeRecorder` | Write-only recorder. Creates `{records_dir}/{run_id}/{ticker}/{trade_date}/{node_name}.json`. Atomic writes (temp file + `os.replace`). Never crashes the backtest on write failure. |
| `hash_string()` / `hash_state_slice()` / `hash_config_subset()` | Deterministic SHA-256 hashing for reproducibility verification. |
| `strip_private_state_keys()` | Removes `_`-prefixed infrastructure keys before serializing state to external systems. |
| `get_recorder(state)` | Extracts `NodeRecorder` from graph state's `_node_recorder` key. |

### Data freshness: `server/data_freshness.py`

- Per-source age reporting for: price, fundamentals, news, social, macro, memory
- EGX market-closed awareness (Sun–Thu 10:00–14:30 Cairo; Fri+Sat = weekend)
- Statuses: `Fresh`, `Fresh (market closed)`, `Stale`, `Missing`, `Available`
- Pushes to Prometheus gauges (`data_freshness_age_seconds`, `data_stale_current`, `data_missing_current`)
- Consumed by `GET /api/data-freshness` and the dashboard monitoring page

### Event streaming: `redis_pubsub.py`

| Component | Purpose |
|---|---|
| `AgentEventPublisher` | Sync publisher with named methods: `prefetch_started()`, `analyst_started()`, `debate_started()`, `trader_started()`, `risk_check_started()`, `signal_emitted()`, etc. All fire-and-forget. |
| `AgentEventSubscriber` | Async context manager for WebSocket handlers. |
| In-memory ring buffer | `MAX_EVENTS_PER_CHANNEL = 100`. `get_buffered_events(ticker)` for replay. Works without Redis. |
| Event schema | `{"event": str, "stage": str, "message": str, "timestamp": ISO8601, "session_id": str, "ticker": str}` |

### Server endpoints (in `server/api_server.py`)

| Endpoint | Method | Purpose |
|---|---|---|
| `/live` | GET | Liveness probe — PID, uptime, timestamp. No external deps checked. |
| `/ready` | GET | Readiness probe — checks LLM API key, ChromaDB path, EGX tools importable. Returns 503 if any fail. |
| `/api/health` | GET | Runtime diagnostics — memory backend info, Postgres reachability, Redis status, degradation reasons. |
| `/metrics` | GET | Standard Prometheus exposition format. Scraped every 15s. |
| `/api/metrics-summary` | GET | Aggregated metrics for dashboard: active sessions, signal counts, LLM call breakdown, WebSocket count, uptime. |
| `/api/system-status` | GET | Service URLs (API, Grafana, Prometheus, Loki, Redis) from env vars. |
| `/api/data-freshness` | GET | Per-source freshness report from `data_freshness.py`. |
| `/api/events` | GET | Buffered pipeline events replay (optional ticker filter, limit 1–200). |

### Dashboard: `dashboard/src/features/monitoring/MonitoringPage.tsx`

React component with auto-polling sections:
1. **Service health grid** — API, Redis, Postgres, Grafana, Prometheus, Loki status
2. **Data freshness panel** — per-source age and staleness indicators
3. **External tool links** — clickable Grafana/Prometheus/Loki URLs
4. **Pipeline metrics** — active sessions, run count, avg duration, signal distribution
5. **Memory & storage** — ChromaDB config, embeddings mode, retrieval method
6. **LLM call breakdown** — by agent and model
7. **Last recommendation** — shadow run info

Polling intervals: system-status 15s, health 15s, metrics 10s, data-freshness 30s.

### Infrastructure: `monitoring/`

| File | Purpose |
|---|---|
| `docker-compose.yml` | Orchestrates Prometheus (v2.53, port 9090, 30d retention), Grafana (v11.1, port 3000, admin/admin), Loki (v3.1, port 3100), Promtail (v3.1, tails `logs/*.log`). |
| `prometheus/prometheus.yml` | Scrape config — hits `/metrics` every 15s. `host.docker.internal` for local dev. |
| `prometheus/alerts.yml` | 9 alert rules (see section 5 below). |
| `grafana/provisioning/` | Auto-provisions Prometheus + Loki as Grafana datasources. |
| `grafana/dashboards/` | Dashboard JSON directory (placeholder — pre-built dashboard not yet populated). |
| `loki/loki-config.yml` | Loki storage + retention config. |
| `promtail/promtail-config.yml` | Tails `logs/*.log`, ships to Loki. |

### Tests

| File | Covers |
|---|---|
| `tests/test_observability.py` | JSON logging, ContextVar injection, file rotation, `metered_node` success/error/contextvar, `MetricsCallbackHandler` token extraction, model pricing, middleware path normalization, fundamentals gauges |
| `tests/test_node_record.py` | Hash determinism, `NodeRecord` serialization, atomic writes, status validation, `prompt_text` gating |
| `tests/test_node_record_integration.py` | Full backtest recording flow end-to-end |
| `tests/test_decision_quality_metrics.py` | Monitoring of analysis quality metrics |

---

## 3. Metrics Catalog

All metrics use the `tradingagents_` prefix.

### LLM metrics
| Metric | Type | Labels | Description |
|---|---|---|---|
| `llm_calls_total` | Counter | agent_name, model, status | Total LLM API calls |
| `llm_latency_seconds` | Histogram | agent_name, model | Call latency (buckets: 0.5–120s) |
| `llm_input_tokens_total` | Counter | agent_name, model | Input tokens consumed |
| `llm_output_tokens_total` | Counter | agent_name, model | Output tokens consumed |
| `llm_cost_usd_total` | Counter | model | Estimated USD cost |
| `llm_failover_total` | Counter | agent_name, provider | Provider failover events |

### Graph node metrics
| Metric | Type | Labels | Description |
|---|---|---|---|
| `node_duration_seconds` | Histogram | node_name | Per-node wall-clock time (buckets: 0.5–300s) |
| `node_status_total` | Counter | node_name, status | success/error counts per node |
| `node_parse_errors_total` | Counter | node_name | JSON/output parse failures (incremented explicitly) |

### Pipeline metrics
| Metric | Type | Labels | Description |
|---|---|---|---|
| `pipeline_duration_seconds` | Histogram | — | Full `propagate()` duration (buckets: 30–600s) |
| `signal_total` | Counter | signal | BUY/SELL/HOLD signal counts |
| `risk_veto_total` | Counter | veto_reason | Risk manager vetoes by reason |
| `active_analysis_sessions` | Gauge | — | Currently running sessions |

### HTTP metrics
| Metric | Type | Labels | Description |
|---|---|---|---|
| `http_requests_total` | Counter | method, endpoint, status_code | Total HTTP requests |
| `http_request_duration_seconds` | Histogram | method, endpoint | Request duration (buckets: 10ms–60s) |
| `active_websocket_connections` | Gauge | — | Active WebSocket connections |

### Data freshness metrics
| Metric | Type | Labels | Description |
|---|---|---|---|
| `data_freshness_age_seconds` | Gauge | source, ticker | Age of most recent data |
| `data_fetch_last_success_timestamp` | Gauge | source, ticker | Unix timestamp of last success |
| `data_stale_current` | Gauge | source | 1=stale, 0=ok |
| `data_missing_current` | Gauge | source | 1=missing, 0=ok |
| `data_fetch_errors_total` | Counter | source | Fetch errors by source |
| `data_fetch_total` | Counter | data_type, source, status | Fetch attempts by outcome |
| `data_fetch_latency_seconds` | Histogram | data_type, source | Fetch latency |

### Fundamentals data quality gauges
| Metric | Type | Description |
|---|---|---|
| `fundamentals_stale_tickers` | Gauge | Tickers with data >90d old |
| `fundamentals_stub_tickers` | Gauge | Tickers with 0 annual periods |
| `fundamentals_cbe_provisional_rows` | Gauge | CBE rows with PROVISIONAL status |
| `fundamentals_dividend_yield_coverage_ratio` | Gauge | Fraction of tickers with dividend data |
| `fundamentals_pe_sparse_tickers` | Gauge | Tickers with <80% PE ratio coverage |

### Process
| Metric | Type | Description |
|---|---|---|
| `process_start_time_seconds` | Gauge | Server start timestamp (Grafana uptime convention) |

### Label cardinality rules
- **Allowed:** agent_name, node_name, model, provider, status, endpoint, signal, data_type, source, direction, veto_reason
- **Bounded:** ticker (only on fundamentals gauges, ~30 EGX tickers)
- **Forbidden:** session_id, run_id, prompt_hash, raw_error, raw_url, trade_date

---

## 4. Integration Points — How Monitoring is Wired

### A. LLM objects (`tradingagents/graph/trading_graph.py`)
- Lines ~130–143: Every LLM object (OpenAI/DeepSeek, Anthropic, Google) gets `callbacks=[MetricsCallbackHandler()]` at construction time.
- `propagate()` method:
  1. Calls `set_trace_context(session_id, ticker, trade_date)` for log correlation
  2. Increments `active_analysis_sessions` gauge
  3. Initializes `_node_recorder` in state
  4. Publishes events via `AgentEventPublisher`
  5. Records final signal to `signal_total` metric
  6. Observes `pipeline_duration_seconds`

### B. Graph nodes (`tradingagents/graph/setup.py`)
- Lines ~173–194: Every node wrapped with `metered_node()`:
  - Analysts: market, fundamentals, news, social
  - Researchers: bull, bear
  - Managers: research_manager
  - Trader
  - Risk: merged_debator, risk_manager
- `metered_node()` sets `_current_node` ContextVar → any LLM call within that node is automatically attributed to the correct `node_name` in metrics.

### C. HTTP server (`server/api_server.py`)
- `PrometheusMiddleware` added to FastAPI app for automatic request metrics
- `/metrics` endpoint exposes Prometheus exposition format
- `/api/metrics-summary` aggregates from Prometheus client registry for dashboard consumption

### D. Individual agents
Each agent (bull_researcher, bear_researcher, market_analyst, etc.) calls `get_recorder(state)` to obtain the `NodeRecorder` and writes its own record with status, raw output, timing, and hashes. The recorder is **optional** — agents check for `None` and skip recording gracefully.

### E. Backtester (`scripts/backtester.py`)
- Creates a `NodeRecorder` per ticker with a unique `run_id`
- Passes it into state as `_node_recorder`
- Records are written to `backtest_records/{run_id}/{ticker}/{trade_date}/{node_name}.json`
- Recording failures never crash the backtest (best-effort)

---

## 5. Alert Rules (`monitoring/prometheus/alerts.yml`)

| Alert | Condition | Severity | For |
|---|---|---|---|
| `DataSourceStale` | `data_stale_current > 0` | warning | 30m |
| `DataSourceMissing` | `data_missing_current > 0` | critical | 15m |
| `LLMHighErrorRate` | >10% error rate over 10m | warning | 5m |
| `LLMFailoverActive` | failover event in 15m | info | 1m |
| `PipelineDurationHigh` | p95 >600s over 30m | warning | 10m |
| `HighHoldRate` | >90% HOLD signals in 1h | info | 30m |
| `RiskVetoSpike` | >5 vetoes in 1h | info | 5m |
| `APIHighErrorRate` | >5% 5xx in 5m | warning | 5m |
| `APIHighLatency` | p95 >10s in 5m | warning | 5m |

**Status:** Rules are evaluated by Prometheus. Alertmanager delivery (Slack/email) is **not yet configured** — Phase 3 future work.

---

## 6. Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `STOCKHIVE_API_URL` | `http://localhost:8000` | API server base URL (for dashboard) |
| `STOCKHIVE_GRAFANA_URL` | `http://localhost:3000` | Grafana link in dashboard/system-status |
| `STOCKHIVE_PROMETHEUS_URL` | `http://localhost:9090` | Prometheus link |
| `STOCKHIVE_LOKI_URL` | `http://localhost:3100` | Loki readiness check |
| `REDIS_URL` | `redis://localhost:6379` | Redis pub/sub for event streaming |
| `TRADINGAGENTS_LOG_FILE` | (empty) | Set to `auto` for `./logs/tradingagents.log`, or a custom path |

---

## 7. Running the Monitoring Stack

```bash
# Start Prometheus + Grafana + Loki + Promtail
cd monitoring && docker compose up -d

# Start the API server (exposes /metrics on :8000)
uvicorn server.api_server:app --reload --port 8000

# Start the dashboard (monitoring page at /monitoring)
cd dashboard && npm run dev

# Grafana: http://localhost:3000 (admin/admin)
# Prometheus: http://localhost:9090
# Loki: http://localhost:3100
```

---

## 8. Known Limitations & Future Work

### Current limitations
1. **Alertmanager not configured** — alert rules exist but no Slack/email delivery
2. **WebSocket replay lost on restart** — in-memory ring buffer only, not durable
3. **Readiness probe is lightweight** — no network call to LLM provider
4. **Batch job metrics not exported** — backtester doesn't push to Prometheus (no Pushgateway)
5. **Grafana dashboards empty** — provisioning directory exists but no pre-built JSON
6. **`metered_node` can only detect success/error** — `fallback`, `skipped`, and `parse_error` statuses are caught internally by nodes and recorded via `NodeRecorder`, not via Prometheus counters
7. **NodeRecorder is the audit source of truth** — Prometheus metrics are operational (rates, aggregates), NodeRecorder JSON files are forensic (per-invocation detail)

### Phase 3 roadmap
1. Alertmanager + notification routing (Slack/email)
2. Full dependency health probes (LLM API roundtrip, ChromaDB query latency)
3. Durable event log via Redis Streams with consumer groups
4. Pre-built Grafana dashboard JSON provisioning
5. Distributed tracing via OpenTelemetry
6. Prometheus Pushgateway for batch job metrics

---

## 9. Design Principles

1. **"Truthful monitoring"** — metrics reflect what the system actually does, not what we wish it did. `metered_node` only reports what it can observe (success/error); `NodeRecorder` captures the richer status vocabulary (fallback/skipped/error).
2. **Never crash the pipeline for monitoring** — all recording and metrics emission is best-effort. `AgentEventPublisher` methods are fire-and-forget. `NodeRecorder.record()` catches exceptions and logs a warning.
3. **Label cardinality is capped** — forbidden labels (session_id, run_id, etc.) prevent Prometheus index explosion. HTTP paths are normalized. Ticker labels are bounded to ~30 EGX names.
4. **Graceful degradation everywhere** — works without Redis (in-memory events), without `python-json-logger` (falls back to text format), without Prometheus (metrics are no-ops if not scraped).
5. **Correlation via ContextVars** — `_trace_ctx` for logging, `_current_node` for LLM attribution. Both are thread/async-safe.
