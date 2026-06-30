"""
Test that macro_provider.get_egx_macro_context uses the date-aware CBE rate
lookup, not the static config value.

This is the regression test for the #1 underperformance root cause: all
downstream agents (research_manager, risk_manager, trader) received a static
27.5% CBE rate via macro_context even for backtest dates where the actual
CBE rate was 19.25%.  The fix makes macro_provider delegate to
rate_lookup.get_egx_risk_free_rate_as_of().
"""

import pytest
from unittest.mock import patch
from tradingagents.dataflows.macro_provider import get_egx_macro_context


# The CSV has:
#   2023-01-05  0.1925  (19.25%)
#   2024-03-06  0.2725  (27.25%)
# So for Jan 2024, the correct rate is 19.25%; for Apr 2024, it's 27.25%.


@pytest.fixture
def config_with_static_rate():
    """Config dict with the old static rate that should NOT be used when CSV exists."""
    return {"egx_risk_free_rate": 0.275}


class TestMacroProviderDateAwareRate:
    """Ensure macro_context.cbe_policy_rate reflects the date-aware CBE lookup."""

    def test_jan2024_uses_1925_not_2750(self, config_with_static_rate):
        """Jan 2024 is before the Mar 2024 hike -> rate should be 19.25%."""
        ctx = get_egx_macro_context(as_of_date="2024-01-15", config=config_with_static_rate)
        rate = ctx["cbe_policy_rate"]
        # Should be ~0.1925, NOT 0.275
        assert abs(rate - 0.1925) < 0.001, (
            f"Expected CBE rate ~0.1925 for Jan 2024, got {rate}. "
            "macro_provider may still be using the static config rate."
        )

    def test_apr2024_uses_2725(self, config_with_static_rate):
        """Apr 2024 is after the Mar 2024 hike -> rate should be 27.25%."""
        ctx = get_egx_macro_context(as_of_date="2024-04-15", config=config_with_static_rate)
        rate = ctx["cbe_policy_rate"]
        assert abs(rate - 0.2725) < 0.001, (
            f"Expected CBE rate ~0.2725 for Apr 2024, got {rate}"
        )

    def test_covid_era_uses_925(self, config_with_static_rate):
        """Mid-2021 is during COVID hold -> rate should be 9.25%."""
        ctx = get_egx_macro_context(as_of_date="2021-06-15", config=config_with_static_rate)
        rate = ctx["cbe_policy_rate"]
        assert abs(rate - 0.0925) < 0.001, (
            f"Expected CBE rate ~0.0925 for mid-2021, got {rate}"
        )

    def test_data_sources_reports_date_aware(self, config_with_static_rate):
        """data_sources dict should say 'date_aware_cbe_policy_rate', not 'config'."""
        ctx = get_egx_macro_context(as_of_date="2024-01-15", config=config_with_static_rate)
        src = ctx.get("data_sources", {}).get("cbe_rate", "")
        assert src == "date_aware_cbe_policy_rate", (
            f"Expected source 'date_aware_cbe_policy_rate', got '{src}'"
        )

    def test_fallback_to_static_when_csv_missing(self):
        """If rate_lookup returns None (e.g. CSV missing), fall back to static config."""
        with patch(
            "tradingagents.agents.analysts.fundamentals.rate_lookup.get_egx_risk_free_rate_as_of",
            return_value=(None, "not_configured", None),
        ):
            ctx = get_egx_macro_context(
                as_of_date="2024-01-15",
                config={"egx_risk_free_rate": 0.275},
            )
            rate = ctx["cbe_policy_rate"]
            assert abs(rate - 0.275) < 0.001, (
                f"Expected static fallback rate 0.275, got {rate}"
            )

    def test_real_rate_uses_date_aware_cbe(self, config_with_static_rate):
        """real_rate = cbe - cpi. If cbe is date-aware, real_rate should shift."""
        ctx_jan = get_egx_macro_context(as_of_date="2024-01-15", config=config_with_static_rate)
        ctx_apr = get_egx_macro_context(as_of_date="2024-04-15", config=config_with_static_rate)
        # real_rate should be lower in Jan (19.25% - CPI) than Apr (27.25% - CPI)
        assert ctx_jan["real_rate"] < ctx_apr["real_rate"], (
            f"Jan real_rate ({ctx_jan['real_rate']}) should be < Apr ({ctx_apr['real_rate']})"
        )
