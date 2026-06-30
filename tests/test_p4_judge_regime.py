"""Tests for P4 research manager prompt: regime-conditional weighting + consistency guardrail.

Verifies prompt content only — no LLM calls, no benchmark, no threshold tuning.
"""

import json
import pytest
from unittest.mock import MagicMock, patch


def _build_judge_prompt(macro_context=None, bull_thesis=None, bear_thesis=None,
                        company="TEST.CA", position=None):
    """Build the research manager prompt by invoking the node with a mocked LLM."""
    from tradingagents.agents.managers.research_manager import create_research_manager

    # Mock LLM that captures the prompt
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(
        content='**1. Strongest Bull Case:** Growth.\n**2. Strongest Bear Case:** Rate.\n'
                '**3. Why One Side Wins:** Bull wins.\n**4. Decision & Plan:** HOLD\n'
                '```json\n{"decision": "HOLD", "confidence": 0.5, "rationale": "test"}\n```'
    )

    # Mock memory
    mock_memory = MagicMock()
    mock_memory.get_memories.return_value = []

    node_fn = create_research_manager(mock_llm, mock_memory)

    state = {
        "company_of_interest": company,
        "investment_debate_state": {
            "bull_thesis": bull_thesis or {"thesis_type": "bullish", "conviction_level": "high"},
            "bear_thesis": bear_thesis or {"thesis_type": "bearish", "conviction_level": "moderate"},
            "history": "Bull argues growth.\nBear argues valuation.",
            "current_response": "Bear: rate risk dominates.",
        },
        "market_report": "Technical: momentum strong_up, RS outperforming",
        "sentiment_report": "",
        "news_report": "",
        "fundamentals_report": "EY 5.2%, P/E 19.2x",
        "macro_context": macro_context or {
            "cbe_rate": 0.2750,
            "tbill_91d": 0.2725,
            "cpi_yoy": 0.338,
            "usdegp": 49.5,
            "egx30_trend": "bullish",
        },
        "current_position": position or {},
        "trade_date": "2024-03-15",
    }

    # Call the node — this builds and invokes the prompt
    try:
        node_fn(state)
    except Exception:
        pass  # We only care about the prompt passed to llm.invoke

    # Extract the prompt from the LLM invoke call
    if mock_llm.invoke.called:
        return mock_llm.invoke.call_args[0][0]
    return ""


class TestP4RegimeGuidance:
    """Verify high-rate regime guidance is present in the research manager prompt."""

    @pytest.fixture(autouse=True)
    def prompt(self):
        self._prompt = _build_judge_prompt()

    def test_regime_section_present(self):
        assert "High-Rate Regime Guidance" in self._prompt

    def test_ey_spread_not_standalone_decisive(self):
        prompt_lower = self._prompt.lower()
        assert "not a stock-specific bear case" in prompt_lower or \
               "not a distinguishing signal" in prompt_lower

    def test_structurally_common_language(self):
        assert "STRUCTURALLY COMMON" in self._prompt

    def test_cash_yields_not_decisive(self):
        assert "standalone decisive reason to HOLD" in self._prompt

    def test_momentum_label_mentioned(self):
        assert "momentum_label" in self._prompt

    def test_rs_label_mentioned(self):
        assert "rs_label" in self._prompt

    def test_volume_confirmation_mentioned(self):
        assert "olume confirmation" in self._prompt

    def test_growth_trajectory_mentioned(self):
        assert "GROWTH trajectory" in self._prompt

    def test_sector_drivers_mentioned(self):
        assert "NIM" in self._prompt
        assert "NAV" in self._prompt or "replacement-cost" in self._prompt

    def test_catalyst_proximity_mentioned(self):
        assert "atalyst proximity" in self._prompt

    def test_lean_buy_instruction(self):
        assert "lean BUY" in self._prompt

    def test_bear_must_identify_stock_specific_risk(self):
        assert "stock-level risk" in self._prompt or "stock-specific risk" in self._prompt


class TestP4ExtremeValuationGuardrail:
    """Verify the extreme-valuation guardrail prevents blind momentum-chasing."""

    @pytest.fixture(autouse=True)
    def prompt(self):
        self._prompt = _build_judge_prompt()

    def test_extreme_valuation_guardrail_present(self):
        assert "EXTREME VALUATION GUARDRAIL" in self._prompt

    def test_pe_threshold_mentioned(self):
        assert "40x" in self._prompt

    def test_momentum_alone_insufficient(self):
        assert "Do not override this with momentum alone" in self._prompt

    def test_speculation_warning(self):
        assert "speculation" in self._prompt.lower()

    def test_deteriorating_fundamentals_mentioned(self):
        assert "deteriorating fundamentals" in self._prompt


class TestP4ConsistencyGuardrail:
    """Verify structured-output consistency self-check is in the prompt."""

    @pytest.fixture(autouse=True)
    def prompt(self):
        self._prompt = _build_judge_prompt()

    def test_consistency_section_present(self):
        assert "Decision Consistency Self-Check" in self._prompt

    def test_mandatory_label(self):
        assert "MANDATORY" in self._prompt

    def test_logical_conclusion_requirement(self):
        assert "LOGICAL CONCLUSION" in self._prompt

    def test_hold_must_not_contain_execution_steps(self):
        assert "concrete execution steps" in self._prompt

    def test_conservatism_override_warning(self):
        assert "conservatism" in self._prompt


class TestP4NoRiskManagerChange:
    """Verify risk manager behavior is NOT changed by P4."""

    def test_risk_manager_source_unchanged(self):
        """Risk manager module should not reference P4 regime guidance."""
        import inspect
        from tradingagents.agents.managers import risk_manager
        source = inspect.getsource(risk_manager)
        assert "High-Rate Regime" not in source
        assert "P4" not in source

    def test_risk_limits_unchanged(self):
        """EGX_RISK_LIMITS should remain as-is."""
        from tradingagents.agents.risk_mgmt.risk_scorer import EGX_RISK_LIMITS
        assert EGX_RISK_LIMITS["max_position_vs_adv_pct"] == 0.10
        assert EGX_RISK_LIMITS["max_single_stock_pct"] == 0.10
        assert EGX_RISK_LIMITS["max_single_trade_loss_pct"] == 0.02
