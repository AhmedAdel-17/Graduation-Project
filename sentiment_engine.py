"""
sentiment_engine.py  (project root)
====================================
Production-grade multi-model sentiment engine for the EGX Trading System.

This module is the public entry-point.  All heavy lifting is in:
    tradingagents/utils/sentiment_engine.py   ← models + routing
    tradingagents/utils/text_preprocessor.py  ← slang, lang-detect, spam

Routing strategy
----------------
English financial text   →  FinBERT   (ProsusAI/finbert)
Arabic MSA               →  CAMeLBERT (CAMeL-Lab/bert-base-arabic-camelbert-da-sentiment)
Social / mixed / dialect →  XLM-R     (cardiffnlp/twitter-xlm-roberta-base-sentiment-multilingual)

If a model fails to load the engine degrades gracefully to a rule-based
lexicon with reduced confidence — no crashes, ever.

Quick usage
-----------
    from sentiment_engine import analyze, analyze_batch

    result = analyze("The company reported strong earnings growth")
    print(result)
    # → {'score': 0.87, 'label': 'bullish', 'confidence': 0.94}

    results = analyze_batch([
        "Apple beat earnings estimates",
        "حققت الشركة أرباحًا قياسية هذا الربع",
        "السهم ده فيه تجميع جامد 🔥",
        "COMI is going up بس في شوية تصريف",
    ])
"""

from __future__ import annotations

from typing import Dict, List, Optional, Union

# ---------------------------------------------------------------------------
# Re-export core types from the internal module
# ---------------------------------------------------------------------------
from tradingagents.utils.sentiment_engine import (
    SentimentEngine,
    SentimentOutput,
)

__all__ = [
    "SentimentEngine",
    "SentimentOutput",
    "analyze",
    "analyze_batch",
    "get_engine",
]


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def get_engine() -> SentimentEngine:
    """Return the process-wide singleton SentimentEngine."""
    return SentimentEngine.get_instance()


def analyze(
    text: str,
    language: Optional[str] = None,
    preprocess: bool = True,
) -> Dict[str, Union[float, str]]:
    """
    Analyze a single text for financial sentiment.

    Parameters
    ----------
    text : str
        Raw input — news headline, social post, mixed content, etc.
    language : str, optional
        Force language detection: ``"ar"`` | ``"en"`` | ``"mixed"``.
        Leave as ``None`` for automatic detection.
    preprocess : bool
        Run the full preprocessing pipeline (normalization, slang mapping,
        spam filter, quality filter).  Default ``True``.

    Returns
    -------
    dict with keys:
        ``score``      float in [-1.0, 1.0]  (bearish → 0 → bullish)
        ``label``      "bullish" | "bearish" | "neutral"
        ``confidence`` float in [0.0, 1.0]
        ``model_used`` which model produced this result
    """
    engine = get_engine()
    result: SentimentOutput = engine.analyze(text, language=language, preprocess=preprocess)
    return {
        "score": result.score,
        "label": result.label,
        "confidence": result.confidence,
        "model_used": result.model_used,
    }


def analyze_batch(
    texts: List[str],
    languages: Optional[List[str]] = None,
    preprocess: bool = True,
    deduplicate: bool = True,
) -> List[Dict[str, Union[float, str]]]:
    """
    Analyze a list of texts efficiently using grouped batching.

    Texts are grouped by detected language and each group is passed to
    its respective model in a single forward pass — no repeated model
    loading.

    Parameters
    ----------
    texts : list of str
    languages : list of str, optional
        Per-text language override (parallel to ``texts``).
    preprocess : bool
        Run the full preprocessing pipeline for each text.
    deduplicate : bool
        Skip near-duplicate texts (saves inference time).

    Returns
    -------
    list of dicts, one per input text, same format as :func:`analyze`.
    """
    engine = get_engine()
    outputs: List[SentimentOutput] = engine.analyze_batch(
        texts,
        languages=languages,
        preprocess=preprocess,
        deduplicate=deduplicate,
    )
    return [
        {
            "score": o.score,
            "label": o.label,
            "confidence": o.confidence,
            "model_used": o.model_used,
        }
        for o in outputs
    ]


# ---------------------------------------------------------------------------
# Standalone demo / test  (python sentiment_engine.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    TEST_CASES = [
        {
            "id": 1,
            "category": "English financial news",
            "text": "The stock price jumped 20% today after breaking revenue records.",
        },
        {
            "id": 2,
            "category": "Arabic news (MSA)",
            "text": "حققت الشركة أرباحًا قياسية هذا الربع",
        },
        {
            "id": 3,
            "category": "Egyptian social post",
            "text": "السهم ده فيه تجميع جامد 🔥",
        },
        {
            "id": 4,
            "category": "Mixed Arabic + English",
            "text": "COMI is going up بس في شوية تصريف",
        },
    ]

    print("=" * 65)
    print("  EGX Sentiment Engine — Test Suite")
    print("=" * 65)

    texts = [tc["text"] for tc in TEST_CASES]
    results = analyze_batch(texts)

    for tc, result in zip(TEST_CASES, results):
        print(f"\n[Test {tc['id']}] {tc['category']}")
        print(f"  Input  : {tc['text']}")
        print(f"  Output : {json.dumps(result, ensure_ascii=False, indent=4)}")

    print("\n" + "=" * 65)
    engine = get_engine()
    print("Model status:", engine.get_status())
    print("=" * 65)
