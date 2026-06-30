"""
Per-node instrumentation wrapper for LangGraph graph nodes.

metered_node() wraps a node function to:
  1. Measure wall-clock duration
  2. Record success/error status
  3. Set _current_node contextvar for LLM callback attribution

What it CAN detect: success, error, duration.
What it CANNOT detect: fallback, skipped, parse_error (nodes catch internally).
NodeRecorder remains the source of truth for detailed per-invocation status.
"""
from __future__ import annotations

import time
from contextvars import ContextVar
from typing import Any, Callable, Optional

from langgraph.types import RunnableConfig
from .metrics import node_duration_seconds, node_status_total

# ContextVar for per-node LLM attribution.
# Set by metered_node(), read by MetricsCallbackHandler in llm_metrics.py.
_current_node: ContextVar[str] = ContextVar("current_node", default="unknown")


def get_current_node() -> str:
    """Get the name of the currently executing graph node."""
    return _current_node.get()


def set_current_node(name: str):
    """Manually set the current node (for use outside metered_node)."""
    _current_node.set(name)


def metered_node(node_fn: Callable, node_name: str) -> Callable:
    """
    Wrap a LangGraph node function with duration and status metrics.

    Sets _current_node contextvar so that any LLM calls made within the
    node are automatically attributed to this node_name in metrics.

    Args:
        node_fn: The original node function (state) -> dict or (state, config) -> dict
        node_name: Human-readable node name matching the graph registration
    """
    def wrapper(state: Any, config: Optional[RunnableConfig] = None) -> Any:
        token = _current_node.set(node_name)
        start = time.perf_counter()
        try:
            # Always call with state only. LangGraph passes config for its
            # own routing but node functions in this project use state dicts
            # (some use functools.partial with extra kwargs that conflict
            # with positional config passing).
            result = node_fn(state)
            elapsed = time.perf_counter() - start
            node_duration_seconds.labels(node_name=node_name).observe(elapsed)
            node_status_total.labels(node_name=node_name, status="success").inc()
            return result
        except Exception:
            elapsed = time.perf_counter() - start
            node_duration_seconds.labels(node_name=node_name).observe(elapsed)
            node_status_total.labels(node_name=node_name, status="error").inc()
            raise
        finally:
            _current_node.reset(token)

    # Preserve function name for debugging
    wrapper.__name__ = f"metered_{node_fn.__name__}" if hasattr(node_fn, "__name__") else f"metered_{node_name}"
    wrapper.__qualname__ = wrapper.__name__
    return wrapper
