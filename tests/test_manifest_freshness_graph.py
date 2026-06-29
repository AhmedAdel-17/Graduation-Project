"""
Tests for manifest freshness integration in TradingAgentsGraph.

Tests the _check_manifest_freshness() method and config-driven behavior
(enforce vs warn) without requiring LLM keys or a full graph run.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from tradingagents.dataflows.manifest_scanner import (
    FreshnessResult,
    ANNUAL_FILING_LAG_DAYS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(ticker="COMI", stmt="income", freq="annual",
                period="2023-12-31", publish=None, row_count=5,
                schema_valid=True, runtime=True, **extra):
    return {
        "ticker": ticker,
        "statement_type": stmt,
        "frequency": freq,
        "path": f"/fake/{ticker}_{stmt}_{freq}.csv",
        "row_count": row_count,
        "latest_period_end_date": period,
        "latest_publish_date": publish,
        "schema_valid": schema_valid,
        "missing_required_fields": extra.get("missing_required_fields", []),
        "warnings": extra.get("warnings", []),
        "used_by_runtime": runtime,
    }


def _make_complete_ticker(ticker, period="2023-12-31", **overrides):
    entries = []
    for stmt in ("income", "balance", "ratios"):
        kw = dict(ticker=ticker, stmt=stmt, period=period)
        kw.update(overrides)
        entries.append(_make_entry(**kw))
    return entries


def _build_graph_instance(tmp_path, config_overrides=None):
    """Build a TradingAgentsGraph with __init__ bypassed (no API keys needed)."""
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = DEFAULT_CONFIG.copy()
    config["data_dir"] = str(tmp_path)
    config["target_market"] = "EGX"
    if config_overrides:
        config.update(config_overrides)

    with patch.object(TradingAgentsGraph, "__init__", lambda self, **kw: None):
        graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = config
    return graph


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestManifestFreshnessCheck:
    """Test _check_manifest_freshness() directly."""

    def test_fresh_manifest_returns_true(self, tmp_path):
        """COMI with recent data -> fresh=True, no reasons."""
        entries = _make_complete_ticker("COMI", period="2023-12-31")
        graph = _build_graph_instance(tmp_path)

        with patch(
            "tradingagents.dataflows.manifest_scanner.scan_fundamentals",
            return_value=entries,
        ), patch(
            "tradingagents.dataflows.manifest_scanner.write_manifest",
        ), patch(
            "tradingagents.dataflows.manifest_scanner.check_manifest_freshness",
            return_value=FreshnessResult(fresh=True, reasons=[]),
        ) as mock_check:
            fresh, reasons, tickers = graph._check_manifest_freshness(
                "COMI.CA", "2024-06-01"
            )

        assert fresh is True
        assert reasons == []
        assert tickers == ["COMI"]

    def test_stale_manifest_warn_mode_returns_false_no_raise(self, tmp_path):
        """enforce=False + stale -> returns fresh=False but does NOT raise."""
        entries = _make_complete_ticker("COMI", period="2020-12-31")
        graph = _build_graph_instance(
            tmp_path,
            {"enforce_fundamentals_manifest_freshness": False},
        )

        with patch(
            "tradingagents.dataflows.manifest_scanner.scan_fundamentals",
            return_value=entries,
        ), patch(
            "tradingagents.dataflows.manifest_scanner.write_manifest",
        ), patch(
            "tradingagents.dataflows.manifest_scanner.check_manifest_freshness",
            return_value=FreshnessResult(
                fresh=False,
                reasons=["COMI/income: latest period 2020-12-31 overdue"],
            ),
        ):
            # Should NOT raise
            fresh, reasons, tickers = graph._check_manifest_freshness(
                "COMI.CA", "2024-06-01"
            )

        assert fresh is False
        assert len(reasons) == 1
        assert "overdue" in reasons[0].lower()

    def test_stale_manifest_enforce_mode_raises(self, tmp_path):
        """enforce=True + stale -> raises RuntimeError."""
        graph = _build_graph_instance(
            tmp_path,
            {"enforce_fundamentals_manifest_freshness": True},
        )

        with patch(
            "tradingagents.dataflows.manifest_scanner.scan_fundamentals",
            return_value=[],
        ), patch(
            "tradingagents.dataflows.manifest_scanner.write_manifest",
        ), patch(
            "tradingagents.dataflows.manifest_scanner.check_manifest_freshness",
            return_value=FreshnessResult(
                fresh=False,
                reasons=["COMI/income: stale data"],
            ),
        ):
            with pytest.raises(RuntimeError, match="FAILED"):
                graph._check_manifest_freshness("COMI.CA", "2024-06-01")

    def test_ticker_normalization(self, tmp_path):
        """COMI.CA -> COMI as the checked ticker."""
        graph = _build_graph_instance(tmp_path)

        with patch(
            "tradingagents.dataflows.manifest_scanner.scan_fundamentals",
            return_value=[],
        ), patch(
            "tradingagents.dataflows.manifest_scanner.write_manifest",
        ), patch(
            "tradingagents.dataflows.manifest_scanner.check_manifest_freshness",
            return_value=FreshnessResult(fresh=True, reasons=[]),
        ) as mock_check:
            _, _, tickers = graph._check_manifest_freshness("COMI.CA", "2024-06-01")

        assert tickers == ["COMI"]
        mock_check.assert_called_once()
        assert mock_check.call_args.kwargs.get("required_tickers") == ["COMI"]


class TestESRSScopedBehavior:
    """ESRS failures must not block COMI-only runs."""

    def test_esrs_failure_does_not_block_comi(self, tmp_path):
        """When user requests COMI, ESRS state is irrelevant."""
        graph = _build_graph_instance(
            tmp_path,
            {"enforce_fundamentals_manifest_freshness": True},
        )

        with patch(
            "tradingagents.dataflows.manifest_scanner.scan_fundamentals",
            return_value=[],
        ), patch(
            "tradingagents.dataflows.manifest_scanner.write_manifest",
        ), patch(
            "tradingagents.dataflows.manifest_scanner.check_manifest_freshness",
            return_value=FreshnessResult(fresh=True, reasons=[]),
        ) as mock_check:
            fresh, reasons, tickers = graph._check_manifest_freshness(
                "COMI.CA", "2024-06-01"
            )

        assert fresh is True
        assert tickers == ["COMI"]
        # Only COMI was passed — ESRS is never mentioned
        assert mock_check.call_args.kwargs.get("required_tickers") == ["COMI"]

    def test_esrs_failure_blocks_esrs_run_when_enforced(self, tmp_path):
        """When user requests ESRS.CA + enforce=True -> RuntimeError."""
        graph = _build_graph_instance(
            tmp_path,
            {"enforce_fundamentals_manifest_freshness": True},
        )

        with patch(
            "tradingagents.dataflows.manifest_scanner.scan_fundamentals",
            return_value=[],
        ), patch(
            "tradingagents.dataflows.manifest_scanner.write_manifest",
        ), patch(
            "tradingagents.dataflows.manifest_scanner.check_manifest_freshness",
            return_value=FreshnessResult(
                fresh=False,
                reasons=["ESRS/income: zero data rows"],
            ),
        ):
            with pytest.raises(RuntimeError, match="FAILED"):
                graph._check_manifest_freshness("ESRS.CA", "2024-06-01")


class TestConfigDefault:
    """Verify the config flag defaults."""

    def test_enforce_defaults_to_false(self):
        from tradingagents.default_config import DEFAULT_CONFIG
        assert DEFAULT_CONFIG.get("enforce_fundamentals_manifest_freshness") is False


class TestFreshManifestEnforceAllows:
    """fresh manifest + enforce=True should allow the run (no raise)."""

    def test_fresh_enforce_true_no_raise(self, tmp_path):
        graph = _build_graph_instance(
            tmp_path,
            {"enforce_fundamentals_manifest_freshness": True},
        )

        with patch(
            "tradingagents.dataflows.manifest_scanner.scan_fundamentals",
            return_value=[],
        ), patch(
            "tradingagents.dataflows.manifest_scanner.write_manifest",
        ), patch(
            "tradingagents.dataflows.manifest_scanner.check_manifest_freshness",
            return_value=FreshnessResult(fresh=True, reasons=[]),
        ):
            fresh, reasons, tickers = graph._check_manifest_freshness(
                "COMI.CA", "2024-06-01"
            )

        assert fresh is True
        assert reasons == []
