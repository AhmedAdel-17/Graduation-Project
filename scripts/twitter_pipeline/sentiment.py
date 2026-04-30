"""
Sentiment module — wraps the project's production sentiment engine and
provides a VADER-based fallback when the heavyweight transformer stack
is unavailable.

Two methods are exposed for the benchmarking requirement:
  * analyze_egx(...)    — multi-model engine in tradingagents.utils
                          (FinBERT / CAMeLBERT / XLM-R + lexicon)
  * analyze_vader(...)  — pure-rule VADER baseline (English only, fast)
"""

from __future__ import annotations

import logging
import sys
import os
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("egx.sentiment")

# Make project root importable when run as a script
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ---------------------------------------------------------------------------
# 1) Project's multi-model engine
# ---------------------------------------------------------------------------

_engine = None
_engine_err = None


def _get_engine():
    global _engine, _engine_err
    if _engine is not None or _engine_err is not None:
        return _engine
    try:
        from sentiment_engine import get_engine  # project root facade
        _engine = get_engine()
        logger.info("Project sentiment engine loaded")
    except Exception as e:
        _engine_err = e
        logger.warning("Project engine unavailable, will use VADER fallback: %s", e)
    return _engine


def analyze_egx(text: str) -> Dict:
    """Score one text via the project's engine; falls back to lexicon."""
    eng = _get_engine()
    if eng is None:
        return _vader_score(text, method="egx-fallback-vader")
    try:
        out = eng.analyze(text)
        return {
            "score": float(out.score),
            "label": out.label,
            "confidence": float(out.confidence),
            "model_used": getattr(out, "model_used", "egx-engine"),
        }
    except Exception as e:
        logger.debug("egx engine analyze failed: %s — falling back", e)
        return _vader_score(text, method="egx-fallback-vader")


def analyze_egx_batch(texts: List[str]) -> List[Dict]:
    eng = _get_engine()
    if eng is None:
        return [_vader_score(t, method="egx-fallback-vader") for t in texts]
    try:
        outs = eng.analyze_batch(texts)
        return [{
            "score": float(o.score),
            "label": o.label,
            "confidence": float(o.confidence),
            "model_used": getattr(o, "model_used", "egx-engine"),
        } for o in outs]
    except Exception as e:
        logger.debug("egx engine batch failed: %s — falling back", e)
        return [_vader_score(t, method="egx-fallback-vader") for t in texts]


# ---------------------------------------------------------------------------
# 2) VADER baseline (pure rule-based, fast, English)
# ---------------------------------------------------------------------------

_vader = None


def _get_vader():
    global _vader
    if _vader is not None:
        return _vader
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        _vader = SentimentIntensityAnalyzer()
    except Exception as e:
        logger.debug("vaderSentiment not installed (%s); using lexicon", e)
        _vader = _LexiconScorer()
    return _vader


def _vader_score(text: str, method: str = "vader") -> Dict:
    sia = _get_vader()
    if hasattr(sia, "polarity_scores"):
        s = sia.polarity_scores(text or "")
        compound = float(s["compound"])
    else:
        compound = float(sia.score(text or ""))
    if compound >= 0.15:
        label = "bullish"
    elif compound <= -0.15:
        label = "bearish"
    else:
        label = "neutral"
    return {
        "score": compound,
        "label": label,
        "confidence": min(1.0, abs(compound) + 0.4),
        "model_used": method,
    }


def analyze_vader(text: str) -> Dict:
    return _vader_score(text, method="vader")


def analyze_vader_batch(texts: List[str]) -> List[Dict]:
    return [analyze_vader(t) for t in texts]


# ---------------------------------------------------------------------------
# Tiny built-in lexicon — lets the pipeline run even without VADER installed
# ---------------------------------------------------------------------------

class _LexiconScorer:
    POS = {
        "gain", "gains", "rise", "rises", "rising", "rally", "rallied",
        "surge", "surges", "soar", "soars", "jump", "jumps", "bull", "bullish",
        "beat", "beats", "strong", "growth", "record", "profit", "profits",
        "buy", "upgrade", "outperform", "breakout", "expand", "expansion",
        "positive", "improve", "improving", "exceed", "exceeded",
        # Arabic
        "ارتفاع", "صعود", "ربح", "أرباح", "تجميع", "تحسن", "نمو", "إيجابي",
        "صاعد", "قوي", "ممتاز", "جامد", "🔥",
    }
    NEG = {
        "loss", "losses", "drop", "drops", "fall", "falls", "fell", "tumble",
        "plunge", "plunges", "crash", "bear", "bearish", "sell", "selloff",
        "downgrade", "underperform", "weak", "miss", "missed", "decline",
        "declines", "negative", "warn", "warning", "concerns",
        # Arabic
        "هبوط", "تراجع", "خسارة", "خسائر", "انخفاض", "ضعيف", "بيع", "تصريف",
        "نازل", "هابط", "سلبي",
    }

    def score(self, text: str) -> float:
        if not text:
            return 0.0
        t = text.lower()
        pos = sum(1 for w in self.POS if w in t)
        neg = sum(1 for w in self.NEG if w in t)
        if pos == 0 and neg == 0:
            return 0.0
        return (pos - neg) / max(1, pos + neg)


# ---------------------------------------------------------------------------
# Aggregate distribution helper
# ---------------------------------------------------------------------------

def aggregate(results: List[Dict]) -> Dict:
    n = len(results)
    if n == 0:
        return {"n": 0, "positive_pct": 0, "neutral_pct": 0,
                "negative_pct": 0, "avg_score": 0.0}
    counts = {"bullish": 0, "neutral": 0, "bearish": 0}
    total = 0.0
    for r in results:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
        total += r["score"]
    return {
        "n": n,
        "positive_pct": round(100 * counts["bullish"] / n, 1),
        "neutral_pct": round(100 * counts["neutral"] / n, 1),
        "negative_pct": round(100 * counts["bearish"] / n, 1),
        "avg_score": round(total / n, 3),
    }
