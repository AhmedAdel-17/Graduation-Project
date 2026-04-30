"""
Multi-Model Transformer Sentiment Engine
==========================================
Routes text to the correct transformer model based on detected language:

  English financial text  →  FinBERT      (ProsusAI/finbert)
  Arabic (MSA + dialect)  →  CAMeLBERT-DA (CAMeL-Lab/bert-base-arabic-camelbert-da-sentiment)
  Mixed / code-switch     →  XLM-R        (cardiffnlp/twitter-xlm-roberta-base-sentiment-multilingual)

Models are loaded lazily on first use and cached for the process lifetime
(singleton pattern). Batch processing groups texts by language to minimize
model switches.

If any model fails to load, the engine falls back to the existing rule-based
lexicon engine with reduced confidence.

Usage:
    from tradingagents.utils.sentiment_engine import SentimentEngine

    engine = SentimentEngine.get_instance()
    result = engine.analyze("Apple beat earnings estimates")
    # → SentimentOutput(score=0.82, label="bullish", confidence=0.91)

    results = engine.analyze_batch(["text1", "text2", ...])
"""

import logging
import threading
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict

from .text_preprocessor import (
    detect_language,
    preprocess_single,
    preprocess_batch,
    PreprocessedText,
)

logger = logging.getLogger("tradingagents.sentiment_engine")


# =============================================================================
# OUTPUT SCHEMA
# =============================================================================

@dataclass
class SentimentOutput:
    """
    Standardized sentiment output consumed by all agents.

    score:      float in [-1.0, 1.0]  (bearish ← 0 → bullish)
    label:      "bullish" | "bearish" | "neutral"
    confidence: float in [0.0, 1.0]
    model_used: which model produced this result
    """
    score: float
    label: str
    confidence: float
    model_used: str = "unknown"

    def to_dict(self) -> Dict:
        return asdict(self)


# =============================================================================
# LABEL MAPPING
# =============================================================================
# Each model outputs different label sets. We normalize them all to
# bullish / bearish / neutral with a score in [-1, 1].

def _normalize_finbert(label: str, prob: float) -> Tuple[str, float]:
    """FinBERT outputs: positive, negative, neutral."""
    mapping = {"positive": "bullish", "negative": "bearish", "neutral": "neutral"}
    score_sign = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}
    normalized_label = mapping.get(label.lower(), "neutral")
    score = score_sign.get(label.lower(), 0.0) * prob
    return normalized_label, score


def _normalize_camelbert(label: str, prob: float) -> Tuple[str, float]:
    """CAMeLBERT-DA outputs: positive, negative, neutral."""
    mapping = {"positive": "bullish", "negative": "bearish", "neutral": "neutral"}
    score_sign = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}
    normalized_label = mapping.get(label.lower(), "neutral")
    score = score_sign.get(label.lower(), 0.0) * prob
    return normalized_label, score


def _normalize_xlmr(label: str, prob: float) -> Tuple[str, float]:
    """
    XLM-R sentiment-multilingual outputs: positive, negative, neutral.
    Some checkpoints use Positive/Negative/Neutral capitalized.
    """
    label_lower = label.lower()
    mapping = {"positive": "bullish", "negative": "bearish", "neutral": "neutral"}
    score_sign = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}
    normalized_label = mapping.get(label_lower, "neutral")
    score = score_sign.get(label_lower, 0.0) * prob
    return normalized_label, score


# =============================================================================
# RULE-BASED FALLBACK
# =============================================================================
# Lightweight fallback if transformer models can't load.
# Uses the existing lexicon from social_media_sources.sentiment_engine.

def _rule_based_fallback(text: str, language: str) -> SentimentOutput:
    """
    Fallback sentiment using keyword lexicon.
    Returns with reduced confidence (max 0.45) to signal lower reliability.
    """
    try:
        from tradingagents.dataflows.social_media_sources.sentiment_engine import (
            _score_arabic_text,
            _score_english_text,
        )

        if language == "ar":
            score, _ = _score_arabic_text(text)
        elif language == "en":
            score, _ = _score_english_text(text)
        else:
            ar_score, _ = _score_arabic_text(text)
            en_score, _ = _score_english_text(text)
            score = (ar_score + en_score) / 2.0

        # Determine label
        if score > 0.1:
            label = "bullish"
        elif score < -0.1:
            label = "bearish"
        else:
            label = "neutral"

        # Cap confidence — rule-based is less reliable
        confidence = min(0.45, abs(score) * 0.6 + 0.15)

        return SentimentOutput(
            score=round(max(-1.0, min(1.0, score)), 4),
            label=label,
            confidence=round(confidence, 4),
            model_used="rule_based_fallback",
        )
    except Exception as e:
        logger.warning("Rule-based fallback also failed: %s", e)
        return SentimentOutput(
            score=0.0, label="neutral", confidence=0.1, model_used="fallback_error"
        )


# =============================================================================
# SENTINEL FOR NEUTRAL WHEN NO VALID TEXT
# =============================================================================

_NEUTRAL_NO_DATA = SentimentOutput(
    score=0.0, label="neutral", confidence=0.1, model_used="no_valid_input"
)


# =============================================================================
# MAIN ENGINE (SINGLETON)
# =============================================================================

class SentimentEngine:
    """
    Singleton sentiment analysis engine with lazy model loading.

    Thread-safe: models are loaded once under a lock and reused.
    """

    _instance: Optional["SentimentEngine"] = None
    _lock = threading.Lock()

    # Model HuggingFace IDs
    FINBERT_MODEL = "ProsusAI/finbert"
    CAMELBERT_MODEL = "CAMeL-Lab/bert-base-arabic-camelbert-da-sentiment"
    XLMR_MODEL = "cardiffnlp/twitter-xlm-roberta-base-sentiment-multilingual"

    def __init__(self):
        self._models: Dict[str, object] = {}
        self._model_status: Dict[str, str] = {
            "finbert": "not_loaded",
            "camelbert": "not_loaded",
            "xlmr": "not_loaded",
        }
        self._load_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "SentimentEngine":
        """Get or create the singleton engine instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Reset singleton (for testing)."""
        with cls._lock:
            cls._instance = None

    # -----------------------------------------------------------------
    # Model loading
    # -----------------------------------------------------------------

    def _load_model(self, model_key: str) -> bool:
        """
        Load a model pipeline by key. Returns True on success.

        Uses transformers.pipeline for simple, efficient inference.
        """
        if self._model_status[model_key] == "loaded":
            return True

        if self._model_status[model_key] == "failed":
            return False

        with self._load_lock:
            # Double-check after acquiring lock
            if self._model_status[model_key] == "loaded":
                return True
            if self._model_status[model_key] == "failed":
                return False

            try:
                from transformers import pipeline as hf_pipeline

                model_ids = {
                    "finbert": self.FINBERT_MODEL,
                    "camelbert": self.CAMELBERT_MODEL,
                    "xlmr": self.XLMR_MODEL,
                }
                model_id = model_ids[model_key]

                logger.info("Loading sentiment model: %s (%s)", model_key, model_id)

                pipe = hf_pipeline(
                    "sentiment-analysis",
                    model=model_id,
                    tokenizer=model_id,
                    truncation=True,
                    max_length=512,
                )

                self._models[model_key] = pipe
                self._model_status[model_key] = "loaded"
                logger.info("Successfully loaded %s", model_key)
                return True

            except Exception as e:
                logger.error("Failed to load model %s: %s", model_key, e)
                self._model_status[model_key] = "failed"
                return False

    def _select_model(self, language: str) -> str:
        """Select the best model key for a given language."""
        if language == "en":
            return "finbert"
        elif language == "ar":
            return "camelbert"
        else:
            return "xlmr"

    def _get_normalizer(self, model_key: str):
        """Get the label normalizer function for a model."""
        normalizers = {
            "finbert": _normalize_finbert,
            "camelbert": _normalize_camelbert,
            "xlmr": _normalize_xlmr,
        }
        return normalizers[model_key]

    # -----------------------------------------------------------------
    # Single text analysis
    # -----------------------------------------------------------------

    def analyze(
        self,
        text: str,
        language: Optional[str] = None,
        preprocess: bool = True,
    ) -> SentimentOutput:
        """
        Analyze a single text for financial sentiment.

        Args:
            text: Input text (news headline, social post, etc.)
            language: Override language detection ("ar", "en", "mixed").
                      If None, auto-detected.
            preprocess: Whether to run the preprocessing pipeline first.

        Returns:
            SentimentOutput with score, label, confidence, and model_used.
        """
        if not text or not text.strip():
            return _NEUTRAL_NO_DATA

        # Preprocess
        if preprocess:
            processed = preprocess_single(text, check_spam=False)
            clean_text = processed.cleaned
            lang = language or processed.language
        else:
            clean_text = text
            lang = language or detect_language(text)

        if lang == "unknown":
            lang = "en"  # Default to English for unknown

        # Select and load model
        model_key = self._select_model(lang)
        model_loaded = self._load_model(model_key)

        if not model_loaded:
            # Try XLM-R as universal fallback
            if model_key != "xlmr":
                logger.warning(
                    "Primary model %s unavailable, trying XLM-R fallback", model_key
                )
                model_key = "xlmr"
                model_loaded = self._load_model("xlmr")

            if not model_loaded:
                logger.warning("All transformer models unavailable, using rule-based fallback")
                return _rule_based_fallback(clean_text, lang)

        # Run inference
        try:
            pipe = self._models[model_key]
            result = pipe(clean_text)[0]

            raw_label = result["label"]
            raw_score = result["score"]

            normalizer = self._get_normalizer(model_key)
            label, score = normalizer(raw_label, raw_score)

            return SentimentOutput(
                score=round(score, 4),
                label=label,
                confidence=round(raw_score, 4),
                model_used=model_key,
            )

        except Exception as e:
            logger.error("Inference failed for %s: %s", model_key, e)
            return _rule_based_fallback(clean_text, lang)

    # -----------------------------------------------------------------
    # Batch analysis
    # -----------------------------------------------------------------

    def analyze_batch(
        self,
        texts: List[str],
        languages: Optional[List[str]] = None,
        preprocess: bool = True,
        deduplicate: bool = True,
    ) -> List[SentimentOutput]:
        """
        Analyze a batch of texts efficiently.

        Groups texts by detected language and processes each group
        through its respective model in a single batch call.

        Args:
            texts: List of input texts.
            languages: Optional per-text language overrides.
            preprocess: Whether to run preprocessing (dedup, filter, normalize).
            deduplicate: Whether to remove duplicates (only if preprocess=True).

        Returns:
            List of SentimentOutput, one per input text.
            Filtered/duplicate texts get neutral output with low confidence.
        """
        if not texts:
            return []

        n = len(texts)
        results: List[Optional[SentimentOutput]] = [None] * n

        # Map from original index to preprocessed data
        index_map: Dict[int, PreprocessedText] = {}

        if preprocess:
            # Preprocess individually to preserve index mapping
            seen_fingerprints = set()
            for i, text in enumerate(texts):
                processed = preprocess_single(text, check_spam=True)

                # Dedup check
                if deduplicate:
                    import re as _re
                    fp = _re.sub(r"\s+", " ", text.lower().strip())[:200]
                    if fp in seen_fingerprints:
                        results[i] = SentimentOutput(
                            score=0.0, label="neutral", confidence=0.05,
                            model_used="deduplicated",
                        )
                        continue
                    seen_fingerprints.add(fp)

                if not processed.is_valid:
                    results[i] = SentimentOutput(
                        score=0.0, label="neutral", confidence=0.05,
                        model_used=f"filtered:{processed.rejection_reason}",
                    )
                    continue

                if languages and i < len(languages) and languages[i]:
                    processed = PreprocessedText(
                        original=processed.original,
                        cleaned=processed.cleaned,
                        language=languages[i],
                        is_valid=True,
                    )

                index_map[i] = processed
        else:
            for i, text in enumerate(texts):
                lang = (languages[i] if languages and i < len(languages) else None) or detect_language(text)
                if lang == "unknown":
                    lang = "en"
                index_map[i] = PreprocessedText(
                    original=text, cleaned=text, language=lang, is_valid=True
                )

        # Group by language for efficient batching
        lang_groups: Dict[str, List[Tuple[int, str]]] = {}
        for i, processed in index_map.items():
            lang = processed.language if processed.language != "unknown" else "en"
            if lang not in lang_groups:
                lang_groups[lang] = []
            lang_groups[lang].append((i, processed.cleaned))

        # Process each language group
        for lang, items in lang_groups.items():
            model_key = self._select_model(lang)
            model_loaded = self._load_model(model_key)

            # Fallback chain
            if not model_loaded and model_key != "xlmr":
                model_key = "xlmr"
                model_loaded = self._load_model("xlmr")

            if not model_loaded:
                # Rule-based fallback for entire group
                for idx, text in items:
                    results[idx] = _rule_based_fallback(text, lang)
                continue

            # Batch inference
            try:
                pipe = self._models[model_key]
                batch_texts = [text for _, text in items]
                batch_results = pipe(batch_texts, batch_size=min(32, len(batch_texts)))

                normalizer = self._get_normalizer(model_key)

                for (idx, _), raw in zip(items, batch_results):
                    label, score = normalizer(raw["label"], raw["score"])
                    results[idx] = SentimentOutput(
                        score=round(score, 4),
                        label=label,
                        confidence=round(raw["score"], 4),
                        model_used=model_key,
                    )

            except Exception as e:
                logger.error("Batch inference failed for %s: %s", model_key, e)
                for idx, text in items:
                    results[idx] = _rule_based_fallback(text, lang)

        # Fill any remaining None slots (shouldn't happen, but defensive)
        for i in range(n):
            if results[i] is None:
                results[i] = _NEUTRAL_NO_DATA

        return results

    # -----------------------------------------------------------------
    # Aggregation helpers
    # -----------------------------------------------------------------

    @staticmethod
    def aggregate_scores(
        outputs: List[SentimentOutput],
        weights: Optional[List[float]] = None,
    ) -> SentimentOutput:
        """
        Aggregate multiple SentimentOutputs into a single weighted score.

        Args:
            outputs: List of individual sentiment outputs.
            weights: Optional weights (e.g., recency-based). If None,
                     each output is weighted by its own confidence.

        Returns:
            Aggregated SentimentOutput.
        """
        if not outputs:
            return _NEUTRAL_NO_DATA

        if len(outputs) == 1:
            return outputs[0]

        # Use confidence as default weight
        if weights is None:
            weights = [o.confidence for o in outputs]

        total_weight = sum(weights)
        if total_weight == 0:
            return _NEUTRAL_NO_DATA

        # Weighted average score
        weighted_score = sum(
            o.score * w for o, w in zip(outputs, weights)
        ) / total_weight

        # Average confidence
        avg_confidence = sum(o.confidence for o in outputs) / len(outputs)

        # Boost confidence if signals agree
        bullish_count = sum(1 for o in outputs if o.label == "bullish")
        bearish_count = sum(1 for o in outputs if o.label == "bearish")
        total = len(outputs)

        agreement_ratio = max(bullish_count, bearish_count) / total
        if agreement_ratio >= 0.7:
            avg_confidence = min(1.0, avg_confidence * 1.15)

        # Determine label
        if weighted_score > 0.1:
            label = "bullish"
        elif weighted_score < -0.1:
            label = "bearish"
        else:
            label = "neutral"

        # Collect model names
        models_used = list(set(o.model_used for o in outputs))
        model_str = "+".join(models_used)

        return SentimentOutput(
            score=round(max(-1.0, min(1.0, weighted_score)), 4),
            label=label,
            confidence=round(min(1.0, avg_confidence), 4),
            model_used=f"aggregated({model_str})",
        )

    # -----------------------------------------------------------------
    # Status / diagnostics
    # -----------------------------------------------------------------

    def get_status(self) -> Dict[str, str]:
        """Return load status of all models."""
        return dict(self._model_status)
