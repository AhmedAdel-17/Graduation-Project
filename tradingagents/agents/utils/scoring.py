"""
Unified Scoring Engine for TradingAgents
========================================
Normalizes outputs from all agents into a common scale (-1.0 to 1.0)
and aggregates them using a weighted system to produce a deterministic
trading decision (BUY / SELL / HOLD).
"""
from typing import Dict, Any, Tuple

# Weights for different analyst components
AGENT_WEIGHTS = {
    "technical": 0.40,
    "fundamental": 0.30,
    "news": 0.15,
    "social": 0.15,
}

# Thresholds for final decision
DECISION_THRESHOLDS = {
    "STRONG_BUY": 0.60,
    "BUY": 0.20,
    "SELL": -0.20,
    "STRONG_SELL": -0.60,
}

def normalize_technical_score(tech_data: Dict[str, Any]) -> float:
    """Normalize technical analysis to [-1.0, 1.0]."""
    if not tech_data:
        return 0.0
        
    signal_scores = {
        "strong buy": 1.0,
        "buy": 0.5,
        "neutral": 0.0,
        "sell": -0.5,
        "strong sell": -1.0,
        "bullish": 0.5,
        "bearish": -0.5,
        "overbought": -0.5,
        "oversold": 0.5
    }
    
    score = 0.0
    count = 0
    
    # Try different technical data structures that might exist
    signals = tech_data.get("signals", {})
    if signals:
        for ind, data in signals.items():
            if isinstance(data, dict) and "signal" in data:
                sig = data["signal"].lower()
                if sig in signal_scores:
                    score += signal_scores[sig]
                    count += 1
    
    # Check trend direction if present
    trend = tech_data.get("trend_direction", {})
    if isinstance(trend, dict) and "direction" in trend:
        d = trend["direction"].lower()
        if d in signal_scores:
            score += (signal_scores[d] * 2) # Trend carries more weight
            count += 2
            
    # Check overall signals if just a string map
    if not signals and "indicator_signals" in tech_data:
        ind_sigs = tech_data.get("indicator_signals", {})
        for k, v in ind_sigs.items():
            if isinstance(v, str) and v.lower() in signal_scores:
                score += signal_scores[v.lower()]
                count += 1
    
    return score / count if count > 0 else 0.0

def normalize_fundamental_score(fund_data: Dict[str, Any]) -> float:
    """Normalize fundamental analysis to [-1.0, 1.0]."""
    if not fund_data:
        return 0.0
        
    score = 0.0
    
    # Parse assessment dictionary string flags ("healthy", "concerning", "critical")
    health = fund_data.get("health_assessment", {})
    if health:
        status_map = {"healthy": 1.0, "concerning": -0.5, "critical": -1.0, "unknown": 0.0}
        cats = ["profitability", "leverage", "liquidity"]
        total_cat_score = 0
        cat_count = 0
        for cat in cats:
            if cat in health and isinstance(health[cat], dict):
                st = health[cat].get("status", "unknown").lower()
                total_cat_score += status_map.get(st, 0.0)
                cat_count += 1
                
        if cat_count > 0:
            score = total_cat_score / cat_count
            
    # Check overall assessment
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
        strength = strength_map.get(sent_data.get("sentiment_strength", "moderate").lower(), 0.5)
        
        if sent == "bullish":
            return strength
        elif sent == "bearish":
            return -strength
    
    return 0.0

def normalize_social_score(soc_data: Dict[str, Any]) -> float:
    """Normalize social media sentiment to [-1.0, 1.0]."""
    if not soc_data:
        return 0.0
        
    # Phase 3 Social pipeline directly provides sentiment_score [-1, 1]
    return soc_data.get("sentiment_score", 0.0)

def calculate_unified_score(state: dict) -> Tuple[str, float, str, dict]:
    """
    Calculate final unified trading decision and confidence.
    
    Returns:
        (decision, confidence, reasoning, component_scores)
    """
    tech_data = state.get("technical_analysis", {})
    fund_data = state.get("fundamental_analysis", {})
    sent_data = state.get("sentiment_analysis", {})
    soc_data = state.get("social_sentiment_analysis", {})
    
    # 1. Normalize scores
    t_score = normalize_technical_score(tech_data)
    f_score = normalize_fundamental_score(fund_data)
    n_score = normalize_sentiment_score(sent_data)
    s_score = normalize_social_score(soc_data)
    
    component_scores = {
        "technical_score": t_score,
        "fundamental_score": f_score,
        "news_score": n_score,
        "social_score": s_score
    }
    
    # 2. Extract agent confidences
    confs = {
        "tech": tech_data.get("confidence_score", 0.5) if isinstance(tech_data, dict) else 0.5,
        "fund": fund_data.get("confidence_score", 0.5) if isinstance(fund_data, dict) else 0.5,
        "news": sent_data.get("confidence_score", 50)/100.0 if isinstance(sent_data, dict) and sent_data.get("confidence_score", 50) > 1.0 else (sent_data.get("confidence_score", 0.5) if isinstance(sent_data, dict) else 0.5),
        "soc": soc_data.get("confidence", 0.5) if isinstance(soc_data, dict) else 0.5
    }
    
    # 3. Calculate weighted final score
    final_score = (
        (t_score * AGENT_WEIGHTS["technical"] * confs["tech"]) +
        (f_score * AGENT_WEIGHTS["fundamental"] * confs["fund"]) +
        (n_score * AGENT_WEIGHTS["news"] * confs["news"]) +
        (s_score * AGENT_WEIGHTS["social"] * confs["soc"])
    )
    
    # Normalize final score strictly to [-1, 1] for safety
    final_score = max(min(final_score, 1.0), -1.0)
    
    # 4. Calculate overall weighted confidence
    total_weight = sum(AGENT_WEIGHTS.values())
    overall_confidence = (
        (confs["tech"] * AGENT_WEIGHTS["technical"]) +
        (confs["fund"] * AGENT_WEIGHTS["fundamental"]) +
        (confs["news"] * AGENT_WEIGHTS["news"]) +
        (confs["soc"] * AGENT_WEIGHTS["social"])
    ) / total_weight
    
    # 5. Determine decision text
    decision = "HOLD"
    if final_score >= DECISION_THRESHOLDS["STRONG_BUY"]:
        decision = "STRONG_BUY"
    elif final_score >= DECISION_THRESHOLDS["BUY"]:
        decision = "BUY"
    elif final_score <= DECISION_THRESHOLDS["STRONG_SELL"]:
        decision = "STRONG_SELL"
    elif final_score <= DECISION_THRESHOLDS["SELL"]:
        decision = "SELL"
        
    # 6. Generate deterministic reasoning
    reasoning = (
        f"Final Score: {final_score:.2f} | Confidence: {overall_confidence:.2f}\n"
        f"Breakdown:\n"
        f"- Technical ({AGENT_WEIGHTS['technical']:.0%}): {t_score:.2f} (conf: {confs['tech']:.2f})\n"
        f"- Fundamental ({AGENT_WEIGHTS['fundamental']:.0%}): {f_score:.2f} (conf: {confs['fund']:.2f})\n"
        f"- News ({AGENT_WEIGHTS['news']:.0%}): {n_score:.2f} (conf: {confs['news']:.2f})\n"
        f"- Social ({AGENT_WEIGHTS['social']:.0%}): {s_score:.2f} (conf: {confs['soc']:.2f})\n"
        f"=> Outcome: Driven prominently by "
    )
    
    max_driver = max(component_scores, key=lambda k: abs(component_scores[k]))
    reasoning += f"{max_driver.replace('_score', '')} signals."
    
    return decision, overall_confidence, reasoning, component_scores
