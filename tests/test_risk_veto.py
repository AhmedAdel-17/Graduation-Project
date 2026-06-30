"""Regression tests for deterministic risk rules in risk_scorer.py.

Locks:
  - Short selling detection → CRITICAL veto
  - Leverage detection → CRITICAL veto
  - ADV participation > 10% → CRITICAL veto
  - ADV participation 5-10% → THROTTLE (auto-adjust)
  - Circuit breaker / price band violation → CRITICAL veto
  - Max trade loss > 2% → CRITICAL veto
  - Missing liquidity data → CRITICAL veto
  - VETO action classification (any critical = VETO)
  - ALLOW when no violations
  - risk_veto_node produces HOLD final decision

Pure-unit: no LLM, no network, no graph.
"""

from __future__ import annotations

import os
import sys

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tradingagents.agents.risk_mgmt.risk_scorer import (
    EGX_RISK_LIMITS,
    check_short_selling_violation,
    check_leverage_violation,
    check_liquidity_participation,
    check_egx_price_band,
    check_max_trade_loss,
    check_position_size_limit,
    determine_risk_action,
    apply_throttle_adjustments,
    run_all_risk_checks,
    risk_veto_node,
)


# ─────────────────────────────────────────────────────────────────────────────
# Short Selling Detection
# ─────────────────────────────────────────────────────────────────────────────


class TestShortSelling:
    def test_short_sell_detected(self):
        """Explicit 'short selling' language triggers CRITICAL."""
        plan = {"decision": "SELL", "notes": "short selling COMI.CA"}
        v = check_short_selling_violation(plan)
        assert v is not None
        assert v.severity == "critical"
        assert v.rule_name == "SHORT_SELLING_FORBIDDEN"

    def test_sell_short_detected(self):
        plan = {"decision": "SELL", "notes": "sell short 1000 shares"}
        v = check_short_selling_violation(plan)
        assert v is not None
        assert v.severity == "critical"

    def test_go_short_detected(self):
        plan = {"notes": "go short on this ticker"}
        v = check_short_selling_violation(plan)
        assert v is not None

    def test_short_term_not_false_positive(self):
        """'short-term' and 'short_term' must NOT trigger the short-sell detector."""
        plan = {"notes": "short-term investment horizon, short_term gains expected"}
        v = check_short_selling_violation(plan)
        assert v is None

    def test_normal_sell_not_flagged(self):
        """A regular SELL (long-only unwind) is not short selling."""
        plan = {"decision": "SELL", "notes": "reduce position by 500 shares"}
        v = check_short_selling_violation(plan)
        assert v is None


# ─────────────────────────────────────────────────────────────────────────────
# Leverage Detection
# ─────────────────────────────────────────────────────────────────────────────


class TestLeverage:
    def test_margin_detected(self):
        plan = {"notes": "use margin to increase position"}
        v = check_leverage_violation(plan)
        assert v is not None
        assert v.severity == "critical"
        assert v.rule_name == "LEVERAGE_FORBIDDEN"

    def test_leverage_keyword(self):
        plan = {"notes": "apply 2x leverage"}
        v = check_leverage_violation(plan)
        assert v is not None

    def test_borrowed_funds(self):
        plan = {"notes": "use borrowed funds"}
        v = check_leverage_violation(plan)
        assert v is not None

    def test_clean_plan_no_leverage(self):
        plan = {"notes": "buy 500 shares with available cash"}
        v = check_leverage_violation(plan)
        assert v is None


# ─────────────────────────────────────────────────────────────────────────────
# Liquidity Participation
# ─────────────────────────────────────────────────────────────────────────────


class TestLiquidityParticipation:
    def test_veto_above_10pct_adv(self):
        """Daily participation > 10% ADV → CRITICAL veto."""
        plan = {
            "position_sizing": {
                "target_shares": 50000,
                "max_shares_per_day": 15000,
            }
        }
        v = check_liquidity_participation(plan, avg_daily_volume=100_000, low_liquidity=False)
        assert v is not None
        assert v.severity == "critical"
        assert "PARTICIPATION" in v.rule_name

    def test_throttle_zone_5_to_10pct(self):
        """Daily participation 5-10% ADV → HIGH (throttle zone)."""
        plan = {
            "position_sizing": {
                "target_shares": 30000,
                "max_shares_per_day": 7000,
            }
        }
        v = check_liquidity_participation(plan, avg_daily_volume=100_000, low_liquidity=False)
        assert v is not None
        assert v.severity == "high"
        assert "THROTTLE" in v.rule_name

    def test_within_limit_no_violation(self):
        """Daily participation < 5% ADV → no violation."""
        plan = {
            "position_sizing": {
                "target_shares": 10000,
                "max_shares_per_day": 4000,
            }
        }
        v = check_liquidity_participation(plan, avg_daily_volume=100_000, low_liquidity=False)
        assert v is None

    def test_zero_adv_critical(self):
        """Missing ADV data → CRITICAL."""
        plan = {"position_sizing": {"target_shares": 100}}
        v = check_liquidity_participation(plan, avg_daily_volume=0, low_liquidity=False)
        assert v is not None
        assert v.severity == "critical"

    def test_low_liquidity_halves_effective_adv(self):
        """low_liquidity flag reduces effective ADV by 50%."""
        plan = {
            "position_sizing": {
                "target_shares": 10000,
                "max_shares_per_day": 6000,
            }
        }
        # At 100k ADV, 6% is in throttle zone. With low_liq, effective=50k, 12% → VETO.
        v = check_liquidity_participation(plan, avg_daily_volume=100_000, low_liquidity=True)
        assert v is not None
        assert v.severity == "critical"


# ─────────────────────────────────────────────────────────────────────────────
# Price Band
# ─────────────────────────────────────────────────────────────────────────────


class TestPriceBand:
    def test_price_above_upper_band_veto(self):
        """Limit price far above +10% band (>5% overshoot) → CRITICAL veto."""
        plan = {
            "entry_logic": {"entry_zone": {"limit_price": 120.0}},
            "decision": "BUY",
        }
        v = check_egx_price_band(plan, current_price=100.0)
        assert v is not None
        assert v.severity == "critical"
        assert "BAND" in v.rule_name

    def test_price_slightly_above_band_auto_corrects(self):
        """Limit price slightly above band (≤5% overshoot) → auto-corrected, no veto."""
        plan = {
            "entry_logic": {"entry_zone": {"limit_price": 112.0}},
            "decision": "BUY",
        }
        v = check_egx_price_band(plan, current_price=100.0)
        assert v is None  # No violation — auto-corrected
        assert plan["entry_logic"]["entry_zone"]["limit_price"] == 110.0  # Clamped to upper band

    def test_price_below_lower_band_veto(self):
        """Limit price far below -10% band (>5% undershoot) → CRITICAL veto."""
        plan = {
            "entry_logic": {"entry_zone": {"limit_price": 80.0}},
            "decision": "SELL",
        }
        v = check_egx_price_band(plan, current_price=100.0)
        assert v is not None
        assert v.severity == "critical"

    def test_price_within_band_ok(self):
        """Price inside the band → no violation."""
        plan = {
            "entry_logic": {"entry_zone": {"limit_price": 105.0}},
            "decision": "BUY",
        }
        v = check_egx_price_band(plan, current_price=100.0)
        # 105 < 110 (upper band) and not in magnet zone (threshold at 108.5)
        assert v is None


# ─────────────────────────────────────────────────────────────────────────────
# Max Trade Loss
# ─────────────────────────────────────────────────────────────────────────────


class TestMaxTradeLoss:
    def test_loss_exceeds_2pct_cap(self):
        """Potential loss > 2% of portfolio → CRITICAL."""
        plan = {
            "position_sizing": {"target_shares": 1000},
            "exit_logic": {"stop_loss": {"price": 90.0}},
        }
        # Loss = (100 - 90) × 1000 = 10,000 = 1% of 1M → OK
        # But with smaller portfolio:
        v = check_max_trade_loss(plan, portfolio_value=100_000, current_price=100.0)
        # Loss = 10,000 / 100,000 = 10% → CRITICAL
        assert v is not None
        assert v.severity == "critical"
        assert v.rule_name == "MAX_TRADE_LOSS_EXCEEDED"

    def test_loss_within_2pct_ok(self):
        """Potential loss ≤ 2% → no violation."""
        plan = {
            "position_sizing": {"target_shares": 100},
            "exit_logic": {"stop_loss": {"price": 98.0}},
        }
        # Loss = (100 - 98) × 100 = 200 = 0.02% of 1M → OK
        v = check_max_trade_loss(plan, portfolio_value=1_000_000, current_price=100.0)
        assert v is None


# ─────────────────────────────────────────────────────────────────────────────
# Action Classification
# ─────────────────────────────────────────────────────────────────────────────


class TestActionClassification:
    def test_no_violations_allow(self):
        assert determine_risk_action([]) == "ALLOW"

    def test_critical_violation_veto(self):
        from tradingagents.agents.risk_mgmt.risk_scorer import RiskViolation
        v = RiskViolation("TEST", "critical", 0.10, 0.15, "test", "fix")
        assert determine_risk_action([v]) == "VETO"

    def test_single_throttle_zone_throttle(self):
        from tradingagents.agents.risk_mgmt.risk_scorer import RiskViolation
        v = RiskViolation("LIQUIDITY_THROTTLE_ZONE", "high", 0.05, 0.07, "test", "fix")
        assert determine_risk_action([v]) == "THROTTLE"

    def test_mixed_critical_and_high_veto(self):
        """Critical + high → VETO (critical wins)."""
        from tradingagents.agents.risk_mgmt.risk_scorer import RiskViolation
        v1 = RiskViolation("SHORT_SELLING_FORBIDDEN", "critical", 0, 1, "test", "fix")
        v2 = RiskViolation("LIQUIDITY_THROTTLE_ZONE", "high", 0.05, 0.07, "test", "fix")
        assert determine_risk_action([v1, v2]) == "VETO"


# ─────────────────────────────────────────────────────────────────────────────
# run_all_risk_checks convenience function
# ─────────────────────────────────────────────────────────────────────────────


class TestRunAllChecks:
    def test_clean_plan_approved(self):
        """A well-formed plan with no violations is approved."""
        plan = {
            "decision": "BUY",
            "position_sizing": {
                "target_shares": 100,
                "max_shares_per_day": 100,
                "portfolio_allocation": "1%",
            },
            "exit_logic": {
                "stop_loss": {"price": 48.0},
            },
            "entry_logic": {
                "entry_zone": {"limit_price": 50.5},
            },
        }
        approved, violations = run_all_risk_checks(
            plan,
            portfolio_value=1_000_000,
            avg_daily_volume=500_000,
            current_price=50.0,
        )
        critical = [v for v in violations if v.severity == "critical"]
        assert approved is True
        assert len(critical) == 0

    def test_short_selling_plan_vetoed(self):
        """A plan with short selling is rejected."""
        plan = {
            "decision": "SELL",
            "notes": "short selling COMI.CA 1000 shares",
            "position_sizing": {"target_shares": 100, "max_shares_per_day": 100},
        }
        approved, violations = run_all_risk_checks(
            plan,
            portfolio_value=1_000_000,
            avg_daily_volume=500_000,
            current_price=50.0,
        )
        assert approved is False
        rule_names = {v.rule_name for v in violations}
        assert "SHORT_SELLING_FORBIDDEN" in rule_names


# ─────────────────────────────────────────────────────────────────────────────
# Risk Veto Node
# ─────────────────────────────────────────────────────────────────────────────


class TestRiskVetoNode:
    def test_veto_node_returns_hold(self):
        """risk_veto_node always outputs HOLD as final_trade_decision."""
        state = {
            "risk_assessment": {"veto_explanation": "Short selling forbidden"},
            "risk_debate_state": {},
        }
        result = risk_veto_node(state)
        assert result["final_trade_decision"] == "HOLD"
        assert result["risk_veto"] is True
