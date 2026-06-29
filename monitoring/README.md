# Monitoring Stack

Prometheus + Grafana + Loki for the TradingAgents EGX system.

## Prerequisites

- Docker and Docker Compose (Docker Desktop on Mac/Windows, or Docker Engine on Linux)
- The TradingAgents API server running on the host at port 8000

## Quick Start

```bash
# 1. Start the API server (file logging is automatic)
cd /path/to/Graduation-Project
uvicorn server.api_server:app --port 8000

# 2. Start monitoring stack (in another terminal)
cd monitoring
docker compose up -d
```

## Access

| Service    | URL                          | Credentials   |
|------------|------------------------------|---------------|
| Grafana    | http://localhost:3000        | admin / admin |
| Prometheus | http://localhost:9090        | (none)        |
| Loki       | http://localhost:3100        | (none)        |
| App metrics| http://localhost:8000/metrics | (none)        |

## Verify

```bash
# Check Prometheus is scraping the app
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import json, sys
data = json.load(sys.stdin)
for t in data['data']['activeTargets']:
    print(f\"{t['labels']['job']}: {t['health']}\")
"

# Check Grafana datasources (should show Prometheus + Loki)
curl -s -u admin:admin 'http://localhost:3000/api/datasources' | python3 -c "
import json, sys
for ds in json.load(sys.stdin):
    print(f\"{ds['name']} ({ds['type']}): uid={ds['uid']}\")
"

# Check Loki is receiving logs
curl -s 'http://localhost:3100/loki/api/v1/labels' | python3 -m json.tool

# Check dashboard provisioned
curl -s -u admin:admin 'http://localhost:3000/api/search?query=TradingAgents'
```

## Architecture

```
Host:
  uvicorn (port 8000) ─── /metrics ─── /live ─── /ready
       │
       └── writes JSON logs → ./logs/tradingagents.log

Docker:
  ┌─────────────────────────────────────────────────────────────┐
  │ Prometheus (:9090) ─── scrapes host:8000/metrics            │
  │                                                             │
  │ Promtail ─── tails ./logs/tradingagents.log ──→ Loki        │
  │                                                             │
  │ Loki (:3100) ─── stores logs, 30d retention                 │
  │                                                             │
  │ Grafana (:3000) ─── queries Prometheus + Loki               │
  └─────────────────────────────────────────────────────────────┘
```

The app runs natively on the host. Prometheus reaches it via `host.docker.internal`.
Promtail reads the JSON log file via a bind-mount of `../logs`.

## Log Shipping

The API server automatically writes structured JSON logs to
`./logs/tradingagents.log` on startup (10 MB rotating, 5 backups).
The `TRADINGAGENTS_LOG_FILE` env var can override the path:

| Value   | Behavior |
|---------|----------|
| (unset) | `./logs/tradingagents.log` (default, automatic) |
| `/path` | Writes to specified absolute path |
| `none`  | Explicitly disable file logging |

Promtail tails the log file and pushes to Loki with these **indexed labels**:
- `job` = "tradingagents"
- `level` (INFO, WARNING, ERROR, DEBUG)
- `logger` (Python logger name, e.g. "tradingagents.gateway")
- `ticker` (current EGX ticker, e.g. "COMI.CA")

Fields like `session_id`, `trade_date`, `message` remain in the log line body —
queryable via LogQL `| json` but NOT indexed (avoids label cardinality explosion).

### Example LogQL queries in Grafana Explore

```logql
# All errors
{job="tradingagents", level="ERROR"}

# Errors for a specific ticker
{job="tradingagents", level="ERROR", ticker="COMI.CA"}

# Search message content
{job="tradingagents"} |= "timeout"

# Parse JSON and filter by session_id
{job="tradingagents"} | json | session_id="abc123"
```

## Dashboard

The pre-provisioned "TradingAgents Overview" dashboard has 6 rows:

1. **Pipeline Overview** — signal counts, active sessions, pipeline duration p50/p95
2. **LLM Costs & Performance** — total cost USD, latency by node, token throughput
3. **Graph Nodes** — per-node duration, error counts, failover events
4. **HTTP & API** — request rate, latency percentiles, error rate
5. **Data Pipeline & Fundamentals** — cache hit rate, fetch latency, data quality gauges
6. **Logs** — recent errors & warnings from Loki

## Tear Down

```bash
# Stop containers (preserves data volumes)
docker compose down

# Stop and delete all data (reset)
docker compose down -v
```

## Custom Service URLs

If running services on non-default hosts/ports, set environment variables
before starting the API server:

```bash
export STOCKHIVE_GRAFANA_URL=http://my-grafana:3000
export STOCKHIVE_PROMETHEUS_URL=http://my-prometheus:9090
export STOCKHIVE_LOKI_URL=http://my-loki:3100
export STOCKHIVE_API_URL=http://my-api:8000
export STOCKHIVE_DASHBOARD_URL=http://my-dashboard:5173
```

The Monitoring page reads these from the `/api/system-status` response.
If unset, all default to `localhost` with standard ports.

## Credentials

Grafana default credentials are `admin` / `admin` (set in `docker-compose.yml`).
**Change these for any non-local deployment** by editing `GF_SECURITY_ADMIN_PASSWORD`
in `docker-compose.yml` or using Grafana's UI after first login.

## Data Retention

- Prometheus: 30 days (`--storage.tsdb.retention.time=30d`)
- Loki: 30 days (`retention_period: 720h` in loki-config.yml)
- Grafana: persistent volume for settings/annotations

## Troubleshooting

**Prometheus target shows "down":**
- Ensure the API server is running on port 8000
- Check `curl http://localhost:8000/metrics` returns Prometheus exposition format
- On Linux, verify `host.docker.internal` resolves (check `extra_hosts` in compose)

**Grafana shows "No data" on metrics panels:**
- Wait 15-30 seconds for the first scrape interval
- Verify Prometheus target is "up" at http://localhost:9090/targets
- Run an analysis to generate metrics (`python main.py`)

**Loki logs panel shows "No data":**
- Check `./logs/tradingagents.log` exists and has content
- If missing, the API server may not have started (it creates logs/ automatically)
- Verify Promtail is running: `docker compose logs promtail`
- Check Loki labels: `curl 'http://localhost:3100/loki/api/v1/labels'`
