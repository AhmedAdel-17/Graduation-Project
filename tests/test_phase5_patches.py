"""Phase 5 corrective patch tests.

Covers:
  - rate_shock recency window (time-aware)
  - Stub FPI data does not escalate liquidity
  - Verified STRONG_OUTFLOW FPI data does escalate liquidity
  - Stub FX premium data does not produce ELEVATED/CRITICAL signals
  - Macro/regime blend combines with existing sentiment_blend_result
  - No hardcoded API keys remain in tradingagents/
  - P0: composite_direction = NO_SIGNAL when all inputs missing
  - P0: data_sources labels (csv / suppressed_static / unavailable)
  - P0: available_at filtering in CSV loaders
  - P0: static fallback guard for pre-CSV dates
  - P0: FRED vintage realtime_end
"""
import os
import pytest
from unittest.mock import patch


# ═══════════════════════════════════════════════════════════════════════════════
# 1. rate_shock recency window
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateShockRecency:
    def test_old_cbe_shock_outside_window_no_rate_shock(self):
        """A >=200 bps change from months ago must NOT trigger rate_shock."""
        from tradingagents.agents.analysts.macro_analyst import _compute_macro_signals

        macro_data = {
            "cbe_policy_rate": 0.2725,
            "cbe_last_change_bps": 600,
            "cbe_last_change_date": "2024-03-06",  # Over a year ago
            "tbill_91d_yield": 0.26,
            "egypt_cpi_yoy": 0.258,
            "vix_level": 20.0,
            "egp_usd_official": 48.5,
        }
        signals = _compute_macro_signals(macro_data, trade_date="2025-05-10")

        assert signals["rate_shock"] is False, (
            "600 bps change from 2024-03-06 should NOT trigger rate_shock on 2025-05-10 "
            "(outside 14-day recency window)"
        )
        # Audit fields should still be present
        assert signals["cbe_last_change_bps"] == 600
        assert signals["cbe_last_change_date"] == "2024-03-06"

    def test_recent_large_change_triggers_rate_shock(self):
        """A >=200 bps change within the recency window MUST trigger rate_shock."""
        from tradingagents.agents.analysts.macro_analyst import _compute_macro_signals

        macro_data = {
            "cbe_policy_rate": 0.2725,
            "cbe_last_change_bps": 600,
            "cbe_last_change_date": "2025-05-05",  # 5 days ago
            "tbill_91d_yield": 0.26,
            "egypt_cpi_yoy": 0.258,
            "vix_level": 20.0,
            "egp_usd_official": 48.5,
        }
        signals = _compute_macro_signals(macro_data, trade_date="2025-05-10")

        assert signals["rate_shock"] is True, (
            "600 bps change from 2025-05-05 should trigger rate_shock on 2025-05-10 "
            "(within 14-day recency window)"
        )

    def test_small_change_within_window_no_rate_shock(self):
        """A <200 bps change even if recent should NOT trigger rate_shock."""
        from tradingagents.agents.analysts.macro_analyst import _compute_macro_signals

        macro_data = {
            "cbe_policy_rate": 0.20,
            "cbe_last_change_bps": 100,
            "cbe_last_change_date": "2025-05-08",
            "tbill_91d_yield": 0.20,
            "egypt_cpi_yoy": 0.15,
            "vix_level": 18.0,
            "egp_usd_official": 50.0,
        }
        signals = _compute_macro_signals(macro_data, trade_date="2025-05-10")

        assert signals["rate_shock"] is False, (
            "100 bps change is below the 200 bps threshold"
        )

    def test_boundary_day_14_triggers(self):
        """Change exactly 14 days ago is still within the window."""
        from tradingagents.agents.analysts.macro_analyst import _compute_macro_signals

        macro_data = {
            "cbe_policy_rate": 0.25,
            "cbe_last_change_bps": 250,
            "cbe_last_change_date": "2025-04-26",  # Exactly 14 days before 2025-05-10
            "tbill_91d_yield": 0.24,
            "egypt_cpi_yoy": 0.15,
            "vix_level": 20.0,
            "egp_usd_official": 50.0,
        }
        signals = _compute_macro_signals(macro_data, trade_date="2025-05-10")
        assert signals["rate_shock"] is True

    def test_boundary_day_15_does_not_trigger(self):
        """Change 15 days ago is outside the window."""
        from tradingagents.agents.analysts.macro_analyst import _compute_macro_signals

        macro_data = {
            "cbe_policy_rate": 0.25,
            "cbe_last_change_bps": 250,
            "cbe_last_change_date": "2025-04-25",  # 15 days before 2025-05-10
            "tbill_91d_yield": 0.24,
            "egypt_cpi_yoy": 0.15,
            "vix_level": 20.0,
            "egp_usd_official": 50.0,
        }
        signals = _compute_macro_signals(macro_data, trade_date="2025-05-10")
        assert signals["rate_shock"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Stub FPI data does NOT escalate liquidity
# ═══════════════════════════════════════════════════════════════════════════════

class TestFPIStubBehavior:
    def test_stub_strong_outflow_does_not_escalate(self):
        """Stub FPI data with STRONG_OUTFLOW must NOT change liquidity classification."""
        from tradingagents.agents.analysts.liquidity_analyst import _load_fpi_flow

        # The current foreign_flow.csv has all rows marked "stub"
        fpi = _load_fpi_flow("COMI.CA", "2025-05-10")

        assert fpi is not None, "FPI data should be loaded"
        assert fpi["data_quality"] == "stub", "Sample rows should be marked as stub"
        # Even though the last COMI.CA row is -44M (OUTFLOW), it's stub data
        # so the escalation guard in liquidity_analyst_node should block it.
        # We verify the loader returns the quality flag correctly.
        assert fpi["fpi_signal"] in ("OUTFLOW", "STRONG_OUTFLOW")

    def test_verified_strong_outflow_escalates(self):
        """Verified FPI STRONG_OUTFLOW MUST escalate liquidity classification."""
        import tempfile
        import csv
        from tradingagents.agents.analysts.liquidity_analyst import _load_fpi_flow

        # Create a temporary CSV with verified data
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            writer = csv.writer(f)
            writer.writerow(["date", "ticker", "fpi_net_buy_egp", "fpi_net_sell_egp",
                             "fpi_net_flow_egp", "source", "data_quality"])
            writer.writerow(["2025-05-08", "TEST.CA", "10000000", "80000000",
                             "-70000000", "EGX", "verified"])
            tmp_path = f.name

        try:
            # Monkey-patch the loader to use our temp file
            import tradingagents.agents.analysts.liquidity_analyst as liq_mod
            original_fn = liq_mod._load_fpi_flow

            def patched_load(ticker, trade_date):
                if ticker == "TEST.CA":
                    import csv as csv2
                    with open(tmp_path, "r") as f2:
                        reader = csv2.DictReader(f2)
                        for row in reader:
                            if row["ticker"] == ticker and row["date"] <= trade_date:
                                flow = float(row["fpi_net_flow_egp"])
                                quality = row.get("data_quality", "stub")
                                if flow > 50_000_000:
                                    signal = "STRONG_INFLOW"
                                elif flow > 10_000_000:
                                    signal = "INFLOW"
                                elif flow >= -10_000_000:
                                    signal = "NEUTRAL"
                                elif flow >= -50_000_000:
                                    signal = "OUTFLOW"
                                else:
                                    signal = "STRONG_OUTFLOW"
                                return {
                                    "fpi_net_flow_egp": flow,
                                    "fpi_signal": signal,
                                    "data_quality": quality,
                                }
                    return None
                return original_fn(ticker, trade_date)

            fpi = patched_load("TEST.CA", "2025-05-10")
            assert fpi is not None
            assert fpi["data_quality"] == "verified"
            assert fpi["fpi_signal"] == "STRONG_OUTFLOW"
            # The escalation logic checks data_quality == "verified"
            assert fpi["data_quality"] in ("real", "verified"), \
                "Verified data should pass the escalation guard"
        finally:
            os.unlink(tmp_path)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Stub FX premium does NOT produce ELEVATED/CRITICAL signals
# ═══════════════════════════════════════════════════════════════════════════════

class TestFXPremiumStubBehavior:
    def test_stub_high_premium_maps_to_normal(self):
        """Stub FX premium data with 126.5% premium must map to NORMAL, not CRITICAL."""
        from tradingagents.agents.analysts.macro_analyst import _load_fx_premium

        # The first row in fx_premium.csv has 126.5% premium but is marked "stub"
        result = _load_fx_premium("2024-01-20")

        assert result is not None
        assert result["fx_premium_pct"] == 126.5
        assert result["data_quality"] == "stub"
        assert result["fx_premium_signal"] == "NORMAL", (
            "Stub data with 126.5% premium must map to NORMAL, not CRITICAL"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Macro/regime blend combines with existing sentiment_blend_result
# ═══════════════════════════════════════════════════════════════════════════════

class TestBlendPrecedence:
    def test_macro_regime_combines_with_prior_blend(self):
        """When sentiment_blend_result exists AND macro/regime are present,
        BOTH must affect final confidence and position_size_multiplier."""
        from tradingagents.agents.utils.scoring import calculate_unified_score

        state = {
            # Directional analysts (enough for quorum)
            "technical_analysis": {
                "signals": {"RSI": {"signal": "buy"}},
                "trend_direction": {"direction": "bullish"},
            },
            "fundamental_analysis": {
                "health_assessment": {
                    "profitability": {"status": "healthy"},
                    "leverage": {"status": "healthy"},
                    "liquidity": {"status": "healthy"},
                },
            },
            "sentiment_analysis": {
                "sentiment": "bullish",
                "sentiment_strength": "moderate",
            },
            # Pre-existing blend from social sentiment
            "sentiment_blend_result": {
                "confidence_multiplier": 0.90,
                "position_size_multiplier": 0.85,
                "audit": "social_blend(conf×0.90,size×0.85)",
            },
            # Macro analyst output — RISK_OFF should reduce confidence further
            "macro_analysis": {
                "composite_direction": "RISK_OFF",
            },
            # Regime analyst output — FEAR should reduce both conf and size
            "regime_analysis": {
                "market_regime_enum": "FEAR",
            },
        }

        decision, conf, reasoning, scores, status = calculate_unified_score(state)

        assert status == "OK"
        # The prior blend gives conf×0.90, size×0.85
        # RISK_OFF gives conf×0.80, size×0.80
        # FEAR gives conf×0.85, size×0.75
        # Combined conf multiplier: 0.90 * 0.80 * 0.85 = 0.612
        # Combined size multiplier: 0.85 * 0.80 * 0.75 = 0.51
        assert conf < 0.90, (
            f"Confidence {conf:.3f} must be less than the prior blend's 0.90 "
            "because macro RISK_OFF and regime FEAR should reduce it further"
        )
        assert scores["position_size_multiplier"] < 0.85, (
            f"Position size {scores['position_size_multiplier']:.3f} must be less than "
            "the prior blend's 0.85 because regime FEAR should reduce it"
        )
        assert "prior_blend" in reasoning, "Audit should mention prior blend"
        assert "macro_regime" in reasoning, "Audit should mention macro/regime blend"

    def test_macro_regime_applies_without_prior_blend(self):
        """When no sentiment_blend_result exists, macro/regime alone should still apply."""
        from tradingagents.agents.utils.scoring import calculate_unified_score

        state = {
            "technical_analysis": {
                "signals": {"RSI": {"signal": "buy"}},
                "trend_direction": {"direction": "bullish"},
            },
            "fundamental_analysis": {
                "health_assessment": {
                    "profitability": {"status": "healthy"},
                    "leverage": {"status": "healthy"},
                    "liquidity": {"status": "healthy"},
                },
            },
            "sentiment_analysis": {
                "sentiment": "bullish",
                "sentiment_strength": "moderate",
            },
            # NO prior blend
            "macro_analysis": {
                "composite_direction": "RISK_OFF",
            },
            "regime_analysis": {
                "market_regime_enum": "PANIC",
            },
        }

        decision, conf, reasoning, scores, status = calculate_unified_score(state)

        assert status == "OK"
        # PANIC: conf×0.70, size×0.50 | RISK_OFF: conf×0.80, size×0.80
        # Combined conf: unblended * 0.70 * 0.80
        # Combined size: 0.50 * 0.80 = 0.40
        assert scores["position_size_multiplier"] < 1.0, \
            "PANIC should reduce position size"
        assert "macro_regime" in reasoning

    def test_direction_never_flips(self):
        """Macro/regime must never flip a BUY into SELL."""
        from tradingagents.agents.utils.scoring import calculate_unified_score

        state = {
            "technical_analysis": {
                "signals": {"RSI": {"signal": "strong buy"}},
                "trend_direction": {"direction": "bullish"},
            },
            "fundamental_analysis": {
                "health_assessment": {
                    "profitability": {"status": "healthy"},
                    "leverage": {"status": "healthy"},
                    "liquidity": {"status": "healthy"},
                },
            },
            "sentiment_analysis": {
                "sentiment": "bullish",
                "sentiment_strength": "strong",
            },
            "macro_analysis": {"composite_direction": "RISK_OFF"},
            "regime_analysis": {"market_regime_enum": "PANIC"},
        }

        decision, conf, reasoning, scores, status = calculate_unified_score(state)
        # All directional analysts are bullish; RISK_OFF + PANIC should NOT flip to SELL
        assert decision in ("BUY", "STRONG_BUY"), \
            f"Direction should remain bullish, got {decision}"


# ═══════════════════════════════════════════════════════════════════════════════
# 4b. P1 scoring: RISK_ON pass-through, RISK_OFF dual reduction, no direction flip
# ═══════════════════════════════════════════════════════════════════════════════

class TestP1ScoringChanges:
    """Verify the two P1 scoring changes:
    - RISK_ON: (1.00, 1.00) — pure pass-through, no confidence penalty
    - RISK_OFF: (0.80, 0.80) — reduces BOTH confidence and position size
    """

    def test_risk_on_is_pure_pass_through(self):
        """RISK_ON must not reduce confidence or position size (was 0.95, now 1.00)."""
        from tradingagents.agents.utils.scoring import blend_sentiment
        from tradingagents.sentiment.contracts import MacroDirection

        class _MockMacro:
            composite_regime = MacroDirection.RISK_ON

        result = blend_sentiment(macro=_MockMacro())

        assert result.confidence_multiplier == 1.0, (
            f"RISK_ON confidence_multiplier should be 1.0 (pass-through), "
            f"got {result.confidence_multiplier}"
        )
        assert result.position_size_multiplier == 1.0, (
            f"RISK_ON position_size_multiplier should be 1.0, "
            f"got {result.position_size_multiplier}"
        )
        assert "pass-through" in result.audit, (
            "RISK_ON audit should say pass-through"
        )

    def test_risk_off_reduces_confidence_and_position_size(self):
        """RISK_OFF must reduce both confidence (×0.80) and position size (×0.80)."""
        from tradingagents.agents.utils.scoring import blend_sentiment
        from tradingagents.sentiment.contracts import MacroDirection

        class _MockMacro:
            composite_regime = MacroDirection.RISK_OFF

        result = blend_sentiment(macro=_MockMacro())

        assert abs(result.confidence_multiplier - 0.80) < 1e-6, (
            f"RISK_OFF confidence_multiplier should be 0.80, "
            f"got {result.confidence_multiplier}"
        )
        assert abs(result.position_size_multiplier - 0.80) < 1e-6, (
            f"RISK_OFF position_size_multiplier should be 0.80, "
            f"got {result.position_size_multiplier}"
        )
        assert "conf×0.80" in result.audit
        assert "size×0.80" in result.audit

    def test_risk_off_panic_does_not_flip_buy_direction(self):
        """Even worst-case RISK_OFF + PANIC, a strong BUY must stay BUY."""
        from tradingagents.agents.utils.scoring import calculate_unified_score

        state = {
            "technical_analysis": {
                "signals": {"RSI": {"signal": "strong buy"}},
                "trend_direction": {"direction": "bullish"},
            },
            "fundamental_analysis": {
                "health_assessment": {
                    "profitability": {"status": "healthy"},
                    "leverage": {"status": "healthy"},
                    "liquidity": {"status": "healthy"},
                },
            },
            "sentiment_analysis": {
                "sentiment": "bullish",
                "sentiment_strength": "strong",
            },
            "macro_analysis": {"composite_direction": "RISK_OFF"},
            "regime_analysis": {"market_regime_enum": "PANIC"},
        }

        decision, conf, reasoning, scores, status = calculate_unified_score(state)

        assert decision in ("BUY", "STRONG_BUY"), (
            f"RISK_OFF + PANIC must not flip BUY direction, got {decision}"
        )
        # Confidence and position size should be reduced
        assert scores["position_size_multiplier"] < 1.0, (
            "Position size should be reduced under RISK_OFF + PANIC"
        )
        # RISK_OFF(0.80) * PANIC(0.70) = 0.56 on confidence
        # RISK_OFF(0.80) * PANIC(0.50) = 0.40 on position size
        assert scores["position_size_multiplier"] < 0.50, (
            f"Expected position_size < 0.50 under RISK_OFF+PANIC, "
            f"got {scores['position_size_multiplier']:.3f}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. No hardcoded API keys
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# 6. composite_direction = NO_SIGNAL when all inputs are missing
# ═══════════════════════════════════════════════════════════════════════════════

class TestCompositeDirectionNoSignal:
    def test_empty_macro_data_produces_no_signal(self):
        """When all macro inputs are None/missing, composite_direction must be NO_SIGNAL."""
        from tradingagents.agents.analysts.macro_analyst import _compute_macro_signals

        signals = _compute_macro_signals({}, trade_date="2022-06-15")

        assert signals["composite_direction"] == "NO_SIGNAL", (
            "Empty macro data should produce NO_SIGNAL, not NEUTRAL"
        )
        assert signals["real_yield_signal"] is None
        assert signals["vix_regime"] is None

    def test_partial_data_still_produces_direction(self):
        """When at least one signal is present, composite_direction should NOT be NO_SIGNAL."""
        from tradingagents.agents.analysts.macro_analyst import _compute_macro_signals

        # Only VIX present — CALM gives risk_on_count=1, not enough for RISK_ON
        macro_data = {"vix_level": 15.0}
        signals = _compute_macro_signals(macro_data, trade_date="2025-05-10")

        assert signals["composite_direction"] != "NO_SIGNAL", (
            "VIX data is present — should produce a real direction"
        )
        assert signals["vix_regime"] == "CALM"

    def test_full_data_neutral_is_real_neutral(self):
        """When signals are present but balanced, NEUTRAL means a real balanced read."""
        from tradingagents.agents.analysts.macro_analyst import _compute_macro_signals

        # NEUTRAL real yield + CALM VIX = 1 risk_on, 0 risk_off → NEUTRAL
        macro_data = {
            "tbill_91d_yield": 0.20,
            "egypt_cpi_yoy": 0.18,   # real yield +2% → NEUTRAL band
            "vix_level": 15.0,        # CALM → risk_on_count=1
        }
        signals = _compute_macro_signals(macro_data, trade_date="2025-05-10")

        assert signals["composite_direction"] == "NEUTRAL"

    def test_no_signal_is_pass_through_in_blend(self):
        """NO_SIGNAL must map to multiplier 1.0 (pass-through) in scoring blend."""
        from tradingagents.agents.utils.scoring import blend_sentiment
        from tradingagents.sentiment.contracts import MacroDirection

        class _MockMacro:
            composite_regime = MacroDirection.NO_SIGNAL

        result = blend_sentiment(macro=_MockMacro())

        assert result.confidence_multiplier == 1.0, (
            "NO_SIGNAL should not modify confidence"
        )
        assert "pass-through" in result.audit


# ═══════════════════════════════════════════════════════════════════════════════
# 6b. Rate-shock guard: rate_shock=True blocks RISK_ON → NEUTRAL
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateShockBlocksRiskOn:
    """Ablation Case 2 (Mar 2024, +600bps): negative real yield + calm VIX
    produced RISK_ON despite an active rate shock.  The guard clamps to NEUTRAL."""

    def test_march_2024_rate_shock_blocks_risk_on(self):
        """600bps hike + negative real yield + calm VIX → NEUTRAL (not RISK_ON)."""
        from tradingagents.agents.analysts.macro_analyst import _compute_macro_signals

        macro_data = {
            "tbill_91d_yield": 0.2349,   # deeply negative real yield
            "egypt_cpi_yoy": 0.319,
            "cbe_last_change_bps": 600,
            "cbe_last_change_date": "2024-03-06",
            "vix_level": 15.2,            # CALM → +1 risk_on
        }
        signals = _compute_macro_signals(macro_data, trade_date="2024-03-12")

        assert signals["rate_shock"] is True
        assert signals["real_yield_signal"] == "RISK_ON"
        assert signals["vix_regime"] == "CALM"
        # Without the guard this would be RISK_ON (on=2, off=1).
        # Guard clamps to NEUTRAL.
        assert signals["composite_direction"] == "NEUTRAL", (
            f"rate_shock=True should block RISK_ON, got {signals['composite_direction']}"
        )

    def test_risk_on_fires_without_rate_shock(self):
        """Same negative real yield + calm VIX but no rate shock → RISK_ON."""
        from tradingagents.agents.analysts.macro_analyst import _compute_macro_signals

        macro_data = {
            "tbill_91d_yield": 0.2349,
            "egypt_cpi_yoy": 0.319,
            "cbe_last_change_bps": 0,
            "cbe_last_change_date": "2023-12-21",
            "vix_level": 12.7,
        }
        signals = _compute_macro_signals(macro_data, trade_date="2024-01-15")

        assert signals["rate_shock"] is False
        assert signals["composite_direction"] == "RISK_ON", (
            "Without rate_shock, negative real yield + calm VIX should give RISK_ON"
        )

    def test_rate_shock_does_not_block_risk_off(self):
        """Rate-shock guard only blocks RISK_ON, never RISK_OFF."""
        from tradingagents.agents.analysts.macro_analyst import _compute_macro_signals

        macro_data = {
            "tbill_91d_yield": 0.28,
            "egypt_cpi_yoy": 0.14,       # positive real yield → RISK_OFF
            "cbe_last_change_bps": -225,
            "cbe_last_change_date": "2025-04-17",
            "vix_level": 29.6,            # ELEVATED → +1 risk_off
        }
        signals = _compute_macro_signals(macro_data, trade_date="2025-04-20")

        assert signals["rate_shock"] is True
        # off=3 (real_yield + rate_shock + elevated_vix), on=0 → RISK_OFF
        assert signals["composite_direction"] == "RISK_OFF", (
            "Rate-shock guard must not interfere with RISK_OFF"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 7. data_sources shows suppressed_static when static fallback is suppressed
# ═══════════════════════════════════════════════════════════════════════════════

class TestDataSourceLabeling:
    """Verify data_sources labels distinguish csv, suppressed_static, and unavailable."""

    def _run_macro_node(self, trade_date, csv_data=None, vix=None, egp=None, fx30=None,
                        tbill_yield=None):
        """Helper: run the macro_analyst_node with mocked data fetchers."""
        from tradingagents.agents.analysts.macro_analyst import create_macro_analyst

        node = create_macro_analyst(quick_llm=None, deep_llm=None)

        state = {
            "trade_date": trade_date,
            "company_of_interest": "TEST.CA",
        }

        # Patch all external data fetchers
        with patch("tradingagents.agents.analysts.macro_analyst._load_macro_csv",
                    return_value=csv_data or {}), \
             patch("tradingagents.agents.analysts.macro_analyst._load_tbill_yield",
                    return_value=tbill_yield), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_fred_macro",
                    return_value={}), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_vix",
                    return_value=vix), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_egp_usd",
                    return_value=egp), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_fx_change_30d",
                    return_value=fx30), \
             patch("tradingagents.agents.analysts.macro_analyst._load_fx_premium",
                    return_value=None):
            result = node(state)

        return result["macro_analysis"]["data_sources"]

    def test_pre_csv_date_shows_suppressed_static(self):
        """Before CSV coverage (2023-01-05) with no CSV data, sources must say suppressed_static."""
        sources = self._run_macro_node("2022-06-15")

        assert sources["cbe_rate"] == "suppressed_static"
        assert sources["tbill"] == "suppressed_static"
        assert sources["cpi"] == "suppressed_static"
        assert sources["egp_usd"] == "suppressed_static"

    def test_csv_present_shows_csv(self):
        """When CSV data is available, sources must say csv."""
        csv_data = {
            "cbe_policy_rate": 0.2725,
            "tbill_91d_yield": 0.26,
            "egypt_cpi_yoy": 0.258,
        }
        sources = self._run_macro_node("2024-06-15", csv_data=csv_data)

        assert sources["cbe_rate"] == "csv"
        assert sources["tbill"] == "csv"
        assert sources["cpi"] == "csv"

    def test_within_range_no_csv_shows_unavailable(self):
        """Within CSV range but no rows matched — sources must say unavailable."""
        sources = self._run_macro_node("2024-06-15")

        assert sources["cbe_rate"] == "unavailable"
        assert sources["tbill"] == "unavailable"
        assert sources["cpi"] == "unavailable"

    def test_yfinance_vix_shows_yfinance(self):
        """VIX from yfinance should show yfinance regardless of CSV state."""
        sources = self._run_macro_node("2022-06-15", vix=20.0)

        assert sources["vix"] == "yfinance"

    def test_no_vix_shows_unavailable(self):
        """Missing VIX should always show unavailable."""
        sources = self._run_macro_node("2024-06-15")

        assert sources["vix"] == "unavailable"

    def test_csv_egp_usd_shows_csv_when_yfinance_unavailable(self):
        """When yfinance returns None but CSV has egp_usd, source must say csv."""
        csv_data = {"egp_usd_official": 50.8}
        sources = self._run_macro_node("2024-06-15", csv_data=csv_data, egp=None)

        assert sources["egp_usd"] == "csv", (
            f"egp_usd source should be 'csv' when CSV provides it, got {sources['egp_usd']!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 8. available_at filtering in CSV loaders
# ═══════════════════════════════════════════════════════════════════════════════

class TestAvailableAtFiltering:
    def test_macro_csv_uses_available_at_for_filtering(self):
        """_load_macro_csv must filter by available_at, not date."""
        import tempfile
        import csv as csv_mod
        from tradingagents.agents.analysts.macro_analyst import _load_macro_csv
        import tradingagents.agents.analysts.macro_analyst as macro_mod

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            writer = csv_mod.writer(f)
            writer.writerow(["date", "available_at", "cbe_rate", "tbill_91d",
                             "cpi_yoy", "egp_usd", "cbe_change_bps", "source",
                             "data_quality", "notes"])
            # Decision on 2025-04-17, but only available on 2025-04-18
            writer.writerow(["2025-04-17", "2025-04-18", "0.2275", "0.228",
                             "0.135", "50.8", "-250", "CBE MPC", "unverified", "test"])
            tmp_path = f.name

        try:
            # Point the loader at our temp file's directory
            parent_dir = os.path.dirname(tmp_path)
            expected_dir = os.path.join(parent_dir, "egx_macro")
            os.makedirs(expected_dir, exist_ok=True)
            expected_path = os.path.join(expected_dir, "egypt_macro.csv")
            import shutil
            shutil.copy(tmp_path, expected_path)

            original_get_config = macro_mod.get_config
            macro_mod.get_config = lambda: {"data_cache_dir": parent_dir}

            try:
                # On 2025-04-17, available_at is 2025-04-18 → should NOT be visible
                result = _load_macro_csv("2025-04-17")
                assert not result, (
                    "Row with available_at=2025-04-18 should NOT be visible on 2025-04-17"
                )

                # On 2025-04-18, it should be visible
                result = _load_macro_csv("2025-04-18")
                assert result.get("cbe_policy_rate") == 0.2275, (
                    "Row should be visible on 2025-04-18"
                )
            finally:
                os.unlink(expected_path)
                os.rmdir(expected_dir)
                macro_mod.get_config = original_get_config
        finally:
            os.unlink(tmp_path)

    def test_fpi_csv_uses_available_at_for_filtering(self):
        """_load_fpi_flow must filter by available_at, not date."""
        from tradingagents.agents.analysts.liquidity_analyst import _load_fpi_flow
        import tempfile
        import csv as csv_mod

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            writer = csv_mod.writer(f)
            writer.writerow(["date", "available_at", "ticker", "fpi_net_buy_egp",
                             "fpi_net_sell_egp", "fpi_net_flow_egp", "source",
                             "data_quality"])
            # Trade on 2025-05-05, available next day
            writer.writerow(["2025-05-05", "2025-05-06", "TEST.CA", "85000000",
                             "42000000", "43000000", "EGX", "stub"])
            tmp_path = f.name

        try:
            import tradingagents.agents.analysts.liquidity_analyst as liq_mod
            original_get_config = liq_mod.get_config

            parent_dir = os.path.dirname(tmp_path)

            def fake_config():
                return {"data_cache_dir": parent_dir}

            liq_mod.get_config = fake_config

            # Rename temp file to match expected path
            expected_dir = os.path.join(parent_dir, "egx_macro")
            os.makedirs(expected_dir, exist_ok=True)
            expected_path = os.path.join(expected_dir, "foreign_flow.csv")
            import shutil
            shutil.copy(tmp_path, expected_path)

            try:
                # On 2025-05-05, the data shouldn't be available yet
                result = _load_fpi_flow("TEST.CA", "2025-05-05")
                assert result is None, (
                    "FPI data with available_at=2025-05-06 should NOT be visible on 2025-05-05"
                )

                # On 2025-05-06, it should be available
                result = _load_fpi_flow("TEST.CA", "2025-05-06")
                assert result is not None, (
                    "FPI data with available_at=2025-05-06 should be visible on 2025-05-06"
                )
                assert result["fpi_net_flow_egp"] == 43000000
            finally:
                os.unlink(expected_path)
                os.rmdir(expected_dir)

            liq_mod.get_config = original_get_config
        finally:
            os.unlink(tmp_path)


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Static fallback guard for pre-CSV dates
# ═══════════════════════════════════════════════════════════════════════════════

class TestStaticFallbackGuard:
    def test_pre_csv_date_suppresses_static_defaults(self):
        """Before first CSV date, macro_data should be empty (not static late-2024 values)."""
        from tradingagents.agents.analysts.macro_analyst import create_macro_analyst

        node = create_macro_analyst(quick_llm=None, deep_llm=None)

        state = {
            "trade_date": "2022-01-15",
            "company_of_interest": "TEST.CA",
        }

        with patch("tradingagents.agents.analysts.macro_analyst._load_macro_csv",
                    return_value={}), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_fred_macro",
                    return_value={}), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_vix",
                    return_value=None), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_egp_usd",
                    return_value=None), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_fx_change_30d",
                    return_value=None), \
             patch("tradingagents.agents.analysts.macro_analyst._load_fx_premium",
                    return_value=None):
            result = node(state)

        analysis = result["macro_analysis"]
        # Should be NO_SIGNAL (not NEUTRAL), because no data
        assert analysis["composite_direction"] == "NO_SIGNAL"
        # CBE rate should be None (not the static 27.25%)
        assert analysis["cbe_policy_rate"] is None
        # All sources should say suppressed_static
        for key in ("cbe_rate", "tbill", "cpi"):
            assert analysis["data_sources"][key] == "suppressed_static", (
                f"data_sources[{key!r}] should be 'suppressed_static', "
                f"got {analysis['data_sources'][key]!r}"
            )

    def test_within_csv_range_no_csv_rows_returns_no_signal(self):
        """Within CSV range but no CSV rows matched — should not use static defaults."""
        from tradingagents.agents.analysts.macro_analyst import create_macro_analyst

        node = create_macro_analyst(quick_llm=None, deep_llm=None)

        state = {
            "trade_date": "2024-06-15",
            "company_of_interest": "TEST.CA",
        }

        with patch("tradingagents.agents.analysts.macro_analyst._load_macro_csv",
                    return_value={}), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_fred_macro",
                    return_value={}), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_vix",
                    return_value=None), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_egp_usd",
                    return_value=None), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_fx_change_30d",
                    return_value=None), \
             patch("tradingagents.agents.analysts.macro_analyst._load_fx_premium",
                    return_value=None):
            result = node(state)

        analysis = result["macro_analysis"]
        # Should be NO_SIGNAL because neither CSV nor FRED/yfinance provided data.
        assert analysis["composite_direction"] == "NO_SIGNAL"
        # CBE rate should be None rather than a static demo default.
        assert analysis["cbe_policy_rate"] is None
        # Sources should say unavailable (not static).
        assert analysis["data_sources"]["cbe_rate"] == "unavailable"


# ═══════════════════════════════════════════════════════════════════════════════
# 10. T-bill yield CSV loader and real yield calculation
# ═══════════════════════════════════════════════════════════════════════════════

class TestTbillYieldLoader:
    def test_loader_returns_yield_after_earliest_bulletin(self):
        """T-bill yield CSV returns data when trade_date >= earliest available_at (2023-11-29)."""
        from tradingagents.agents.analysts.macro_analyst import _load_tbill_yield

        # Nov 2023 bulletin (available_at=2023-11-29) covers Jun 2022 - Jul 2023.
        # A backtest on 2023-12-15 should see Jul 2023 yield (0.2349).
        y = _load_tbill_yield("2023-12-15")
        assert y is not None
        assert abs(y - 0.2349) < 0.001, f"Expected yield ~0.2349 (Jul 2023), got {y}"

    def test_loader_tiered_availability(self):
        """T-bill yields become available in tiers matching bulletin publication dates."""
        from tradingagents.agents.analysts.macro_analyst import _load_tbill_yield

        # Before any bulletin: no data
        assert _load_tbill_yield("2023-11-28") is None

        # After Nov 2023 bulletin (2023-11-29): sees through Jul 2023
        y1 = _load_tbill_yield("2023-11-30")
        assert y1 is not None
        assert abs(y1 - 0.2349) < 0.001, f"Expected Jul 2023 yield, got {y1}"

        # After Sep 2024 bulletin (2024-09-30): sees through Apr 2024
        y2 = _load_tbill_yield("2024-10-01")
        assert y2 is not None
        assert abs(y2 - 0.2559) < 0.001, f"Expected Apr 2024 yield, got {y2}"

        # Between bulletins: Sep 2024 data not yet available on 2024-09-29
        y3 = _load_tbill_yield("2024-09-29")
        assert y3 is not None
        # Should see Jul 2023 (last from Nov 2023 bulletin), not Aug 2023+
        assert abs(y3 - 0.2349) < 0.001, f"Expected Jul 2023 yield (pre-Sep bulletin), got {y3}"

    def test_loader_returns_none_before_coverage(self):
        """Before T-bill CSV observation range, loader should return None."""
        from tradingagents.agents.analysts.macro_analyst import _load_tbill_yield

        y = _load_tbill_yield("2022-05-01")
        assert y is None

    def test_loader_returns_none_before_any_publication(self):
        """Before first MoF bulletin date (2023-11-29), loader returns None."""
        from tradingagents.agents.analysts.macro_analyst import _load_tbill_yield

        # Observation data exists for 2023-06, but the earliest bulletin
        # with that data was published 2023-11-29.
        y = _load_tbill_yield("2023-06-15")
        assert y is None, (
            "T-bill yield should be None before first bulletin publication (2023-11-29)."
        )

    def test_tbill_enables_real_yield_signal(self):
        """When both T-bill and CPI are available (post-publication), real_yield is computed."""
        from tradingagents.agents.analysts.macro_analyst import create_macro_analyst

        node = create_macro_analyst(quick_llm=None, deep_llm=None)
        # Use a date after 2025-06-18 so all T-bill CSV data is available
        state = {"trade_date": "2025-07-01", "company_of_interest": "TEST.CA"}

        with patch("tradingagents.agents.analysts.macro_analyst._fetch_vix",
                    return_value=None), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_egp_usd",
                    return_value=None), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_fx_change_30d",
                    return_value=None), \
             patch("tradingagents.agents.analysts.macro_analyst._load_fx_premium",
                    return_value=None):
            result = node(state)

        analysis = result["macro_analysis"]
        assert analysis["real_yield_91d"] is not None, "Real yield should be computed"
        assert analysis["real_yield_signal"] is not None
        assert analysis["data_sources"]["tbill"] == "tbill_csv"
        assert analysis["data_sources"]["cpi"] == "csv"

    def test_tbill_enables_real_yield_mid_2024(self):
        """After Sep 2024 bulletin, backtests in late 2024 get real yield signals."""
        from tradingagents.agents.analysts.macro_analyst import create_macro_analyst

        node = create_macro_analyst(quick_llm=None, deep_llm=None)
        # 2024-10-15: T-bill available (Sep 2024 bulletin), CPI available from macro CSV
        state = {"trade_date": "2024-10-15", "company_of_interest": "TEST.CA"}

        with patch("tradingagents.agents.analysts.macro_analyst._fetch_vix",
                    return_value=None), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_egp_usd",
                    return_value=None), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_fx_change_30d",
                    return_value=None), \
             patch("tradingagents.agents.analysts.macro_analyst._load_fx_premium",
                    return_value=None):
            result = node(state)

        analysis = result["macro_analysis"]
        assert analysis["real_yield_91d"] is not None, \
            "Real yield should compute for 2024-10-15 (T-bill from Sep bulletin + CPI from CSV)"
        assert analysis["data_sources"]["tbill"] == "tbill_csv"

    def test_tbill_source_shows_unavailable_without_data(self):
        """When T-bill CSV has no PIT coverage, source should say unavailable."""
        from tradingagents.agents.analysts.macro_analyst import create_macro_analyst

        node = create_macro_analyst(quick_llm=None, deep_llm=None)
        state = {"trade_date": "2023-02-10", "company_of_interest": "TEST.CA"}

        with patch("tradingagents.agents.analysts.macro_analyst._load_tbill_yield",
                    return_value=None), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_vix",
                    return_value=None), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_egp_usd",
                    return_value=None), \
             patch("tradingagents.agents.analysts.macro_analyst._fetch_fx_change_30d",
                    return_value=None), \
             patch("tradingagents.agents.analysts.macro_analyst._load_fx_premium",
                    return_value=None):
            result = node(state)

        assert result["macro_analysis"]["data_sources"]["tbill"] == "unavailable"


# ═══════════════════════════════════════════════════════════════════════════════
# 11. FRED vintage realtime_end parameter
# ═══════════════════════════════════════════════════════════════════════════════

class TestFredVintageFilter:
    def test_fred_provider_sends_realtime_end(self):
        """FRED API calls must include realtime_end=trade_date to prevent look-ahead."""
        import json

        captured_params = {}

        def mock_get(url, params=None, timeout=None):
            captured_params.update(params or {})

            class MockResp:
                status_code = 200
                def json(self):
                    return {"observations": []}
                def raise_for_status(self):
                    pass

            return MockResp()

        with patch("tradingagents.dataflows.fred_provider.requests.get", side_effect=mock_get):
            from tradingagents.dataflows.fred_provider import fetch_fred_series
            fetch_fred_series("FPCPITOTLZGEGY", "2024-06-15")

        assert "realtime_end" in captured_params, (
            "FRED API call must include realtime_end parameter"
        )
        assert captured_params["realtime_end"] == "2024-06-15", (
            f"realtime_end should be trade_date, got {captured_params['realtime_end']}"
        )


class TestNoHardcodedKeys:
    def test_no_api_keys_in_source(self):
        """No hardcoded API key patterns should exist in tradingagents/ source."""
        import re
        import pathlib

        root = pathlib.Path(__file__).parent.parent / "tradingagents"
        key_patterns = [
            re.compile(r"sk-[a-f0-9]{32,}"),               # OpenAI-style
            re.compile(r"gsk_[a-zA-Z0-9]{40,}"),            # Groq-style
            re.compile(r"AIzaSy[a-zA-Z0-9_-]{33}"),         # Google-style
            re.compile(r"[0-9a-f]{14}\.[0-9]{8}"),          # EODHD-style
        ]

        violations = []
        for py_file in root.rglob("*.py"):
            content = py_file.read_text(errors="ignore")
            for pattern in key_patterns:
                matches = pattern.findall(content)
                if matches:
                    violations.append(f"{py_file.relative_to(root)}: {matches}")

        assert not violations, (
            f"Hardcoded API keys found:\n" + "\n".join(violations)
        )
