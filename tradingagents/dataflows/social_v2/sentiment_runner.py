"""Bridge to the project's multi-model sentiment engine + VADER baseline.

Exposes:
    analyze_egx_batch(texts)   -> list[dict]
    analyze_vader_batch(texts) -> list[dict]

Each dict: {label: "bullish|bearish|neutral", score: float in [-1,1],
            confidence: float in [0,1], model_used: str}
"""

from __future__ import annotations

import logging
from typing import List

from tradingagents.utils.sentiment_engine import SentimentEngine, SentimentOutput

log = logging.getLogger("tradingagents.social_v2.sentiment_runner")


def _to_dict(output: SentimentOutput) -> dict:
    return {
        "label": output.label,
        "score": round(float(output.score), 4),
        "confidence": round(float(output.confidence), 4),
        "model_used": output.model_used,
    }


def analyze_egx_batch(texts: List[str]) -> List[dict]:
    if not texts:
        return []
    engine = SentimentEngine.get_instance()
    outputs = engine.analyze_batch(texts, preprocess=True, deduplicate=False)
    return [_to_dict(o) for o in outputs]


def _vader_label(compound: float) -> str:
    if compound >= 0.05:
        return "bullish"
    if compound <= -0.05:
        return "bearish"
    return "neutral"


def analyze_vader_batch(texts: List[str]) -> List[dict]:
    if not texts:
        return []
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        analyzer = SentimentIntensityAnalyzer()
    except Exception as exc:
        log.warning("VADER unavailable, returning neutral baselines: %s", exc)
        return [
            {"label": "neutral", "score": 0.0, "confidence": 0.1, "model_used": "vader_unavailable"}
            for _ in texts
        ]

    out: List[dict] = []
    for text in texts:
        try:
            scores = analyzer.polarity_scores(text or "")
            compound = float(scores.get("compound", 0.0))
        except Exception:
            compound = 0.0
        out.append(
            {
                "label": _vader_label(compound),
                "score": round(compound, 4),
                "confidence": round(min(1.0, abs(compound) + 0.1), 4),
                "model_used": "vader",
            }
        )
    return out
