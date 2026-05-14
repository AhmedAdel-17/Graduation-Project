"""Canonical state -> feature-vector mapping for the RL meta-policy.

This module is the single source of truth for the observation space used at
both training time (offline dataset construction) and inference time
(`RLSizingPolicy.predict`, arriving in Stage C). Both paths MUST call
``extract_state_features`` so the feature ordering and units cannot drift.

Design rules:
- Pure function. No LLM calls, no IO, no global state.
- Deterministic. The same ``(final_state, ticker, trade_date, portfolio_ctx)``
  must yield the same vector across runs and machines.
- Defensive. Missing or malformed sub-fields fall back to a zero / NaN-safe
  default; the policy must not crash on partial state.
- No look-ahead. Every feature is sourced from fields that the agent graph
  produced *at* ``trade_date``. Forward returns are NEVER features — they
  are reward labels only and live in ``dataset.py``.

Versioning:
- ``FEATURE_VERSION`` is bumped any time the feature list, ordering, or unit
  conventions change. A trained model carries this version in its fingerprint;
  inference refuses a vector whose version disagrees with the model card.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np

from tradingagents.sentiment.taxonomy import SectorEnum, ticker_to_sector

logger = logging.getLogger("tradingagents.rl.feature_extractor")

FEATURE_VERSION = "rl_state_v1"


# ─────────────────────────────────────────────────────────────────────────────
# Categorical vocabularies. Order is part of the contract.
# ─────────────────────────────────────────────────────────────────────────────

_ACTION_VOCAB: Tuple[str, ...] = ("BUY", "SELL", "HOLD")
_MARKET_REGIME_VOCAB: Tuple[str, ...] = (
    "EUPHORIA", "GREED", "NEUTRAL", "FEAR", "PANIC", "NO_SIGNAL",
)
_VOLATILITY_MOOD_VOCAB: Tuple[str, ...] = (
    "CALM", "ELEVATED", "STRESSED", "NO_SIGNAL",
)
_MACRO_DIR_VOCAB: Tuple[str, ...] = (
    "RISK_ON", "NEUTRAL", "RISK_OFF", "NO_SIGNAL",
)
_SECTOR_VOCAB: Tuple[str, ...] = (
    SectorEnum.BANKS.value,
    SectorEnum.REAL_ESTATE.value,
    SectorEnum.INDUSTRY.value,
    SectorEnum.TELECOM_TECH.value,
    SectorEnum.FINANCIAL_SERVICES.value,
    SectorEnum.FOOD_BEV.value,
    SectorEnum.UNKNOWN.value,
)


def _one_hot(value: Optional[str], vocab: Tuple[str, ...]) -> Tuple[float, ...]:
    """One-hot encode ``value`` against ``vocab``. Unknown -> all zeros."""
    if value is None:
        return tuple(0.0 for _ in vocab)
    v = str(value).strip().upper()
    return tuple(1.0 if v == token.upper() else 0.0 for token in vocab)


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Coerce ``value`` to float; return ``default`` on failure / NaN / inf."""
    try:
        if value is None:
            return default
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ─────────────────────────────────────────────────────────────────────────────
# Feature spec table. The order here defines the column order of the output
# vector and the parquet schema. Bump FEATURE_VERSION when this changes.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _FeatureSpec:
    name: str
    extractor: Callable[[Dict[str, Any]], float]


def _parse_social_sentiment(state: Dict[str, Any]) -> Dict[str, Any]:
    """``state['social_sentiment_analysis']`` is a JSON string. Parse defensively."""
    raw = state.get("social_sentiment_analysis")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _get_market_block(state: Dict[str, Any]) -> Dict[str, Any]:
    social = _parse_social_sentiment(state)
    market = social.get("market") if isinstance(social, dict) else None
    return market if isinstance(market, dict) else {}


def _get_sector_block(state: Dict[str, Any]) -> Dict[str, Any]:
    social = _parse_social_sentiment(state)
    sector = social.get("sector") if isinstance(social, dict) else None
    return sector if isinstance(sector, dict) else {}


def _get_stock_block(state: Dict[str, Any]) -> Dict[str, Any]:
    social = _parse_social_sentiment(state)
    stock = social.get("stock") if isinstance(social, dict) else None
    return stock if isinstance(stock, dict) else {}


def _get_macro_block(state: Dict[str, Any]) -> Dict[str, Any]:
    social = _parse_social_sentiment(state)
    macro = social.get("macro") if isinstance(social, dict) else None
    return macro if isinstance(macro, dict) else {}


def _get_blend_block(state: Dict[str, Any]) -> Dict[str, Any]:
    """`sentiment_blend_result` is a dict with the multipliers."""
    blend = state.get("sentiment_blend_result")
    return blend if isinstance(blend, dict) else {}


def _get_confidence_block(state: Dict[str, Any]) -> Dict[str, Any]:
    conf = state.get("confidence_scores")
    return conf if isinstance(conf, dict) else {}


def _get_data_quality_block(state: Dict[str, Any]) -> Dict[str, Any]:
    dq = state.get("data_quality")
    return dq if isinstance(dq, dict) else {}


def _get_position_block(state: Dict[str, Any]) -> Dict[str, Any]:
    pos = state.get("current_position")
    return pos if isinstance(pos, dict) else {}


def _get_risk_metrics_block(state: Dict[str, Any]) -> Dict[str, Any]:
    rm = state.get("risk_metrics")
    return rm if isinstance(rm, dict) else {}


def _extract_action(final_state: Dict[str, Any]) -> str:
    """Re-use the project's regex extractor — DO NOT duplicate the regex here."""
    from tradingagents.graph.signal_processing import SignalProcessor

    signal = (
        final_state.get("final_trade_decision")
        or final_state.get("trader_investment_plan")
        or ""
    )
    return SignalProcessor().process_signal(signal)


# Numeric feature extractors. Each takes the full final_state and returns one float.

def _extract_conf_overall(s: Dict[str, Any]) -> float:
    return _clip(_safe_float(_get_confidence_block(s).get("overall")), 0.0, 1.0)


def _extract_conf_technical(s: Dict[str, Any]) -> float:
    return _clip(_safe_float(_get_confidence_block(s).get("technical")), 0.0, 1.0)


def _extract_conf_fundamental(s: Dict[str, Any]) -> float:
    return _clip(_safe_float(_get_confidence_block(s).get("fundamental")), 0.0, 1.0)


def _extract_conf_sentiment(s: Dict[str, Any]) -> float:
    return _clip(_safe_float(_get_confidence_block(s).get("sentiment")), 0.0, 1.0)


def _extract_pos_size_multiplier(s: Dict[str, Any]) -> float:
    """Existing rule-based multiplier (becomes a baseline feature for RL)."""
    block = _get_confidence_block(s)
    val = block.get("position_size_multiplier")
    if val is None:
        val = _get_blend_block(s).get("position_size_multiplier")
    return _clip(_safe_float(val, default=1.0), 0.0, 1.5)


def _extract_blend_confidence_mult(s: Dict[str, Any]) -> float:
    return _clip(
        _safe_float(_get_blend_block(s).get("confidence_multiplier"), default=1.0),
        0.0, 1.5,
    )


def _extract_sector_score(s: Dict[str, Any]) -> float:
    return _clip(_safe_float(_get_sector_block(s).get("score")), -1.0, 1.0)


def _extract_sector_confidence(s: Dict[str, Any]) -> float:
    return _clip(_safe_float(_get_sector_block(s).get("confidence")), 0.0, 1.0)


def _extract_stock_score(s: Dict[str, Any]) -> float:
    return _clip(_safe_float(_get_stock_block(s).get("score")), -1.0, 1.0)


def _extract_stock_confidence(s: Dict[str, Any]) -> float:
    return _clip(_safe_float(_get_stock_block(s).get("confidence")), 0.0, 1.0)


def _extract_stock_contradicts_market(s: Dict[str, Any]) -> float:
    return 1.0 if bool(_get_stock_block(s).get("contradicts_market", False)) else 0.0


def _extract_market_score(s: Dict[str, Any]) -> float:
    return _clip(_safe_float(_get_market_block(s).get("score")), -1.0, 1.0)


def _extract_market_confidence(s: Dict[str, Any]) -> float:
    return _clip(_safe_float(_get_market_block(s).get("confidence")), 0.0, 1.0)


def _extract_market_n_posts(s: Dict[str, Any]) -> float:
    """Log1p so 0 and very large counts both stay numerically tame."""
    return math.log1p(max(0.0, _safe_float(_get_market_block(s).get("n_posts"))))


def _extract_data_completeness(s: Dict[str, Any]) -> float:
    val = _safe_float(_get_data_quality_block(s).get("data_completeness_score"))
    # data_completeness_score is reported as 0..100 in the codebase; normalize.
    if val > 1.0:
        val = val / 100.0
    return _clip(val, 0.0, 1.0)


def _extract_no_signal_count(s: Dict[str, Any]) -> float:
    """How many sentiment layers reported NO_SIGNAL. 0..4."""
    layers = (
        _get_market_block(s),
        _get_sector_block(s),
        _get_stock_block(s),
        _get_macro_block(s),
    )
    no_signal_tokens = ("NO_SIGNAL", "INSUFFICIENT_DATA", "INSUFFICIENT")
    n = 0
    for layer in layers:
        status = str(layer.get("status", "")).upper()
        if any(tok in status for tok in no_signal_tokens):
            n += 1
        elif not layer:
            n += 1  # missing layer counts as NO_SIGNAL
    return float(n)


def _extract_low_liquidity(s: Dict[str, Any]) -> float:
    return 1.0 if bool(s.get("low_liquidity", False)) else 0.0


def _extract_volume_missing(s: Dict[str, Any]) -> float:
    return 1.0 if bool(s.get("volume_missing", False)) else 0.0


def _extract_adv_log(s: Dict[str, Any]) -> float:
    """``log1p(avg_daily_volume)`` so the magnitude doesn't dominate."""
    adv = _safe_float(s.get("avg_daily_volume"))
    return math.log1p(max(0.0, adv))


def _extract_position_pct(s: Dict[str, Any]) -> float:
    """Current holding value / portfolio value (0..1+)."""
    portfolio_val = _safe_float(s.get("portfolio_value"))
    if portfolio_val <= 0:
        return 0.0
    pos = _get_position_block(s)
    pos_val = _safe_float(pos.get("market_value"))
    if pos_val == 0.0:
        shares = _safe_float(pos.get("shares"))
        cur_price = _safe_float(s.get("current_price"))
        pos_val = shares * cur_price
    return _clip(pos_val / portfolio_val, 0.0, 2.0)


def _extract_risk_action_throttle(s: Dict[str, Any]) -> float:
    """1.0 if the risk scorer threw a THROTTLE / WARN / VETO flag."""
    action = str(s.get("risk_action") or _get_risk_metrics_block(s).get("risk_action") or "").upper()
    return 1.0 if action in ("THROTTLE", "WARN", "VETO") else 0.0


def _extract_risk_adv_participation(s: Dict[str, Any]) -> float:
    return _clip(
        _safe_float(_get_risk_metrics_block(s).get("adv_participation_pct")),
        0.0, 1.0,
    )


def _extract_risk_stop_distance(s: Dict[str, Any]) -> float:
    return _clip(
        _safe_float(_get_risk_metrics_block(s).get("stop_distance_pct")),
        0.0, 0.5,
    )


_NUMERIC_FEATURES: Tuple[_FeatureSpec, ...] = (
    _FeatureSpec("conf_overall",                  _extract_conf_overall),
    _FeatureSpec("conf_technical",                _extract_conf_technical),
    _FeatureSpec("conf_fundamental",              _extract_conf_fundamental),
    _FeatureSpec("conf_sentiment",                _extract_conf_sentiment),
    _FeatureSpec("rule_pos_size_multiplier",      _extract_pos_size_multiplier),
    _FeatureSpec("blend_confidence_multiplier",   _extract_blend_confidence_mult),
    _FeatureSpec("sector_sentiment_score",        _extract_sector_score),
    _FeatureSpec("sector_sentiment_confidence",   _extract_sector_confidence),
    _FeatureSpec("stock_sentiment_score",         _extract_stock_score),
    _FeatureSpec("stock_sentiment_confidence",    _extract_stock_confidence),
    _FeatureSpec("stock_contradicts_market",      _extract_stock_contradicts_market),
    _FeatureSpec("market_sentiment_score",        _extract_market_score),
    _FeatureSpec("market_sentiment_confidence",   _extract_market_confidence),
    _FeatureSpec("market_log_n_posts",            _extract_market_n_posts),
    _FeatureSpec("data_completeness",             _extract_data_completeness),
    _FeatureSpec("no_signal_layer_count",         _extract_no_signal_count),
    _FeatureSpec("low_liquidity_flag",            _extract_low_liquidity),
    _FeatureSpec("volume_missing_flag",           _extract_volume_missing),
    _FeatureSpec("avg_daily_volume_log",          _extract_adv_log),
    _FeatureSpec("position_pct_portfolio",        _extract_position_pct),
    _FeatureSpec("risk_throttle_flag",            _extract_risk_action_throttle),
    _FeatureSpec("risk_adv_participation_pct",    _extract_risk_adv_participation),
    _FeatureSpec("risk_stop_distance_pct",        _extract_risk_stop_distance),
)


def _categorical_feature_names() -> Tuple[str, ...]:
    names = []
    names.extend(f"action_{v.lower()}" for v in _ACTION_VOCAB)
    names.extend(f"market_regime_{v.lower()}" for v in _MARKET_REGIME_VOCAB)
    names.extend(f"volatility_mood_{v.lower()}" for v in _VOLATILITY_MOOD_VOCAB)
    names.extend(f"macro_direction_{v.lower()}" for v in _MACRO_DIR_VOCAB)
    names.extend(f"sector_{v}" for v in _SECTOR_VOCAB)
    return tuple(names)


def _portfolio_feature_names() -> Tuple[str, ...]:
    return (
        "portfolio_drawdown_so_far_pct",
        "settled_cash_pct",
    )


FEATURE_NAMES: Tuple[str, ...] = (
    _categorical_feature_names()
    + tuple(spec.name for spec in _NUMERIC_FEATURES)
    + _portfolio_feature_names()
)


def feature_vector_size() -> int:
    return len(FEATURE_NAMES)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def extract_state_features_dict(
    final_state: Dict[str, Any],
    *,
    ticker: str,
    trade_date: str,
    portfolio_ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
    """Return an ordered dict of feature_name -> float.

    The order MUST match :data:`FEATURE_NAMES`. Use
    :func:`extract_state_features` to get a ``numpy`` array directly.

    ``portfolio_ctx`` is the optional out-of-state portfolio snapshot used when
    the backtester has more recent values than what was injected into the
    ``AgentState`` (e.g. drawdown realized since the state was captured).
    Recognized keys: ``portfolio_drawdown_so_far_pct`` (float, 0..1),
    ``settled_cash_pct`` (float, 0..1).
    """
    if not isinstance(final_state, dict):
        final_state = {}
    portfolio_ctx = portfolio_ctx or {}

    out: Dict[str, float] = {}

    # ── Categorical one-hots ───────────────────────────────────────────────
    action = _extract_action(final_state)
    for token, val in zip(_ACTION_VOCAB, _one_hot(action, _ACTION_VOCAB)):
        out[f"action_{token.lower()}"] = val

    market_block = _get_market_block(final_state)
    regime = str(market_block.get("regime", "NO_SIGNAL"))
    for token, val in zip(_MARKET_REGIME_VOCAB, _one_hot(regime, _MARKET_REGIME_VOCAB)):
        out[f"market_regime_{token.lower()}"] = val

    vol_mood = str(market_block.get("volatility_mood", "NO_SIGNAL"))
    for token, val in zip(_VOLATILITY_MOOD_VOCAB, _one_hot(vol_mood, _VOLATILITY_MOOD_VOCAB)):
        out[f"volatility_mood_{token.lower()}"] = val

    macro_block = _get_macro_block(final_state)
    macro_dir = str(macro_block.get("composite_regime", "NO_SIGNAL"))
    for token, val in zip(_MACRO_DIR_VOCAB, _one_hot(macro_dir, _MACRO_DIR_VOCAB)):
        out[f"macro_direction_{token.lower()}"] = val

    sector = ticker_to_sector(ticker).value
    for token, val in zip(_SECTOR_VOCAB, _one_hot(sector, _SECTOR_VOCAB)):
        out[f"sector_{token}"] = val

    # ── Numeric features ────────────────────────────────────────────────────
    for spec in _NUMERIC_FEATURES:
        out[spec.name] = float(spec.extractor(final_state))

    # ── Portfolio context (callers inject) ─────────────────────────────────
    out["portfolio_drawdown_so_far_pct"] = _clip(
        _safe_float(portfolio_ctx.get("portfolio_drawdown_so_far_pct")), 0.0, 1.0
    )
    out["settled_cash_pct"] = _clip(
        _safe_float(portfolio_ctx.get("settled_cash_pct"), default=1.0), 0.0, 1.0
    )

    # Sanity check — order assertion. Cheap; protects against silent bugs.
    if tuple(out.keys()) != FEATURE_NAMES:
        # Reconstruct in canonical order; never silently drop columns.
        out = {name: out.get(name, 0.0) for name in FEATURE_NAMES}

    return out


def extract_state_features(
    final_state: Dict[str, Any],
    *,
    ticker: str,
    trade_date: str,
    portfolio_ctx: Optional[Dict[str, Any]] = None,
) -> np.ndarray:
    """Return the feature vector as a 1-D ``float32`` numpy array.

    Trade_date is accepted but not consumed by current features. It is kept on
    the signature so future revisions (e.g. day-of-week, month encoding) can
    add it without changing every call site.
    """
    feats = extract_state_features_dict(
        final_state,
        ticker=ticker,
        trade_date=trade_date,
        portfolio_ctx=portfolio_ctx,
    )
    return np.asarray([feats[name] for name in FEATURE_NAMES], dtype=np.float32)
