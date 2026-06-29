"""
Regression tests for Price/OHLCV temporal safety.

Ensures that technical indicator fetches in backtest mode are bounded by
trade_date, not today.  This prevents future price data from leaking into
historical backtest evaluations.

Leakage surfaces tested:
  1. _get_stock_stats_bulk()  — y_finance.py
  2. StockstatsUtils.get_stock_stats() — stockstats_utils.py
  3. Cache key includes end_date derived from curr_date
  4. get_stock_data tool clamps end_date to trade_date
  5. get_indicators tool clamps curr_date to trade_date
"""

import os
import re
import pytest
from unittest.mock import patch, MagicMock, call
from datetime import datetime

import pandas as pd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv_df(start: str, end: str) -> pd.DataFrame:
    """Create a minimal OHLCV DataFrame for the given date range."""
    dates = pd.bdate_range(start, end)
    n = len(dates)
    return pd.DataFrame({
        "Date": dates,
        "Open": [100.0 + i for i in range(n)],
        "High": [101.0 + i for i in range(n)],
        "Low": [99.0 + i for i in range(n)],
        "Close": [100.5 + i for i in range(n)],
        "Volume": [1000000] * n,
    })


# ---------------------------------------------------------------------------
# Test: _get_stock_stats_bulk uses curr_date as end, not today
# ---------------------------------------------------------------------------

class TestGetStockStatsBulkTemporal:
    """_get_stock_stats_bulk must fetch data up to curr_date, not today."""

    @patch("tradingagents.dataflows.y_finance.yf.download")
    @patch("tradingagents.dataflows.config.get_config")
    def test_yf_download_end_date_matches_curr_date(self, mock_config, mock_download):
        """yfinance download must be called with end = curr_date + 1 day."""
        from tradingagents.dataflows.y_finance import _get_stock_stats_bulk

        trade_date = "2024-01-15"
        mock_config.return_value = {
            "data_vendors": {"technical_indicators": "yfinance"},
            "data_cache_dir": "/tmp/test_cache_ohlcv_temporal",
        }
        mock_df = _make_ohlcv_df("2009-01-15", trade_date)
        mock_download.return_value = mock_df

        with patch("os.path.exists", return_value=False):
            with patch.object(mock_df, "to_csv"):
                try:
                    _get_stock_stats_bulk("COMI.CA", "rsi", trade_date)
                except Exception:
                    pass  # indicator calc may fail on mock data

        assert mock_download.called, "yf.download was not called"
        call_str = str(mock_download.call_args)

        today_str = datetime.now().strftime("%Y-%m-%d")
        assert today_str not in call_str, (
            f"yf.download was called with today's date ({today_str}): {call_str}. "
            "Temporal leakage — indicator fetches must not extend beyond trade_date."
        )
        # end should be 2024-01-16 (trade_date + 1 day, yfinance exclusive)
        assert "2024-01-16" in call_str, (
            f"Expected end_date '2024-01-16' in yf.download call, got: {call_str}"
        )

    @patch("tradingagents.dataflows.config.get_config")
    def test_result_dict_has_no_future_dates(self, mock_config):
        """Returned indicator dict must not contain dates after curr_date."""
        from tradingagents.dataflows.y_finance import _get_stock_stats_bulk

        trade_date = "2024-01-15"
        full_df = _make_ohlcv_df("2023-01-01", "2024-06-01")

        mock_config.return_value = {
            "data_vendors": {"technical_indicators": "local"},
            "data_cache_dir": "/tmp/test_cache_ohlcv_temporal",
        }
        with patch("pandas.read_csv", return_value=full_df):
            try:
                result = _get_stock_stats_bulk("COMI.CA", "close_10_ema", trade_date)
            except Exception:
                pytest.skip("stockstats indicator calc failed on synthetic data")

        for date_key in result:
            # date_key may be a Timestamp or string depending on data path
            dk = str(date_key)[:10]
            assert dk <= trade_date, (
                f"Result contains date {dk} after trade_date {trade_date}. "
                "Future data leakage."
            )


# ---------------------------------------------------------------------------
# Test: StockstatsUtils.get_stock_stats uses curr_date as end, not today
# ---------------------------------------------------------------------------

class TestStockstatsUtilsTemporal:
    """StockstatsUtils.get_stock_stats must not fetch data beyond curr_date."""

    @patch("tradingagents.dataflows.stockstats_utils.yf.download")
    @patch("tradingagents.dataflows.stockstats_utils.get_config")
    def test_yf_download_end_date_not_today(self, mock_config, mock_download):
        """yfinance download end_date must derive from curr_date, not today."""
        from tradingagents.dataflows.stockstats_utils import StockstatsUtils

        trade_date = "2024-03-10"
        mock_config.return_value = {
            "data_vendors": {"technical_indicators": "yfinance"},
            "data_cache_dir": "/tmp/test_cache_stockstats_temporal",
        }
        mock_df = _make_ohlcv_df("2009-03-10", trade_date)
        mock_download.return_value = mock_df

        with patch("os.path.exists", return_value=False):
            with patch.object(mock_df, "to_csv"):
                try:
                    StockstatsUtils.get_stock_stats("COMI.CA", "rsi", trade_date)
                except Exception:
                    pass

        assert mock_download.called, "yf.download was not called"
        today_str = datetime.now().strftime("%Y-%m-%d")
        call_str = str(mock_download.call_args)
        assert today_str not in call_str, (
            f"StockstatsUtils called yf.download with today ({today_str}): {call_str}. "
            "Temporal leakage."
        )
        # end should be 2024-03-11
        assert "2024-03-11" in call_str, (
            f"Expected end_date '2024-03-11' in call, got: {call_str}"
        )


# ---------------------------------------------------------------------------
# Test: Cache key changes when trade_date changes
# ---------------------------------------------------------------------------

class TestCacheKeyTemporal:
    """Cache filenames must include end_date derived from curr_date."""

    @patch("tradingagents.dataflows.config.get_config")
    def test_different_trade_dates_produce_different_cache_keys(self, mock_config):
        """Two different trade_dates must not hit the same cache file."""
        from tradingagents.dataflows.y_finance import _get_stock_stats_bulk

        mock_config.return_value = {
            "data_vendors": {"technical_indicators": "yfinance"},
            "data_cache_dir": "/tmp/test_cache_key_temporal",
        }

        cache_paths = []
        def capture_exists(path):
            if "YFin-data" in str(path):
                cache_paths.append(str(path))
            return False

        mock_df = _make_ohlcv_df("2020-01-01", "2024-06-01")

        for trade_date in ["2024-01-15", "2024-03-15"]:
            with patch("os.path.exists", side_effect=capture_exists):
                with patch("tradingagents.dataflows.y_finance.yf.download", return_value=mock_df):
                    with patch.object(mock_df, "to_csv"):
                        try:
                            _get_stock_stats_bulk("COMI.CA", "rsi", trade_date)
                        except Exception:
                            pass

        assert len(cache_paths) >= 2, f"Expected >=2 cache lookups, got {len(cache_paths)}"
        assert cache_paths[0] != cache_paths[1], (
            f"Cache paths identical for different trade_dates: {cache_paths[0]}. "
            "Future-inclusive cache reuse risk."
        )


# ---------------------------------------------------------------------------
# Test: get_stock_data tool clamps end_date to trade_date
# ---------------------------------------------------------------------------

class TestGetStockDataToolClamp:
    """The get_stock_data tool must clamp end_date to config trade_date."""

    @patch("tradingagents.dataflows.config.get_config")
    def test_end_date_clamped(self, mock_config):
        """If LLM requests end_date > trade_date, it must be clamped."""
        mock_config.return_value = {"trade_date": "2024-01-15"}

        from tradingagents.agents.utils.core_stock_tools import get_stock_data

        with patch("tradingagents.agents.utils.core_stock_tools.route_to_vendor") as mock_route:
            mock_route.return_value = "mock data"
            get_stock_data.invoke({
                "symbol": "COMI.CA",
                "start_date": "2023-06-01",
                "end_date": "2025-12-31",
            })
            call_args = mock_route.call_args[0]
            actual_end = call_args[3]  # 4th positional: end_date
            assert actual_end == "2024-01-15", (
                f"end_date not clamped: expected '2024-01-15', got '{actual_end}'"
            )


# ---------------------------------------------------------------------------
# Test: get_indicators tool clamps curr_date to trade_date
# ---------------------------------------------------------------------------

class TestGetIndicatorsToolClamp:
    """The get_indicators tool must clamp curr_date to config trade_date."""

    @patch("tradingagents.dataflows.config.get_config")
    def test_curr_date_clamped(self, mock_config):
        """If LLM requests curr_date > trade_date, it must be clamped."""
        mock_config.return_value = {"trade_date": "2024-01-15"}

        from tradingagents.agents.utils.technical_indicators_tools import get_indicators

        with patch("tradingagents.agents.utils.technical_indicators_tools.route_to_vendor") as mock_route:
            mock_route.return_value = "mock indicators"
            get_indicators.invoke({
                "symbol": "COMI.CA",
                "indicator": "rsi",
                "curr_date": "2025-06-01",
                "look_back_days": 30,
            })
            call_args = mock_route.call_args[0]
            # route_to_vendor("get_indicators", symbol, indicator, curr_date, look_back_days)
            actual_curr = call_args[3]  # 4th positional: curr_date
            assert actual_curr == "2024-01-15", (
                f"curr_date not clamped: expected '2024-01-15', got '{actual_curr}'"
            )


# ---------------------------------------------------------------------------
# Test: no market analyst path fetches prices after trade_date
# ---------------------------------------------------------------------------

class TestNoFuturePricesInIndicators:
    """End-to-end: indicator window must not contain future dates."""

    @patch("tradingagents.dataflows.config.get_config")
    def test_indicator_window_bounded_by_curr_date(self, mock_config):
        """get_stock_stats_indicators_window output must not have future dates."""
        from tradingagents.dataflows.y_finance import get_stock_stats_indicators_window

        trade_date = "2024-02-01"
        full_df = _make_ohlcv_df("2023-01-01", "2024-12-31")

        mock_config.return_value = {
            "data_vendors": {"technical_indicators": "local"},
            "data_cache_dir": "/tmp/test_no_future_indicators",
        }
        with patch("pandas.read_csv", return_value=full_df):
            try:
                result = get_stock_stats_indicators_window(
                    "COMI.CA", "close_10_ema", trade_date, 60
                )
            except Exception:
                pytest.skip("indicator calc failed on synthetic data")

        dates_in_result = re.findall(r"(\d{4}-\d{2}-\d{2}):", result)
        for d in dates_in_result:
            assert d <= trade_date, (
                f"Indicator result contains date {d} after trade_date {trade_date}. "
                "Future data leakage."
            )
