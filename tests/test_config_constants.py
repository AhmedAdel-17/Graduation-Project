"""Tests that every config-driven domain constant is actually consumed.

Each test overrides a single config key and verifies that the consumer
function's behavior changes accordingly. This locks the contract between
default_config.py and the 4 consumer modules:

  - data_loader.py          → filing_lag_annual_days, filing_lag_quarterly_days
  - sector_config.py        → leverage_alert_threshold
  - calibration.py          → calibration_max_de_for_down, calibration_up_confidence
  - scoring.py              → data_confidence_weights

Pure-unit: no network, no LLM, uses temp CSV files where needed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tradingagents.agents.analysts.fundamentals.calibration import (
    calibrate_earnings_direction,
    _DEFAULT_CALIBRATED_UP_CONFIDENCE,
    _DEFAULT_MAX_DE_FOR_DOWN_CALL,
)
from tradingagents.agents.analysts.fundamentals.scoring import compute_data_confidence
from tradingagents.agents.analysts.fundamentals.sector_config import SectorConfig
from tradingagents.agents.analysts.fundamentals.data_loader import load_multi_period


# ─────────────────────────────────────────────────────────────────────────────
# Helpers for data_loader tests
# ─────────────────────────────────────────────────────────────────────────────


def _write_csv(path: Path, content: str):
    path.write_text(content.strip() + "\n")


@pytest.fixture
def tmp_data_dir(tmp_path):
    egx_dir = tmp_path / "egx_fundamentals"
    (egx_dir / "income_statements").mkdir(parents=True)
    (egx_dir / "balance_sheets").mkdir(parents=True)
    (egx_dir / "key_ratios").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def patch_data_dir(tmp_data_dir):
    with patch("tradingagents.agents.analysts.fundamentals.data_loader.DATA_DIR", str(tmp_data_dir)):
        yield tmp_data_dir


# ─────────────────────────────────────────────────────────────────────────────
# 1. filing_lag_annual_days
# ─────────────────────────────────────────────────────────────────────────────


class TestFilingLagAnnualOverride:
    def test_default_annual_lag_120d(self, patch_data_dir):
        """Default annual filing lag is 120 days."""
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        _write_csv(csv_path, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2023-12-31,5000,3000,2000,1500
2023-06-30,4000,2500,1800,1200""")
        # curr_date=2024-05-01, 120d cutoff=2024-01-02 → 2023-12-31 passes, 2023-06-30 passes
        result = load_multi_period("COMI.CA", curr_date="2024-05-01", freq="annual")
        assert result["diagnostics"]["income"]["date_filter_method"] == "filing_lag_120d"
        assert result["diagnostics"]["income"]["rows_after_date_filter"] == 2

    def test_annual_lag_override_to_90d(self, patch_data_dir):
        """Override filing_lag_annual_days=90 → tighter filter."""
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_annual.csv"
        # Row 2024-01-15: with 120d lag from 2024-05-01, cutoff=2024-01-02 → fails (2024-01-15 > 2024-01-02)
        #                 with 90d lag from 2024-05-01, cutoff=2024-02-01 → passes (2024-01-15 <= 2024-02-01)
        _write_csv(csv_path, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2024-01-15,5000,3000,2000,1500
2023-06-30,4000,2500,1800,1200""")

        # Default 120d: curr_date=2024-05-01, cutoff=2024-01-02 → 2024-01-15 fails, 2023-06-30 passes → 1 row
        result_default = load_multi_period("COMI.CA", curr_date="2024-05-01", freq="annual")
        assert result_default["diagnostics"]["income"]["rows_after_date_filter"] == 1

        # Override to 90d: cutoff=2024-02-01 → both rows pass → 2 rows
        with patch(
            "tradingagents.agents.analysts.fundamentals.data_loader.get_config",
            return_value={"filing_lag_annual_days": 90},
        ):
            result_override = load_multi_period("COMI.CA", curr_date="2024-05-01", freq="annual")
        assert result_override["diagnostics"]["income"]["date_filter_method"] == "filing_lag_90d"
        assert result_override["diagnostics"]["income"]["rows_after_date_filter"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# 2. filing_lag_quarterly_days (already tested in test_quarterly_pipeline.py,
#    included here for completeness of the config-constants audit)
# ─────────────────────────────────────────────────────────────────────────────


class TestFilingLagQuarterlyOverride:
    def test_quarterly_lag_override_to_30d(self, patch_data_dir):
        """Override filing_lag_quarterly_days=30 changes the filter cutoff."""
        csv_path = patch_data_dir / "egx_fundamentals" / "income_statements" / "COMI_income_quarterly.csv"
        _write_csv(csv_path, """\
period_end_date,revenue,gross_profit,operating_income,net_income
2024-03-31,1500,900,600,400
2023-12-31,1400,850,580,390""")

        # curr_date=2024-05-10:
        #   45d cutoff = 2024-03-26 → 2024-03-31 fails → 1 row
        #   30d cutoff = 2024-04-10 → 2024-03-31 passes → 2 rows
        result_default = load_multi_period("COMI.CA", curr_date="2024-05-10", freq="quarterly")
        assert result_default["diagnostics"]["income"]["rows_after_date_filter"] == 1

        with patch(
            "tradingagents.agents.analysts.fundamentals.data_loader.get_config",
            return_value={"filing_lag_quarterly_days": 30},
        ):
            result_override = load_multi_period("COMI.CA", curr_date="2024-05-10", freq="quarterly")
        assert result_override["diagnostics"]["income"]["date_filter_method"] == "filing_lag_30d"
        assert result_override["diagnostics"]["income"]["rows_after_date_filter"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# 3. leverage_alert_threshold
# ─────────────────────────────────────────────────────────────────────────────


class TestLeverageAlertThresholdOverride:
    def test_default_threshold_5(self):
        """Default: D/E=5.5 on operational sector triggers HIGH_LEVERAGE_ALERT."""
        cfg = SectorConfig("ETEL")
        flags = cfg.generate_distress_flags(
            net_margin=0.10, debt_to_equity=5.5, current_ratio=1.5,
            total_equity=1000, revenue=5000, eps=2.0, pe_ratio=10.0, roe=0.15,
        )
        assert "HIGH_LEVERAGE_ALERT" in flags

    def test_threshold_override_to_8(self):
        """Override to 8.0: D/E=5.5 no longer triggers alert."""
        with patch(
            "tradingagents.agents.analysts.fundamentals.sector_config.get_config",
            return_value={"leverage_alert_threshold": 8.0},
        ):
            cfg = SectorConfig("ETEL")
            flags = cfg.generate_distress_flags(
                net_margin=0.10, debt_to_equity=5.5, current_ratio=1.5,
                total_equity=1000, revenue=5000, eps=2.0, pe_ratio=10.0, roe=0.15,
            )
        assert "HIGH_LEVERAGE_ALERT" not in flags

    def test_threshold_override_to_3(self):
        """Override to 3.0: D/E=3.5 now triggers alert (wouldn't at default 5.0)."""
        # First confirm default doesn't trigger
        cfg_default = SectorConfig("ETEL")
        flags_default = cfg_default.generate_distress_flags(
            net_margin=0.10, debt_to_equity=3.5, current_ratio=1.5,
            total_equity=1000, revenue=5000, eps=2.0, pe_ratio=10.0, roe=0.15,
        )
        assert "HIGH_LEVERAGE_ALERT" not in flags_default

        # Now override
        with patch(
            "tradingagents.agents.analysts.fundamentals.sector_config.get_config",
            return_value={"leverage_alert_threshold": 3.0},
        ):
            cfg = SectorConfig("ETEL")
            flags = cfg.generate_distress_flags(
                net_margin=0.10, debt_to_equity=3.5, current_ratio=1.5,
                total_equity=1000, revenue=5000, eps=2.0, pe_ratio=10.0, roe=0.15,
            )
        assert "HIGH_LEVERAGE_ALERT" in flags


# ─────────────────────────────────────────────────────────────────────────────
# 4. calibration_max_de_for_down
# ─────────────────────────────────────────────────────────────────────────────


class TestCalibrationMaxDEOverride:
    def test_default_gate_at_4(self):
        """Default: D/E=5.0 > 4.0 → down overridden to up."""
        r = calibrate_earnings_direction(
            fundamental_outlook="bearish", downside_risk_level="high",
            raw_earnings_direction="down", earnings_direction_confidence=90,
            sector="operational", de_ratio=5.0,
        )
        assert r.calibrated_direction == "up"

    def test_override_gate_to_6(self):
        """Override to 6.0: D/E=5.0 no longer triggers gate → down kept."""
        with patch(
            "tradingagents.agents.analysts.fundamentals.calibration.get_config",
            return_value={"calibration_max_de_for_down": 6.0},
        ):
            r = calibrate_earnings_direction(
                fundamental_outlook="bearish", downside_risk_level="high",
                raw_earnings_direction="down", earnings_direction_confidence=90,
                sector="operational", de_ratio=5.0,
            )
        assert r.calibrated_direction == "down"

    def test_override_gate_to_2(self):
        """Override to 2.0: D/E=3.0 now triggers gate (wouldn't at default 4.0)."""
        # Default: D/E=3.0 < 4.0, bearish+high+conf>=75 → down kept
        r_default = calibrate_earnings_direction(
            fundamental_outlook="bearish", downside_risk_level="high",
            raw_earnings_direction="down", earnings_direction_confidence=90,
            sector="operational", de_ratio=3.0,
        )
        assert r_default.calibrated_direction == "down"

        # Override: D/E=3.0 > 2.0 → up
        with patch(
            "tradingagents.agents.analysts.fundamentals.calibration.get_config",
            return_value={"calibration_max_de_for_down": 2.0},
        ):
            r_override = calibrate_earnings_direction(
                fundamental_outlook="bearish", downside_risk_level="high",
                raw_earnings_direction="down", earnings_direction_confidence=90,
                sector="operational", de_ratio=3.0,
            )
        assert r_override.calibrated_direction == "up"


# ─────────────────────────────────────────────────────────────────────────────
# 5. calibration_up_confidence
# ─────────────────────────────────────────────────────────────────────────────


class TestCalibrationUpConfidenceOverride:
    def test_default_confidence_60(self):
        """Default: flat → up with confidence=60."""
        r = calibrate_earnings_direction(
            fundamental_outlook="neutral", downside_risk_level="moderate",
            raw_earnings_direction="flat", earnings_direction_confidence=65,
        )
        assert r.calibrated_direction == "up"
        assert r.calibrated_confidence == 60  # _DEFAULT_CALIBRATED_UP_CONFIDENCE

    def test_override_confidence_to_50(self):
        """Override to 50: flat → up with confidence=50."""
        with patch(
            "tradingagents.agents.analysts.fundamentals.calibration.get_config",
            return_value={"calibration_up_confidence": 50},
        ):
            r = calibrate_earnings_direction(
                fundamental_outlook="neutral", downside_risk_level="moderate",
                raw_earnings_direction="flat", earnings_direction_confidence=65,
            )
        assert r.calibrated_direction == "up"
        assert r.calibrated_confidence == 50

    def test_override_confidence_to_75(self):
        """Override to 75: banks sector down → up with confidence=75."""
        with patch(
            "tradingagents.agents.analysts.fundamentals.calibration.get_config",
            return_value={"calibration_up_confidence": 75},
        ):
            r = calibrate_earnings_direction(
                fundamental_outlook="bearish", downside_risk_level="high",
                raw_earnings_direction="down", earnings_direction_confidence=90,
                sector="banks",
            )
        assert r.calibrated_direction == "up"
        assert r.calibrated_confidence == 75


# ─────────────────────────────────────────────────────────────────────────────
# 6. data_confidence_weights
# ─────────────────────────────────────────────────────────────────────────────


class TestScoringWeightsOverride:
    """Test that overriding data_confidence_weights changes the score."""

    _FULL_INCOME = {
        "revenue": 1000, "gross_profit": 500,
        "operating_income": 300, "net_income": 200,
    }
    _FULL_BALANCE = {
        "total_assets": 5000, "total_liabilities": 3000, "total_equity": 2000,
    }
    _EMPTY_RATIOS: dict = {}

    def test_default_weights_produce_expected_score(self):
        """All required fields, 0 optional, current data, 5 periods → known score."""
        # field_coverage=7/7=1.0, optional=0, staleness=1.0 (periods_since=0), depth=5/8=0.625
        # score = (1.0*0.45 + 0*0.15 + 1.0*0.25 + 0.625*0.15) * 100
        #       = (0.45 + 0 + 0.25 + 0.09375) * 100 = 79.375 → 79
        score = compute_data_confidence(
            self._FULL_INCOME, self._FULL_BALANCE, self._EMPTY_RATIOS,
            n_annual_periods=5, periods_since_last_filing=0,
        )
        assert score == 79

    def test_override_weights_all_on_field_coverage(self):
        """Override: field_coverage=1.0, rest=0 → score = field_coverage * 100."""
        with patch(
            "tradingagents.agents.analysts.fundamentals.scoring.get_config",
            return_value={"data_confidence_weights": {
                "field_coverage": 1.0, "optional_coverage": 0.0,
                "staleness": 0.0, "period_depth": 0.0,
            }},
        ):
            # 7/7 required fields → field_coverage=1.0 → score=100
            score = compute_data_confidence(
                self._FULL_INCOME, self._FULL_BALANCE, self._EMPTY_RATIOS,
                n_annual_periods=0, periods_since_last_filing=6,
            )
        assert score == 100

    def test_override_weights_all_on_staleness(self):
        """Override: staleness=1.0, rest=0 → score depends only on freshness."""
        with patch(
            "tradingagents.agents.analysts.fundamentals.scoring.get_config",
            return_value={"data_confidence_weights": {
                "field_coverage": 0.0, "optional_coverage": 0.0,
                "staleness": 1.0, "period_depth": 0.0,
            }},
        ):
            # periods_since=0 → staleness=1.0 → score=100
            score_fresh = compute_data_confidence(
                self._FULL_INCOME, self._FULL_BALANCE, self._EMPTY_RATIOS,
                n_annual_periods=5, periods_since_last_filing=0,
            )
            # periods_since=6 → staleness=0.0 → score=0
            score_stale = compute_data_confidence(
                self._FULL_INCOME, self._FULL_BALANCE, self._EMPTY_RATIOS,
                n_annual_periods=5, periods_since_last_filing=6,
            )
        assert score_fresh == 100
        assert score_stale == 0
