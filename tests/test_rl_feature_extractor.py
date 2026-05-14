"""Tests for the RL feature extractor.

Coverage:
- Vector size matches FEATURE_NAMES length
- Determinism: identical input -> identical output
- Order: dict keys match FEATURE_NAMES tuple
- Action one-hot uses the project's SignalProcessor (no duplicated regex)
- Defensive defaults: empty / malformed state does not raise
- Sentiment blocks: nested social_sentiment_analysis JSON string is parsed
- No-look-ahead: trade_date is not consulted for any numeric feature
- Categorical fallbacks: unknown regime / sector -> zero vector
"""

import json

import numpy as np
import pytest

from tradingagents.rl.feature_extractor import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    extract_state_features,
    extract_state_features_dict,
    feature_vector_size,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _well_formed_state() -> dict:
    """A representative final_state covering the most-used branches."""
    return {
        "final_trade_decision": '{"action": "BUY", "rationale": "go long"}',
        "confidence_scores": {
            "overall": 0.72, "technical": 0.65, "fundamental": 0.80,
            "sentiment": 0.55, "position_size_multiplier": 0.85,
            "overall_status": "OK",
        },
        "data_quality": {"data_completeness_score": 85},
        "low_liquidity": False,
        "volume_missing": False,
        "avg_daily_volume": 250_000.0,
        "current_price": 130.0,
        "portfolio_value": 1_000_000.0,
        "current_position": {"shares": 200, "avg_cost": 120.0, "market_value": 26_000.0},
        "risk_metrics": {"adv_participation_pct": 0.04, "stop_distance_pct": 0.05},
        "risk_action": "ALLOW",
        "sentiment_blend_result": {
            "confidence_multiplier": 0.95,
            "position_size_multiplier": 0.85,
            "audit": "regime=NEUTRAL",
        },
        "social_sentiment_analysis": json.dumps({
            "market": {
                "status": "SIGNAL", "score": 0.10, "confidence": 0.60,
                "regime": "NEUTRAL", "volatility_mood": "CALM",
                "n_posts": 80, "n_distinct_sources": 4,
            },
            "sector": {
                "status": "SIGNAL", "score": 0.15, "confidence": 0.55,
                "sector": "banks", "n_posts": 20, "n_distinct_days": 5,
            },
            "stock": {
                "status": "SIGNAL", "score": 0.20, "confidence": 0.65,
                "ticker": "COMI.CA", "tier": "MEGA",
                "n_strong_mentions": 10, "n_distinct_authors": 6,
                "n_distinct_sources": 3, "contradicts_market": False,
            },
            "macro": {
                "composite_regime": "NEUTRAL",
                "active_events": [],
            },
        }),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Shape + ordering
# ──────────────────────────────────────────────────────────────────────────────


def test_feature_version_constant():
    assert isinstance(FEATURE_VERSION, str) and FEATURE_VERSION.startswith("rl_state_v")


def test_feature_vector_size_matches_names():
    assert feature_vector_size() == len(FEATURE_NAMES)
    assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES)), "feature names must be unique"


def test_returns_numpy_float32_array():
    arr = extract_state_features(_well_formed_state(), ticker="COMI.CA", trade_date="2024-06-01")
    assert isinstance(arr, np.ndarray)
    assert arr.dtype == np.float32
    assert arr.shape == (len(FEATURE_NAMES),)


def test_dict_keys_in_canonical_order():
    feats = extract_state_features_dict(
        _well_formed_state(), ticker="COMI.CA", trade_date="2024-06-01"
    )
    assert tuple(feats.keys()) == FEATURE_NAMES


def test_dict_and_array_agree():
    state = _well_formed_state()
    feats = extract_state_features_dict(state, ticker="COMI.CA", trade_date="2024-06-01")
    arr = extract_state_features(state, ticker="COMI.CA", trade_date="2024-06-01")
    for i, name in enumerate(FEATURE_NAMES):
        assert pytest.approx(arr[i], abs=1e-6) == feats[name], name


# ──────────────────────────────────────────────────────────────────────────────
# Determinism
# ──────────────────────────────────────────────────────────────────────────────


def test_deterministic_same_input_same_output():
    state = _well_formed_state()
    a = extract_state_features(state, ticker="COMI.CA", trade_date="2024-06-01")
    b = extract_state_features(state, ticker="COMI.CA", trade_date="2024-06-01")
    np.testing.assert_array_equal(a, b)


def test_deterministic_across_dict_ordering():
    state = _well_formed_state()
    reordered = dict(reversed(list(state.items())))
    a = extract_state_features(state, ticker="COMI.CA", trade_date="2024-06-01")
    b = extract_state_features(reordered, ticker="COMI.CA", trade_date="2024-06-01")
    np.testing.assert_array_equal(a, b)


# ──────────────────────────────────────────────────────────────────────────────
# Defensive defaults
# ──────────────────────────────────────────────────────────────────────────────


def test_empty_state_does_not_raise():
    arr = extract_state_features({}, ticker="COMI.CA", trade_date="2024-06-01")
    assert arr.shape == (len(FEATURE_NAMES),)
    # action defaults to HOLD via SignalProcessor on empty signal
    feats = extract_state_features_dict({}, ticker="COMI.CA", trade_date="2024-06-01")
    assert feats["action_hold"] == 1.0
    assert feats["action_buy"] == 0.0
    assert feats["action_sell"] == 0.0


def test_none_state_treated_as_empty():
    # extract_state_features_dict guards against non-dict input
    feats = extract_state_features_dict(None, ticker="COMI.CA", trade_date="2024-06-01")  # type: ignore[arg-type]
    assert feats["action_hold"] == 1.0


def test_malformed_social_json_does_not_crash():
    state = {
        "final_trade_decision": "FINAL TRANSACTION PROPOSAL: HOLD",
        "social_sentiment_analysis": "{not valid json",
    }
    feats = extract_state_features_dict(state, ticker="COMI.CA", trade_date="2024-06-01")
    # Sentiment scores should fall back to zero, action to HOLD
    assert feats["market_sentiment_score"] == 0.0
    assert feats["action_hold"] == 1.0


def test_nan_and_inf_in_confidence_become_zero():
    state = {
        "final_trade_decision": "BUY",
        "confidence_scores": {"overall": float("nan"), "technical": float("inf")},
    }
    feats = extract_state_features_dict(state, ticker="COMI.CA", trade_date="2024-06-01")
    assert feats["conf_overall"] == 0.0
    assert feats["conf_technical"] == 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Specific feature semantics
# ──────────────────────────────────────────────────────────────────────────────


def test_action_one_hot_from_signal_processor():
    """Action must come from SignalProcessor — not a re-implemented regex."""
    state_buy = {"final_trade_decision": "FINAL TRANSACTION PROPOSAL: BUY"}
    state_sell = {"final_trade_decision": "FINAL TRANSACTION PROPOSAL: SELL"}
    state_veto = {"final_trade_decision": "VETO — do not enter this trade"}

    f_buy = extract_state_features_dict(state_buy, ticker="COMI.CA", trade_date="2024-06-01")
    f_sell = extract_state_features_dict(state_sell, ticker="COMI.CA", trade_date="2024-06-01")
    f_veto = extract_state_features_dict(state_veto, ticker="COMI.CA", trade_date="2024-06-01")

    assert (f_buy["action_buy"], f_buy["action_sell"], f_buy["action_hold"]) == (1.0, 0.0, 0.0)
    assert (f_sell["action_buy"], f_sell["action_sell"], f_sell["action_hold"]) == (0.0, 1.0, 0.0)
    # VETO must collapse to HOLD per SignalProcessor's contract
    assert (f_veto["action_buy"], f_veto["action_sell"], f_veto["action_hold"]) == (0.0, 0.0, 1.0)


def test_sector_one_hot_for_known_ticker():
    feats = extract_state_features_dict(_well_formed_state(), ticker="COMI.CA", trade_date="2024-06-01")
    assert feats["sector_banks"] == 1.0
    # No other sector should be 1
    other_sectors = ("real_estate", "industry", "telecom_tech", "financial_services", "food_bev", "unknown")
    for s in other_sectors:
        assert feats[f"sector_{s}"] == 0.0


def test_sector_unknown_for_made_up_ticker():
    feats = extract_state_features_dict(_well_formed_state(), ticker="FOOBAR.CA", trade_date="2024-06-01")
    assert feats["sector_unknown"] == 1.0


def test_market_regime_one_hot():
    state = _well_formed_state()
    payload = json.loads(state["social_sentiment_analysis"])
    payload["market"]["regime"] = "PANIC"
    state["social_sentiment_analysis"] = json.dumps(payload)
    feats = extract_state_features_dict(state, ticker="COMI.CA", trade_date="2024-06-01")
    assert feats["market_regime_panic"] == 1.0
    assert feats["market_regime_neutral"] == 0.0


def test_unknown_regime_zero_vector():
    state = _well_formed_state()
    payload = json.loads(state["social_sentiment_analysis"])
    payload["market"]["regime"] = "NONSENSE"
    state["social_sentiment_analysis"] = json.dumps(payload)
    feats = extract_state_features_dict(state, ticker="COMI.CA", trade_date="2024-06-01")
    regime_features = [
        feats["market_regime_euphoria"], feats["market_regime_greed"],
        feats["market_regime_neutral"], feats["market_regime_fear"],
        feats["market_regime_panic"], feats["market_regime_no_signal"],
    ]
    assert sum(regime_features) == 0.0


def test_no_signal_layer_count():
    """NO_SIGNAL on 2 layers -> count == 2."""
    state = _well_formed_state()
    payload = json.loads(state["social_sentiment_analysis"])
    payload["market"]["status"] = "NO_SIGNAL"
    payload["sector"]["status"] = "NO_SIGNAL"
    state["social_sentiment_analysis"] = json.dumps(payload)
    feats = extract_state_features_dict(state, ticker="COMI.CA", trade_date="2024-06-01")
    assert feats["no_signal_layer_count"] == 2.0


def test_position_pct_uses_market_value_when_available():
    state = _well_formed_state()
    # 26_000 / 1_000_000 = 0.026
    feats = extract_state_features_dict(state, ticker="COMI.CA", trade_date="2024-06-01")
    assert pytest.approx(feats["position_pct_portfolio"], abs=1e-4) == 0.026


def test_position_pct_falls_back_to_shares_times_price():
    state = _well_formed_state()
    state["current_position"] = {"shares": 100, "avg_cost": 100.0}  # no market_value
    state["current_price"] = 110.0
    state["portfolio_value"] = 50_000.0
    feats = extract_state_features_dict(state, ticker="COMI.CA", trade_date="2024-06-01")
    # 100 * 110 / 50_000 = 0.22
    assert pytest.approx(feats["position_pct_portfolio"], abs=1e-4) == 0.22


def test_avg_daily_volume_log_transform():
    state = _well_formed_state()
    state["avg_daily_volume"] = 0.0
    feats = extract_state_features_dict(state, ticker="COMI.CA", trade_date="2024-06-01")
    assert feats["avg_daily_volume_log"] == 0.0
    state["avg_daily_volume"] = 100_000.0
    feats2 = extract_state_features_dict(state, ticker="COMI.CA", trade_date="2024-06-01")
    assert feats2["avg_daily_volume_log"] > 11.0  # log1p(100_000) ≈ 11.51


def test_portfolio_ctx_injection():
    feats = extract_state_features_dict(
        _well_formed_state(),
        ticker="COMI.CA",
        trade_date="2024-06-01",
        portfolio_ctx={"portfolio_drawdown_so_far_pct": 0.12, "settled_cash_pct": 0.70},
    )
    assert pytest.approx(feats["portfolio_drawdown_so_far_pct"], abs=1e-6) == 0.12
    assert pytest.approx(feats["settled_cash_pct"], abs=1e-6) == 0.70


def test_portfolio_ctx_defaults_when_missing():
    feats = extract_state_features_dict(_well_formed_state(), ticker="COMI.CA", trade_date="2024-06-01")
    assert feats["portfolio_drawdown_so_far_pct"] == 0.0
    # settled_cash_pct defaults to 1.0 (full settled when caller didn't say otherwise)
    assert feats["settled_cash_pct"] == 1.0


# ──────────────────────────────────────────────────────────────────────────────
# No look-ahead invariant
# ──────────────────────────────────────────────────────────────────────────────


def test_trade_date_is_not_consumed_by_numeric_features():
    """Two different trade_dates with identical state must yield identical vectors.

    This proves no numeric feature secretly reads a forward window keyed on
    trade_date. (Categorical / portfolio features also don't depend on date.)
    """
    state = _well_formed_state()
    a = extract_state_features(state, ticker="COMI.CA", trade_date="2024-06-01")
    b = extract_state_features(state, ticker="COMI.CA", trade_date="2025-12-31")
    np.testing.assert_array_equal(a, b)


def test_no_feature_named_forward_return():
    """Reward labels must never appear in the observation space."""
    for name in FEATURE_NAMES:
        assert "forward_return" not in name.lower()
        assert "trade_result" not in name.lower()
        assert "realized" not in name.lower()


# ──────────────────────────────────────────────────────────────────────────────
# Clipping / bounds
# ──────────────────────────────────────────────────────────────────────────────


def test_all_features_finite_on_well_formed_input():
    arr = extract_state_features(_well_formed_state(), ticker="COMI.CA", trade_date="2024-06-01")
    assert np.all(np.isfinite(arr))


def test_outliers_get_clipped():
    state = _well_formed_state()
    state["confidence_scores"]["overall"] = 5.0  # nonsense; clip range is [0, 1]
    feats = extract_state_features_dict(state, ticker="COMI.CA", trade_date="2024-06-01")
    assert feats["conf_overall"] == 1.0
    payload = json.loads(state["social_sentiment_analysis"])
    payload["sector"]["score"] = -5.0
    state["social_sentiment_analysis"] = json.dumps(payload)
    feats2 = extract_state_features_dict(state, ticker="COMI.CA", trade_date="2024-06-01")
    assert feats2["sector_sentiment_score"] == -1.0
