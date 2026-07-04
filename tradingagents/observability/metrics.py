"""
Prometheus metric definitions for EGX Trading Agents.

Single source of truth for all metrics. Import from here — never define
metrics inline in other modules.

Label cardinality rules:
  Allowed: agent_name, node_name, model, provider, status, endpoint,
           signal, data_type, source, direction, veto_reason
  Bounded: ticker (only on fundamentals gauges, ~30 EGX tickers)
  Forbidden: session_id, run_id, prompt_hash, raw_error, raw_url, trade_date
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# =============================================================================
# LLM metrics
# =============================================================================

llm_calls_total = Counter(
    "tradingagents_llm_calls_total",
    "Total LLM API calls",
    ["agent_name", "model", "status"],
)

llm_latency_seconds = Histogram(
    "tradingagents_llm_latency_seconds",
    "LLM call latency in seconds",
    ["agent_name", "model"],
    buckets=[0.5, 1, 2, 5, 10, 20, 30, 60, 120],
)

llm_input_tokens_total = Counter(
    "tradingagents_llm_input_tokens_total",
    "Total LLM input tokens consumed",
    ["agent_name", "model"],
)

llm_output_tokens_total = Counter(
    "tradingagents_llm_output_tokens_total",
    "Total LLM output tokens consumed",
    ["agent_name", "model"],
)

llm_cost_usd_total = Counter(
    "tradingagents_llm_cost_usd_total",
    "Estimated LLM API cost in USD (0 if model pricing unknown)",
    ["model"],
)

llm_failover_total = Counter(
    "tradingagents_llm_failover_total",
    "LLM provider failover rotation events",
    ["agent_name", "provider"],
)

# =============================================================================
# Graph node metrics
# =============================================================================

node_duration_seconds = Histogram(
    "tradingagents_node_duration_seconds",
    "Per-node wall clock time in the trading graph",
    ["node_name"],
    buckets=[0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300],
)

node_status_total = Counter(
    "tradingagents_node_status_total",
    "Per-node completion status (success or error only from wrapper)",
    ["node_name", "status"],
)

node_parse_errors_total = Counter(
    "tradingagents_node_parse_errors_total",
    "JSON/output parse failures per node (incremented explicitly where detected)",
    ["node_name"],
)

# =============================================================================
# Pipeline metrics
# =============================================================================

pipeline_duration_seconds = Histogram(
    "tradingagents_pipeline_duration_seconds",
    "Full propagate() duration in seconds",
    buckets=[30, 60, 120, 180, 240, 300, 600],
)

signal_total = Counter(
    "tradingagents_signal_total",
    "Final trading signals emitted (BUY/SELL/HOLD)",
    ["signal"],
)

risk_veto_total = Counter(
    "tradingagents_risk_veto_total",
    "Risk manager vetoes by reason",
    ["veto_reason"],
)

active_analysis_sessions = Gauge(
    "tradingagents_active_analysis_sessions",
    "Number of currently running analysis sessions",
)

# =============================================================================
# HTTP metrics
# =============================================================================

http_requests_total = Counter(
    "tradingagents_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)

http_request_duration_seconds = Histogram(
    "tradingagents_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30, 60],
)

active_websocket_connections = Gauge(
    "tradingagents_active_websocket_connections",
    "Number of active WebSocket connections",
)

# =============================================================================
# Data pipeline metrics
# =============================================================================

data_fetch_total = Counter(
    "tradingagents_data_fetch_total",
    "Data fetch attempts by type, source, and outcome",
    ["data_type", "source", "status"],
)

data_fetch_latency_seconds = Histogram(
    "tradingagents_data_fetch_latency_seconds",
    "Data fetch latency in seconds",
    ["data_type", "source"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30],
)

# =============================================================================
# Fundamentals data quality gauges
# =============================================================================

fundamentals_stale_tickers = Gauge(
    "tradingagents_fundamentals_stale_tickers",
    "Number of tickers with stale data (>90d since last period)",
)

fundamentals_stub_tickers = Gauge(
    "tradingagents_fundamentals_stub_tickers",
    "Number of tickers with 0 annual periods (stub files)",
)

fundamentals_cbe_provisional_rows = Gauge(
    "tradingagents_fundamentals_cbe_provisional_rows",
    "Number of CBE policy rate rows with PROVISIONAL verification status",
)

fundamentals_dividend_yield_coverage_ratio = Gauge(
    "tradingagents_fundamentals_dividend_yield_coverage_ratio",
    "Fraction of tickers with non-zero dividend_yield data (0.0-1.0)",
)

fundamentals_pe_sparse_tickers = Gauge(
    "tradingagents_fundamentals_pe_sparse_tickers",
    "Number of tickers with sparse (<80%) pe_ratio coverage",
)

# =============================================================================
# Data freshness metrics
# =============================================================================

data_freshness_age_seconds = Gauge(
    "tradingagents_data_freshness_age_seconds",
    "Age of most recent data in seconds, by source and ticker",
    ["source", "ticker"],
)

data_fetch_last_success_timestamp = Gauge(
    "tradingagents_data_fetch_last_success_timestamp",
    "Unix timestamp of last successful data fetch, by source and ticker",
    ["source", "ticker"],
)

data_stale_current = Gauge(
    "tradingagents_data_stale_current",
    "Current number of stale data sources (1=stale, 0=ok)",
    ["source"],
)

data_missing_current = Gauge(
    "tradingagents_data_missing_current",
    "Current number of missing data sources (1=missing, 0=ok)",
    ["source"],
)

data_fetch_errors_total = Counter(
    "tradingagents_data_fetch_errors_total",
    "Data fetch errors by source",
    ["source"],
)

# ── Process lifecycle ───────────────────────────────────────────────────────
# process_start_time_seconds is a Prometheus convention — Grafana's
# "uptime" panel type understands it natively.
import time as _time

process_start_time_seconds = Gauge(
    "tradingagents_process_start_time_seconds",
    "Unix timestamp when the API server process started",
)
process_start_time_seconds.set_to_current_time()
