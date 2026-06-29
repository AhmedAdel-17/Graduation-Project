"""
Observability package for EGX Trading Agents.

Provides structured logging, Prometheus metrics, and per-node instrumentation.
All components degrade gracefully when optional dependencies are absent.
"""
from .logging_config import setup_logging, set_trace_context, get_trace_context
from .node_metrics import metered_node
from .llm_metrics import MetricsCallbackHandler, get_current_node, set_current_node

__all__ = [
    "setup_logging",
    "set_trace_context",
    "get_trace_context",
    "metered_node",
    "MetricsCallbackHandler",
    "get_current_node",
    "set_current_node",
]
