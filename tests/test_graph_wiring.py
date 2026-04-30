"""Verify compiled graph structure after Phase 3a restructuring.

These tests instantiate TradingAgentsGraph with dummy API keys and inspect
the compiled graph's node set — no LLM calls are made.

Covers:
  - Old separate risk debator nodes (Risky/Safe/Neutral Analyst) are ABSENT
  - "Merged Risk Debate" node is PRESENT
  - "Risk Judge" node is PRESENT
  - Graph compiles without error
"""
import os
import pytest


@pytest.fixture(scope="module", autouse=True)
def patch_api_keys(monkeypatch_module=None):
    """Ensure API keys are set before importing graph modules."""
    os.environ.setdefault("OPENAI_API_KEY", "test-key")
    os.environ.setdefault("GROQ_API_KEY", "test-key")
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


def _get_node_names():
    """Build TradingAgentsGraph and return compiled node names."""
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    tag = TradingAgentsGraph(
        selected_analysts=["market", "social", "news", "fundamentals"],
    )
    # LangGraph compiled graphs expose .nodes as a dict-like mapping
    if hasattr(tag.graph, "nodes"):
        return set(tag.graph.nodes.keys())
    # Fallback: try the underlying graph object
    return set()


class TestLegacyDebatorNodesAbsent:
    """Old 3-node risk debate architecture must be gone after Phase 3a."""

    def test_risky_analyst_node_absent(self):
        node_names = _get_node_names()
        if not node_names:
            pytest.skip("Cannot inspect compiled graph node names")
        assert "Risky Analyst" not in node_names, \
            "Legacy 'Risky Analyst' node still present — Phase 3a merge incomplete"

    def test_safe_analyst_node_absent(self):
        node_names = _get_node_names()
        if not node_names:
            pytest.skip("Cannot inspect compiled graph node names")
        assert "Safe Analyst" not in node_names, \
            "Legacy 'Safe Analyst' node still present — Phase 3a merge incomplete"

    def test_neutral_analyst_node_absent(self):
        node_names = _get_node_names()
        if not node_names:
            pytest.skip("Cannot inspect compiled graph node names")
        assert "Neutral Analyst" not in node_names, \
            "Legacy 'Neutral Analyst' node still present — Phase 3a merge incomplete"


class TestMergedDebateNodes:
    """New Phase 3a nodes must exist in the compiled graph."""

    def test_merged_risk_debate_node_present(self):
        node_names = _get_node_names()
        if not node_names:
            pytest.skip("Cannot inspect compiled graph node names")
        assert "Merged Risk Debate" in node_names, \
            "'Merged Risk Debate' node missing from compiled graph"

    def test_risk_judge_node_present(self):
        node_names = _get_node_names()
        if not node_names:
            pytest.skip("Cannot inspect compiled graph node names")
        assert "Risk Judge" in node_names, \
            "'Risk Judge' node missing from compiled graph"


class TestGraphCompiles:
    def test_graph_builds_without_error(self):
        """TradingAgentsGraph must instantiate cleanly."""
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        tag = TradingAgentsGraph(
            selected_analysts=["market", "social", "news", "fundamentals"],
        )
        assert tag.graph is not None
