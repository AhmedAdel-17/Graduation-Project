# Observability Roadmap

> **Status:** Phase 1 + Phase 2 complete as of 2026-06-22.

---

## Phase 1 — Monitoring Truthfulness (DONE)

Completed 2026-06-22. The Monitoring page now:

- Shows real data from backend health, metrics, and freshness endpoints
- Never displays hardcoded warnings — ChromaDB retrieval mode is data-driven
  via `embeddings_active` and `retrieval_mode` fields from `/api/health`
- Has proper loading, empty, and error states for every section
- Labels Grafana/Prometheus/Loki as "optional" with setup instructions
  when Docker services are not running
- Uses honest, technical language appropriate for a Developer section
- Pipeline metrics explain that counters are in-memory and reset on restart

### Backend diagnostic addition

Two fields were added to the `/api/health` response under `diagnostics.memory`:

| Field | Type | Source |
|-------|------|--------|
| `embeddings_active` | `boolean` | Same provider-detection logic as `memory.py` (Ollama or OpenAI URL match) |
| `retrieval_mode` | `"vector"` or `"bm25_keyword"` | Derived from `embeddings_active` |

### Known Phase 1 risk: MetricsCallbackHandler coverage gap

`MetricsCallbackHandler` is properly wired in `trading_graph.py` — it's
attached as `callbacks=[_metrics_cb]` to every LLM object on all three
provider paths (DeepSeek, Anthropic, Google). LLM metrics in the Monitoring
page are real data, not decorative.

**However:** `tradingagents/agents/utils/llm_failover.py:build_resilient_llm()`
constructs LLM objects **without** attaching `MetricsCallbackHandler`. This
function is currently dead code (never called outside its own docstring), but
if the system later switches to the failover path, LLM metrics would silently
drop to zero with no warning.

**Mitigation when activating failover:** Add `callbacks=[MetricsCallbackHandler()]`
to each provider in `build_resilient_llm()`, or attach it at the graph level
via LangChain's global callback manager.

---

## Phase 2 — Production Observability (DONE)

Completed 2026-06-22. All 7 items implemented.

**Not included in this pass:** Alertmanager delivery (Slack/email notifications).
This pass adds Prometheus recording and alerting rules only. Alertmanager is
deferred to Phase 3.

### 2.1 `/ready` and `/live` probes ✓

- `/live` — lightweight liveness check returning `{"status": "alive", "pid", "uptime_seconds"}`
- `/ready` — lightweight readiness probe. Checks LLM API key presence, ChromaDB
  path existence, and EGX tools importability. Returns HTTP 503 with
  `{"ready": false, "checks": {...}, "reasons": [...]}` when degraded.
  Note: these are config/file-existence checks, not full dependency health probes
  (no network roundtrip to LLM provider, no ChromaDB query).

### 2.2 Process uptime metric ✓

- `process_start_time_seconds` Prometheus Gauge in `tradingagents/observability/metrics.py`
  (set at module import time — Grafana "uptime" panel understands this convention)
- `server_uptime_seconds` field added to `/api/metrics-summary` response
- "Server uptime" tile added to the Monitoring page (first tile in the grid)
- Ephemeral counters remain in-process; the uptime indicator tells developers the counter epoch

### 2.3 Alerting rules ✓

- `monitoring/prometheus/alerts.yml` — 4 groups, 8 rules:
  - `DataSourceStale` / `DataSourceMissing` (data freshness)
  - `LLMHighErrorRate` / `LLMFailoverActive` (LLM health)
  - `PipelineDurationHigh` / `HighHoldRate` / `RiskVetoSpike` (pipeline quality)
  - `APIHighErrorRate` / `APIHighLatency` (HTTP health)
- `prometheus.yml` updated with `rule_files: ["alerts.yml"]`
- All metric names use the `tradingagents_` prefix and correct label names
  (e.g. `status_code` not `status` on HTTP metrics)
- **Alertmanager is NOT configured.** This pass adds rules only — Prometheus
  will evaluate them and show firing alerts in its UI, but no notifications
  are delivered. See Phase 3 for Alertmanager setup.

### 2.4 WebSocket event replay (in-memory) ✓

- In-memory ring buffer in `redis_pubsub.py` — `MAX_EVENTS_PER_CHANNEL = 100`
- `_buffer_event()` stores every event locally (works even without Redis)
- `get_buffered_events(ticker)` / `get_all_recent_events(limit)` for retrieval
- `/api/events?ticker=X&limit=N` REST endpoint for replay on page load
- **In-memory only:** buffer is lost on server restart. This is not durable
  history — it covers the gap between "client connects" and "next live event."
  For persistent event log, see Phase 3 (Redis Streams).

### 2.5 Config-driven monitoring URLs ✓

- `get_system_status()` in `dashboard_store.py` reads `STOCKHIVE_*` environment variables:
  `STOCKHIVE_API_URL`, `STOCKHIVE_DASHBOARD_URL`, `STOCKHIVE_GRAFANA_URL`,
  `STOCKHIVE_PROMETHEUS_URL`, `STOCKHIVE_LOKI_URL`, `STOCKHIVE_REDIS_URL`
- Monitoring page reads URLs from the system-status response (no hardcoded localhost)
- Defaults to `localhost:PORT` when env vars are unset

### 2.6 Monitoring stack startup docs ✓

- `monitoring/README.md` updated with:
  - Prerequisites and quick-start (`docker compose up -d`)
  - Promtail log path verification
  - Custom service URLs (STOCKHIVE_* env vars)
  - Grafana credential rotation guidance
  - Troubleshooting for common issues

### 2.7 Structured logging for Loki ✓

- `setup_logging()` in `api_server.py` writes JSON-structured logs to `./logs/tradingagents.log`
  via `python-json-logger` (fields: timestamp, level, logger, message)
- `contextvars`-based trace context (`session_id`, `ticker`, `trade_date`) injected
  via `set_trace_context()` at the start of each pipeline run
- Promtail → Loki pipeline verified: app writes `logs/*.log` → Docker mounts to
  `/var/log/tradingagents` → Promtail tails and ships to Loki with label extraction

---

## Phase 3 — Future Enhancements (NOT STARTED)

- **Alertmanager delivery:** Add `alertmanager.yml` config and service to
  `monitoring/docker-compose.yml`. Wire Slack webhook or email for alert
  notifications. Phase 2 rules will start firing alerts once Alertmanager
  is reachable.
- **Full dependency health probes:** `/ready` currently checks config/file
  existence only. Add actual network probes (LLM provider ping, ChromaDB
  heartbeat query) for production orchestrator use.
- **Persistent metrics:** Prometheus Pushgateway for batch jobs (backtests),
  or Postgres-backed counters for cross-restart continuity
- **Redis Streams:** Replace in-memory ring buffer with Streams for durable
  event log with consumer groups and replay from any offset
- **Grafana dashboards:** Pre-built dashboards for LLM metrics, pipeline health,
  data freshness (currently requires manual panel creation)
- **Distributed tracing:** OpenTelemetry spans across the multi-agent pipeline
