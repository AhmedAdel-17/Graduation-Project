# StockHive Monitoring Stack

> **Audience:** Graduation thesis reviewers, presentation attendees, and developers.
> This document describes the observability infrastructure of StockHive as of June 2026.
> It is intentionally honest about what is implemented, what is lightweight, and what remains future work.

---

## 1. Executive Summary

StockHive is a multi-agent AI system that produces investment recommendations for the Egyptian Exchange (EGX). A single recommendation involves coordinating four analyst agents, two researcher agents, a trader agent, and a risk manager, each backed by large language model (LLM) API calls and multiple external data sources. This architecture creates a unique observability challenge: failures can be silent (an LLM returns a plausible-sounding but degraded answer), distributed (one agent's stale data poisons downstream decisions), and expensive (each wasted LLM call costs real money).

The monitoring stack addresses three core problems:

1. **Operational visibility.** Developers and operators need to know whether the system is healthy, whether data sources are fresh, and whether the LLM provider is responding reliably.
2. **Recommendation quality assurance.** A system that defaults to HOLD on every ticker due to parse failures or stale data is technically "working" but useless. Monitoring tracks signal distribution, pipeline duration, and risk veto rates to surface quality degradation before it becomes invisible.
3. **Audit and debugging.** When a recommendation is questioned, structured logs with correlation IDs (session, ticker, date) allow tracing the full decision path from data ingestion through agent reasoning to final signal.

The stack is designed for a research and development environment. It runs without any external monitoring infrastructure for local development, while supporting a full Prometheus + Grafana + Loki deployment when deeper observability is needed.

---

## 2. Architecture Overview

The monitoring stack has six components. Three are built into the application and always available. Three are external services deployed via Docker Compose and are optional for local development.

### Built-in Components (Always Available)

| Component | Role |
|-----------|------|
| **FastAPI observability endpoints** | The API server (`server/api_server.py`) exposes health probes (`/live`, `/ready`), a Prometheus metrics endpoint (`/metrics`), diagnostic endpoints (`/api/health`, `/api/metrics-summary`, `/api/data-freshness`, `/api/system-status`), and an event replay endpoint (`/api/events`). |
| **React Monitoring page** | The dashboard (`dashboard/src/features/monitoring/MonitoringPage.tsx`) provides a developer-facing view of system health, pipeline metrics, data freshness, and infrastructure status. It polls the API endpoints via React Query with automatic refresh intervals. |
| **In-memory event buffer** | Pipeline progress events (agent start/complete, data fetch, signal emission) are stored in a per-channel ring buffer (`redis_pubsub.py`). This allows clients that connect mid-pipeline to catch up on recent events via the `/api/events` REST endpoint. |

### External Components (Optional, Docker Compose)

| Component | Role | Default URL |
|-----------|------|-------------|
| **Prometheus** | Time-series metrics database. Scrapes the `/metrics` endpoint every 15 seconds and evaluates alerting rules. | `http://localhost:9090` |
| **Grafana** | Visualization platform. Queries Prometheus for metric dashboards and Loki for log exploration. | `http://localhost:3000` |
| **Loki + Promtail** | Log aggregation. Promtail tails the application's JSON log file and ships entries to Loki, where they can be queried by label (level, logger) or full-text search. | `http://localhost:3100` |

### Component Interaction

```
                                       +------------------+
                                       |     Grafana      |
                                       |  (visualization) |
                                       +--------+---------+
                                                |
                                    queries     |     queries
                                  +-------------+-------------+
                                  |                           |
                           +------+------+             +------+------+
                           |  Prometheus |             |    Loki     |
                           |  (metrics)  |             |   (logs)    |
                           +------+------+             +------+------+
                                  |                           |
                          scrapes |                    ships   |
                          /metrics|                    logs    |
                                  |                           |
   +------------------+    +------+------+             +------+------+
   | React Dashboard  +--->|  FastAPI    |             |  Promtail   |
   | (monitoring page)|    |  API Server |----writes-->|  (log tail) |
   +------------------+    +------+------+   JSON logs +-------------+
          polls                   |
     /api/health                  |  buffers events
     /api/metrics-summary         |  in memory
     /api/data-freshness     +----+----+
     /api/system-status      | Ring    |
     /api/events             | Buffer  |
                             +---------+
```

---

## 3. Monitoring Data Flow

### 3.1 Metrics Flow

1. **Metric emission.** Application code increments Prometheus counters, gauges, and histograms defined in `tradingagents/observability/metrics.py`. Examples include `tradingagents_llm_calls_total` (LLM API call count by agent and status), `tradingagents_pipeline_duration_seconds` (end-to-end pipeline latency), and `tradingagents_signal_total` (BUY/SELL/HOLD signal counts).

2. **HTTP middleware instrumentation.** The `PrometheusMiddleware` (`tradingagents/observability/middleware.py`) automatically records `tradingagents_http_requests_total` and `tradingagents_http_request_duration_seconds` for every API request, labeled by method, endpoint, and status code.

3. **LLM callback instrumentation.** The `MetricsCallbackHandler` (`tradingagents/observability/llm_metrics.py`) is attached to every LLM object constructed in `trading_graph.py`. It records per-call latency, token counts, estimated cost, and error status.

4. **Prometheus scrape.** Prometheus is configured (`monitoring/prometheus/prometheus.yml`) to scrape `http://host.docker.internal:8000/metrics` every 15 seconds. Scraped samples are stored in Prometheus's time-series database with 30-day retention.

5. **Alert evaluation.** Prometheus evaluates alerting rules (`monitoring/prometheus/alerts.yml`) against the scraped data. Firing alerts are visible in the Prometheus UI. Notification delivery (Slack, email) requires Alertmanager, which is not yet configured.

6. **Visualization.** Grafana queries Prometheus using PromQL to render dashboards. Custom dashboards can be created for LLM performance, pipeline health, and data freshness.

### 3.2 Logging Flow

1. **Structured log emission.** The application uses Python's standard `logging` module, configured by `tradingagents/observability/logging_config.py` to emit JSON-formatted log lines via `python-json-logger`.

2. **Correlation context.** Before each pipeline run, `set_trace_context(session_id, ticker, trade_date)` injects correlation fields into a `contextvars.ContextVar`. A `_ContextFilter` attached to every log handler copies these fields into each log record. The JSON formatter includes them in every output line:
   ```json
   {
     "timestamp": "2026-06-22T16:39:35",
     "level": "INFO",
     "logger": "tradingagents.graph.trading_graph",
     "message": "pipeline complete",
     "session_id": "abc123",
     "ticker": "COMI.CA",
     "trade_date": "2024-06-01"
   }
   ```

3. **File output.** Logs are written to `./logs/tradingagents.log` using a `RotatingFileHandler` (10 MB max, 5 backups).

4. **Log shipping.** Promtail (deployed via Docker Compose) tails `logs/*.log` (mounted as `/var/log/tradingagents/*.log` inside the container) and ships entries to Loki. Loki indexes logs by labels extracted from the JSON structure, enabling queries like `{logger="tradingagents.agents.trader"} |= "position_sizing"`.

### 3.3 Dashboard Polling Flow

The React Monitoring page polls four API endpoints at staggered intervals to build its view:

| Endpoint | Interval | Content |
|----------|----------|---------|
| `/api/system-status` | 15 s | Infrastructure service reachability (API, Redis, Postgres, Grafana, Prometheus, Loki) |
| `/api/health` | 15 s | Detailed diagnostics: memory backend, ChromaDB state, embeddings mode, Postgres audit stats |
| `/api/metrics-summary` | 10 s | Aggregated pipeline metrics: active sessions, run count, average duration, signal distribution, server uptime |
| `/api/data-freshness` | 30 s | Per-source data age and staleness status for OHLCV, fundamentals, news, social, and macro data |

All queries use React Query (TanStack Query) with automatic background refetching. The page renders loading skeletons during initial fetch, empty-state cards when the server has no data (e.g., zero pipeline runs), and error cards with retry buttons on fetch failures.

### 3.4 Event Replay Flow

1. **Event emission.** During pipeline execution, agents publish progress events (e.g., `"analyst_complete"`, `"signal_emitted"`) via the `redis_pubsub.AgentEventPublisher`.

2. **Local buffering.** Every published event is simultaneously stored in an in-memory ring buffer (Python `collections.deque`, 100 events per channel). This happens regardless of whether Redis is available.

3. **Replay on demand.** The `/api/events` endpoint returns buffered events, optionally filtered by ticker. Clients that connect after a pipeline run starts can retrieve the event trace up to that point.

4. **Live streaming.** If Redis is available, events are also published to Redis pub/sub channels. WebSocket clients subscribed to a ticker's channel receive events in real time.

---

## 4. Implemented Features

### 4.1 Health and Readiness Probes

**`GET /live`** — Liveness probe. Returns HTTP 200 with process ID and uptime if the event loop is responsive. Does not check external dependencies. Suitable for container orchestrators to detect hung processes.

```json
{
  "status": "alive",
  "pid": 34576,
  "uptime_seconds": 34.7,
  "timestamp": "2026-06-22T16:43:18.240280"
}
```

**`GET /ready`** — Readiness probe. Checks three conditions:
- LLM API key is present (`DEEPSEEK_API_KEY` or `OPENAI_API_KEY` environment variable)
- ChromaDB persistence directory exists (or in-memory mode is active)
- EGX data tools are importable

Returns HTTP 200 when all checks pass, HTTP 503 with diagnostic details when any check fails. These are lightweight configuration and file-existence checks, not network round-trips to external services.

```json
{
  "ready": true,
  "checks": {"llm_api_key": true, "chroma": true, "egx_tools": true},
  "reasons": [],
  "timestamp": "2026-06-22T16:43:21.058998"
}
```

### 4.2 Runtime Diagnostics

**`GET /api/health`** — Comprehensive diagnostic report covering:
- Memory backend configuration (ChromaDB or pgvector), collection counts, seeding status
- Embeddings provider detection and retrieval mode (`"vector"` or `"bm25_keyword"`)
- Postgres reachability and audit table statistics
- Redis reachability and package availability
- Degradation reasons (e.g., `"chroma_memory_in_memory_only"`, `"redis_streaming_unavailable"`)

### 4.3 Prometheus Metrics

**`GET /metrics`** — Standard Prometheus exposition format. All custom metrics use the `tradingagents_` prefix. The following metric families are exported:

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `tradingagents_llm_calls_total` | Counter | agent_name, model, status | Total LLM API calls |
| `tradingagents_llm_latency_seconds` | Histogram | agent_name, model | LLM call latency |
| `tradingagents_llm_input_tokens_total` | Counter | agent_name, model | Input tokens consumed |
| `tradingagents_llm_output_tokens_total` | Counter | agent_name, model | Output tokens consumed |
| `tradingagents_llm_cost_usd_total` | Counter | model | Estimated API cost in USD |
| `tradingagents_llm_failover_total` | Counter | agent_name, provider | Provider failover events |
| `tradingagents_node_duration_seconds` | Histogram | node_name | Per-node wall clock time |
| `tradingagents_node_status_total` | Counter | node_name, status | Per-node completion status |
| `tradingagents_pipeline_duration_seconds` | Histogram | — | Full pipeline duration |
| `tradingagents_signal_total` | Counter | signal | Final BUY/SELL/HOLD signals |
| `tradingagents_risk_veto_total` | Counter | veto_reason | Risk manager vetoes |
| `tradingagents_active_analysis_sessions` | Gauge | — | Currently running sessions |
| `tradingagents_http_requests_total` | Counter | method, endpoint, status_code | HTTP request count |
| `tradingagents_http_request_duration_seconds` | Histogram | method, endpoint | HTTP request latency |
| `tradingagents_active_websocket_connections` | Gauge | — | Active WebSocket connections |
| `tradingagents_data_fetch_total` | Counter | data_type, source, status | Data fetch attempts |
| `tradingagents_data_stale_current` | Gauge | source | Stale data source indicator |
| `tradingagents_data_missing_current` | Gauge | source | Missing data source indicator |
| `tradingagents_process_start_time_seconds` | Gauge | — | Server start timestamp (epoch) |

### 4.4 Pipeline Metrics Summary

**`GET /api/metrics-summary`** — JSON digest of key metrics for the dashboard, derived from the Prometheus client registry. Includes active sessions, pipeline run count, average duration, signal distribution, WebSocket connections, and server uptime.

### 4.5 Data Freshness

**`GET /api/data-freshness`** — Per-source freshness report. For each data source (OHLCV, fundamentals, news, social, macro), reports the age of the most recent data, staleness threshold, and whether the source is considered stale. Accounts for market-closed periods (weekends, after trading hours) to avoid false staleness alarms.

### 4.6 Structured JSON Logging

Application logs are emitted in JSON format with the following fields:

| Field | Source | Description |
|-------|--------|-------------|
| `timestamp` | Logger | ISO 8601 timestamp |
| `level` | Logger | INFO, WARNING, ERROR, etc. |
| `logger` | Logger | Python logger name (e.g., `tradingagents.agents.trader`) |
| `message` | Logger | Log message text |
| `session_id` | ContextVar | Pipeline session identifier (set per-run) |
| `ticker` | ContextVar | Ticker under analysis (e.g., `COMI.CA`) |
| `trade_date` | ContextVar | Analysis target date |

Context fields are empty strings when no pipeline is active, ensuring every log line has a consistent schema for Loki indexing.

### 4.7 Prometheus Alert Rules

Nine alerting rules are defined in `monitoring/prometheus/alerts.yml`, organized into four groups:

| Alert | Condition | Severity | Purpose |
|-------|-----------|----------|---------|
| `DataSourceStale` | Any data source stale for > 30 min | Warning | Detects data pipeline degradation |
| `DataSourceMissing` | Any data source completely absent for > 15 min | Critical | Detects data pipeline failure |
| `LLMHighErrorRate` | > 10% of LLM calls failing over 10 min | Warning | Detects LLM provider issues |
| `LLMFailoverActive` | Any failover event in 15 min | Info | Signals provider rotation |
| `PipelineDurationHigh` | p95 duration > 10 min over 30 min | Warning | Detects latency regression |
| `HighHoldRate` | > 90% HOLD signals over 1 hour | Info | Detects quality degradation |
| `RiskVetoSpike` | > 5 vetoes in 1 hour | Info | Detects overly restrictive risk limits |
| `APIHighErrorRate` | > 5% of HTTP requests returning 5xx over 5 min | Warning | Detects server errors |
| `APIHighLatency` | p95 response time > 10 s over 5 min | Warning | Detects API performance issues |

These rules are evaluated by Prometheus. Firing alerts are visible in the Prometheus Alerts UI (`http://localhost:9090/alerts`). Notification delivery via Alertmanager is not yet configured.

### 4.8 Config-Driven Monitoring URLs

All monitoring service URLs are configurable via environment variables, allowing deployment on non-localhost hosts without code changes:

| Variable | Default | Used For |
|----------|---------|----------|
| `STOCKHIVE_API_URL` | `http://localhost:8000` | API server base URL |
| `STOCKHIVE_DASHBOARD_URL` | `http://localhost:5173` | Dashboard dev server |
| `STOCKHIVE_GRAFANA_URL` | `http://localhost:3000` | Grafana link in dashboard |
| `STOCKHIVE_PROMETHEUS_URL` | `http://localhost:9090` | Prometheus link in dashboard |
| `STOCKHIVE_LOKI_URL` | `http://localhost:3100` | Loki readiness check |
| `STOCKHIVE_REDIS_URL` | (from config) | Redis reachability check |

### 4.9 Dashboard Monitoring Page

The React Monitoring page (`dashboard/src/features/monitoring/MonitoringPage.tsx`) is a developer-facing panel that presents system status without requiring Grafana. Key design principles:

- **Truthful display.** Data is sourced from live API responses, never hardcoded. If ChromaDB is using BM25 keyword fallback instead of vector embeddings, the page says so.
- **Graceful degradation.** Each section handles three states: loading (skeleton), empty (informational card), and error (retry button with error message).
- **Optional services.** Grafana, Prometheus, and Loki are labeled as "Optional" with a setup hint when not running, rather than displaying alarming red "Offline" badges.
- **Automatic refresh.** System status and health refresh every 15 seconds, metrics every 10 seconds, and data freshness every 30 seconds.

### 4.10 Server Uptime Metric

The `tradingagents_process_start_time_seconds` Prometheus Gauge records the Unix timestamp when the API server process started. This serves two purposes:

1. **Grafana compatibility.** Grafana's native "uptime" panel type understands this metric convention and can display process uptime automatically.
2. **Counter epoch awareness.** Since Prometheus counters are in-process and reset on server restart, the uptime metric tells operators how long the current counter epoch has been accumulating. The dashboard displays this as a human-readable "Server uptime" tile.

---

## 5. Design Decisions

### 5.1 Lightweight Readiness Probe

The `/ready` endpoint checks configuration presence (API key environment variable, ChromaDB directory, tool importability) rather than performing network calls to the LLM provider or ChromaDB queries. This is a deliberate trade-off: a readiness probe that calls the LLM API would add latency, consume API credits, and could itself trigger rate limiting. For a research system with a single developer, configuration checks catch the most common failure mode (missing or expired API key after environment reset) without the cost of full dependency probing. Full network health checks are documented as a Phase 3 enhancement.

### 5.2 Optional External Monitoring

Grafana, Prometheus, and Loki require Docker and occupy three additional ports. For local development and demonstration, the built-in `/api/health`, `/api/metrics-summary`, and `/api/data-freshness` endpoints provide sufficient visibility through the React Monitoring page. The external stack adds value for persistent metric history (Prometheus retains 30 days), cross-restart continuity, alerting, and log search. Making it optional means a developer can run `uvicorn server.api_server:app` and `npm run dev` without any Docker dependency.

### 5.3 In-Memory Event Replay

The WebSocket event buffer uses a Python `collections.deque` with a 100-event-per-channel limit. This buffer is lost on server restart. The design is intentional: event replay is a convenience feature for catching up on an active or recently completed pipeline run, not a durable audit log. The audit trail is stored separately in Postgres (shadow runs table). A durable event log (via Redis Streams) is documented as future work but adds infrastructure complexity that is not justified for the current single-user research use case.

### 5.4 Counter Reset and Prometheus

Prometheus counters (`tradingagents_llm_calls_total`, `tradingagents_signal_total`, etc.) are held in the API server's process memory. When the server restarts, counters reset to zero. This is standard Prometheus behavior: Prometheus stores the scraped values in its own time-series database, and PromQL functions like `rate()` and `increase()` handle counter resets correctly. The "Server uptime" tile on the dashboard makes the current counter epoch visible to operators.

### 5.5 Alertmanager Deferred

Prometheus alerting rules are implemented and evaluated, but Alertmanager (the component that routes firing alerts to Slack, email, or PagerDuty) is not configured. This is because the current deployment is a single-developer research environment where the Prometheus Alerts UI is sufficient for awareness. Adding Alertmanager is a configuration task (adding a service to `docker-compose.yml` and an `alertmanager.yml` routing config) that is straightforward when notification delivery becomes necessary.

### 5.6 Truthful Monitoring

The Monitoring page is designed to be informative rather than visually alarming. A common pattern in monitoring dashboards is to show red badges for any service that is not running, which creates alert fatigue in development environments where optional services are routinely absent. StockHive's approach:

- Required services that are down produce error states with actionable messages.
- Optional services that are not running show a neutral "Optional / Not running" label with a one-line setup hint.
- Warnings are data-driven: the ChromaDB "BM25 fallback" warning only appears when the health endpoint reports `embeddings_active: false`, not as a hardcoded assumption.

---

## 6. Limitations and Future Work

### Current Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| **No Alertmanager notification delivery** | Firing alerts are only visible in the Prometheus UI. No Slack, email, or webhook notifications. | Operators must check the Prometheus Alerts page manually. Adding Alertmanager is a configuration task, not a code change. |
| **WebSocket replay is not durable** | Event buffer is lost on server restart. Events from previous server sessions cannot be replayed. | The audit trail (shadow runs, Postgres) provides the permanent record. Replay is a convenience feature, not an audit mechanism. |
| **Readiness checks are lightweight** | `/ready` does not verify that the LLM provider is actually accepting requests or that ChromaDB can serve queries. A configuration-valid but network-unreachable provider would pass readiness. | The first pipeline run will surface provider issues. Full health probes are planned for Phase 3. |
| **No pre-built Grafana dashboards** | Grafana is deployed but requires manual dashboard creation. There are no provisioned panels for LLM metrics or pipeline health. | Developers can create dashboards using the documented metric names from Section 4.3. Dashboard JSON provisioning is a future task. |
| **Metrics are in-process only** | Batch jobs (backtester) do not export metrics to Prometheus. Only the API server's metrics are scraped. | Prometheus Pushgateway can be added for batch job metrics in future work. |

### Planned Enhancements (Phase 3)

1. **Alertmanager configuration.** Add `alertmanager.yml` and the Alertmanager service to `docker-compose.yml`. Configure Slack webhook or email routing for warning and critical alerts.
2. **Full dependency health probes.** Extend `/ready` with network round-trips to the LLM provider and a ChromaDB heartbeat query, behind a configurable timeout.
3. **Durable event log.** Replace the in-memory ring buffer with Redis Streams for persistent, replayable event history with consumer group support.
4. **Pre-built Grafana dashboards.** Provision JSON dashboard definitions for LLM performance, pipeline health, data freshness, and API latency.
5. **Distributed tracing.** Add OpenTelemetry spans across the multi-agent pipeline for per-request trace visualization.
6. **Batch job metrics.** Integrate Prometheus Pushgateway so that backtest runs export metrics alongside the API server.

---

## 7. Local Runbook

### 7.1 Start the Application

```bash
# Start the API server (port 8000)
uvicorn server.api_server:app --reload --port 8000

# Start the dashboard (port 5173)
cd dashboard && npm install && npm run dev
```

### 7.2 Start the Monitoring Stack

```bash
# Start Prometheus, Grafana, Loki, and Promtail
cd monitoring && docker compose up -d

# Verify all containers are running
docker compose ps
```

### 7.3 Service URLs

| Service | URL | Credentials |
|---------|-----|-------------|
| API Server | `http://localhost:8000` | None (no auth) |
| Dashboard | `http://localhost:5173` | None |
| Grafana | `http://localhost:3000` | `admin` / `admin` (change on first login) |
| Prometheus Targets | `http://localhost:9090/targets` | None |
| Prometheus Alerts | `http://localhost:9090/alerts` | None |
| Loki Readiness | `http://localhost:3100/ready` | None |

### 7.4 Verification Commands

```bash
# Liveness check
curl -s http://localhost:8000/live | python3 -m json.tool

# Readiness check (expect 200 or 503)
curl -s -w "\nHTTP %{http_code}\n" http://localhost:8000/ready | python3 -m json.tool

# Runtime diagnostics
curl -s http://localhost:8000/api/health | python3 -m json.tool

# Pipeline metrics summary
curl -s http://localhost:8000/api/metrics-summary | python3 -m json.tool

# Data freshness
curl -s http://localhost:8000/api/data-freshness | python3 -m json.tool

# Prometheus metrics (raw)
curl -s http://localhost:8000/metrics | grep tradingagents_ | head -20

# Prometheus scrape target status
curl -s http://localhost:9090/api/v1/targets | python3 -m json.tool

# Loki readiness
curl -s http://localhost:3100/ready
```

### 7.5 Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `/ready` returns 503 with `"No LLM API key"` | `.env` file missing or `DEEPSEEK_API_KEY` not set | Add the key to `.env` and restart the server |
| Prometheus target shows "DOWN" | API server not running or port mismatch | Start the API server on port 8000, check `host.docker.internal` resolution |
| Grafana shows "No data" | Prometheus not scraping or metric name mismatch | Check Prometheus Targets page; verify metric names start with `tradingagents_` |
| Logs not appearing in Loki | Promtail not tailing the correct path | Verify `logs/tradingagents.log` exists; check Promtail container logs |
| Metrics all zero after restart | Normal behavior (in-process counters) | Run a pipeline analysis to generate new metrics; Prometheus retains pre-restart history |

---

## 8. Suggested Thesis / Presentation Highlights

The following points summarize the monitoring stack for thesis chapters or presentation slides.

### Observability Architecture
- StockHive implements a layered observability stack: built-in API diagnostics for development, with optional Prometheus + Grafana + Loki for production-grade monitoring.
- The architecture separates concerns: health probes for orchestrators, metrics for trend analysis, structured logs for debugging, and event replay for real-time pipeline visibility.

### Health and Readiness Probes
- Two distinct probes serve different purposes: `/live` confirms the process is responsive (liveness), `/ready` confirms critical configuration is in place (readiness).
- The readiness probe returns HTTP 503 with machine-readable failure reasons, enabling automated orchestration decisions.

### Metrics Pipeline
- 18 custom Prometheus metrics cover four domains: LLM performance (calls, latency, tokens, cost, failover), graph node execution (duration, status, parse errors), pipeline quality (signal distribution, risk vetoes, session count), and HTTP health (request count, latency).
- Metrics are instrumented at three levels: HTTP middleware (automatic), LLM callbacks (per-call), and application code (explicit counter increments).
- Nine alerting rules detect data staleness, LLM failures, pipeline latency regression, excessive HOLD rates, and API errors.

### Structured Logging
- JSON-formatted logs with `session_id`, `ticker`, and `trade_date` correlation fields enable tracing a single recommendation through the entire multi-agent pipeline.
- Context propagation uses Python's `contextvars` module, which works correctly across both synchronous agent code and asynchronous API handlers.

### Dashboard Truthfulness
- The Monitoring page displays only data sourced from live API responses. No hardcoded warnings or placeholder values.
- Optional infrastructure (Grafana, Prometheus, Loki) is presented as "Optional / Not running" rather than false-alarm "Offline" errors, reducing alert fatigue in development.
- Every section handles loading, empty, and error states gracefully.

### Alert Rules and Future Alertmanager
- Prometheus evaluates nine alerting rules covering data freshness, LLM health, pipeline quality, and API reliability.
- Alert rules use the actual exported metric names (e.g., `tradingagents_llm_calls_total{status="error"}`), verified against the `/metrics` endpoint.
- Alertmanager notification delivery (Slack, email) is a documented Phase 3 enhancement. The current implementation focuses on rule definition and evaluation.

### Design Philosophy
- Monitoring is designed for honesty over impressiveness: it reports what is actually working, acknowledges what is lightweight, and documents what remains future work.
- The stack degrades gracefully: the system is fully functional without Docker, Prometheus, or Grafana, with the built-in diagnostic endpoints providing baseline visibility.
