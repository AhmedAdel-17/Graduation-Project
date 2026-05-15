"""
Hero test scenarios — 5 deterministic E2E tests proving core system logic.

No LLM calls. No network access. Each scenario constructs a mock AgentState,
feeds it through deterministic components (propagate_confidence, SignalProcessor,
risk scorer checks), and asserts expected behavior.

These tests directly answer: "Show me the system works correctly."
"""

import pytest

from tradingagents.graph.signal_processing import SignalProcessor
from tradingagents.graph.propagation import Propagator
from tradingagents.agents.risk_mgmt.risk_scorer import (
    check_short_selling_violation,
    check_leverage_violation,
    check_liquidity_participation,
    check_position_size_limit,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_state(
    *,
    tech_conf=None,
    fund_conf=None,
    news_conf=None,
    data_completeness=100,
    sentiment_blend=None,
):
    """Build a minimal state dict for propagate_confidence()."""
    state = {
        "technical_analysis": {"confidence_score": tech_conf} if tech_conf is not None else {},
        "fundamental_analysis": {"confidence_score": fund_conf} if fund_conf is not None else {},
        "sentiment_analysis": {
            "combined_sentiment": {"confidence": news_conf}
        } if news_conf is not None else {},
        "social_sentiment_analysis": {},
        "data_quality": {"data_completeness_score": data_completeness},
        "sentiment_blend_result": sentiment_blend,
    }
    return state


# ── Scenario 1: Strong BUY consensus ────────────────────────────────────────

class TestScenario1_StrongBuyConsensus:
    """All analysts agree: bullish with high confidence. System should produce
    BUY signal and high overall confidence."""

    def test_signal_extraction_buy(self):
        """SignalProcessor extracts BUY from a clear bullish signal."""
        sp = SignalProcessor()
        signal_text = (
            'After careful analysis, my recommendation is:\n'
            '{"action": "BUY", "confidence": "high"}\n'
            'FINAL TRANSACTION PROPOSAL: BUY'
        )
        assert sp.process_signal(signal_text) == "BUY"

    def test_confidence_all_bullish(self):
        """All 3 directional analysts return high confidence -> overall is high."""
        state = _make_state(tech_conf=0.85, fund_conf=0.90, news_conf=0.80)
        result = Propagator.propagate_confidence(state)

        assert result["overall_status"] == "OK"
        assert result["overall"] >= 0.80
        assert result["technical"] == 0.85
        assert result["fundamental"] == 0.90
        assert result["position_size_multiplier"] > 0

    def test_no_risk_veto_on_long_buy(self):
        """A standard BUY with no short selling or leverage -> no veto."""
        plan = {"action": "BUY", "position_size": 50000, "entry_price": 45.0}
        assert check_short_selling_violation(plan) is None
        assert check_leverage_violation(plan) is None


# ── Scenario 2: Risk veto on short-sell attempt ─────────────────────────────

class TestScenario2_RiskVetoShortSell:
    """Trader proposes SHORT — EGX constraints must block it."""

    def test_short_sell_detected(self):
        """Risk scorer catches short selling language."""
        plan = {"action": "short-sell COMI.CA", "shares": 1000}
        violation = check_short_selling_violation(plan)
        assert violation is not None
        assert violation.rule_name == "SHORT_SELLING_FORBIDDEN"
        assert violation.severity == "critical"

    def test_go_short_detected(self):
        """'go short' variant also caught."""
        plan = {"strategy": "go short on COMI.CA at 45 EGP"}
        violation = check_short_selling_violation(plan)
        assert violation is not None

    def test_leverage_detected(self):
        """Margin/leverage language caught."""
        plan = {"action": "BUY", "notes": "use 2x margin for this trade"}
        violation = check_leverage_violation(plan)
        assert violation is not None
        assert violation.rule_name == "LEVERAGE_FORBIDDEN"

    def test_signal_processor_veto_overrides(self):
        """VETO keyword in signal text -> HOLD regardless of other content."""
        sp = SignalProcessor()
        signal = "The analysis strongly suggests BUY, but risk manager says VETO."
        assert sp.process_signal(signal) == "HOLD"

    def test_short_term_not_false_positive(self):
        """'short-term' should NOT trigger short selling veto."""
        plan = {"strategy": "short-term investment in COMI.CA", "horizon": "1-4 weeks"}
        violation = check_short_selling_violation(plan)
        assert violation is None


# ── Scenario 3: Insufficient data (quorum failure) ──────────────────────────

class TestScenario3_QuorumFailure:
    """Only 1 of 3 directional analysts returns data. Quorum (>=2) fails.
    System should report INSUFFICIENT_DATA."""

    def test_single_analyst_insufficient(self):
        """Only technical analyst has data -> quorum fails."""
        state = _make_state(tech_conf=0.85, fund_conf=None, news_conf=None)
        result = Propagator.propagate_confidence(state)

        assert result["overall_status"] == "INSUFFICIENT_DATA"
        assert result["overall"] == 0.10  # floor value
        assert result["technical"] == 0.85
        assert result["fundamental"] is None
        assert result["sentiment"] is None

    def test_no_analysts_insufficient(self):
        """No analyst returns confidence -> quorum fails."""
        state = _make_state(tech_conf=None, fund_conf=None, news_conf=None)
        result = Propagator.propagate_confidence(state)

        assert result["overall_status"] == "INSUFFICIENT_DATA"
        assert result["overall"] == 0.10

    def test_two_analysts_meets_quorum(self):
        """Two analysts with data -> quorum met."""
        state = _make_state(tech_conf=0.80, fund_conf=0.75, news_conf=None)
        result = Propagator.propagate_confidence(state)

        assert result["overall_status"] == "OK"
        assert result["overall"] > 0.10

    def test_signal_processor_empty_input(self):
        """Empty signal text -> safe HOLD fallback."""
        sp = SignalProcessor()
        assert sp.process_signal("") == "HOLD"
        assert sp.process_signal(None) == "HOLD"


# ── Scenario 4: Low liquidity ───────────────────────────────────────────────

class TestScenario4_LowLiquidity:
    """Stock has low ADV — system should flag or reduce position."""

    def test_low_adv_flagged(self):
        """ADV below minimum threshold triggers a violation."""
        plan = {"position_sizing": {"target_shares": 5000}}
        violation = check_liquidity_participation(
            execution_plan=plan,
            avg_daily_volume=10_000,  # below 50k minimum
            low_liquidity=True,
        )
        assert violation is not None

    def test_normal_adv_passes(self):
        """ADV above threshold with reasonable position -> no violation."""
        plan = {"position_sizing": {"target_shares": 10_000}}
        violation = check_liquidity_participation(
            execution_plan=plan,
            avg_daily_volume=500_000,
            low_liquidity=False,
        )
        assert violation is None

    def test_confidence_with_incomplete_data(self):
        """Data quality penalty reduces overall confidence."""
        state_complete = _make_state(tech_conf=0.80, fund_conf=0.80, news_conf=0.80, data_completeness=100)
        state_incomplete = _make_state(tech_conf=0.80, fund_conf=0.80, news_conf=0.80, data_completeness=50)

        conf_complete = Propagator.propagate_confidence(state_complete)
        conf_incomplete = Propagator.propagate_confidence(state_incomplete)

        assert conf_incomplete["overall"] < conf_complete["overall"]


# ── Scenario 5: Conflicting signals ─────────────────────────────────────────

class TestScenario5_ConflictingSignals:
    """Tech says bullish, Fundamentals says bearish, News neutral.
    System should still function and produce a reasonable confidence."""

    def test_mixed_signals_produce_moderate_confidence(self):
        """Conflicting signals -> weakest-link dampening reduces confidence."""
        # High tech, low fund, medium news
        state = _make_state(tech_conf=0.90, fund_conf=0.30, news_conf=0.60)
        result = Propagator.propagate_confidence(state)

        assert result["overall_status"] == "OK"
        # Weakest-link dampening: 30% weight on min (0.30) should drag down
        assert result["overall"] < 0.70  # well below the mean of 0.60

    def test_signal_processor_json_takes_priority(self):
        """JSON action field overrides bare keywords in conflicting text."""
        sp = SignalProcessor()
        signal = (
            'The bear case suggests SELL, but our final assessment:\n'
            '{"action": "HOLD", "rationale": "too much uncertainty"}'
        )
        # JSON "action": "HOLD" should take priority over the bare SELL word
        assert sp.process_signal(signal) == "HOLD"

    def test_signal_processor_proposal_over_bare(self):
        """FINAL TRANSACTION PROPOSAL takes priority over bare keywords."""
        sp = SignalProcessor()
        signal = (
            "We considered BUY but given the bearish fundamentals...\n"
            "FINAL TRANSACTION PROPOSAL: HOLD"
        )
        assert sp.process_signal(signal) == "HOLD"

    def test_position_size_limit_on_concentrated_bet(self):
        """A too-large position gets flagged even with bullish signals."""
        plan = {"position_sizing": {"portfolio_allocation": "50%"}}
        violation = check_position_size_limit(
            execution_plan=plan,
            portfolio_value=10_000_000,
        )
        assert violation is not None
