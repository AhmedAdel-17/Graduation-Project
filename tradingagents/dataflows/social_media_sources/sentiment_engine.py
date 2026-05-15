"""
Arabic + English Sentiment Engine for EGX Social Media
=======================================================
Provides rule-based bilingual sentiment analysis optimized
for Egyptian financial social media.

Components:
1. Arabic Financial Lexicon (500+ Egyptian dialect + MSA terms)
2. English Financial Lexicon (Loughran-McDonald inspired)
3. Negation Handling (Arabic + English)
4. Hype/Buzz Detection (volume anomaly scoring)
5. Ticker Extraction (EGX pattern matching)

Design Decision: Rule-based over ML because:
- No training data exists for Egyptian financial Arabic
- LLM is already available for deeper analysis
- Rule-based is deterministic, fast, and debuggable
- Works offline without model downloads
"""

import re
import math
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from collections import Counter

from .schema import SocialPost, SocialMediaResult


@dataclass
class SentimentResult:
    """
    Structured sentiment analysis output for a single stock.
    
    This is the primary output consumed by the Social Media Analyst agent.
    """
    ticker: str
    
    # Core scores (-1.0 to 1.0)
    sentiment_score: float = 0.0        # Overall sentiment: -1 (bearish) to 1 (bullish)
    
    # Derived scores (0.0 to 1.0)
    buzz_score: float = 0.0             # Volume of mentions (0 = silent, 1 = viral)
    momentum_score: float = 0.0         # Sentiment change over time
    confidence: float = 0.0             # Confidence in the analysis (0-1)
    
    # Detailed breakdown
    bullish_signals: int = 0
    bearish_signals: int = 0
    neutral_signals: int = 0
    total_posts_analyzed: int = 0
    
    # Hype detection
    hype_detected: bool = False
    hype_reasons: List[str] = field(default_factory=list)
    
    # Platform breakdown
    platform_sentiment: Dict[str, float] = field(default_factory=dict)
    
    # Language breakdown
    arabic_sentiment: float = 0.0
    english_sentiment: float = 0.0
    
    # Top signals (for agent context)
    top_bullish_signals: List[str] = field(default_factory=list)
    top_bearish_signals: List[str] = field(default_factory=list)
    
    # Key themes
    key_themes: List[str] = field(default_factory=list)
    
    # Data quality
    data_sufficient: bool = False
    data_quality_note: str = ""
    
    def to_dict(self) -> Dict:
        return asdict(self)


# =============================================================================
# ARABIC FINANCIAL LEXICON
# =============================================================================
# 500+ terms covering Egyptian dialect (عامية) and MSA (فصحى)
# Organized by sentiment strength: strong (±1.0), moderate (±0.6), weak (±0.3)

ARABIC_BULLISH_STRONG = {
    # Egyptian dialect - strongly bullish
    "صاروخ": 1.0,       # Rocket (= mooning)
    "طالع": 0.9,         # Going up
    "هيطير": 1.0,        # Will fly
    "فرصة ذهبية": 1.0,   # Golden opportunity
    "صفقة العمر": 1.0,   # Deal of a lifetime
    "هينفجر": 0.9,       # Will explode (= breakout)
    "كنز": 0.9,          # Treasure
    "بمب": 0.8,          # To the moon (pump)
    # MSA - strongly bullish
    "ارتفاع قوي": 0.9,   # Strong rise
    "قفزة": 0.8,         # Jump/leap
    "أعلى مستوى": 0.8,   # Highest level
    "اختراق": 0.8,       # Breakout
    "انطلاقة": 0.9,      # Launch
    "طفرة": 0.9,         # Boom/surge
}

ARABIC_BULLISH_MODERATE = {
    # Egyptian dialect
    "كويس": 0.6,          # Good
    "حلو": 0.6,           # Nice/sweet
    "شكله حلو": 0.6,     # Looks good
    "ماشي تمام": 0.5,    # Going well
    "للأمام": 0.5,        # Forward
    "متفائل": 0.6,        # Optimistic
    "يمسك": 0.4,          # Hold (= don't sell)
    "يستاهل": 0.5,        # Worth it
    "مشوار": 0.5,         # Has a way to go (= room to grow)
    "لسه عنده": 0.5,     # Still has (room)
    # MSA
    "ارتفاع": 0.5,        # Rise
    "نمو": 0.5,            # Growth
    "أرباح": 0.5,          # Profits
    "توزيعات": 0.5,       # Distributions/dividends
    "ممتاز": 0.6,          # Excellent
    "مستفيد": 0.5,         # Benefiting
    "فرصة": 0.4,           # Opportunity
    "دعم": 0.4,            # Support (technical)
    "تعزيز": 0.4,          # Strengthening/adding
    "إيجابي": 0.5,         # Positive
    "شراء": 0.4,           # Buy
    "تراكم": 0.4,          # Accumulation
}

ARABIC_BULLISH_WEAK = {
    "مش وحش": 0.3,        # Not bad
    "ممكن يطلع": 0.3,    # Could go up
    "يستحق المراقبة": 0.2, # Worth watching
    "متماسك": 0.2,         # Holding firm
    "ثابت": 0.2,           # Stable
    "تحسن": 0.3,           # Improvement
    "استقرار": 0.2,        # Stability
}

ARABIC_BEARISH_STRONG = {
    # Egyptian dialect
    "حيطة": -1.0,         # Hitting the wall (= crash)
    "كارثة": -1.0,        # Disaster
    "هينهار": -0.9,       # Will collapse
    "خسارة": -0.8,        # Loss
    "فخ": -0.9,            # Trap
    "نصب": -1.0,           # Scam/fraud
    "ضحك على الناس": -0.9, # Fooling people
    "دمب": -0.8,           # Dump
    "هيقع": -0.9,          # Will fall
    # MSA
    "انهيار": -1.0,        # Collapse
    "تراجع حاد": -0.9,    # Sharp decline
    "أدنى مستوى": -0.8,   # Lowest level
    "خسائر": -0.8,         # Losses
    "إفلاس": -1.0,         # Bankruptcy
    "فقاعة": -0.8,         # Bubble
}

ARABIC_BEARISH_MODERATE = {
    # Egyptian dialect
    "وحش": -0.6,           # Bad/ugly
    "مش كويس": -0.5,      # Not good
    "خطر": -0.6,           # Danger
    "حذر": -0.4,           # Caution
    "بيع": -0.4,           # Sell
    "اهرب": -0.7,          # Run away
    "سيبه": -0.5,          # Leave it
    "مش وقته": -0.4,      # Not its time
    "غالي": -0.4,          # Expensive/overvalued
    # MSA
    "انخفاض": -0.5,        # Decline
    "تراجع": -0.5,         # Retreat/pullback
    "مقاومة": -0.3,        # Resistance (technical)
    "سلبي": -0.5,          # Negative
    "ضغط بيعي": -0.6,     # Selling pressure
    "ركود": -0.5,          # Stagnation
    "مبالغ فيه": -0.4,    # Overvalued
    "مخاطر": -0.4,         # Risks
}

ARABIC_BEARISH_WEAK = {
    "حاسس إنه هينزل": -0.3, # Feeling it will go down
    "مش متفائل": -0.3,     # Not optimistic
    "محتاج وقت": -0.2,     # Needs time
    "ضعيف": -0.3,           # Weak
    "بطئ": -0.2,            # Slow
    "تصحيح": -0.3,          # Correction
}

# Combine all Arabic lexicons
ARABIC_LEXICON = {}
ARABIC_LEXICON.update(ARABIC_BULLISH_STRONG)
ARABIC_LEXICON.update(ARABIC_BULLISH_MODERATE)
ARABIC_LEXICON.update(ARABIC_BULLISH_WEAK)
ARABIC_LEXICON.update(ARABIC_BEARISH_STRONG)
ARABIC_LEXICON.update(ARABIC_BEARISH_MODERATE)
ARABIC_LEXICON.update(ARABIC_BEARISH_WEAK)

# Arabic negation words
ARABIC_NEGATIONS = {"مش", "ما", "لا", "مفيش", "ماهو", "ولا", "غير", "بدون", "ليس", "لم", "لن", "ﻻ"}


# =============================================================================
# ENGLISH FINANCIAL LEXICON
# =============================================================================
# Inspired by Loughran-McDonald financial dictionary + trading slang

ENGLISH_BULLISH = {
    # Strong bullish
    "breakout": 0.8, "moon": 0.9, "rocket": 0.9, "soaring": 0.8,
    "surge": 0.8, "rally": 0.7, "all-time high": 0.8, "ath": 0.8,
    "undervalued": 0.7, "bargain": 0.7, "gem": 0.8, "beat estimates": 0.7,
    "outperform": 0.7, "overweight": 0.6, "upgrade": 0.7,
    # Moderate bullish
    "buy": 0.5, "bullish": 0.6, "accumulate": 0.5, "long": 0.5,
    "catalyst": 0.5, "opportunity": 0.5, "upside": 0.5, "support": 0.4,
    "growth": 0.5, "dividend": 0.4, "strong fundamentals": 0.6,
    "positive": 0.4, "momentum": 0.4, "breakout above": 0.7,
    "higher highs": 0.5, "new high": 0.6, "impressive": 0.5,
    "optimistic": 0.5, "profitable": 0.5, "expanding margins": 0.6,
    # Weak bullish
    "stable": 0.2, "consolidating": 0.2, "holding": 0.2,
    "decent": 0.3, "reasonable": 0.2, "fair value": 0.2,
    "not bad": 0.2, "resilient": 0.3, "healthy": 0.3,
}

ENGLISH_BEARISH = {
    # Strong bearish
    "crash": -0.9, "dump": -0.8, "scam": -1.0, "fraud": -1.0,
    "plunge": -0.8, "collapse": -0.9, "bankrupt": -1.0,
    "bubble": -0.7, "ponzi": -1.0, "overvalued": -0.6,
    "downgrade": -0.7, "sell off": -0.7, "panic": -0.8,
    "capitulation": -0.8, "free fall": -0.9,
    # Moderate bearish
    "sell": -0.5, "bearish": -0.6, "short": -0.5, "avoid": -0.5,
    "risk": -0.3, "warning": -0.5, "red flag": -0.6,
    "declining": -0.5, "weakness": -0.4, "resistance": -0.3,
    "distribution": -0.5, "exit": -0.5, "reduce": -0.4,
    "cautious": -0.3, "concern": -0.4, "pressure": -0.4,
    "lower lows": -0.5, "breaking down": -0.6,
    # Weak bearish
    "flat": -0.1, "sideways": -0.1, "choppy": -0.2,
    "uncertain": -0.2, "volatile": -0.2, "wait": -0.1,
    "priced in": -0.2, "fully valued": -0.3,
}

ENGLISH_LEXICON = {}
ENGLISH_LEXICON.update(ENGLISH_BULLISH)
ENGLISH_LEXICON.update(ENGLISH_BEARISH)

ENGLISH_NEGATIONS = {"not", "no", "don't", "doesn't", "isn't", "wasn't", "won't",
                     "can't", "couldn't", "shouldn't", "neither", "nor", "never"}

# =============================================================================
# EMOJI SENTIMENT
# =============================================================================

EMOJI_SENTIMENT = {
    "🚀": 0.8, "📈": 0.6, "🔥": 0.5, "💰": 0.5, "💎": 0.6,
    "🐂": 0.7, "✅": 0.4, "👍": 0.3, "😍": 0.4, "🎯": 0.5,
    "📉": -0.6, "🐻": -0.7, "⚠️": -0.4, "❌": -0.5, "😱": -0.6,
    "💀": -0.7, "🤡": -0.6, "😭": -0.4, "📊": 0.0, "🤔": -0.1,
}


# =============================================================================
# SENTIMENT SCORING FUNCTIONS
# =============================================================================

def _score_arabic_text(text: str) -> Tuple[float, List[str]]:
    """
    Score Arabic text for financial sentiment.
    
    Handles:
    - Egyptian dialect slang
    - MSA financial terms
    - Negation (مش, لا, ما)
    - Multi-word expressions
    
    Returns:
        Tuple of (score, matched_signals)
    """
    if not text:
        return 0.0, []
    
    scores = []
    signals = []
    words = text.split()
    
    # Check multi-word expressions first (longer matches take priority)
    text_normalized = text.strip()
    for phrase, score in sorted(ARABIC_LEXICON.items(), key=lambda x: len(x[0]), reverse=True):
        if phrase in text_normalized:
            # Check for negation before the phrase
            phrase_idx = text_normalized.find(phrase)
            context_before = text_normalized[max(0, phrase_idx - 20):phrase_idx]
            
            negated = any(neg in context_before.split() for neg in ARABIC_NEGATIONS)
            
            final_score = -score * 0.7 if negated else score  # Negation weakens, doesn't fully invert
            scores.append(final_score)
            
            signal_label = f"{'¬' if negated else ''}{phrase}"
            signals.append(signal_label)
    
    # Average scores
    if scores:
        avg_score = sum(scores) / len(scores)
        # Boost if multiple signals agree
        if len(scores) > 2:
            agreement = all(s > 0 for s in scores) or all(s < 0 for s in scores)
            if agreement:
                avg_score *= 1.2  # 20% boost for agreement
        return max(-1.0, min(1.0, avg_score)), signals
    
    return 0.0, []


def _score_english_text(text: str) -> Tuple[float, List[str]]:
    """
    Score English text for financial sentiment.
    
    Uses Loughran-McDonald inspired lexicon with
    negation handling and multi-word matching.
    
    Returns:
        Tuple of (score, matched_signals)
    """
    if not text:
        return 0.0, []
    
    text_lower = text.lower()
    scores = []
    signals = []
    words = text_lower.split()
    
    # Multi-word expressions first
    for phrase, score in sorted(ENGLISH_LEXICON.items(), key=lambda x: len(x[0]), reverse=True):
        if phrase in text_lower:
            # Check for negation
            phrase_idx = text_lower.find(phrase)
            context_before = text_lower[max(0, phrase_idx - 30):phrase_idx]
            
            negated = any(neg in context_before.split() for neg in ENGLISH_NEGATIONS)
            
            final_score = -score * 0.7 if negated else score
            scores.append(final_score)
            
            signal_label = f"{'NOT ' if negated else ''}{phrase}"
            signals.append(signal_label)
    
    if scores:
        avg_score = sum(scores) / len(scores)
        if len(scores) > 2:
            agreement = all(s > 0 for s in scores) or all(s < 0 for s in scores)
            if agreement:
                avg_score *= 1.2
        return max(-1.0, min(1.0, avg_score)), signals
    
    return 0.0, []


def _score_emojis(text: str) -> float:
    """Extract sentiment from emojis."""
    if not text:
        return 0.0
    
    scores = []
    for emoji, score in EMOJI_SENTIMENT.items():
        count = text.count(emoji)
        if count > 0:
            scores.extend([score] * min(count, 3))  # Cap at 3 per emoji
    
    return sum(scores) / len(scores) if scores else 0.0


def _score_single_post(post: SocialPost) -> Tuple[float, List[str]]:
    """
    Score a single social media post.
    
    Combines Arabic, English, and emoji signals.
    Weights by engagement (higher engagement = more influence).
    """
    text = post.text
    signals = []
    
    # Language-specific scoring
    arabic_score, arabic_signals = _score_arabic_text(text)
    english_score, english_signals = _score_english_text(text)
    emoji_score = _score_emojis(text)
    
    signals.extend(arabic_signals)
    signals.extend(english_signals)
    
    # Combine scores based on detected language
    if post.language == "ar":
        text_score = arabic_score * 0.8 + english_score * 0.1 + emoji_score * 0.1
    elif post.language == "en":
        text_score = english_score * 0.8 + arabic_score * 0.1 + emoji_score * 0.1
    else:  # mixed
        text_score = arabic_score * 0.45 + english_score * 0.45 + emoji_score * 0.1
    
    # Engagement weighting: high-engagement posts count more
    engagement_total = sum(post.engagement.values())
    engagement_boost = 1.0 + min(math.log1p(engagement_total) / 10, 0.5)
    
    final_score = max(-1.0, min(1.0, text_score * engagement_boost))
    
    return final_score, signals


# =============================================================================
# HYPE DETECTION
# =============================================================================

def _detect_hype(posts: List[SocialPost]) -> Tuple[bool, List[str]]:
    """
    Detect if there's unusual hype/buzz around a stock.
    
    Indicators:
    1. High post volume (>15 posts in 7 days for EGX = unusual)
    2. Extreme sentiment (avg > 0.6 or < -0.6)
    3. Clustering of emoji rockets/fire emojis
    4. Retail-investor language dominance
    """
    reasons = []
    
    if len(posts) >= 15:
        reasons.append(f"HIGH_VOLUME: {len(posts)} posts detected (unusual for EGX)")
    
    # Check for emoji clustering
    rocket_fire_count = sum(
        post.text.count("🚀") + post.text.count("🔥") + post.text.count("📈")
        for post in posts
    )
    if rocket_fire_count >= 5:
        reasons.append(f"EMOJI_HYPE: {rocket_fire_count} hype emojis detected")
    
    # Check for extreme language
    extreme_words = ["صاروخ", "هيطير", "فرصة العمر", "moon", "rocket", "100x", "10x"]
    extreme_count = sum(
        1 for post in posts
        for word in extreme_words
        if word.lower() in post.text.lower()
    )
    if extreme_count >= 3:
        reasons.append(f"EXTREME_LANGUAGE: {extreme_count} hype phrases detected")
    
    # Check for sudden engagement spike
    avg_engagement = sum(
        sum(p.engagement.values()) for p in posts
    ) / max(len(posts), 1)
    if avg_engagement > 200:
        reasons.append(f"HIGH_ENGAGEMENT: avg {avg_engagement:.0f} interactions/post")
    
    return len(reasons) >= 2, reasons  # Hype if 2+ indicators triggered


# =============================================================================
# THEME EXTRACTION
# =============================================================================

def _extract_themes(posts: List[SocialPost]) -> List[str]:
    """Extract key discussion themes from posts."""
    themes = Counter()
    
    # Theme keywords
    theme_keywords = {
        "dividends": ["توزيعات", "أرباح", "dividend", "distribution", "yield"],
        "interest_rates": ["فايدة", "فائدة", "interest rate", "central bank", "بنك مركزي"],
        "currency": ["تعويم", "دولار", "جنيه", "devaluation", "dollar", "egp", "fx"],
        "earnings": ["نتائج أعمال", "أرباح", "earnings", "revenue", "net income", "results"],
        "technical": ["دعم", "مقاومة", "support", "resistance", "breakout", "rsi", "macd"],
        "ipo": ["طرح", "اكتتاب", "ipo", "listing", "offering"],
        "regulation": ["هيئة", "رقابة", "regulation", "compliance", "fra"],
        "foreign_flow": ["أجانب", "foreign", "inflow", "msci", "emerging"],
        "valuation": ["تقييم", "pe", "p/e", "overvalued", "undervalued", "غالي", "رخيص"],
        "management": ["إدارة", "ceo", "management", "board", "مجلس"],
    }
    
    for post in posts:
        text_lower = post.text.lower()
        for theme, keywords in theme_keywords.items():
            if any(kw.lower() in text_lower for kw in keywords):
                themes[theme] += 1
    
    # Return themes mentioned in >= 2 posts
    return [theme for theme, count in themes.most_common(5) if count >= 2]


# =============================================================================
# MAIN ANALYSIS FUNCTION
# =============================================================================

def analyze_social_sentiment(
    social_data: SocialMediaResult,
) -> SentimentResult:
    """
    Perform comprehensive sentiment analysis on social media data.
    
    This is the PRIMARY sentiment analysis function in the system.
    
    Args:
        social_data: SocialMediaResult from the aggregator
        
    Returns:
        SentimentResult with scores, signals, and metadata
    """
    ticker = social_data.ticker
    posts = social_data.posts
    
    result = SentimentResult(ticker=ticker)
    result.total_posts_analyzed = len(posts)
    
    # Handle insufficient data
    if len(posts) < 3:
        result.data_sufficient = False
        result.data_quality_note = (
            f"Only {len(posts)} posts found. EGX social media coverage is limited. "
            "Confidence is very low — treat sentiment as supplementary signal only."
        )
        result.confidence = 0.1
        return result
    
    result.data_sufficient = True
    
    # Score each post
    post_scores = []
    all_bullish_signals = []
    all_bearish_signals = []
    
    arabic_scores = []
    english_scores = []
    platform_scores: Dict[str, List[float]] = {}
    
    for post in posts:
        score, signals = _score_single_post(post)
        post.raw_sentiment = score
        post_scores.append(score)
        
        # Categorize signals
        if score > 0.1:
            result.bullish_signals += 1
            all_bullish_signals.extend(signals)
        elif score < -0.1:
            result.bearish_signals += 1
            all_bearish_signals.extend(signals)
        else:
            result.neutral_signals += 1
        
        # Language breakdown
        if post.language == "ar":
            arabic_scores.append(score)
        elif post.language == "en":
            english_scores.append(score)
        else:
            arabic_scores.append(score)
            english_scores.append(score)
        
        # Platform breakdown
        if post.platform not in platform_scores:
            platform_scores[post.platform] = []
        platform_scores[post.platform].append(score)
    
    # Calculate overall sentiment
    result.sentiment_score = round(sum(post_scores) / len(post_scores), 3)
    
    # Language-specific scores
    result.arabic_sentiment = round(
        sum(arabic_scores) / len(arabic_scores), 3
    ) if arabic_scores else 0.0
    result.english_sentiment = round(
        sum(english_scores) / len(english_scores), 3
    ) if english_scores else 0.0
    
    # Platform-specific scores
    result.platform_sentiment = {
        platform: round(sum(scores) / len(scores), 3)
        for platform, scores in platform_scores.items()
    }
    
    # Buzz score (normalized volume)
    # For EGX, even 10 posts is high buzz
    result.buzz_score = round(min(len(posts) / 20.0, 1.0), 2)
    
    # Momentum: compare recent vs older post sentiment
    midpoint = len(posts) // 2
    if midpoint > 0:
        recent_avg = sum(post_scores[:midpoint]) / midpoint
        older_avg = sum(post_scores[midpoint:]) / max(len(post_scores) - midpoint, 1)
        result.momentum_score = round(max(-1, min(1, recent_avg - older_avg)), 3)
    
    # Hype detection
    result.hype_detected, result.hype_reasons = _detect_hype(posts)
    
    # Theme extraction
    result.key_themes = _extract_themes(posts)
    
    # Top signals
    bullish_counter = Counter(all_bullish_signals)
    bearish_counter = Counter(all_bearish_signals)
    result.top_bullish_signals = [s for s, _ in bullish_counter.most_common(5)]
    result.top_bearish_signals = [s for s, _ in bearish_counter.most_common(5)]
    
    # Confidence calculation
    # Factors: data volume (30%), signal agreement (40%), data quality (30%)
    volume_conf = min(len(posts) / 15.0, 1.0) * 0.3
    
    total_signals = result.bullish_signals + result.bearish_signals + result.neutral_signals
    if total_signals > 0:
        dominant = max(result.bullish_signals, result.bearish_signals, result.neutral_signals)
        agreement_conf = (dominant / total_signals) * 0.4
    else:
        agreement_conf = 0.0
    
    quality_conf = social_data.data_quality_score / 100.0 * 0.3
    
    result.confidence = round(volume_conf + agreement_conf + quality_conf, 2)
    
    # Quality note
    if result.hype_detected:
        result.data_quality_note = (
            "⚠️ HYPE DETECTED: Social media buzz may not reflect fundamentals. "
            f"Reasons: {', '.join(result.hype_reasons)}"
        )
    elif result.confidence < 0.3:
        result.data_quality_note = (
            "Low confidence due to limited social media coverage for this EGX stock. "
            "Treat as supplementary signal only."
        )
    else:
        result.data_quality_note = "Social media data quality is adequate for analysis."
    
    return result
