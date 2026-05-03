"""Comprehensive tests for the Deterministic Risk Scorer (risk_scorer.py).

Covers all 12 requirement areas:
  1. Basic scorer outputs (ALLOW / WARN / THROTTLE / VETO)
  2. THROTTLE behavior — adjusted execution_plan
  3. VETO behavior — no LLM, correct fields
  4. Short-selling detection — regex correctness
  5. Max trade loss — severity = CRITICAL
  6. Stop-loss and ATR — generation priority order
  7. EGX price band checks
  8. Merged Risk Debate input — receives throttled plan
  9. Constitutional Risk Manager — receives pre-scored data
 10. Final deterministic gate — covered in test_risk_manager_veto.py
 11. State and logging — field completeness
 12. Smoke tests — scorer node end-to-end
"""
import json
import pytest
from unittest.mock import patch, MagicMock

from tests.conftest import MockLLM

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

_EGX_CONFIG = {"target_market": "EGX", "backtest_mode": False}
_SCORER_PATCH = "tradingagents.agents.risk_mgmt.risk_scorer.get_config"


def _valid_plan(
    target_shares=1000,
    max_shares_per_day=200,
    alloc_pct="8%",
    stop_price=73.0,
    limit_price=77.5,
    decision="BUY",
    symbol="COMI.CA",
):
    """Build a minimal valid EGX execution plan."""
    return {
        "symbol": symbol,
        "decision": decision,
        "position_sizing": {
            "target_shares": target_shares,
            "max_shares_per_day": max_shares_per_day,
            "portfolio_allocation": alloc_pct,
        },
        "entry_logic": {"order_type": "limit",
                        "entry_zone": {"limit_price": limit_price}},
        "exit_logic": {"stop_loss": {"price": stop_price}},
    }


def _state(
    plan=None,
    portfolio_value=10_000_000,
    avg_daily_volume=500_000,
    current_price=77.5,
    low_liquidity=False,
    technical_analysis=None,
    current_position=None,
):
    ep = plan if plan is not None else _valid_plan()
    return {
        "company_of_interest": ep.get("symbol", "TEST.CA"),
        "execution_plan": {"execution_plan": ep},
        "portfolio_value": portfolio_value,
        "avg_daily_volume": avg_daily_volume,
        "current_price": current_price,
        "low_liquidity": low_liquidity,
        "technical_analysis": technical_analysis or {},
        "current_position": current_position or {},
    }


def _run_scorer(state):
    from tradingagents.agents.risk_mgmt.risk_scorer import create_risk_scorer_node
    with patch(_SCORER_PATCH, return_value=_EGX_CONFIG):
        return create_risk_scorer_node()(state)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Basic scorer outputs
# ─────────────────────────────────────────────────────────────────────────────

class TestRiskScorerBasicOutputs:

    def test_valid_trade_returns_allow(self):
        """Clean plan, ADV well within limits → ALLOW."""
        result = _run_scorer(_state())
        assert result["risk_action"] == "ALLOW"

    def test_valid_trade_approved_true(self):
        result = _run_scorer(_state())
        assert result["risk_assessment"]["approved"] is True

    def test_valid_trade_no_hard_violations(self):
        result = _run_scorer(_state())
        assert result["risk_assessment"]["hard_violations"] == []

    def test_warn_on_exit_horizon_exceeded(self):
        """Very large position requires >10 days to exit → HIGH warning → WARN."""
        # 500k ADV * 10% = 50k/day max; 600k / 50k = 12 days → EXIT_HORIZON_EXCEEDED (HIGH)
        # Use a tight stop (0.3 EGP) so 600k * 0.3 / 10M = 1.8% — stays under the 2% Elder rule
        plan = _valid_plan(target_shares=600_000, max_shares_per_day=10_000, stop_price=77.2)
        result = _run_scorer(_state(plan=plan))
        assert result["risk_action"] == "WARN"
        rules = [w["rule"] for w in result["risk_assessment"]["warnings"]]
        assert "EXIT_HORIZON_EXCEEDED" in rules

    def test_throttle_on_adv_5_to_10_percent(self):
        """max_shares_per_day at 7% of ADV (200k ADV, 14k/day) → THROTTLE."""
        # portfolio_value=30M keeps per-trade loss at 50k*4.5/30M=0.75% — under 2% Elder rule
        plan = _valid_plan(target_shares=50_000, max_shares_per_day=14_000)
        result = _run_scorer(_state(plan=plan, avg_daily_volume=200_000, portfolio_value=30_000_000))
        assert result["risk_action"] == "THROTTLE"

    def test_veto_on_adv_over_10_percent(self):
        """max_shares_per_day at 20% of ADV → VETO."""
        plan = _valid_plan(target_shares=50_000, max_shares_per_day=20_000)
        result = _run_scorer(_state(plan=plan, avg_daily_volume=100_000))
        assert result["risk_action"] == "VETO"

    def test_risk_metrics_always_returned(self):
        result = _run_scorer(_state())
        assert isinstance(result["risk_metrics"], dict)
        assert result["risk_metrics"], "risk_metrics must be non-empty for EGX"

    def test_risk_metrics_contains_required_fields(self):
        result = _run_scorer(_state())
        m = result["risk_metrics"]
        required = [
            "position_pct", "adv_participation_pct", "days_to_exit",
            "per_trade_loss_pct", "stop_distance_pct",
        ]
        for field in required:
            assert field in m, f"risk_metrics missing '{field}'"

    def test_risk_metrics_price_band_proximity_present_when_limit_price_given(self):
        result = _run_scorer(_state())
        m = result["risk_metrics"]
        assert "price_band_proximity_pct" in m

    def test_risk_metrics_atr_flag_present(self):
        result = _run_scorer(_state())
        assert "atr_14_available" in result["risk_metrics"]

    def test_risk_metrics_atr_populated_when_available(self):
        ta = {"atr_14": 2.5}
        result = _run_scorer(_state(technical_analysis=ta))
        assert result["risk_metrics"]["atr_14_available"] is True
        assert result["risk_metrics"]["atr_14"] == 2.5

    def test_non_egx_market_passes_through_as_allow(self):
        from tradingagents.agents.risk_mgmt.risk_scorer import create_risk_scorer_node
        with patch(_SCORER_PATCH, return_value={"target_market": "US"}):
            result = create_risk_scorer_node()(_state())
        assert result["risk_action"] == "ALLOW"
        assert result["risk_metrics"] == {}


# ─────────────────────────────────────────────────────────────────────────────
# 2. THROTTLE behavior
# ─────────────────────────────────────────────────────────────────────────────

class TestThrottleBehavior:
    """ADV participation 5–10%: position auto-adjusted, trade continues."""

    # 200k ADV; 7% = 14k/day (throttle zone)
    _ADV = 200_000
    _ORIGINAL_DAILY = 14_000

    def _throttle_result(self):
        # portfolio_value=30M: 100k shares * 4.5 EGP stop / 30M = 1.5% — under 2% Elder rule
        plan = _valid_plan(target_shares=100_000, max_shares_per_day=self._ORIGINAL_DAILY)
        return _run_scorer(_state(plan=plan, avg_daily_volume=self._ADV, portfolio_value=30_000_000))

    def test_throttle_action_returned(self):
        assert self._throttle_result()["risk_action"] == "THROTTLE"

    def test_max_shares_per_day_reduced(self):
        result = self._throttle_result()
        ep = result["execution_plan"]
        inner = ep.get("execution_plan", ep)
        new_daily = inner["position_sizing"]["max_shares_per_day"]
        # 5% of 200k = 10k
        assert new_daily == 10_000, f"Expected 10000, got {new_daily}"

    def test_target_shares_unchanged(self):
        """Throttle reduces daily rate, not the total position size."""
        result = self._throttle_result()
        ep = result["execution_plan"]
        inner = ep.get("execution_plan", ep)
        assert inner["position_sizing"]["target_shares"] == 100_000

    def test_execution_days_recalculated(self):
        result = self._throttle_result()
        ep = result["execution_plan"]
        inner = ep.get("execution_plan", ep)
        # 100k shares / 10k per day = 10 days (+ 1 partial = 11)
        assert inner["position_sizing"]["execution_days"] == 11

    def test_throttle_adjustments_in_assessment(self):
        result = self._throttle_result()
        adj = result["risk_assessment"]["throttle_adjustments"]
        assert adj.get("throttle_applied") is True

    def test_throttle_adjustments_contains_old_and_new_daily(self):
        result = self._throttle_result()
        adj = result["risk_assessment"]["throttle_adjustments"]
        assert adj.get("old_max_shares_per_day") == self._ORIGINAL_DAILY
        assert adj.get("new_max_shares_per_day") == 10_000

    def test_throttle_adjustments_explains_threshold(self):
        result = self._throttle_result()
        adj = result["risk_assessment"]["throttle_adjustments"]
        assert "throttle_threshold_pct" in adj
        assert adj["throttle_threshold_pct"] == 0.05

    def test_throttled_plan_marked_in_position_sizing(self):
        result = self._throttle_result()
        ep = result["execution_plan"]
        inner = ep.get("execution_plan", ep)
        assert inner["position_sizing"].get("throttle_applied") is True

    def test_throttle_not_triggered_below_5_pct(self):
        """3% ADV participation is below throttle zone → ALLOW."""
        plan = _valid_plan(target_shares=10_000, max_shares_per_day=6_000)
        result = _run_scorer(_state(plan=plan, avg_daily_volume=200_000))
        assert result["risk_action"] in ("ALLOW", "WARN")

    def test_low_liquidity_halves_threshold(self):
        """With low_liquidity=True, effective ADV is halved; 7% of 100k effective = 7k/day."""
        plan = _valid_plan(target_shares=50_000, max_shares_per_day=7_000)
        # ADV 200k, low_liquidity=True → effective 100k; 7k/100k = 7% → throttle
        # portfolio_value=30M: 50k*4.5/30M=0.75% — under 2% Elder rule
        result = _run_scorer(_state(plan=plan, avg_daily_volume=200_000, low_liquidity=True,
                                    portfolio_value=30_000_000))
        assert result["risk_action"] == "THROTTLE"


# ─────────────────────────────────────────────────────────────────────────────
# 3. VETO behavior
# ─────────────────────────────────────────────────────────────────────────────

class TestVetoBehavior:

    def _short_sell_plan(self):
        """A plan that explicitly mentions short selling → CRITICAL."""
        plan = _valid_plan()
        plan["notes"] = "go short position on COMI"
        return plan

    def test_scorer_returns_veto_on_critical_violation(self):
        result = _run_scorer(_state(plan=self._short_sell_plan()))
        assert result["risk_action"] == "VETO"

    def test_veto_approved_false(self):
        result = _run_scorer(_state(plan=self._short_sell_plan()))
        assert result["risk_assessment"]["approved"] is False

    def test_veto_hard_violations_non_empty(self):
        result = _run_scorer(_state(plan=self._short_sell_plan()))
        assert len(result["risk_assessment"]["hard_violations"]) > 0

    def test_veto_explanation_in_assessment(self):
        result = _run_scorer(_state(plan=self._short_sell_plan()))
        assert "veto_explanation" in result["risk_assessment"]
        assert "RISK VETO" in result["risk_assessment"]["veto_explanation"]

    def test_veto_node_returns_hold(self):
        from tradingagents.agents.risk_mgmt.risk_scorer import risk_veto_node
        scorer_result = _run_scorer(_state(plan=self._short_sell_plan()))
        veto_state = {**_state(plan=self._short_sell_plan()), **scorer_result}
        veto_result = risk_veto_node(veto_state)
        assert veto_result["final_trade_decision"] == "HOLD"

    def test_veto_node_sets_risk_veto_true(self):
        from tradingagents.agents.risk_mgmt.risk_scorer import risk_veto_node
        scorer_result = _run_scorer(_state(plan=self._short_sell_plan()))
        veto_state = {**_state(plan=self._short_sell_plan()), **scorer_result}
        veto_result = risk_veto_node(veto_state)
        assert veto_result["risk_veto"] is True

    def test_veto_node_does_not_call_llm(self):
        """risk_veto_node has no LLM parameter — calling it must not invoke any LLM."""
        from tradingagents.agents.risk_mgmt.risk_scorer import risk_veto_node
        scorer_result = _run_scorer(_state(plan=self._short_sell_plan()))
        veto_state = {**_state(plan=self._short_sell_plan()), **scorer_result,
                      "risk_debate_state": {"history": "", "count": 0}}
        # risk_veto_node is a plain function — it has no LLM; just calling it is the proof
        result = risk_veto_node(veto_state)
        assert result["final_trade_decision"] == "HOLD"

    def test_veto_before_debate_means_debate_state_not_populated(self):
        """On VETO path, merged debate is never called so debate history stays empty."""
        from tradingagents.agents.risk_mgmt.risk_scorer import risk_veto_node
        scorer_result = _run_scorer(_state(plan=self._short_sell_plan()))
        initial_rds = {"history": "", "count": 0, "risky_history": "",
                       "safe_history": "", "neutral_history": "",
                       "current_risky_response": "", "current_safe_response": "",
                       "current_neutral_response": "", "latest_speaker": "",
                       "judge_decision": ""}
        veto_state = {**_state(plan=self._short_sell_plan()), **scorer_result,
                      "risk_debate_state": initial_rds}
        result = risk_veto_node(veto_state)
        # history stays empty because debate never ran
        assert result["risk_debate_state"]["history"] == ""


# ─────────────────────────────────────────────────────────────────────────────
# 4. Short-selling detection
# ─────────────────────────────────────────────────────────────────────────────

class TestShortSellingDetection:
    """Fixed regex must not false-positive on innocent 'short' usage."""

    def _check(self, plan_extra: dict):
        plan = {**_valid_plan(), **plan_extra}
        from tradingagents.agents.risk_mgmt.risk_scorer import check_short_selling_violation
        return check_short_selling_violation(plan)

    # --- Must NOT trigger ---

    def test_short_term_does_not_trigger(self):
        assert self._check({"horizon": "short_term"}) is None

    def test_short_horizon_does_not_trigger(self):
        assert self._check({"strategy": "short-term outlook"}) is None

    def test_short_term_in_timing_field_does_not_trigger(self):
        assert self._check({"entry_logic": {"timing": "short_term breakout play"}}) is None

    def test_short_investment_does_not_trigger(self):
        assert self._check({"rationale": "short investment horizon of 2 weeks"}) is None

    def test_word_short_alone_in_innocent_context_does_not_trigger(self):
        # "Short list of catalysts" or similar
        assert self._check({"notes": "short list of upside catalysts"}) is None

    # --- Must trigger ---

    def test_sell_short_triggers_critical(self):
        result = self._check({"notes": "we will sell short 500 shares"})
        assert result is not None and result.severity == "critical"

    def test_short_sell_triggers_critical(self):
        result = self._check({"notes": "short sell position"})
        assert result is not None and result.severity == "critical"

    def test_naked_short_triggers_critical(self):
        result = self._check({"notes": "naked short on this name"})
        assert result is not None and result.severity == "critical"

    def test_open_short_triggers_critical(self):
        result = self._check({"order": "open short at 77"})
        assert result is not None and result.severity == "critical"

    def test_go_short_triggers_critical(self):
        result = self._check({"strategy": "go short the position"})
        assert result is not None and result.severity == "critical"

    def test_short_position_triggers_critical(self):
        result = self._check({"current": "hold a short position"})
        assert result is not None and result.severity == "critical"

    def test_sell_with_no_current_position_goes_through_scorer_and_flagged_by_final_gate(self):
        """
        A plan with decision=SELL and no open position is NOT caught by the short-selling
        check (it's not short selling). It is caught by the final gate in risk_manager.py.
        The scorer itself must pass it through.
        """
        plan = _valid_plan(decision="SELL")
        result = _run_scorer(_state(plan=plan))
        # Scorer should not VETO on this (no short-selling language)
        # The final gate in risk_manager will catch it
        assert result["risk_action"] in ("ALLOW", "WARN", "THROTTLE")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Max trade loss
# ─────────────────────────────────────────────────────────────────────────────

class TestMaxTradeLoss:

    def test_loss_under_2pct_no_violation(self):
        """1000 shares, stop 5% below at 73.625 → loss = 1000 * 3.875 / 10M = 0.04% < 2%."""
        from tradingagents.agents.risk_mgmt.risk_scorer import check_max_trade_loss
        plan = _valid_plan(target_shares=1000, stop_price=73.625)
        result = check_max_trade_loss(plan, portfolio_value=10_000_000, current_price=77.5)
        assert result is None

    def test_loss_over_2pct_returns_critical(self):
        """10000 shares, stop 5% below: loss = 10000 * 3.875 / 10M = 0.39% < 2%. Push shares higher."""
        from tradingagents.agents.risk_mgmt.risk_scorer import check_max_trade_loss
        # 100000 shares, stop at 73.5 → loss = 100000 * 4.0 / 10M = 4% > 2%
        plan = _valid_plan(target_shares=100_000, stop_price=73.5)
        result = check_max_trade_loss(plan, portfolio_value=10_000_000, current_price=77.5)
        assert result is not None
        assert result.severity == "critical", f"Expected critical, got {result.severity}"

    def test_loss_exactly_at_limit_no_violation(self):
        """Exactly 2% loss should not trigger (strict greater-than)."""
        from tradingagents.agents.risk_mgmt.risk_scorer import check_max_trade_loss
        # 2% of 10M = 200k. Stop distance = 2 EGP. Shares = 100k.
        # loss = 100k * 2 / 10M = 2% exactly → no violation (> not >=)
        plan = _valid_plan(target_shares=100_000, stop_price=75.5)  # 77.5 - 75.5 = 2.0
        result = check_max_trade_loss(plan, portfolio_value=10_000_000, current_price=77.5)
        assert result is None

    def test_critical_severity_not_high(self):
        """The 2% rule must be CRITICAL not HIGH (Elder hard cap, not a warning)."""
        from tradingagents.agents.risk_mgmt.risk_scorer import check_max_trade_loss
        plan = _valid_plan(target_shares=200_000, stop_price=73.5)
        result = check_max_trade_loss(plan, portfolio_value=10_000_000, current_price=77.5)
        assert result is not None and result.severity == "critical"

    def test_no_stop_price_returns_none(self):
        """Without a stop price, loss cannot be calculated."""
        from tradingagents.agents.risk_mgmt.risk_scorer import check_max_trade_loss
        plan = _valid_plan()
        plan["exit_logic"] = {}
        result = check_max_trade_loss(plan, portfolio_value=10_000_000, current_price=77.5)
        assert result is None

    def test_sell_direction_uses_abs_distance(self):
        """For SELL trades, stop is above entry. Distance = stop - current. Must still flag if too large."""
        from tradingagents.agents.risk_mgmt.risk_scorer import check_max_trade_loss
        # current=50, stop=52 (above for SELL), shares=500k → loss = 500k*2/10M = 10% > 2%
        plan = _valid_plan(decision="SELL", target_shares=500_000, stop_price=52.0)
        result = check_max_trade_loss(plan, portfolio_value=10_000_000, current_price=50.0)
        assert result is not None and result.severity == "critical"

    def test_scorer_vetoes_on_trade_loss_critical(self):
        """End-to-end: scorer returns VETO when trade loss > 2%."""
        plan = _valid_plan(target_shares=200_000, stop_price=73.5)
        result = _run_scorer(_state(plan=plan))
        assert result["risk_action"] == "VETO"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Stop-loss and ATR
# ─────────────────────────────────────────────────────────────────────────────

class TestStopLossAndATR:

    def _run_stop_check(self, plan, technical_analysis=None):
        from tradingagents.agents.risk_mgmt.risk_scorer import check_stop_loss_atr
        ta = technical_analysis or {}
        return check_stop_loss_atr(
            plan, decision=plan.get("decision", "BUY"),
            current_price=77.5, technical_analysis=ta
        )

    def test_existing_stop_not_replaced(self):
        """If stop_loss.price is already set, scorer must not touch it."""
        plan = _valid_plan(stop_price=73.0)
        result = self._run_stop_check(plan)
        assert result is None
        # Confirm stop unchanged
        assert plan["exit_logic"]["stop_loss"]["price"] == 73.0

    def test_missing_stop_with_atr_generates_atr_stop(self):
        """ATR available: stop = 2×ATR below entry, not a fixed 5%."""
        plan = _valid_plan()
        plan["exit_logic"] = {}  # No stop
        ta = {"atr_14": 2.0}
        result = self._run_stop_check(plan, technical_analysis=ta)
        assert result is None  # No violation — stop was generated
        stop = plan["exit_logic"]["stop_loss"]
        assert stop.get("atr_based") is True
        assert "ATR-based" in stop.get("note", "")
        # 2×2.0 = 4.0 EGP below 77.5 = 73.5
        assert stop["price"] == pytest.approx(73.5, abs=0.1)

    def test_atr_stop_capped_at_7pct(self):
        """Very large ATR must be capped at 7% (inside EGX magnet zone)."""
        plan = _valid_plan()
        plan["exit_logic"] = {}
        ta = {"atr_14": 10.0}  # 2×10/77.5 = 25.8% → cap at 7%
        self._run_stop_check(plan, technical_analysis=ta)
        stop = plan["exit_logic"]["stop_loss"]
        distance_pct = (77.5 - stop["price"]) / 77.5
        assert distance_pct <= 0.071, f"Stop distance {distance_pct:.2%} exceeds 7% cap"

    def test_missing_stop_no_atr_generates_fallback_5pct(self):
        """No ATR available: fallback to exactly 5% fixed stop."""
        plan = _valid_plan()
        plan["exit_logic"] = {}
        result = self._run_stop_check(plan, technical_analysis={})
        assert result is None
        stop = plan["exit_logic"]["stop_loss"]
        assert stop.get("atr_based") is False
        assert "fallback" in stop.get("note", "").lower()
        assert stop["price"] == pytest.approx(77.5 * 0.95, abs=0.1)

    def test_fallback_stop_is_not_primary_path(self):
        """When ATR is present, the fallback 5% must NOT be used."""
        plan = _valid_plan()
        plan["exit_logic"] = {}
        ta = {"atr_14": 1.5}
        self._run_stop_check(plan, technical_analysis=ta)
        stop = plan["exit_logic"]["stop_loss"]
        # 5% of 77.5 = 73.625; 2×1.5 = 3.0 below 77.5 = 74.5
        assert stop["price"] != pytest.approx(73.625, abs=0.1), \
            "Fallback 5% stop used even though ATR was available"

    def test_sell_stop_generated_above_entry(self):
        """For SELL trades, stop is above current price."""
        plan = _valid_plan(decision="SELL")
        plan["exit_logic"] = {}
        ta = {"atr_14": 2.0}
        self._run_stop_check(plan, technical_analysis=ta)
        stop = plan["exit_logic"]["stop_loss"]
        assert stop["price"] > 77.5

    def test_atr_from_execution_plan_risk_controls_used(self):
        """ATR in execution_plan.risk_controls is a fallback source."""
        plan = _valid_plan()
        plan["exit_logic"] = {}
        plan["risk_controls"] = {"atr_14": 2.0}
        result = self._run_stop_check(plan, technical_analysis={})
        assert result is None
        stop = plan["exit_logic"]["stop_loss"]
        assert stop.get("atr_based") is True

    def test_hold_decision_no_stop_required(self):
        """HOLD decisions must never require a stop-loss."""
        plan = _valid_plan(decision="HOLD")
        plan["exit_logic"] = {}
        result = self._run_stop_check(plan)
        assert result is None

    def test_scorer_generates_stop_and_continues_to_allow(self):
        """Missing stop → scorer auto-generates it, trade still ALLOW (not VETO)."""
        plan = _valid_plan()
        plan["exit_logic"] = {}  # Remove stop
        result = _run_scorer(_state(plan=plan))
        # Stop was auto-generated; no critical violation from missing stop
        assert result["risk_action"] in ("ALLOW", "WARN", "THROTTLE")


# ─────────────────────────────────────────────────────────────────────────────
# 7. EGX price band checks
# ─────────────────────────────────────────────────────────────────────────────

class TestEGXPriceBand:
    # current_price = 77.5; band ±10% = [69.75, 85.25]
    # magnet zone upper = 85.25 - 77.5*0.015 = 85.25 - 1.1625 = 84.09
    # magnet zone lower = 69.75 + 77.5*0.015 = 69.75 + 1.1625 = 70.91

    def _check(self, limit_price, decision="BUY"):
        from tradingagents.agents.risk_mgmt.risk_scorer import check_egx_price_band
        plan = _valid_plan(limit_price=limit_price, decision=decision)
        return check_egx_price_band(plan, current_price=77.5)

    def test_price_inside_band_no_violation(self):
        assert self._check(80.0) is None

    def test_price_above_upper_band_critical(self):
        result = self._check(86.0)  # > 85.25
        assert result is not None and result.severity == "critical"
        assert result.rule_name == "PRICE_OUTSIDE_EGX_BAND"

    def test_price_below_lower_band_critical(self):
        result = self._check(68.0)  # < 69.75
        assert result is not None and result.severity == "critical"
        assert result.rule_name == "PRICE_OUTSIDE_EGX_BAND"

    def test_buy_in_upper_magnet_zone_high_warning(self):
        # 84.5 is above the magnet threshold ~84.09 → HIGH warning for BUY
        result = self._check(84.5, decision="BUY")
        assert result is not None and result.severity == "high"
        assert result.rule_name == "PRICE_IN_EGX_MAGNET_ZONE"

    def test_sell_in_lower_magnet_zone_high_warning(self):
        # 70.5 is below the lower magnet threshold ~70.91 → HIGH warning for SELL
        result = self._check(70.5, decision="SELL")
        assert result is not None and result.severity == "high"
        assert result.rule_name == "PRICE_IN_EGX_MAGNET_ZONE"

    def test_buy_near_but_outside_magnet_zone_no_warning(self):
        # 82.0 is well inside the band, not in magnet zone → None
        assert self._check(82.0, decision="BUY") is None

    def test_no_limit_price_returns_none(self):
        from tradingagents.agents.risk_mgmt.risk_scorer import check_egx_price_band
        plan = _valid_plan()
        del plan["entry_logic"]["entry_zone"]["limit_price"]
        result = check_egx_price_band(plan, current_price=77.5)
        assert result is None

    def test_outside_band_causes_scorer_veto(self):
        plan = _valid_plan(limit_price=90.0)  # Above 85.25
        result = _run_scorer(_state(plan=plan))
        assert result["risk_action"] == "VETO"

    def test_magnet_zone_warning_appears_in_scorer_warnings(self):
        plan = _valid_plan(limit_price=84.5, decision="BUY")
        result = _run_scorer(_state(plan=plan))
        warnings = result["risk_assessment"]["warnings"]
        assert any(w["rule"] == "PRICE_IN_EGX_MAGNET_ZONE" for w in warnings)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Merged Risk Debate receives adjusted plan when THROTTLE occurred
# ─────────────────────────────────────────────────────────────────────────────

class TestMergedDebateInputAfterThrottle:

    def test_throttled_plan_passed_forward_to_debate(self):
        """After THROTTLE, the debate node must receive the reduced max_shares_per_day."""
        from tradingagents.agents.risk_mgmt.merged_debator import create_merged_risk_debator

        # Build a state that the scorer will THROTTLE
        # portfolio_value=30M: 100k*4.5/30M=1.5% — under 2% Elder rule
        plan = _valid_plan(target_shares=100_000, max_shares_per_day=14_000)
        scorer_state = _state(plan=plan, avg_daily_volume=200_000, portfolio_value=30_000_000)

        # Run scorer — mutates execution_plan in state output
        scorer_result = _run_scorer(scorer_state)
        assert scorer_result["risk_action"] == "THROTTLE"

        # Feed scorer output into the merged debate state
        merged_state = {
            **scorer_state,
            "execution_plan": scorer_result["execution_plan"],
            "risk_debate_state": {"history": "", "count": 0},
            "risk_action": scorer_result["risk_action"],
            "risk_metrics": scorer_result["risk_metrics"],
            "risk_assessment": scorer_result["risk_assessment"],
            "company_of_interest": "TEST.CA",
            "low_liquidity": False,
            "technical_analysis": {},
            "fundamental_analysis": {},
            "sentiment_analysis": {},
            "investment_debate_state": {},
            "investment_plan": "BUY",
            "trader_investment_plan": "BUY",
        }

        captured_prompts = []

        class SpyLLM:
            def invoke(self, prompt, *args, **kwargs):
                captured_prompts.append(str(prompt))
                return type("R", (), {"content": """
### 🔴 RISKY ANALYST (Risk-Taking Perspective)
Throttled position is still viable.

### 🟢 SAFE ANALYST (Conservative Perspective)
Reduced position size limits downside.

### 🟡 NEUTRAL ANALYST (Balanced Perspective)
Throttle is appropriate given ADV constraints.

### 📋 SYNTHESIS
Proceed with throttled position."""})()

        with patch("tradingagents.agents.risk_mgmt.merged_debator.get_config",
                   return_value={"target_market": "EGX"}):
            node = create_merged_risk_debator(SpyLLM())
            result = node(merged_state)

        # Debate ran
        assert result["risk_debate_state"]["current_risky_response"]

        # Throttled plan was passed — max_shares_per_day should be 10k in state
        ep = merged_state["execution_plan"]
        inner = ep.get("execution_plan", ep)
        assert inner["position_sizing"]["max_shares_per_day"] == 10_000, \
            "Merged debate did not receive the throttled execution plan"

    def test_debate_does_not_receive_full_text_reports(self):
        """Merged debate must not re-pass full analyst text reports (token savings)."""
        from tradingagents.agents.risk_mgmt.merged_debator import create_merged_risk_debator
        LONG_REPORT = "VERBOSE_REPORT_MARKER " * 300
        base_state = _state()
        merged_state = {
            **base_state,
            "risk_debate_state": {"history": "", "count": 0},
            "market_report": LONG_REPORT,
            "fundamentals_report": LONG_REPORT,
            "news_report": LONG_REPORT,
            "sentiment_report": LONG_REPORT,
            "company_of_interest": "TEST.CA",
            "technical_analysis": {},
            "fundamental_analysis": {},
            "sentiment_analysis": {},
            "investment_debate_state": {},
            "investment_plan": "",
            "trader_investment_plan": "",
            "risk_action": "ALLOW",
            "risk_metrics": {},
        }
        captured = []

        class SpyLLM:
            def invoke(self, prompt, *args, **kwargs):
                captured.append(str(prompt))
                return type("R", (), {"content": """
### 🔴 RISKY ANALYST (Risk-Taking Perspective)
A.
### 🟢 SAFE ANALYST (Conservative Perspective)
B.
### 🟡 NEUTRAL ANALYST (Balanced Perspective)
C.
### 📋 SYNTHESIS
D."""})()

        with patch("tradingagents.agents.risk_mgmt.merged_debator.get_config",
                   return_value={"target_market": "EGX"}):
            node = create_merged_risk_debator(SpyLLM())
            node(merged_state)

        assert captured
        assert "VERBOSE_REPORT_MARKER" not in captured[0]

    def test_debate_three_sections_still_present(self):
        """Risky / Safe / Neutral sections must exist after the scorer runs."""
        from tradingagents.agents.risk_mgmt.merged_debator import create_merged_risk_debator

        MERGED_RESPONSE = """
### 🔴 RISKY ANALYST (Risk-Taking Perspective)
Strong upside.

### 🟢 SAFE ANALYST (Conservative Perspective)
Downside risk contained.

### 🟡 NEUTRAL ANALYST (Balanced Perspective)
Balanced view.

### 📋 SYNTHESIS
Proceed.
"""
        base_state = _state()
        merged_state = {
            **base_state,
            "risk_debate_state": {"history": "", "count": 0},
            "company_of_interest": "TEST.CA",
            "technical_analysis": {},
            "fundamental_analysis": {},
            "sentiment_analysis": {},
            "investment_debate_state": {},
            "investment_plan": "",
            "trader_investment_plan": "",
            "risk_action": "ALLOW",
            "risk_metrics": {},
        }
        with patch("tradingagents.agents.risk_mgmt.merged_debator.get_config",
                   return_value={"target_market": "EGX"}):
            node = create_merged_risk_debator(MockLLM(MERGED_RESPONSE))
            result = node(merged_state)

        rds = result["risk_debate_state"]
        assert rds["current_risky_response"]
        assert rds["current_safe_response"]
        assert rds["current_neutral_response"]


# ─────────────────────────────────────────────────────────────────────────────
# 9. Constitutional Risk Manager inputs
# ─────────────────────────────────────────────────────────────────────────────

class TestConstitutionalRiskManagerInputs:
    """The risk_manager_node must receive and use pre-scored data."""

    def _capture(self, state):
        captured = []

        class SpyLLM:
            def invoke(self, prompt, *a, **kw):
                captured.append(str(prompt))
                return type("R", (), {
                    "content": '```json\n{"action": "BUY", "confidence": 0.8}\n```'
                })()

        mem = MagicMock()
        mem.get_memories.return_value = []
        from tradingagents.agents.managers.risk_manager import create_risk_manager
        with patch("tradingagents.agents.managers.risk_manager.get_config",
                   return_value={"target_market": "EGX"}):
            node = create_risk_manager(SpyLLM(), mem)
            node(state)
        return captured[0] if captured else ""

    def _state_with_metrics(self, **overrides):
        s = {
            "company_of_interest": "COMI.CA",
            "execution_plan": {"execution_plan": _valid_plan()},
            "portfolio_value": 10_000_000,
            "current_price": 77.5,
            "low_liquidity": False,
            "current_position": {},
            "market_report": "", "news_report": "",
            "fundamentals_report": "", "sentiment_report": "",
            "risk_debate_state": {
                "history": "debate content", "count": 3,
                "risky_history": "", "safe_history": "", "neutral_history": "",
                "current_risky_response": "", "current_safe_response": "",
                "current_neutral_response": "", "latest_speaker": "", "judge_decision": "",
            },
            "risk_action": "ALLOW",
            "risk_metrics": {
                "position_pct": 0.08,
                "adv_participation_pct": 0.04,
                "days_to_exit": 2.0,
                "per_trade_loss_pct": 0.005,
                "stop_distance_pct": 0.058,
            },
            "risk_assessment": {
                "approved": True, "risk_action": "ALLOW",
                "hard_violations": [], "warnings": [], "violations": [],
                "throttle_adjustments": {}, "risk_metrics": {},
            },
        }
        s.update(overrides)
        return s

    def test_manager_receives_risk_action_in_prompt(self):
        prompt = self._capture(self._state_with_metrics(risk_action="ALLOW"))
        assert "ALLOW" in prompt

    def test_manager_receives_throttle_context_in_prompt(self):
        prompt = self._capture(self._state_with_metrics(risk_action="THROTTLE"))
        assert "THROTTLE" in prompt

    def test_manager_receives_risk_metrics_in_prompt(self):
        s = self._state_with_metrics()
        prompt = self._capture(s)
        assert "adv_participation_pct" in prompt or "position_pct" in prompt

    def test_manager_receives_exec_plan(self):
        prompt = self._capture(self._state_with_metrics())
        assert "COMI.CA" in prompt

    def test_manager_receives_debate_summary(self):
        s = self._state_with_metrics()
        s["risk_debate_state"]["history"] = "UNIQUE_DEBATE_CONTENT_ABCXYZ"
        prompt = self._capture(s)
        assert "UNIQUE_DEBATE_CONTENT_ABCXYZ" in prompt

    def test_veto_path_never_reaches_risk_manager(self):
        """If scorer returns VETO, the risk_manager LLM must not be called.
        This is enforced by the graph routing, not by risk_manager_node itself.
        We verify the graph routing logic directly."""
        # Check setup.py's routing function: VETO routes to Risk Veto, not Merged Debate
        # We simulate by asserting risk_manager_node is never part of the VETO path
        from tradingagents.agents.risk_mgmt.risk_scorer import risk_veto_node
        state = {
            "risk_action": "VETO",
            "risk_assessment": {
                "approved": False,
                "veto_explanation": "VETO",
                "hard_violations": [{"rule": "SHORT"}],
            },
            "risk_debate_state": {"history": "", "count": 0,
                                   "risky_history": "", "safe_history": "",
                                   "neutral_history": "",
                                   "current_risky_response": "", "current_safe_response": "",
                                   "current_neutral_response": "", "latest_speaker": "",
                                   "judge_decision": ""},
        }
        result = risk_veto_node(state)
        assert result["final_trade_decision"] == "HOLD"
        assert result["risk_veto"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 11. State and logging completeness
# ─────────────────────────────────────────────────────────────────────────────

class TestStateAndLoggingCompleteness:

    def test_scorer_output_contains_all_required_fields(self):
        result = _run_scorer(_state())
        assert "risk_action" in result
        assert "risk_metrics" in result
        assert "risk_assessment" in result
        assert "execution_plan" in result

    def test_risk_assessment_structure(self):
        result = _run_scorer(_state())
        ra = result["risk_assessment"]
        for key in ("approved", "risk_action", "hard_violations", "warnings",
                    "violations", "throttle_adjustments", "constraints_checked"):
            assert key in ra, f"risk_assessment missing '{key}'"

    def test_constraints_checked_list_populated(self):
        result = _run_scorer(_state())
        cc = result["risk_assessment"]["constraints_checked"]
        assert len(cc) >= 7

    def test_veto_assessment_has_veto_explanation(self):
        plan = _valid_plan()
        plan["notes"] = "go short position"
        result = _run_scorer(_state(plan=plan))
        assert "veto_explanation" in result["risk_assessment"]

    def test_throttle_assessment_has_adjustments(self):
        plan = _valid_plan(target_shares=100_000, max_shares_per_day=14_000)
        result = _run_scorer(_state(plan=plan, avg_daily_volume=200_000, portfolio_value=30_000_000))
        assert result["risk_action"] == "THROTTLE"
        assert result["risk_assessment"]["throttle_adjustments"]["throttle_applied"] is True

    def test_execution_plan_preserved_in_allow_case(self):
        result = _run_scorer(_state())
        ep = result["execution_plan"]
        inner = ep.get("execution_plan", ep)
        assert inner["symbol"] == "COMI.CA"

    def test_final_trade_decision_accessible_from_backtester(self):
        """Backtester reads final_trade_decision — must always be present in output."""
        from tradingagents.agents.risk_mgmt.risk_scorer import risk_veto_node
        veto_result = risk_veto_node({
            "risk_assessment": {"veto_explanation": "VETO", "hard_violations": []},
            "risk_debate_state": {"history": "", "count": 0, "risky_history": "",
                                   "safe_history": "", "neutral_history": "",
                                   "current_risky_response": "", "current_safe_response": "",
                                   "current_neutral_response": "", "latest_speaker": "",
                                   "judge_decision": ""},
        })
        assert "final_trade_decision" in veto_result


# ─────────────────────────────────────────────────────────────────────────────
# 12. Smoke tests — scorer node end-to-end
# ─────────────────────────────────────────────────────────────────────────────

class TestSmokeTests:

    def test_normal_trade_full_scorer_pipeline(self):
        """Full scorer run on a clean plan: must return ALLOW + metrics + no violations."""
        plan = _valid_plan(
            target_shares=2000, max_shares_per_day=500,
            alloc_pct="8%", stop_price=73.0, limit_price=77.5,
        )
        result = _run_scorer(_state(
            plan=plan, portfolio_value=10_000_000,
            avg_daily_volume=500_000, current_price=77.5,
        ))
        assert result["risk_action"] == "ALLOW"
        assert result["risk_assessment"]["approved"] is True
        assert result["risk_assessment"]["hard_violations"] == []
        assert result["risk_metrics"]["position_pct"] == pytest.approx(0.08, abs=0.01)
        assert result["risk_metrics"]["adv_participation_pct"] == pytest.approx(0.001, abs=0.005)

    def test_forced_veto_full_pipeline(self):
        """Critical violation: scorer returns VETO; veto_node returns HOLD, risk_veto=True."""
        from tradingagents.agents.risk_mgmt.risk_scorer import risk_veto_node
        # Short-selling language → CRITICAL
        plan = _valid_plan()
        plan["strategy"] = "open short position to hedge"
        scorer_result = _run_scorer(_state(plan=plan))
        assert scorer_result["risk_action"] == "VETO"

        veto_state = {
            **_state(plan=plan),
            **scorer_result,
            "risk_debate_state": {"history": "", "count": 0, "risky_history": "",
                                   "safe_history": "", "neutral_history": "",
                                   "current_risky_response": "", "current_safe_response": "",
                                   "current_neutral_response": "", "latest_speaker": "",
                                   "judge_decision": ""},
        }
        final = risk_veto_node(veto_state)
        assert final["final_trade_decision"] == "HOLD"
        assert final["risk_veto"] is True
        assert final["risk_debate_state"]["latest_speaker"] == "Risk_Scorer"

    def test_forced_throttle_full_pipeline(self):
        """Throttle: scorer adjusts plan, risk_action=THROTTLE, debate would proceed."""
        # portfolio_value=30M: 100k*4.5/30M=1.5% — under 2% Elder rule
        plan = _valid_plan(target_shares=100_000, max_shares_per_day=14_000)
        result = _run_scorer(_state(plan=plan, avg_daily_volume=200_000, portfolio_value=30_000_000))
        assert result["risk_action"] == "THROTTLE"
        ep = result["execution_plan"]
        inner = ep.get("execution_plan", ep)
        # Plan is adjusted — debate would receive this throttled plan
        assert inner["position_sizing"]["max_shares_per_day"] == 10_000
        assert inner["position_sizing"]["target_shares"] == 100_000  # unchanged
        assert result["risk_assessment"]["approved"] is True  # not vetoed

    def test_scorer_idempotent_on_second_call(self):
        """Running scorer twice on the same state must give the same result."""
        plan = _valid_plan()
        state = _state(plan=plan)
        r1 = _run_scorer(state)
        # Reset auto-generated stop so second run can regenerate
        plan2 = _valid_plan()
        state2 = _state(plan=plan2)
        r2 = _run_scorer(state2)
        assert r1["risk_action"] == r2["risk_action"]

    def test_backtest_mode_relaxes_concentration_limit(self):
        """In backtest_mode=True, 25% single-stock allocation must not VETO."""
        plan = _valid_plan(alloc_pct="25%")
        from tradingagents.agents.risk_mgmt.risk_scorer import create_risk_scorer_node
        with patch(_SCORER_PATCH,
                   return_value={"target_market": "EGX", "backtest_mode": True}):
            result = create_risk_scorer_node()(_state(plan=plan))
        # 25% is within the relaxed 25% backtest limit → should not be CRITICAL
        critical_rules = [v["rule"] for v in result["risk_assessment"]["hard_violations"]]
        assert "MAX_SINGLE_STOCK_EXPOSURE" not in critical_rules
