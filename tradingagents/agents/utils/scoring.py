"""
Unified Scoring Engine for TradingAgents
=========================================
Normalizes outputs from all agents into a common scale (-1.0 to 1.0)
and aggregates them using a confidence-weighted mean with a quorum rule.

Phase 3 sentiment redesign principles:
- Sentiment NEVER changes the directional score — only confidence × and position size ×.
- ≥2 non-absent directional analysts required for a valid decision (quorum rule).
- Social/sentiment layers are confidence/size modifiers, not directional contributors.
- The blender (Layer E) applies market-regime, macro-direction, and sector-tilt
  multipliers after the directional score is fixed.
"""
from __future__ import annotations

from typing import Any, Dict, NamedTuple, Optional, Tuple

# ---------------------------------------------------------------------------
# Weights used in confidence-weighted mean
# ---------------------------------------------------------------------------
AGENT_WEIGHTS: Dict[str, float] = {
    "technical":   0.40,
    "fundamental": 0.30,
    "news":        0.15,
    "social":      0.15,
}

# Thresholds for final BUY / SELL decision
DECISION_THRESHOLDS: Dict[str, float] = {
    "STRONG_BUY":  0.60,
    "BUY":         0.20,
    "SELL":       -0.20,
    "STRONG_SELL": -0.60,
}

# Minimum active (non-absent) directional analysts for a valid quorum
QUORUM_MINIMUM: int = 2


# ---------------------------------------------------------------------------
# Layer E: Sentiment blender
# ---------------------------------------------------------------------------

class SentimentBlend(NamedTuple):
    """Output of the Layer E blender.

    Carries multiplicative modifiers only — never a directional signal.
    Confidence and position-size multipliers are applied downstream by the
    propagator and trader; they never alter BUY/SELL/HOLD.
    """
    confidence_multiplier: float     # multiply per-analyst confidence by this
    position_size_multiplier: float  # multiply position size recommendation by this
    audit: str                       # canonical string for logs / agent context / audit


# Multiplier tables (populated lazily to avoid circular imports at module load)
_MARKET_REGIME_MULT: Dict[Any, Tuple[float, float]] = {}
_MACRO_DIRECTION_CONF_MULT: Dict[Any, float] = {}


def _init_mult_tables() -> None:
    global _MARKET_REGIME_MULT, _MACRO_DIRECTION_CONF_MULT
    if _MARKET_REGIME_MULT:
        return
    from tradingagents.sentiment.contracts import MacroDirection, MarketRegime

    # (confidence_multiplier, position_size_multiplier)
    _MARKET_REGIME_MULT = {
        MarketRegime.PANIC:     (0.70, 0.50),
        MarketRegime.FEAR:      (0.85, 0.75),
        MarketRegime.NEUTRAL:   (1.00, 1.00),
        MarketRegime.GREED:     (0.90, 0.90),
        MarketRegime.EUPHORIA:  (0.70, 0.60),
        MarketRegime.NO_SIGNAL: (1.00, 1.00),
    }
    # (confidence_multiplier, position_size_multiplier)
    # RISK_ON = 1.00: Egypt's post-2016 inflation-hedge regime means negative
    # real yields are equity-supportive, not risk-adverse.  Ablation (May 2026)
    # confirmed the 0.95 haircut was directionally wrong.
    # RISK_OFF = (0.80, 0.80): high real yields + rate shock pull capital into
    # fixed income; reduce both confidence and position size.
    _MACRO_DIRECTION_CONF_MULT = {
        MacroDirection.RISK_OFF:  (0.80, 0.80),
        MacroDirection.RISK_ON:   (1.00, 1.00),
        MacroDirection.NEUTRAL:   (1.00, 1.00),
        MacroDirection.NO_SIGNAL: (1.00, 1.00),
    }


def blend_sentiment(
    macro=None,
    market=None,
    sector=None,
) -> SentimentBlend:
    """Compute blended confidence/position-size multipliers from sentiment layers.

    All parameters accept typed objects from ``tradingagents.sentiment`` or ``None``.
    ``None`` is treated as NO_SIGNAL / pass-through (multiplier = 1.0).
    Sentiment never flips direction.

    Multiplier cascade (applied in order):
    1. Market regime   → scales both confidence and position size
    2. Macro direction → scales confidence only
    3. Sector tilt     → additive ±0.10 cap on confidence multiplier
    """
    _init_mult_tables()

    conf_mult = 1.0
    size_mult = 1.0
    parts: list[str] = []

    # ── Market regime ──────────────────────────────────────────────────────
    if market is not None:
        # LayerStatus extends str, so "SIGNAL" comparison works for both enum and str
        status = getattr(market, "status", None)
        if status is not None and str(status).upper().endswith("SIGNAL") and "NO_" not in str(status):
            regime = getattr(market, "regime", None)
            if regime is not None:
                c, s = _MARKET_REGIME_MULT.get(regime, (1.00, 1.00))
                conf_mult *= c
                size_mult *= s
                regime_val = regime.value if hasattr(regime, "value") else str(regime)
                parts.append(f"market={regime_val}(conf×{c:.2f},size×{s:.2f})")
            else:
                parts.append("market=signal(regime_unknown,pass-through)")
        else:
            reason = getattr(getattr(market, "reason", None), "gate_failed", "gate")
            parts.append(f"market=NO_SIGNAL(gate={reason},pass-through)")
    else:
        parts.append("market=absent(pass-through)")

    # ── Macro direction ────────────────────────────────────────────────────
    if macro is not None:
        from tradingagents.sentiment.contracts import MacroDirection
        direction = getattr(macro, "composite_regime", MacroDirection.NO_SIGNAL)
        c, s = _MACRO_DIRECTION_CONF_MULT.get(direction, (1.00, 1.00))
        conf_mult *= c
        size_mult *= s
        dir_val = direction.value if hasattr(direction, "value") else str(direction)
        if c != 1.0 or s != 1.0:
            parts.append(f"macro={dir_val}(conf×{c:.2f},size×{s:.2f})")
        else:
            parts.append(f"macro={dir_val}(pass-through)")
    else:
        parts.append("macro=absent(pass-through)")

    # ── Sector tilt (additive ±0.10 cap) ──────────────────────────────────
    if sector is not None:
        status = getattr(sector, "status", None)
        if status is not None and str(status).upper().endswith("SIGNAL") and "NO_" not in str(status):
            raw_score = getattr(sector, "score", None)
            score = float(raw_score) if raw_score is not None else 0.0
            tilt = max(-0.10, min(0.10, score * 0.10))
            # Allow conf_mult to exceed 1.0 for positive sector tilt;
            # downstream callers clamp blended_conf to [0.10, 1.0].
            conf_mult = max(0.10, conf_mult + tilt)
            sector_name = getattr(sector, "sector", "unknown")
            parts.append(f"sector={sector_name}(tilt{tilt:+.3f})")
        else:
            parts.append("sector=NO_SIGNAL(pass-through)")
    else:
        parts.append("sector=absent(pass-through)")

    audit = (
        "blend: "
        + "; ".join(parts)
        + f" => conf×{conf_mult:.4f}, size×{size_mult:.4f}"
    )
    return SentimentBlend(
        confidence_multiplier=round(conf_mult, 4),
        position_size_multiplier=round(size_mult, 4),
        audit=audit,
    )


# ---------------------------------------------------------------------------
# Phase 3 wiring: build typed sentiment inputs from new analyst state dicts
# ---------------------------------------------------------------------------

def _macro_input_from_state(state: dict):
    """Construct a duck-typed macro input for blend_sentiment from macro_analysis.

    The macro analyst (Phase 2.1) emits ``state["macro_analysis"]["composite_direction"]``
    as one of "RISK_ON" / "RISK_OFF" / "NEUTRAL". We map that to the existing
    MacroDirection enum so blend_sentiment's multiplier table applies cleanly.
    Returns None when the analyst was not run.
    """
    macro = state.get("macro_analysis") or {}
    direction_str = macro.get("composite_direction")
    if not direction_str:
        return None
    try:
        from tradingagents.sentiment.contracts import MacroDirection
        direction = MacroDirection(direction_str)
    except (ValueError, ImportError):
        return None

    class _MacroIn:
        composite_regime = direction
    return _MacroIn()


def _market_input_from_state(state: dict):
    """Construct a duck-typed market input for blend_sentiment from regime_analysis.

    The regime analyst (Phase 2.3) emits ``state["regime_analysis"]["market_regime_enum"]``
    as "PANIC" / "FEAR" / "NEUTRAL" / "GREED". We map that to MarketRegime so
    the existing market-regime multiplier table applies.
    Returns None when the analyst was not run.
    """
    regime = state.get("regime_analysis") or {}
    enum_str = regime.get("market_regime_enum")
    if not enum_str:
        return None
    try:
        from tradingagents.sentiment.contracts import LayerStatus, MarketRegime
        market_regime = MarketRegime(enum_str)
    except (ValueError, ImportError):
        return None

    class _MarketIn:
        status = LayerStatus.SIGNAL
        regime = market_regime
    return _MarketIn()


# Liquidity confidence haircut — applied post-blend, never directional.
_LIQUIDITY_CONF_HAIRCUT = {
    "SEVERELY_ILLIQUID": 0.60,
    "ILLIQUID":          0.80,
    "MODERATE_CONCERN":  0.95,
    "ADEQUATE":          1.00,
}


def _liquidity_haircut(state: dict) -> Tuple[float, str]:
    """Return (multiplier, audit_string) for liquidity confidence haircut.

    The Liquidity Analyst (Phase 2.2) does NOT flow through blend_sentiment.
    Instead, severe illiquidity reduces confidence directly. This is a
    risk-style modifier, not a sentiment one.
    """
    liq = state.get("liquidity_analysis") or {}
    cls = liq.get("liquidity_classification")
    if not cls:
        return 1.0, "liquidity=absent(pass-through)"
    mult = _LIQUIDITY_CONF_HAIRCUT.get(cls, 1.00)
    if mult == 1.0:
        return 1.0, f"liquidity={cls}(pass-through)"
    return mult, f"liquidity={cls}(conf×{mult:.2f})"


def blend_from_dict(blend_dict: Optional[Dict[str, Any]]) -> Optional[SentimentBlend]:
    """Reconstruct a SentimentBlend from its serialised dict form stored in AgentState.

    Returns None only when blend_dict is None. An empty dict produces a
    pass-through blend (confidence_multiplier=1.0, position_size_multiplier=1.0).
    """
    if blend_dict is None:
        return None
    try:
        return SentimentBlend(
            confidence_multiplier=float(blend_dict.get("confidence_multiplier", 1.0)),
            position_size_multiplier=float(blend_dict.get("position_size_multiplier", 1.0)),
            audit=str(blend_dict.get("audit", "blend:deserialized")),
        )
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Score normalizers (unchanged)
# ---------------------------------------------------------------------------

def normalize_technical_score(tech_data: Dict[str, Any]) -> float:
    """Normalize technical analysis to [-1.0, 1.0]."""
    if not tech_data:
        return 0.0

    signal_scores = {
        "strong buy":  1.0,
        "buy":         0.5,
        "neutral":     0.0,
        "sell":       -0.5,
        "strong sell": -1.0,
        "bullish":     0.5,
        "bearish":    -0.5,
        "overbought": -0.5,
        "oversold":    0.5,
    }

    score = 0.0
    count = 0

    signals = tech_data.get("signals", {})
    if signals:
        for _ind, data in signals.items():
            if isinstance(data, dict) and "signal" in data:
                sig = data["signal"].lower()
                if sig in signal_scores:
                    score += signal_scores[sig]
                    count += 1

    trend = tech_data.get("trend_direction", {})
    if isinstance(trend, dict) and "direction" in trend:
        d = trend["direction"].lower()
        if d in signal_scores:
            score += signal_scores[d] * 2  # trend carries more weight
            count += 2

    if not signals and "indicator_signals" in tech_data:
        ind_sigs = tech_data.get("indicator_signals", {})
        for _k, v in ind_sigs.items():
            if isinstance(v, str) and v.lower() in signal_scores:
                score += signal_scores[v.lower()]
                count += 1

    return score / count if count > 0 else 0.0


def normalize_fundamental_score(fund_data: Dict[str, Any]) -> float:
    """Normalize fundamental analysis to [-1.0, 1.0]."""
    if not fund_data:
        return 0.0

    score = 0.0

    health = fund_data.get("health_assessment", {})
    if health:
        status_map = {"healthy": 1.0, "concerning": -0.5, "critical": -1.0, "unknown": 0.0}
        cats = ["profitability", "leverage", "liquidity"]
        total_cat_score = 0.0
        cat_count = 0
        for cat in cats:
            if cat in health and isinstance(health[cat], dict):
                st = health[cat].get("status", "unknown").lower()
                total_cat_score += status_map.get(st, 0.0)
                cat_count += 1
        if cat_count > 0:
            score = total_cat_score / cat_count

    if "overall_assessment" in fund_data:
        ov = fund_data["overall_assessment"].lower()
        if "strong" in ov or "excellent" in ov:
            score = max(score, 0.8)
        elif "poor" in ov or "weak" in ov:
            score = min(score, -0.8)

    return score


def normalize_sentiment_score(sent_data: Dict[str, Any]) -> float:
    """Normalize news sentiment to [-1.0, 1.0]."""
    if not sent_data:
        return 0.0

    if "sentiment" in sent_data:
        sent = sent_data["sentiment"].lower()
        strength_map = {"strong": 1.0, "moderate": 0.6, "weak": 0.3}
        strength = strength_map.get(
            sent_data.get("sentiment_strength", "moderate").lower(), 0.5
        )
        if sent == "bullish":
            return strength
        elif sent == "bearish":
            return -strength

    return 0.0


def normalize_social_score(soc_data: Dict[str, Any]) -> float:
    """Normalize social media sentiment to [-1.0, 1.0].

    NOTE: in the Phase 3 redesign social is a *modifier*, not a directional analyst.
    This function is retained for backward-compatibility with callers that still
    pass social data to ``calculate_unified_score``. It is excluded from the quorum
    count and from the directional weighted mean.
    """
    if not soc_data:
        return 0.0
    return soc_data.get("sentiment_score", 0.0)


# ---------------------------------------------------------------------------
# Confidence extractor helper
# ---------------------------------------------------------------------------

def _extract_confidence(d: Optional[Dict[str, Any]], key: str = "confidence_score") -> Optional[float]:
    """Return confidence in [0, 1] from an analyst output dict, or None if absent."""
    if not d or not isinstance(d, dict):
        return None
    raw = d.get(key)
    if raw is None:
        return None
    try:
        v = float(raw)
        return v / 100.0 if v > 1.0 else max(0.0, min(1.0, v))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Main scoring entry point
# ---------------------------------------------------------------------------

def calculate_unified_score(
    state: dict,
    sentiment_blend: Optional[SentimentBlend] = None,
) -> Tuple[str, float, str, dict, str]:
    """Calculate the final unified trading decision and blended confidence.

    Parameters
    ----------
    state:
        AgentState dict containing ``technical_analysis``, ``fundamental_analysis``,
        ``sentiment_analysis`` (news), and optionally ``social_sentiment_analysis``.
        If ``state`` contains a ``sentiment_blend_result`` dict, it is used as the
        blend unless a typed ``sentiment_blend`` is passed explicitly.
    sentiment_blend:
        Pre-computed ``SentimentBlend`` from ``blend_sentiment()``. Takes priority
        over anything in ``state["sentiment_blend_result"]``.

    Returns
    -------
    (decision, blended_confidence, reasoning, component_scores, overall_status)
        ``overall_status`` is ``"OK"`` or ``"INSUFFICIENT_DATA"``.
        When ``"INSUFFICIENT_DATA"`` the caller should treat the decision as ``HOLD``
        and propagate the status to agents / audit logs.
    """
    # ── Collect per-analyst data ───────────────────────────────────────────
    tech_data  = state.get("technical_analysis") or {}
    fund_data  = state.get("fundamental_analysis") or {}
    sent_data  = state.get("sentiment_analysis") or {}

    # ── Directional scores for the 3 analytical analysts ──────────────────
    t_score = normalize_technical_score(tech_data)    if tech_data else None
    f_score = normalize_fundamental_score(fund_data)  if fund_data else None
    n_score = normalize_sentiment_score(sent_data)    if sent_data else None

    # ── Per-analyst confidence (0-1 normalised) ────────────────────────────
    t_conf = _extract_confidence(tech_data)  if tech_data else None
    f_conf = _extract_confidence(fund_data)  if fund_data else None
    n_conf = (
        _extract_confidence(sent_data)
        if sent_data
        else None
    )
    # news can store confidence under "confidence_score" or inside combined_sentiment
    if n_conf is None and isinstance(sent_data, dict):
        combined = sent_data.get("combined_sentiment") or {}
        n_conf = _extract_confidence(combined, "confidence")

    # ── Quorum: require ≥2 directional analysts with non-None scores ───────
    directional = [
        ("technical",   t_score, t_conf,  AGENT_WEIGHTS["technical"]),
        ("fundamental", f_score, f_conf,  AGENT_WEIGHTS["fundamental"]),
        ("news",        n_score, n_conf,  AGENT_WEIGHTS["news"]),
    ]
    active = [
        (name, score, conf, w)
        for name, score, conf, w in directional
        if score is not None
    ]

    if len(active) < QUORUM_MINIMUM:
        missing = [name for name, s, _, _ in directional if s is None]
        reason_str = (
            f"INSUFFICIENT_DATA: only {len(active)} of {len(directional)} "
            f"directional analysts active (need ≥{QUORUM_MINIMUM}); "
            f"absent: {missing}"
        )
        return (
            "HOLD",
            0.0,
            reason_str,
            {name: score for name, score, _, _ in directional},
            "INSUFFICIENT_DATA",
        )

    # ── Confidence-weighted mean of directional scores ─────────────────────
    # Weight = analyst structural weight × analyst confidence (or 0.50 default)
    eff_weights = [
        w * (conf if conf is not None else 0.50)
        for _, _, conf, w in active
    ]
    total_eff_weight = sum(eff_weights)

    if total_eff_weight > 0:
        weighted_score = sum(
            s * ew for (_, s, _, _), ew in zip(active, eff_weights)
        ) / total_eff_weight
    else:
        # Degenerate: equal-weight fallback
        weighted_score = sum(s for _, s, _, _ in active) / len(active)

    weighted_score = max(-1.0, min(1.0, weighted_score))

    # ── Unblended analyst confidence (structural-weight average) ──────────
    structural_weight_sum = sum(w for _, _, _, w in active)
    unblended_conf = (
        sum(
            w * (conf if conf is not None else 0.50)
            for _, _, conf, w in active
        ) / structural_weight_sum
    ) if structural_weight_sum > 0 else 0.50

    # ── Resolve sentiment blend ────────────────────────────────────────────
    # 1. Start with any pre-existing blend (e.g. from social sentiment pipeline)
    if sentiment_blend is None:
        raw_blend = state.get("sentiment_blend_result")
        if raw_blend:
            sentiment_blend = blend_from_dict(raw_blend)

    # 2. Always layer macro/regime on top — even when a prior blend exists.
    #    Macro/regime are non-directional modifiers that stack multiplicatively.
    #    They NEVER flip BUY/SELL/HOLD direction.
    macro_in  = _macro_input_from_state(state)
    market_in = _market_input_from_state(state)
    macro_regime_blend = None
    if macro_in is not None or market_in is not None:
        macro_regime_blend = blend_sentiment(macro=macro_in, market=market_in)

    # 3. Combine: multiply through both blend layers
    blended_conf = unblended_conf
    pos_size_mult = 1.0
    audit_parts: list[str] = []

    if sentiment_blend is not None:
        blended_conf *= sentiment_blend.confidence_multiplier
        pos_size_mult *= sentiment_blend.position_size_multiplier
        audit_parts.append(f"prior_blend({sentiment_blend.audit})")

    if macro_regime_blend is not None:
        blended_conf *= macro_regime_blend.confidence_multiplier
        pos_size_mult *= macro_regime_blend.position_size_multiplier
        audit_parts.append(f"macro_regime({macro_regime_blend.audit})")

    blended_conf = min(1.0, max(0.10, blended_conf))

    if not audit_parts:
        blend_audit = "no_blend"
    else:
        blend_audit = " + ".join(audit_parts)

    # 4. Liquidity haircut (separate from sentiment blend by design)
    liq_mult, liq_audit = _liquidity_haircut(state)
    if liq_mult != 1.0:
        blended_conf = min(1.0, max(0.10, blended_conf * liq_mult))
        if liq_mult <= 0.80:
            pos_size_mult *= liq_mult
    blend_audit = f"{blend_audit} | {liq_audit}"

    # ── Decision thresholds ────────────────────────────────────────────────
    decision = "HOLD"
    if weighted_score >= DECISION_THRESHOLDS["STRONG_BUY"]:
        decision = "STRONG_BUY"
    elif weighted_score >= DECISION_THRESHOLDS["BUY"]:
        decision = "BUY"
    elif weighted_score <= DECISION_THRESHOLDS["STRONG_SELL"]:
        decision = "STRONG_SELL"
    elif weighted_score <= DECISION_THRESHOLDS["SELL"]:
        decision = "SELL"

    # ── Component scores dict (includes absent analysts as None) ───────────
    component_scores: Dict[str, Any] = {
        name: score for name, score, _, _ in directional
    }
    component_scores["position_size_multiplier"] = pos_size_mult

    # ── Reasoning ─────────────────────────────────────────────────────────
    active_lines = "\n".join(
        f"  - {name} (w={w:.2f}): score={s:.3f}, conf={conf if conf is not None else 'n/a'}"
        for name, s, conf, w in active
    )
    absent_names = [name for name, s, _, _ in directional if s is None]
    reasoning = (
        f"Score: {weighted_score:.3f} | Conf (raw): {unblended_conf:.3f} | "
        f"Conf (blended): {blended_conf:.3f} | PosSize×: {pos_size_mult:.3f}\n"
        f"Quorum: {len(active)}/{len(directional)} analysts active"
        + (f" (absent: {absent_names})" if absent_names else "")
        + f"\nActive analysts:\n{active_lines}\n"
        f"Blend: {blend_audit}"
    )

    return decision, blended_conf, reasoning, component_scores, "OK"
