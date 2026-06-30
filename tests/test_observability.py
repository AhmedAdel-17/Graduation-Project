"""
Acceptance tests for the observability package (Phase 1).

Tests cover:
  - Structured JSON logging with context injection
  - metered_node() success/error/contextvar behavior
  - MetricsCallbackHandler LLM metrics
  - Model pricing estimation
  - FastAPI middleware and endpoints
  - Label cardinality safety
  - Fundamentals gauges
"""
import json
import logging
import time
from unittest.mock import MagicMock

import pytest
from prometheus_client import CollectorRegistry

# ─── Helpers ───────────────────────��────────────────────────────���───────────

def _fresh_registry():
    """Return a fresh Prometheus registry to avoid cross-test pollution."""
    return CollectorRegistry()


# ════════��═══════════════════════════════════���══════════════════════════════════
# 1–3: Structured logging
# ═══════════════════════════════════════════════���═══════════════════════════════

class TestStructuredLogging:
    def test_json_log_format(self, capfd):
        """setup_logging(json_output=True) produces valid JSON with required keys."""
        from tradingagents.observability.logging_config import setup_logging

        setup_logging(json_output=True, level="DEBUG")
        test_logger = logging.getLogger("test.json_format")
        test_logger.info("hello world")

        captured = capfd.readouterr()
        # JSON goes to stderr
        line = captured.err.strip().split("\n")[-1]
        record = json.loads(line)
        assert "timestamp" in record
        assert "level" in record
        assert "logger" in record
        assert "message" in record
        assert record["message"] == "hello world"

    def test_context_var_in_logs(self, capfd):
        """set_trace_context() injects session_id, ticker, trade_date into log JSON."""
        from tradingagents.observability.logging_config import setup_logging, set_trace_context

        setup_logging(json_output=True, level="DEBUG")
        set_trace_context(session_id="sess123", ticker="COMI.CA", trade_date="2024-01-15")
        test_logger = logging.getLogger("test.context_var")
        test_logger.info("with context")

        captured = capfd.readouterr()
        line = captured.err.strip().split("\n")[-1]
        record = json.loads(line)
        assert record.get("session_id") == "sess123"
        assert record.get("ticker") == "COMI.CA"
        assert record.get("trade_date") == "2024-01-15"

    def test_context_var_reset(self, capfd):
        """After setting trace context, resetting to empty removes fields."""
        from tradingagents.observability.logging_config import setup_logging, set_trace_context

        setup_logging(json_output=True, level="DEBUG")
        set_trace_context(session_id="temp", ticker="X.CA", trade_date="2024-01-01")
        set_trace_context()  # reset to empty
        test_logger = logging.getLogger("test.context_reset")
        test_logger.info("after reset")

        captured = capfd.readouterr()
        line = captured.err.strip().split("\n")[-1]
        record = json.loads(line)
        assert record.get("session_id") == ""
        assert record.get("ticker") == ""

    def test_log_file_handler(self, tmp_path):
        """setup_logging(log_file=...) creates a RotatingFileHandler that writes JSON."""
        from tradingagents.observability.logging_config import setup_logging, set_trace_context

        log_path = str(tmp_path / "test.log")
        setup_logging(json_output=True, level="DEBUG", log_file=log_path)
        set_trace_context(session_id="file_test", ticker="TMGH.CA")
        test_logger = logging.getLogger("test.file_handler")
        test_logger.info("file log entry")

        import pathlib
        content = pathlib.Path(log_path).read_text()
        record = json.loads(content.strip().split("\n")[-1])
        assert record["message"] == "file log entry"
        assert record.get("session_id") == "file_test"


# ═══════════════════════════════════════════════════════════════════════════════
# 4–6: metered_node()
# ═══════════════════════════════════════════���═══════════════════════════════��═══

class TestMeteredNode:
    def test_metered_node_success(self):
        """metered_node records duration and success status."""
        from tradingagents.observability.node_metrics import metered_node
        from tradingagents.observability.metrics import node_duration_seconds, node_status_total

        def dummy_node(state):
            time.sleep(0.01)
            return {"result": "ok"}

        wrapped = metered_node(dummy_node, "test_success_node")
        result = wrapped({"input": "data"})

        assert result == {"result": "ok"}
        # Check that metrics were recorded (sample count > 0)
        sample = node_duration_seconds.labels(node_name="test_success_node")
        assert sample._sum._value > 0
        assert node_status_total.labels(node_name="test_success_node", status="success")._value._value == 1.0

    def test_metered_node_error(self):
        """metered_node records error status and re-raises."""
        from tradingagents.observability.node_metrics import metered_node
        from tradingagents.observability.metrics import node_status_total

        def failing_node(state):
            raise ValueError("test error")

        wrapped = metered_node(failing_node, "test_error_node")
        with pytest.raises(ValueError, match="test error"):
            wrapped({"input": "data"})

        assert node_status_total.labels(node_name="test_error_node", status="error")._value._value == 1.0

    def test_metered_node_sets_current_node(self):
        """Inside a metered node, _current_node contextvar returns the node name."""
        from tradingagents.observability.node_metrics import metered_node, _current_node

        captured_name = []

        def capturing_node(state):
            captured_name.append(_current_node.get())
            return {}

        wrapped = metered_node(capturing_node, "my_node")
        wrapped({})

        assert captured_name == ["my_node"]


# ══════════════════════════════════════════════��════════════════════════════════
# 7–11: MetricsCallbackHandler
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetricsCallback:
    def _make_handler(self):
        from tradingagents.observability.llm_metrics import MetricsCallbackHandler
        return MetricsCallbackHandler()

    def _mock_response(self, model="deepseek-chat", prompt_tokens=0, completion_tokens=0):
        from langchain_core.outputs import LLMResult, Generation
        llm_output = {"model_name": model}
        if prompt_tokens or completion_tokens:
            llm_output["token_usage"] = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            }
        return LLMResult(
            generations=[[Generation(text="test")]],
            llm_output=llm_output,
        )

    def test_callback_on_llm_end(self):
        """on_llm_end increments success counter and records latency."""
        from tradingagents.observability.metrics import llm_calls_total

        handler = self._make_handler()
        handler.on_llm_start({}, ["test"], run_id="run1")
        time.sleep(0.01)
        handler.on_llm_end(self._mock_response(), run_id="run1")

        # Should have recorded a success call
        metric = llm_calls_total.labels(agent_name="unknown", model="deepseek-chat", status="success")
        assert metric._value._value >= 1.0

    def test_callback_on_llm_error(self):
        """on_llm_error increments error counter."""
        from tradingagents.observability.metrics import llm_calls_total

        handler = self._make_handler()
        handler.on_llm_start({}, ["test"], run_id="run_err")
        handler.on_llm_error(RuntimeError("boom"), run_id="run_err")

        metric = llm_calls_total.labels(agent_name="unknown", model="unknown", status="error")
        assert metric._value._value >= 1.0

    def test_callback_reads_current_node(self):
        """Callback uses _current_node contextvar for agent_name label."""
        from tradingagents.observability.node_metrics import _current_node
        from tradingagents.observability.metrics import llm_calls_total

        handler = self._make_handler()
        token = _current_node.set("bull_researcher")
        try:
            handler.on_llm_start({}, ["test"], run_id="run_node")
            handler.on_llm_end(self._mock_response(), run_id="run_node")
        finally:
            _current_node.reset(token)

        metric = llm_calls_total.labels(agent_name="bull_researcher", model="deepseek-chat", status="success")
        assert metric._value._value >= 1.0

    def test_callback_token_counting(self):
        """Token usage from response is recorded in counters."""
        from tradingagents.observability.metrics import llm_input_tokens_total, llm_output_tokens_total

        handler = self._make_handler()
        handler.on_llm_start({}, ["test"], run_id="run_tok")
        handler.on_llm_end(
            self._mock_response(prompt_tokens=100, completion_tokens=50),
            run_id="run_tok",
        )

        inp_metric = llm_input_tokens_total.labels(agent_name="unknown", model="deepseek-chat")
        out_metric = llm_output_tokens_total.labels(agent_name="unknown", model="deepseek-chat")
        assert inp_metric._value._value >= 100.0
        assert out_metric._value._value >= 50.0

    def test_callback_missing_tokens(self):
        """No crash when token_usage is absent from response."""
        from langchain_core.outputs import LLMResult, Generation

        handler = self._make_handler()
        handler.on_llm_start({}, ["test"], run_id="run_no_tok")
        # Response with no token_usage
        response = LLMResult(
            generations=[[Generation(text="test")]],
            llm_output={"model_name": "deepseek-chat"},
        )
        handler.on_llm_end(response, run_id="run_no_tok")  # Should not raise


# ═════════════════════════════════════════════���══════════════════════════════��══
# 12–13: Model pricing
# ══════════════════════════════════════════���════════════════════════════════════

class TestNodeLLMAttribution:
    """Integration: metered_node + MetricsCallbackHandler attribution."""

    def test_metered_node_attributes_llm_calls_to_node_name(self):
        """
        When a node function triggers an LLM callback inside metered_node,
        llm_calls_total records the node's name as agent_name (not 'unknown').
        """
        from tradingagents.observability.node_metrics import metered_node
        from tradingagents.observability.llm_metrics import MetricsCallbackHandler
        from tradingagents.observability.metrics import llm_calls_total
        from langchain_core.outputs import LLMResult, Generation

        handler = MetricsCallbackHandler()

        def node_that_calls_llm(state):
            """Simulates a node that invokes an LLM (via callback)."""
            handler.on_llm_start({}, ["prompt"], run_id="integ_run")
            response = LLMResult(
                generations=[[Generation(text="response")]],
                llm_output={"model_name": "deepseek-chat"},
            )
            handler.on_llm_end(response, run_id="integ_run")
            return {"output": "done"}

        wrapped = metered_node(node_that_calls_llm, "trader")
        wrapped({"input": "test"})

        # The LLM call should be attributed to "trader", not "unknown"
        metric = llm_calls_total.labels(agent_name="trader", model="deepseek-chat", status="success")
        assert metric._value._value >= 1.0


class TestModelPricing:
    def test_model_pricing_known(self):
        """Known model returns positive cost estimate."""
        from tradingagents.observability.model_pricing import estimate_cost
        cost = estimate_cost("deepseek-chat", 1000, 500)
        assert cost > 0
        # deepseek-chat: 1000 * 0.14e-6 + 500 * 0.28e-6 = 0.00014 + 0.00014 = 0.00028
        assert abs(cost - 0.00028) < 1e-8

    def test_model_pricing_unknown(self):
        """Unknown model returns 0.0 cost."""
        from tradingagents.observability.model_pricing import estimate_cost
        cost = estimate_cost("unknown-model-xyz", 1000, 500)
        assert cost == 0.0


# ════════════════════════════════════════════════���═════════════════════════════���
# 14–16: FastAPI middleware and endpoints
# ═══════════════════════════════════��════════════════════════════════��══════════

class TestFastAPIEndpoints:
    @pytest.fixture
    def client(self):
        """Create a TestClient for the API server."""
        from fastapi.testclient import TestClient
        from server.api_server import app
        return TestClient(app)

    def test_middleware_request_counter(self, client):
        """HTTP request to /live increments http_requests_total."""
        from tradingagents.observability.metrics import http_requests_total

        # Make a request
        response = client.get("/live")
        assert response.status_code == 200

        # Check metric was incremented
        metric = http_requests_total.labels(method="GET", endpoint="/live", status_code="200")
        assert metric._value._value >= 1.0

    def test_metrics_endpoint_format(self, client):
        """GET /metrics returns Prometheus exposition format."""
        response = client.get("/metrics")
        assert response.status_code == 200
        body = response.text
        assert "tradingagents_" in body
        # Should contain at least one HELP line
        assert "# HELP" in body

    def test_ready_and_live_endpoints(self, client):
        """Readiness and liveness probes respond correctly."""
        ready = client.get("/ready")
        assert ready.status_code == 200
        assert ready.json()["ready"] is True

        live = client.get("/live")
        assert live.status_code == 200
        assert live.json()["status"] == "alive"


# ═══════════════════════════════════════════════════════════════════════════════
# 17: Label cardinality safety
# ═════════════════════���════════════════════════════════���════════════════════════

class TestLabelCardinality:
    def test_no_forbidden_labels(self):
        """No metric in metrics.py uses forbidden label names."""
        import tradingagents.observability.metrics as m
        from prometheus_client import Counter, Gauge, Histogram

        forbidden = {"session_id", "run_id", "prompt_hash", "raw_error", "raw_url", "trade_date"}

        for attr_name in dir(m):
            obj = getattr(m, attr_name)
            if isinstance(obj, (Counter, Gauge, Histogram)):
                label_names = set(obj._labelnames)
                overlap = label_names & forbidden
                assert not overlap, f"Metric {attr_name} uses forbidden labels: {overlap}"


# ═════════════════════════════════════════���═════════════════════════════════════
# 18: Fundamentals gauges
# ══════════════════════════���══════════════════════���═════════════════════════════

class TestFundamentalsGauges:
    def test_fundamentals_gauge_set(self):
        """Fundamentals gauges can be set and read."""
        from tradingagents.observability.metrics import fundamentals_stale_tickers

        fundamentals_stale_tickers.set(3)
        assert fundamentals_stale_tickers._value._value == 3.0

        fundamentals_stale_tickers.set(0)
        assert fundamentals_stale_tickers._value._value == 0.0
