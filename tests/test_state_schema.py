"""Schema validation for AgentState, InvestDebateState, and RiskDebateState.

These are hard assertions — if a field is missing, the corresponding
Phase feature cannot work. These tests guard against accidental removals
from agent_states.py.
"""
import pytest


class TestAgentStateSchema:
    """Verify AgentState has all fields required by optimization phases."""

    def test_prefetch_fields_exist(self):
        """Phase 2a: All 4 prefetch keys must be declared in AgentState."""
        from tradingagents.agents.utils.agent_states import AgentState

        annotations = AgentState.__annotations__
        for field in [
            "prefetched_company_news",
            "prefetched_market_news",
            "prefetched_social_sentiment",
            "prefetched_social_posts",
        ]:
            assert field in annotations, \
                f"AgentState is missing required prefetch field: {field}"

    def test_technical_analysis_field_exists(self):
        """Phase 1b/2d: technical_analysis must be in AgentState."""
        from tradingagents.agents.utils.agent_states import AgentState

        assert "technical_analysis" in AgentState.__annotations__, \
            "AgentState is missing 'technical_analysis' field (required by Phase 1b/2d)"

    def test_risk_veto_field_exists(self):
        """Phase 3b: risk_veto must be in AgentState for early-return veto path."""
        from tradingagents.agents.utils.agent_states import AgentState

        assert "risk_veto" in AgentState.__annotations__, \
            "AgentState is missing 'risk_veto' field (required by Phase 3b)"


class TestInvestDebateStateSchema:
    """Verify InvestDebateState has structured thesis fields (Phase 2d/2e)."""

    def test_bull_thesis_field_exists(self):
        from tradingagents.agents.utils.agent_states import InvestDebateState

        assert "bull_thesis" in InvestDebateState.__annotations__, \
            "InvestDebateState is missing 'bull_thesis' field"

    def test_bear_thesis_field_exists(self):
        from tradingagents.agents.utils.agent_states import InvestDebateState

        assert "bear_thesis" in InvestDebateState.__annotations__, \
            "InvestDebateState is missing 'bear_thesis' field"


class TestRiskDebateStateSchema:
    """Verify RiskDebateState has fields for the merged debate (Phase 3a)."""

    def test_current_perspective_fields_exist(self):
        from tradingagents.agents.utils.agent_states import RiskDebateState

        annotations = RiskDebateState.__annotations__
        for field in [
            "current_risky_response",
            "current_safe_response",
            "current_neutral_response",
        ]:
            assert field in annotations, \
                f"RiskDebateState is missing field: {field}"

    def test_history_and_count_fields_exist(self):
        from tradingagents.agents.utils.agent_states import RiskDebateState

        annotations = RiskDebateState.__annotations__
        assert "history" in annotations
        assert "count" in annotations
