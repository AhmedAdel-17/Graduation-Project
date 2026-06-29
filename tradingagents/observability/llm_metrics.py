"""
LangChain callback handler for LLM metrics collection.

Uses LangChain's official callback system (not a wrapper class) to avoid
breaking BaseChatModel contracts. Works with all invocation patterns:
llm.invoke(), chain.invoke(), llm.bind_tools(), tool-call loops.

Per-node attribution uses the _current_node contextvar set by metered_node().
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Union

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from .metrics import (
    llm_calls_total,
    llm_cost_usd_total,
    llm_input_tokens_total,
    llm_latency_seconds,
    llm_output_tokens_total,
)
from .model_pricing import estimate_cost
from .node_metrics import _current_node, get_current_node, set_current_node

logger = logging.getLogger(__name__)


class MetricsCallbackHandler(BaseCallbackHandler):
    """
    LangChain callback that emits Prometheus metrics on every LLM call.

    Attach to LLM objects at construction time:
        cb = MetricsCallbackHandler()
        llm = ChatOpenAI(..., callbacks=[cb])

    Metrics emitted:
      - llm_calls_total (success/error by agent_name, model)
      - llm_latency_seconds (histogram by agent_name, model)
      - llm_input_tokens_total (by agent_name, model)
      - llm_output_tokens_total (by agent_name, model)
      - llm_cost_usd_total (by model, estimated)
    """

    # Thread-safe: each callback invocation gets its own kwargs context
    # from LangChain, so we use a dict keyed by run_id to track start times.

    def __init__(self) -> None:
        super().__init__()
        self._start_times: Dict[str, float] = {}

    @property
    def raise_error(self) -> bool:
        return False

    def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Record start time for latency measurement."""
        key = str(run_id) if run_id else "default"
        self._start_times[key] = time.perf_counter()

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Record success metrics: latency, tokens, cost."""
        key = str(run_id) if run_id else "default"
        start = self._start_times.pop(key, None)
        elapsed = time.perf_counter() - start if start else 0.0

        node = get_current_node()
        model = self._extract_model(response)

        llm_calls_total.labels(agent_name=node, model=model, status="success").inc()
        llm_latency_seconds.labels(agent_name=node, model=model).observe(elapsed)

        # Token counting — availability depends on provider
        inp, out = self._extract_tokens(response)
        if inp > 0:
            llm_input_tokens_total.labels(agent_name=node, model=model).inc(inp)
        if out > 0:
            llm_output_tokens_total.labels(agent_name=node, model=model).inc(out)
        if inp > 0 or out > 0:
            cost = estimate_cost(model, inp, out)
            if cost > 0:
                llm_cost_usd_total.labels(model=model).inc(cost)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """Record error metrics."""
        key = str(run_id) if run_id else "default"
        start = self._start_times.pop(key, None)
        elapsed = time.perf_counter() - start if start else 0.0

        node = get_current_node()
        llm_calls_total.labels(agent_name=node, model="unknown", status="error").inc()
        llm_latency_seconds.labels(agent_name=node, model="unknown").observe(elapsed)

    def _extract_model(self, response: LLMResult) -> str:
        """Extract model name from LLMResult."""
        if response.llm_output:
            # OpenAI-style
            model = response.llm_output.get("model_name") or response.llm_output.get("model")
            if model:
                return str(model)
        return "unknown"

    def _extract_tokens(self, response: LLMResult) -> tuple:
        """Extract (input_tokens, output_tokens) from LLMResult. Returns (0, 0) if unavailable."""
        if not response.llm_output:
            return 0, 0

        usage = response.llm_output.get("token_usage") or {}
        inp = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        out = usage.get("completion_tokens") or usage.get("output_tokens") or 0
        return int(inp), int(out)
