"""Determinism and reproducibility verification tests.

Locks:
  - Main graph LLM construction includes temperature=0
  - Verifies seed parameter presence (or documents its absence)
  - Signal processor produces identical output on repeated calls
  - Calibration produces identical output on repeated calls
  - Risk scorer produces identical output on repeated calls
  - Model/provider configuration is accessible for audit logging
  - AST scan: every ChatOpenAI() in production files has temperature=0 + seed
  - AST scan: no .invoke(temperature=...) anti-pattern anywhere

Pure-unit: no actual LLM calls, no network.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

_PROJECT_PATH = Path(_PROJECT_ROOT)


# ─────────────────────────────────────────────────────────────────────────────
# LLM Construction Verification
# ─────────────────────────────────────────────────────────────────────────────


class TestLLMConstruction:
    """Verify LLM instantiation parameters for determinism."""

    def test_main_graph_sets_temperature_zero_and_seed_on_both_llms(self):
        """TradingAgentsGraph creates BOTH deep and quick LLMs with temperature=0 and seed=42."""
        from tradingagents.default_config import DEFAULT_CONFIG

        all_calls: list[dict] = []

        class MockChatOpenAI:
            def __init__(self, **kwargs):
                all_calls.append(dict(kwargs))

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), \
             patch("tradingagents.graph.trading_graph.ChatOpenAI", MockChatOpenAI), \
             patch("tradingagents.graph.trading_graph._resolve_memory_class", return_value=MagicMock()):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            try:
                TradingAgentsGraph(config={**DEFAULT_CONFIG})
            except Exception:
                pass  # May fail on graph setup, we only care about LLM construction

        # The OpenAI branch creates exactly 2 LLMs: deep then quick
        assert len(all_calls) == 2, (
            f"Expected exactly 2 ChatOpenAI constructions (deep + quick), got {len(all_calls)}: "
            f"{all_calls}"
        )

        deep_kwargs = all_calls[0]
        quick_kwargs = all_calls[1]

        # Temperature
        assert deep_kwargs.get("temperature") == 0, (
            f"deep_thinking_llm must have temperature=0, got kwargs: {deep_kwargs}"
        )
        assert quick_kwargs.get("temperature") == 0, (
            f"quick_thinking_llm must have temperature=0, got kwargs: {quick_kwargs}"
        )

        # Seed — this SHOULD FAIL until trading_graph.py is fixed
        assert deep_kwargs.get("seed") == 42, (
            f"deep_thinking_llm must have seed=42 for reproducibility, got kwargs: {deep_kwargs}"
        )
        assert quick_kwargs.get("seed") == 42, (
            f"quick_thinking_llm must have seed=42 for reproducibility, got kwargs: {quick_kwargs}"
        )

    def test_default_config_specifies_model(self):
        """Config has explicit model names for audit trail."""
        from tradingagents.default_config import DEFAULT_CONFIG
        assert "deep_think_llm" in DEFAULT_CONFIG
        assert "quick_think_llm" in DEFAULT_CONFIG
        assert DEFAULT_CONFIG["deep_think_llm"]  # not empty
        assert DEFAULT_CONFIG["quick_think_llm"]  # not empty

    def test_failover_builder_sets_seed_for_primary(self):
        """build_resilient_llm passes seed=42 for primary (DeepSeek) provider."""
        captured_kwargs = {}

        class MockChatOpenAI:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)

            def invoke(self, *a, **kw):
                return MagicMock(content="test")

            def bind_tools(self, *a, **kw):
                return self

            @property
            def _llm_type(self):
                return "test"

        # ChatOpenAI is imported inside build_resilient_llm's body via
        # `from langchain_openai import ChatOpenAI`, so we patch at the
        # source package level.
        with patch("langchain_openai.ChatOpenAI", MockChatOpenAI):
            from tradingagents.agents.utils.llm_failover import build_resilient_llm
            config = {
                "deep_think_llm": "deepseek-chat",
                "quick_think_llm": "deepseek-chat",
                "backend_url": "https://api.deepseek.com",
                "DEEPSEEK_API_KEY": "test-key",
                "llm_failover_priority": ["primary"],
            }
            try:
                build_resilient_llm(config, role="deep", seed=42)
            except Exception:
                pass

        assert captured_kwargs.get("seed") == 42, (
            "build_resilient_llm must pass seed to primary provider. "
            f"Got: {captured_kwargs.get('seed')}"
        )
        assert captured_kwargs.get("temperature") == 0


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic Component Stability
# ─────────────────────────────────────────────────────────────────────────────


class TestDeterministicStability:
    """Verify that deterministic components produce identical output on repeated runs."""

    def test_signal_processor_stable(self):
        """Same input → same output across 100 invocations."""
        from tradingagents.graph.signal_processing import SignalProcessor
        sp = SignalProcessor()

        test_cases = [
            ('{"action": "BUY"}', "BUY"),
            ("FINAL TRANSACTION PROPOSAL: SELL", "SELL"),
            ("VETO this trade", "HOLD"),
            ("I think we should BUY", "BUY"),
            ("", "HOLD"),
        ]

        for text, expected in test_cases:
            results = {sp.process_signal(text) for _ in range(100)}
            assert len(results) == 1, f"Non-deterministic output for input: {text!r}"
            assert results.pop() == expected

    def test_calibration_stable(self):
        """Same calibration inputs → same output across 100 invocations."""
        from tradingagents.agents.analysts.fundamentals.calibration import (
            calibrate_earnings_direction,
        )

        kwargs = dict(
            fundamental_outlook="bearish",
            downside_risk_level="high",
            raw_earnings_direction="down",
            earnings_direction_confidence=80,
            data_confidence=70,
            sector="operational",
            de_ratio=1.5,
        )

        results = set()
        for _ in range(100):
            r = calibrate_earnings_direction(**kwargs)
            results.add((r.calibrated_direction, r.calibrated_confidence, r.policy))

        assert len(results) == 1, "Calibration produced non-deterministic results"

    def test_risk_scorer_stable(self):
        """Same risk inputs → same violations across 100 invocations."""
        from tradingagents.agents.risk_mgmt.risk_scorer import run_all_risk_checks

        plan = {
            "decision": "BUY",
            "position_sizing": {
                "target_shares": 100,
                "max_shares_per_day": 100,
                "portfolio_allocation": "1%",
            },
            "exit_logic": {"stop_loss": {"price": 48.0}},
            "entry_logic": {"entry_zone": {"limit_price": 50.5}},
        }

        results = set()
        for _ in range(100):
            import copy
            approved, violations = run_all_risk_checks(
                copy.deepcopy(plan),
                portfolio_value=1_000_000,
                avg_daily_volume=500_000,
                current_price=50.0,
            )
            violation_tuple = tuple(
                (v.rule_name, v.severity) for v in sorted(violations, key=lambda x: x.rule_name)
            )
            results.add((approved, violation_tuple))

        assert len(results) == 1, "Risk scorer produced non-deterministic results"


# ─────────────────────────────────────────────────────────────────────────────
# Audit Trail: Model/Provider Info Accessible
# ─────────────────────────────────────────────────────────────────────────────


class TestAuditTrail:
    def test_config_contains_provider_info(self):
        """DEFAULT_CONFIG has the fields needed for audit logging."""
        from tradingagents.default_config import DEFAULT_CONFIG

        required_fields = [
            "llm_provider",
            "deep_think_llm",
            "quick_think_llm",
            "backend_url",
        ]
        for field in required_fields:
            assert field in DEFAULT_CONFIG, f"Missing audit field: {field}"
            assert DEFAULT_CONFIG[field], f"Empty audit field: {field}"

    def test_config_model_names_not_generic(self):
        """Model names should be specific, not generic placeholders."""
        from tradingagents.default_config import DEFAULT_CONFIG
        for key in ("deep_think_llm", "quick_think_llm"):
            model = DEFAULT_CONFIG[key]
            assert model not in ("", "default", "auto"), (
                f"{key} should be a specific model name, got: {model!r}"
            )

    def test_fingerprint_records_seed_for_default_provider(self):
        """build_model_fingerprint records seed=42 for the default OpenAI-compatible provider."""
        from tradingagents.db.audit_writer import build_model_fingerprint
        from tradingagents.default_config import DEFAULT_CONFIG

        fp = build_model_fingerprint(DEFAULT_CONFIG)

        assert fp["seed"] == 42, (
            f"Fingerprint must record seed=42 for OpenAI-compatible providers, got: {fp}"
        )
        assert fp["temperature"] == 0, (
            f"Fingerprint must record temperature=0, got: {fp}"
        )
        assert fp["llm_provider"] == DEFAULT_CONFIG["llm_provider"]
        assert fp["deep_think_llm"] == DEFAULT_CONFIG["deep_think_llm"]
        assert fp["quick_think_llm"] == DEFAULT_CONFIG["quick_think_llm"]
        assert fp["backend_url"] == DEFAULT_CONFIG["backend_url"]
        assert fp["target_market"] == DEFAULT_CONFIG["target_market"]

    def test_fingerprint_and_graph_seed_agree(self):
        """The seed in build_model_fingerprint must match the seed used in graph LLM construction.

        If someone changes the seed in trading_graph.py but not in
        audit_writer.py (or vice versa), the audit trail becomes a lie.
        """
        from tradingagents.db.audit_writer import build_model_fingerprint
        from tradingagents.default_config import DEFAULT_CONFIG

        # Capture what the graph actually passes to ChatOpenAI
        all_calls: list[dict] = []

        class MockChatOpenAI:
            def __init__(self, **kwargs):
                all_calls.append(dict(kwargs))

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), \
             patch("tradingagents.graph.trading_graph.ChatOpenAI", MockChatOpenAI), \
             patch("tradingagents.graph.trading_graph._resolve_memory_class", return_value=MagicMock()):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            try:
                TradingAgentsGraph(config={**DEFAULT_CONFIG})
            except Exception:
                pass

        fp = build_model_fingerprint(DEFAULT_CONFIG)

        # Both graph LLMs must use the same seed the fingerprint records
        for i, label in enumerate(("deep", "quick")):
            graph_seed = all_calls[i].get("seed")
            assert graph_seed == fp["seed"], (
                f"{label}_thinking_llm was constructed with seed={graph_seed} "
                f"but build_model_fingerprint records seed={fp['seed']} — "
                "audit trail would be inaccurate"
            )


# ─────────────────────────────────────────────────────────────────────────────
# AST-Based Static Regression Gates
# ─────────────────────────────────────────────────────────────────────────────

# Production files where every ChatOpenAI() MUST have temperature=0 and seed.
_PRODUCTION_CHATOPEN_AI_FILES = [
    "tradingagents/graph/trading_graph.py",
    "tradingagents/agents/utils/llm_failover.py",
    "tradingagents/agents/profiling/investor_profiling_agent.py",
    "run_egx_prediction.py",
]


def _find_chat_openai_calls(filepath: Path):
    """Parse a Python file and return (line, kwargs_dict) for each ChatOpenAI(...)."""
    source = filepath.read_text()
    tree = ast.parse(source)
    results = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name != "ChatOpenAI":
            continue
        kwargs = {kw.arg for kw in node.keywords if kw.arg is not None}
        results.append((node.lineno, kwargs))
    return results


class TestASTChatOpenAIDeterminism:
    """Static AST scan: every ChatOpenAI() in production files must set
    temperature and seed. Catches regressions when new LLM sites are added."""

    @pytest.mark.parametrize("relpath", _PRODUCTION_CHATOPEN_AI_FILES)
    def test_all_chat_openai_have_temperature(self, relpath):
        filepath = _PROJECT_PATH / relpath
        if not filepath.exists():
            pytest.skip(f"{relpath} not found")
        calls = _find_chat_openai_calls(filepath)
        assert calls, f"No ChatOpenAI calls found in {relpath}"
        for lineno, kwargs in calls:
            assert "temperature" in kwargs, (
                f"{relpath}:{lineno} — ChatOpenAI() missing temperature parameter"
            )

    @pytest.mark.parametrize("relpath", _PRODUCTION_CHATOPEN_AI_FILES)
    def test_all_chat_openai_have_seed(self, relpath):
        filepath = _PROJECT_PATH / relpath
        if not filepath.exists():
            pytest.skip(f"{relpath} not found")
        calls = _find_chat_openai_calls(filepath)
        assert calls, f"No ChatOpenAI calls found in {relpath}"
        for lineno, kwargs in calls:
            assert "seed" in kwargs, (
                f"{relpath}:{lineno} — ChatOpenAI() missing seed parameter. "
                "All OpenAI-compatible LLM clients must set seed for determinism."
            )


class TestNoInvokeTemperatureAntiPattern:
    """Ensure no production code passes temperature= to .invoke().

    LangChain's .invoke() does not accept temperature as a kwarg — it's
    silently ignored, giving a false sense of determinism control."""

    def test_no_invoke_with_temperature_in_production_code(self):
        violations = []
        for py_file in _PROJECT_PATH.rglob("*.py"):
            rel = py_file.relative_to(_PROJECT_PATH)
            parts = str(rel).split(os.sep)
            if any(p in ("tests", "node_modules", ".venv", "venv", "__pycache__",
                         "dashboard", "chroma_db", ".claude") for p in parts):
                continue
            try:
                source = py_file.read_text()
                tree = ast.parse(source)
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "invoke":
                    for kw in node.keywords:
                        if kw.arg == "temperature":
                            violations.append(f"{rel}:{node.lineno}")

        assert not violations, (
            ".invoke(temperature=...) is not a valid LangChain pattern. "
            f"Set temperature at construction time. Violations: {violations}"
        )
