"""EGX-specific constraint edge-case tests.

Covers:
  - No short selling: SELL with no open position triggers a critical violation
  - Short-selling language in exec plan triggers violation
  - Low liquidity: very thin stock gets small position limits
  - Zero volume produces sane (not crashing) output
  - DAILY_PRICE_LIMIT constant is ±10% as required by EGX rules
"""
import pytest
from unittest.mock import patch


class TestNoShortSelling:
    def test_short_selling_language_triggers_violation(self):
        """Any short-selling language in exec plan must trigger a critical violation."""
        from tradingagents.agents.managers.risk_manager import check_short_selling_violation

        exec_plan = {"decision": "SELL", "strategy": "short sell COMI.CA"}
        violation = check_short_selling_violation(exec_plan)

        assert violation is not None, \
            "Short-selling language must trigger a RiskViolation"
        assert violation.severity == "critical"

    def test_clean_buy_plan_no_violation(self):
        """A normal BUY plan must not trigger a short-selling violation."""
        from tradingagents.agents.managers.risk_manager import check_short_selling_violation

        exec_plan = {
            "decision": "BUY",
            "symbol": "COMI.CA",
            "position_sizing": {"target_shares": 500},
            "exit_logic": {"stop_loss": {"price": 70.0}},
        }
        violation = check_short_selling_violation(exec_plan)
        assert violation is None, \
            "Clean BUY plan must not trigger a short-selling violation"

    def test_run_all_checks_blocks_short_selling(self):
        """run_all_risk_checks must return approved=False for short-selling plan."""
        from tradingagents.agents.managers.risk_manager import run_all_risk_checks

        exec_plan = {
            "decision": "SELL",
            "symbol": "COMI.CA",
            "strategy": "short COMI.CA 1000 shares",
            "position_sizing": {"target_shares": 1000},
            "exit_logic": {"stop_loss": {"price": 80.0}},
        }
        approved, violations = run_all_risk_checks(
            exec_plan,
            portfolio_value=10_000_000,
            avg_daily_volume=500_000,
            current_price=77.5,
            low_liquidity=False,
        )

        assert approved is False, \
            "Short-selling plan must be rejected by run_all_risk_checks"
        critical = [v for v in violations if v.severity == "critical"]
        assert len(critical) >= 1


class TestLiquidityConstraints:
    def test_very_low_volume_limits_position_size(self):
        from tradingagents.agents.trader.trader import calculate_position_limits

        with patch("tradingagents.agents.trader.trader.get_config",
                   return_value={"backtest_mode": False}):
            limits = calculate_position_limits(
                avg_daily_volume=1_000,
                current_price=100.0,
                portfolio_value=10_000_000,
                low_liquidity=True,
            )

        # 5% of 1,000 = 50 shares/day max
        assert limits["max_shares_per_day"] <= 100, \
            "Very thin stock must produce tiny daily share limit"
        assert limits["days_to_full_position"] > 1, \
            "Low-liquidity thin stock must require multiple days to accumulate"

    def test_zero_volume_produces_sane_output(self):
        """Edge case: stock with no volume data must not crash."""
        from tradingagents.agents.trader.trader import calculate_position_limits

        with patch("tradingagents.agents.trader.trader.get_config",
                   return_value={"backtest_mode": False}):
            limits = calculate_position_limits(
                avg_daily_volume=0,
                current_price=50.0,
                portfolio_value=10_000_000,
            )

        assert limits["max_shares_per_day"] == 0

    def test_high_liquidity_allows_larger_position(self):
        """High-ADV stock should produce larger limits than thin stock."""
        from tradingagents.agents.trader.trader import calculate_position_limits

        with patch("tradingagents.agents.trader.trader.get_config",
                   return_value={"backtest_mode": False}):
            high_liq = calculate_position_limits(
                avg_daily_volume=1_000_000,
                current_price=77.5,
                portfolio_value=10_000_000,
                low_liquidity=False,
            )
            low_liq = calculate_position_limits(
                avg_daily_volume=30_000,
                current_price=77.5,
                portfolio_value=10_000_000,
                low_liquidity=True,
            )

        assert high_liq["max_shares_per_day"] > low_liq["max_shares_per_day"]


class TestCircuitBreaker:
    def test_daily_price_limit_is_10pct(self):
        from tradingagents.agents.trader.trader import DAILY_PRICE_LIMIT

        assert DAILY_PRICE_LIMIT == 0.10, \
            "EGX daily price limit must be ±10%"


class TestLeverageConstraint:
    def test_leverage_language_triggers_violation(self):
        """Any leverage/margin language in exec plan must trigger a critical violation."""
        from tradingagents.agents.managers.risk_manager import check_leverage_violation

        exec_plan = {"decision": "BUY", "note": "Use 2x margin to amplify gains"}
        violation = check_leverage_violation(exec_plan)

        assert violation is not None, \
            "Leverage language must trigger a RiskViolation"
        assert violation.severity == "critical"

    def test_clean_plan_no_leverage_violation(self):
        from tradingagents.agents.managers.risk_manager import check_leverage_violation

        exec_plan = {
            "decision": "BUY",
            "symbol": "COMI.CA",
            "position_sizing": {"target_shares": 500},
        }
        violation = check_leverage_violation(exec_plan)
        assert violation is None
