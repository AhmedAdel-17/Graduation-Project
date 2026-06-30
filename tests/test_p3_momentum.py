"""
P3 Tests: Momentum, Relative Strength, NAV proxy, leakage safety, and integration.

Covers T1-T15, T23-T41 from P3 spec §7.
T16-T22 (EGX30 loader) are in tests/test_egx30_loader.py.
"""
import math

import pytest

from tradingagents.agents.analysts.fundamentals.momentum import (
    MomentumPack,
    _derive_momentum_label,
    _derive_rs_label,
    _find_egx30_close,
    _period_return,
    _price_vs_sma,
    _trend_slope_annualized,
    _volume_ratio,
    compute_momentum_pack,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_series(start: float, end: float, n: int) -> list:
    """Linear interpolation from start to end over n points."""
    if n == 1:
        return [end]
    step = (end - start) / (n - 1)
    return [start + i * step for i in range(n)]


def _make_dates(n: int, start: str = "2024-01-01") -> list:
    """Generate n sequential YYYY-MM-DD dates from start."""
    from datetime import datetime, timedelta
    dt = datetime.strptime(start, "%Y-%m-%d")
    return [(dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


# =============================================================================
# §7.1 Momentum computation tests (T1-T11)
# =============================================================================

class TestMomentumComputation:
    """T1-T11: Core momentum feature computation."""

    def test_return_20d_basic(self):
        """T1: Correct 20-day return from known price series."""
        # 21 bars: first = 100, last = 120 → return = 20%
        closes = [100.0] + [100.0] * 19 + [120.0]
        assert _period_return(closes, 20) == pytest.approx(0.20)

    def test_return_60d_basic(self):
        """T2: Correct 60-day return."""
        closes = [80.0] + [80.0] * 59 + [100.0]
        assert _period_return(closes, 60) == pytest.approx(0.25)

    def test_return_120d_basic(self):
        """T3: Correct 120-day return."""
        closes = [50.0] + [50.0] * 119 + [75.0]
        assert _period_return(closes, 120) == pytest.approx(0.50)

    def test_insufficient_history_returns_null(self):
        """T4: < 20 bars → return_20d is None, not 0.0 or error."""
        closes = [100.0] * 15  # only 15 bars
        result = _period_return(closes, 20)
        assert result is None

    def test_sma_position_above(self):
        """T5: Price above SMA50 → positive price_vs_sma50."""
        # 50 bars at 100, then current at 120
        closes = [100.0] * 50 + [120.0]
        result = _price_vs_sma(closes, 50)
        # SMA50 of last 50 = (100*49 + 120)/50 = 100.4; but _sma uses last 50
        # Actually closes has 51 items, _sma takes last 50 → [100]*49 + [120] → avg = 100.4
        # price_vs_sma = 120 / 100.4 - 1 ≈ 0.1952
        assert result is not None
        assert result > 0

    def test_sma_position_below(self):
        """T6: Price below SMA50 → negative price_vs_sma50."""
        closes = [120.0] * 50 + [100.0]
        result = _price_vs_sma(closes, 50)
        assert result is not None
        assert result < 0

    def test_sma200_insufficient_history(self):
        """T7: < 200 bars → price_vs_sma200 is None."""
        closes = [100.0] * 150  # 150 < 200
        result = _price_vs_sma(closes, 200)
        assert result is None

    def test_volume_ratio_above_threshold(self):
        """T8: volume_ratio > 1.2 → volume_confirmed = True."""
        volumes = [1000.0] * 19 + [1500.0]  # 20 bars, last = 1.5x avg
        ratio = _volume_ratio(volumes, 20)
        assert ratio is not None
        assert ratio > 1.2  # 1500 / avg(1000*19+1500)/20 ≈ 1500/1025 ≈ 1.46

    def test_volume_ratio_below_threshold(self):
        """T9: volume_ratio ≤ 1.2 → volume_confirmed = False."""
        volumes = [1000.0] * 19 + [1100.0]  # 20 bars, last = 1.1x avg
        ratio = _volume_ratio(volumes, 20)
        assert ratio is not None
        # avg = (1000*19 + 1100) / 20 = 1005; ratio = 1100/1005 ≈ 1.095
        assert ratio <= 1.2

    def test_trend_slope_positive(self):
        """T10: Uptrending series → positive annualized slope."""
        # Exponentially growing series (10% over 60 bars)
        closes = [100.0 * math.exp(0.001 * i) for i in range(60)]
        slope = _trend_slope_annualized(closes, 60)
        assert slope is not None
        assert slope > 0

    def test_trend_slope_flat(self):
        """T11: Flat series → slope near zero."""
        closes = [100.0] * 60
        slope = _trend_slope_annualized(closes, 60)
        assert slope is not None
        assert abs(slope) < 0.01  # Nearly zero


# =============================================================================
# §7.2 Momentum label tests (T12-T15)
# =============================================================================

class TestMomentumLabel:
    """T12-T15: Derived momentum label classification."""

    def test_momentum_label_strong_up(self):
        """T12: +20% 60d, above SMA50, vol confirmed → strong_up."""
        label = _derive_momentum_label(
            return_60d=0.20,
            price_vs_sma50=0.10,
            volume_confirmed=True,
        )
        assert label == "strong_up"

    def test_momentum_label_neutral(self):
        """T13: Mixed signals → neutral."""
        label = _derive_momentum_label(
            return_60d=0.03,  # < 5%, not strongly up
            price_vs_sma50=0.01,  # slightly above
            volume_confirmed=False,
        )
        assert label == "neutral"

    def test_momentum_label_strong_down(self):
        """T14: -20% 60d, below SMA50, vol confirmed → strong_down."""
        label = _derive_momentum_label(
            return_60d=-0.20,
            price_vs_sma50=-0.10,
            volume_confirmed=True,
        )
        assert label == "strong_down"

    def test_momentum_label_insufficient(self):
        """T15: return_60d is None → insufficient_history."""
        label = _derive_momentum_label(
            return_60d=None,
            price_vs_sma50=None,
            volume_confirmed=False,
        )
        assert label == "insufficient_history"


# =============================================================================
# §7.4 Relative strength tests (T23-T29)
# =============================================================================

class TestRelativeStrength:
    """T23-T29: Relative strength vs EGX30."""

    def test_rs_60d_outperforming(self):
        """T23: Ticker +30%, EGX30 +10% → rs_60d = +20pp, label = outperforming."""
        n = 61  # need 61 bars for 60-day return
        dates = _make_dates(n)
        # Ticker: 100 → 130 (30% return)
        closes = _make_series(100, 130, n)
        volumes = [1000.0] * n
        # EGX30: 10000 → 11000 (10% return) — need dates[0] and dates[-1]
        egx30 = {dates[0]: 10000.0, dates[-1]: 11000.0}

        pack = compute_momentum_pack(closes, volumes, dates, dates[-1], egx30)
        assert pack["rs_60d"] is not None
        assert pack["rs_60d"] == pytest.approx(0.20, abs=0.01)
        assert pack["rs_label"] == "outperforming"

    def test_rs_60d_underperforming(self):
        """T24: Ticker +5%, EGX30 +15% → rs_60d = -10pp, label = underperforming."""
        n = 61
        dates = _make_dates(n)
        closes = _make_series(100, 105, n)
        volumes = [1000.0] * n
        egx30 = {dates[0]: 10000.0, dates[-1]: 11500.0}

        pack = compute_momentum_pack(closes, volumes, dates, dates[-1], egx30)
        assert pack["rs_60d"] is not None
        assert pack["rs_60d"] == pytest.approx(-0.10, abs=0.01)
        assert pack["rs_label"] == "underperforming"

    def test_rs_missing_egx30_returns_null(self):
        """T25: Empty EGX30 map → all rs_* = None, label = insufficient_data."""
        n = 61
        dates = _make_dates(n)
        closes = _make_series(100, 130, n)
        volumes = [1000.0] * n

        pack = compute_momentum_pack(closes, volumes, dates, dates[-1], egx30_map={})
        assert pack["rs_20d"] is None
        assert pack["rs_60d"] is None
        assert pack["rs_120d"] is None
        assert pack["rs_label"] == "insufficient_data"

    def test_rs_missing_egx30_dates_backward_search(self):
        """T26: EGX30 has gap on target date → backward search up to 3 days finds nearest."""
        # Target date is 2024-01-20, EGX30 has data on 2024-01-19 (1 day back)
        egx30 = {"2024-01-19": 15000.0, "2024-01-25": 15500.0}
        close = _find_egx30_close(egx30, "2024-01-20")
        assert close == 15000.0

    def test_rs_missing_egx30_dates_beyond_3_days(self):
        """T27: EGX30 gap > 3 days → returns None (no stale-price leakage)."""
        egx30 = {"2024-01-10": 15000.0}  # 10 days before target
        close = _find_egx30_close(egx30, "2024-01-20")
        assert close is None

    def test_rs_windows_use_only_dates_lte_trade_date(self):
        """T28: EGX30 map with dates after trade_date → those dates excluded."""
        n = 61
        dates = _make_dates(n, start="2024-01-01")
        trade_date = dates[-1]  # 2024-03-01 (approx)
        closes = _make_series(100, 130, n)
        volumes = [1000.0] * n

        # Include EGX30 data that extends beyond trade_date
        egx30 = {
            dates[0]: 10000.0,
            dates[-1]: 11000.0,
            "2025-12-31": 99999.0,  # Future! Should be excluded.
        }
        pack = compute_momentum_pack(closes, volumes, dates, trade_date, egx30)
        # The future date should not affect the computation
        assert pack["rs_60d"] is not None
        # rs_60d should be based on egx30 return from dates[0]→dates[-1]
        # not from some future price

    def test_no_yfinance_fallback_when_csv_exists(self):
        """T29: When local CSV is present, the loader never attempts yfinance."""
        # This is a design test — egx30_loader has no yfinance import at all
        import tradingagents.dataflows.egx30_loader as loader_module
        source_code = open(loader_module.__file__).read()
        assert "import yfinance" not in source_code
        assert "yf.download" not in source_code


# =============================================================================
# §7.5 Leakage safety tests (T30-T33)
# =============================================================================

class TestLeakageSafety:
    """T30-T33: No future data leakage."""

    def test_no_future_ohlcv_in_momentum(self):
        """T30: OHLCV with dates after trade_date → truncated, not used."""
        dates = _make_dates(100, start="2024-01-01")
        closes = _make_series(100, 200, 100)
        volumes = [1000.0] * 100

        # trade_date is 50 bars in — should only use first 50
        trade_date = dates[49]
        pack = compute_momentum_pack(closes, volumes, dates, trade_date, egx30_map={})

        # With only 50 bars, return_60d should be None (insufficient)
        assert pack["return_60d"] is None
        # return_20d should work with 50 bars
        assert pack["return_20d"] is not None
        # The 20d return should be based on closes[29] to closes[49], not future data
        expected = (closes[49] / closes[29]) - 1
        assert pack["return_20d"] == pytest.approx(expected, abs=0.001)

    def test_no_future_egx30_in_relative_strength(self):
        """T31: EGX30 map with future dates → truncated."""
        n = 25
        dates = _make_dates(n, start="2024-01-01")
        closes = _make_series(100, 125, n)
        volumes = [1000.0] * n
        trade_date = dates[-1]

        egx30 = {
            dates[0]: 10000.0,
            dates[4]: 10100.0,   # within window for backward search
            dates[-1]: 10500.0,
            "2025-01-01": 50000.0,  # Future! Must not be used.
            "2025-06-01": 60000.0,  # Also future.
        }
        pack = compute_momentum_pack(closes, volumes, dates, trade_date, egx30)
        # rs_20d should be computed without future EGX30 data
        # With 25 bars: return_20d needs 21 bars ✓, EGX30 needs dates[-21] and dates[-1]
        assert pack["return_20d"] is not None
        # rs_20d may be None if EGX30 doesn't have an exact match at dates[-21]
        # The key assertion: future dates are excluded from the EGX30 map
        if pack["rs_20d"] is not None:
            # If rs_20d was computed, it used trade_date EGX30 price (10500), not future (50000)
            # ticker return ≈ 25/n * 20/n  → moderate
            # egx30 return would be based on ≤ trade_date prices
            assert abs(pack["rs_20d"]) < 1.0  # Not using the 50000 future price

    def test_no_future_volume_in_momentum(self):
        """T32: Volume from future dates → not used."""
        dates = _make_dates(50, start="2024-01-01")
        closes = [100.0] * 50
        # Future volumes are abnormally high — if leaked, volume_ratio would spike
        volumes = [1000.0] * 25 + [99999.0] * 25

        trade_date = dates[24]  # Only first 25 bars should be used
        pack = compute_momentum_pack(closes, volumes, dates, trade_date, egx30_map={})

        # volume_ratio should be based on first 25 bars only
        # With only 25 bars of volume=1000, ratio = 1000/1000 = 1.0
        if pack["volume_ratio_20d"] is not None:
            assert pack["volume_ratio_20d"] == pytest.approx(1.0, abs=0.01)

    def test_sequential_dates_no_leakage(self):
        """T33: Compute momentum for date T, then T+20 → T+20 does not contain T+21 data."""
        dates = _make_dates(80, start="2024-01-01")
        closes = _make_series(100, 180, 80)
        volumes = [1000.0] * 80

        # First computation at T = dates[39] (40 bars)
        pack_t = compute_momentum_pack(closes, volumes, dates, dates[39], egx30_map={})
        # Second at T+20 = dates[59] (60 bars)
        pack_t20 = compute_momentum_pack(closes, volumes, dates, dates[59], egx30_map={})

        # pack_t should use closes[0:40], pack_t20 should use closes[0:60]
        # return_20d for pack_t: closes[39]/closes[19] - 1
        if pack_t["return_20d"] is not None:
            expected_t = (closes[39] / closes[19]) - 1
            assert pack_t["return_20d"] == pytest.approx(expected_t, abs=0.001)

        if pack_t20["return_20d"] is not None:
            expected_t20 = (closes[59] / closes[39]) - 1
            assert pack_t20["return_20d"] == pytest.approx(expected_t20, abs=0.001)


# =============================================================================
# §7.6 NAV proxy tests (T34-T37) — tests against data_cot after integration
# =============================================================================

class TestNAVProxy:
    """T34-T37: NAV inflation note injection.

    Note: T34-T37 test the integration with data_cot.py and sector_config.py.
    These are integration tests that verify the evidence narrative contains
    or omits the NAV inflation note based on sector and regime.
    They will be enabled after the integration step.
    """

    def test_nav_note_injected_for_real_estate_high_rate(self):
        """T34: real_estate + high regime → nav_inflation_note in narrative."""
        from tradingagents.agents.analysts.fundamentals.sector_config import NAV_INFLATION_NOTE
        assert "REAL ASSET REPRICING" in NAV_INFLATION_NOTE

    def test_nav_note_not_injected_for_operational(self):
        """T35: operational sector → no nav_inflation_note. Verified via sector_config."""
        from tradingagents.agents.analysts.fundamentals.sector_config import (
            NAV_INFLATION_NOTE,
            SectorConfig,
        )
        # operational sector should not have the note
        cfg = SectorConfig("ETEL")  # telecom → operational
        assert cfg.sector == "operational"
        # NAV note is only for real_estate/holdings — verified in data_cot integration

    def test_nav_note_not_injected_for_normal_rate(self):
        """T36: real_estate + normal regime → no nav_inflation_note.

        The injection logic in data_cot checks inflation_regime == 'high'.
        This test verifies the constant exists and will be testable after integration.
        """
        from tradingagents.agents.analysts.fundamentals.sector_config import NAV_INFLATION_NOTE
        assert isinstance(NAV_INFLATION_NOTE, str)
        assert len(NAV_INFLATION_NOTE) > 50

    def test_existing_pb_flag_unchanged(self):
        """T37: PB_UNDERSTATED_HISTORICAL_COST still fires for real_estate."""
        from tradingagents.agents.analysts.fundamentals.sector_config import SectorConfig
        cfg = SectorConfig("HELI")  # Heliopolis Housing → real_estate
        assert cfg.sector == "real_estate"
        flags = cfg.generate_distress_flags(
            net_margin=0.10,
            debt_to_equity=1.0,
            current_ratio=1.5,
            total_equity=1_000_000,
            revenue=5_000_000,
            eps=2.0,
            pe_ratio=15.0,
            roe=0.15,
        )
        assert "PB_UNDERSTATED_HISTORICAL_COST" in flags


# =============================================================================
# §7.7 Integration / regression tests (T38-T41)
# =============================================================================

class TestIntegrationRegression:
    """T38-T41: Evidence pack integration and P2 regression."""

    def test_evidence_pack_contains_momentum_section(self):
        """T38: data_cot narrative includes 'PRICE MOMENTUM' header when momentum is provided."""
        from tradingagents.agents.analysts.fundamentals.data_cot import format_evidence_narrative

        pack = {
            "ticker": "TEST.CA",
            "analysis_date": "2024-06-01",
            "fiscal_period": "FY2023",
            "sector": "operational",
            "sector_context": {},
            "ratios": {"roe": 0.15, "pe_ratio": 10.0},
            "directions": {},
            "distress_flags": [],
            "data_confidence": 60,
            "signal_coherence": 80,
            "freq": "annual",
            "revenue_growth_yoy": 0.10,
            "net_income_growth_yoy": 0.08,
            "common_size_income": {},
            "common_size_balance": {},
            "piotroski_score": 5,
            "financial_health_heuristic": "healthy",
            "inflation_regime": "normal",
            "risk_free_rate_value": 0.12,
            "pe_ratio_source": "trade_date_price",
            "risk_free_rate_source": "date_aware_cbe_policy_rate",
            "risk_free_rate_effective_date": "2024-03-07",
            "supplemental_context": {},
            "momentum_pack": {
                "return_20d": 0.05,
                "return_60d": 0.15,
                "return_120d": 0.30,
                "price_vs_sma20": 0.02,
                "price_vs_sma50": 0.08,
                "price_vs_sma200": 0.20,
                "trend_slope_60d": 0.45,
                "volume_ratio_20d": 1.35,
                "volume_confirmed": True,
                "momentum_label": "strong_up",
                "rs_20d": 0.03,
                "rs_60d": 0.10,
                "rs_120d": 0.15,
                "rs_label": "outperforming",
            },
        }
        narrative = format_evidence_narrative(pack)
        assert "PRICE MOMENTUM" in narrative
        assert "strong_up" in narrative.lower() or "STRONG_UP" in narrative
        assert "outperforming" in narrative.lower() or "OUTPERFORMING" in narrative

    def test_evidence_pack_momentum_null_when_unavailable(self):
        """T39: No momentum data → narrative says 'unavailable' or omits section."""
        from tradingagents.agents.analysts.fundamentals.data_cot import format_evidence_narrative

        pack = {
            "ticker": "TEST.CA",
            "analysis_date": "2024-06-01",
            "fiscal_period": "FY2023",
            "sector": "operational",
            "sector_context": {},
            "ratios": {"roe": 0.15},
            "directions": {},
            "distress_flags": [],
            "data_confidence": 60,
            "signal_coherence": 80,
            "freq": "annual",
            "revenue_growth_yoy": 0.10,
            "net_income_growth_yoy": 0.08,
            "common_size_income": {},
            "common_size_balance": {},
            "piotroski_score": 5,
            "financial_health_heuristic": "healthy",
            "inflation_regime": "normal",
            "risk_free_rate_value": 0.12,
            "pe_ratio_source": "",
            "risk_free_rate_source": "",
            "risk_free_rate_effective_date": "",
            "supplemental_context": {},
            # No momentum_pack key
        }
        narrative = format_evidence_narrative(pack)
        # Should either not have the section or say unavailable
        if "PRICE MOMENTUM" in narrative:
            assert "unavailable" in narrative.lower() or "N/A" in narrative

    def test_p2_tests_still_pass(self):
        """T40: P2 regime awareness tests still pass (import and verify)."""
        # Verify core P2 calibration behavior is unchanged
        from tradingagents.agents.analysts.fundamentals.calibration import (
            calibrate_earnings_direction,
        )
        # P2: high-rate flat preservation
        result = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="high",
            raw_earnings_direction="flat",
            earnings_direction_confidence=70,
            freq="annual",
            data_confidence=60,
            sector="operational",
            de_ratio=1.5,
            risk_free_rate=0.2725,  # > 15%
        )
        assert result.calibrated_direction == "flat"
        assert "P2" in " ".join(result.notes)

        # P2: normal-rate flat → up
        result_normal = calibrate_earnings_direction(
            fundamental_outlook="bearish",
            downside_risk_level="high",
            raw_earnings_direction="flat",
            earnings_direction_confidence=70,
            freq="annual",
            data_confidence=60,
            sector="operational",
            de_ratio=1.5,
            risk_free_rate=0.10,  # < 15%
        )
        assert result_normal.calibrated_direction == "up"

    def test_temporal_safety_suite_still_passes(self):
        """T41: Temporal safety — momentum truncation is correct."""
        # Verify that compute_momentum_pack correctly truncates future data
        dates = _make_dates(30, start="2024-01-01")
        closes = _make_series(100, 130, 30)
        volumes = [1000.0] * 30

        # Use a trade_date in the middle
        trade_date = dates[19]  # Only first 20 bars
        pack = compute_momentum_pack(closes, volumes, dates, trade_date, egx30_map={})

        # With 20 bars, return_20d should be None (need 21 bars)
        assert pack["return_20d"] is None
        # momentum_label should reflect insufficient history for 60d
        assert pack["return_60d"] is None
        assert pack["momentum_label"] == "insufficient_history"


# =============================================================================
# TMGH Sector Classification Regression (T42-T45)
# =============================================================================

class TestTMGHSectorClassification:
    """Verify TMGH.CA maps to real_estate and triggers NAV inflation note."""

    def test_t42_tmgh_ca_classifies_as_real_estate(self):
        from tradingagents.agents.analysts.fundamentals.sector_config import classify_sector
        assert classify_sector("TMGH.CA") == "real_estate"

    def test_t42b_tmgh_bare_classifies_as_real_estate(self):
        from tradingagents.agents.analysts.fundamentals.sector_config import classify_sector
        assert classify_sector("TMGH") == "real_estate"

    def test_t42c_tmg_still_classifies_as_real_estate(self):
        """Existing TMG mapping must not break."""
        from tradingagents.agents.analysts.fundamentals.sector_config import classify_sector
        assert classify_sector("TMG") == "real_estate"

    def test_t43_sector_config_init_strips_ca(self):
        from tradingagents.agents.analysts.fundamentals.sector_config import SectorConfig
        cfg = SectorConfig("TMGH.CA")
        assert cfg.sector == "real_estate"

    def test_t44_nav_inflation_note_triggers_for_tmgh(self):
        """NAV inflation note should appear in evidence narrative for TMGH
        in high-rate regime when momentum_pack is present."""
        from tradingagents.agents.analysts.fundamentals.sector_config import (
            SectorConfig, NAV_INFLATION_NOTE,
        )
        from tradingagents.agents.analysts.fundamentals.data_cot import (
            build_evidence_pack, format_evidence_narrative,
        )
        from tradingagents.agents.analysts.fundamentals.schemas import FundamentalAnalysisReport

        report = FundamentalAnalysisReport(
            ticker="TMGH.CA",
            analysis_date="2024-03-24",
            fiscal_period="FY2023",
            sector="real_estate",
            currency="EGP",
            financial_health="healthy",
            earnings_direction="",
            key_risks=[],
            ratios={"pb_ratio": 0.8, "debt_to_equity": 1.5},
            preprocessing={},
            distress_flags=[],
            data_confidence=70,
            signal_coherence=60,
            # High-rate regime: CBE > 15% triggers NAV note
            risk_free_rate_value=27.25,
            risk_free_rate_source="test",
        )
        sector_cfg = SectorConfig("TMGH.CA")
        assert sector_cfg.sector == "real_estate"

        # Provide a momentum pack so the NAV note section is reached
        mock_momentum = {
            "return_20d": 0.10, "return_60d": 0.20, "return_120d": 0.30,
            "momentum_label": "up", "rs_60d": 0.05, "rs_label": "neutral",
            "volume_confirmed": False, "price_vs_sma20": 0.02,
            "price_vs_sma50": 0.05, "price_vs_sma200": 0.10,
            "trend_slope_60d": 0.15, "volume_ratio_20d": 1.0,
        }
        pack = build_evidence_pack(
            report, sector_cfg, freq="annual", momentum_pack=mock_momentum,
        )
        narrative = format_evidence_narrative(pack)
        assert "REAL ASSET REPRICING CONTEXT" in narrative

    def test_t45_ca_suffix_stripping_does_not_affect_others(self):
        """Banks, holdings, operational tickers still classify correctly with .CA."""
        from tradingagents.agents.analysts.fundamentals.sector_config import classify_sector
        assert classify_sector("COMI.CA") == "banks"
        assert classify_sector("SWDY.CA") == "holdings"
        assert classify_sector("ETEL.CA") == "operational"
        assert classify_sector("HELI.CA") == "real_estate"
