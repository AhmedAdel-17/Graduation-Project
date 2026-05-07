"""Regression tests for scripts/test_sentiment_pipeline.py — the EGX Sentiment harness.

Coverage:
  - All 5 scenario data builders produce correct structure and typed DataPoint objects
  - positive scenario: all 4 layers produce SIGNAL
  - no_signal scenario: all 4 layers produce NO_SIGNAL with expected gate failures
  - conflict scenario: Layer C sets contradicts_market=True; Layer A emits SIGNAL
  - low_liquidity scenario: SMALL tier; Layer C passes with minimum evidence
  - multilingual scenario: runs without error; Layer A0 passes Gate 1 (TIER2 sources)
  - build_report() produces correct top-level JSON structure
  - load_from_pipeline_file() returns None gracefully on missing file
  - load_from_pipeline_file() returns None gracefully on malformed JSON
  - load_from_pipeline_file() loads valid pipeline JSON and routes posts correctly
  - _parse_ts() handles all 6 supported timestamp formats
  - _parse_ts() returns None for unparseable strings
  - _iso_ago() returns a UTC-aware ISO-8601 string the right distance in the past
  - SCENARIOS registry maps to exactly the 5 builder functions
  - SCENARIO_DEFAULT_TICKER provides a ticker for every scenario key
  - blend multipliers: positive scenario confidence × > 0 and ≤ 1.0
  - blend multipliers: no_signal scenario uses pass-through ×1.0

All tests are deterministic — reference time is fixed at module level.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Make sure scripts/ and project root are on sys.path
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# ─────────────────────────────────────────────────────────────────────────────
# Import the harness under test (lazy — avoids stdout noise at collection time)
# ─────────────────────────────────────────────────────────────────────────────

import importlib

_harness = importlib.import_module("test_sentiment_pipeline")

build_positive_signal_data = _harness.build_positive_signal_data
build_no_signal_data       = _harness.build_no_signal_data
build_conflict_data        = _harness.build_conflict_data
build_low_liquidity_data   = _harness.build_low_liquidity_data
build_multilingual_data    = _harness.build_multilingual_data
load_from_pipeline_file    = _harness.load_from_pipeline_file
build_report               = _harness.build_report
SCENARIOS                  = _harness.SCENARIOS
SCENARIO_DEFAULT_TICKER    = _harness.SCENARIO_DEFAULT_TICKER
_parse_ts                  = _harness._parse_ts
_iso_ago                   = _harness._iso_ago

# Layer runners (used in integration-style tests)
run_layer_a0 = _harness.run_layer_a0
run_layer_a  = _harness.run_layer_a
run_layer_b  = _harness.run_layer_b
run_layer_c  = _harness.run_layer_c
run_layer_e  = _harness.run_layer_e

# ─────────────────────────────────────────────────────────────────────────────
# Shared reference time (fixed for determinism)
# ─────────────────────────────────────────────────────────────────────────────

REF = datetime(2026, 5, 1, 14, 0, 0, tzinfo=timezone.utc)

# ─────────────────────────────────────────────────────────────────────────────
# DataPoint type imports (for isinstance assertions)
# ─────────────────────────────────────────────────────────────────────────────

from tradingagents.sentiment.macro  import MacroDataPoint
from tradingagents.sentiment.market import MarketDataPoint
from tradingagents.sentiment.sector import SectorDataPoint
from tradingagents.sentiment.stock  import StockDataPoint


# ═════════════════════════════════════════════════════════════════════════════
# 1. Helper utilities
# ═════════════════════════════════════════════════════════════════════════════

class TestParseTs:
    """_parse_ts should handle all documented timestamp formats."""

    @pytest.mark.parametrize("ts", [
        "2026-05-01T14:00:00+00:00",
        "2026-05-01T14:00:00.000000+00:00",
        "2026-05-01T14:00:00Z",
        "2026-05-01T14:00:00.000000Z",
        "2026-05-01 14:00:00",
        "2026-05-01",
    ])
    def test_supported_formats_return_datetime(self, ts: str) -> None:
        result = _parse_ts(ts)
        assert isinstance(result, datetime), f"Expected datetime for {ts!r}, got {result}"

    def test_parsed_result_is_utc_aware(self) -> None:
        result = _parse_ts("2026-05-01 14:00:00")
        assert result is not None
        assert result.tzinfo is not None

    def test_unparseable_returns_none(self) -> None:
        assert _parse_ts("not-a-date") is None
        assert _parse_ts("") is None
        assert _parse_ts("01/05/2026") is None  # dd/mm/yyyy not supported


class TestIsoAgo:
    """_iso_ago should return an ISO string the correct number of hours before ref."""

    def test_zero_hours_ago(self) -> None:
        s = _iso_ago(REF, 0)
        dt = _parse_ts(s)
        assert dt is not None
        assert abs((dt - REF).total_seconds()) < 1

    def test_24_hours_ago(self) -> None:
        s = _iso_ago(REF, 24)
        dt = _parse_ts(s)
        assert dt is not None
        delta = REF - dt
        assert abs(delta.total_seconds() - 86400) < 2

    def test_fractional_hours(self) -> None:
        s = _iso_ago(REF, 0.5)
        dt = _parse_ts(s)
        assert dt is not None
        delta = REF - dt
        assert abs(delta.total_seconds() - 1800) < 2


# ═════════════════════════════════════════════════════════════════════════════
# 2. Scenario data builders — structure and type checks
# ═════════════════════════════════════════════════════════════════════════════

_REQUIRED_KEYS = {"macro", "market", "sector", "stock"}


class TestPositiveSignalDataBuilder:
    def setup_method(self) -> None:
        self.data = build_positive_signal_data("COMI.CA", REF)

    def test_returns_all_required_keys(self) -> None:
        assert set(self.data.keys()) == _REQUIRED_KEYS

    def test_all_values_are_lists(self) -> None:
        for key in _REQUIRED_KEYS:
            assert isinstance(self.data[key], list), f"{key} should be a list"

    def test_macro_posts_are_macro_data_points(self) -> None:
        assert len(self.data["macro"]) >= 2
        for p in self.data["macro"]:
            assert isinstance(p, MacroDataPoint), f"Expected MacroDataPoint, got {type(p)}"

    def test_market_posts_are_market_data_points(self) -> None:
        assert len(self.data["market"]) >= 50
        for p in self.data["market"][:5]:
            assert isinstance(p, MarketDataPoint)

    def test_sector_posts_are_sector_data_points(self) -> None:
        assert len(self.data["sector"]) >= 10
        for p in self.data["sector"][:5]:
            assert isinstance(p, SectorDataPoint)

    def test_stock_posts_contain_spam_and_clean(self) -> None:
        spam = [p for p in self.data["stock"] if p.is_spam_promo]
        clean = [p for p in self.data["stock"] if not p.is_spam_promo]
        assert len(spam) >= 1, "Should have at least 1 spam post"
        assert len(clean) >= 8, "Should have at least 8 clean posts"


class TestNoSignalDataBuilder:
    def setup_method(self) -> None:
        self.data = build_no_signal_data("EFID.CA", REF)

    def test_returns_all_required_keys(self) -> None:
        assert set(self.data.keys()) == _REQUIRED_KEYS

    def test_macro_has_low_credibility_source(self) -> None:
        # The no_signal macro post should come from a non-credible domain
        assert len(self.data["macro"]) >= 1
        domains = [p.source_domain for p in self.data["macro"]]
        assert not any(d in ("cbe.org.eg", "reuters.com", "bloomberg.com") for d in domains)

    def test_market_has_fewer_than_50_posts(self) -> None:
        assert len(self.data["market"]) < 50

    def test_sector_has_fewer_than_10_posts(self) -> None:
        assert len(self.data["sector"]) < 10

    def test_stock_is_empty(self) -> None:
        assert len(self.data["stock"]) == 0


class TestConflictDataBuilder:
    def setup_method(self) -> None:
        self.data = build_conflict_data("TMGH.CA", REF)

    def test_returns_all_required_keys(self) -> None:
        assert set(self.data.keys()) == _REQUIRED_KEYS

    def test_macro_posts_are_risk_off(self) -> None:
        assert len(self.data["macro"]) >= 2
        for p in self.data["macro"]:
            assert p.direction == "RISK_OFF"

    def test_market_posts_are_bullish(self) -> None:
        scores = [p.sentiment_score for p in self.data["market"]]
        assert sum(scores) / len(scores) > 0, "Mean market score should be positive"

    def test_stock_posts_are_bearish(self) -> None:
        scores = [p.sentiment_score for p in self.data["stock"]]
        assert sum(scores) / len(scores) < 0, "Mean stock score should be negative"

    def test_market_has_at_least_50_posts(self) -> None:
        assert len(self.data["market"]) >= 50


class TestLowLiquidityDataBuilder:
    def setup_method(self) -> None:
        self.data = build_low_liquidity_data("DOMT.CA", REF)

    def test_returns_all_required_keys(self) -> None:
        assert set(self.data.keys()) == _REQUIRED_KEYS

    def test_macro_posts_empty(self) -> None:
        assert len(self.data["macro"]) == 0

    def test_stock_posts_meet_mid_tier_gates(self) -> None:
        clean = [p for p in self.data["stock"] if not p.is_spam_promo]
        strong = [p for p in clean if p.entity_confidence >= 0.85]
        authors = {p.author for p in clean}
        platforms = {p.platform for p in clean}
        assert len(strong) >= 5, "Should have ≥5 strong mentions for MID tier"
        assert len(authors) >= 3, "Should have ≥3 distinct authors"
        assert len(platforms) >= 2, "Should have ≥2 distinct platforms"

    def test_stock_posts_are_typed_correctly(self) -> None:
        for p in self.data["stock"]:
            assert isinstance(p, StockDataPoint)
            assert isinstance(p.is_spam_promo, bool)


class TestMultilingualDataBuilder:
    def setup_method(self) -> None:
        self.data = build_multilingual_data("ETEL.CA", REF)

    def test_returns_all_required_keys(self) -> None:
        assert set(self.data.keys()) == _REQUIRED_KEYS

    def test_macro_posts_use_tier2_sources(self) -> None:
        # Both sources in multilingual scenario are TIER2
        domains = {p.source_domain for p in self.data["macro"]}
        tier2 = {"almalnews.com", "enterprise.press", "alborsaanews.com", "dailynewsegypt.com"}
        assert domains & tier2, "At least one TIER2 domain expected in multilingual macro"

    def test_arabic_author_names_in_stock(self) -> None:
        authors = [p.author for p in self.data["stock"]]
        arabic_authors = [a for a in authors if any(ord(c) > 0x600 for c in a)]
        assert len(arabic_authors) >= 3, "Expected Arabic author names in multilingual scenario"

    def test_stock_posts_are_typed_correctly(self) -> None:
        for p in self.data["stock"]:
            assert isinstance(p, StockDataPoint)


# ═════════════════════════════════════════════════════════════════════════════
# 3. Scenario registry consistency
# ═════════════════════════════════════════════════════════════════════════════

class TestScenarioRegistry:
    _EXPECTED_SCENARIOS = {"positive", "no_signal", "conflict", "low_liquidity", "multilingual"}

    def test_all_expected_scenarios_present(self) -> None:
        assert set(SCENARIOS.keys()) == self._EXPECTED_SCENARIOS

    def test_all_scenarios_have_default_ticker(self) -> None:
        for name in self._EXPECTED_SCENARIOS:
            assert name in SCENARIO_DEFAULT_TICKER, f"Missing default ticker for {name!r}"
            assert SCENARIO_DEFAULT_TICKER[name].endswith(".CA"), (
                f"Default ticker for {name!r} should end with .CA"
            )

    def test_all_scenario_builders_are_callable(self) -> None:
        for name, fn in SCENARIOS.items():
            assert callable(fn), f"SCENARIOS[{name!r}] should be callable"

    def test_each_builder_runs_without_error(self) -> None:
        for name, fn in SCENARIOS.items():
            ticker = SCENARIO_DEFAULT_TICKER[name]
            data = fn(ticker, REF)
            assert isinstance(data, dict), f"Builder for {name!r} should return a dict"
            assert _REQUIRED_KEYS == set(data.keys()), (
                f"Builder for {name!r} missing required keys"
            )


# ═════════════════════════════════════════════════════════════════════════════
# 4. Layer integration tests — layer runners called directly
#    (suppress stdout because runners are display-heavy)
# ═════════════════════════════════════════════════════════════════════════════

class TestPositiveScenarioLayers:
    """All layers should emit SIGNAL for the positive scenario."""

    @pytest.fixture(autouse=True)
    def _setup(self, capsys) -> None:
        data = build_positive_signal_data("COMI.CA", REF)
        self.macro_posts  = data["macro"]
        self.market_posts = data["market"]
        self.sector_posts = data["sector"]
        self.stock_posts  = data["stock"]
        # capsys suppresses the display helpers' stdout during tests
        self._capsys = capsys

    def test_layer_a0_emits_signal(self) -> None:
        result = run_layer_a0(self.macro_posts, REF, verbose=False)
        # MacroSentiment uses composite_regime, not .status
        assert result.composite_regime.value != "NO_SIGNAL", (
            f"Expected SIGNAL, got NO_SIGNAL. Reason: "
            f"{getattr(result, 'reason', 'unknown')}"
        )

    def test_layer_a_emits_signal(self) -> None:
        result = run_layer_a(self.market_posts, REF, verbose=False)
        assert result.status.value == "SIGNAL"

    def test_layer_a_score_is_positive(self) -> None:
        result = run_layer_a(self.market_posts, REF, verbose=False)
        if result.status.value == "SIGNAL":
            assert result.score is not None
            assert result.score > 0, "Positive scenario should have bullish market score"

    def test_layer_b_emits_signal(self) -> None:
        from tradingagents.sentiment.taxonomy import SectorEnum
        result = run_layer_b(SectorEnum.BANKS, self.sector_posts, verbose=False)
        assert result.status.value == "SIGNAL"

    def test_layer_c_emits_signal(self) -> None:
        result = run_layer_c("COMI.CA", self.stock_posts, REF,
                             market_score=0.30, verbose=False)
        assert result.status.value == "SIGNAL"

    def test_layer_c_no_contradiction_in_positive_scenario(self) -> None:
        result = run_layer_c("COMI.CA", self.stock_posts, REF,
                             market_score=0.30, verbose=False)
        if result.status.value == "SIGNAL":
            assert not result.contradicts_market

    def test_layer_e_confidence_multiplier_in_range(self) -> None:
        from tradingagents.sentiment.taxonomy import SectorEnum
        macro_r  = run_layer_a0(self.macro_posts, REF)
        market_r = run_layer_a(self.market_posts, REF)
        sector_r = run_layer_b(SectorEnum.BANKS, self.sector_posts)
        blend    = run_layer_e(macro_r, market_r, sector_r)
        assert 0.0 < blend.confidence_multiplier <= 1.0

    def test_layer_e_position_size_multiplier_in_range(self) -> None:
        from tradingagents.sentiment.taxonomy import SectorEnum
        macro_r  = run_layer_a0(self.macro_posts, REF)
        market_r = run_layer_a(self.market_posts, REF)
        sector_r = run_layer_b(SectorEnum.BANKS, self.sector_posts)
        blend    = run_layer_e(macro_r, market_r, sector_r)
        assert 0.0 < blend.position_size_multiplier <= 1.0

    def test_layer_e_audit_string_is_non_empty(self) -> None:
        from tradingagents.sentiment.taxonomy import SectorEnum
        macro_r  = run_layer_a0(self.macro_posts, REF)
        market_r = run_layer_a(self.market_posts, REF)
        sector_r = run_layer_b(SectorEnum.BANKS, self.sector_posts)
        blend    = run_layer_e(macro_r, market_r, sector_r)
        assert isinstance(blend.audit, str) and len(blend.audit) > 0


class TestNoSignalScenarioLayers:
    """All layers should emit NO_SIGNAL for the no_signal scenario."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        data = build_no_signal_data("EFID.CA", REF)
        self.macro_posts  = data["macro"]
        self.market_posts = data["market"]
        self.sector_posts = data["sector"]
        self.stock_posts  = data["stock"]

    def test_layer_a0_emits_no_signal(self) -> None:
        result = run_layer_a0(self.macro_posts, REF)
        assert result.composite_regime.value == "NO_SIGNAL"

    def test_layer_a0_fails_source_credibility_gate(self) -> None:
        result = run_layer_a0(self.macro_posts, REF)
        assert result.composite_regime.value == "NO_SIGNAL"
        assert result.reason.gate_failed == "macro.source_credibility"

    def test_layer_a_emits_no_signal(self) -> None:
        result = run_layer_a(self.market_posts, REF)
        assert result.status.value == "NO_SIGNAL"

    def test_layer_a_fails_volume_gate(self) -> None:
        result = run_layer_a(self.market_posts, REF)
        assert result.reason.gate_failed == "market.n_total_posts"

    def test_layer_b_emits_no_signal(self) -> None:
        from tradingagents.sentiment.taxonomy import SectorEnum
        result = run_layer_b(SectorEnum.INDUSTRY, self.sector_posts)
        assert result.status.value == "NO_SIGNAL"

    def test_layer_c_emits_no_signal(self) -> None:
        result = run_layer_c("EFID.CA", self.stock_posts, REF)
        assert result.status.value == "NO_SIGNAL"

    def test_layer_c_fails_strong_mentions_gate(self) -> None:
        result = run_layer_c("EFID.CA", self.stock_posts, REF)
        assert result.reason.gate_failed == "stock.n_strong_mentions"

    def test_layer_e_pass_through_when_all_no_signal(self) -> None:
        from tradingagents.sentiment.taxonomy import SectorEnum
        macro_r  = run_layer_a0(self.macro_posts, REF)
        market_r = run_layer_a(self.market_posts, REF)
        sector_r = run_layer_b(SectorEnum.INDUSTRY, self.sector_posts)
        blend    = run_layer_e(macro_r, market_r, sector_r)
        # All NO_SIGNAL → pass-through multipliers
        assert blend.confidence_multiplier == pytest.approx(1.0, abs=0.01)
        assert blend.position_size_multiplier == pytest.approx(1.0, abs=0.01)

    def test_no_signal_reason_has_gate_and_readable_message(self) -> None:
        result = run_layer_a(self.market_posts, REF)
        assert result.reason is not None
        assert result.reason.gate_failed
        assert result.reason.human_readable


class TestConflictScenarioLayers:
    """Layer C should flag contradicts_market=True; market should be SIGNAL."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        data = build_conflict_data("TMGH.CA", REF)
        self.macro_posts  = data["macro"]
        self.market_posts = data["market"]
        self.sector_posts = data["sector"]
        self.stock_posts  = data["stock"]

    def test_layer_a_emits_signal(self) -> None:
        result = run_layer_a(self.market_posts, REF)
        assert result.status.value == "SIGNAL"

    def test_layer_a_score_is_positive(self) -> None:
        result = run_layer_a(self.market_posts, REF)
        if result.status.value == "SIGNAL":
            assert result.score > 0

    def test_layer_c_emits_signal(self) -> None:
        result = run_layer_c("TMGH.CA", self.stock_posts, REF, market_score=0.30)
        assert result.status.value == "SIGNAL", (
            f"Expected SIGNAL but got NO_SIGNAL: {result.reason}"
        )

    def test_layer_c_contradicts_market(self) -> None:
        result = run_layer_c("TMGH.CA", self.stock_posts, REF, market_score=0.30)
        if result.status.value == "SIGNAL":
            assert result.contradicts_market, (
                "Stock is bearish while market is bullish — contradicts_market must be True"
            )

    def test_layer_a0_emits_signal_with_risk_off(self) -> None:
        result = run_layer_a0(self.macro_posts, REF)
        assert result.composite_regime.value != "NO_SIGNAL"
        assert result.composite_regime.value == "RISK_OFF"


class TestLowLiquidityScenarioLayers:
    """SMALL tier (DOMT.CA) should pass Layer C with minimum evidence."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        data = build_low_liquidity_data("DOMT.CA", REF)
        self.macro_posts  = data["macro"]
        self.market_posts = data["market"]
        self.sector_posts = data["sector"]
        self.stock_posts  = data["stock"]

    def test_domt_is_mid_tier(self) -> None:
        from tradingagents.sentiment.liquidity_tiers import tier_for, LiquidityTier
        tier = tier_for("DOMT.CA")
        assert tier == LiquidityTier.MID

    def test_layer_a0_no_signal_no_macro_posts(self) -> None:
        result = run_layer_a0(self.macro_posts, REF)
        assert result.composite_regime.value == "NO_SIGNAL"

    def test_layer_a_emits_signal(self) -> None:
        result = run_layer_a(self.market_posts, REF)
        assert result.status.value == "SIGNAL"

    def test_layer_c_passes_at_mid_tier(self) -> None:
        result = run_layer_c("DOMT.CA", self.stock_posts, REF)
        assert result.status.value == "SIGNAL", (
            f"MID tier should pass. Got NO_SIGNAL: {getattr(result, 'reason', None)}"
        )

    def test_layer_c_result_has_correct_tier(self) -> None:
        result = run_layer_c("DOMT.CA", self.stock_posts, REF)
        if result.status.value == "SIGNAL":
            assert result.tier == "MID"


class TestMultilingualScenarioLayers:
    """Multilingual scenario should run without errors."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        data = build_multilingual_data("ETEL.CA", REF)
        self.macro_posts  = data["macro"]
        self.market_posts = data["market"]
        self.sector_posts = data["sector"]
        self.stock_posts  = data["stock"]

    def test_layer_a0_runs_without_error(self) -> None:
        result = run_layer_a0(self.macro_posts, REF)
        assert result is not None

    def test_layer_a0_passes_with_tier2_sources(self) -> None:
        result = run_layer_a0(self.macro_posts, REF)
        # Both macro posts are TIER2 but from 2 distinct domains — Gate 1 & 2 pass
        assert result.composite_regime.value != "NO_SIGNAL", (
            f"TIER2 sources should satisfy Gate 1 credibility. "
            f"Gate failed: {getattr(result.reason, 'gate_failed', None)}"
        )

    def test_layer_a_runs_without_error(self) -> None:
        result = run_layer_a(self.market_posts, REF)
        assert result is not None

    def test_layer_a_emits_signal(self) -> None:
        result = run_layer_a(self.market_posts, REF)
        assert result.status.value == "SIGNAL"

    def test_layer_c_handles_arabic_author_names(self) -> None:
        result = run_layer_c("ETEL.CA", self.stock_posts, REF)
        assert result is not None  # must not raise on Arabic string authors

    def test_layer_c_emits_signal_for_etel(self) -> None:
        result = run_layer_c("ETEL.CA", self.stock_posts, REF)
        assert result.status.value == "SIGNAL", (
            f"ETEL (MEGA tier) should pass with ≥8 strong mentions + 3 sources. "
            f"Got: {getattr(result, 'reason', None)}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 5. build_report() — JSON structure contract
# ═════════════════════════════════════════════════════════════════════════════

class TestBuildReport:
    """build_report() must return the documented JSON structure."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        from tradingagents.sentiment.taxonomy import SectorEnum
        data = build_positive_signal_data("COMI.CA", REF)

        macro_r  = run_layer_a0(data["macro"], REF)
        market_r = run_layer_a(data["market"], REF)
        sector_r = run_layer_b(SectorEnum.BANKS, data["sector"])
        stock_r  = run_layer_c("COMI.CA", data["stock"], REF, market_score=0.30)
        blend    = run_layer_e(macro_r, market_r, sector_r)

        self.report = build_report(
            ticker="COMI.CA",
            date_str="2026-05-01",
            scenario="positive",
            macro_result=macro_r,
            market_result=market_r,
            sector_result=sector_r,
            stock_result=stock_r,
            blend=blend,
        )

    def test_top_level_keys_present(self) -> None:
        expected = {"ticker", "date", "scenario", "tier", "run_at", "layers", "blend_e", "summary"}
        assert set(self.report.keys()) >= expected

    def test_ticker_matches(self) -> None:
        assert self.report["ticker"] == "COMI.CA"

    def test_date_matches(self) -> None:
        assert self.report["date"] == "2026-05-01"

    def test_scenario_matches(self) -> None:
        assert self.report["scenario"] == "positive"

    def test_tier_is_mega_for_comi(self) -> None:
        assert self.report["tier"] == "MEGA"

    def test_layers_has_four_entries(self) -> None:
        layers = self.report["layers"]
        assert set(layers.keys()) == {"macro_a0", "market_a", "sector_b", "stock_c"}

    def test_each_layer_has_status(self) -> None:
        for layer_key, layer_data in self.report["layers"].items():
            assert "status" in layer_data, f"{layer_key} missing 'status'"
            assert "is_no_signal" in layer_data, f"{layer_key} missing 'is_no_signal'"

    def test_blend_e_has_required_fields(self) -> None:
        blend_e = self.report["blend_e"]
        assert "confidence_multiplier" in blend_e
        assert "position_size_multiplier" in blend_e
        assert "audit" in blend_e

    def test_blend_multipliers_are_floats(self) -> None:
        blend_e = self.report["blend_e"]
        assert isinstance(blend_e["confidence_multiplier"], float)
        assert isinstance(blend_e["position_size_multiplier"], float)

    def test_summary_counts_are_non_negative(self) -> None:
        summary = self.report["summary"]
        assert summary["signal_layers"] >= 0
        assert summary["no_signal_layers"] >= 0
        assert summary["signal_layers"] + summary["no_signal_layers"] == 4

    def test_report_is_json_serialisable(self) -> None:
        serialized = json.dumps(self.report, default=str)
        parsed = json.loads(serialized)
        assert parsed["ticker"] == "COMI.CA"

    def test_positive_scenario_has_all_signal_layers(self) -> None:
        assert self.report["summary"]["no_signal_layers"] == 0


class TestBuildReportNoSignal:
    """build_report() for no_signal scenario should have all 4 layers flagged."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        from tradingagents.sentiment.taxonomy import SectorEnum
        data = build_no_signal_data("EFID.CA", REF)

        macro_r  = run_layer_a0(data["macro"], REF)
        market_r = run_layer_a(data["market"], REF)
        sector_r = run_layer_b(SectorEnum.FOOD_BEV, data["sector"])
        stock_r  = run_layer_c("EFID.CA", data["stock"], REF)
        blend    = run_layer_e(macro_r, market_r, sector_r)

        self.report = build_report(
            ticker="EFID.CA",
            date_str="2026-05-01",
            scenario="no_signal",
            macro_result=macro_r,
            market_result=market_r,
            sector_result=sector_r,
            stock_result=stock_r,
            blend=blend,
        )

    def test_all_four_layers_are_no_signal(self) -> None:
        assert self.report["summary"]["no_signal_layers"] == 4

    def test_is_no_signal_true_for_all_layers(self) -> None:
        for layer_key, layer_data in self.report["layers"].items():
            assert layer_data["is_no_signal"] is True, (
                f"{layer_key}: expected is_no_signal=True"
            )

    def test_each_no_signal_layer_has_gate_failed(self) -> None:
        for layer_key, layer_data in self.report["layers"].items():
            assert "gate_failed" in layer_data, (
                f"{layer_key}: NO_SIGNAL layer should include 'gate_failed'"
            )


# ═════════════════════════════════════════════════════════════════════════════
# 6. load_from_pipeline_file() — file loading robustness
# ═════════════════════════════════════════════════════════════════════════════

class TestLoadFromPipelineFile:
    """load_from_pipeline_file() must handle edge cases without raising."""

    def test_missing_file_returns_none(self) -> None:
        result = load_from_pipeline_file(
            filepath="/does/not/exist/results.json",
            ticker="COMI.CA",
            ref=REF,
        )
        assert result is None

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{ this is not valid json", encoding="utf-8")
        result = load_from_pipeline_file(
            filepath=str(bad_file),
            ticker="COMI.CA",
            ref=REF,
        )
        assert result is None

    def test_empty_items_list_returns_none(self, tmp_path: Path) -> None:
        empty_file = tmp_path / "empty.json"
        empty_file.write_text(json.dumps({"items": []}), encoding="utf-8")
        result = load_from_pipeline_file(
            filepath=str(empty_file),
            ticker="COMI.CA",
            ref=REF,
        )
        assert result is None

    def test_valid_file_returns_dict_with_required_keys(self, tmp_path: Path) -> None:
        payload = {
            "items": [
                {
                    "platform": "facebook",
                    "timestamp": _iso_ago(REF, 5),
                    "sentiment": {"score": 0.25},
                    "weight": 0.80,
                    "mentions": [{"symbol": "EGX_30", "confidence": 0.80}],
                    "is_spam_promo": False,
                },
                {
                    "platform": "telegram",
                    "timestamp": _iso_ago(REF, 10),
                    "sentiment": {"score": 0.30},
                    "weight": 0.75,
                    "mentions": [{"symbol": "COMI", "confidence": 0.90}],
                    "author": "user_001",
                    "is_spam_promo": False,
                },
            ]
        }
        valid_file = tmp_path / "valid.json"
        valid_file.write_text(json.dumps(payload), encoding="utf-8")

        result = load_from_pipeline_file(
            filepath=str(valid_file),
            ticker="COMI.CA",
            ref=REF,
        )
        assert result is not None
        assert set(result.keys()) == {"macro", "market", "sector", "stock"}
        assert isinstance(result["market"], list)
        assert isinstance(result["stock"], list)

    def test_valid_file_routes_ticker_to_stock(self, tmp_path: Path) -> None:
        payload = {
            "items": [
                {
                    "platform": "facebook",
                    "timestamp": _iso_ago(REF, 3),
                    "sentiment": {"score": 0.40},
                    "weight": 0.85,
                    "mentions": [{"symbol": "COMI", "confidence": 0.92}],
                    "author": "investor_x",
                    "is_spam_promo": False,
                }
            ]
        }
        f = tmp_path / "comi.json"
        f.write_text(json.dumps(payload), encoding="utf-8")

        result = load_from_pipeline_file(str(f), "COMI.CA", REF)
        assert result is not None
        assert len(result["stock"]) >= 1
        for p in result["stock"]:
            assert isinstance(p, StockDataPoint)

    def test_valid_file_stock_entity_confidence_preserved(self, tmp_path: Path) -> None:
        payload = {
            "items": [
                {
                    "platform": "telegram",
                    "timestamp": _iso_ago(REF, 2),
                    "sentiment": {"score": 0.20},
                    "weight": 0.70,
                    "mentions": [{"symbol": "ETEL", "confidence": 0.88}],
                    "author": "user_ar",
                    "is_spam_promo": False,
                }
            ]
        }
        f = tmp_path / "etel.json"
        f.write_text(json.dumps(payload), encoding="utf-8")

        result = load_from_pipeline_file(str(f), "ETEL.CA", REF)
        assert result is not None
        if result["stock"]:
            assert result["stock"][0].entity_confidence == pytest.approx(0.88, abs=0.001)

    def test_ticker_with_ca_suffix_matched_correctly(self, tmp_path: Path) -> None:
        payload = {
            "items": [
                {
                    "platform": "facebook",
                    "timestamp": _iso_ago(REF, 1),
                    "sentiment": {"score": 0.15},
                    "weight": 0.60,
                    "mentions": [{"symbol": "TMGH", "confidence": 0.87}],
                    "author": "user_002",
                    "is_spam_promo": False,
                }
            ]
        }
        f = tmp_path / "tmgh.json"
        f.write_text(json.dumps(payload), encoding="utf-8")

        result = load_from_pipeline_file(str(f), "TMGH.CA", REF)
        assert result is not None
        assert len(result["stock"]) >= 1


# ═════════════════════════════════════════════════════════════════════════════
# 7. Liquidity tier contract
# ═════════════════════════════════════════════════════════════════════════════

class TestLiquidityTierForScenarioTickers:
    """Each scenario default ticker should map to the expected liquidity tier."""

    def test_comi_is_mega(self) -> None:
        from tradingagents.sentiment.liquidity_tiers import tier_for, LiquidityTier
        assert tier_for("COMI.CA") == LiquidityTier.MEGA

    def test_tmgh_is_mega(self) -> None:
        from tradingagents.sentiment.liquidity_tiers import tier_for, LiquidityTier
        assert tier_for("TMGH.CA") == LiquidityTier.MEGA

    def test_etel_is_mega(self) -> None:
        from tradingagents.sentiment.liquidity_tiers import tier_for, LiquidityTier
        assert tier_for("ETEL.CA") == LiquidityTier.MEGA

    def test_domt_is_mid(self) -> None:
        from tradingagents.sentiment.liquidity_tiers import tier_for, LiquidityTier
        assert tier_for("DOMT.CA") == LiquidityTier.MID

    def test_efid_is_mid(self) -> None:
        from tradingagents.sentiment.liquidity_tiers import tier_for, LiquidityTier
        assert tier_for("EFID.CA") == LiquidityTier.MID

    def test_unknown_ticker_is_small(self) -> None:
        from tradingagents.sentiment.liquidity_tiers import tier_for, LiquidityTier
        assert tier_for("UNKN.CA") == LiquidityTier.SMALL


# ═════════════════════════════════════════════════════════════════════════════
# 8. Blend multipliers correctness
# ═════════════════════════════════════════════════════════════════════════════

class TestBlendMultipliersContract:
    """Blend multipliers must satisfy the invariants documented in PR 7."""

    def _blend_for(self, scenario_fn: Any, ticker: str, sector_enum: Any) -> Any:
        data = scenario_fn(ticker, REF)
        macro_r  = run_layer_a0(data["macro"], REF)
        market_r = run_layer_a(data["market"], REF)
        sector_r = run_layer_b(sector_enum, data["sector"])
        return run_layer_e(macro_r, market_r, sector_r)

    def test_confidence_multiplier_never_exceeds_one(self) -> None:
        from tradingagents.sentiment.taxonomy import SectorEnum
        blend = self._blend_for(build_positive_signal_data, "COMI.CA", SectorEnum.BANKS)
        assert blend.confidence_multiplier <= 1.0

    def test_position_size_multiplier_never_exceeds_one(self) -> None:
        from tradingagents.sentiment.taxonomy import SectorEnum
        blend = self._blend_for(build_positive_signal_data, "COMI.CA", SectorEnum.BANKS)
        assert blend.position_size_multiplier <= 1.0

    def test_no_signal_scenario_pass_through(self) -> None:
        from tradingagents.sentiment.taxonomy import SectorEnum
        blend = self._blend_for(build_no_signal_data, "EFID.CA", SectorEnum.INDUSTRY)
        assert blend.confidence_multiplier == pytest.approx(1.0, abs=0.01)
        assert blend.position_size_multiplier == pytest.approx(1.0, abs=0.01)

    def test_conflict_scenario_risk_off_reduces_confidence(self) -> None:
        from tradingagents.sentiment.taxonomy import SectorEnum
        blend = self._blend_for(build_conflict_data, "TMGH.CA", SectorEnum.REAL_ESTATE)
        # RISK_OFF macro → confidence multiplier reduced (0.80 per spec)
        # May also be reduced by GREED/NEUTRAL market regime
        # Either way: should not be exactly 1.0 when RISK_OFF fires
        macro_r = run_layer_a0(build_conflict_data("TMGH.CA", REF)["macro"], REF)
        if macro_r.composite_regime.value == "RISK_OFF":
            assert blend.confidence_multiplier < 1.0, (
                "RISK_OFF macro should reduce confidence multiplier below 1.0"
            )

    def test_blend_audit_string_contains_macro_or_market(self) -> None:
        from tradingagents.sentiment.taxonomy import SectorEnum
        blend = self._blend_for(build_positive_signal_data, "COMI.CA", SectorEnum.BANKS)
        # Audit must mention at least one layer
        audit_lower = blend.audit.lower()
        has_ref = any(kw in audit_lower for kw in ("macro", "market", "sector", "pass"))
        assert has_ref, f"Audit string lacks layer reference: {blend.audit!r}"
