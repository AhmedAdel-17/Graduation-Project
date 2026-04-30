"""
Text Preprocessing Pipeline for Sentiment Analysis
====================================================
Cleans, filters, and normalizes text BEFORE any model inference.

Pipeline order:
  1. Normalize text (Unicode, whitespace, Arabic char variants)
  2. Egyptian slang mapping (dialect → MSA equivalents)
  3. Spam detection (bot patterns, low-quality heuristics)
  4. Quality filtering (min length, engagement threshold)
  5. Deduplication (fuzzy fingerprint)

All functions are stateless and can be called independently.
"""

import re
import unicodedata
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass


# =============================================================================
# EGYPTIAN FINANCIAL SLANG → STANDARD FORM MAPPING
# =============================================================================
# Maps Egyptian dialect financial slang to MSA / English equivalents
# that transformer models (CAMeLBERT, FinBERT) understand better.
#
# The mapped form is injected alongside the original — we don't delete
# the original because the model may still capture useful signal from
# surrounding dialect context.

EGYPTIAN_SLANG_MAP: Dict[str, str] = {
    # Bullish dialect
    "تجميع": "تراكم شرائي",           # Accumulation / buy pressure
    "بامب": "ارتفاع مفاجئ",            # Pump
    "هامور": "مستثمر مؤسسي كبير",      # Whale / institutional buyer
    "صاروخ": "ارتفاع قوي جدا",         # Rocket / mooning
    "هيطير": "سيرتفع بقوة",            # Will fly
    "طالع": "في اتجاه صاعد",           # Going up
    "هينفجر": "اختراق قوي متوقع",      # Will explode / breakout
    "فرصة ذهبية": "فرصة استثمارية ممتازة",  # Golden opportunity
    "صفقة العمر": "فرصة شراء استثنائية",    # Deal of a lifetime
    "كنز": "سهم ذو قيمة عالية",        # Treasure / gem
    "مشوار": "إمكانية صعود إضافية",    # Room to grow
    "لسه عنده": "لا يزال لديه مجال للصعود",  # Still has room
    "يمسك": "احتفاظ بالسهم",           # Hold

    # Bearish dialect
    "تصريف": "بيع مؤسسي",             # Distribution / sell pressure
    "دمب": "انهيار سعري",              # Dump
    "حيطة": "انهيار كامل",             # Hitting the wall / crash
    "هينهار": "انهيار متوقع",          # Will collapse
    "هيقع": "انخفاض متوقع",            # Will fall
    "فخ": "فخ سعري",                   # Trap
    "نصب": "احتيال",                   # Scam / fraud
    "ضحك على الناس": "تضليل المستثمرين",  # Fooling people
    "اهرب": "بيع فورا",               # Run away / sell now
    "سيبه": "تجنب هذا السهم",          # Leave it / avoid
    "غالي": "مبالغ في تقييمه",          # Overvalued

    # Neutral / technical dialect
    "شكله حلو": "المؤشرات الفنية إيجابية",  # Looks good technically
    "ماشي تمام": "أداء مستقر",         # Going well / stable
    "مش وحش": "أداء مقبول",            # Not bad
    "مش كويس": "أداء سلبي",            # Not good
    "مش وقته": "توقيت غير مناسب للشراء",  # Not its time
}


# =============================================================================
# ARABIC UNICODE NORMALIZATION
# =============================================================================
# Normalizes Arabic character variants to canonical forms.
# This improves matching for both slang mapping and model tokenization.

ARABIC_CHAR_NORMALIZATIONS: Dict[str, str] = {
    "أ": "ا",  # Alef with hamza above
    "إ": "ا",  # Alef with hamza below
    "آ": "ا",  # Alef with madda
    "ٱ": "ا",  # Alef wasla
    "ى": "ي",  # Alef maksura → ya
    "ؤ": "و",  # Waw with hamza
    "ئ": "ي",  # Ya with hamza
}

# Diacritics (tashkeel) regex — removed for normalization
_TASHKEEL_RE = re.compile(r"[\u0617-\u061A\u064B-\u0652\u0670]")

# Tatweel (kashida) — decorative stretching
_TATWEEL_RE = re.compile(r"\u0640")


# =============================================================================
# SPAM / BOT DETECTION PATTERNS
# =============================================================================

# Telegram / Twitter spam patterns
_SPAM_PATTERNS = [
    re.compile(r"(?:join|انضم|اشترك).{0,20}(?:group|channel|قناة|جروب)", re.IGNORECASE),
    re.compile(r"(?:free|مجان).{0,15}(?:signal|إشارة|توصية)", re.IGNORECASE),
    re.compile(r"(?:DM|message|راسل).{0,10}(?:me|now|الآن)", re.IGNORECASE),
    re.compile(r"(?:100|200|300|500|1000)%\s*(?:profit|ربح|guaranteed|مضمون)", re.IGNORECASE),
    re.compile(r"t\.me/\S+", re.IGNORECASE),              # Telegram invite links
    re.compile(r"(?:bit\.ly|tinyurl|shorturl)\S+", re.IGNORECASE),  # URL shorteners
    re.compile(r"(🚀){4,}"),                                # Excessive rocket emojis
    re.compile(r"(.)\1{7,}"),                               # 8+ repeated characters
]

# Minimum content thresholds
MIN_TEXT_LENGTH = 15          # Characters — anything shorter is noise
MIN_WORD_COUNT = 3            # Words
MIN_ENGAGEMENT_SOCIAL = 2     # Total engagement (likes+shares+comments)
MIN_NEWS_ARTICLE_LENGTH = 50  # Characters for news articles


# =============================================================================
# CORE FUNCTIONS
# =============================================================================

def normalize_arabic(text: str) -> str:
    """
    Normalize Arabic text for consistent processing.

    - Removes tashkeel (diacritics)
    - Removes tatweel (kashida)
    - Normalizes character variants (أ/إ/آ → ا, etc.)
    - Normalizes whitespace
    """
    if not text:
        return text

    # Remove diacritics
    text = _TASHKEEL_RE.sub("", text)

    # Remove tatweel
    text = _TATWEEL_RE.sub("", text)

    # Normalize character variants
    for src, dst in ARABIC_CHAR_NORMALIZATIONS.items():
        text = text.replace(src, dst)

    return text


def normalize_text(text: str) -> str:
    """
    General text normalization applied to ALL input.

    - Strips leading/trailing whitespace
    - Collapses multiple whitespace to single space
    - Removes zero-width and invisible Unicode characters
    - Normalizes Arabic characters
    - Preserves meaningful emojis (they carry sentiment signal)
    """
    if not text:
        return ""

    # Remove zero-width chars and other invisible Unicode
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]", "", text)

    # Normalize Unicode (NFC form)
    text = unicodedata.normalize("NFC", text)

    # Normalize Arabic
    text = normalize_arabic(text)

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text


def apply_slang_mapping(text: str) -> str:
    """
    Expand Egyptian financial slang by appending MSA equivalents.

    The original text is preserved — the MSA gloss is appended in
    parentheses so the transformer model sees both forms.

    Example:
      "السهم ده تجميع" → "السهم ده تجميع (تراكم شرائي)"
    """
    if not text:
        return text

    for slang, standard in EGYPTIAN_SLANG_MAP.items():
        if slang in text:
            text = text.replace(slang, f"{slang} ({standard})")

    return text


def is_spam(text: str) -> bool:
    """
    Detect spam / bot content using heuristic patterns.

    Returns True if the text matches any known spam pattern.
    """
    if not text:
        return True

    for pattern in _SPAM_PATTERNS:
        if pattern.search(text):
            return True

    return False


def passes_quality_filter(
    text: str,
    min_length: int = MIN_TEXT_LENGTH,
    min_words: int = MIN_WORD_COUNT,
) -> bool:
    """
    Check whether text meets minimum quality thresholds.

    Returns True if the text is worth sending to a model.
    """
    if not text:
        return False

    if len(text) < min_length:
        return False

    word_count = len(text.split())
    if word_count < min_words:
        return False

    return True


def passes_engagement_filter(
    engagement: Dict[str, int],
    min_total: int = MIN_ENGAGEMENT_SOCIAL,
) -> bool:
    """
    Check whether a social post has enough engagement to be signal.

    Very low engagement posts are likely noise or self-promotion.
    """
    total = sum(engagement.values())
    return total >= min_total


@dataclass
class DeduplicationIndex:
    """Tracks seen text fingerprints for deduplication."""
    _seen: set = None

    def __post_init__(self):
        if self._seen is None:
            self._seen = set()

    def _fingerprint(self, text: str) -> str:
        """Create a normalized fingerprint from text."""
        normalized = re.sub(r"\s+", " ", text.lower().strip())
        # Use first 200 chars — catches near-duplicates with minor trailing diffs
        return normalized[:200]

    def is_duplicate(self, text: str) -> bool:
        """Return True if we've seen this (or very similar) text before."""
        fp = self._fingerprint(text)
        if fp in self._seen:
            return True
        self._seen.add(fp)
        return False


def deduplicate_texts(texts: List[str]) -> List[str]:
    """
    Remove duplicate or near-duplicate texts from a list.
    Preserves order; keeps first occurrence.
    """
    index = DeduplicationIndex()
    return [t for t in texts if not index.is_duplicate(t)]


# =============================================================================
# HIGH-LEVEL PIPELINE
# =============================================================================

@dataclass
class PreprocessedText:
    """Result of preprocessing a single text."""
    original: str
    cleaned: str               # After normalization + slang mapping
    language: str              # "ar", "en", "mixed"
    is_valid: bool             # Passed all filters
    rejection_reason: str = "" # Why it was filtered out (if any)


def detect_language(text: str) -> str:
    """
    Lightweight language detection using Unicode character ratios.

    Returns "ar", "en", or "mixed".
    """
    if not text:
        return "unknown"

    arabic_chars = len(re.findall(r"[\u0600-\u06FF]", text))
    latin_chars = len(re.findall(r"[a-zA-Z]", text))

    total = arabic_chars + latin_chars
    if total == 0:
        return "unknown"

    ratio = arabic_chars / total
    if ratio > 0.6:
        return "ar"
    elif ratio > 0.2:
        return "mixed"
    return "en"


def preprocess_single(
    text: str,
    min_length: int = MIN_TEXT_LENGTH,
    min_words: int = MIN_WORD_COUNT,
    check_spam: bool = True,
) -> PreprocessedText:
    """
    Full preprocessing pipeline for a single text.

    Steps:
      1. Normalize text
      2. Detect language
      3. Check spam (if enabled)
      4. Check quality (length, word count)
      5. Apply slang mapping (for Arabic/mixed)

    Returns PreprocessedText with validity flag.
    """
    normalized = normalize_text(text)
    language = detect_language(normalized)

    # Spam check
    if check_spam and is_spam(normalized):
        return PreprocessedText(
            original=text,
            cleaned=normalized,
            language=language,
            is_valid=False,
            rejection_reason="spam",
        )

    # Quality check
    if not passes_quality_filter(normalized, min_length, min_words):
        return PreprocessedText(
            original=text,
            cleaned=normalized,
            language=language,
            is_valid=False,
            rejection_reason="low_quality",
        )

    # Apply slang mapping for Arabic content
    cleaned = normalized
    if language in ("ar", "mixed"):
        cleaned = apply_slang_mapping(normalized)

    return PreprocessedText(
        original=text,
        cleaned=cleaned,
        language=language,
        is_valid=True,
    )


def preprocess_batch(
    texts: List[str],
    deduplicate: bool = True,
    min_length: int = MIN_TEXT_LENGTH,
    min_words: int = MIN_WORD_COUNT,
    check_spam: bool = True,
) -> Tuple[List[PreprocessedText], Dict[str, int]]:
    """
    Preprocess a batch of texts with deduplication.

    Returns:
        Tuple of (valid preprocessed texts, stats dict)

    Stats dict contains:
        total, duplicates_removed, spam_removed, low_quality_removed, valid
    """
    stats = {
        "total": len(texts),
        "duplicates_removed": 0,
        "spam_removed": 0,
        "low_quality_removed": 0,
        "valid": 0,
    }

    # Deduplicate first (cheapest filter)
    if deduplicate:
        unique = deduplicate_texts(texts)
        stats["duplicates_removed"] = len(texts) - len(unique)
        texts = unique

    results = []
    for text in texts:
        processed = preprocess_single(text, min_length, min_words, check_spam)
        if processed.is_valid:
            results.append(processed)
            stats["valid"] += 1
        elif processed.rejection_reason == "spam":
            stats["spam_removed"] += 1
        elif processed.rejection_reason == "low_quality":
            stats["low_quality_removed"] += 1

    return results, stats


def preprocess_news_articles(
    articles: List[Dict],
    text_key: str = "headline",
    body_key: str = "body",
) -> Tuple[List[Dict], Dict[str, int]]:
    """
    Preprocess news articles with deduplication and quality filtering.

    Each article dict is expected to have at least a headline field.
    Optionally has a body field for richer content.

    Returns:
        Tuple of (valid articles with 'cleaned_text' added, stats dict)
    """
    stats = {
        "total": len(articles),
        "duplicates_removed": 0,
        "low_quality_removed": 0,
        "valid": 0,
    }

    dedup_index = DeduplicationIndex()
    valid_articles = []

    for article in articles:
        headline = article.get(text_key, "")
        body = article.get(body_key, "")
        combined = f"{headline}. {body}".strip(". ") if body else headline

        # Deduplicate by headline
        if dedup_index.is_duplicate(headline):
            stats["duplicates_removed"] += 1
            continue

        # Normalize
        cleaned = normalize_text(combined)

        # Quality check (news articles should be more substantial)
        if not passes_quality_filter(cleaned, MIN_NEWS_ARTICLE_LENGTH, 5):
            stats["low_quality_removed"] += 1
            continue

        # Detect language and apply slang mapping
        language = detect_language(cleaned)
        if language in ("ar", "mixed"):
            cleaned = apply_slang_mapping(cleaned)

        article_copy = dict(article)
        article_copy["cleaned_text"] = cleaned
        article_copy["detected_language"] = language
        valid_articles.append(article_copy)
        stats["valid"] += 1

    return valid_articles, stats
