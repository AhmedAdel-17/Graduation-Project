"""Tests for calculate_position_limits() and EGX trader constraints (Phase 2f).

Covers:
  - Normal liquidity: 10% ADV cap, 10% portfolio cap
  - Low liquidity: 5% ADV cap (halved), low_liquidity_adjustment=True
  - Backtest mode: portfolio cap relaxed to 100%
  - Zero price: max_shares_total must be 0 (no division by zero)
  - Zero volume: max_shares_per_day must be 0
"""
import pytest
from unittest.mock import patch


class TestCalculatePositionLimitsNormalLiquidity:
    def test_normal_adv_constraint_pct(self):
        from tradingagents.agents.trader.trader import calculate_position_limits

        with patch("tradingagents.agents.trader.trader.get_config",
                   return_value={"backtest_mode": False}):
            limits = calculate_position_limits(
                avg_daily_volume=500_000,
                current_price=77.5,
                portfolio_value=10_000_000,
                low_liquidity=False,
            )

        assert limits["adv_constraint_pct"] == 0.10
        assert limits["low_liquidity_adjustment"] is False

    def test_normal_max_shares_per_day(self):
        from tradingagents.agents.trader.trader import calculate_position_limits

        with patch("tradingagents.agents.trader.trader.get_config",
                   return_value={"backtest_mode": False}):
            limits = calculate_position_limits(
                avg_daily_volume=500_000,
                current_price=77.5,
                portfolio_value=10_000_000,
                low_liquidity=False,
            )

        # 10% of 500K = 50,000 shares/day
        assert limits["max_shares_per_day"] == 50_000

    def test_portfolio_cap_10pct(self):
        from tradingagents.agents.trader.trader import calculate_position_limits

        with patch("tradingagents.agents.trader.trader.get_config",
                   return_value={"backtest_mode": False}):
            limits = calculate_position_limits(
                avg_daily_volume=500_000,
                current_price=77.5,
                portfolio_value=10_000_000,
                low_liquidity=False,
            )

        assert limits["portfolio_constraint_pct"] == 0.10


class TestCalculatePositionLimitsLowLiquidity:
    def test_low_liquidity_halves_adv_pct(self):
        from tradingagents.agents.trader.trader import calculate_position_limits

        with patch("tradingagents.agents.trader.trader.get_config",
                   return_value={"backtest_mode": False}):
            limits = calculate_position_limits(
                avg_daily_volume=500_000,
                current_price=77.5,
                portfolio_value=10_000_000,
                low_liquidity=True,
            )

        assert limits["adv_constraint_pct"] == 0.05
        assert limits["low_liquidity_adjustment"] is True

    def test_low_liquidity_max_shares_halved(self):
        from tradingagents.agents.trader.trader import calculate_position_limits

        with patch("tradingagents.agents.trader.trader.get_config",
                   return_value={"backtest_mode": False}):
            limits = calculate_position_limits(
                avg_daily_volume=500_000,
                current_price=77.5,
                portfolio_value=10_000_000,
                low_liquidity=True,
            )

        # 5% of 500K = 25,000 shares/day
        assert limits["max_shares_per_day"] == 25_000

    def test_very_thin_stock_position_small(self):
        from tradingagents.agents.trader.trader import calculate_position_limits

        with patch("tradingagents.agents.trader.trader.get_config",
                   return_value={"backtest_mode": False}):
            limits = calculate_position_limits(
                avg_daily_volume=1_000,
                current_price=100.0,
                portfolio_value=10_000_000,
                low_liquidity=True,
            )

        # 5% of 1,000 = 50 shares/day
        assert limits["max_shares_per_day"] == 50
        assert limits["days_to_full_position"] > 1


class TestCalculatePositionLimitsBacktestMode:
    def test_backtest_relaxes_portfolio_cap(self):
        from tradingagents.agents.trader.trader import calculate_position_limits

        with patch("tradingagents.agents.trader.trader.get_config",
                   return_value={"backtest_mode": True}):
            limits = calculate_position_limits(
                avg_daily_volume=100_000,
                current_price=50.0,
                portfolio_value=500_000,
            )

        # Backtest mode: single-stock cap should be 100%, not 10%
        assert limits["portfolio_constraint_pct"] == 1.0


class TestCalculatePositionLimitsEdgeCases:
    def test_zero_price_returns_zero_shares(self):
        from tradingagents.agents.trader.trader import calculate_position_limits

        with patch("tradingagents.agents.trader.trader.get_config",
                   return_value={"backtest_mode": False}):
            limits = calculate_position_limits(
                avg_daily_volume=100_000,
                current_price=0.0,
                portfolio_value=10_000_000,
            )

        # Zero price must not cause division error; shares must be 0
        assert limits["max_shares_total"] == 0

    def test_zero_volume_returns_zero_per_day(self):
        from tradingagents.agents.trader.trader import calculate_position_limits

        with patch("tradingagents.agents.trader.trader.get_config",
                   return_value={"backtest_mode": False}):
            limits = calculate_position_limits(
                avg_daily_volume=0,
                current_price=50.0,
                portfolio_value=10_000_000,
            )

        assert limits["max_shares_per_day"] == 0

    def test_returns_all_required_keys(self):
        from tradingagents.agents.trader.trader import calculate_position_limits

        with patch("tradingagents.agents.trader.trader.get_config",
                   return_value={"backtest_mode": False}):
            limits = calculate_position_limits(
                avg_daily_volume=200_000,
                current_price=45.0,
                portfolio_value=5_000_000,
            )

        required_keys = {
            "max_shares_per_day",
            "max_shares_total",
            "days_to_full_position",
            "adv_constraint_pct",
            "portfolio_constraint_pct",
            "low_liquidity_adjustment",
        }
        assert required_keys.issubset(set(limits.keys()))


class TestDailyPriceLimitConstant:
    def test_egx_daily_price_limit_is_10pct(self):
        from tradingagents.agents.trader.trader import DAILY_PRICE_LIMIT
        assert DAILY_PRICE_LIMIT == 0.10
