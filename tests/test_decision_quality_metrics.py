"""Tests for per-decision EGX30-relative quality metrics (Phase B).

These tests exercise ``BacktestingEngine._calculate_decision_metrics()``
with synthetic audit_log data.  No LLM or API calls are made.
"""

import pytest
from unittest.mock import MagicMock, patch


def _make_engine():
    """Create a minimal BacktestingEngine with mocked dependencies."""
    with patch("scripts.backtester.DataGateway"):
        with patch("scripts.backtester.TradingAgentsGraph"):
            from scripts.backtester import BacktestingEngine
            engine = BacktestingEngine.__new__(BacktestingEngine)
            engine.audit_log = []
            engine.trade_history = []
            engine.daily_history = []
            engine.benchmark_history = []
            engine.buyhold_history = []
            engine.initial_capital = 1_000_000.0
            engine.portfolio_value = 1_000_000.0
            engine.benchmark_start_price = None
            engine.buyhold_start_price = None
            engine._bm_data_map = {}
            engine.benchmark_ticker = None
            engine.risk_free_rate = 0.2725
            engine.config = {}
            engine._output_dir = ""
            return engine


class TestDecisionQualityMetrics:
    """Unit tests for _calculate_decision_metrics()."""

    def test_empty_audit_log_returns_empty(self):
        engine = _make_engine()
        engine.audit_log = []
        result = engine._calculate_decision_metrics()
        assert result == {}

    def test_no_forward_returns_returns_empty(self):
        """Audit entries without fwd_excess_return should be ignored."""
        engine = _make_engine()
        engine.audit_log = [
            {"date": "2024-01-15", "price": 50.0, "parsed_decision": "BUY"},
            {"date": "2024-01-29", "price": 52.0, "parsed_decision": "HOLD"},
        ]
        result = engine._calculate_decision_metrics()
        assert result == {}

    def test_single_buy_hit(self):
        """BUY where ticker outperformed EGX30 → hit rate 100%."""
        engine = _make_engine()
        engine.audit_log = [
            {
                "date": "2024-01-15",
                "price": 50.0,
                "egx30_price": 30000.0,
                "parsed_decision": "BUY",
                "fwd_ticker_return": 0.04,      # +4%
                "fwd_egx30_return": 0.01,        # +1%
                "fwd_excess_return": 0.03,        # +3% excess
                "holding_days": 14,
            },
        ]
        result = engine._calculate_decision_metrics()
        assert result["n_buy"] == 1
        assert result["buy_hit_rate_vs_egx30"] == 1.0
        assert result["false_buy_rate"] == 0.0
        assert result["buy_mean_excess_return"] == pytest.approx(0.03, abs=1e-6)

    def test_single_buy_miss(self):
        """BUY where ticker underperformed EGX30 → false buy rate 100%."""
        engine = _make_engine()
        engine.audit_log = [
            {
                "date": "2024-01-15",
                "price": 50.0,
                "egx30_price": 30000.0,
                "parsed_decision": "BUY",
                "fwd_ticker_return": 0.01,
                "fwd_egx30_return": 0.03,
                "fwd_excess_return": -0.02,
                "holding_days": 14,
            },
        ]
        result = engine._calculate_decision_metrics()
        assert result["buy_hit_rate_vs_egx30"] == 0.0
        assert result["false_buy_rate"] == 1.0

    def test_hold_correct_rejection(self):
        """HOLD when ticker underperformed EGX30 → correct rejection."""
        engine = _make_engine()
        engine.audit_log = [
            {
                "date": "2024-01-15",
                "price": 50.0,
                "egx30_price": 30000.0,
                "parsed_decision": "HOLD",
                "fwd_ticker_return": -0.02,
                "fwd_egx30_return": 0.01,
                "fwd_excess_return": -0.03,
                "holding_days": 14,
            },
        ]
        result = engine._calculate_decision_metrics()
        assert result["hold_rejection_quality"] == 1.0
        assert result["hold_miss_rate"] == 0.0

    def test_hold_missed_opportunity(self):
        """HOLD when ticker outperformed EGX30 → missed opportunity."""
        engine = _make_engine()
        engine.audit_log = [
            {
                "date": "2024-01-15",
                "price": 50.0,
                "egx30_price": 30000.0,
                "parsed_decision": "HOLD",
                "fwd_ticker_return": 0.05,
                "fwd_egx30_return": 0.01,
                "fwd_excess_return": 0.04,
                "holding_days": 14,
            },
        ]
        result = engine._calculate_decision_metrics()
        assert result["hold_rejection_quality"] == 0.0
        assert result["hold_miss_rate"] == 1.0

    def test_sell_treated_as_hold_category(self):
        """SELL decisions are grouped with HOLD for rejection quality."""
        engine = _make_engine()
        engine.audit_log = [
            {
                "date": "2024-01-15",
                "price": 50.0,
                "egx30_price": 30000.0,
                "parsed_decision": "SELL",
                "fwd_ticker_return": -0.03,
                "fwd_egx30_return": 0.01,
                "fwd_excess_return": -0.04,
                "holding_days": 14,
            },
        ]
        result = engine._calculate_decision_metrics()
        assert result["n_hold_sell"] == 1
        assert result["hold_rejection_quality"] == 1.0

    def test_mixed_decisions(self):
        """Mix of BUY hits, BUY misses, and correct HOLDs."""
        engine = _make_engine()
        engine.audit_log = [
            # BUY hit: +3% excess
            {
                "date": "2024-01-15", "price": 50.0, "egx30_price": 30000.0,
                "parsed_decision": "BUY",
                "fwd_ticker_return": 0.04, "fwd_egx30_return": 0.01,
                "fwd_excess_return": 0.03, "holding_days": 14,
            },
            # BUY miss: -2% excess
            {
                "date": "2024-01-29", "price": 52.0, "egx30_price": 30300.0,
                "parsed_decision": "BUY",
                "fwd_ticker_return": 0.01, "fwd_egx30_return": 0.03,
                "fwd_excess_return": -0.02, "holding_days": 14,
            },
            # HOLD correct rejection: -1% excess
            {
                "date": "2024-02-12", "price": 52.5, "egx30_price": 31200.0,
                "parsed_decision": "HOLD",
                "fwd_ticker_return": -0.01, "fwd_egx30_return": 0.00,
                "fwd_excess_return": -0.01, "holding_days": 14,
            },
            # HOLD missed opportunity: +2% excess
            {
                "date": "2024-02-26", "price": 52.0, "egx30_price": 31200.0,
                "parsed_decision": "HOLD",
                "fwd_ticker_return": 0.03, "fwd_egx30_return": 0.01,
                "fwd_excess_return": 0.02, "holding_days": 14,
            },
        ]
        result = engine._calculate_decision_metrics()

        assert result["total_measurable_decisions"] == 4
        assert result["n_buy"] == 2
        assert result["n_hold_sell"] == 2

        # BUY: 1 hit / 2 total = 50%
        assert result["buy_hit_rate_vs_egx30"] == 0.5
        assert result["false_buy_rate"] == 0.5
        # Mean BUY excess: (0.03 + (-0.02)) / 2 = 0.005
        assert result["buy_mean_excess_return"] == pytest.approx(0.005, abs=1e-6)

        # HOLD: 1 correct / 2 total = 50%
        assert result["hold_rejection_quality"] == 0.5
        assert result["hold_miss_rate"] == 0.5

    def test_wilson_ci_present(self):
        """Wilson CI bounds should be present when there are decisions."""
        engine = _make_engine()
        engine.audit_log = [
            {
                "date": "2024-01-15", "price": 50.0, "egx30_price": 30000.0,
                "parsed_decision": "BUY",
                "fwd_ticker_return": 0.04, "fwd_egx30_return": 0.01,
                "fwd_excess_return": 0.03, "holding_days": 14,
            },
        ]
        result = engine._calculate_decision_metrics()
        assert "buy_hit_rate_ci_lo" in result
        assert "buy_hit_rate_ci_hi" in result
        assert result["buy_hit_rate_ci_lo"] <= result["buy_hit_rate_vs_egx30"]
        assert result["buy_hit_rate_ci_hi"] >= result["buy_hit_rate_vs_egx30"]

    def test_per_decision_detail_present(self):
        """The per_decision array should list each measurable decision."""
        engine = _make_engine()
        engine.audit_log = [
            {
                "date": "2024-01-15", "price": 50.0, "egx30_price": 30000.0,
                "parsed_decision": "BUY",
                "fwd_ticker_return": 0.04, "fwd_egx30_return": 0.01,
                "fwd_excess_return": 0.03, "holding_days": 14,
            },
            {
                "date": "2024-01-29", "price": 52.0, "egx30_price": 30300.0,
                "parsed_decision": "HOLD",
                "fwd_ticker_return": -0.01, "fwd_egx30_return": 0.00,
                "fwd_excess_return": -0.01, "holding_days": 14,
            },
        ]
        result = engine._calculate_decision_metrics()
        assert len(result["per_decision"]) == 2
        assert result["per_decision"][0]["decision"] == "BUY"
        assert result["per_decision"][0]["quality_label"] == "HIT"
        assert result["per_decision"][1]["decision"] == "HOLD"
        assert result["per_decision"][1]["quality_label"] == "CORRECT_REJECTION"

    def test_all_holds_no_buys(self):
        """When there are no BUY decisions, BUY metrics should be absent."""
        engine = _make_engine()
        engine.audit_log = [
            {
                "date": "2024-01-15", "price": 50.0, "egx30_price": 30000.0,
                "parsed_decision": "HOLD",
                "fwd_ticker_return": -0.02, "fwd_egx30_return": 0.01,
                "fwd_excess_return": -0.03, "holding_days": 14,
            },
        ]
        result = engine._calculate_decision_metrics()
        assert result["n_buy"] == 0
        assert "buy_hit_rate_vs_egx30" not in result
        assert "false_buy_rate" not in result
        assert result["hold_rejection_quality"] == 1.0

    def test_zero_excess_return_counts_as_miss_for_buy(self):
        """Exactly zero excess return should count as a BUY miss (not a hit)."""
        engine = _make_engine()
        engine.audit_log = [
            {
                "date": "2024-01-15", "price": 50.0, "egx30_price": 30000.0,
                "parsed_decision": "BUY",
                "fwd_ticker_return": 0.02, "fwd_egx30_return": 0.02,
                "fwd_excess_return": 0.0,
                "holding_days": 14,
            },
        ]
        result = engine._calculate_decision_metrics()
        assert result["buy_hit_rate_vs_egx30"] == 0.0
        assert result["false_buy_rate"] == 1.0

    def test_zero_excess_return_counts_as_correct_hold(self):
        """Exactly zero excess return should count as correct HOLD rejection."""
        engine = _make_engine()
        engine.audit_log = [
            {
                "date": "2024-01-15", "price": 50.0, "egx30_price": 30000.0,
                "parsed_decision": "HOLD",
                "fwd_ticker_return": 0.02, "fwd_egx30_return": 0.02,
                "fwd_excess_return": 0.0,
                "holding_days": 14,
            },
        ]
        result = engine._calculate_decision_metrics()
        assert result["hold_rejection_quality"] == 1.0
        assert result["hold_miss_rate"] == 0.0

    def test_quality_label_all_cases(self):
        """quality_label should be decision-aware for all four cases."""
        engine = _make_engine()
        engine.audit_log = [
            # BUY HIT: positive excess
            {
                "date": "2024-01-15", "price": 50.0, "egx30_price": 30000.0,
                "parsed_decision": "BUY",
                "fwd_ticker_return": 0.04, "fwd_egx30_return": 0.01,
                "fwd_excess_return": 0.03, "holding_days": 14,
            },
            # BUY MISS: negative excess
            {
                "date": "2024-01-29", "price": 52.0, "egx30_price": 30300.0,
                "parsed_decision": "BUY",
                "fwd_ticker_return": 0.01, "fwd_egx30_return": 0.03,
                "fwd_excess_return": -0.02, "holding_days": 14,
            },
            # HOLD CORRECT_REJECTION: negative excess
            {
                "date": "2024-02-12", "price": 52.5, "egx30_price": 31200.0,
                "parsed_decision": "HOLD",
                "fwd_ticker_return": -0.01, "fwd_egx30_return": 0.00,
                "fwd_excess_return": -0.01, "holding_days": 14,
            },
            # SELL MISSED_OPPORTUNITY: positive excess
            {
                "date": "2024-02-26", "price": 52.0, "egx30_price": 31200.0,
                "parsed_decision": "SELL",
                "fwd_ticker_return": 0.05, "fwd_egx30_return": 0.01,
                "fwd_excess_return": 0.04, "holding_days": 14,
            },
        ]
        result = engine._calculate_decision_metrics()
        labels = [d["quality_label"] for d in result["per_decision"]]
        assert labels == ["HIT", "MISS", "CORRECT_REJECTION", "MISSED_OPPORTUNITY"]


class TestForwardReturnBackfill:
    """Test the forward-return backfill logic that runs in the audit_log
    during the backtest loop.

    Instead of running a full backtest, we simulate the backfill logic
    directly to verify correctness.
    """

    def _simulate_backfill(self, entries):
        """Simulate the backfill logic from the backtest loop.

        Takes a list of audit entries (with date, price, egx30_price,
        parsed_decision) and backfills fwd_* fields on each entry[i]
        using entry[i+1].
        """
        from datetime import datetime

        for i in range(1, len(entries)):
            prev = entries[i - 1]
            cur = entries[i]
            prev_price = prev.get("price")
            cur_price = cur.get("price")
            prev_egx = prev.get("egx30_price")
            cur_egx = cur.get("egx30_price")

            if prev_price and prev_price > 0 and cur_price and cur_price > 0:
                fwd_ticker = (cur_price / prev_price) - 1.0
                prev["fwd_ticker_return"] = fwd_ticker
                if (prev_egx and prev_egx > 0 and cur_egx and cur_egx > 0):
                    fwd_egx = (cur_egx / prev_egx) - 1.0
                    prev["fwd_egx30_return"] = fwd_egx
                    prev["fwd_excess_return"] = fwd_ticker - fwd_egx
                try:
                    d_prev = datetime.strptime(prev["date"], "%Y-%m-%d")
                    d_cur = datetime.strptime(cur["date"], "%Y-%m-%d")
                    prev["holding_days"] = (d_cur - d_prev).days
                except (ValueError, KeyError):
                    pass
        return entries

    def test_basic_backfill(self):
        entries = [
            {"date": "2024-01-15", "price": 50.0, "egx30_price": 30000.0, "parsed_decision": "BUY"},
            {"date": "2024-01-29", "price": 52.0, "egx30_price": 30300.0, "parsed_decision": "HOLD"},
        ]
        self._simulate_backfill(entries)

        # First entry should have forward returns
        assert entries[0]["fwd_ticker_return"] == pytest.approx(0.04, abs=1e-6)
        assert entries[0]["fwd_egx30_return"] == pytest.approx(0.01, abs=1e-6)
        assert entries[0]["fwd_excess_return"] == pytest.approx(0.03, abs=1e-6)
        assert entries[0]["holding_days"] == 14

        # Last entry should NOT have forward returns
        assert "fwd_ticker_return" not in entries[1]
        assert "fwd_excess_return" not in entries[1]

    def test_backfill_without_egx30(self):
        """When EGX30 price is missing, only ticker return is computed."""
        entries = [
            {"date": "2024-01-15", "price": 50.0, "egx30_price": None, "parsed_decision": "BUY"},
            {"date": "2024-01-29", "price": 52.0, "egx30_price": None, "parsed_decision": "HOLD"},
        ]
        self._simulate_backfill(entries)

        assert entries[0]["fwd_ticker_return"] == pytest.approx(0.04, abs=1e-6)
        assert "fwd_egx30_return" not in entries[0]
        assert "fwd_excess_return" not in entries[0]

    def test_three_dates(self):
        """Each consecutive pair should produce a forward return."""
        entries = [
            {"date": "2024-01-15", "price": 50.0, "egx30_price": 30000.0, "parsed_decision": "BUY"},
            {"date": "2024-01-29", "price": 52.0, "egx30_price": 30300.0, "parsed_decision": "HOLD"},
            {"date": "2024-02-12", "price": 51.0, "egx30_price": 30600.0, "parsed_decision": "SELL"},
        ]
        self._simulate_backfill(entries)

        # Entry 0 → measured against entry 1
        assert "fwd_excess_return" in entries[0]
        # Entry 1 → measured against entry 2
        assert "fwd_excess_return" in entries[1]
        # Entry 2 → last, no forward return
        assert "fwd_excess_return" not in entries[2]
